"""Transcripciones doradas: la salida real de una subida entera de Uploadrr-EMU, reproducida por los parsers de la app.

Cada `tests/fixtures/golden_uploadrr/*.txt` es el stdout capturado de Uploadrr-EMU contra su tracker falso. Las líneas
pasan por `parse_uploadrr_progress`, `subpaso_de_linea` y `classify_upload_result`, igual que en `upload_execution`.
Si Uploadrr-EMU cambia una línea, la copia dorada se regenera allí y este test dice qué patrón de la app deja de verla.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from emuwarez_uploadrr.services import subida_pasos as sp
from emuwarez_uploadrr.services.upload_result_parser import classify_upload_result
from emuwarez_uploadrr.services.uploadrr_progress import PHASE_KEY_ALIASES, RUNNING_DISPLAY_ORDER, parse_uploadrr_progress

CARPETA = Path(__file__).parent / "fixtures" / "golden_uploadrr"
UPLOADRR_EMU = Path(__file__).resolve().parents[1] / "uploadrr-emu"
TRANSCRIPCIONES = sorted(CARPETA.glob("*.txt"))
# Las de eventos (.jsonl) las prueba test_eventos_uploadrr.py; aquí solo se comprueba que son las de Uploadrr-EMU.
COPIAS = TRANSCRIPCIONES + sorted(CARPETA.glob("*.jsonl"))
# Las que acaban en fallo (duplicado, exit 1) se prueban aparte: no llegan al torrent ni a la subida.
FALLIDAS = [r for r in TRANSCRIPCIONES if "duplicado" in r.name]
EXITOSAS = [r for r in TRANSCRIPCIONES if r not in FALLIDAS]


def _lineas(ruta: Path) -> list[str]:
    return ruta.read_text(encoding="utf-8").splitlines()


@pytest.mark.skipif(bool(TRANSCRIPCIONES), reason="ya hay transcripciones")
def test_hay_transcripciones_doradas():
    pytest.skip("tests/fixtures/golden_uploadrr/ no tiene ningún .txt todavía (la transcripción real de Uploadrr-EMU)")


@pytest.mark.parametrize("ruta", FALLIDAS, ids=lambda r: r.name)
def test_un_duplicado_llega_a_la_fase_de_duplicados_y_no_pasa_de_ahi(ruta):
    lineas = _lineas(ruta)
    claves = {r[0] for r in map(parse_uploadrr_progress, lineas) if r}
    assert "duplicates" in claves
    assert not claves & {"torrent_create", "uploading_emu", "qbittorrent_add"}, sorted(claves)
    assert [p for p in map(sp.subpaso_de_linea, lineas) if p] == []

    resultado = classify_upload_result(chr(10).join(lineas), "", 1)
    assert resultado["status"] == "failed"
    assert resultado["result_url"] is None and resultado["torrent_id"] is None


@pytest.mark.parametrize("ruta", FALLIDAS, ids=lambda r: r.name)
def test_un_duplicado_se_le_dice_al_usuario_como_duplicado(ruta):
    resultado = classify_upload_result(ruta.read_text(encoding="utf-8"), "", 1)
    assert "duplic" in resultado["error_summary"].lower(), resultado["error_summary"]


@pytest.mark.parametrize("ruta", EXITOSAS, ids=lambda r: r.name)
def test_las_fases_avanzan_en_orden_y_llegan_a_la_creacion_del_torrent(ruta):
    vistas: list[tuple[str, str]] = []  # (clave cruda, fase mostrada)
    for linea in _lineas(ruta):
        r = parse_uploadrr_progress(linea)
        if r is None:
            continue
        clave = r[0]
        vistas.append((clave, PHASE_KEY_ALIASES.get(clave, clave)))

    posiciones = [RUNNING_DISPLAY_ORDER.index(fase) for _c, fase in vistas]
    assert posiciones == sorted(posiciones), f"una fase retrocede: {[c for c, _f in vistas]}"
    claves = {c for c, _f in vistas}
    for esperada in ("torrent_create", "uploading_emu", "qbittorrent_add"):
        assert esperada in claves, f"{esperada} nunca se reconoce; claves vistas: {sorted(claves)}"


@pytest.mark.parametrize("ruta", EXITOSAS, ids=lambda r: r.name)
def test_los_pasos_y_la_ficha_se_reconocen(ruta):
    lineas = _lineas(ruta)
    pasos = [p for p in (sp.subpaso_de_linea(ln) for ln in lineas) if p]
    assert pasos == ["screenshots", "torrent", "tracker"]

    resultado = classify_upload_result("\n".join(lineas), "", 0)
    assert resultado["status"] == "completed"
    assert resultado["result_url"] and "/torrents/" in resultado["result_url"]
    assert resultado["torrent_id"] and resultado["torrent_id"].isdigit()
    assert resultado["result_url"].endswith(f"/torrents/{resultado['torrent_id']}")


@pytest.mark.parametrize("ruta", EXITOSAS, ids=lambda r: r.name)
def test_se_ve_que_qbittorrent_lo_añadio(ruta):
    assert any(ln.strip().startswith("Added to:") for ln in _lineas(ruta))


@pytest.mark.parametrize("ruta", COPIAS, ids=lambda r: r.name)
def test_la_copia_dorada_es_la_de_uploadrr_emu(ruta):
    """La copia es la misma que Uploadrr-EMU genera y guarda en su `tests/golden/`."""
    original = UPLOADRR_EMU / "tests" / "golden" / ruta.name
    assert ruta.read_bytes().replace(b"\r\n", b"\n") == original.read_bytes().replace(b"\r\n", b"\n"), (
        f"{ruta.name} difiere de {original}: copia la de Uploadrr-EMU (la suya manda)")


def test_el_contrato_de_eventos_es_el_de_uploadrr_emu():
    """`tests/fixtures/contrato_eventos.json` es copia de `contrato_eventos.json` de Uploadrr-EMU (la suya manda)."""
    original = UPLOADRR_EMU / "contrato_eventos.json"
    copia = CARPETA.parent / "contrato_eventos.json"
    assert copia.read_bytes().replace(b"\r\n", b"\n") == original.read_bytes().replace(b"\r\n", b"\n"), (
        f"{copia.name} difiere de {original}: copia la de Uploadrr-EMU")
