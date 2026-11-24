"""La conclusión de «En <tracker> ya hay» (emu_catalog.conclusion_duplicados): la única regla de duplicados y trumps.
El asistente, el lote y «Subir también a…» enseñan lo que dice; el navegador no la aplica."""
from __future__ import annotations

from emuwarez_uploadrr.services.emu_catalog import conclusion_duplicados, get_items_comparison
from emuwarez_uploadrr.services.trackers import obtener
from tests.test_catalogo_por_tracker import _pelicula, db_conn  # noqa: F401 (fixture)

_FIXTURES_COMPARTIDAS = (db_conn,)


def _con(*veredictos, aporta_audio=False):
    return [{"name": "T", "veredicto": v, "aporta_audio": aporta_audio} for v in veredictos]


def test_sin_torrents_no_hay_conclusion():
    assert conclusion_duplicados([]) is None


def test_una_copia_peor_dice_que_tracker_la_rechazaria():
    assert conclusion_duplicados(_con("peor"))["texto"].endswith(": eMuwarez probablemente la rechace.")
    assert conclusion_duplicados(_con("peor"), obtener("mnv"))["texto"].endswith(": Milnueve probablemente la rechace.")


def test_una_calidad_igual_a_alguna_es_un_duplicado_aunque_otras_sean_peores():
    assert conclusion_duplicados(_con("peor", "igual")) == {
        "nivel": "duplicado", "clase": "igual", "ya_lo_tiene": True,
        "texto": "Ya hay 1 torrent con tu misma calidad: la subida sería un duplicado."}
    assert conclusion_duplicados(_con("igual", "igual"))["texto"].startswith("Ya hay 2 torrents con tu misma calidad")


def test_mejor_que_todo_es_un_posible_trump_y_manda_revisar_las_reglas():
    assert conclusion_duplicados(_con("mejor", "mejor")) == {
        "nivel": "trump", "clase": "mejor", "ya_lo_tiene": False,
        "texto": "Tu copia mejora los 2 torrents que hay: puede ser un trump. Revisa las reglas de trumps antes de subir."}
    assert conclusion_duplicados(_con("mejor"))["texto"].startswith("Tu copia mejora el torrent que hay:")


def test_mejor_que_unos_y_peor_que_otros_no_es_un_trump_claro():
    assert conclusion_duplicados(_con("mejor", "peor")) == {
        "nivel": "mixto", "clase": "igual", "ya_lo_tiene": False,
        "texto": "Tu copia mejora 1 y empeora 1 de lo que hay: no es un trump claro."}


def test_peor_que_todo_se_dice_como_rechazo_probable():
    assert conclusion_duplicados(_con("peor")) == {
        "nivel": "peor", "clase": "peor", "ya_lo_tiene": True,
        "texto": "Tu copia es peor que todo lo que ya hay: eMuwarez probablemente la rechace."}


def test_si_falta_alguna_comparacion_la_conclusion_es_parcial_y_no_afirma_peor_que_todo():
    peor = conclusion_duplicados(_con("peor", None))
    assert peor["nivel"] == "peor"
    assert peor["texto"] == ("Tu copia es peor que todo lo comparado: eMuwarez probablemente la rechace. "
                             "Alguna comparación falta: la conclusión es parcial.")
    assert "parcial" in conclusion_duplicados(_con("mejor", None))["texto"]
    assert "parcial" not in conclusion_duplicados(_con("peor"))["texto"]


def test_sin_nada_comparable_no_afirma_nada():
    assert conclusion_duplicados(_con(None)) == {
        "nivel": "sin-comparar", "clase": None, "ya_lo_tiene": False,
        "texto": "No se pudo comparar la calidad con lo que ya hay: revisa a mano si sería un duplicado."}


def test_misma_calidad_con_audio_esp_que_no_tiene_ninguno_es_una_mejora():
    assert conclusion_duplicados(_con("igual", aporta_audio=True)) == {
        "nivel": "trump", "clase": "mejor", "ya_lo_tiene": False,
        "texto": "Aportas audio ESP que no tiene ninguno: puede ser un trump. Revisa las reglas de trumps antes de subir."}


def test_misma_calidad_y_el_mismo_audio_sigue_siendo_un_duplicado():
    # Con uno que ya lo tiene en castellano (o sin saberlo), el audio no aporta: manda la calidad.
    sin_es, con_es = _con("igual", aporta_audio=True), _con("igual")
    assert conclusion_duplicados(sin_es + con_es)["nivel"] == "duplicado"
    assert conclusion_duplicados(con_es)["nivel"] == "duplicado"


def test_una_copia_peor_que_aporta_audio_es_una_mejora_y_lo_dice():
    """Decidido así: si ningún torrent tiene castellano, tu versión en castellano es nueva aunque su calidad sea
    peor. No se salta ni se desmarca; la frase avisa de la calidad y manda a las reglas de trumps."""
    assert conclusion_duplicados(_con("peor", aporta_audio=True)) == {
        "nivel": "trump", "clase": "mejor", "ya_lo_tiene": False,
        "texto": "Aportas audio ESP que no tiene ninguno, aunque tu copia empeora 1 de lo que hay: puede ser un trump. "
                 "Revisa las reglas de trumps antes de subir."}


def test_el_audio_de_la_comparacion_sale_del_catalogo(db_conn):
    from emuwarez_uploadrr.database import set_state
    from emuwarez_uploadrr.services.emu_catalog import SYNC_COMPLETED_STATE_KEY, get_item_comparison
    peli = _pelicula(db_conn, "1")
    db_conn.execute("UPDATE library_items SET audio_languages = 'spa' WHERE id = ?", (peli,))
    db_conn.execute("INSERT INTO emu_catalog (tracker, category, tmdb_id, resolution, source, has_spanish, name) "
                    "VALUES ('emu', 'Películas', '1', '1080p', 'WEB-DL', 0, 'Peli 1080p WEB-DL VOSE')")
    set_state(db_conn, SYNC_COMPLETED_STATE_KEY, "2026-11-24T00:00:00")
    db_conn.commit()

    c = get_item_comparison(db_conn, peli)

    assert c["matches"][0]["aporta_audio"] is True
    assert c["conclusion"]["nivel"] == "trump" and c["conclusion"]["texto"].startswith("Aportas audio ESP")
    assert c["aportes"] == [{"dim": "idioma", "detail": "ESP"}]


def test_varios_elementos_juntan_sus_torrents_en_una_conclusion(db_conn):
    from emuwarez_uploadrr.database import set_state
    from emuwarez_uploadrr.services.emu_catalog import SYNC_COMPLETED_STATE_KEY
    a, b = _pelicula(db_conn, "1"), _pelicula(db_conn, "2")
    for tmdb, res in (("1", "1080p"), ("2", "720p")):
        db_conn.execute("INSERT INTO emu_catalog (tracker, category, tmdb_id, resolution, source, name) "
                        "VALUES ('emu', 'Películas', ?, ?, 'WEB-DL', ?)", (tmdb, res, f"Peli {res} WEB-DL"))
    set_state(db_conn, SYNC_COMPLETED_STATE_KEY, "2026-11-24T00:00:00")
    db_conn.commit()

    r = get_items_comparison(db_conn, [a, b])

    assert [c["conclusion"]["nivel"] for c in r["comparaciones"]] == ["duplicado", "trump"]
    assert r["conclusion"]["texto"] == "Ya hay 1 torrent con tu misma calidad: la subida sería un duplicado."


def test_la_ruta_de_varias_comparaciones(client, auth_headers):
    r = client.get("/api/library/emu-comparisons?ids=999&tracker=nobs", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"comparaciones": [{"available": False, "message": "Elemento no encontrado", "matches": []}],
                        "conclusion": None}
    assert client.get("/api/library/emu-comparisons?ids=1&tracker=x", headers=auth_headers).status_code == 422
