"""La costura con Uploadrr-EMU en los casos que el fake de flujo fijo no puede simular.

Cada test lanza el subproceso de verdad (`upload_execution`) contra tests/fixtures/fake_uploadrr_emu.py en modo guion,
con líneas copiadas literales del Uploadrr-EMU real, y comprueba el estado del trabajo, los pasos guardados y lo que se le dice al usuario.
"""
from __future__ import annotations

import json
import time

import psutil
import pytest

from emuwarez_uploadrr.database import connect, db_path, init_db
from emuwarez_uploadrr.services import subida_pasos as sp
from emuwarez_uploadrr.services.upload_execution import (
    CONFIRMATION_TOKEN,
    consultas,
    get_upload_job,
    start_upload_job,
)

# Líneas del Uploadrr-EMU real (UNIT3D.py, checks.py, meta.py, upload.py, clients.py).
BUSCA = "Searching for existing torrents on EMU..."
SIN_DUPES = "No dupes found"
CAPTURAS = "Step done: screenshots"
TORRENT = "Step done: torrent"
SUBIENDO = "Uploading to EMU"
URL = "Torrent URL: https://fake-emu.invalid.test/torrents/4242"
EXITO = "Torrent uploaded successfully!"

HASTA_EL_POST = [{"out": ln} for ln in (BUSCA, SIN_DUPES, CAPTURAS, TORRENT, SUBIENDO)]


@pytest.fixture
def db_conn(isolated_db):
    conn = connect(db_path())
    init_db(conn)
    yield conn
    conn.close()


def _lanzar(db_conn, tmp_path, fake_dir, python_path):
    for k, v in {"uploadrr.path": str(fake_dir), "uploadrr.python_path": python_path,
                 "tmdb.api_key": "tmdb_secret_abc123", "emu.api_key": "emu_secret_xyz789",
                 "uploadrr.timeout": "30"}.items():
        db_conn.execute("INSERT OR REPLACE INTO settings (key, value, is_secret) VALUES (?, ?, 0)", (k, v))
    prepared = tmp_path / "prepared.mkv"
    prepared.write_bytes(b"\x00" * 16)
    cur = db_conn.execute(
        "INSERT INTO upload_jobs (title, path, status, stage, source_path, prepared_path, uploadrr_command_json, "
        "created_at, updated_at) VALUES ('T', ?, 'ready', 'preflight', ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (str(prepared), str(prepared), str(prepared),
         json.dumps({"category": "movie", "tmdb_id": "1", "resolution": "1080p", "title": "T"})))
    db_conn.commit()
    jid = cur.lastrowid
    assert start_upload_job(jid, dry_run=False, start=True, confirmation=CONFIRMATION_TOKEN, conn=db_conn)["action"] == "started"
    return jid


def _terminal(db_conn, jid, plazo=15.0):
    limite = time.monotonic() + plazo
    while time.monotonic() < limite:
        if get_upload_job(jid, conn=db_conn)["status"] in ("completed", "failed", "cancelled"):
            time.sleep(0.3)  # el finally del hilo (restaurar config, avisos) va justo detrás
            return get_upload_job(jid, conn=db_conn)
        time.sleep(0.05)
    raise AssertionError("la subida no terminó")


def _avisos(db_conn):
    return [(f["event_type"], f["title"], f["body"] or "") for f in db_conn.execute("SELECT * FROM notifications ORDER BY id")]


def _estados_de_pasos(job):
    pasos = sp.pasos_de_subida(job["status"], job["stage"], job.get("etapa_parada"), job.get("pasos_uploadrr_json"))
    return {p["clave"]: p["estado"] for p in pasos}


def test_muere_entre_el_post_y_torrent_url(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    guion_uploadrr([*HASTA_EL_POST, {"err": "Killed"}, {"exit": 137}])
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path)

    job = _terminal(db_conn, jid)

    assert (job["status"], job["stage"], job["exit_code"]) == ("failed", "failed", 137)
    # Sin `Torrent URL:` la app no sabe si el torrent llegó al tracker: no inventa ni ficha ni id.
    assert not job["result_url"] and not job["result_torrent_id"] and not job["torrent_subido"]
    # Lo que Uploadrr-EMU sí dio por terminado antes del POST se conserva para el reintento.
    assert sp.leer_subpasos(job["pasos_uploadrr_json"]) == ["screenshots", "torrent"]
    assert "Exit code: 137" in job["error_message"] and "Killed" in job["error_message"]
    assert [t for e, t, _b in _avisos(db_conn) if e == "upload_failed"], "el usuario no recibió el aviso de subida fallida"
    assert _estados_de_pasos(job)["subida"] == "fallido"


def test_muerto_por_señal_entre_el_post_y_torrent_url(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    guion_uploadrr([*HASTA_EL_POST, {"kill": 9}])
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path)

    job = _terminal(db_conn, jid)

    assert (job["status"], job["exit_code"]) == ("failed", -9)
    assert not job["result_url"] and not job["torrent_subido"]
    assert "Exit code: -9" in job["error_message"]


def test_colgado_sin_salida_lo_corta_el_timeout(
        db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr, monkeypatch):
    guion_uploadrr([{"hang": True}])
    real = consultas.get_runtime_settings
    monkeypatch.setattr(consultas, "get_runtime_settings", lambda conn: {**real(conn), "timeout": 0.4})
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path)
    limite = time.monotonic() + 5
    pid = None
    while pid is None and time.monotonic() < limite:
        pid = get_upload_job(jid, conn=db_conn).get("process_id")
        time.sleep(0.02)

    job = _terminal(db_conn, jid)

    assert (job["status"], job["stage"]) == ("failed", "timeout")
    assert "timeout" in job["error_message"].lower()
    assert not job["result_url"] and not job["torrent_subido"]
    assert sp.leer_subpasos(job["pasos_uploadrr_json"]) == []
    assert [b for e, _t, b in _avisos(db_conn) if e == "upload_failed" and "timeout" in b.lower()]
    if pid:
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_colgado_tras_el_post_con_la_ficha_ya_impresa_conserva_la_ficha(
        db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr, monkeypatch):
    """Uploadrr-EMU imprimió `Torrent URL:` y se colgó (qBittorrent): el torrent ya está en eMuwarez."""
    guion_uploadrr([*HASTA_EL_POST, {"out": URL}, {"hang": True}])
    real = consultas.get_runtime_settings
    monkeypatch.setattr(consultas, "get_runtime_settings", lambda conn: {**real(conn), "timeout": 0.5})
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path)

    job = _terminal(db_conn, jid)

    assert (job["status"], job["stage"]) == ("failed", "timeout")
    assert job["result_url"] == "https://fake-emu.invalid.test/torrents/4242"
    assert job["result_torrent_id"] == "4242" and job["torrent_subido"] == 1
    assert sp.leer_subpasos(job["pasos_uploadrr_json"]) == ["screenshots", "torrent", "tracker"]


def test_salida_cortada_antes_de_la_url_y_exit_1(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    guion_uploadrr([*HASTA_EL_POST, {"raw": "Torrent U"}, {"exit": 1}])
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path)

    job = _terminal(db_conn, jid)

    assert (job["status"], job["exit_code"]) == ("failed", 1)
    assert not job["result_url"] and not job["torrent_subido"]
    assert sp.leer_subpasos(job["pasos_uploadrr_json"]) == ["screenshots", "torrent"]


@pytest.mark.xfail(strict=True, reason="BUG: una línea `Torrent URL:` cortada a medias (sin salto de línea) con salida no cero se da por "
                   "completa: la app guarda un id falso (12 en vez de 1234) y marca torrent_subido, que habilita «Reenviar a qBittorrent»")
def test_url_cortada_a_medias_no_se_da_por_la_ficha(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    guion_uploadrr([*HASTA_EL_POST, {"raw": "Torrent URL: https://fake-emu.invalid.test/torrents/12"}, {"kill": 9}])
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path)

    job = _terminal(db_conn, jid)

    assert job["status"] == "failed"
    assert job["result_torrent_id"] != "12"


def test_salida_cortada_tras_la_ficha_y_exit_0_es_una_subida_buena(
        db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    guion_uploadrr([*HASTA_EL_POST, {"out": URL}, {"out": EXITO}, {"out": "Adding and rechecking torrent"},
                    {"raw": "Added to: /downl"}])
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path)

    job = _terminal(db_conn, jid)

    assert (job["status"], job["exit_code"]) == ("completed", 0)
    assert job["result_torrent_id"] == "4242"
    assert sp.leer_subpasos(job["pasos_uploadrr_json"]) == ["screenshots", "torrent", "tracker"]
    assert _estados_de_pasos(job) and set(_estados_de_pasos(job).values()) == {"hecho"}
