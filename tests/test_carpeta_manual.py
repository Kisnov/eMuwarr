"""Carpeta manual: una instancia `carpeta` escanea vídeos del disco que no vienen de Radarr/Sonarr, guessit lee el nombre
y la ficha de TMDb la confirma el usuario antes de subir. TMDb y MediaInfo van de mentira."""
from __future__ import annotations

import json

import pytest

from emuwarez_uploadrr.database import connect, init_db
from emuwarez_uploadrr.integrations import tmdb
from emuwarez_uploadrr.integrations.base import IntegrationError
from emuwarez_uploadrr.services import library, media_info_service
from emuwarez_uploadrr.services.instances import add_instance, get_instance, list_instances
from emuwarez_uploadrr.services.library import carpeta
from emuwarez_uploadrr.services.settings import set_setting_canonical

PELI = "Oppenheimer.2023.1080p.AMZN.WEB-DL.DDP5.1.H.264-GRUPO.mkv"
PACK = "Shogun.2024.S01.1080p.DSNP.WEB-DL.DDP5.1.H.264-GRUPO"
CAPITULOS = [f"{PACK}/Shogun.2024.S01E0{n}.1080p.DSNP.WEB-DL.DDP5.1.H.264-GRUPO.mkv" for n in (1, 2)]
MEDIA = {"video_codec": "H.264", "audio_codec": "EAC3", "audio_languages": "ESP / ENG", "subtitle_languages": "ESP"}


@pytest.fixture
def carpeta_dir(tmp_path):
    raiz = tmp_path / "manual"
    for rel in (PELI, *CAPITULOS, f"{PACK}/Sample/sample.mkv", "notas.txt"):
        fichero = raiz / rel
        fichero.parent.mkdir(parents=True, exist_ok=True)
        fichero.write_bytes(b"x" * 10)
    return raiz


@pytest.fixture
def lecturas(monkeypatch):
    leidas = []
    monkeypatch.setattr(media_info_service, "read_media_info_summary", lambda ruta: leidas.append(ruta) or dict(MEDIA))
    return leidas


@pytest.fixture
def busquedas(monkeypatch):
    hechas = []

    def buscar(kind, query, *, year=None, conn=None, **_):
        hechas.append((kind, query, year))
        if kind == "movie":
            return [{"id": 872585, "title": "Oppenheimer", "original_title": "Oppenheimer",
                     "release_date": "2023-07-19", "poster_path": "/oppi.jpg", "overview": "Física."}]
        return [{"id": 126308, "name": "Shōgun", "original_name": "Shōgun", "first_air_date": "2024-02-27"},
                {"id": 1, "name": "Shogun", "original_name": "Shogun", "first_air_date": "1980-09-15"}]

    monkeypatch.setattr(tmdb, "search", buscar)
    return hechas


@pytest.fixture
def fichas(monkeypatch):
    def detalles(kind, tmdb_id, **_):
        if kind == "movie":
            return {"id": int(tmdb_id), "title": "Oppenheimer", "original_title": "Oppenheimer",
                    "release_date": "2023-07-19", "imdb_id": "tt15398776", "poster_path": "/oppi.jpg"}
        return {"id": int(tmdb_id), "name": "Shōgun", "original_name": "Shōgun", "first_air_date": "2024-02-27",
                "external_ids": {"imdb_id": "tt2788316", "tvdb_id": 392573},
                "seasons": [{"season_number": 1, "episode_count": 10, "air_date": "2024-02-27"}]}

    monkeypatch.setattr(tmdb, "get_details", detalles)


@pytest.fixture
def conn(isolated_db, carpeta_dir):
    c = connect(isolated_db / "emuwarez_uploadrr.db")
    init_db(c)
    set_setting_canonical(c, "tmdb.api_key", "clave-tmdb")
    c.commit()
    yield c
    c.close()


@pytest.fixture
def instancia(conn, carpeta_dir):
    return get_instance(conn, add_instance(conn, "carpeta", "Manual", str(carpeta_dir), ""))


def _escanear(conn, instancia):
    return library.sync_instance(conn, get_instance(conn, instancia.id))


def _filas(conn, instancia):
    return {r["external_id"]: dict(r) for r in conn.execute(
        "SELECT * FROM library_items WHERE instance_id = ?", (instancia.id,))}


def test_guessit_da_la_fuente_la_resolucion_y_el_grupo():
    peli = carpeta.analizar_nombre(PELI)
    assert peli == {"tipo": "movie", "titulo": "Oppenheimer", "anio": 2023, "temporada": None, "episodios": [],
                         "resolucion": "1080p", "fuente": "WEB-DL", "grupo": "GRUPO"}
    rip = carpeta.analizar_nombre("Arcane.S02E03.2160p.NF.WEBRip.x265-GRUPO.mkv")
    assert (rip["tipo"], rip["temporada"], rip["episodios"], rip["fuente"]) == ("episode", 2, [3], "WEBRip")
    doble = carpeta.analizar_nombre("Breaking.Bad.S01E01-E02.720p.BluRay.x264-GRUPO.mkv")
    assert (doble["episodios"], doble["fuente"], doble["resolucion"]) == ([1, 2], "BluRay", "720p")
    # Sin temporada en el nombre: la de la carpeta.
    assert carpeta.analizar_nombre("Frieren/Season 1/Frieren - S01E05 - 1080p WEB-DL.mkv")["temporada"] == 1


def test_el_escaneo_crea_la_pelicula_y_la_serie_por_identificar(conn, instancia, lecturas, busquedas):
    importados, faltantes = _escanear(conn, instancia)
    filas = _filas(conn, instancia)
    assert (importados, faltantes) == (3, 0)   # la muestra y el .txt no cuentan
    peli = filas[f"peli:{PELI}"]
    assert peli["item_type"] == "movie" and peli["tmdb_id"] is None and peli["title"] == "Oppenheimer"
    assert (peli["source"], peli["resolution"], peli["release_group"], peli["local_status"]) == (
        "WEB-DL", "1080p", "GRUPO", "Disponible")
    assert (peli["video_codec"], peli["audio_languages"]) == ("H.264", "ESP / ENG")
    assert json.loads(peli["raw_json"])["sugerencia"]["tmdb_id"] == "872585"
    serie = filas["serie:shogun:2024:1080p"]
    temporada = filas["season:shogun:2024:1080p:1"]
    capitulos = [filas[f"episode:{c}"] for c in CAPITULOS]
    assert temporada["parent_id"] == serie["id"] and {c["parent_id"] for c in capitulos} == {temporada["id"]}
    assert [c["title"] for c in capitulos] == ["Shogun S01E01", "Shogun S01E02"]
    assert temporada["video_codec"] == "H.264" and temporada["size_bytes"] == 20   # el resumen de la temporada
    # Dos «Shogun» en TMDb, y el del año del nombre es uno: esa es la sugerencia.
    assert json.loads(serie["raw_json"])["sugerencia"]["tmdb_id"] == "126308"
    assert carpeta.contar_por_identificar(conn, instancia.id) == 2


def test_volver_a_escanear_no_relee_ni_vuelve_a_buscar(conn, instancia, lecturas, busquedas):
    _escanear(conn, instancia)
    leidas, buscadas = len(lecturas), len(busquedas)
    _escanear(conn, instancia)
    assert (len(lecturas), len(busquedas)) == (leidas, buscadas)


def test_identificar_la_pelicula_sobrevive_al_escaneo(conn, instancia, lecturas, busquedas, fichas):
    _escanear(conn, instancia)
    peli = _filas(conn, instancia)[f"peli:{PELI}"]
    assert carpeta.identificar(conn, peli["id"], "872585", "movie") == peli["id"]
    _escanear(conn, instancia)
    peli = _filas(conn, instancia)[f"peli:{PELI}"]
    assert (peli["tmdb_id"], peli["imdb_id"], peli["year"]) == ("872585", "tt15398776", 2023)
    assert peli["poster_url"] == "https://image.tmdb.org/t/p/original/oppi.jpg"
    assert "sugerencia" not in json.loads(peli["raw_json"])
    with pytest.raises(ValueError):
        carpeta.identificar(conn, peli["id"], "126308", "tv")


def test_identificar_la_serie_llega_a_sus_capitulos(conn, instancia, lecturas, busquedas, fichas):
    _escanear(conn, instancia)
    capitulo = _filas(conn, instancia)[f"episode:{CAPITULOS[0]}"]
    serie_id = carpeta.identificar(conn, capitulo["id"], "126308")   # desde un capítulo, la serie
    _escanear(conn, instancia)
    filas = _filas(conn, instancia)
    assert filas["serie:shogun:2024:1080p"]["id"] == serie_id
    capitulo = filas[f"episode:{CAPITULOS[0]}"]
    assert (capitulo["tmdb_id"], capitulo["tvdb_id"], capitulo["title"]) == ("126308", "392573", "Shōgun S01E01")
    assert json.loads(capitulo["raw_json"])["episode"]["airDate"] == "2024-02-27"
    assert filas["season:shogun:2024:1080p:1"]["title"] == "Shōgun - Temporada 1"


def test_el_pack_de_carpeta_pide_la_ficha_y_la_temporada_entera(conn, instancia, lecturas, busquedas, fichas):
    from emuwarez_uploadrr.services.season_pack import _get_season_episodes
    _escanear(conn, instancia)
    temporada = _filas(conn, instancia)["season:shogun:2024:1080p:1"]
    serie = _filas(conn, instancia)["serie:shogun:2024:1080p"]
    capitulos = _get_season_episodes(temporada["id"], conn)
    assert "Identifica la serie" in carpeta.errores_de_temporada(temporada, serie, capitulos)[0]
    carpeta.identificar(conn, serie["id"], "126308")
    serie = _filas(conn, instancia)["serie:shogun:2024:1080p"]
    errores = carpeta.errores_de_temporada(temporada, serie, capitulos)
    assert errores == ["Temporada incompleta: hay 2 de 10 capítulos según TMDb (faltan 3, 4, 5, 6, 7, 8, 9, 10)."]
    # Una temporada de Sonarr no pasa por aquí.
    assert carpeta.errores_de_temporada({**temporada, "instance_type": "sonarr"}, serie, capitulos) == []


def test_sin_identificar_no_se_sube(conn, instancia, lecturas, busquedas):
    from emuwarez_uploadrr.services.upload_preflight import validate_upload_preconditions
    _escanear(conn, instancia)
    peli = _filas(conn, instancia)[f"peli:{PELI}"]
    errores = validate_upload_preconditions(peli, conn=conn)["errors"]
    assert any("Identifica la ficha en TMDb" in e for e in errores)


def test_lo_que_sale_de_la_carpeta_se_quita_pero_una_carpeta_vacia_no_borra_nada(
        conn, instancia, carpeta_dir, lecturas, busquedas):
    _escanear(conn, instancia)
    (carpeta_dir / CAPITULOS[1]).unlink()
    _escanear(conn, instancia)
    assert f"episode:{CAPITULOS[1]}" not in _filas(conn, instancia)
    for rel in (PELI, CAPITULOS[0]):
        (carpeta_dir / rel).unlink()
    with pytest.raises(IntegrationError, match="no tiene vídeos"):
        _escanear(conn, instancia)
    assert f"peli:{PELI}" in _filas(conn, instancia)


def test_sin_clave_de_tmdb_escanea_sin_buscar(conn, instancia, lecturas, busquedas):
    set_setting_canonical(conn, "tmdb.api_key", "")
    conn.commit()
    _escanear(conn, instancia)
    assert busquedas == []
    assert carpeta.contar_por_identificar(conn, instancia.id) == 2


def test_las_sugeridas_se_confirman_de_una_vez(conn, instancia, lecturas, busquedas, fichas):
    _escanear(conn, instancia)
    assert carpeta.identificar_sugeridas(conn, instancia.id) == 2
    assert carpeta.contar_por_identificar(conn, instancia.id) == 0


def test_la_misma_serie_en_otra_resolucion_se_identifica_a_la_vez(
        conn, instancia, carpeta_dir, lecturas, busquedas, fichas):
    otro = carpeta_dir / "Shogun.2024.S01E01.2160p.DSNP.WEB-DL.DDP5.1.H.265-GRUPO.mkv"
    otro.write_bytes(b"x")
    _escanear(conn, instancia)
    filas = _filas(conn, instancia)
    assert {"serie:shogun:2024:1080p", "serie:shogun:2024:2160p"} <= filas.keys()   # un pack nunca mezcla resoluciones
    carpeta.identificar(conn, filas["serie:shogun:2024:1080p"]["id"], "126308")
    assert _filas(conn, instancia)["serie:shogun:2024:2160p"]["tmdb_id"] == "126308"


def test_dos_series_con_el_mismo_titulo_y_otro_anio_no_se_juntan(
        conn, instancia, carpeta_dir, lecturas, busquedas, fichas):
    (carpeta_dir / "Shogun.1980.S01E01.1080p.WEB-DL.H.264-GRUPO.mkv").write_bytes(b"x")
    _escanear(conn, instancia)
    filas = _filas(conn, instancia)
    assert {"serie:shogun:2024:1080p", "serie:shogun:1980:1080p"} <= filas.keys()
    carpeta.identificar(conn, filas["serie:shogun:2024:1080p"]["id"], "126308")
    assert _filas(conn, instancia)["serie:shogun:1980:1080p"]["tmdb_id"] is None


def test_una_subcarpeta_que_no_se_puede_leer_para_el_escaneo_sin_borrar_nada(
        conn, instancia, monkeypatch, lecturas, busquedas):
    _escanear(conn, instancia)
    antes = set(_filas(conn, instancia))
    paseo = carpeta.os.walk

    def a_medias(raiz, onerror=None):
        yield from paseo(raiz)
        onerror(PermissionError(13, "Permission denied", str(raiz) + "/privada"))
    monkeypatch.setattr(carpeta.os, "walk", a_medias)
    with pytest.raises(IntegrationError, match="No se puede leer .*/privada"):
        _escanear(conn, instancia)
    assert set(_filas(conn, instancia)) == antes


def test_no_se_confirma_con_una_sincronizacion_en_marcha(conn, instancia, monkeypatch, lecturas, busquedas, fichas):
    _escanear(conn, instancia)
    peli = _filas(conn, instancia)[f"peli:{PELI}"]
    monkeypatch.setattr(library.sync, "_LIB_SYNC_PROGRESS", {**library.sync._LIB_SYNC_PROGRESS, "active": True})
    with pytest.raises(library.SyncAlreadyRunning):
        carpeta.identificar(conn, peli["id"], "872585")
    assert _filas(conn, instancia)[f"peli:{PELI}"]["tmdb_id"] is None


def test_un_fichero_con_varios_capitulos_no_se_sube_todavia(
        conn, instancia, carpeta_dir, lecturas, busquedas, fichas):
    from emuwarez_uploadrr.services.season_pack import _get_season_episodes
    from emuwarez_uploadrr.services.upload_preflight import validate_upload_preconditions
    doble = f"{PACK}/Shogun.2024.S01E03-E04.1080p.DSNP.WEB-DL.DDP5.1.H.264-GRUPO.mkv"
    (carpeta_dir / doble).write_bytes(b"x")
    _escanear(conn, instancia)
    serie = _filas(conn, instancia)["serie:shogun:2024:1080p"]
    carpeta.identificar(conn, serie["id"], "126308")
    filas = _filas(conn, instancia)
    errores = validate_upload_preconditions(filas[f"episode:{doble}"], conn=conn)["errors"]
    assert any("varios capítulos" in e for e in errores)
    temporada = filas["season:shogun:2024:1080p:1"]
    errores = carpeta.errores_de_temporada(temporada, filas["serie:shogun:2024:1080p"],
                                           _get_season_episodes(temporada["id"], conn))
    assert len(errores) == 1 and "S01E03-E04" in errores[0]


def test_las_sugeridas_recalculan_la_biblioteca_una_vez(conn, instancia, monkeypatch, lecturas, busquedas, fichas):
    _escanear(conn, instancia)
    veces = []
    monkeypatch.setattr(carpeta, "recompute_after_sync", lambda c: veces.append(1))
    assert carpeta.identificar_sugeridas(conn) == 2
    assert veces == [1]


def test_las_sugeridas_no_repiten_la_copia_en_otra_resolucion(
        conn, instancia, carpeta_dir, monkeypatch, lecturas, busquedas, fichas):
    (carpeta_dir / PELI.replace("1080p", "2160p")).write_bytes(b"x")
    _escanear(conn, instancia)
    pedidas, detalles = [], tmdb.get_details
    monkeypatch.setattr(tmdb, "get_details", lambda kind, tmdb_id, **kw: pedidas.append(tmdb_id) or detalles(kind, tmdb_id, **kw))
    assert carpeta.identificar_sugeridas(conn, instancia.id) == 2
    assert sorted(pedidas) == ["126308", "872585"]
    assert carpeta.contar_por_identificar(conn, instancia.id) == 0


def test_las_sugeridas_paran_si_empieza_una_sincronizacion(conn, instancia, monkeypatch, lecturas, busquedas, fichas):
    _escanear(conn, instancia)
    real, hechas, recalculos = carpeta.identificar, [], []

    def a_la_segunda(*args, **kw):
        if hechas:
            raise library.SyncAlreadyRunning()
        hechas.append(1)
        return real(*args, **kw)
    monkeypatch.setattr(carpeta, "identificar", a_la_segunda)
    monkeypatch.setattr(carpeta, "recompute_after_sync", lambda c: recalculos.append(1))
    assert carpeta.identificar_sugeridas(conn) == 1
    assert recalculos == []   # ya recalcula la sincronización en marcha al acabar
    with pytest.raises(library.SyncAlreadyRunning):   # sin confirmar ninguna, el 429 de siempre
        carpeta.identificar_sugeridas(conn)


def test_candidatos_sin_texto_usan_lo_que_leyo_guessit(conn, instancia, lecturas, busquedas):
    _escanear(conn, instancia)
    serie = _filas(conn, instancia)["serie:shogun:2024:1080p"]
    respuesta = carpeta.candidatos(conn, serie["id"])
    assert (respuesta["tipo"], respuesta["consulta"], respuesta["anio"]) == ("tv", "Shogun", 2024)
    assert respuesta["candidatos"][0] == {"tmdb_id": "126308", "titulo": "Shōgun", "titulo_original": "Shōgun",
                                          "anio": 2024, "poster_url": None, "resumen": ""}
    assert carpeta.candidatos(conn, serie["id"], consulta="Shogun 1980")["anio"] is None
    with pytest.raises(LookupError):
        carpeta.candidatos(conn, 999999)


def test_las_comprobaciones_de_radarr_y_sonarr_no_ven_la_carpeta(conn, instancia):
    conn.execute("INSERT INTO instances (type, name, url, api_key) VALUES ('radarr', 'Radarr', 'http://r', 'k')")
    conn.commit()
    assert {i.type for i in list_instances(conn, enabled_only=True)} == {"radarr", "carpeta"}
    assert {i.type for i in list_instances(conn, enabled_only=True, solo_arr=True)} == {"radarr"}


def test_probar_carpeta(conn, carpeta_dir, tmp_path):
    assert carpeta.probar_carpeta(str(tmp_path / "no-existe"))["ok"] is False
    assert carpeta.probar_carpeta("relativa/x") == {
        "ok": False, "error": "La ruta de la carpeta debe ser absoluta (empieza por /)"}
    resultado = carpeta.probar_carpeta(str(carpeta_dir), conn)
    assert resultado["ok"] is True
    assert resultado["message"] == "3 vídeos en la carpeta"
    assert "está fuera de las rutas permitidas" in resultado["warning"]   # tmp_path no cuelga de /data


def test_probar_carpeta_con_una_subcarpeta_que_no_se_puede_leer(conn, carpeta_dir, monkeypatch):
    def a_medias(raiz, onerror=None):
        onerror(PermissionError(13, "Permission denied", str(raiz) + "/privada"))
        yield from ()
    monkeypatch.setattr(carpeta.os, "walk", a_medias)
    resultado = carpeta.probar_carpeta(str(carpeta_dir), conn)
    assert resultado["ok"] is False and "/privada" in resultado["error"]


def test_el_escaneo_no_ve_enlaces_rotos_ficheros_de_mac_ni_la_carpeta_de_subidas(
        conn, instancia, carpeta_dir, monkeypatch, lecturas, busquedas):
    from emuwarez_uploadrr.services import carpeta_subida
    (carpeta_dir / f"._{PELI}").write_bytes(b"x")
    (carpeta_dir / "Roto.2020.1080p.WEB-DL.H.264-GRUPO.mkv").symlink_to(carpeta_dir / "no-existe.mkv")
    subidas = carpeta_dir / "subidas"
    subidas.mkdir()
    (subidas / "Oppenheimer.2023.1080p.WEB-DL.H.264-GRUPO.mkv").write_bytes(b"x")
    monkeypatch.setattr(carpeta_subida, "RAIZ_SUBIDAS", str(subidas))
    _escanear(conn, instancia)
    assert set(_filas(conn, instancia)) == {f"peli:{PELI}", "serie:shogun:2024:1080p", "season:shogun:2024:1080p:1",
                                            *(f"episode:{c}" for c in CAPITULOS)}
    assert carpeta.probar_carpeta(str(carpeta_dir))["message"] == "3 vídeos en la carpeta"


def test_la_carpeta_de_subidas_no_vale_como_carpeta_manual_pero_una_de_dentro_si(conn, tmp_path, monkeypatch):
    """Sus enlaces saldrían como elementos y renombrar uno se lo quitaba al torrent que lo siembra."""
    from emuwarez_uploadrr.services import carpeta_subida
    subidas = tmp_path / "uploads"
    (subidas / "Up").mkdir(parents=True)
    monkeypatch.setattr(carpeta_subida, "RAIZ_SUBIDAS", str(subidas))
    for ruta in (str(subidas), str(subidas) + "/", str(subidas / "Up" / "..")):
        resultado = carpeta.probar_carpeta(ruta, conn)
        assert resultado["ok"] is False and "carpeta de subidas" in resultado["error"]
        with pytest.raises(ValueError):
            carpeta.ruta_de_carpeta(ruta)
    assert carpeta.probar_carpeta(str(subidas / "Up"), conn)["ok"] is True


def test_capitulos_sin_resolucion_en_el_nombre_se_separan_por_la_del_fichero(
        conn, instancia, carpeta_dir, monkeypatch, lecturas, busquedas):
    medidas = []
    monkeypatch.setattr(media_info_service, "read_resolution",
                        lambda ruta: medidas.append(ruta) or ("2160p" if "E02" in ruta else "1080p"))
    for n in (1, 2):
        (carpeta_dir / f"Pluribus.2025.S01E0{n}.WEB-DL.DDP5.1.H.264-GRUPO.mkv").write_bytes(b"x")
    _escanear(conn, instancia)
    filas = _filas(conn, instancia)
    assert {"serie:pluribus:2025:1080p", "serie:pluribus:2025:2160p"} <= filas.keys()
    assert filas["episode:Pluribus.2025.S01E02.WEB-DL.DDP5.1.H.264-GRUPO.mkv"]["resolution"] == "2160p"
    _escanear(conn, instancia)
    assert len(medidas) == 2   # el segundo escaneo usa la guardada


def test_otra_carpeta_con_el_mismo_nombre_no_hereda_la_ficha(
        conn, instancia, carpeta_dir, tmp_path, lecturas, busquedas, fichas):
    import shutil
    from emuwarez_uploadrr.services.instances import update_instance
    _escanear(conn, instancia)
    carpeta.identificar(conn, _filas(conn, instancia)[f"peli:{PELI}"]["id"], "872585")
    movida = tmp_path / "movida"
    shutil.copytree(carpeta_dir, movida)
    update_instance(conn, instancia.id, url=str(movida))
    _escanear(conn, instancia)
    assert _filas(conn, instancia)[f"peli:{PELI}"]["tmdb_id"] == "872585"   # el mismo disco montado en otro sitio
    otra = tmp_path / "otra"
    otra.mkdir()
    (otra / PELI).write_bytes(b"y" * 20)
    update_instance(conn, instancia.id, url=str(otra))
    _escanear(conn, instancia)
    assert _filas(conn, instancia)[f"peli:{PELI}"]["tmdb_id"] is None


def test_titulos_en_otro_alfabeto_no_se_identifican_a_la_vez(conn, instancia, carpeta_dir, lecturas, busquedas, fichas):
    for titulo in ("山", "海"):
        (carpeta_dir / f"{titulo}.2020.1080p.WEB-DL.H.264-GRUPO.mkv").write_bytes(b"x")
    _escanear(conn, instancia)
    carpeta.identificar(conn, _filas(conn, instancia)["peli:山.2020.1080p.WEB-DL.H.264-GRUPO.mkv"]["id"], "872585")
    assert _filas(conn, instancia)["peli:海.2020.1080p.WEB-DL.H.264-GRUPO.mkv"]["tmdb_id"] is None


def test_el_pack_pide_cada_capitulo_no_cuantos_hay():
    temporada = {"instance_type": "carpeta", "season_number": 1}
    serie = {"tmdb_id": "1", "raw_json": json.dumps({"temporadas": {"1": {"capitulos": 2}}})}
    capitulos = [{"raw_json": json.dumps({"episodios": [e]}), "episode_number": e} for e in (1, 3)]
    assert carpeta.errores_de_temporada(temporada, serie, capitulos) == [
        "Temporada incompleta: hay 1 de 2 capítulos según TMDb (faltan 2)."]


def test_reconstruir_instancias_no_reutiliza_el_id_de_una_borrada(isolated_db):
    from emuwarez_uploadrr import database
    c = connect(isolated_db / "emuwarez_uploadrr.db")
    init_db(c)
    for nombre in ("a", "b"):
        c.execute("INSERT INTO instances (type, name, url, api_key) VALUES ('radarr', ?, 'http://r', 'k')", (nombre,))
    c.execute("DELETE FROM instances WHERE name = 'b'")
    database._rebuild_instances_table(c, "'radarr','sonarr','carpeta'", unique_clause="UNIQUE(name)")
    nueva = c.execute("INSERT INTO instances (type, name, url, api_key) VALUES ('radarr', 'c', 'http://r', 'k')")
    assert nueva.lastrowid == 3
    c.close()


def test_sin_anio_el_mismo_titulo_no_se_identifica_a_la_vez(conn, instancia, carpeta_dir, lecturas, busquedas, fichas):
    for res in ("1080p", "2160p"):
        (carpeta_dir / f"Dune.{res}.WEB-DL.DDP5.1.H.264-GRUPO.mkv").write_bytes(b"x")
    _escanear(conn, instancia)
    carpeta.identificar(conn, _filas(conn, instancia)["peli:Dune.1080p.WEB-DL.DDP5.1.H.264-GRUPO.mkv"]["id"], "438631")
    assert _filas(conn, instancia)["peli:Dune.2160p.WEB-DL.DDP5.1.H.264-GRUPO.mkv"]["tmdb_id"] is None


def test_la_migracion_admite_carpeta(isolated_db):
    c = connect(isolated_db / "emuwarez_uploadrr.db")
    init_db(c)
    init_db(c)
    sql = c.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'instances'").fetchone()["sql"]
    assert "'carpeta'" in sql and "UNIQUE(name)" in sql
    c.close()


# --- API ---

@pytest.fixture
def clave_tmdb(isolated_db):
    c = connect(isolated_db / "emuwarez_uploadrr.db")
    init_db(c)
    set_setting_canonical(c, "tmdb.api_key", "clave-tmdb")
    c.commit()
    c.close()


def _crear(client, auth_headers, carpeta_dir, **extra):
    return client.post("/api/instances", headers=auth_headers,
                       json={"type": "carpeta", "name": "Manual", "url": str(carpeta_dir), "api_key": "", **extra})


def test_api_crea_y_escanea_la_carpeta(clave_tmdb, client, auth_headers, carpeta_dir, lecturas, busquedas,
                                       fichas):
    assert _crear(client, auth_headers, carpeta_dir).status_code == 201
    instancia = next(i for i in client.get("/api/instances", headers=auth_headers).json() if i["type"] == "carpeta")
    assert instancia["url"] == str(carpeta_dir) and instancia["api_key_masked"] == ""
    escaneo = client.post(f"/api/instances/{instancia['id']}/sync", headers=auth_headers)
    assert escaneo.status_code == 200, escaneo.text
    assert escaneo.json()["message"] == "Carpeta 'Manual' escaneada: 3 vídeos, 2 por identificar en TMDb"

    lista = client.get("/api/library?por_identificar=true&limit=50", headers=auth_headers).json()
    assert {it["item_type"] for it in lista["items"]} == {"movie", "series"}
    peli = next(it for it in lista["items"] if it["item_type"] == "movie")
    assert peli["por_identificar"] is True and peli["sugerencia_tmdb"]["tmdb_id"] == "872585"

    candidatos = client.get(f"/api/library/{peli['id']}/tmdb-candidatos?q=Oppenheimer&anio=2023",
                            headers=auth_headers)
    assert candidatos.status_code == 200 and candidatos.json()["candidatos"][0]["tmdb_id"] == "872585"
    hecho = client.post(f"/api/library/{peli['id']}/identificar", headers=auth_headers,
                        json={"tmdb_id": 872585, "tipo": "movie"})
    assert hecho.status_code == 200 and hecho.json()["item_id"] == peli["id"]
    assert client.get(f"/api/library/{peli['id']}", headers=auth_headers).json()["por_identificar"] is False

    sugeridas = client.post("/api/library/identificar-sugeridas", headers=auth_headers, json={})
    assert sugeridas.json()["confirmadas"] == 1
    assert client.get("/api/library?por_identificar=true", headers=auth_headers).json()["items"] == []


def test_api_valida_la_ruta_y_la_url_segun_el_tipo(client, auth_headers, carpeta_dir):
    assert _crear(client, auth_headers, carpeta_dir, url="http://nas/manual").status_code == 422
    radarr_con_ruta = client.post("/api/instances", headers=auth_headers,
                                  json={"type": "radarr", "name": "R", "url": "/data", "api_key": "k"})
    assert radarr_con_ruta.status_code == 422
    sin_clave = client.post("/api/instances", headers=auth_headers,
                            json={"type": "radarr", "name": "R", "url": "http://r", "api_key": ""})
    assert sin_clave.status_code == 422


def test_api_identificar_errores(client, auth_headers, carpeta_dir, lecturas, busquedas):
    assert client.post("/api/library/999/identificar", headers=auth_headers,
                       json={"tmdb_id": "1"}).status_code == 404
    _crear(client, auth_headers, carpeta_dir)
    instancia = next(i for i in client.get("/api/instances", headers=auth_headers).json() if i["type"] == "carpeta")
    client.post(f"/api/instances/{instancia['id']}/sync", headers=auth_headers)
    peli = next(it for it in client.get("/api/library?por_identificar=true", headers=auth_headers).json()["items"]
                if it["item_type"] == "movie")
    malo = client.post(f"/api/library/{peli['id']}/identificar", headers=auth_headers, json={"tmdb_id": "abc"})
    assert malo.status_code == 400 and malo.json()["detail"] == "El id de TMDb es un número"
