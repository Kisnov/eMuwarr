"""El CSS del frontend no usa `vh` a pelo (las alturas se acotan con `--vh100`).

La interfaz se escala con `--ui-scale` (zoom del body) y en el móvil `vh` ignora la barra de direcciones; `--vh100`
(`globals.css`, el único que puede usar `vh` porque lo define) ya trae las dos correcciones.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ESTILOS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "styles"
HOJAS = sorted(p for p in ESTILOS.glob("*.css") if p.name != "globals.css")


def test_hay_hojas_que_mirar():
    assert any(p.name == "panel.css" for p in HOJAS)


@pytest.mark.parametrize("hoja", HOJAS, ids=lambda p: p.name)
def test_css_no_usa_unidades_vh(hoja):
    sin_comentarios = re.sub(r"/\*.*?\*/", "", hoja.read_text(encoding="utf-8"), flags=re.DOTALL)
    culpables = re.findall(r"[\d.]+\s*(?:vh|dvh|svh|lvh)\b", sin_comentarios)
    assert not culpables, f"{hoja.name}: usa calc(var(--vh100) * …) en vez de {culpables}"
