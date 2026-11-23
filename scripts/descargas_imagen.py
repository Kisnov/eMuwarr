#!/usr/bin/env python3
"""Suma las descargas de todas las versiones publicadas (X.Y.Z) de ghcr.io/kisnov/emuwarr.

GHCR cuenta las descargas por versión (digest), no por etiqueta, y su API no las da: salen de la página de versiones
del paquete, la que ve cualquiera en GitHub. El «Total downloads» del paquete suma también cada :dev, cada :pr-<n> y
las versiones sin etiqueta, y puede bajar cuando la limpieza semanal las borra; las X.Y.Z no se borran nunca, así que esta
suma solo crece. Cada una cuenta los `docker pull` por cualquiera de sus etiquetas (X.Y.Z, latest, su sha).

    python3 scripts/descargas_imagen.py                         # enseña el JSON
    python3 scripts/descargas_imagen.py --salida descargas.json

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
MAX_PAGINAS = 50

# Cada etiqueta de una versión enlaza a /users/Kisnov/packages/container/emuwarr/<id>?tag=<etiqueta>.
_ENLACE = re.compile(r"/container/emuwarr/(\d+)\?tag=([^\"'&\s<>]+)")
_DESCARGAS = re.compile(r"(\d[\d.,]*)\s*([kKmM]?)\s+Version\s+downloads")
_MARCA = re.compile(r"<[^>]+>")
_PUBLICADA = re.compile(r"\d+\.\d+\.\d+")


def _numero(cifra: str, sufijo: str) -> int:
    if sufijo:
        return round(float(cifra.replace(",", "")) * (1000 if sufijo.lower() == "k" else 1_000_000))
    return int(cifra.replace(",", "").replace(".", ""))


def filas(pagina: str) -> list[tuple[str, list[str], int]]:
    """(id, etiquetas, descargas) de cada versión de la página, en orden.

    La fila de una versión va desde su primer enlace hasta el de la versión siguiente, y la cifra se busca solo ahí:
    si una fila no la tiene, es un error (la página ha cambiado), nunca la cifra de la fila de al lado.
    """
    enlaces = list(_ENLACE.finditer(pagina))
    inicios: dict[str, int] = {}
    etiquetas: dict[str, list[str]] = {}
    for e in enlaces:
        inicios.setdefault(e.group(1), e.start())
        etiquetas.setdefault(e.group(1), [])
        etiqueta = html.unescape(e.group(2))
        if etiqueta not in etiquetas[e.group(1)]:
            etiquetas[e.group(1)].append(etiqueta)
    orden = sorted(inicios, key=inicios.get)
    resultado = []
    for i, version in enumerate(orden):
        hasta = inicios[orden[i + 1]] if i + 1 < len(orden) else len(pagina)
        texto = html.unescape(_MARCA.sub(" ", pagina[inicios[version]:hasta]))
        cifra = _DESCARGAS.search(texto)
        if cifra is None:
            raise ValueError(f"la fila de la versión {version} ({', '.join(etiquetas[version])}) no dice sus descargas")
        resultado.append((version, etiquetas[version], _numero(cifra.group(1), cifra.group(2))))
    return resultado


def leer(url: str) -> str:
    peticion = urllib.request.Request(url, headers={"User-Agent": "emuwarr-insignias"})
    with urllib.request.urlopen(peticion, timeout=30) as respuesta:  # noqa: S310 (URL fija de github.com)
        return respuesta.read().decode("utf-8", "replace")


def publicadas(lector=leer) -> dict[str, tuple[list[str], int]]:
    """{id: (etiquetas, descargas)} de las versiones con una etiqueta X.Y.Z, recorriendo todas las páginas."""
    versiones: dict[str, tuple[list[str], int]] = {}
    for n in range(1, MAX_PAGINAS + 1):
        pagina = filas(lector(PAGINA.format(n)))
        if not pagina:
            break
        for version, etiquetas, descargas in pagina:
            if any(_PUBLICADA.fullmatch(e) for e in etiquetas):
                versiones[version] = (etiquetas, descargas)
    else:
        raise LookupError(f"la página de versiones tiene más de {MAX_PAGINAS} páginas: la suma saldría corta")
    if not versiones:
        raise LookupError("ninguna versión etiquetada lleva un número X.Y.Z")
    return versiones


def insignia(descargas: int) -> dict:
    return {
        "schemaVersion": 1,
        "label": "descargas",
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
        versiones = publicadas(lector)
    except (LookupError, ValueError, OSError) as exc:
        print(f"No se pudieron contar las descargas: {exc}", file=sys.stderr)
        return 1
    for etiquetas, descargas in versiones.values():
        print(f"  {', '.join(etiquetas)}: {descargas}", file=sys.stderr)
    total = sum(descargas for _, descargas in versiones.values())
    print(f"{len(versiones)} versiones publicadas: {total} descargas", file=sys.stderr)
    datos = json.dumps(insignia(total), ensure_ascii=False) + "\n"
    if args.salida:
        args.salida.write_text(datos, encoding="utf-8")
    else:
        sys.stdout.write(datos)
    return 0


if __name__ == "__main__":
    sys.exit(main())
