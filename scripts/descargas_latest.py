#!/usr/bin/env python3
"""Cuenta las descargas de la versión que lleva :latest en ghcr.io/kisnov/emuwarr.

GHCR cuenta las descargas por versión (digest), no por etiqueta, y su API no las da: salen de la página de versiones
del paquete, la que ve cualquiera en GitHub. La versión de :latest también lleva su X.Y.Z y su sha, así que suma los
`docker pull` de las tres etiquetas, y vuelve a empezar de cero con cada versión publicada.

    python3 scripts/descargas_latest.py                         # enseña el JSON
    python3 scripts/descargas_latest.py --salida descargas.json

El JSON es el de una insignia «endpoint» de shields.io; el workflow descargas.yml lo deja en la rama insignias.
"""
from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import sys
import urllib.request

PAGINA = "https://github.com/Kisnov/eMuwarr/pkgs/container/emuwarr/versions?filters%5Bversion_type%5D=tagged&page={}"
MAX_PAGINAS = 20
ETIQUETA = "latest"

# Cada etiqueta de una versión enlaza a /users/Kisnov/packages/container/emuwarr/<id>?tag=<etiqueta>.
_ENLACE = re.compile(r"/container/emuwarr/(\d+)\?tag=([^\"'&\s<>]+)")
_DESCARGAS = re.compile(r"(\d[\d.,]*)\s*([kKmM]?)\s+Version\s+downloads")
_MARCA = re.compile(r"<[^>]+>")


def _numero(cifra: str, sufijo: str) -> int:
    if sufijo:
        return round(float(cifra.replace(",", "")) * (1000 if sufijo.lower() == "k" else 1_000_000))
    return int(cifra.replace(",", "").replace(".", ""))


def descargas_de(pagina: str, etiqueta: str = ETIQUETA) -> tuple[str, int, list[str]] | None:
    """(id de la versión, descargas, sus etiquetas) de la versión con `etiqueta`, o None si no está en esta página.

    La cifra se busca solo dentro de la fila de esa versión: desde su enlace hasta el de la versión siguiente. Si la
    fila no la tiene, es un error (la página ha cambiado), no la cifra de la fila de al lado.
    """
    enlaces = list(_ENLACE.finditer(pagina))
    suyo = next((e for e in enlaces if html.unescape(e.group(2)) == etiqueta), None)
    if suyo is None:
        return None
    version = suyo.group(1)
    desde = min(e.start() for e in enlaces if e.group(1) == version)
    hasta = next((e.start() for e in enlaces if e.start() > desde and e.group(1) != version), len(pagina))
    texto = html.unescape(_MARCA.sub(" ", pagina[desde:hasta]))
    cifra = _DESCARGAS.search(texto)
    if cifra is None:
        raise ValueError(f"la fila de :{etiqueta} (versión {version}) no dice cuántas descargas tiene")
    etiquetas = list(dict.fromkeys(html.unescape(e.group(2)) for e in enlaces if e.group(1) == version))
    return version, _numero(cifra.group(1), cifra.group(2)), etiquetas


def leer(url: str) -> str:
    peticion = urllib.request.Request(url, headers={"User-Agent": "emuwarr-insignias"})
    with urllib.request.urlopen(peticion, timeout=30) as respuesta:  # noqa: S310 (URL fija de github.com)
        return respuesta.read().decode("utf-8", "replace")


def buscar(lector=leer) -> tuple[str, int, list[str]]:
    for n in range(1, MAX_PAGINAS + 1):
        pagina = lector(PAGINA.format(n))
        encontrada = descargas_de(pagina)
        if encontrada:
            return encontrada
        if not _ENLACE.search(pagina):
            break
    raise LookupError(f"ninguna versión etiquetada lleva :{ETIQUETA}")


def insignia(descargas: int) -> dict:
    return {
        "schemaVersion": 1,
        "label": f"descargas :{ETIQUETA}",
        "message": f"{descargas:,}".replace(",", "."),
        "color": "2496ED",
        "namedLogo": "docker",
        "logoColor": "white",
    }


def main(argv: list[str] | None = None, lector=leer) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--salida", type=pathlib.Path, help="Escribe aquí el JSON de la insignia")
    args = parser.parse_args(argv)
    try:
        version, descargas, etiquetas = buscar(lector)
    except (LookupError, ValueError, OSError) as exc:
        print(f"No se pudieron contar las descargas: {exc}", file=sys.stderr)
        return 1
    print(f"Versión {version} ({', '.join(etiquetas)}): {descargas} descargas", file=sys.stderr)
    datos = json.dumps(insignia(descargas), ensure_ascii=False) + "\n"
    if args.salida:
        args.salida.write_text(datos, encoding="utf-8")
    else:
        sys.stdout.write(datos)
    return 0


if __name__ == "__main__":
    sys.exit(main())
