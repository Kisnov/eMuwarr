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


def test_a_390_px_la_cuarta_pestana_asoma_y_las_pestanas_llegan_al_minimo_de_toque():
    css = _css("ajustes.css")
    movil = css[css.index("@media (max-width: 768px) {\n  .aj-tabs {"):]
    m = re.search(r"\.hud-app \.aj-tab \{([^}]*)\}", movil)
    assert m, "Falta el relleno de las pestañas en móvil"
    relleno = re.search(r"padding:\s*\d+px (\d+)px", m.group(1))
    # Con 14 px a cada lado Backup quedaba fuera de la fila a 390 px; con 11 asoma y la fila se desplaza hasta ella.
    assert relleno and int(relleno.group(1)) <= 11
    assert re.search(r"@media \(pointer: coarse\) \{\s*\.aj-indice__enlace,\s*\.hud-app \.aj-tab \{[^}]*min-height: var\(--tap-min\)", css)


def test_toda_tarjeta_de_diagnostico_y_backup_lleva_icono_en_la_cabecera():
    ajustes = Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "ajustes"
    for ruta in ("DiagnosticsTab.tsx", "BackupTab.tsx", "diagnostico/VersionCard.tsx", "diagnostico/PathMappingsCard.tsx"):
        fuente = (ajustes / ruta).read_text(encoding="utf-8")
        cabeceras = len(re.findall(r"<CabeceraTarjeta\b", fuente))
        iconos = len(re.findall(r"\bicono=\{", fuente))
        assert cabeceras and cabeceras == iconos, f"{ruta}: {cabeceras} cabeceras y {iconos} iconos"
