"""El cliente de la API de UNIT3D que comparten la app y Uploadrr-EMU (integrations/cliente_unit3d.py)."""
from __future__ import annotations

import asyncio
import logging
import urllib.parse
from pathlib import Path

import httpx
import pytest

from emuwarez_uploadrr.integrations import cliente_unit3d as cu

RAIZ = Path(__file__).resolve().parents[1]
COPIA_UPLOADRR = RAIZ / "uploadrr-emu" / "src" / "utils" / "cliente_unit3d.py"
TRACKER = cu.Tracker(base_url="https://emu.test/", api_key=" CLAVE+/1 \n", user_agent=cu.user_agent("1.2.3"))


def _consulta(url: str) -> list[tuple[str, str]]:
    return urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, keep_blank_values=True)


def test_la_copia_de_uploadrr_es_identica():
    """Uploadrr-EMU no importa la app: lleva el mismo módulo, byte a byte."""
    assert COPIA_UPLOADRR.read_bytes() == Path(cu.__file__).read_bytes()


def test_los_registros_de_httpx_no_apuntan_las_urls():
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
    assert not logging.getLogger("httpcore").isEnabledFor(logging.INFO)


# --- Núcleo -----------------------------------------------------------------

def test_una_ruta_lleva_la_clave_la_primera_y_el_user_agent():
    url, cabeceras = cu.peticion(TRACKER, "/api/torrents/filter",
                                 {"perPage": 1, "categories[]": [1, 2], "name": "", "uploader": None})
    assert url.startswith("https://emu.test/api/torrents/filter?api_token=CLAVE%2B%2F1&")
    assert _consulta(url) == [("api_token", "CLAVE+/1"), ("perPage", "1"), ("categories[]", "1"),
                              ("categories[]", "2"), ("name", "")]
    assert cabeceras == {"User-Agent": "emuwarr/1.2.3"}


def test_un_enlace_del_tracker_conserva_su_consulta_y_cambia_la_clave():
    url, _ = cu.peticion(TRACKER, "https://emu.test/api/torrents/filter?page=2&api_token=VIEJA&tmdbId=5")
    assert _consulta(url) == [("api_token", "CLAVE+/1"), ("page", "2"), ("tmdbId", "5")]
    url, _ = cu.peticion(TRACKER, "https://EMU.test:443/torrent/download/7.abc")
    assert url == "https://EMU.test:443/torrent/download/7.abc?api_token=CLAVE%2B%2F1"


@pytest.mark.parametrize("enlace", [
    "https://otro.test/torrent/download/7", "http://emu.test/torrent/download/7",
    "https://emu.test:8443/torrent/download/7", "https://emu.test.otro.test/x",
])
def test_la_clave_no_va_a_otro_origen(enlace):
    with pytest.raises(cu.OtroOrigen):
        cu.peticion(TRACKER, enlace)


@pytest.mark.parametrize("enlace", ["https://[::1/x", "https://emu.test:99999/x"])
def test_un_enlace_que_no_es_una_url_no_se_pide(enlace):
    with pytest.raises(ValueError):
        cu.peticion(TRACKER, enlace)


@pytest.mark.parametrize("cuerpo", [
    {"data": {"attributes": {"seeders": 3}}}, {"attributes": {"seeders": 3}}, {"seeders": 3},
])
def test_los_atributos_del_torrent_con_o_sin_envoltorio(cuerpo):
    assert cu.atributos_torrent(cuerpo) == {"seeders": 3}


@pytest.mark.parametrize("cuerpo", [[], "x", None, {"data": []}])
def test_un_torrent_que_no_es_un_objeto(cuerpo):
    with pytest.raises(ValueError):
        cu.atributos_torrent(cuerpo)


# --- Fachadas ---------------------------------------------------------------

class ErrorHTTP(Exception):
    def __init__(self, status, retry_after=None):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


def _cliente_sync(resultado):
    pasos = []

    def enviar(metodo, url, cabeceras, **opciones):
        pasos.append(("enviar", metodo, url, cabeceras, opciones))
        if isinstance(resultado, Exception):
            raise resultado
        return resultado

    cliente = cu.Cliente(TRACKER, enviar, turno=lambda p: pasos.append(("turno", p)),
                         apuntar_429=lambda ra: pasos.append(("429", ra)))
    return cliente, pasos


def test_el_cliente_pide_turno_antes_de_cada_peticion():
    cliente, pasos = _cliente_sync({"data": []})
    assert cliente.pedir("GET", "/api/user", prioridad="recorrido", cabeceras={"Accept": "application/json"},
                         timeout=20) == {"data": []}
    url, _ = cu.peticion(TRACKER, "/api/user")
    assert pasos == [("turno", "recorrido"),
                     ("enviar", "GET", url, {"User-Agent": "emuwarr/1.2.3", "Accept": "application/json"},
                      {"timeout": 20})]


def test_el_cliente_apunta_el_429_con_su_retry_after_y_lo_relanza():
    error = ErrorHTTP(429, "12")
    cliente, pasos = _cliente_sync(error)
    with pytest.raises(ErrorHTTP) as info:
        cliente.pedir("GET", "/api/user")
    assert info.value is error
    assert pasos[0] == ("turno", "interactiva") and pasos[-1] == ("429", "12")


@pytest.mark.parametrize("error", [ErrorHTTP(500), ErrorHTTP(403), OSError("sin red")])
def test_otros_errores_no_pausan_a_nadie(error):
    cliente, pasos = _cliente_sync(error)
    with pytest.raises(type(error)):
        cliente.pedir("GET", "/api/user")
    assert [p[0] for p in pasos] == ["turno", "enviar"]


def test_el_cliente_no_pide_un_enlace_de_otro_origen_ni_gasta_turno():
    cliente, pasos = _cliente_sync({})
    with pytest.raises(cu.OtroOrigen):
        cliente.pedir("GET", "https://otro.test/x")
    assert pasos == []


def test_el_cliente_asincrono_pide_turno_y_apunta_el_429():
    pasos = []
    vistas = []

    def responder(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return httpx.Response(429 if request.method == "GET" else 200, headers={"Retry-After": "3"})

    async def turno(prioridad):
        pasos.append(("turno", prioridad))

    async def probar():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as http:
            cliente = cu.ClienteAsincrono(TRACKER, http, turno=turno, apuntar_429=lambda ra: pasos.append(("429", ra)))
            await cliente.pedir("GET", "/api/torrents/filter", {"tmdbId": 5})
            await cliente.pedir("POST", "/api/torrents/upload", prioridad="interactiva", data={"name": "X"},
                                files={"torrent": ("t.torrent", b"d4:infoe", "application/x-bittorrent")})

    asyncio.run(probar())
    assert pasos == [("turno", "subida"), ("429", "3"), ("turno", "interactiva")]
    get, post = vistas
    assert _consulta(str(get.url)) == [("api_token", "CLAVE+/1"), ("tmdbId", "5")]
    assert get.headers["User-Agent"] == post.headers["User-Agent"] == "emuwarr/1.2.3"
    assert b'name="name"' in post.content and b"d4:infoe" in post.content
    assert _consulta(str(post.url)) == [("api_token", "CLAVE+/1")]
