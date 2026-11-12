"""Transcripciones doradas: la salida real de una subida entera de Uploadrr-EMU, reproducida por los parsers de la app.

Cada `tests/fixtures/golden_uploadrr/*.txt` es el stdout capturado de Uploadrr-EMU contra su tracker falso. Las líneas
pasan por `parse_uploadrr_progress`, `subpaso_de_linea` y `classify_upload_result`, igual que en `upload_execution`.
Si Uploadrr-EMU cambia una línea, la copia dorada se regenera allí y este test dice qué patrón de la app deja de verla.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from emuwarez_uploadrr.services import subida_pasos as sp
from emuwarez_uploadrr.services.upload_result_parser import classify_upload_result
from emuwarez_uploadrr.services.uploadrr_progress import PHASE_KEY_ALIASES, RUNNING_DISPLAY_ORDER, parse_uploadrr_progress

CARPETA = Path(__file__).parent / "fixtures" / "golden_uploadrr"
TRANSCRIPCIONES = sorted(CARPETA.glob("*.txt"))


def _lineas(ruta: Path) -> list[str]:
    return ruta.read_text(encoding="utf-8").splitlines()


@pytest.mark.skipif(bool(TRANSCRIPCIONES), reason="ya hay transcripciones")
def test_hay_transcripciones_doradas():
    pytest.skip("tests/fixtures/golden_uploadrr/ no tiene ningún .txt todavía (la transcripción real de Uploadrr-EMU)")


@pytest.mark.parametrize("ruta", TRANSCRIPCIONES, ids=lambda r: r.name)
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


@pytest.mark.parametrize("ruta", TRANSCRIPCIONES, ids=lambda r: r.name)
def test_los_pasos_y_la_ficha_se_reconocen(ruta):
    lineas = _lineas(ruta)
    pasos = [p for p in (sp.subpaso_de_linea(ln) for ln in lineas) if p]
    assert pasos == ["screenshots", "torrent", "tracker"]

    resultado = classify_upload_result("\n".join(lineas), "", 0)
    assert resultado["status"] == "completed"
    assert resultado["result_url"] and "/torrents/" in resultado["result_url"]
    assert resultado["torrent_id"] and resultado["torrent_id"].isdigit()
    assert resultado["result_url"].endswith(f"/torrents/{resultado['torrent_id']}")


@pytest.mark.parametrize("ruta", TRANSCRIPCIONES, ids=lambda r: r.name)
def test_se_ve_que_qbittorrent_lo_añadio(ruta):
    assert any(ln.strip().startswith("Added to:") for ln in _lineas(ruta))


@pytest.mark.parametrize("ruta", TRANSCRIPCIONES, ids=lambda r: r.name)
def test_la_copia_dorada_es_la_de_uploadrr_emu(ruta):
    """La copia es la misma que Uploadrr-EMU genera y guarda en su `tests/`; sin su repo a mano se salta."""
    for candidata in (os.environ.get("UPLOADRR_EMU_DIR"), "/opt/Uploadrr"):
        if candidata and (Path(candidata) / "tests").is_dir():
            originales = list((Path(candidata) / "tests").rglob(ruta.name))
            break
    else:
        pytest.skip("Sin UPLOADRR_EMU_DIR ni /opt/Uploadrr no se puede comparar con la transcripción de Uploadrr-EMU")
    if not originales:
        pytest.skip(f"Uploadrr-EMU no tiene tests/**/{ruta.name} todavía")
    original = originales[0].read_bytes().replace(b"\r\n", b"\n")
    assert ruta.read_bytes().replace(b"\r\n", b"\n") == original, (
        f"{ruta.name} difiere de {originales[0]}: copia la de Uploadrr-EMU (la suya manda)")
