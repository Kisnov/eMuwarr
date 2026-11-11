"""/assets se sirve precomprimido: el build del frontend deja un .br y un .gz junto a cada JS, CSS y SVG, y el servidor
manda el que el navegador admite en vez de comprimir en cada petición.

Las peticiones van directas al ASGI (sin httpx) para ver los bytes tal como salen, sin que el cliente los descomprima.
"""
from __future__ import annotations

import asyncio
import gzip
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.middleware.gzip import GZipMiddleware
from starlette.routing import Mount

from emuwarez_uploadrr.web.app import CACHE_DE_ASSETS, _AssetsInmutables, _codificaciones_aceptadas

RAIZ = Path(__file__).resolve().parent.parent
JS = ("console.log('hola');\n" * 200).encode()  # pasa de 1 kB: el GZipMiddleware lo comprimiría
BR = b"BR:" + b"\x01" * 40  # no hace falta que sea brotli válido: el servidor no lo abre
GZ = gzip.compress(JS)
FUENTE = b"wOF2" + b"\x07" * 3000  # una fuente: sin variantes


@pytest.fixture
def assets(tmp_path):
    (tmp_path / "app-abc123.js").write_bytes(JS)
    (tmp_path / "app-abc123.js.br").write_bytes(BR)
    (tmp_path / "app-abc123.js.gz").write_bytes(GZ)
    (tmp_path / "solo-gz-abc123.js").write_bytes(JS)
    (tmp_path / "solo-gz-abc123.js.gz").write_bytes(GZ)
    (tmp_path / "fuente-abc123.woff2").write_bytes(FUENTE)
    return tmp_path


def _app(directorio) -> Starlette:
    # Como create_app: el montaje de /assets dentro del GZipMiddleware.
    app = Starlette(routes=[Mount("/assets", app=_AssetsInmutables(directory=str(directorio)), name="assets")])
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
    return app


def pedir(directorio, nombre: str, **cabeceras: str) -> tuple[int, dict[str, str], bytes]:
    """GET /assets/<nombre> al ASGI: (estado, cabeceras en minúsculas, cuerpo tal como sale)."""
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": f"/assets/{nombre}", "raw_path": f"/assets/{nombre}".encode(), "query_string": b"", "root_path": "",
        "headers": [(k.lower().replace("_", "-").encode(), v.encode()) for k, v in cabeceras.items()],
        "client": ("127.0.0.1", 1), "server": ("testserver", 80),
    }
    salida: dict = {"cuerpo": b""}

    pedida = False

    async def recibir():
        # Como un servidor real: la petición una vez y después nada hasta que el cliente se vaya (FileResponse
        # espera aquí mientras envía el fichero y se cancela al acabar).
        nonlocal pedida
        if not pedida:
            pedida = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()

    async def enviar(mensaje):
        if mensaje["type"] == "http.response.start":
            salida["estado"] = mensaje["status"]
            salida["cabeceras"] = {k.decode().lower(): v.decode() for k, v in mensaje["headers"]}
        elif mensaje["type"] == "http.response.body":
            salida["cuerpo"] += mensaje.get("body", b"")

    asyncio.run(_app(directorio)(scope, recibir, enviar))
    return salida["estado"], salida["cabeceras"], salida["cuerpo"]


def test_con_brotli_admitido_manda_el_br_tal_cual(assets):
    estado, cab, cuerpo = pedir(assets, "app-abc123.js", accept_encoding="gzip, deflate, br")
    assert estado == 200
    assert cuerpo == BR
    assert cab["content-encoding"] == "br"
    assert cab["content-type"].startswith(("text/javascript", "application/javascript"))
    assert cab["vary"] == "Accept-Encoding"
    assert cab["cache-control"] == CACHE_DE_ASSETS
    assert int(cab["content-length"]) == len(BR)


def test_sin_brotli_manda_el_gzip_ya_hecho_y_el_middleware_no_lo_comprime_otra_vez(assets):
    estado, cab, cuerpo = pedir(assets, "app-abc123.js", accept_encoding="gzip, deflate")
    assert estado == 200
    assert cuerpo == GZ
    assert cab["content-encoding"] == "gzip"
    assert gzip.decompress(cuerpo) == JS


def test_una_codificacion_con_q_cero_no_se_usa(assets):
    _, cab, cuerpo = pedir(assets, "app-abc123.js", accept_encoding="br;q=0, gzip;q=0.5")
    assert cab["content-encoding"] == "gzip"
    assert cuerpo == GZ


def test_si_solo_hay_gzip_se_usa_aunque_se_prefiera_brotli(assets):
    _, cab, cuerpo = pedir(assets, "solo-gz-abc123.js", accept_encoding="br, gzip")
    assert cab["content-encoding"] == "gzip"
    assert cuerpo == GZ


def test_sin_accept_encoding_va_el_original_y_la_respuesta_avisa_de_que_varia(assets):
    estado, cab, cuerpo = pedir(assets, "app-abc123.js")
    assert estado == 200
    assert cuerpo == JS
    assert "content-encoding" not in cab
    # (El GZipMiddleware añade el suyo a las respuestas que no comprime: puede salir repetido, y da igual.)
    assert "Accept-Encoding" in cab["vary"]
    assert cab["cache-control"] == CACHE_DE_ASSETS


def test_lo_que_no_tiene_variantes_se_sirve_como_antes(assets):
    estado, cab, cuerpo = pedir(assets, "fuente-abc123.woff2")
    assert estado == 200
    assert cuerpo == FUENTE
    assert "content-encoding" not in cab
    assert "vary" not in cab
    assert cab["cache-control"] == CACHE_DE_ASSETS


def test_el_etag_es_el_de_cada_variante_y_if_none_match_da_304(assets):
    _, cab_br, _ = pedir(assets, "app-abc123.js", accept_encoding="br")
    _, cab_gz, _ = pedir(assets, "app-abc123.js", accept_encoding="gzip")
    assert cab_br["etag"] != cab_gz["etag"]

    estado, cab, cuerpo = pedir(assets, "app-abc123.js", accept_encoding="br", if_none_match=cab_br["etag"])
    assert estado == 304
    assert cuerpo == b""
    assert cab["vary"] == "Accept-Encoding"
    assert cab["cache-control"] == CACHE_DE_ASSETS
    # La etiqueta del br no vale para el gzip: otra representación.
    assert pedir(assets, "app-abc123.js", accept_encoding="gzip", if_none_match=cab_br["etag"])[0] == 200


@pytest.mark.parametrize("valor, esperado", [
    ("", set()),
    ("gzip, deflate, br", {"gzip", "deflate", "br"}),
    ("BR;q=1.0, gzip;q=0.8", {"br", "gzip"}),
    ("br;q=0, gzip", {"gzip"}),
    ("gzip;q=abc", set()),
    ("identity", {"identity"}),
])
def test_codificaciones_aceptadas(valor, esperado):
    assert _codificaciones_aceptadas(valor) == esperado


def test_el_build_del_frontend_escribe_los_mismos_sufijos_que_busca_el_servidor():
    """frontend/vite.config.ts y web/app.py acuerdan los nombres (.br, .gz) y qué se precomprime: sin la mitad del
    contrato, el servidor serviría el original (nunca algo roto), pero el ahorro se perdería sin que nada avise."""
    vite = (RAIZ / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    assert "precomprimirAssets()" in vite.split("plugins:")[1].split("],")[0]
    assert "['.br'," in vite and "['.gz'," in vite
    assert "(js|css|svg)" in vite
