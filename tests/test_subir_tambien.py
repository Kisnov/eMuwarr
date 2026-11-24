"""«Subir también a…» (services/subir_tambien.py): lo que ya está en un tracker, a otro con el mismo fichero (de eMuwarez
a N.O.B.S o Milnueve, o al revés).

La subida nueva reutiliza la preparación de la de origen (`copia_de_job_id`) y, si su `tmp/<nombre>` ya no tiene el
BASE.torrent, lanza Uploadrr-EMU con `--base-desde <TK>:<id>` para hacerlo con el torrent del otro tracker: mismas piezas.
Antes de encolar se mira que qBittorrent tiene entero el torrent de origen con ese fichero como contenido.
"""
from __future__ import annotations

import json
import time

import pytest

from emuwarez_uploadrr.services import seeding_qbit, subir_tambien as st
from emuwarez_uploadrr.services import upload_queue as uq
from emuwarez_uploadrr.services.uploadrr_contract import build_uploadrr_emu_command
from tests.test_subida_por_tracker import (  # noqa: F401 (fixtures)
    _biblioteca_en_tmp, _clave, _fila, _logs, _peli, _subida_a_emu_hecha, _trabajo, db_conn, pipeline,
)

# pytest busca las fixtures por nombre en este módulo; nombrarlas aquí las da por usadas (sin esto, cada test que las
# recibe es un F811 de ruff).
_FIXTURES_COMPARTIDAS = (db_conn, pipeline)


@pytest.fixture
def sin_hilos(monkeypatch):
    """Los trabajos se crean pero no arrancan: aquí solo importa qué se encola."""
    monkeypatch.setattr(uq, "_lanzar_trabajo", lambda job_id: None)


def _qbit(monkeypatch, conn, torrents):
    """qBittorrent configurado y con esta lista (ya resumida, como la deja seeding_qbit._resumir)."""
    from emuwarez_uploadrr.services.settings import set_setting
    set_setting(conn, "qbit.url", "http://qbit.local:8080")
    conn.commit()
    lista = [{"hash": "h", "name": "", "save_path": "", "tags": "", "ratio": 0.0, "uploaded": 0,
              "num_complete": None, "num_incomplete": None, **t} for t in torrents]
    monkeypatch.setattr(seeding_qbit, "_torrents", lambda *a, **k: (lista, "", time.time()))


def _entero(ruta):
    return {"content_path": str(ruta), "state": "stalledUP", "progress": 1.0}


def _emu_completada(conn, tmp_path, nombre="Peli.mkv", **extra):
    """Una subida a eMuwarez (u otro `tracker=`) completada (id 42 allí) con su fichero en la carpeta de subidas."""
    ruta = tmp_path / nombre
    ruta.write_bytes(b"x")
    iid = _peli(conn, str(tmp_path / f"orig-{nombre}"))
    datos = {"status": "completed", "stage": "completed", "torrent_subido": 1, "prepared_path": str(ruta),
             "result_torrent_id": "42", **extra}
    return iid, _trabajo(conn, iid, **datos), ruta


# ---------------------------------------------------------------------------
# El argv: --base-desde solo cuando no queda un BASE.torrent reutilizable
# ---------------------------------------------------------------------------

def test_el_argv_lleva_base_desde_y_resume():
    argv = build_uploadrr_emu_command(python_path="python3", uploadrr_script_path="/u/upload.py",
                                      prepared_path="/data/Peli.mkv", torrent_name="Peli", category="movie",
                                      tracker="NOBS", base_desde="EMU:42")
    assert argv[argv.index("--base-desde") + 1] == "EMU:42" and "--resume" in argv
    # «Reenviar a qBittorrent» no sube nada: ahí no pinta.
    solo = build_uploadrr_emu_command(python_path="python3", uploadrr_script_path="/u/upload.py",
                                      prepared_path="/data/Peli.mkv", torrent_name="Peli", category="movie",
                                      tracker="NOBS", base_desde="EMU:42", solo_cliente="7")
    assert "--base-desde" not in solo


def _comando(conn, job_id):
    from emuwarez_uploadrr.services.upload_execution import build_uploadrr_execution_command, get_upload_job
    job = get_upload_job(job_id, conn=conn)
    job["uploadrr_command_json"] = json.dumps({"runtime_type": "uploadrr_emu", "category": "movie", "title": "Peli"})
    return build_uploadrr_execution_command(job, conn=conn)["args"]


def test_base_desde_solo_cuando_el_base_torrent_ya_no_esta(db_conn, tmp_path):
    from emuwarez_uploadrr.services.settings import set_setting
    uploadrr = tmp_path / "Uploadrr"
    uploadrr.mkdir()
    set_setting(db_conn, "uploadrr.path", str(uploadrr))
    db_conn.commit()
    iid, _emu, ruta = _emu_completada(db_conn, tmp_path)
    nobs = _trabajo(db_conn, iid, tracker="nobs", prepared_path=str(ruta), reanuda_uploadrr=1)

    args = _comando(db_conn, nobs)
    assert args[args.index("--base-desde") + 1] == "EMU:42" and "--resume" in args

    base = uploadrr / "tmp" / "Peli.mkv" / "BASE.torrent"
    base.parent.mkdir(parents=True)
    base.write_bytes(b"d4:infod4:name1:xee")
    meta = base.parent / "meta.json"
    meta.write_text(json.dumps({"pasos_hechos": ["screens", "torrent"]}), encoding="utf-8")
    assert "--base-desde" not in _comando(db_conn, nobs)  # el de eMuwarez sigue ahí: --resume lo reutiliza

    base.unlink()
    db_conn.execute("UPDATE upload_jobs SET reanuda_uploadrr = 0 WHERE id = ?", (nobs,))
    db_conn.commit()
    assert "--base-desde" not in _comando(db_conn, nobs)  # sin reutilizar la preparación no hay de dónde


@pytest.mark.parametrize("meta", [
    {"pasos_hechos": ["screens"]},  # escrito pero sin marcar: un --base-desde cortado antes de marcar `torrent`
    None,                           # sin meta.json
    "no es json",
])
def test_base_desde_tambien_con_un_base_torrent_sin_la_marca(db_conn, tmp_path, meta):
    """Uploadrr-EMU no reutiliza un BASE.torrent sin el paso `torrent` en su meta.json: lo borra y, sin --base-desde,
    lo volvería a hashear (otras piezas que las del torrent de eMuwarez)."""
    from emuwarez_uploadrr.services.settings import set_setting
    uploadrr = tmp_path / "Uploadrr"
    base = uploadrr / "tmp" / "Peli.mkv" / "BASE.torrent"
    base.parent.mkdir(parents=True)
    base.write_bytes(b"d4:infod4:name1:xee")
    if meta is not None:
        (base.parent / "meta.json").write_text(meta if isinstance(meta, str) else json.dumps(meta), encoding="utf-8")
    set_setting(db_conn, "uploadrr.path", str(uploadrr))
    db_conn.commit()
    iid, _emu, ruta = _emu_completada(db_conn, tmp_path)
    nobs = _trabajo(db_conn, iid, tracker="nobs", prepared_path=str(ruta), reanuda_uploadrr=1)

    args = _comando(db_conn, nobs)
    assert args[args.index("--base-desde") + 1] == "EMU:42"


def test_sin_subida_completada_con_id_no_hay_base_desde(db_conn, tmp_path):
    iid, emu, ruta = _emu_completada(db_conn, tmp_path, result_torrent_id=None)
    nobs = _trabajo(db_conn, iid, tracker="nobs", prepared_path=str(ruta), reanuda_uploadrr=1)
    assert "--base-desde" not in _comando(db_conn, nobs)


def test_base_desde_con_el_torrent_de_nobs_para_subir_a_emuwarez(db_conn, tmp_path):
    from emuwarez_uploadrr.services.settings import set_setting
    set_setting(db_conn, "uploadrr.path", str(tmp_path / "Uploadrr"))
    db_conn.commit()
    iid, _nobs, ruta = _emu_completada(db_conn, tmp_path, tracker="nobs")
    emu = _trabajo(db_conn, iid, tracker="emu", prepared_path=str(ruta), reanuda_uploadrr=1)
    args = _comando(db_conn, emu)
    assert args[args.index("-tk") + 1] == "EMU" and args[args.index("--base-desde") + 1] == "NOBS:42"


def test_base_desde_con_el_torrent_de_la_subida_elegida(db_conn, tmp_path):
    """Con el mismo fichero en eMuwarez y N.O.B.S, las piezas salen de la que se eligió en «Subir también a…»."""
    from emuwarez_uploadrr.services.settings import set_setting
    set_setting(db_conn, "uploadrr.path", str(tmp_path / "Uploadrr"))
    db_conn.commit()
    iid, _emu, ruta = _emu_completada(db_conn, tmp_path)
    nobs = _trabajo(db_conn, iid, tracker="nobs", status="completed", prepared_path=str(ruta), result_torrent_id="7")
    mnv = _trabajo(db_conn, iid, tracker="mnv", prepared_path=str(ruta), reanuda_uploadrr=1, copia_de_job_id=nobs)
    args = _comando(db_conn, mnv)
    assert args[args.index("--base-desde") + 1] == "NOBS:7"


# ---------------------------------------------------------------------------
# qBittorrent: el torrent de origen entero, con ese fichero como contenido
# ---------------------------------------------------------------------------

def test_qbit_entero_en_la_misma_ruta(db_conn, monkeypatch, tmp_path):
    ruta = str(tmp_path / "Peli.mkv")
    _qbit(monkeypatch, db_conn, [_entero(ruta)])
    assert seeding_qbit.completo_en_qbit(db_conn, {ruta: "eMuwarez"}) == {ruta: None}


@pytest.mark.parametrize("torrent, texto", [
    (None, "no tiene ningún torrent"),
    ({"state": "downloading", "progress": 0.4}, "no está entero"),
    ({"state": "missingFiles", "progress": 1.0}, "no está entero"),
    ({"state": "checkingUP", "progress": 1.0}, "no está entero"),
], ids=["no-esta", "a-medias", "faltan-ficheros", "comprobando"])
def test_qbit_sin_el_torrent_entero(db_conn, monkeypatch, tmp_path, torrent, texto):
    ruta = str(tmp_path / "Peli.mkv")
    otro = {"content_path": str(tmp_path / "Otra.mkv"), "state": "stalledUP", "progress": 1.0}
    _qbit(monkeypatch, db_conn, [otro] + ([{"content_path": ruta, **torrent}] if torrent else []))
    assert texto in seeding_qbit.completo_en_qbit(db_conn, {ruta: "eMuwarez"})[ruta]


def test_sin_qbittorrent_configurado_no_se_puede_comprobar(db_conn, tmp_path):
    ruta = str(tmp_path / "Peli.mkv")
    motivo = seeding_qbit.completo_en_qbit(db_conn, {ruta: "N.O.B.S"})[ruta]
    assert "qBittorrent no está configurado" in motivo and "el torrent de N.O.B.S" in motivo


# ---------------------------------------------------------------------------
# Crear las subidas
# ---------------------------------------------------------------------------

def test_sin_la_clave_del_tracker_no_se_encola_nada(db_conn, tmp_path, sin_hilos):
    _iid, emu, _ruta = _emu_completada(db_conn, tmp_path)
    with pytest.raises(st.SubirTambienRechazado) as e:
        st.subir_tambien(db_conn, [emu], ["nobs"])
    assert "Falta la clave de API de N.O.B.S" in e.value.args[0][0]
    assert db_conn.execute("SELECT COUNT(*) FROM upload_jobs").fetchone()[0] == 1


def test_una_subida_a_nobs_con_el_fichero_de_la_de_emuwarez(db_conn, monkeypatch, tmp_path, sin_hilos):
    _clave(db_conn, "nobs")
    iid, emu, ruta = _emu_completada(db_conn, tmp_path, nomenclature_snapshot_json=json.dumps({"proposed_title": "P"}))
    _qbit(monkeypatch, db_conn, [_entero(ruta)])

    r = st.subir_tambien(db_conn, [emu], ["nobs"])

    assert r["encolados"] == 1 and r["lote_id"] is None  # una sola: sin lote
    (res,) = r["resultados"]
    fila = _fila(db_conn, res["nuevo_job_id"])
    assert (fila["tracker"], fila["copia_de_job_id"], fila["library_item_id"]) == ("nobs", emu, iid)
    assert json.loads(fila["nomenclature_snapshot_json"]) == {"proposed_title": "P"}  # el mismo nombre


def test_ya_subida_a_ese_tracker_no_se_vuelve_a_ofrecer(db_conn, monkeypatch, tmp_path, sin_hilos):
    _clave(db_conn, "nobs")
    _clave(db_conn, "mnv")
    iid, emu, ruta = _emu_completada(db_conn, tmp_path)
    _qbit(monkeypatch, db_conn, [_entero(ruta)])
    fallida = _trabajo(db_conn, iid, tracker="nobs", status="failed", stage="failed")

    from emuwarez_uploadrr.services.upload_execution import get_upload_job
    assert [t.id for t in st.ofrecidos(db_conn, get_upload_job(emu, conn=db_conn))[0]] == ["mnv"]
    r = st.subir_tambien(db_conn, [emu], ["nobs"])
    assert r["encolados"] == 0 and f"(#{fallida})" in r["resultados"][0]["motivo"]
    # Con una en marcha tampoco: la misma regla.
    db_conn.execute("UPDATE upload_jobs SET status = 'running' WHERE id = ?", (fallida,))
    db_conn.commit()
    assert st.subir_tambien(db_conn, [emu], ["nobs"])["encolados"] == 0


def _en_el_catalogo(conn, iid, tracker_id, resolution):
    """La película `iid` (1080p WEB-DL) y un torrent suyo en el catálogo local leído de `tracker_id`."""
    from emuwarez_uploadrr.database import set_state
    from emuwarez_uploadrr.services import emu_catalog
    from emuwarez_uploadrr.services.trackers import obtener
    conn.execute("UPDATE library_items SET tmdb_id = '7', resolution = '1080p', source = 'WEB-DL' WHERE id = ?", (iid,))
    conn.execute("INSERT INTO emu_catalog (tracker, category, tmdb_id, resolution, source, name) "
                 "VALUES (?, 'Películas', '7', ?, 'WEB-DL', ?)", (tracker_id, resolution, f"X {resolution} WEB-DL"))
    set_state(conn, emu_catalog._clave(emu_catalog.SYNC_COMPLETED_STATE_KEY, obtener(tracker_id)), "2026-11-24T00:00:00")
    conn.commit()


def test_lo_que_el_catalogo_de_otro_tracker_ya_tiene_no_se_ofrece(db_conn, monkeypatch, tmp_path, sin_hilos):
    _clave(db_conn, "nobs")
    _clave(db_conn, "mnv")
    iid, emu, ruta = _emu_completada(db_conn, tmp_path)
    _qbit(monkeypatch, db_conn, [_entero(ruta)])
    _en_el_catalogo(db_conn, iid, "nobs", "1080p")  # subida a mano: la misma calidad

    from emuwarez_uploadrr.services.upload_execution import get_upload_job
    libres, avisos = st.ofrecidos(db_conn, get_upload_job(emu, conn=db_conn))
    ya = "Ya hay uno igual o mejor en N.O.B.S (catálogo local)."
    assert [t.id for t in libres] == ["mnv"]
    assert avisos == [ya, "Sin catálogo local de Milnueve: no se ha mirado si ya lo tiene."]
    assert [([t.id for t in f["trackers"]], f["ya_lo_tienen"]) for f in st.candidatas(db_conn)] == [(["mnv"], [ya])]
    r = st.subir_tambien(db_conn, [emu], ["nobs", "mnv"])
    assert [(x["tracker"], x["status"], x["motivo"]) for x in r["resultados"]] == [
        ("nobs", "blocked", ya), ("mnv", "queued", None)]


def test_un_trump_si_se_ofrece(db_conn, tmp_path):
    _clave(db_conn, "nobs")
    iid, emu, _ruta = _emu_completada(db_conn, tmp_path)
    _en_el_catalogo(db_conn, iid, "nobs", "720p")

    from emuwarez_uploadrr.services.upload_execution import get_upload_job
    libres, avisos = st.ofrecidos(db_conn, get_upload_job(emu, conn=db_conn))
    assert ([t.id for t in libres], avisos) == (["nobs"], [])


def test_sin_el_torrent_entero_en_qbittorrent_no_se_encola(db_conn, monkeypatch, tmp_path, sin_hilos):
    _clave(db_conn, "nobs")
    _iid, emu, ruta = _emu_completada(db_conn, tmp_path)
    _qbit(monkeypatch, db_conn, [{"content_path": str(ruta), "state": "downloading", "progress": 0.5}])

    r = st.subir_tambien(db_conn, [emu], ["nobs"])

    assert r["encolados"] == 0 and "no está entero en qBittorrent" in r["resultados"][0]["motivo"]
    assert db_conn.execute("SELECT COUNT(*) FROM upload_jobs WHERE tracker = 'nobs'").fetchone()[0] == 0


def test_solo_lo_completado(db_conn, monkeypatch, tmp_path, sin_hilos):
    _clave(db_conn, "nobs")
    _iid, fallida, ruta = _emu_completada(db_conn, tmp_path, status="failed")
    _qbit(monkeypatch, db_conn, [_entero(ruta)])
    assert "no está completada" in st.subir_tambien(db_conn, [fallida], ["nobs"])["resultados"][0]["motivo"]


def test_de_nobs_a_emuwarez_y_milnueve(db_conn, monkeypatch, tmp_path, sin_hilos):
    _clave(db_conn, "emu")
    _clave(db_conn, "nobs")
    _clave(db_conn, "mnv")
    iid, nobs, ruta = _emu_completada(db_conn, tmp_path, tracker="nobs")
    _qbit(monkeypatch, db_conn, [_entero(ruta)])

    from emuwarez_uploadrr.services.upload_execution import get_upload_job
    assert [t.id for t in st.ofrecidos(db_conn, get_upload_job(nobs, conn=db_conn))[0]] == ["emu", "mnv"]
    # Con todos marcados, su propio tracker no cuenta: ni se encola ni sale como saltada.
    r = st.subir_tambien(db_conn, [nobs], ["emu", "nobs", "mnv"])

    assert (r["encolados"], r["saltados"]) == (2, 0) and r["lote_id"]
    filas = db_conn.execute("SELECT tracker, copia_de_job_id, library_item_id FROM upload_jobs "
                            "WHERE copia_de_job_id IS NOT NULL ORDER BY id").fetchall()
    assert [tuple(f) for f in filas] == [("emu", nobs, iid), ("mnv", nobs, iid)]


def test_solo_a_su_propio_tracker_no_se_encola_nada(db_conn, tmp_path, sin_hilos):
    _clave(db_conn, "emu")
    _iid, emu, _ruta = _emu_completada(db_conn, tmp_path)
    with pytest.raises(st.SubirTambienRechazado) as e:
        st.subir_tambien(db_conn, [emu], ["emu"])
    assert "Elige un tracker distinto" in e.value.args[0][0]


def test_sin_la_clave_de_emuwarez_no_se_ofrece_ni_se_encola(db_conn, tmp_path, sin_hilos):
    """eMuwarez se sube sin su clave desde el asistente (allí la pide); aquí no, porque fallaría después."""
    _clave(db_conn, "nobs")
    _clave(db_conn, "mnv")
    _iid, nobs, _ruta = _emu_completada(db_conn, tmp_path, tracker="nobs")
    from emuwarez_uploadrr.services.upload_execution import get_upload_job
    assert [t.id for t in st.ofrecidos(db_conn, get_upload_job(nobs, conn=db_conn))[0]] == ["mnv"]
    with pytest.raises(st.SubirTambienRechazado) as e:
        st.subir_tambien(db_conn, [nobs], ["emu"])
    assert "Falta la clave de API de eMuwarez" in e.value.args[0][0]


def test_sin_qbittorrent_el_motivo_nombra_el_tracker_de_origen(db_conn, tmp_path, sin_hilos):
    _clave(db_conn, "emu")
    _iid, nobs, _ruta = _emu_completada(db_conn, tmp_path, tracker="nobs")
    assert "el torrent de N.O.B.S" in st.subir_tambien(db_conn, [nobs], ["emu"])["resultados"][0]["motivo"]


def test_a_emuwarez_se_aplican_sus_normas_de_idioma(db_conn, monkeypatch, tmp_path, sin_hilos):
    """Solo latino: N.O.B.S lo admite y eMuwarez no; la copia no pasa por las comprobaciones del asistente."""
    _clave(db_conn, "emu")
    _clave(db_conn, "nobs")
    iid, nobs, ruta = _emu_completada(db_conn, tmp_path, tracker="nobs")
    db_conn.execute("UPDATE library_items SET audio_languages = 'es-419' WHERE id = ?", (iid,))
    db_conn.commit()
    _qbit(monkeypatch, db_conn, [_entero(ruta)])

    r = st.subir_tambien(db_conn, [nobs], ["emu"])

    assert r["encolados"] == 0 and "castellano" in r["resultados"][0]["motivo"]


def test_la_tanda_crea_una_subida_por_elemento_y_tracker(db_conn, monkeypatch, tmp_path, sin_hilos):
    _clave(db_conn, "nobs")
    _clave(db_conn, "mnv")
    a = _emu_completada(db_conn, tmp_path, "A.mkv")
    b = _emu_completada(db_conn, tmp_path, "B.mkv")
    c = _emu_completada(db_conn, tmp_path, "C.mkv")
    _qbit(monkeypatch, db_conn, [_entero(a[2]), _entero(b[2])])  # C no está en qBittorrent

    r = st.subir_tambien(db_conn, [a[1], b[1], c[1]], ["nobs", "mnv"])

    assert (r["encolados"], r["saltados"]) == (4, 2) and r["lote_id"]
    filas = db_conn.execute("SELECT library_item_id, tracker, copia_de_job_id, lote_id FROM upload_jobs "
                            "WHERE copia_de_job_id IS NOT NULL ORDER BY id").fetchall()
    assert [(f[0], f[1], f[2]) for f in filas] == [
        (a[0], "nobs", a[1]), (a[0], "mnv", a[1]), (b[0], "nobs", b[1]), (b[0], "mnv", b[1])]
    assert {f[3] for f in filas} == {r["lote_id"]}
    assert [x["status"] for x in r["resultados"] if x["job_id"] == c[1]] == ["blocked", "blocked"]


def test_las_candidatas_son_las_completadas_que_faltan_en_otro(db_conn, tmp_path):
    _clave(db_conn, "emu")
    assert st.candidatas(db_conn) == []  # sin la clave de otro tracker no hay nada que ofrecer
    _clave(db_conn, "nobs")
    a = _emu_completada(db_conn, tmp_path, "A.mkv")
    b = _emu_completada(db_conn, tmp_path, "B.mkv")
    _trabajo(db_conn, b[0], tracker="nobs", status="completed", stage="completed")
    _emu_completada(db_conn, tmp_path, "C.mkv", status="failed")
    d = _emu_completada(db_conn, tmp_path, "D.mkv", tracker="nobs")
    # Una subida más nueva de A que ya no vale (sin id) no tapa la de eMuwarez.
    _trabajo(db_conn, a[0], tracker="mnv", status="completed", stage="completed")
    assert sorted((f["job"]["id"], f["origen"].id, [t.id for t in f["trackers"]]) for f in st.candidatas(db_conn)) == [
        (a[1], "emu", ["nobs"]), (d[1], "nobs", ["emu"])]


# ---------------------------------------------------------------------------
# La preparación: el mismo fichero o nada
# ---------------------------------------------------------------------------

def test_una_subida_de_antes_de_las_huellas_se_reutiliza(db_conn, pipeline):
    """Las subidas a eMuwarez de antes de noviembre no apuntaban pistas ni huellas: «Subir también» vale igual."""
    iid, emu = _subida_a_emu_hecha(db_conn, pipeline, huellas_preparacion_json=None, remux_snapshot_json=None)
    nobs = _trabajo(db_conn, iid, tracker="nobs", copia_de_job_id=emu)

    uq._run_queued_job(nobs)

    fila = _fila(db_conn, nobs)
    assert fila["status"] != "failed", _logs(db_conn, nobs)
    assert pipeline.preparados == [] and fila["reanuda_uploadrr"] == 1
    assert fila["prepared_path"] == str(pipeline.destino)


def test_sin_huellas_una_subida_normal_a_nobs_sigue_fallando(db_conn, pipeline):
    iid, _emu = _subida_a_emu_hecha(db_conn, pipeline, huellas_preparacion_json=None)
    nobs = _trabajo(db_conn, iid, tracker="nobs")
    uq._run_queued_job(nobs)
    assert _fila(db_conn, nobs)["status"] == "failed"


def test_con_otro_nombre_no_se_sube(db_conn, pipeline, tmp_path):
    """Si el nombre de eMuwarez de ahora no es el de entonces, otro fichero no se sembraría junto: falla sin preparar."""
    iid, emu = _subida_a_emu_hecha(db_conn, pipeline)
    db_conn.execute("UPDATE upload_jobs SET prepared_path = ? WHERE id = ?", (str(tmp_path / "subidas" / "Viejo.mkv"), emu))
    db_conn.commit()
    nobs = _trabajo(db_conn, iid, tracker="nobs", copia_de_job_id=emu)

    uq._run_queued_job(nobs)

    fila = _fila(db_conn, nobs)
    assert fila["status"] == "failed" and "Viejo.mkv" in fila["error_message"]
    assert pipeline.preparados == []


def test_el_reintento_sigue_siendo_copia(db_conn, tmp_path, sin_hilos):
    _clave(db_conn, "nobs")
    iid, emu, _ruta = _emu_completada(db_conn, tmp_path)
    fallida = _trabajo(db_conn, iid, tracker="nobs", status="failed", stage="failed", copia_de_job_id=emu)
    r = uq.queue_retry_for_job(fallida, conn=db_conn)
    assert _fila(db_conn, r["job"]["id"])["copia_de_job_id"] == emu


# ---------------------------------------------------------------------------
# La API
# ---------------------------------------------------------------------------

def test_la_api_rechaza_sin_la_clave(client, auth_headers, db_conn, tmp_path):
    _iid, emu, _ruta = _emu_completada(db_conn, tmp_path)
    r = client.post("/api/uploads/subir-tambien", json={"job_ids": [emu], "trackers": ["nobs"]}, headers=auth_headers)
    assert r.status_code == 422 and "N.O.B.S" in r.json()["detail"]


def test_la_api_ofrece_los_trackers_en_el_detalle_y_la_lista(client, auth_headers, db_conn, tmp_path):
    _iid, emu, _ruta = _emu_completada(db_conn, tmp_path)
    assert client.get(f"/api/uploads/{emu}", headers=auth_headers).json()["subir_tambien_a"] == []
    _clave(db_conn, "mnv")
    assert client.get(f"/api/uploads/{emu}", headers=auth_headers).json()["subir_tambien_a"] == [
        {"id": "mnv", "nombre": "Milnueve"}]
    datos = client.get("/api/uploads/subir-tambien", headers=auth_headers).json()
    assert datos["trackers"] == [{"id": "mnv", "nombre": "Milnueve"}]  # eMuwarez, sin su clave, no
    assert [(s["job_id"], s["nombre"], s["origen"]["id"]) for s in datos["subidas"]] == [(emu, "Peli.mkv", "emu")]
    # Sin su catálogo leído se ofrece igual, con el aviso.
    sin_catalogo = ["Sin catálogo local de Milnueve: no se ha mirado si ya lo tiene."]
    assert datos["avisos"] == sin_catalogo
    assert client.get(f"/api/uploads/{emu}", headers=auth_headers).json()["subir_tambien_avisos"] == sin_catalogo
