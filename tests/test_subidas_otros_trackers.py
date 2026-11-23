"""Lo que falló tras subir a N.O.B.S y Milnueve en la prueba del NAS (23/11): el tráiler, el registro del config y el
seeding en qBittorrent de cada trabajo, que daban por hecho que la subida era a eMuwarez."""
from __future__ import annotations

import json
import os
import time

import pytest

from emuwarez_uploadrr.database import connect, db, db_path, init_db
from emuwarez_uploadrr.services import seeding_qbit as sq
from emuwarez_uploadrr.services.trailer_check import TRAILER_INCLUIDO, check_trailer_for_job, find_emu_description_file
from emuwarez_uploadrr.services.upload_execution import _check_trailer_and_log, _notify_upload_completed
from tests.test_seeding_qbit import RAIZ, _config, _salud, _servidor, torrent

NOMBRE = "101.dalmatas.1961.720p.BluRay.x264.MULTI.DD.Mono.SUBS-DCINE"
FICHERO = f"{NOMBRE}.mkv"


@pytest.fixture
def db_conn(isolated_db):
    conn = connect(db_path())
    init_db(conn)
    yield conn
    conn.close()


def _job(conn, tracker="emu", *, ruta=None, result_url=None, status="completed"):
    cur = conn.execute(
        "INSERT INTO upload_jobs (tracker, title, path, status, stage, prepared_path, result_url,"
        " nomenclature_snapshot_json, created_at, updated_at)"
        " VALUES (?, '101 dálmatas', '', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (tracker, status, status, ruta, result_url, json.dumps({"proposed_filename": NOMBRE})),
    )
    conn.commit()
    return cur.lastrowid


def _descripcion(tmp_path, tracker, texto, nombre=NOMBRE):
    carpeta = tmp_path / "tmp" / nombre
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / f"[{tracker}]DESCRIPTION.txt").write_text(texto, encoding="utf-8")


def _logs(conn, jid):
    return [r[0] for r in conn.execute("SELECT message FROM upload_job_logs WHERE upload_job_id = ? ORDER BY sequence", (jid,))]


# --- 1. el tráiler se lee de la descripción del tracker del trabajo ----------------------------------------------

def test_el_trailer_de_nobs_se_lee_de_su_descripcion(tmp_path):
    _descripcion(tmp_path, "NOBS", "[youtube]nobs123[/youtube]")
    assert check_trailer_for_job(str(tmp_path), [NOMBRE], tracker="NOBS")["url"] == "https://www.youtube.com/watch?v=nobs123"
    # eMuwarez, como siempre, no lo ve: no es la suya.
    assert check_trailer_for_job(str(tmp_path), [NOMBRE])["checked"] is False


def test_nunca_vale_la_descripcion_de_otro_tracker_ni_en_la_busqueda_de_respaldo(tmp_path):
    _descripcion(tmp_path, "EMU", "[youtube]deemu[/youtube]")
    _descripcion(tmp_path, "EMU", "[youtube]deemu[/youtube]", nombre="otra carpeta")
    desde = "2000-01-01T00:00:00+00:00"
    assert find_emu_description_file(str(tmp_path), [NOMBRE], desde=desde, tracker="MNV") is None
    assert find_emu_description_file(str(tmp_path), ["No.Esta"], desde=desde, tracker="MNV") is None
    _descripcion(tmp_path, "MNV", "x", nombre="otra carpeta")
    encontrado = find_emu_description_file(str(tmp_path), ["No.Esta"], desde=desde, tracker="MNV")
    assert encontrado is not None and encontrado.name == "[MNV]DESCRIPTION.txt"


def test_al_terminar_una_subida_a_milnueve_comprueba_su_trailer(db_conn, tmp_path):
    db_conn.execute("INSERT OR REPLACE INTO settings (key, value, is_secret) VALUES ('uploadrr_path', ?, 0)",
                    (str(tmp_path),))
    db_conn.commit()
    _descripcion(tmp_path, "EMU", "sin tráiler")
    _descripcion(tmp_path, "MNV", "[youtube]mnv987[/youtube]")
    jid = _job(db_conn, "mnv")
    _check_trailer_and_log(jid)
    fila = db_conn.execute("SELECT trailer_estado, trailer_url FROM upload_jobs WHERE id = ?", (jid,)).fetchone()
    assert tuple(fila) == (TRAILER_INCLUIDO, "https://www.youtube.com/watch?v=mnv987")
    assert not any("No se pudo localizar" in m for m in _logs(db_conn, jid))


# --- 2. el registro del config nombra el bloque del tracker del trabajo -------------------------------------------

@pytest.mark.parametrize("tracker, bloque", [("emu", "TRACKERS.EMU.api_key"), ("nobs", "TRACKERS.NOBS.api_key"),
                                             ("mnv", "TRACKERS.MNV.api_key")])
def test_el_registro_del_config_nombra_el_tracker_de_la_subida(db_conn, tmp_path, tracker, bloque):
    from emuwarez_uploadrr.services.upload_execution import config as ue
    config = tmp_path / "Uploadrr" / "data" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("DEFAULT:\n  tmdb: ''\n", encoding="utf-8")
    jid = _job(db_conn, tracker)
    ue.preparar_config_de_la_subida(jid, str(tmp_path / "Uploadrr"), tmp_path / "subida.yaml")
    preparado = [m for m in _logs(db_conn, jid) if m.startswith("Config de Uploadrr-EMU preparado")]
    assert len(preparado) == 1 and bloque in preparado[0]
    otros = {"TRACKERS.EMU.api_key", "TRACKERS.NOBS.api_key", "TRACKERS.MNV.api_key"} - {bloque}
    assert not any(o in preparado[0] for o in otros)


# --- 3. el seeding de cada trabajo es el de su propio torrent ----------------------------------------------------

def _de(tracker_host, url, hash):
    """El torrent que Uploadrr-EMU añade tras subir: mismo fichero y ruta, su anuncio y su comentario."""
    t = torrent(FICHERO, hash=hash, tags="uploadrr")
    t["tracker"] = f"https://{tracker_host}/announce/CLAVE-DE-PASO"
    t["comment"] = f"Torrent descargado de algún sitio.\n{url}"
    return t


EMU_URL = "https://emu-warez.com/torrents/101"
NOBS_URL = "https://nobs.rawsmoke.net/torrents/55"
MNV_URL = "https://tracker.milnueve.cc/torrents/77"


def test_tres_torrents_del_mismo_fichero_cada_subida_el_suyo(db_conn, http_falso):
    _config(tag="uploadrr")
    ruta = f"{RAIZ}/{FICHERO}"
    emu = _job(db_conn, "emu", ruta=ruta, result_url=EMU_URL)
    nobs = _job(db_conn, "nobs", ruta=ruta, result_url=NOBS_URL)
    mnv = _job(db_conn, "mnv", ruta=ruta, result_url=MNV_URL)
    _servidor(http_falso, [_de("emu-warez.com", EMU_URL, "e" * 40), _de("nobs.rawsmoke.net", NOBS_URL, "b" * 40),
                           _de("tracker.milnueve.cc", MNV_URL, "c" * 40)])
    por_id = {i["job_id"]: i for i in _salud(refrescar=True)["items"]}
    assert (por_id[emu]["hash"], por_id[nobs]["hash"], por_id[mnv]["hash"]) == ("e" * 40, "b" * 40, "c" * 40)
    assert all(por_id[j]["estado"] == "sembrando" for j in (emu, nobs, mnv))


def test_sin_anuncio_que_funcione_basta_el_comentario():
    sin_anuncio = []
    for host, url, h in (("emu-warez.com", EMU_URL, "e"), ("nobs.rawsmoke.net", NOBS_URL, "b")):
        t = _de(host, url, h * 40)
        t["tracker"] = ""
        sin_anuncio.append(t)
    indices = sq._indices([sq._resumir(t) for t in sin_anuncio])
    t, como = sq.casar(f"{RAIZ}/{FICHERO}", indices, "uploadrr", RAIZ, tracker="nobs", result_url=NOBS_URL)
    assert t["hash"] == "b" * 40 and como == "ruta"


def test_el_torrent_de_otro_tracker_no_es_el_de_la_subida():
    """Solo está el de eMuwarez: la subida a N.O.B.S no lo toma por suyo (sale «No se encuentra» y el reenvío)."""
    indices = sq._indices([sq._resumir(_de("emu-warez.com", EMU_URL, "e" * 40))])
    assert sq.casar(f"{RAIZ}/{FICHERO}", indices, "uploadrr", RAIZ, tracker="nobs", result_url=NOBS_URL) == (None, "")
    t, _ = sq.casar(f"{RAIZ}/{FICHERO}", indices, "uploadrr", RAIZ, tracker="emu", result_url=EMU_URL)
    assert t["hash"] == "e" * 40


def test_dos_del_mismo_tracker_los_desempata_la_url_de_la_subida():
    otro = "https://nobs.rawsmoke.net/torrents/56"
    indices = sq._indices([sq._resumir(_de("nobs.rawsmoke.net", otro, "a" * 40)),
                           sq._resumir(_de("nobs.rawsmoke.net", NOBS_URL, "b" * 40))])
    t, _ = sq.casar(f"{RAIZ}/{FICHERO}", indices, "uploadrr", RAIZ, tracker="nobs", result_url=NOBS_URL)
    assert t["hash"] == "b" * 40
    # «/torrents/5» no es «/torrents/55»: la URL se compara entera.
    assert sq.casar(f"{RAIZ}/{FICHERO}", indices, "uploadrr", RAIZ, tracker="nobs",
                    result_url="https://nobs.rawsmoke.net/torrents/5") == (None, "")


def test_un_torrent_sin_datos_de_tracker_sigue_casando_como_antes():
    """Un qBittorrent viejo (sin comentario en /torrents/info) y sin anuncio que funcione: nada cambia para eMuwarez."""
    indices = sq._indices([sq._resumir(torrent(FICHERO))])
    t, como = sq.casar(f"{RAIZ}/{FICHERO}", indices, "", RAIZ, tracker="emu", result_url=EMU_URL)
    assert t is not None and como == "ruta"


def test_el_torrent_viejo_de_emuwarez_com_es_de_emuwarez():
    viejo = _de("emuwarez.com", "https://emuwarez.com/torrents/9", "e" * 40)
    nobs = _de("nobs.rawsmoke.net", NOBS_URL, "b" * 40)
    indices = sq._indices([sq._resumir(viejo), sq._resumir(nobs)])
    t, _ = sq.casar(f"{RAIZ}/{FICHERO}", indices, "uploadrr", RAIZ, tracker="emu", result_url=EMU_URL)
    assert t["hash"] == "e" * 40


def test_por_nombre_la_etiqueta_va_antes_que_el_tracker():
    """qBittorrent ve otra ruta: casa por nombre. El torrent etiquetado de la subida, sin anuncio ni comentario, gana a
    una descarga del mismo nombre sin etiqueta que sí lleva el anuncio de N.O.B.S."""
    otra = "/otra/ruta"
    propio = torrent(FICHERO, ruta=otra, hash="b" * 40, tags="uploadrr")
    descarga = _de("nobs.rawsmoke.net", "https://nobs.rawsmoke.net/torrents/999", "d" * 40)
    descarga.update(save_path=otra + "/", content_path=f"{otra}/{FICHERO}", tags="")
    indices = sq._indices([sq._resumir(propio), sq._resumir(descarga)])
    t, como = sq.casar(f"{RAIZ}/{FICHERO}", indices, "uploadrr", RAIZ, tracker="nobs", result_url=NOBS_URL)
    assert t["hash"] == "b" * 40 and como == "nombre"


def test_la_clave_de_paso_del_anuncio_no_se_guarda():
    resumen = sq._resumir(_de("nobs.rawsmoke.net", NOBS_URL, "b" * 40))
    assert "CLAVE-DE-PASO" not in json.dumps(resumen)


# --- otras: el aviso de la campana nombra el tracker ---------------------------------------------------------------

def test_el_aviso_de_subida_completada_nombra_su_tracker(db_conn):
    jid = _job(db_conn, "nobs")
    _notify_upload_completed(jid, NOBS_URL)
    with db() as conn:
        cuerpo = conn.execute("SELECT body FROM notifications WHERE entity_id = ?", (jid,)).fetchone()[0]
    assert cuerpo.startswith("Se ha subido a N.O.B.S correctamente.")


def test_la_descripcion_vieja_de_otra_subida_no_cuenta(tmp_path):
    """El respaldo sigue exigiendo un fichero escrito después de empezar el trabajo."""
    _descripcion(tmp_path, "NOBS", "[youtube]viejo[/youtube]", nombre="otra")
    viejo = time.time() - 3600
    os.utime(tmp_path / "tmp" / "otra" / "[NOBS]DESCRIPTION.txt", (viejo, viejo))
    desde = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(viejo + 60))
    assert find_emu_description_file(str(tmp_path), ["No.Esta"], desde=desde, tracker="NOBS") is None
