"""El filtro Estado de Biblioteca, contra lo que enseña la columna.

La regla vive en DOS sitios: el resumen que pinta la celda de una serie o temporada
(`rollups._aggregate_local_status`, vía `_batch_rollups`) y el SQL del filtro
(`rollups.estado_local_sql`). «Parcial» solo existe como resumen: ninguna fila lo guarda, así que filtrar
por `local_status` no podía ofrecerlo, y filtrar por «Disponible» devolvía series que la celda dice «Parcial».
Este test cruza las dos para todas las filas, con y sin «ignorar especiales».
"""
from __future__ import annotations

import pytest

from emuwarez_uploadrr.database import connect, db_path, init_db
from emuwarez_uploadrr.services.library import (
    _batch_rollups, count_library, library_filter_values, list_library, list_library_agrupada,
)

ESTADOS = ("Disponible", "Faltante", "Parcial")


@pytest.fixture
def conn(isolated_db):
    c = connect(db_path())
    init_db(c)
    yield c
    c.close()


def _poner(c, titulo, tipo, estado, *, padre=None, temporada=None, capitulo=None) -> int:
    return c.execute(
        """INSERT INTO library_items (item_type, title, local_status, parent_id, season_number, episode_number,
                                      instance_type, instance_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (tipo, titulo, estado, padre, temporada, capitulo, "sonarr" if tipo != "movie" else "radarr", 1),
    ).lastrowid


def _serie(c, titulo, temporadas, *, guardado="Disponible"):
    """`temporadas`: {número: [estados de sus capítulos]}. `guardado` es el estado de la fila de la serie y de
    sus temporadas, que es el que Sonarr da y la celda NO enseña cuando hay capítulos."""
    sid = _poner(c, titulo, "series", guardado)
    for n, estados in temporadas.items():
        seid = _poner(c, f"{titulo} T{n}", "season", guardado, padre=sid, temporada=n)
        for e, estado in enumerate(estados, 1):
            _poner(c, f"{titulo} T{n}E{e}", "episode", estado, padre=seid, temporada=n, capitulo=e)


@pytest.fixture
def biblioteca(conn):
    _poner(conn, "Peli disponible", "movie", "Disponible")
    _poner(conn, "Peli faltante", "movie", "Faltante")
    _poner(conn, "Peli subiendo", "movie", "Subiendo")
    _serie(conn, "Completa", {1: ["Disponible", "Disponible"], 2: ["Disponible"]})
    _serie(conn, "Vacia", {1: ["Faltante", "Faltante"]})
    # Sonarr la guarda «Disponible» y la celda enseña «Parcial»: lo que hacía mentir al filtro.
    _serie(conn, "Mezcla", {1: ["Disponible", "Faltante"], 2: ["Disponible", "Disponible"]})
    # «Subido» no cuenta como Disponible en el resumen (ni en la celda): ninguno → Faltante.
    _serie(conn, "Subida", {1: ["Subido", "Subido"]}, guardado="Subido")
    _serie(conn, "Con especiales", {0: ["Faltante"], 1: ["Disponible", "Disponible"]}, guardado="Parcial")
    _serie(conn, "Solo especiales", {0: ["Faltante", "Faltante"]}, guardado="Faltante")
    # Un capítulo en otro estado (aquí con error) no está disponible.
    _serie(conn, "Sin estado", {1: ["Disponible", "Error"]})
    # Una serie sin capítulos conserva el suyo.
    _poner(conn, "Sin capitulos", "series", "Faltante")
    _poner(conn, "Sin capitulos parcial", "series", "Parcial")
    conn.commit()
    return conn


def _la_celda_dice(c, ignorar_especiales: bool) -> dict[int, str]:
    """El Estado de cada fila tal y como lo monta la lista (`_campos_de_fila`): el resumen de sus capítulos si es
    una serie o temporada que los tiene, y el guardado en cualquier otro caso."""
    filas = [dict(r) for r in c.execute("SELECT id, item_type, local_status FROM library_items")]
    resumenes = _batch_rollups(
        c, [f["id"] for f in filas if f["item_type"] == "series"],
        [f["id"] for f in filas if f["item_type"] == "season"], ignorar_especiales)
    return {f["id"]: (resumenes.get(f["id"], {}).get("local_status") or f["local_status"]) for f in filas}


@pytest.mark.parametrize("ignorar_especiales", [True, False])
@pytest.mark.parametrize("estado", ESTADOS)
def test_el_filtro_devuelve_justo_lo_que_la_celda_enseña(biblioteca, estado, ignorar_especiales):
    esperados = {i for i, e in _la_celda_dice(biblioteca, ignorar_especiales).items() if e == estado}
    ids = {r["id"] for r in list_library(biblioteca, status=estado, limit=1000, ignore_specials=ignorar_especiales)}
    assert ids == esperados, (
        f"\nEl filtro Estado «{estado}» y la celda no dicen lo mismo (ignorar especiales: {ignorar_especiales})."
        f"\n  solo en el filtro: {sorted(ids - esperados)}\n  solo en la celda: {sorted(esperados - ids)}")
    # El total de la paginación sale de la misma regla.
    assert count_library(biblioteca, status=estado, ignore_specials=ignorar_especiales) == len(esperados)


def test_parcial_encuentra_series_y_temporadas_con_mezcla(biblioteca):
    titulos = {r["title"] for r in list_library(biblioteca, status="Parcial", limit=1000)}
    # «Mezcla» (la serie y su temporada 1), «Sin estado» (la serie y su temporada 1) y la que ya lo guardaba.
    assert {"Mezcla", "Mezcla T1", "Sin estado", "Sin estado T1", "Sin capitulos parcial"} <= titulos
    # «Con especiales» solo es Parcial si se cuentan los especiales.
    assert "Con especiales" not in titulos
    sin_ignorar = {r["title"] for r in list_library(biblioteca, status="Parcial", limit=1000, ignore_specials=False)}
    assert "Con especiales" in sin_ignorar


def test_disponible_ya_no_trae_la_serie_que_la_celda_dice_parcial(biblioteca):
    titulos = {r["title"] for r in list_library(biblioteca, status="Disponible", limit=1000)}
    assert "Completa" in titulos and "Con especiales" in titulos
    assert "Mezcla" not in titulos and "Sin estado" not in titulos


def test_varios_estados_se_suman_y_el_agrupado_usa_la_misma_regla(biblioteca):
    celda = _la_celda_dice(biblioteca, True)
    filas = list_library(biblioteca, status=["Parcial", "Faltante"], limit=1000)
    assert {r["id"] for r in filas} == {i for i, e in celda.items() if e in ("Parcial", "Faltante")}
    # En plano, lo que cumple sube hasta su serie: «Mezcla» sale por su Parcial aunque se guarde Disponible.
    raices, total = list_library_agrupada(biblioteca, status="Parcial", limit=1000)
    assert "Mezcla" in {r["title"] for r in raices} and total == len(raices)


@pytest.mark.parametrize("ignorar_especiales", [True, False])
def test_el_desplegable_ofrece_los_estados_que_enseña_la_celda(biblioteca, ignorar_especiales):
    from emuwarez_uploadrr.services.settings import escribir_ajuste
    escribir_ajuste(biblioteca, "series_ignore_specials", ignorar_especiales)
    biblioteca.commit()
    celda = _la_celda_dice(biblioteca, ignorar_especiales)
    assert library_filter_values(biblioteca)["statuses"] == sorted(set(celda.values()))
    assert "Parcial" in library_filter_values(biblioteca)["statuses"]
