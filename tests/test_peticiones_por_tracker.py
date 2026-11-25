"""Las peticiones de cada tracker con clave (N.O.B.S, Milnueve), no solo las de eMuwarez.

Nada aquí habla con un tracker: `emu_fetch_all_pages` se sustituye por lo que devolvería cada uno."""
from __future__ import annotations

import pytest

from emuwarez_uploadrr.integrations import emu
from emuwarez_uploadrr.integrations.base import IntegrationError
from emuwarez_uploadrr.services import notification_center as nc
from emuwarez_uploadrr.services import requests as reqs
from emuwarez_uploadrr.services.settings import set_setting


def _fila(id_, nombre=None, tmdb=0, categoria=1):
    return {"id": id_, "name": nombre or f"Petición {id_}", "category_id": categoria, "tmdb": tmdb or id_ + 1000,
            "status": "unfilled", "bounty": "10.00", "created": "2025-01-01T00:00:00+00:00"}


@pytest.fixture
def db_conn(isolated_db):
    from emuwarez_uploadrr.database import connect, init_db
    conn = connect(isolated_db / "emuwarez_uploadrr.db")
    init_db(conn)
    set_setting(conn, "emu.api_key", "clave-emu")
    return conn


@pytest.fixture
def trackers(monkeypatch):
    """trackers[<EMU|NOBS|MNV>] = (filas, total) o una excepción; `llamadas` guarda con qué se pidió cada uno."""
    respuestas: dict = {}
    llamadas: list = []

    def fetch(api_key, base_url=None, progress=None, tracker="EMU", nombre="eMuwarez"):
        llamadas.append((tracker, api_key, base_url))
        r = respuestas[tracker]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(reqs.emu, "emu_fetch_all_pages", fetch)
    respuestas["llamadas"] = llamadas
    return respuestas


def _filas(conn):
    return sorted(tuple(f) for f in conn.execute("SELECT tracker, emu_id FROM request_items"))


def test_solo_con_emuwarez_no_se_pide_nada_mas(db_conn, trackers):
    trackers["EMU"] = ([_fila(1)], 1)
    assert reqs.sync_emu_requests(db_conn, force=True)["imported"] == 1
    assert [t for t, *_ in trackers["llamadas"]] == ["EMU"]
    assert _filas(db_conn) == [("emu", "1")]


def test_cada_tracker_con_clave_se_lee_con_su_clave_su_url_y_su_cupo(db_conn, trackers):
    set_setting(db_conn, "nobs.api_key", "clave-nobs")
    trackers["EMU"] = ([_fila(1)], 1)
    trackers["NOBS"] = ([_fila(1, "Ajena")], 1)
    assert reqs.sync_emu_requests(db_conn, force=True)["imported"] == 2
    assert trackers["llamadas"] == [("EMU", "clave-emu", "https://emu-warez.com"),
                                    ("NOBS", "clave-nobs", "https://nobs.rawsmoke.net")]
    # El mismo id en dos trackers son dos peticiones.
    assert _filas(db_conn) == [("emu", "1"), ("nobs", "1")]
    url = db_conn.execute("SELECT url FROM request_items WHERE tracker = 'nobs'").fetchone()[0]
    assert url == "https://nobs.rawsmoke.net/requests/1"


def test_las_peticiones_de_otro_tracker_llevan_los_nombres_de_su_tabla():
    item = emu.normalize_request({"id": 7, "name": "X", "category_id": 5, "type_id": 5, "resolution_id": 7}, "NOBS",
                                 "https://nobs.rawsmoke.net/")
    assert (item.category, item.requested_source, item.requested_resolution) == ("Anime TV Shows", "WEBRip", "576i")
    assert item.url == "https://nobs.rawsmoke.net/requests/7"


def test_de_otro_tracker_solo_se_piden_sus_categorias_de_video(monkeypatch):
    pedidas = []
    monkeypatch.setattr(emu, "_pedir_pagina", lambda clave, url, ruta, params, tracker: pedidas.append(
        (tracker, params["category_id[]"])) or {})
    emu.emu_get_requests("K", tracker="NOBS")
    emu.emu_get_requests("K", tracker="MNV")
    emu.emu_get_requests("K")
    assert pedidas == [("NOBS", [1, 2, 4, 5]), ("MNV", [1, 2, 3, 4, 8, 10, 14]), ("EMU", [1, 2, 4])]


def test_sin_la_tabla_de_otro_tracker_no_se_piden_sus_peticiones(monkeypatch):
    """Sin su tabla no se sabe qué categorías son de vídeo: sin filtro llegarían libros, juegos y música."""
    monkeypatch.setitem(emu._ETIQUETAS_OTROS, "MNV", None)
    monkeypatch.setattr(emu, "_pedir_pagina", lambda *a, **k: pytest.fail("pidió sin filtro"))
    with pytest.raises(IntegrationError, match="tabla de ids de MNV"):
        emu.emu_get_requests("K", tracker="MNV")


def test_las_de_un_tracker_sin_clave_se_borran(db_conn, trackers):
    db_conn.execute("INSERT INTO request_items (tracker, emu_id, title) VALUES ('nobs', '9', 'De cuando tenía clave')")
    db_conn.commit()
    trackers["EMU"] = ([_fila(1)], 1)
    reqs.sync_emu_requests(db_conn, force=True)
    assert _filas(db_conn) == [("emu", "1")]


def test_al_volver_a_poner_la_clave_sus_peticiones_no_se_avisan_como_nuevas(db_conn, trackers):
    set_setting(db_conn, "nobs.api_key", "clave-nobs")
    trackers["EMU"], trackers["NOBS"] = ([_fila(1)], 1), ([_fila(5)], 1)
    reqs.sync_emu_requests(db_conn, force=True)  # recorrido completo: desde aquí se sabe cuáles son nuevas
    marca = reqs.emu_catalog._clave(reqs.SYNC_COMPLETA_KEY, reqs.TRACKERS["nobs"])
    assert reqs.get_state(db_conn, marca) == "1"
    set_setting(db_conn, "nobs.api_key", "")
    reqs.sync_emu_requests(db_conn, force=True)
    # Sin la marca, el primer recorrido con la clave de vuelta no da ninguna por nueva (_leer_peticiones_de).
    assert reqs.get_state(db_conn, marca) != "1"


def test_los_errores_de_otro_tracker_dicen_su_nombre(monkeypatch):
    def limitado(*_a, **_kw):
        raise IntegrationError("http 429 en una URL con la clave", status=429)
    monkeypatch.setattr(emu, "emu_get_requests", limitado)
    with pytest.raises(IntegrationError) as e:
        emu.emu_fetch_all_pages("K", tracker="NOBS", nombre="N.O.B.S")
    assert str(e.value) == "N.O.B.S ha limitado las peticiones; prueba en unos minutos"


def test_el_fallo_de_otro_tracker_no_frena_a_los_demas_ni_borra_sus_peticiones(db_conn, trackers):
    set_setting(db_conn, "nobs.api_key", "clave-nobs")
    set_setting(db_conn, "mnv.api_key", "clave-mnv")
    db_conn.execute("INSERT INTO request_items (tracker, emu_id, title) VALUES ('nobs', '9', 'De antes')")
    db_conn.commit()
    trackers["EMU"] = ([_fila(1)], 1)
    trackers["NOBS"] = IntegrationError("N.O.B.S ha rechazado la API key (¿ratio por debajo de 1?)", status=401)
    trackers["MNV"] = ([_fila(3)], 1)
    assert reqs.sync_emu_requests(db_conn, force=True)["status"] == "ok"
    assert _filas(db_conn) == [("emu", "1"), ("mnv", "3"), ("nobs", "9")]
    fallo = "N.O.B.S ha rechazado la API key (¿ratio por debajo de 1?)"  # el nombre, una vez
    assert reqs.get_request_sync_progress()["error_otros"] == fallo
    resumen = db_conn.execute("SELECT summary FROM task_runs WHERE name = 'sync-emu-requests' "
                              "ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert fallo in resumen
    # Su fallo no deshace el límite de una por hora: la de eMuwarez sí se hizo.
    assert reqs.can_sync_requests(db_conn)[0] is False


def test_si_falla_emuwarez_los_demas_se_guardan_y_la_sincronizacion_falla_como_siempre(db_conn, trackers):
    set_setting(db_conn, "nobs.api_key", "clave-nobs")
    trackers["EMU"] = IntegrationError("eMuwarez ha limitado las peticiones; prueba en unos minutos", status=429)
    trackers["NOBS"] = ([_fila(2)], 1)
    with pytest.raises(IntegrationError):
        reqs.sync_emu_requests(db_conn, force=True)
    assert _filas(db_conn) == [("nobs", "2")]
    assert db_conn.execute("SELECT 1 FROM request_readiness").fetchone()  # se cruzaron y prepararon
    assert reqs.can_sync_requests(db_conn)[0] is True  # como siempre: un fallo de eMuwarez no bloquea una hora


def test_solo_un_recorrido_completo_de_un_tracker_borra_las_suyas(db_conn, trackers):
    set_setting(db_conn, "nobs.api_key", "clave-nobs")
    trackers["EMU"] = ([_fila(1)], 1)
    trackers["NOBS"] = ([_fila(1), _fila(2)], 2)
    reqs.sync_emu_requests(db_conn, force=True)
    trackers["NOBS"] = ([_fila(1)], 2)  # cortado: no cuadra con el total
    reqs.sync_emu_requests(db_conn, force=True)
    assert _filas(db_conn) == [("emu", "1"), ("nobs", "1"), ("nobs", "2")]
    trackers["NOBS"] = ([_fila(1)], 1)
    trackers["EMU"] = ([_fila(1)], 1)
    assert reqs.sync_emu_requests(db_conn, force=True)["removed"] == 1
    assert _filas(db_conn) == [("emu", "1"), ("nobs", "1")]


def _lista_para_subir(conn, tmdb, audio="spa", has_spanish_ok=1):
    conn.execute(
        "INSERT INTO library_items (item_type, title, year, tmdb_id, local_status, file_exists, nomenclature_status,"
        " prepared_path, resolution, source, audio_languages, has_spanish_ok, emu_in_catalog, emu_aportes)"
        " VALUES ('movie', 'Dune', 2021, ?, 'Disponible', 1, 'Correcta', '/data/prepared/d.mkv', '1080p', 'WEB-DL',"
        " ?, ?, 1, '[]')", (str(tmdb), audio, has_spanish_ok))
    conn.commit()


def _preparacion(conn, tracker):
    return tuple(conn.execute("SELECT readiness_status, blockers_json FROM request_readiness rr JOIN request_items ri "
                              "ON ri.id = rr.request_item_id WHERE ri.tracker = ?", (tracker,)).fetchone())


def test_lo_que_ya_hay_en_emuwarez_no_cierra_la_peticion_de_otro_tracker(db_conn, trackers):
    """`emu_in_catalog`/`emu_aportes` son de eMuwarez: la de N.O.B.S mira el catálogo local de N.O.B.S."""
    set_setting(db_conn, "nobs.api_key", "clave-nobs")
    _lista_para_subir(db_conn, 500)
    trackers["EMU"] = ([_fila(1, "Dune", tmdb=500)], 1)
    trackers["NOBS"] = ([_fila(1, "Dune", tmdb=500)], 1)
    reqs.sync_emu_requests(db_conn, force=True)
    assert _preparacion(db_conn, "emu")[0] == "already_exists"
    assert _preparacion(db_conn, "nobs")[0] == "ready_to_upload"


def test_lo_que_ya_hay_en_el_catalogo_de_otro_tracker_cierra_su_peticion(db_conn, trackers):
    from emuwarez_uploadrr.database import set_state
    set_setting(db_conn, "nobs.api_key", "clave-nobs")
    _lista_para_subir(db_conn, 500)
    db_conn.execute("INSERT INTO emu_catalog (tracker, torrent_id, category, tmdb_id, resolution, source, has_spanish,"
                    " name) VALUES ('nobs', '1', 'Movies', '500', '1080p', 'WEB-DL', 1, 'Dune 2021 1080p WEB-DL')")
    set_state(db_conn, "last_emu_catalog_completed_utc.nobs", "2026-11-24T00:00:00+00:00")
    db_conn.commit()
    trackers["EMU"] = ([], 0)
    trackers["NOBS"] = ([_fila(1, "Dune", tmdb=500)], 1)
    reqs.sync_emu_requests(db_conn, force=True)
    estado, bloqueos = _preparacion(db_conn, "nobs")
    assert estado == "already_exists" and "ya existe en N.O.B.S" in bloqueos


def test_la_peticion_de_otro_tracker_sigue_sus_normas_de_idioma(db_conn, trackers):
    """Sin castellano pero con audio latino: eMuwarez no la admite, N.O.B.S sí."""
    set_setting(db_conn, "nobs.api_key", "clave-nobs")
    _lista_para_subir(db_conn, 500, audio="lat", has_spanish_ok=0)
    db_conn.execute("UPDATE library_items SET emu_in_catalog = 0")
    db_conn.commit()
    trackers["EMU"] = ([_fila(1, "Dune", tmdb=500)], 1)
    trackers["NOBS"] = ([_fila(1, "Dune", tmdb=500)], 1)
    reqs.sync_emu_requests(db_conn, force=True)
    assert _preparacion(db_conn, "emu")[0] == "no_castellano"
    assert _preparacion(db_conn, "nobs")[0] == "ready_to_upload"


def test_el_aviso_de_peticiones_nuevas_dice_de_que_tracker_son(db_conn, trackers):
    set_setting(db_conn, "nobs.api_key", "clave-nobs")
    trackers["EMU"] = ([], 0)
    trackers["NOBS"] = ([], 0)
    reqs.sync_emu_requests(db_conn, force=True)  # la base: sin ella nada es nuevo
    _lista_para_subir(db_conn, 500)
    db_conn.execute("UPDATE library_items SET emu_in_catalog = 0")
    db_conn.commit()
    trackers["NOBS"] = ([_fila(1, "Dune", tmdb=500)], 1)
    reqs.sync_emu_requests(db_conn, force=True)
    avisos = nc.list_notifications(db_conn, limit=50)
    assert [n["body"] for n in avisos if n["event_type"] == "request_new_ready"] == [
        "Hay una petición nueva que ya puedes rellenar: «Dune» (N.O.B.S)."]
    compatibles = [n["body"] for n in avisos if n["event_type"] == "request_match_found"]
    assert compatibles == ["Tienes contenido compatible con esta petición de N.O.B.S."]


def test_una_serie_entera_de_otro_tracker_se_mira_temporada_a_temporada(db_conn, monkeypatch):
    """El catálogo y las normas de N.O.B.S se miran en cada temporada, no en la fila de la serie."""
    from emuwarez_uploadrr.services import emu_catalog, reglas_tracker
    from emuwarez_uploadrr.services.reglas_tracker import Reglas
    from emuwarez_uploadrr.services.request_readiness import build_request_candidates
    serie = db_conn.execute(
        "INSERT INTO library_items (item_type, title, year, tvdb_id, local_status, file_exists, has_spanish_ok)"
        " VALUES ('series', 'Serie', 2020, '77', 'Disponible', 1, 0)").lastrowid
    temporadas = [db_conn.execute(
        "INSERT INTO library_items (item_type, title, parent_id, season_number, local_status, has_spanish_ok)"
        " VALUES ('season', 'Serie', ?, ?, 'Disponible', 0)", (serie, n)).lastrowid for n in (1, 2)]
    pid = db_conn.execute("INSERT INTO request_items (tracker, emu_id, title, category, tvdb_id) "
                          "VALUES ('nobs', 1, 'Serie', 'TV', '77')").lastrowid
    db_conn.commit()
    comparadas, mirada = [], []
    monkeypatch.setattr(emu_catalog, "get_item_comparison", lambda _c, i, tracker: (
        comparadas.append(i) or {"conclusion": {"ya_lo_tiene": True}}))
    monkeypatch.setattr(reglas_tracker, "reglas_para_subir", lambda _c, fila, _t: (
        mirada.append(fila["id"]) or Reglas(permitido=True, motivo="Audio latino.", variante=None)))

    [candidata] = build_request_candidates(pid, db_conn)

    assert comparadas == temporadas and mirada == temporadas
    assert candidata["readiness_status"] == "already_exists", candidata["blockers"]


def test_el_pack_de_una_peticion_de_otro_tracker_sigue_sus_normas(db_conn, monkeypatch):
    """Una temporada solo en latino no se puede subir como pack a eMuwarez, pero sí a N.O.B.S."""
    from emuwarez_uploadrr.services import reglas_tracker
    from emuwarez_uploadrr.services.reglas_tracker import Reglas
    from emuwarez_uploadrr.services.season_pack import _PACK_ROLLUP_FIELDS
    serie = db_conn.execute("INSERT INTO library_items (item_type, title, local_status) VALUES ('series', 'Serie', 'Disponible')").lastrowid
    temporada = db_conn.execute(
        "INSERT INTO library_items (item_type, title, parent_id, season_number, has_spanish_ok, local_status) "
        "VALUES ('season', 'Serie', ?, 1, 0, 'Disponible')", (serie,)).lastrowid
    db_conn.execute(f"UPDATE library_items SET {', '.join(f'{c} = 1' for c in _PACK_ROLLUP_FIELDS)} WHERE id = ?",
                    (temporada,))
    db_conn.commit()
    monkeypatch.setattr(reglas_tracker, "reglas_para_subir",
                        lambda _c, _f, t: Reglas(permitido=t == "nobs", motivo="Audio latino.", variante=None))
    serie_fila = {"id": serie, "item_type": "series"}
    pide = {"req_scope": "season", "req_season": 1}
    assert "castellano" in reqs._pack_blocked_reason(db_conn, serie_fila, {**pide, "tracker": "emu"})
    assert reqs._pack_blocked_reason(db_conn, serie_fila, {**pide, "tracker": "nobs"}) is None
