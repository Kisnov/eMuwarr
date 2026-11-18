"""El cliente de la API de UNIT3D (src/utils/cliente_unit3d.py).

Es una copia exacta del de la app (emuwarez_uploadrr/integrations/cliente_unit3d.py), cuyos tests lo cubren; aquí,
que sigue siendo Python 3.9 y que se importa desde Uploadrr-EMU."""

import ast
from pathlib import Path

from src.utils import cliente_unit3d


def test_es_python_3_9():
    ast.parse(Path(cliente_unit3d.__file__).read_text(encoding="utf-8"), feature_version=(3, 9))


def test_monta_la_peticion_con_la_clave():
    tracker = cliente_unit3d.Tracker("https://emu.test", "CLAVE", cliente_unit3d.user_agent("1.0"))
    assert cliente_unit3d.peticion(tracker, "/api/torrents/1") == (
        "https://emu.test/api/torrents/1", {"User-Agent": "emuwarr/1.0", "Authorization": "Bearer CLAVE"})
