"""Los ids de UNIT3D de un tracker, de `data/trackers/<TRACKER>.json` (la app lee el mismo fichero).

Solo stdlib: la app lo carga por ruta en sus tests. Si falta o está mal, TablaIdsError: nunca un "0" silencioso.
"""

import json
from pathlib import Path
from typing import Any, Callable, Dict, List

CARPETA = Path(__file__).resolve().parents[2] / "data" / "trackers"
SECCIONES = ("categories", "types", "resolutions")


class TablaIdsError(RuntimeError):
    pass


def ruta_ids(tracker: str) -> Path:
    return CARPETA / f"{tracker}.json"


def _es_id(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _validar(tabla: Any) -> None:
    if tabla.get("version") != 1:
        raise ValueError(f"versión {tabla.get('version')!r} desconocida")
    for seccion in SECCIONES:
        tokens: List[str] = []
        for entrada in tabla[seccion]:
            if not _es_id(entrada["id"]) or not all(isinstance(t, str) and t for t in entrada["tokens"]):
                raise ValueError(f"entrada mal formada en {seccion}: {entrada!r}")
            tokens += entrada["tokens"]
        if len(tokens) != len(set(tokens)):
            raise ValueError(f"token repetido en {seccion}")
        if not _es_id(tabla["defaults"][seccion]):
            raise ValueError(f"defaults.{seccion} no es un id")
    if not all(_es_id(i) for grupo in tabla["search"]["resolution_groups"] for i in grupo):
        raise ValueError("search.resolution_groups mal formado")


def cargar_ids(tracker: str) -> Dict[str, Any]:
    ruta = ruta_ids(tracker)
    try:
        with ruta.open(encoding="utf-8") as f:
            tabla = json.load(f)
        _validar(tabla)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise TablaIdsError(f"No se puede leer la tabla de ids de {tracker} ({ruta}): {e}") from e
    return tabla


def ids_por_token(tabla: Dict[str, Any], seccion: str, clave: Callable[[str], str]) -> Dict[str, str]:
    """token normalizado con `clave` (como llega en meta) -> id en texto, como lo envía el formulario."""
    return {clave(t): str(e["id"]) for e in tabla[seccion] for t in e["tokens"]}
