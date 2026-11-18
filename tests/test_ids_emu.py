"""La tabla de ids de eMuwarez: una sola, `uploadrr-emu/data/trackers/EMU.json`, para la app y Uploadrr-EMU.

Uploadrr-EMU envía con ella los ids de la subida; la app pone con ella el nombre a los ids de las peticiones.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

import pytest

from emuwarez_uploadrr.integrations import emu

CARPETA_UPLOADRR = Path(__file__).resolve().parents[1] / "uploadrr-emu"


@pytest.fixture(scope="module")
def ids_uploadrr():
    """`src/utils/ids_tracker.py` de Uploadrr-EMU (solo stdlib), el que carga EMU.py."""
    spec = importlib.util.spec_from_file_location("_uploadrr_ids_real", CARPETA_UPLOADRR / "src/utils/ids_tracker.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_la_app_y_uploadrr_leen_el_mismo_fichero(ids_uploadrr):
    assert emu.RUTA_IDS_EMU.resolve() == ids_uploadrr.ruta_ids("EMU").resolve()
    tabla = ids_uploadrr.cargar_ids("EMU")  # la que Uploadrr-EMU da por buena
    for seccion, etiquetas in (("categories", emu.EMU_CATEGORIAS), ("types", emu.EMU_TIPOS),
                               ("resolutions", emu.EMU_RESOLUCIONES)):
        assert etiquetas == {e["id"]: e["label"] for e in tabla[seccion] if "label" in e}


def test_cada_tipo_que_la_app_da_por_bueno_tiene_id_en_la_tabla(ids_uploadrr):
    """La comprobación «tipo» deja subir solo con un `-t` de _UPLOADRR_TYPES: ninguno puede acabar en el tipo 0."""
    from emuwarez_uploadrr.services.uploadrr_contract import _UPLOADRR_TYPES
    tokens = {t for e in ids_uploadrr.cargar_ids("EMU")["types"] for t in e["tokens"]}
    assert set(_UPLOADRR_TYPES.values()) <= tokens


def test_las_etiquetas_no_cambian():
    assert emu.EMU_CATEGORIAS == {1: "Películas", 2: "Series", 4: "Documentales"}
    assert emu.EMU_TIPOS == {
        1: "Full Disc", 2: "Remux", 3: "Encode", 4: "WEB-DL", 5: "WEBRIP", 6: "HDTV", 7: "SD", 8: "STEAMRIP",
    }
    assert emu.EMU_RESOLUCIONES == {
        1: "4320p", 2: "2160p", 3: "1080p", 4: "1080i", 5: "720p", 6: "576p", 7: "540p", 8: "480p", 10: "Otras",
    }


@pytest.mark.parametrize("contenido", [None, "{roto", '{"version": 1, "categories": []}'],
                         ids=["sin-fichero", "json-roto", "sin-seccion"])
def test_sin_tabla_avisa_una_vez(tmp_path, caplog, contenido):
    ruta = tmp_path / "EMU.json"
    if contenido is not None:
        ruta.write_text(contenido, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="emuwarez.emu"):
        assert emu._cargar_etiquetas(ruta) is None
    assert len([r for r in caplog.records if "tabla de ids" in r.message]) == 1


FILA = {"id": 1, "name": "Dune", "category_id": 4, "type_id": 8, "resolution_id": 10}


def test_sin_tabla_las_peticiones_llevan_el_id(monkeypatch):
    monkeypatch.setattr(emu, "_ETIQUETAS", None)
    for nombre in ("EMU_CATEGORIAS", "EMU_TIPOS", "EMU_RESOLUCIONES"):
        monkeypatch.setattr(emu, nombre, {})
    item = emu.normalize_request(FILA)
    assert (item.category, item.requested_source, item.requested_resolution) == ("Categoría 4", "Tipo 8", "Resolución 10")
    assert emu.normalize_request({"id": 2, "name": "X"}).category is None


def test_con_tabla_un_id_desconocido_sigue_sin_nombre():
    item = emu.normalize_request({**FILA, "category_id": 3, "type_id": 9, "resolution_id": 9})
    assert (item.category, item.requested_source, item.requested_resolution) == (None, None, None)
    item = emu.normalize_request(FILA)
    assert (item.category, item.requested_source, item.requested_resolution) == ("Documentales", "STEAMRIP", "Otras")
