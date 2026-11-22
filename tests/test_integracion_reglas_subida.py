"""Fase B del plan multitracker: la subida por tracker usa las normas de ese tracker (services/reglas_tracker.py).

Las comprobaciones previas, el plan de un pack (capítulo a capítulo) y el encolado miran las normas de idioma del tracker
del trabajo; el argv lleva `--variante` con la categoría que le toca allí. eMuwarez sigue como siempre, sin variante.
"""
from __future__ import annotations

import json

import pytest

from emuwarez_uploadrr.services import trackers
from emuwarez_uploadrr.services import upload_queue as uq
from emuwarez_uploadrr.services.season_pack import build_season_pack_plan
from emuwarez_uploadrr.services.settings import set_setting
from emuwarez_uploadrr.services.upload_execution import build_uploadrr_execution_command


@pytest.fixture(autouse=True)
def _biblioteca_en_tmp(tmp_path, monkeypatch):
    from emuwarez_uploadrr.services import uploadrr_contract
    monkeypatch.setattr(uploadrr_contract, "DEFAULT_ALLOWED_ROOTS", [str(tmp_path)])


@pytest.fixture
def db_conn(isolated_db, tmp_path):
    from emuwarez_uploadrr.database import connect, init_db
    conn = connect(isolated_db / "emuwarez_uploadrr.db")
    init_db(conn)
    (tmp_path / "U").mkdir()
    (tmp_path / "U" / "upload.py").write_text("")
    for clave, valor in (("emu.api_key", "a"), ("nobs.api_key", "b"), ("mnv.api_key", "c"),
                         ("tmdb.api_key", "d"), ("uploadrr.path", str(tmp_path / "U"))):
        set_setting(conn, clave, valor)
    conn.commit()
    return conn


def _peli(conn, tmp_path, audio, subs=None, *, mal_id=None):
    ruta = tmp_path / "Peli.2024.1080p.WEB-DL.mkv"
    ruta.write_bytes(b"x")
    cur = conn.execute(
        """INSERT INTO library_items (item_type, title, year, tmdb_id, local_status, request_status,
                                      nomenclature_status, path, resolution, audio_languages, subtitle_languages,
                                      mal_id, size_bytes, updated_at)
           VALUES ('movie', 'Peli', 2024, 603, 'Disponible', 'Sin petición', 'N/A', ?, '1080p', ?, ?, ?, 100,
                   CURRENT_TIMESTAMP)""", (str(ruta), audio, subs, mal_id))
    conn.commit()
    return cur.lastrowid


def _trabajo(conn, item_id, tracker):
    cur = conn.execute(
        "INSERT INTO upload_jobs (library_item_id, status, stage, title, path, tracker, created_at, updated_at)"
        " VALUES (?, 'preparing', 'verificacion_presubida', 'X', '', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (item_id, tracker))
    conn.commit()
    return cur.lastrowid


def _argv_del_trabajo(conn, job_id):
    fila = dict(conn.execute("SELECT * FROM upload_jobs WHERE id = ?", (job_id,)).fetchone())
    return fila, build_uploadrr_execution_command(fila, conn=conn)["args"]


def _listo(conn, item_id, tracker, monkeypatch, season_overrides=None):
    from emuwarez_uploadrr.services import upload_preflight
    # Las precondiciones del fichero (rutas, nomenclatura) no son de este test.
    monkeypatch.setattr(upload_preflight, "validate_upload_preconditions", lambda *a, **k: {"ok": True, "errors": []})
    job_id = _trabajo(conn, item_id, tracker)
    ok = uq._populate_ready(job_id, item_id, conn, season_overrides=season_overrides)
    return ok, job_id


# ---------------------------------------------------------------------------
# argv: --variante del tracker del trabajo, nunca en eMuwarez
# ---------------------------------------------------------------------------

def test_un_anime_a_nobs_lleva_variante_anime(db_conn, tmp_path, monkeypatch):
    iid = _peli(db_conn, tmp_path, "spa / jpn", mal_id="5114")
    ok, job_id = _listo(db_conn, iid, "nobs", monkeypatch)
    assert ok
    fila, argv = _argv_del_trabajo(db_conn, job_id)
    assert json.loads(fila["uploadrr_command_json"])["variante"] == "anime"
    assert argv[argv.index("-tk") + 1] == "NOBS"
    assert argv[argv.index("--variante") + 1] == "anime"


def test_el_mismo_anime_a_emuwarez_no_lleva_variante(db_conn, tmp_path, monkeypatch):
    iid = _peli(db_conn, tmp_path, "spa / jpn", mal_id="5114")
    ok, job_id = _listo(db_conn, iid, "emu", monkeypatch)
    assert ok
    fila, argv = _argv_del_trabajo(db_conn, job_id)
    assert "variante" not in json.loads(fila["uploadrr_command_json"])
    assert "--variante" not in argv and argv[argv.index("-tk") + 1] == "EMU"


def test_emuwarez_no_lleva_variante_aunque_el_comando_guardado_la_tenga(db_conn, tmp_path):
    comando = {"runtime_type": "uploadrr_emu", "title": "Peli", "variante": "anime"}
    job = {"id": 1, "tracker": "emu", "prepared_path": str(tmp_path / "Peli.mkv"),
           "uploadrr_command_json": json.dumps(comando)}
    assert "--variante" not in build_uploadrr_execution_command(job, conn=db_conn)["args"]


def test_un_anime_solo_en_catalan_a_milnueve_va_a_anime_en_catalan(db_conn, tmp_path, monkeypatch):
    iid = _peli(db_conn, tmp_path, "cat / jpn", mal_id="5114")
    r = uq.queue_upload_for_item(iid, tracker="mnv", conn=db_conn)
    assert r["status"] == "queued", r
    db_conn.execute("DELETE FROM upload_jobs")
    ok, job_id = _listo(db_conn, iid, "mnv", monkeypatch)
    assert ok
    _, argv = _argv_del_trabajo(db_conn, job_id)
    assert argv[argv.index("-tk") + 1] == "MNV" and argv[argv.index("--variante") + 1] == "anime_catalan"


def test_una_peli_solo_en_latino_a_milnueve_va_a_latino(db_conn, tmp_path, monkeypatch):
    iid = _peli(db_conn, tmp_path, "es-419 / eng")
    ok, job_id = _listo(db_conn, iid, "mnv", monkeypatch)
    assert ok
    _, argv = _argv_del_trabajo(db_conn, job_id)
    assert argv[argv.index("--variante") + 1] == "latino"


# ---------------------------------------------------------------------------
# Se rechaza antes de lanzar, con el mensaje de su norma
# ---------------------------------------------------------------------------

def test_algo_solo_en_catalan_no_se_encola_para_nobs(db_conn, tmp_path):
    iid = _peli(db_conn, tmp_path, "cat / jpn", mal_id="5114")
    r = uq.queue_upload_for_item(iid, tracker="nobs", conn=db_conn)
    assert r["status"] == "blocked" and r["errors"][0].startswith("N.O.B.S no lo admite")
    assert db_conn.execute("SELECT COUNT(*) FROM upload_jobs").fetchone()[0] == 0


def test_y_si_los_idiomas_cambian_despues_de_encolar_falla_antes_de_lanzar(db_conn, tmp_path, monkeypatch):
    iid = _peli(db_conn, tmp_path, "cat")
    ok, job_id = _listo(db_conn, iid, "nobs", monkeypatch)
    fila = dict(db_conn.execute("SELECT status, error_message FROM upload_jobs WHERE id = ?", (job_id,)).fetchone())
    assert not ok and fila["status"] == "failed" and fila["error_message"].startswith("N.O.B.S no lo admite")


def test_emuwarez_sigue_encolando_como_siempre(db_conn, tmp_path):
    """La norma de eMuwarez no se mira al encolar (va en las comprobaciones del asistente): sin cambios."""
    iid = _peli(db_conn, tmp_path, "cat")
    assert uq.queue_upload_for_item(iid, conn=db_conn)["status"] == "queued"


# ---------------------------------------------------------------------------
# Las comprobaciones con el tracker
# ---------------------------------------------------------------------------

def test_las_comprobaciones_de_un_anime_para_nobs_usan_su_norma(db_conn, tmp_path):
    from emuwarez_uploadrr.services.upload_checks import run_upload_checks
    iid = _peli(db_conn, tmp_path, "es-419 / jpn", mal_id="5114")
    nobs = {c["id"]: c for c in run_upload_checks(db_conn, iid, scope="content", tracker="nobs")["checks"]}
    emu = {c["id"]: c for c in run_upload_checks(db_conn, iid, scope="content")["checks"]}
    assert nobs["spanish"]["status"] == "ok" and nobs["spanish"]["label"] == "Idiomas admitidos en N.O.B.S"
    assert emu["spanish"]["status"] == "fail" and emu["spanish"]["label"] == "No tiene castellano"


def test_la_api_de_reglas_dice_la_norma_de_cada_tracker(db_conn, tmp_path, client, auth_headers):
    iid = _peli(db_conn, tmp_path, "cat / jpn", mal_id="5114")
    mnv = client.get(f"/api/library/{iid}/upload/reglas", params={"tracker": "mnv"}, headers=auth_headers).json()
    nobs = client.get(f"/api/library/{iid}/upload/reglas", params={"tracker": "nobs"}, headers=auth_headers).json()
    assert mnv == {"tracker": "mnv", "permitido": True, "motivo": "Audio en catalán, sin castellano.",
                   "variante": "anime_catalan"}
    assert nobs["permitido"] is False and nobs["variante"] is None
    assert client.get(f"/api/library/{iid}/upload/reglas", params={"tracker": "xyz"}, headers=auth_headers).status_code == 422
    assert client.post(f"/api/library/{iid}/upload/checks", params={"tracker": "xyz"}, headers=auth_headers).status_code == 422


# ---------------------------------------------------------------------------
# Packs: la norma del tracker en cada capítulo
# ---------------------------------------------------------------------------

def _temporada(conn, tmp_path, audios):
    cur = conn.execute(
        """INSERT INTO library_items (item_type, title, year, local_status, request_status, nomenclature_status,
                                      updated_at)
           VALUES ('series', 'Serie', 2020, 'Parcial', 'Sin petición', 'N/A', CURRENT_TIMESTAMP)""")
    serie = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO library_items (item_type, title, year, local_status, request_status, nomenclature_status,
                                      parent_id, season_number, updated_at)
           VALUES ('season', 'Serie - Temporada 1', 2020, 'Disponible', 'Sin petición', 'N/A', ?, 1,
                   CURRENT_TIMESTAMP)""", (serie,))
    temporada = cur.lastrowid
    carpeta = tmp_path / "Serie" / "Season 1"
    carpeta.mkdir(parents=True)
    for n, audio in enumerate(audios, start=1):
        f = carpeta / f"S01E0{n}.mkv"
        f.write_bytes(b"video")
        conn.execute(
            """INSERT INTO library_items (item_type, title, year, local_status, request_status, nomenclature_status,
                                          parent_id, season_number, episode_number, path, quality, video_codec,
                                          audio_codec, audio_languages, subtitle_languages, source, updated_at)
               VALUES ('episode', ?, 2020, 'Disponible', 'Sin petición', 'No adaptada', ?, 1, ?, ?, 'WEBDL-1080p',
                       'x264', 'AC3', ?, NULL, 'WEB-DL', CURRENT_TIMESTAMP)""",
            (f"Serie S01E0{n}", temporada, n, str(f), audio))
    conn.commit()
    return temporada


def test_un_pack_a_milnueve_usa_su_norma_en_cada_capitulo(db_conn, tmp_path):
    temporada = _temporada(db_conn, tmp_path, ["es-419", "es-419"])
    emu = build_season_pack_plan(temporada, conn=db_conn)
    mnv = build_season_pack_plan(temporada, conn=db_conn, tracker="mnv")
    assert emu["status"] == "blocked" and any("no tiene castellano" in e for e in emu["errors"])
    assert emu["variante"] is None
    assert mnv["status"] == "valid", mnv["errors"]
    assert mnv["variante"] == "latino"


def test_un_capitulo_que_milnueve_no_admite_bloquea_el_pack_con_su_mensaje(db_conn, tmp_path):
    temporada = _temporada(db_conn, tmp_path, ["es-419", "eng"])
    mnv = build_season_pack_plan(temporada, conn=db_conn, tracker="mnv")
    assert mnv["status"] == "blocked"
    assert any(e.startswith("Capítulo 2: Milnueve no lo admite") for e in mnv["errors"])
    r = uq.queue_upload_for_item(temporada, tracker="mnv", conn=db_conn)
    assert r["status"] == "blocked" and r["errors"][0].startswith("Capítulo 2: Milnueve no lo admite")


def test_el_pack_a_milnueve_llega_al_argv_con_su_variante(db_conn, tmp_path, monkeypatch):
    from emuwarez_uploadrr.services import source_assistant
    monkeypatch.setattr(source_assistant, "get_tmdb_available_providers", lambda *a, **kw: [])
    temporada = _temporada(db_conn, tmp_path, ["es-419", "es-419"])
    ok, job_id = _listo(db_conn, temporada, "mnv", monkeypatch)
    assert ok
    _, argv = _argv_del_trabajo(db_conn, job_id)
    assert argv[argv.index("-tk") + 1] == "MNV" and argv[argv.index("--variante") + 1] == "latino"
    assert trackers.obtener("mnv").uploadrr == "MNV"
