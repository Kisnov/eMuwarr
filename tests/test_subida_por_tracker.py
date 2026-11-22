"""Fase B del plan multitracker: una subida por tracker (eMuwarez, N.O.B.S, Milnueve), todas del mismo fichero preparado.

Cada trabajo lleva su tracker (`upload_jobs.tracker`) y lanza Uploadrr-EMU con su `-tk`. La segunda subida del mismo
contenido espera a que acabe la primera (comparten el fichero y el `tmp/<nombre>` de Uploadrr-EMU), reutiliza su
preparación y lanza con `--resume` para no borrar capturas ni BASE.torrent.
"""
from __future__ import annotations

import json
import os
import threading

import pytest

from emuwarez_uploadrr.services import trackers
from emuwarez_uploadrr.services import upload_queue as uq
from emuwarez_uploadrr.services.uploadrr_contract import build_uploadrr_emu_command


@pytest.fixture(autouse=True)
def _biblioteca_en_tmp(tmp_path, monkeypatch):
    from emuwarez_uploadrr.services import uploadrr_contract
    monkeypatch.setattr(uploadrr_contract, "DEFAULT_ALLOWED_ROOTS", [str(tmp_path)])


@pytest.fixture
def db_conn(isolated_db):
    from emuwarez_uploadrr.database import connect, init_db
    conn = connect(isolated_db / "emuwarez_uploadrr.db")
    init_db(conn)
    return conn


def _clave(conn, tracker_id, valor="clave"):
    from emuwarez_uploadrr.services.settings import set_setting
    set_setting(conn, f"{trackers.obtener(tracker_id).prefijo_ajustes}.api_key", valor)
    conn.commit()


def _peli(conn, ruta):
    conn.execute(
        """INSERT INTO library_items (item_type, title, local_status, request_status, nomenclature_status, path,
                                      size_bytes, updated_at)
           VALUES ('movie', 'X', 'Disponible', 'Sin petición', 'N/A', ?, 100, CURRENT_TIMESTAMP)""", (ruta,))
    conn.commit()
    return conn.execute("SELECT id FROM library_items ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _trabajo(conn, item_id, **datos):
    datos = {"status": "queued", "stage": "queued", "tracker": "emu", **datos}
    columnas = ", ".join(["library_item_id", "title", "path", "created_at", "updated_at", *datos])
    marcas = ", ".join(["?", "'X'", "''", "CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP", *("?" * len(datos))])
    cur = conn.execute(f"INSERT INTO upload_jobs ({columnas}) VALUES ({marcas})", (item_id, *datos.values()))
    conn.commit()
    return cur.lastrowid


def _fila(conn, job_id):
    return dict(conn.execute("SELECT * FROM upload_jobs WHERE id = ?", (job_id,)).fetchone())


def _logs(conn, job_id):
    return [f["message"] for f in conn.execute("SELECT message FROM upload_job_logs WHERE upload_job_id = ?", (job_id,))]


# ---------------------------------------------------------------------------
# El argv: -tk del tracker, -avbc solo para eMuwarez
# ---------------------------------------------------------------------------

def _argv(**cambios):
    datos = dict(python_path="python3", uploadrr_script_path="/opt/Uploadrr/upload.py",
                 prepared_path="/data/subidas/Peli.mkv", torrent_name="Peli", tmdb_id="603", category="movie",
                 resolution="480p")
    return build_uploadrr_emu_command(**{**datos, **cambios})


def test_el_argv_de_emu_no_cambia():
    argv = _argv()
    assert argv[3:5] == ["-tk", "EMU"]
    assert "-avbc" in argv
    assert "-avbc" not in _argv(resolution="1080p")


@pytest.mark.parametrize("tracker", ["NOBS", "MNV"])
def test_el_argv_de_otro_tracker_lleva_su_tk_y_no_el_aviso_de_emuwarez(tracker):
    argv = _argv(tracker=tracker)
    assert argv[3:5] == ["-tk", tracker]
    assert "EMU" not in argv and "-avbc" not in argv


def test_el_comando_de_la_ejecucion_sale_del_tracker_del_trabajo(db_conn, tmp_path):
    from emuwarez_uploadrr.services.upload_execution import build_uploadrr_execution_command
    comando = {"runtime_type": "uploadrr_emu", "category": "movie", "title": "Peli", "resolution": "480p",
               # Lo que guardó una vista previa: no manda, manda la columna.
               "tracker": "EMU"}
    base = {"id": 1, "prepared_path": str(tmp_path / "Peli.mkv"), "uploadrr_command_json": json.dumps(comando)}

    nobs = build_uploadrr_execution_command({**base, "tracker": "nobs"}, conn=db_conn)["args"]
    emu = build_uploadrr_execution_command({**base, "tracker": "emu"}, conn=db_conn)["args"]
    antigua = build_uploadrr_execution_command(base, conn=db_conn)["args"]  # fila de antes de la columna

    assert nobs[nobs.index("-tk") + 1] == "NOBS" and "-avbc" not in nobs
    assert emu[emu.index("-tk") + 1] == "EMU" and "-avbc" in emu
    assert antigua == emu


def test_reenviar_a_qbittorrent_de_una_subida_a_nobs_pide_el_torrent_a_nobs(db_conn, tmp_path):
    from emuwarez_uploadrr.services.upload_execution import build_uploadrr_execution_command
    job = {"id": 1, "tracker": "nobs", "prepared_path": str(tmp_path / "Peli.mkv"),
           "uploadrr_command_json": json.dumps({"runtime_type": "uploadrr_emu", "title": "Peli"})}
    args = build_uploadrr_execution_command(job, conn=db_conn, solo_cliente="77")["args"]
    assert args[args.index("-tk") + 1] == "NOBS"
    assert args[args.index("--solo-cliente") + 1] == "77" and "--resume" in args


# ---------------------------------------------------------------------------
# Crear el trabajo
# ---------------------------------------------------------------------------

def test_sin_decir_tracker_la_subida_es_a_emuwarez(db_conn, tmp_path):
    iid = _peli(db_conn, str(tmp_path / "a.mkv"))
    r = uq.queue_upload_for_item(iid, conn=db_conn)
    assert r["status"] == "queued" and _fila(db_conn, r["job"]["id"])["tracker"] == "emu"


def test_la_subida_a_nobs_guarda_su_tracker(db_conn, tmp_path):
    _clave(db_conn, "nobs")
    iid = _peli(db_conn, str(tmp_path / "a.mkv"))
    r = uq.queue_upload_for_item(iid, tracker="nobs", conn=db_conn)
    assert r["status"] == "queued" and _fila(db_conn, r["job"]["id"])["tracker"] == "nobs"


@pytest.mark.parametrize("tracker", ["nobs", "mnv"])
def test_sin_su_clave_no_se_crea_la_subida(db_conn, tmp_path, tracker):
    iid = _peli(db_conn, str(tmp_path / "a.mkv"))
    r = uq.queue_upload_for_item(iid, tracker=tracker, conn=db_conn)
    assert r["status"] == "blocked" and "Falta la clave de API" in r["errors"][0]
    assert db_conn.execute("SELECT COUNT(*) FROM upload_jobs").fetchone()[0] == 0


def test_un_tracker_desconocido_no_se_crea(db_conn, tmp_path):
    iid = _peli(db_conn, str(tmp_path / "a.mkv"))
    assert uq.queue_upload_for_item(iid, tracker="otro", conn=db_conn)["status"] == "blocked"


def test_la_misma_subida_a_otro_tracker_se_crea_aunque_la_primera_siga_en_marcha(db_conn, tmp_path):
    _clave(db_conn, "nobs")
    iid = _peli(db_conn, str(tmp_path / "a.mkv"))
    primera = uq.queue_upload_for_item(iid, conn=db_conn)
    assert primera["status"] == "queued"

    # La misma al mismo tracker no: sería un duplicado.
    assert uq.queue_upload_for_item(iid, conn=db_conn)["status"] == "blocked"
    otra = uq.queue_upload_for_item(iid, tracker="nobs", conn=db_conn)

    assert otra["status"] == "queued"
    # Una cadena de reintentos por tracker: la de N.O.B.S no reintenta la de eMuwarez.
    assert _fila(db_conn, otra["job"]["id"])["retry_of_job_id"] is None


def test_el_reintento_de_una_subida_a_nobs_sigue_siendo_a_nobs(db_conn, tmp_path):
    _clave(db_conn, "nobs")
    iid = _peli(db_conn, str(tmp_path / "a.mkv"))
    fallida = _trabajo(db_conn, iid, status="failed", stage="failed", tracker="nobs")
    r = uq.queue_retry_for_job(fallida, conn=db_conn)
    assert r["status"] == "queued" and _fila(db_conn, r["job"]["id"])["tracker"] == "nobs"


def test_la_api_rechaza_con_422_una_subida_sin_la_clave_de_su_tracker(client, auth_headers, db_conn, tmp_path):
    iid = _peli(db_conn, str(tmp_path / "a.mkv"))
    r = client.post(f"/api/library/{iid}/upload/queue", json={"tracker": "nobs"}, headers=auth_headers)
    assert r.status_code == 422 and "N.O.B.S" in r.json()["detail"]


def test_la_api_dice_a_que_trackers_se_puede_subir(client, auth_headers, db_conn):
    assert client.get("/api/upload/trackers", headers=auth_headers).json() == [{"id": "emu", "nombre": "eMuwarez"}]
    _clave(db_conn, "mnv")
    assert [t["id"] for t in client.get("/api/upload/trackers", headers=auth_headers).json()] == ["emu", "mnv"]


# ---------------------------------------------------------------------------
# Nunca dos a la vez sobre el mismo contenido
# ---------------------------------------------------------------------------

def test_la_segunda_espera_a_que_termine_la_primera(db_conn, tmp_path, monkeypatch):
    monkeypatch.setattr(uq, "ESPERA_ENTRE_TRACKERS_S", 0.01)
    iid = _peli(db_conn, str(tmp_path / "a.mkv"))
    primera = _trabajo(db_conn, iid, status="running", stage="running")
    segunda = _trabajo(db_conn, iid, tracker="nobs")
    hecho = []
    hilo = threading.Thread(target=lambda: hecho.append(uq._esperar_a_la_anterior(segunda)))
    hilo.start()
    hilo.join(0.3)
    assert hilo.is_alive() and not hecho  # sigue esperando: la primera está subiendo
    assert any("Espera a que termine la subida" in m and "eMuwarez" in m for m in _logs(db_conn, segunda))

    db_conn.execute("UPDATE upload_jobs SET status = 'completed' WHERE id = ?", (primera,))
    db_conn.commit()
    hilo.join(5)
    assert hecho == [True]


def test_la_primera_no_espera_a_la_segunda(db_conn, tmp_path):
    iid = _peli(db_conn, str(tmp_path / "a.mkv"))
    primera = _trabajo(db_conn, iid)
    _trabajo(db_conn, iid, tracker="nobs")
    assert uq._esperar_a_la_anterior(primera) is True


def test_con_el_hilo_vivo_cuenta_aunque_este_lista(db_conn, tmp_path, monkeypatch):
    """Entre 'ready' y 'running' (el arranque de Uploadrr) la primera no está en un estado activo, pero sí en marcha."""
    iid = _peli(db_conn, str(tmp_path / "a.mkv"))
    primera = _trabajo(db_conn, iid, status="ready", stage="preflight")
    segunda = _trabajo(db_conn, iid, tracker="nobs")
    monkeypatch.setattr(uq, "_EN_CURSO", {primera, segunda})
    assert uq._anterior_en_curso(db_conn, segunda)["id"] == primera
    monkeypatch.setattr(uq, "_EN_CURSO", {segunda})
    assert uq._anterior_en_curso(db_conn, segunda) is None


def test_cancelada_mientras_espera_no_sigue(db_conn, tmp_path, monkeypatch):
    monkeypatch.setattr(uq, "ESPERA_ENTRE_TRACKERS_S", 0.01)
    iid = _peli(db_conn, str(tmp_path / "a.mkv"))
    _trabajo(db_conn, iid, status="running", stage="running")
    segunda = _trabajo(db_conn, iid, status="cancelled", stage="cancelled", tracker="nobs")
    assert uq._esperar_a_la_anterior(segunda) is False


def test_otro_contenido_no_espera(db_conn, tmp_path):
    _trabajo(db_conn, _peli(db_conn, str(tmp_path / "a.mkv")), status="running", stage="running")
    otra = _trabajo(db_conn, _peli(db_conn, str(tmp_path / "b.mkv")), tracker="nobs")
    assert uq._esperar_a_la_anterior(otra) is True


# ---------------------------------------------------------------------------
# La segunda reutiliza el fichero de la primera y reanuda Uploadrr-EMU
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline(db_conn, tmp_path, monkeypatch, ficheros_de_mentira):
    """_run_queued_job de una película hasta el arranque de Uploadrr, que no se lanza."""
    from emuwarez_uploadrr.services import carpeta_subida, nomenclature, upload_execution

    subidas = tmp_path / "subidas"
    subidas.mkdir()
    monkeypatch.setattr(carpeta_subida, "RAIZ_SUBIDAS", str(subidas))
    monkeypatch.setattr(uq, "_check_generic_upload_preconditions", lambda *a, **k: True)
    monkeypatch.setattr(uq, "_populate_ready", lambda *a, **k: True)
    monkeypatch.setattr(uq, "_renombrar_si_esta_en_subidas", lambda _j, _i, origen, *a, **k: origen)

    def arrancar(*_a, **_k):
        upload_execution._upload_concurrency_gate.release()
        return {}

    monkeypatch.setattr(upload_execution, "start_upload_job", arrancar)
    origen = tmp_path / "Peli.mkv"
    origen.write_bytes(b"x" * 100)
    monkeypatch.setattr(nomenclature, "build_nomenclature_apply_plan", lambda *a, **k: {
        "status": "ready", "source_path": str(origen), "nombre": "Peli.2024.mkv", "proposal": None,
        "raw_destination_path": None})
    preparados = []

    def preparar(job_id, item_id, org, destino, modo, conn, **kw):
        preparados.append(destino)
        os.link(org, destino)
        return {"metodo": "enlace duro", "original_editado": False, "pistas": None, "omitida": "Ninguno"}

    monkeypatch.setattr(uq, "_preparar_fichero", preparar)
    return type("P", (), {"origen": origen, "destino": subidas / "Peli.2024.mkv", "preparados": preparados})


def _subida_a_emu_hecha(conn, pipeline, **extra):
    """La subida a eMuwarez ya completada, con su fichero en la carpeta de subidas."""
    os.link(pipeline.origen, pipeline.destino)
    iid = _peli(conn, str(pipeline.origen))
    huellas = json.dumps({"Peli.2024.mkv": uq._huella_de(str(pipeline.origen))})
    datos = {"status": "completed", "stage": "completed", "torrent_subido": 1, "prepared_path": str(pipeline.destino),
             "remux_snapshot_json": json.dumps({"omitida": "Ninguno"}), "huellas_preparacion_json": huellas,
             "pasos_uploadrr_json": json.dumps(["screenshots", "torrent", "tracker"]), **extra}
    return iid, _trabajo(conn, iid, **datos)


def test_la_subida_a_nobs_reutiliza_el_fichero_de_la_de_emuwarez_y_reanuda(db_conn, pipeline):
    iid, emu = _subida_a_emu_hecha(db_conn, pipeline)
    nobs = _trabajo(db_conn, iid, tracker="nobs")

    uq._run_queued_job(nobs)

    fila = _fila(db_conn, nobs)
    assert fila["status"] != "failed", _logs(db_conn, nobs)
    assert pipeline.preparados == []  # ni se rehace ni se enlaza otra vez: el mismo fichero
    assert fila["prepared_path"] == str(pipeline.destino)
    assert fila["reanuda_uploadrr"] == 1  # --resume: no borra capturas ni BASE.torrent de tmp/<nombre>
    # Hereda capturas y torrent; la subida a eMuwarez no es la suya.
    assert json.loads(fila["pasos_uploadrr_json"]) == ["screenshots", "torrent"]
    assert any(f"Se reutiliza la preparación de la subida #{emu} (eMuwarez)" in m for m in _logs(db_conn, nobs))


def test_si_el_original_cambio_la_subida_a_nobs_falla_sin_tocar_el_de_emuwarez(db_conn, pipeline):
    iid, emu = _subida_a_emu_hecha(db_conn, pipeline, huellas_preparacion_json=json.dumps({"Peli.2024.mkv": {}}))
    nobs = _trabajo(db_conn, iid, tracker="nobs")

    uq._run_queued_job(nobs)

    fila = _fila(db_conn, nobs)
    assert fila["status"] == "failed" and f"subida #{emu} (eMuwarez)" in fila["error_message"]
    assert pipeline.preparados == []


def test_la_misma_subida_al_mismo_tracker_sigue_bloqueada(db_conn, pipeline):
    """Lo de siempre: si eMuwarez ya lo tiene, una segunda subida a eMuwarez no rehace su fichero."""
    iid, emu = _subida_a_emu_hecha(db_conn, pipeline)
    otra = _trabajo(db_conn, iid)

    uq._run_queued_job(otra)

    fila = _fila(db_conn, otra)
    assert fila["status"] == "failed" and f"es de la subida #{emu}" in fila["error_message"]


# ---------------------------------------------------------------------------
# Lo que ve el usuario
# ---------------------------------------------------------------------------

def test_los_pasos_dicen_a_que_tracker_se_sube():
    from emuwarez_uploadrr.services.subida_pasos import pasos_de_subida
    (subida,) = [p for p in pasos_de_subida("completed", "completed", None, None, "N.O.B.S") if p["clave"] == "subida"]
    assert subida["subpasos"][-1]["nombre"] == "Subida a N.O.B.S"
    (subida,) = [p for p in pasos_de_subida("completed", "completed", None) if p["clave"] == "subida"]
    assert subida["subpasos"][-1]["nombre"] == "Subida a eMuwarez"


def test_una_subida_a_nobs_no_se_da_por_pendiente_de_moderacion_en_emuwarez():
    from datetime import datetime, timedelta, timezone

    from emuwarez_uploadrr.services.mis_torrents import pendiente_de_moderacion
    ayer = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    lectura = (datetime.now(timezone.utc), frozenset())
    job = {"status": "completed", "result_torrent_id": "7", "finished_at": ayer, "anonimo": 0}
    assert pendiente_de_moderacion({**job, "tracker": "emu"}, lectura) is True
    assert pendiente_de_moderacion({**job, "tracker": "nobs"}, lectura) is False
