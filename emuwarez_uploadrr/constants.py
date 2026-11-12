"""Constantes de la aplicación.

No importa nada de la aplicación a propósito: es la hoja del árbol de
dependencias, y cualquier import que se le añada puede crear un ciclo.
"""
from __future__ import annotations

import re
from pathlib import Path


APP_NAME = "emuwarez-renamer"
CONFIG_DIR = Path.home() / ".config" / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
CONFIG_ENC_FILE = CONFIG_DIR / "config.enc.json"
LOG_DIR = CONFIG_DIR / "logs"

EMU_BASE_URL = "https://emu-warez.com"

# La tabla de idiomas vive en services/emu_format.py (resolve_language): era
# la segunda copia, y la que contaba un es-MX como castellano.
SOURCE_PATTERNS = [
    (re.compile(r"\b(?:bd[ ._-]?remux|bluray[ ._-]?remux)\b", re.I), "BDRemux"),
    # "Full BluRay" y no "FBD": es el código que usa la aplicación
    # (source_assistant.EMU_SOURCES). El motor de Source sí habla en FBD, pero
    # traduce al salir (source_engine/integration.ENGINE_TO_APP_SOURCE); esta
    # detección por nombre no traducía nada, así que un fichero con "bd50" en
    # el nombre acababa con un source que el tracker no acepta.
    # "Full.BluRay" es como lo escribe la propia app al renombrar.
    (re.compile(r"\b(?:full[ ._-]?bd|full[ ._-]?blu[ ._-]?ray|bd50|bd25)\b", re.I), "Full BluRay"),
    (re.compile(r"\b(?:full[ ._-]?dvd|dvd9|dvd5)\b", re.I), "FDVD"),
    (re.compile(r"\b(?:web[ ._-]?dl|webdl)\b", re.I), "WEB-DL"),
    (re.compile(r"\b(?:web[ ._-]?rip|webrip)\b", re.I), "WEBRip"),
    (re.compile(r"\b(?:hdtv)\b", re.I), "HDTV"),
    (re.compile(r"\b(?:micro[ ._-]?hd|mhd)\b", re.I), "MHD"),
    (re.compile(r"\b(?:blu[ ._-]?ray|bdrip|brrip|bdr)\b", re.I), "BluRay"),
    (re.compile(r"\b(?:dvd[ ._-]?rip|dvdrip|dvd)\b", re.I), "SD"),
]
