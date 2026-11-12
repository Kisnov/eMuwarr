"""La sincronización del catálogo de eMuwarez en segundo plano y las rutas `/api/library/emu-sync*` que usa la interfaz.

`sync_emu_catalog` se sustituye por un falso que controla el test: aquí se prueban el hilo, el limitador, el progreso,
los eventos y las rutas, no la descarga (esa la cubren los tests del catálogo).
"""
from __future__ import annotations

import threading
import time

import pytest

from emuwarez_uploadrr.database import connect, db_path, get_state, init_db, set_state
from emuwarez_uploadrr.services import emu_catalog as ec


@pytest.fixture(autouse=True)
def progreso_limpio(isolated_db):
    conn = connect(db_path())
    init_db(conn)
    conn.close()
    ec._set_progress(active=False, done=False, error=None, phase="")
    yield
    ec._set_progress(active=False, done=False, error=None, phase="")


@pytest.fixture
def sync_falsa(monkeypatch):
    """Sustituye la sincronización real: registra con qué se llamó y espera a `soltar` o lanza `fallo`."""
    class Falsa:
        llamadas: list[dict] = []
        soltar = threading.Event()
        fallo: Exception | None = None

    Falsa.llamadas = []
    Falsa.soltar = threading.Event()
    Falsa.soltar.set()

    def sync(conn, *, force=False, mode="incremental"):
        Falsa.llamadas.append({"force": force, "mode": mode})
        Falsa.soltar.wait(5)
        if Falsa.fallo:
            raise Falsa.fallo
        return {"status": "ok"}

    monkeypatch.setattr(ec, "sync_emu_catalog", sync)
    return Falsa


@pytest.fixture
def eventos(monkeypatch):
    vistos: list[str] = []
    monkeypatch.setattr(ec, "publicar", lambda evento, datos=None: vistos.append(evento))
    return vistos


def _estado(clave):
    conn = connect(db_path())
    init_db(conn)
    try:
        return get_state(conn, clave)
    finally:
        conn.close()


def _poner_estado(clave, valor):
    conn = connect(db_path())
    init_db(conn)
    try:
        set_state(conn, clave, valor)
        conn.commit()
    finally:
        conn.close()


def _esperar(condicion, plazo=5.0):
    limite = time.monotonic() + plazo
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.02)
    return False


def _terminada():
    return ec.get_sync_progress()["done"]


def test_arranca_en_segundo_plano_deja_progreso_y_avisa_al_empezar_y_al_acabar(isolated_db, sync_falsa, eventos):
    sync_falsa.soltar.clear()

    assert ec.start_emu_sync_background() == {"status": "started"}
    assert _esperar(lambda: sync_falsa.llamadas)
    progreso = ec.get_sync_progress()
    assert (progreso["active"], progreso["done"], progreso["error"]) == (True, False, None)
    assert eventos == [ec.EVENTO_SYNC_EMPEZADA]

    sync_falsa.soltar.set()
    assert _esperar(_terminada)
    progreso = ec.get_sync_progress()
    assert (progreso["active"], progreso["error"]) == (False, None)
    assert _esperar(lambda: len(eventos) == 2)  # el aviso de fin va justo detrás de marcar el progreso
    assert eventos == [ec.EVENTO_SYNC_EMPEZADA, ec.EVENTO_SYNC_TERMINADA]
    assert sync_falsa.llamadas == [{"force": True, "mode": "incremental"}]  # el limitador ya se anotó al empezar


def test_el_limitador_se_anota_al_empezar_y_corta_el_segundo_intento(isolated_db, sync_falsa, eventos):
    sync_falsa.soltar.clear()
    ec.start_emu_sync_background()
    assert _estado(ec.SYNC_STATE_KEY), "la marca del limitador tiene que estar antes de que acabe la descarga"
    assert _estado(ec.FULL_SYNC_STATE_KEY) is None

    segundo = ec.start_emu_sync_background()

    assert segundo["status"] == "throttled" and 0 < segundo["wait_seconds"] <= ec.MIN_SYNC_SECONDS
    sync_falsa.soltar.set()
    assert _esperar(_terminada)
    assert len(sync_falsa.llamadas) == 1


def test_force_con_una_en_marcha_no_lanza_otra(isolated_db, sync_falsa, eventos):
    sync_falsa.soltar.clear()
    ec.start_emu_sync_background()
    assert _esperar(lambda: sync_falsa.llamadas)

    assert ec.start_emu_sync_background(force=True)["status"] == "started"

    sync_falsa.soltar.set()
    assert _esperar(_terminada)
    assert len(sync_falsa.llamadas) == 1
    assert eventos.count(ec.EVENTO_SYNC_EMPEZADA) == 1


def test_la_completa_anota_las_dos_marcas_y_pasa_el_modo(isolated_db, sync_falsa, eventos):
    ec.start_emu_sync_background(mode="full")
    assert _esperar(_terminada)

    assert sync_falsa.llamadas == [{"force": True, "mode": "full"}]
    assert _estado(ec.SYNC_STATE_KEY) and _estado(ec.FULL_SYNC_STATE_KEY)


def test_si_la_descarga_falla_el_error_sale_en_el_progreso_y_el_limitador_se_restaura(isolated_db, sync_falsa, eventos):
    anterior = "2020-01-01T00:00:00+00:00"
    _poner_estado(ec.SYNC_STATE_KEY, anterior)
    sync_falsa.fallo = RuntimeError("eMuwarez no responde")

    assert ec.start_emu_sync_background()["status"] == "started"
    assert _esperar(_terminada)

    progreso = ec.get_sync_progress()
    assert progreso["active"] is False and "eMuwarez no responde" in progreso["error"] and progreso["phase"] == "Error"
    assert _estado(ec.SYNC_STATE_KEY) == anterior, "un fallo no debe dejarte sin poder reintentar durante una hora"
    assert _esperar(lambda: eventos[-1:] == [ec.EVENTO_SYNC_TERMINADA])


def test_si_falla_la_completa_se_restauran_las_dos_marcas(isolated_db, sync_falsa, eventos):
    sync_falsa.fallo = RuntimeError("boom")
    ec.start_emu_sync_background(mode="full")
    assert _esperar(_terminada)

    assert not _estado(ec.SYNC_STATE_KEY) and not _estado(ec.FULL_SYNC_STATE_KEY)
    assert ec.start_emu_sync_background(mode="full")["status"] == "started"  # y se puede reintentar al momento


# --- las rutas que usa la interfaz ----------------------------------------------------------------------

def test_las_rutas_piden_sesion(client):
    for metodo, ruta in (("get", "/api/library/emu-sync-status"), ("post", "/api/library/emu-sync"),
                         ("get", "/api/library/emu-sync-progress")):
        assert getattr(client, metodo)(ruta).status_code == 401, ruta


def test_la_ruta_de_estado_dice_que_se_puede_sincronizar_y_luego_cuanto_falta(client, auth_headers, sync_falsa, eventos):
    antes = client.get("/api/library/emu-sync-status", headers=auth_headers).json()
    assert antes["can_sync"] is True and antes["wait_seconds"] == 0 and antes["last_sync_at"] is None

    assert client.post("/api/library/emu-sync", headers=auth_headers).json() == {"status": "started"}
    assert _esperar(_terminada)

    despues = client.get("/api/library/emu-sync-status", headers=auth_headers).json()
    assert despues["can_sync"] is False and despues["wait_seconds"] > 0 and despues["last_sync_at"]
    assert despues["full_can_sync"] is True


def test_la_ruta_de_sincronizar_devuelve_429_con_el_tiempo_que_falta(client, auth_headers, sync_falsa, eventos):
    client.post("/api/library/emu-sync", headers=auth_headers)
    assert _esperar(_terminada)

    resp = client.post("/api/library/emu-sync", headers=auth_headers)

    assert resp.status_code == 429
    assert resp.json()["detail"].startswith("Espera ") and "antes de volver a sincronizar" in resp.json()["detail"]


@pytest.mark.parametrize("pedido, modo", [("full", "full"), ("incremental", "incremental"), ("cualquier-cosa", "incremental")])
def test_la_ruta_solo_conoce_dos_modos(client, auth_headers, sync_falsa, eventos, pedido, modo):
    assert client.post(f"/api/library/emu-sync?mode={pedido}", headers=auth_headers).status_code == 200
    assert _esperar(_terminada)

    assert sync_falsa.llamadas == [{"force": True, "mode": modo}]


def test_la_ruta_de_progreso_enseña_el_progreso_en_vivo(client, auth_headers, sync_falsa, eventos):
    sync_falsa.soltar.clear()
    client.post("/api/library/emu-sync", headers=auth_headers)
    assert _esperar(lambda: sync_falsa.llamadas)

    en_marcha = client.get("/api/library/emu-sync-progress", headers=auth_headers).json()
    assert en_marcha["active"] is True and en_marcha["done"] is False

    sync_falsa.soltar.set()
    assert _esperar(_terminada)
    acabada = client.get("/api/library/emu-sync-progress", headers=auth_headers).json()
    assert acabada["active"] is False and acabada["done"] is True and acabada["error"] is None
