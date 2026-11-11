"""Un póster sin imagen enseña su nombre una sola vez.

La tarjeta pequeña de Biblioteca (`bib-tarjeta`) pone el nombre en el marco cuando no hay imagen
(`bib-tarjeta-sin-nombre`), y su pie con el título aparece al pasar el ratón o siempre en táctil
(`hover: none`): el título salía dos veces. LibraryPage marca la tarjeta con `bib-tarjeta-sin-poster`
(lo cubre LibraryPage.test.tsx) y esta regla de biblioteca.css esconde el título del pie solo ahí.
"""
from __future__ import annotations

import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "styles" / "biblioteca.css"


def test_el_titulo_del_pie_se_esconde_solo_en_tarjetas_sin_poster():
    css = CSS.read_text(encoding="utf-8")
    assert re.search(r"\.bib-tarjeta-sin-poster\s+\.bib-tarjeta-titulo\s*\{\s*display:\s*none", css)
    # No hay una regla que esconda el título del pie de las tarjetas con imagen.
    for regla in re.finditer(r"([^{}]+)\{\s*display:\s*none", css):
        if "bib-tarjeta-titulo" in regla.group(1):
            assert "bib-tarjeta-sin-poster" in regla.group(1)
