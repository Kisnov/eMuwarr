"""Peticiones y Subidas (frontend/src/styles): detalles de forma que DESIGN.md fija y la app había dejado distintos.

jsdom no calcula estilos, así que se lee la hoja tal cual.
"""
from __future__ import annotations

import re
from pathlib import Path

ESTILOS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "styles"


def _regla(archivo: str, selector: str) -> str:
    css = (ESTILOS / archivo).read_text(encoding="utf-8")
    m = re.search(r"^" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.MULTILINE)
    assert m, f"Falta la regla {selector!r} en {archivo}"
    return m.group(1)


def test_los_chips_de_grupo_de_peticiones_son_de_6_px_no_pildora():
    # DESIGN.md, Chips: chapa de 6 px (`--radius-badge`).
    radio = re.search(r"border-radius:\s*([^;]+);", _regla("peticiones.css", ".pet-resumen-chip"))
    assert radio and radio.group(1).strip() == "var(--radius-badge)"
