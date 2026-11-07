"""El aviso «Sin trailer» de Subidas: `upload_jobs.trailer_estado` (esquema 54).

La comprobación del tráiler al terminar una subida (services/trailer_check.py) deja su resultado en la fila, la lista se
puede filtrar por las que lo necesitan, «Ya lo he añadido» lo apaga (y se puede deshacer) y la migración deduce el de
las subidas de antes por lo que dejaron escrito.
"""
from __future__ import annotations

import json

import pytest

from emuwarez_uploadrr.database import _migrate_upload_jobs_trailer_estado, connect, db_path, init_db
from emuwarez_uploadrr.services import eventos
from emuwarez_uploadrr.services.trailer_check import (
    TRAILER_A_MANO,
    TRAILER_FALTA,
    TRAILER_INCLUIDO,
    marcar_trailer_a_mano,
)
from emuwarez_uploadrr.services.upload_execution import (
    _check_trailer_and_log,
    append_upload_log,
    count_upload_jobs,
    create_upload_artifact,
    list_upload_jobs,
)


@pytest.fixture
def db_conn(isolated_db):
    conn = connect(db_path())
    init_db(conn)
    yield conn
    conn.close()


def _job(conn, status="completed", *, title="Peli", nombre="Peli.2024-EMUWAREZ", trailer_estado=None):
    cur = conn.execute(
        "INSERT INTO upload_jobs (title, path, status, stage, nomenclature_snapshot_json, trailer_estado,"
        " created_at, updated_at) VALUES (?, '', ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (title, status, status, json.dumps({"proposed_filename": nombre}), trailer_estado),
    )
    conn.commit()
    return cur.lastrowid


def _estado(conn, jid):
    return conn.execute("SELECT trailer_estado FROM upload_jobs WHERE id = ?", (jid,)).fetchone()[0]


def _descripcion(conn, tmp_path, nombre: str, texto: str):
    conn.execute("INSERT OR REPLACE INTO settings (key, value, is_secret) VALUES ('uploadrr_path', ?, 0)", (str(tmp_path),))
    conn.commit()
    carpeta = tmp_path / "tmp" / nombre
    carpeta.mkdir(parents=True)
    (carpeta / "[EMU]DESCRIPTION.txt").write_text(texto, encoding="utf-8")


@pytest.fixture
def avisos(monkeypatch):
    publicados: list[tuple[str, dict]] = []
    from emuwarez_uploadrr.services import upload_states
    monkeypatch.setattr(upload_states, "publicar", lambda tipo, datos=None: publicados.append((tipo, datos or {})) or 1)
    return publicados


# ---------------------------------------------------------------------------
# La comprobación al terminar deja el estado en la fila
# ---------------------------------------------------------------------------

def test_con_trailer_queda_incluido_con_su_enlace(db_conn, tmp_path, avisos):
    jid = _job(db_conn)
    _descripcion(db_conn, tmp_path, "Peli.2024-EMUWAREZ", "[center][youtube]xyz987[/youtube][/center]")

    _check_trailer_and_log(jid)

    fila = db_conn.execute("SELECT trailer_estado, trailer_url FROM upload_jobs WHERE id = ?", (jid,)).fetchone()
    assert tuple(fila) == (TRAILER_INCLUIDO, "https://www.youtube.com/watch?v=xyz987")
    # La interfaz se entera: el aviso de 'completed' salió antes de la comprobación.
    assert (eventos.EVENTO_SUBIDA_ESTADO, {"job_id": jid, "status": None, "stage": None}) in avisos


def test_sin_trailer_queda_en_falta_y_avisa_a_la_interfaz(db_conn, tmp_path, avisos):
    jid = _job(db_conn)
    _descripcion(db_conn, tmp_path, "Peli.2024-EMUWAREZ", "sin tráiler")

    _check_trailer_and_log(jid)

    assert _estado(db_conn, jid) == TRAILER_FALTA
    assert [a for a in avisos if a[1].get("job_id") == jid]


def test_sin_descripcion_que_leer_no_dice_nada(db_conn, tmp_path, avisos):
    """Inconcluso: no se encontró el archivo. Mejor no avisar que avisar en falso."""
    jid = _job(db_conn)
    db_conn.execute("INSERT OR REPLACE INTO settings (key, value, is_secret) VALUES ('uploadrr_path', ?, 0)", (str(tmp_path),))
    db_conn.commit()

    _check_trailer_and_log(jid)

    assert _estado(db_conn, jid) is None
    assert not avisos


# ---------------------------------------------------------------------------
# El filtro de la lista y el recuento
# ---------------------------------------------------------------------------

def test_el_filtro_solo_da_las_que_les_falta(db_conn):
    falta = _job(db_conn, title="Falta", trailer_estado=TRAILER_FALTA)
    _job(db_conn, title="Incluido", trailer_estado=TRAILER_INCLUIDO)
    _job(db_conn, title="A mano", trailer_estado=TRAILER_A_MANO)
    _job(db_conn, title="Sin comprobar")

    filtros = {"sin_trailer": True, "hide_preparatory": True}
    assert [j["id"] for j in list_upload_jobs(filtros, conn=db_conn)] == [falta]
    assert count_upload_jobs(filtros, conn=db_conn) == 1
    assert count_upload_jobs({"hide_preparatory": True}, conn=db_conn) == 4


# ---------------------------------------------------------------------------
# «Ya lo he añadido» y deshacerlo
# ---------------------------------------------------------------------------

def test_marcar_a_mano_y_deshacer(db_conn, avisos):
    jid = _job(db_conn, trailer_estado=TRAILER_FALTA)

    assert marcar_trailer_a_mano(jid, conn=db_conn) == {"ok": True, "error": None, "trailer_estado": TRAILER_A_MANO}
    assert _estado(db_conn, jid) == TRAILER_A_MANO
    assert avisos
    # Repetirlo no falla ni vuelve a escribir.
    avisos.clear()
    assert marcar_trailer_a_mano(jid, conn=db_conn)["ok"]
    assert not avisos

    assert marcar_trailer_a_mano(jid, puesto=False, conn=db_conn)["trailer_estado"] == TRAILER_FALTA
    assert _estado(db_conn, jid) == TRAILER_FALTA


@pytest.mark.parametrize("estado", [None, TRAILER_INCLUIDO])
def test_no_inventa_un_aviso_donde_no_lo_hay(db_conn, estado):
    jid = _job(db_conn, trailer_estado=estado)
    assert marcar_trailer_a_mano(jid, conn=db_conn) == {"ok": False, "error": "sin_aviso", "trailer_estado": estado}
    assert marcar_trailer_a_mano(jid, puesto=False, conn=db_conn)["ok"] is False
    assert _estado(db_conn, jid) == estado


def test_una_subida_que_no_existe(db_conn):
    assert marcar_trailer_a_mano(999, conn=db_conn)["error"] == "not_found"


# ---------------------------------------------------------------------------
# La API
# ---------------------------------------------------------------------------

def test_la_api_lista_filtra_cuenta_y_marca(client, auth_headers, db_conn):
    falta = _job(db_conn, title="Falta", trailer_estado=TRAILER_FALTA)
    _job(db_conn, title="Incluido", trailer_estado=TRAILER_INCLUIDO)

    todas = client.get("/api/uploads?hide_preparatory=true", headers=auth_headers).json()
    assert {j["title"]: j["trailer_estado"] for j in todas} == {"Falta": TRAILER_FALTA, "Incluido": TRAILER_INCLUIDO}
    sin = client.get("/api/uploads?hide_preparatory=true&sin_trailer=true", headers=auth_headers).json()
    assert [j["id"] for j in sin] == [falta]
    assert client.get("/api/uploads/count?hide_preparatory=true&sin_trailer=true", headers=auth_headers).json() == {"total": 1}

    ruta = f"/api/uploads/{falta}/trailer-a-mano"
    assert client.post(ruta, json={}, headers=auth_headers).json() == {"trailer_estado": TRAILER_A_MANO}
    assert client.get(f"/api/uploads/{falta}", headers=auth_headers).json()["trailer_estado"] == TRAILER_A_MANO
    assert client.get("/api/uploads/count?sin_trailer=true", headers=auth_headers).json() == {"total": 0}
    assert client.post(ruta, json={"puesto": False}, headers=auth_headers).json() == {"trailer_estado": TRAILER_FALTA}


def test_la_api_rechaza_lo_que_no_tiene_aviso(client, auth_headers, db_conn):
    incluido = _job(db_conn, trailer_estado=TRAILER_INCLUIDO)
    assert client.post(f"/api/uploads/{incluido}/trailer-a-mano", json={}, headers=auth_headers).status_code == 409
    assert client.post("/api/uploads/999/trailer-a-mano", json={}, headers=auth_headers).status_code == 404
    assert client.post(f"/api/uploads/{incluido}/trailer-a-mano", json={}).status_code == 401


# ---------------------------------------------------------------------------
# La migración (esquema 54)
# ---------------------------------------------------------------------------

def test_la_migracion_deduce_las_de_antes_y_se_puede_repetir(db_conn):
    db_conn.execute("ALTER TABLE upload_jobs DROP COLUMN trailer_estado")
    con_trailer = db_conn.execute(
        "INSERT INTO upload_jobs (title, path, status, created_at, updated_at) "
        "VALUES ('Con', '', 'completed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)").lastrowid
    sin_trailer = db_conn.execute(
        "INSERT INTO upload_jobs (title, path, status, created_at, updated_at) "
        "VALUES ('Sin', '', 'completed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)").lastrowid
    sin_comprobar = db_conn.execute(
        "INSERT INTO upload_jobs (title, path, status, created_at, updated_at) "
        "VALUES ('Nada', '', 'completed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)").lastrowid
    db_conn.commit()
    create_upload_artifact(con_trailer, "trailer_url", name="Trailer", url="https://youtube.com/watch?v=a", conn=db_conn)
    append_upload_log(sin_trailer, "warning",
                      "No se encontró un trailer en la descripción generada por Uploadrr "
                      "(TMDb podría no tener uno para este contenido).", stream="system", conn=db_conn)

    _migrate_upload_jobs_trailer_estado(db_conn)
    db_conn.execute("UPDATE upload_jobs SET trailer_estado = 'a_mano' WHERE id = ?", (sin_trailer,))
    _migrate_upload_jobs_trailer_estado(db_conn)   # no pisa lo que marcó el usuario

    assert _estado(db_conn, con_trailer) == TRAILER_INCLUIDO
    assert _estado(db_conn, sin_trailer) == TRAILER_A_MANO
    assert _estado(db_conn, sin_comprobar) is None
