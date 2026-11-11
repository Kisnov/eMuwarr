"""El CSS del Panel no usa `vh` a pelo (los carriles se acotan con `--vh100`).

La interfaz se escala con `--ui-scale` (zoom del body) y en el móvil `vh` ignora la barra de direcciones; `--vh100`
(`globals.css`) ya trae las dos correcciones. Guarda `frontend/src/styles/panel.css`.
"""
from __future__ import annotations

import re
from pathlib import Path

PANEL_CSS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "styles" / "panel.css"


def test_panel_css_no_usa_unidades_vh():
    sin_comentarios = re.sub(r"/\*.*?\*/", "", PANEL_CSS.read_text(encoding="utf-8"), flags=re.DOTALL)
    culpables = re.findall(r"[\d.]+\s*(?:vh|dvh|svh|lvh)\b", sin_comentarios)
    assert not culpables, f"Usa calc(var(--vh100) * …) en vez de {culpables}"
    assert "var(--vh100)" in sin_comentarios
