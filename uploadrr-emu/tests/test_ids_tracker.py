"""La tabla de ids de data/trackers/: sin ella, o mal escrita, EMU no se carga (nunca envía un "0" por defecto)."""

import json
import sys

import pytest

from src.utils import ids_tracker
from src.utils.ids_tracker import TablaIdsError, cargar_ids
from src.utils.loads import load_trackers

TABLA = json.loads(ids_tracker.ruta_ids("EMU").read_text(encoding="utf-8"))


@pytest.fixture
def carpeta(tmp_path, monkeypatch):
    monkeypatch.setattr(ids_tracker, "CARPETA", tmp_path)
    return tmp_path


def test_sin_fichero_falla_con_la_ruta(carpeta):
    with pytest.raises(TablaIdsError, match=r"tabla de ids de EMU .*EMU\.json"):
        cargar_ids("EMU")


@pytest.mark.parametrize(
    "romper",
    [
        lambda t: "{no es json",
        lambda t: "[]",
        lambda t: json.dumps({**t, "version": 2}),
        lambda t: json.dumps({k: v for k, v in t.items() if k != "types"}),
        lambda t: json.dumps({**t, "types": [{"id": "4", "tokens": ["webdl"]}]}),
        lambda t: json.dumps({**t, "types": [{"id": 4}]}),
        lambda t: json.dumps({**t, "types": [{"id": 4, "tokens": ["webdl"]}, {"id": 5, "tokens": ["webdl"]}]}),
        lambda t: json.dumps({**t, "defaults": {"categories": 0, "types": 0}}),
        lambda t: json.dumps({**t, "search": {"resolution_groups": [["3", "4"]]}}),
    ],
    ids=["json-roto", "lista", "version", "sin-seccion", "id-texto", "sin-tokens", "token-repetido", "sin-defecto", "grupo-texto"],
)
def test_mal_formada_falla(carpeta, romper):
    (carpeta / "EMU.json").write_text(romper(TABLA), encoding="utf-8")
    with pytest.raises(TablaIdsError):
        cargar_ids("EMU")


def test_la_de_verdad_se_carga():
    assert cargar_ids("EMU")["tracker"] == "EMU"


def test_sin_tabla_emu_no_es_un_tracker_valido(carpeta, monkeypatch):
    """upload.py sale con «No valid trackers» en vez de subir con ids inventados."""
    monkeypatch.delitem(sys.modules, "src.trackers.EMU", raising=False)
    assert load_trackers(["EMU"], {"TRACKERS": {"EMU": {"api_key": "x"}}, "DEFAULT": {}}) == {}
