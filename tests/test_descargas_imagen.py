"""La insignia de descargas suma las de todas las versiones publicadas (X.Y.Z), no el total del paquete.

El «Total downloads» de GHCR suma también cada :dev, cada :pr-<n> y las versiones sin etiqueta, y puede bajar cuando la
limpieza las borra. La suma de las X.Y.Z, leída fila a fila de la página de versiones, solo crece. Si la página cambia y
una fila deja de decir su cifra, el script falla en vez de leer la de al lado.
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


descargas = _cargar("descargas_imagen")


def _fila(version: int, etiquetas: list[str], cifra: str | None) -> str:
    enlaces = "".join(
        f'<a class="Label" href="/users/Kisnov/packages/container/emuwarr/{version}?tag={e}">{e}</a>' for e in etiquetas
    )
    pie = f'<span><svg class="octicon"></svg>\n  {cifra}\n</span> Version downloads' if cifra is not None else ""
    return f'<li class="Box-row"><div>{enlaces}</div><div>Published about 2 hours ago · Digest sha256:abc</div>{pie}</li>'


def _pagina(*filas: str) -> str:
    return f"<html><body><ul>{''.join(filas)}</ul></body></html>"


def _main(paginas: dict[int, str], tmp_path) -> tuple[int, pathlib.Path]:
    urls = {descargas.PAGINA.format(n): p for n, p in paginas.items()}
    salida = tmp_path / "descargas.json"
    return descargas.main(["--salida", str(salida)], lector=lambda url: urls.get(url, _pagina())), salida


def test_cada_fila_con_su_cifra():
    pagina = _pagina(
        _fila(30, ["pr-55"], "0"),
        _fila(20, ["dev"], "1"),
        _fila(10, ["latest", "1.3.2", "b44358fb0aadfd22f7ead30462fff9e89adf8f5f"], "6"),
        _fila(5, ["1.3.1"], "120"),
    )
    assert descargas.filas(pagina) == [
        ("30", ["pr-55"], 0),
        ("20", ["dev"], 1),
        ("10", ["latest", "1.3.2", "b44358fb0aadfd22f7ead30462fff9e89adf8f5f"], 6),
        ("5", ["1.3.1"], 120),
    ]


def test_una_fila_sin_cifra_es_un_error_y_no_la_de_la_siguiente():
    with pytest.raises(ValueError):
        descargas.filas(_pagina(_fila(10, ["latest", "1.3.2"], None), _fila(5, ["1.3.1"], "120")))


@pytest.mark.parametrize("cifra,esperada", [("0", 0), ("6", 6), ("1,234", 1234), ("1.2k", 1200), ("2M", 2_000_000)])
def test_cifras_como_las_escribe_github(cifra, esperada):
    assert descargas.filas(_pagina(_fila(10, ["1.3.2"], cifra)))[0][2] == esperada


def test_suma_solo_las_publicadas_de_todas_las_paginas(tmp_path):
    codigo, salida = _main({
        1: _pagina(_fila(30, ["pr-55"], "40"), _fila(20, ["dev"], "9"), _fila(10, ["latest", "1.3.2"], "6")),
        2: _pagina(_fila(5, ["1.3.1"], "1,000"), _fila(4, ["multitracker"], "3"), _fila(3, ["1.3.0-rc"], "7")),
    }, tmp_path)
    assert codigo == 0
    datos = json.loads(salida.read_text(encoding="utf-8"))
    assert datos["schemaVersion"] == 1
    assert datos["label"] == "descargas"
    assert datos["message"] == "1.006"


def test_una_version_repetida_entre_paginas_cuenta_una_vez(tmp_path):
    codigo, salida = _main({
        1: _pagina(_fila(10, ["latest", "1.3.2"], "6"), _fila(5, ["1.3.1"], "100")),
        2: _pagina(_fila(5, ["1.3.1"], "100"), _fila(4, ["1.3.0"], "50")),
    }, tmp_path)
    assert codigo == 0
    assert json.loads(salida.read_text(encoding="utf-8"))["message"] == "156"


def test_sin_versiones_publicadas_falla_sin_escribir_nada(tmp_path, capsys):
    codigo, salida = _main({1: _pagina(_fila(20, ["dev"], "1"), _fila(30, ["pr-55"], "0"))}, tmp_path)
    assert codigo == 1
    assert not salida.exists()
    assert "X.Y.Z" in capsys.readouterr().err


def test_mas_paginas_de_las_previstas_falla_en_vez_de_sumar_corto(tmp_path, monkeypatch):
    monkeypatch.setattr(descargas, "MAX_PAGINAS", 2)
    codigo, salida = _main({n: _pagina(_fila(10 - n, [f"1.0.{n}"], "1")) for n in (1, 2, 3)}, tmp_path)
    assert codigo == 1
    assert not salida.exists()


def test_una_fila_rota_falla_sin_escribir_nada(tmp_path):
    codigo, salida = _main({1: _pagina(_fila(10, ["latest", "1.3.2"], None), _fila(5, ["1.3.1"], "120"))}, tmp_path)
    assert codigo == 1
    assert not salida.exists()


def test_un_fallo_de_red_falla_sin_escribir_nada(tmp_path):
    def sin_red(url):
        raise OSError("sin red")

    salida = tmp_path / "descargas.json"
    assert descargas.main(["--salida", str(salida)], lector=sin_red) == 1
    assert not salida.exists()
