import asyncio
import logging

from src.screenshots.imghosts.chevereto import CheveretoTemplate
from src.utils.console import log

CLAVE = "clave-secreta-de-imgbb"


class _Respuesta:
    status_code = 200
    reason_phrase = "OK"
    headers = {"content-type": "application/json"}

    async def aread(self):
        return b"{}"

    def json(self):
        return {"status_code": 200, "data": {"url": "https://i.example/x.png"}}


class _Cliente:
    async def post(self, url, headers=None, data=None):
        return _Respuesta()


class _Registro(logging.Handler):
    def __init__(self):
        super().__init__(level=0)
        self.lineas = []

    def emit(self, record):
        self.lineas.append(record.getMessage())


def test_la_clave_del_host_no_sale_en_el_log(tmp_path):
    """El logger no propaga y el redactor solo tapa las claves que conoce: la clave no se escribe en el mensaje."""
    imagen = tmp_path / "captura.png"
    imagen.write_bytes(b"png")
    host = CheveretoTemplate({"SCREENS": {"API_KEYS": {"imgbb": CLAVE}}}, "https://imgbb.example/api", "imgbb")
    registro, nivel = _Registro(), log.level
    log.addHandler(registro)
    log.setLevel(logging.DDEBUG)
    try:
        asyncio.run(host.upload(_Cliente(), str(imagen)))
    finally:
        log.removeHandler(registro)
        log.setLevel(nivel)
    assert any("Using API key for imgbb" in linea for linea in registro.lineas)
    assert not any(CLAVE in linea for linea in registro.lineas)
