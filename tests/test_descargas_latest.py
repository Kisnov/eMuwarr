"""La insignia de descargas cuenta las de la versión que lleva :latest, no las del paquete entero.

El total de GHCR suma cada :dev, cada :pr-<n> y las versiones viejas; la cifra buena es la de la fila de :latest en la
página de versiones. Si la página cambia y esa fila deja de decirla, el script falla en vez de leer la de al lado.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def _cargar(nombre: str):
    ruta = RAIZ / "scripts" / f"{nombre}.py"
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


descargas = _cargar("descargas_latest")


def _fila(version: int, etiquetas: list[str], cifra: str | None) -> str:
    enlaces = "".join(
        f'<a class="Label" href="/users/Kisnov/packages/container/emuwarr/{version}?tag={e}">{e}</a>' for e in etiquetas
    )
    pie = f'<span><svg class="octicon"></svg>\n  {cifra}\n</span> Version downloads' if cifra is not None else ""
    return f'<li class="Box-row"><div>{enlaces}</div><div>Published about 2 hours ago · Digest sha256:abc</div>{pie}</li>'


def _pagina(*filas: str) -> str:
    return f"<html><body><ul>{''.join(filas)}</ul></body></html>"


def test_lee_la_fila_de_latest_y_no_las_de_al_lado():
    pagina = _pagina(
        _fila(30, ["pr-55"], "0"),
        _fila(20, ["dev"], "1"),
        _fila(10, ["latest", "1.3.2", "b44358fb0aadfd22f7ead30462fff9e89adf8f5f"], "6"),
        _fila(5, ["1.3.1"], "120"),
    )
    assert descargas.descargas_de(pagina) == ("10", 6, ["latest", "1.3.2", "b44358fb0aadfd22f7ead30462fff9e89adf8f5f"])


def test_sin_latest_en_la_pagina_no_hay_cifra():
    assert descargas.descargas_de(_pagina(_fila(30, ["pr-55"], "0"), _fila(20, ["dev"], "1"))) is None


def test_una_fila_de_latest_sin_cifra_es_un_error_y_no_la_de_la_siguiente():
    pagina = _pagina(_fila(10, ["latest", "1.3.2"], None), _fila(5, ["1.3.1"], "120"))
    with pytest.raises(ValueError):
        descargas.descargas_de(pagina)


def test_la_etiqueta_tiene_que_ser_exacta():
    assert descargas.descargas_de(_pagina(_fila(10, ["latest-arm"], "3"))) is None


@pytest.mark.parametrize("cifra,esperada", [("0", 0), ("6", 6), ("1,234", 1234), ("1.2k", 1200), ("2M", 2_000_000)])
def test_cifras_como_las_escribe_github(cifra, esperada):
    assert descargas.descargas_de(_pagina(_fila(10, ["latest"], cifra)))[1] == esperada


def test_busca_latest_en_las_paginas_siguientes(tmp_path):
    paginas = {
        descargas.PAGINA.format(1): _pagina(_fila(30, ["pr-55"], "0"), _fila(20, ["dev"], "1")),
        descargas.PAGINA.format(2): _pagina(_fila(10, ["latest", "1.3.2"], "1,042")),
    }
    salida = tmp_path / "descargas.json"
    assert descargas.main(["--salida", str(salida)], lector=paginas.__getitem__) == 0
    datos = json.loads(salida.read_text(encoding="utf-8"))
    assert datos["schemaVersion"] == 1
    assert datos["label"] == "descargas :latest"
    assert datos["message"] == "1.042"


def test_sin_latest_en_ninguna_pagina_falla_sin_escribir_nada(tmp_path, capsys):
    paginas = {descargas.PAGINA.format(1): _pagina(_fila(20, ["dev"], "1")), descargas.PAGINA.format(2): _pagina()}
    salida = tmp_path / "descargas.json"
    assert descargas.main(["--salida", str(salida)], lector=paginas.__getitem__) == 1
    assert not salida.exists()
    assert ":latest" in capsys.readouterr().err


def test_un_fallo_de_red_falla_sin_escribir_nada(tmp_path):
    def sin_red(url):
        raise OSError("sin red")

    salida = tmp_path / "descargas.json"
    assert descargas.main(["--salida", str(salida)], lector=sin_red) == 1
    assert not salida.exists()
