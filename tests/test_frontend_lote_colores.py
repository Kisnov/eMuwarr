"""Banner del lote de Subidas (frontend/src/styles/subidas.css): «en cola» y «cancelada» no son el mismo gris.

Las dos eran casi del mismo gris (`--text-muted` frente a `--color-neutral`). Ahora «en cola» (aún no ha empezado) va
hueca, con un anillo, y «cancelada» (terminó sin subir) llena, y el tono de «en cola» es otro. jsdom no calcula
estilos, así que se lee la hoja tal cual.
"""
from __future__ import annotations

import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "styles" / "subidas.css"


def _regla(selector: str) -> str:
    # Al principio de línea: «.sub-lote-tramo-en_cola» también aparece al final de la regla de los dos contadores.
    m = re.search(r"^" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS.read_text(encoding="utf-8"), re.MULTILINE)
    assert m, f"Falta la regla {selector!r} en {CSS.name}"
    return m.group(1)


def _color(selector: str) -> str:
    m = re.search(r"--sub-lote-color:\s*([^;]+)", _regla(selector))
    assert m, f"{selector!r} no fija --sub-lote-color"
    return m.group(1).strip()


def test_en_cola_y_cancelada_no_usan_el_mismo_gris():
    assert _color(".sub-lote-contador-en_cola, .sub-lote-tramo-en_cola") != _color(
        ".sub-lote-contador-canceladas, .sub-lote-tramo-canceladas"
    )


def test_el_punto_y_el_tramo_de_en_cola_van_huecos():
    # Se distinguen por la forma (anillo), no solo por el tono.
    assert re.search(r"background:\s*transparent", _regla(".sub-lote-contador-en_cola .sub-lote-punto"))
    assert re.search(r"background:\s*transparent", _regla(".sub-lote-tramo-en_cola"))
