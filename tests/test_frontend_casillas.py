"""Las casillas de varias opciones (frontend/src/styles/components.css) tienen aspecto propio, no el nativo.

Los ajustes de sí o no son interruptores `role="switch"` (`.aj-interruptor__control`); las listas de varias opciones
siguen siendo casillas, pero con el mismo vocabulario que el interruptor y no la casilla nativa de 15 px con el azul
de sistema. jsdom no calcula estilos, así que se lee la hoja tal cual.
"""
from __future__ import annotations

import re
from pathlib import Path

ESTILOS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "styles"
CSS = (ESTILOS / "components.css").read_text(encoding="utf-8")
SEL = r'input\[type="checkbox"\]:not\(\[role="switch"\]\)'


def _regla(selector: str) -> str:
    m = re.search(r"^" + selector + r"\s*\{([^}]*)\}", CSS, re.MULTILINE)
    assert m, f"Falta la regla {selector!r} en components.css"
    return m.group(1)


def test_la_casilla_no_es_la_nativa_y_deja_fuera_a_los_interruptores():
    cuerpo = _regla(SEL)
    assert "appearance: none" in cuerpo
    assert re.search(r"width:\s*16px", cuerpo) and re.search(r"height:\s*16px", cuerpo)


def test_marcada_va_en_cian_y_lleva_marca():
    assert "--hud-cyan" in _regla(SEL + ":checked")
    assert 'content: ""' in _regla(SEL + ":checked::after")


def test_ninguna_hoja_vuelve_a_pintar_la_casilla_con_el_color_del_sistema():
    culpables = [
        f"{ruta.name}:{n}"
        for ruta in sorted(ESTILOS.glob("*.css"))
        for n, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1)
        if linea.strip().startswith("accent-color")
    ]
    assert not culpables, f"Las casillas llevan estilo propio, no accent-color: {culpables}"


def test_con_el_dedo_la_etiqueta_de_la_casilla_llega_al_minimo_de_toque():
    m = re.search(
        r"@media \(pointer: coarse\) \{\s*\.checkbox-label:not\(\.lote-casilla\) \{([^}]*)\}", CSS
    )
    assert m and "min-height: var(--tap-min)" in m.group(1)
