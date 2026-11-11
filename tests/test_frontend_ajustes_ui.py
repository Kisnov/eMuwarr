"""Configuración (frontend/src/styles): la rejilla de tarjetas sigue el orden del índice y la fila de pestañas se desplaza.

jsdom no calcula estilos ni medidas, así que se lee la hoja tal cual.
"""
from __future__ import annotations

import re
from pathlib import Path

ESTILOS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "styles"


def _css(nombre: str) -> str:
    return (ESTILOS / nombre).read_text(encoding="utf-8")


def _reglas(css: str, selector: str) -> list[str]:
    return re.findall(r"^" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.MULTILINE)


def test_las_tarjetas_van_en_rejilla_y_no_en_columnas_css():
    # Con `columns` las tarjetas se reparten de arriba abajo columna a columna y la que abría la segunda columna
    # cambiaba con el ancho: no coincidía con el orden del índice. La rejilla va por filas, en el orden del DOM.
    reglas = _reglas(_css("dashboard.css"), ".settings-config-grid") + _reglas(
        _css("ajustes.css"), ".aj-config .settings-config-grid"
    )
    assert reglas, "Falta la regla de .settings-config-grid"
    todo = "\n".join(reglas)
    assert "display: grid" in todo and "grid-template-columns" in todo
    assert not re.search(r"(^|[\s;])columns\s*:", todo), "`columns` vuelve a repartir las tarjetas por columnas"
    assert not re.search(r"column-count|column-fill", todo)
