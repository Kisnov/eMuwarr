"""Configuración (frontend/src/styles): tarjetas en columnas sin huecos, listas de proveedores con alto máximo y fila de pestañas que se desplaza.

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


def test_las_tarjetas_van_en_columnas_css_sin_huecos_entre_ellas():
    # Con una rejilla las filas se igualan al alto de la tarjeta más alta y quedan huecos en blanco (bajo TMDb y
    # qBittorrent, junto a los proveedores de vídeo). Con `columns` cada tarjeta se pega a la anterior de su columna.
    reglas = _reglas(_css("dashboard.css"), ".settings-config-grid") + _reglas(
        _css("ajustes.css"), ".aj-config .settings-config-grid"
    )
    assert reglas, "Falta la regla de .settings-config-grid"
    todo = "\n".join(reglas)
    assert re.search(r"(^|[\s;])columns\s*:\s*4\s+\d+px", todo), "Faltan las columnas CSS (máximo cuatro)"
    assert "display: grid" not in todo and "grid-template-columns" not in todo, "La rejilla deja huecos entre filas"
    # Cada tarjeta (el envoltorio .aj-seccion) no se parte entre dos columnas.
    css = _css("dashboard.css")
    assert re.search(r"\.settings-config-grid > \.aj-seccion[^{]*\{[^}]*break-inside:\s*avoid", css)


def test_las_listas_de_proveedores_enseñan_unas_filas_y_se_desplazan_por_dentro():
    css = _css("dashboard.css")
    reglas = _reglas(css, ".settings-config-grid .provider-card__list")
    assert reglas, "Falta la regla de la lista de proveedores"
    todo = "\n".join(reglas)
    assert re.search(r"max-height:\s*calc\(var\(--alto-fila-proveedor\) \* 4\.5\)", todo)
    assert "overflow-y: auto" in todo and "overscroll-behavior: contain" in todo
    # La de imágenes lleva su propio alto de fila: sus filas son más altas (insignia de clave y botón Probar).
    assert ".provider-card__list--imagenes" in css
    fuente = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "ajustes" / "config" / "ProveedoresImagenesCard.tsx").read_text(encoding="utf-8")
    assert "provider-card__list--imagenes" in fuente


def test_el_marco_de_la_aplicacion_no_se_puede_desplazar_por_codigo():
    # overflow: hidden se puede mover con scrollIntoView(); en el móvil la aplicación subía y quedaba el fondo vacío.
    reglas = _reglas(_css("layout.css"), ".app-shell")
    assert reglas and "overflow: clip" in reglas[0]


def test_ningun_salto_a_una_seccion_usa_scrollintoview_a_pelo():
    # Saltar a una tarjeta con scrollIntoView() desplazaba también la ventana con el zoom del body (utils/desplazar).
    src = Path(__file__).resolve().parents[1] / "frontend" / "src"
    for ruta in ("pages/ajustes/ConfigTab.tsx", "pages/ajustes/Campo.tsx", "pages/GuidePage.tsx", "pages/RequestsPage.tsx"):
        fuente = (src / ruta).read_text(encoding="utf-8")
        assert "desplazarAlContenedor" in fuente, ruta
        assert not re.search(r"\.scrollIntoView", fuente), f"{ruta} vuelve a usar scrollIntoView"


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
