"""Avisos de la cuenta de eMuwarez: lo que cambia entre dos lecturas del perfil, una vez por cambio."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from emuwarez_uploadrr.database import connect, db_path, get_json_state, init_db, set_json_state
from emuwarez_uploadrr.services import avisos_cuenta, emu_perfil
from emuwarez_uploadrr.services.avisos_cuenta import avisar_cambios
from emuwarez_uploadrr.services.emu_perfil import perfil_emu
from emuwarez_uploadrr.services.notification_center import EVENT_TYPES
from emuwarez_uploadrr.services.settings import set_setting

PERFIL = {
    "username": "Kisnov", "group": "PowerUser", "uploaded": "1.2 TiB", "downloaded": "300 GiB", "ratio": "4.1",
    "buffer": "900 GiB", "seeding": 800, "leeching": 0, "seedbonus": "12345.50", "hit_and_runs": 1,
}


@pytest.fixture
def conn(isolated_db):
    c = connect(db_path())
    init_db(c)
    yield c
    c.close()


def _avisos(conn):
    return [(f["event_type"], f["title"], f["body"], f["level"])
            for f in conn.execute("SELECT * FROM notifications ORDER BY id")]


def test_los_eventos_estan_en_el_catalogo():
    for evento in (avisos_cuenta.EVENTO_HR, avisos_cuenta.EVENTO_GRUPO, avisos_cuenta.EVENTO_BUFFER,
                   avisos_cuenta.EVENTO_SEEDING):
        assert evento in EVENT_TYPES


def test_sin_cambios_no_avisa(conn):
    avisar_cambios(conn, PERFIL, dict(PERFIL))
    assert _avisos(conn) == []


@pytest.mark.parametrize("antes", [None, {}, {**PERFIL, "username": "Otro", "hit_and_runs": 0}])
def test_sin_lectura_anterior_de_la_misma_cuenta_no_compara(conn, antes):
    avisar_cambios(conn, antes, {**PERFIL, "hit_and_runs": 5})
    assert _avisos(conn) == []


def test_un_hr_nuevo(conn):
    avisar_cambios(conn, PERFIL, {**PERFIL, "hit_and_runs": 2})
    [(evento, titulo, cuerpo, nivel)] = _avisos(conn)
    assert (evento, titulo, nivel) == ("cuenta_hr_nuevo", "Tienes un H&R nuevo en eMuwarez", "warning")
    assert "Ahora tienes 2" in cuerpo


def test_varios_hr_nuevos_y_ninguno_si_bajan(conn):
    avisar_cambios(conn, PERFIL, {**PERFIL, "hit_and_runs": 4})
    avisar_cambios(conn, {**PERFIL, "hit_and_runs": 4}, {**PERFIL, "hit_and_runs": 0})
    assert [a[1] for a in _avisos(conn)] == ["Tienes 3 H&R nuevos en eMuwarez"]


def test_cambio_de_grupo(conn):
    avisar_cambios(conn, PERFIL, {**PERFIL, "group": "InsaneUser"})
    assert _avisos(conn) == [("cuenta_grupo_cambia", "Tu grupo en eMuwarez ha cambiado",
                              "De PowerUser a InsaneUser.", "info")]


def test_buffer_que_pasa_a_negativo_avisa_una_vez(conn):
    avisar_cambios(conn, PERFIL, {**PERFIL, "buffer": "-1.5 GiB"})
    avisar_cambios(conn, {**PERFIL, "buffer": "-1.5 GiB"}, {**PERFIL, "buffer": "-3 GiB"})   # sigue negativo
    [(evento, _, cuerpo, nivel)] = _avisos(conn)
    assert (evento, nivel) == ("cuenta_buffer_negativo", "error")
    assert "-1.5 GiB" in cuerpo


@pytest.mark.parametrize("antes, ahora, avisa", [
    (800, 600, True),     # 200 menos: más del 20 %
    (800, 700, False),    # 100 menos: menos del 20 %
    (30, 20, True),       # 10 menos, un tercio
    (12, 3, False),       # menos de 10 torrents: normal con pocos
    (800, 900, False),
])
def test_bajon_de_seeding(conn, antes, ahora, avisa):
    avisar_cambios(conn, {**PERFIL, "seeding": antes}, {**PERFIL, "seeding": ahora})
    assert [a[0] for a in _avisos(conn)] == (["cuenta_seeding_baja"] if avisa else [])


def test_cifras_raras_no_rompen(conn):
    avisar_cambios(conn, {**PERFIL, "hit_and_runs": None, "buffer": "∞", "seeding": "x"},
                   {**PERFIL, "hit_and_runs": 3, "buffer": "-1 GiB", "seeding": 0})
    assert _avisos(conn) == []


def test_un_fallo_al_avisar_no_lanza(conn, monkeypatch, caplog):
    def falla(*a, **k):
        raise RuntimeError("campana rota")

    monkeypatch.setattr(avisos_cuenta, "emit", falla)
    avisar_cambios(conn, PERFIL, {**PERFIL, "hit_and_runs": 2})
    assert "No se pudieron comprobar los avisos" in caplog.text


def test_la_lectura_del_perfil_avisa_comparando_con_la_anterior(conn, monkeypatch):
    set_setting(conn, "emu.api_key", "clave")
    conn.commit()
    lecturas = iter([dict(PERFIL), {**PERFIL, "hit_and_runs": 2}])
    monkeypatch.setattr(emu_perfil, "emu_get_user", lambda api_key, base_url=None: next(lecturas))
    perfil_emu(conn)
    assert _avisos(conn) == []          # la primera lectura no tiene con qué comparar
    # Una hora después, la siguiente lectura trae un H&R más.
    guardado = get_json_state(conn, emu_perfil.PERFIL_STATE_KEY)
    guardado["intento_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    set_json_state(conn, emu_perfil.PERFIL_STATE_KEY, guardado)
    conn.commit()
    perfil_emu(conn)
    assert [a[0] for a in _avisos(conn)] == ["cuenta_hr_nuevo"]
