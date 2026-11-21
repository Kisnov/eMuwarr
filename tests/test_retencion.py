"""Lo que crecía sin que nadie lo borrara: los temporales de cada subida, las propuestas de nombre caducadas, la caché
de otra versión del motor de fuentes, las sesiones caducadas y lo que deja a medias un reinicio.

Bloque 4 del análisis de la base de 2026-11-19 (CRE-1, CRE-5, CRE-6, CRE-7, INT-6).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from emuwarez_uploadrr.database import connect, db_path, init_db, set_state
from emuwarez_uploadrr.services import maintenance_service as mant
from emuwarez_uploadrr.services import uploadrr_eventos
from emuwarez_uploadrr.services.settings import set_setting
from emuwarez_uploadrr.services.source_engine.constants import ENGINE_VERSION
from emuwarez_uploadrr.services.tasks import cerrar_tareas_interrumpidas

AHORA = datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)


def _hace(dias: int) -> str:
    return (AHORA - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def conn(isolated_db):
    c = connect(db_path())
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def tmp_uploadrr(conn, tmp_path):
    """La carpeta tmp de Uploadrr-EMU, con `uploadrr.path` apuntando a su carpeta."""
    carpeta = tmp_path / "Uploadrr"
    (carpeta / "tmp").mkdir(parents=True)
    set_setting(conn, "uploadrr.path", str(carpeta))
    conn.commit()
    return carpeta / "tmp"


def _envejecer(ruta, dias: int) -> None:
    marca = (AHORA - timedelta(days=dias)).timestamp()
    os.utime(ruta, (marca, marca), follow_symlinks=False)


def _temporal(tmp, nombre: str, dias: int):
    """Lo que deja una subida, sin tocar desde hace `dias`."""
    carpeta = tmp / nombre
    carpeta.mkdir()
    for fichero in ("meta.json", "BASE.torrent"):
        (carpeta / fichero).write_bytes(b"x")
        _envejecer(carpeta / fichero, dias)
    _envejecer(carpeta, dias)
    return carpeta


def _subida(conn, estado: str, ruta: str, **columnas) -> int:
    job_id = conn.execute(
        "INSERT INTO upload_jobs(title, path, status) VALUES ('t', ?, ?)", (ruta, estado)).lastrowid
    for columna, valor in columnas.items():
        conn.execute(f"UPDATE upload_jobs SET {columna} = ? WHERE id = ?", (valor, job_id))
    conn.commit()
    return job_id


# ---------------------------------------------------------------------------
# CRE-1: los temporales de cada subida
# ---------------------------------------------------------------------------

def test_se_borran_los_temporales_que_nadie_toca_y_no_los_recientes(conn, tmp_uploadrr):
    vieja = _temporal(tmp_uploadrr, "Peli.Vieja.2020", 8)
    reciente = _temporal(tmp_uploadrr, "Peli.Reciente.2026", 6)
    tocada = _temporal(tmp_uploadrr, "Serie.S01", 30)
    (tocada / "captura.png").write_bytes(b"x")
    _envejecer(tocada / "captura.png", 1)
    _envejecer(tocada, 30)  # la carpeta parece vieja, pero dentro hay algo de ayer
    suelto = tmp_uploadrr / "suelto.txt"
    suelto.write_bytes(b"x")
    _envejecer(suelto, 400)

    assert mant.podar_tmp_de_uploadrr(conn, 7, AHORA) == 1

    assert not vieja.exists()
    assert reciente.exists() and tocada.exists() and suelto.exists()


def test_una_carpeta_temporal_vacia_se_trata_como_las_demas(conn, tmp_uploadrr):
    vieja, reciente = tmp_uploadrr / "Vacia.Vieja", tmp_uploadrr / "Vacia.Reciente"
    for carpeta, dias in ((vieja, 30), (reciente, 1)):
        carpeta.mkdir()
        _envejecer(carpeta, dias)

    estado, resumen = mant.ejecutar_mantenimiento(conn, AHORA)

    assert estado == "completed" and "1 carpetas temporales de subidas" in resumen
    assert not vieja.exists() and reciente.exists()


def test_los_temporales_de_una_subida_en_marcha_no_se_tocan(conn, tmp_uploadrr):
    en_marcha = _temporal(tmp_uploadrr, "Peli.En.Marcha", 30)
    reenviando = _temporal(tmp_uploadrr, "Peli.Reenviando", 30)
    terminada = _temporal(tmp_uploadrr, "Peli.Terminada", 30)
    _subida(conn, "running", "/media/original.mkv", prepared_path="/data/uploads/Peli.En.Marcha")
    _subida(conn, "completed", "/data/uploads/Peli.Reenviando", reenvio_qbit_estado="en_curso")
    _subida(conn, "completed", "/data/uploads/Peli.Terminada")

    assert mant.podar_tmp_de_uploadrr(conn, 7, AHORA) == 1

    assert en_marcha.exists() and reenviando.exists()
    assert not terminada.exists()


def test_un_enlace_dentro_de_tmp_no_se_sigue(conn, tmp_uploadrr, tmp_path):
    fuera = tmp_path / "biblioteca"
    fuera.mkdir()
    (fuera / "pelicula.mkv").write_bytes(b"x")
    _envejecer(fuera / "pelicula.mkv", 400)
    _envejecer(fuera, 400)
    enlace = tmp_uploadrr / "Enlace"
    enlace.symlink_to(fuera, target_is_directory=True)
    _envejecer(enlace, 400)

    assert mant.podar_tmp_de_uploadrr(conn, 7, AHORA) == 0

    assert (fuera / "pelicula.mkv").exists()


def test_sin_carpeta_de_uploadrr_no_hay_nada_que_borrar(conn, tmp_path):
    assert mant.podar_tmp_de_uploadrr(conn, 7, AHORA) == 0
    set_setting(conn, "uploadrr.path", str(tmp_path / "no-existe"))
    assert mant.podar_tmp_de_uploadrr(conn, 7, AHORA) == 0


def test_el_mantenimiento_borra_los_temporales_con_los_dias_de_su_opcion(conn, tmp_uploadrr):
    de_diez = _temporal(tmp_uploadrr, "Diez.Dias", 10)
    assert mant.valor_opcion(conn, "mantenimiento.dias_tmp") == 7
    mant.guardar_opciones(conn, {"mantenimiento.dias_tmp": 14})

    estado, resumen = mant.ejecutar_mantenimiento(conn, AHORA)

    assert estado == "completed" and de_diez.exists()
    assert "0 carpetas temporales de subidas" in resumen

    mant.guardar_opciones(conn, {"mantenimiento.dias_tmp": 7})
    estado, resumen = mant.ejecutar_mantenimiento(conn, AHORA)

    assert estado == "completed" and not de_diez.exists()
    assert "1 carpetas temporales de subidas" in resumen


# ---------------------------------------------------------------------------
# CRE-5: propuestas de nombre caducadas
# ---------------------------------------------------------------------------

def _propuesta(conn, estado: str, dias: int) -> int:
    return conn.execute(
        "INSERT INTO nomenclature_proposals(library_item_id, status, updated_at) VALUES (1, ?, ?)",
        (estado, _hace(dias))).lastrowid


def test_se_borran_las_propuestas_caducadas_viejas_que_ninguna_subida_guarda(conn):
    caducada_vieja = _propuesta(conn, "expired", 120)
    de_una_subida = _propuesta(conn, "expired", 120)
    caducada_reciente = _propuesta(conn, "expired", 30)
    aplicada_vieja = _propuesta(conn, "applied", 400)
    _subida(conn, "completed", "/p", nomenclature_proposal_id=de_una_subida)

    assert mant.podar_propuestas_caducadas(conn, AHORA) == 1

    quedan = {r[0] for r in conn.execute("SELECT id FROM nomenclature_proposals")}
    assert quedan == {de_una_subida, caducada_reciente, aplicada_vieja}
    assert caducada_vieja not in quedan


# ---------------------------------------------------------------------------
# CRE-6 y CRE-7: lo que ya no se puede volver a leer
# ---------------------------------------------------------------------------

def test_se_borran_la_cache_de_otro_motor_las_sesiones_caducadas_y_las_marcas_de_lotes_sin_subidas(conn):
    for version in ("0.0.1", ENGINE_VERSION):
        conn.execute(
            "INSERT INTO source_engine_cache(path, size_bytes, mtime, engine_version, result_json) "
            "VALUES (?, 1, 1, ?, '{}')", (f"/p/{version}", version))
    usuario = conn.execute("INSERT INTO users(username, password_hash) VALUES ('u', 'h')").lastrowid
    for token, dias in (("viejo", 120), ("reciente", 20)):
        conn.execute(
            "INSERT INTO auth_sessions(user_id, token_hash, created_at, last_used_at) VALUES (?, ?, ?, ?)",
            (usuario, token, _hace(dias + 5), _hace(dias)))
    _subida(conn, "completed", "/p", lote_id="lote-con-subidas")
    set_state(conn, "lote_avisado:lote-con-subidas", "[1]")
    set_state(conn, "lote_avisado:lote-borrado", "[2]")
    conn.commit()

    assert mant.podar_restos(conn, AHORA) == 3

    assert [r[0] for r in conn.execute("SELECT engine_version FROM source_engine_cache")] == [ENGINE_VERSION]
    assert [r[0] for r in conn.execute("SELECT token_hash FROM auth_sessions")] == ["reciente"]
    assert [r[0] for r in conn.execute("SELECT key FROM app_state WHERE key LIKE 'lote_avisado:%'")] == [
        "lote_avisado:lote-con-subidas"]


# ---------------------------------------------------------------------------
# INT-6 y CRE-7: lo que deja a medias un reinicio
# ---------------------------------------------------------------------------

def test_al_arrancar_las_tareas_que_seguian_corriendo_quedan_como_fallidas(conn):
    conn.execute("INSERT INTO task_runs(name, status, summary) VALUES ('sync-library', 'running', 'x')")
    conn.execute("INSERT INTO task_runs(name, status, summary) VALUES ('sync-library', 'completed', 'bien')")
    conn.commit()

    assert cerrar_tareas_interrumpidas(conn) == 1

    assert [(r["status"], r["summary"], r["finished_at"] is not None) for r in conn.execute(
        "SELECT status, summary, finished_at FROM task_runs ORDER BY id")] == [
        ("failed", "Interrumpida al reiniciarse la aplicación", True), ("completed", "bien", False)]


def test_al_arrancar_se_borran_los_eventos_de_subidas_que_murieron(conn):
    assert uploadrr_eventos.barrer_eventos() == 0  # sin carpeta todavía
    carpeta = db_path().parent / "eventos_uploadrr"
    carpeta.mkdir()
    (carpeta / "12.jsonl").write_text("{}\n")
    (carpeta / "12-reenvio.jsonl").write_text("{}\n")

    assert uploadrr_eventos.barrer_eventos() == 2
    assert list(carpeta.iterdir()) == []


def test_el_arranque_de_la_aplicacion_cierra_lo_interrumpido(conn):
    from fastapi.testclient import TestClient

    from emuwarez_uploadrr.web.app import create_app

    conn.execute("INSERT INTO task_runs(name, status) VALUES ('sync-library', 'running')")
    conn.commit()
    carpeta = db_path().parent / "eventos_uploadrr"
    carpeta.mkdir()
    (carpeta / "7.jsonl").write_text("{}\n")

    with TestClient(create_app()):
        pass

    assert conn.execute("SELECT status FROM task_runs").fetchone()[0] == "failed"
    assert list(carpeta.iterdir()) == []
