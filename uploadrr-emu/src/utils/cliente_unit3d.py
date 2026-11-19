"""Cliente de la API de un tracker UNIT3D (eMuwarez), el mismo para la app y para Uploadrr-EMU.

El núcleo es puro: `peticion` monta la URL y las cabeceras de cada llamada (la clave, el User-Agent) y solo deja que
la clave vaya a un enlace del propio tracker; `atributos_torrent` lee /api/torrents/{id}, con o sin el envoltorio
`data`. Encima, dos fachadas que hacen lo mismo con él: `Cliente` (síncrono, la app: envía con la función que se le
pasa) y `ClienteAsincrono` (Uploadrr-EMU, sobre un httpx.AsyncClient). Las dos piden turno al limitador antes de cada
petición y le apuntan cada 429. Ninguna lanza por el código HTTP ni reintenta, salvo una vez por la clave: eso lo
decide quien llama.

La clave va en `Authorization: Bearer` (la próxima versión de UNIT3D solo acepta esa). Si el tracker responde 401 o 403,
la petición se repite una sola vez con la clave en la URL (?api_token=), que es como iba antes. Nunca se registra una
URL entera: por eso los registros de httpx, que apuntan cada petición en INFO con su URL, se quedan en WARNING en cuanto
se importa este módulo.

Este fichero existe dos veces, idéntico: emuwarez_uploadrr/integrations/cliente_unit3d.py (la app) y
uploadrr-emu/src/utils/cliente_unit3d.py (Uploadrr-EMU, que no importa la app). Solo stdlib (en la app nada más
importa httpx que http_cliente) y Python 3.9.
"""
from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Tuple

PARAMETRO_CLAVE = "api_token"
RECHAZOS_DE_CLAVE = (401, 403)  # con ellos se prueba otra vez con la clave en la URL
_PUERTOS = {"http": 80, "https": 443}

for _nombre in ("httpx", "httpcore"):
    logging.getLogger(_nombre).setLevel(logging.WARNING)


class OtroOrigen(ValueError):
    """Un enlace que no es del tracker: la clave no va."""


@dataclass(frozen=True)
class Tracker:
    base_url: str  # "https://emu-warez.com"
    api_key: str
    user_agent: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        object.__setattr__(self, "api_key", (self.api_key or "").strip())


def user_agent(version: str) -> str:
    return f"emuwarr/{version}"


def _origen(url: Any) -> Tuple[str, str, Optional[int]]:
    partes = urllib.parse.urlsplit(str(url))
    esquema = partes.scheme.lower()
    return esquema, partes.hostname or "", partes.port or _PUERTOS.get(esquema)


def mismo_origen(a: Any, b: Any) -> bool:
    """Mismo esquema, nombre y puerto (el del esquema cuenta igual escrito que sin escribir). ValueError si alguna no
    es una URL."""
    return _origen(a) == _origen(b)


def peticion(tracker: Tracker, ruta: str, params: Optional[Mapping[str, Any]] = None, *,
             en_la_url: bool = False) -> Tuple[str, Dict[str, str]]:
    """(URL, cabeceras) de una llamada.

    `ruta` es una ruta de la API ("/api/torrents/filter") o un enlace que ha dado el propio tracker (la página
    siguiente, la descarga del .torrent): si no es de su mismo origen, OtroOrigen. Un valor lista repite el parámetro
    (`categories[]=1&categories[]=2`) y uno None no va. La clave que trajera el enlace se quita; la nuestra va en
    `Authorization: Bearer` o, con `en_la_url`, la primera de la consulta."""
    if "://" in ruta:
        if not mismo_origen(ruta, tracker.base_url):
            raise OtroOrigen("el enlace apunta a otro dominio")
        url = ruta
    else:
        url = f"{tracker.base_url}/{ruta.lstrip('/')}"
    partes = urllib.parse.urlsplit(url)
    consulta = [(PARAMETRO_CLAVE, tracker.api_key)] if en_la_url else []
    consulta += [(k, v) for k, v in urllib.parse.parse_qsl(partes.query, keep_blank_values=True) if k != PARAMETRO_CLAVE]
    for nombre, valor in (params or {}).items():
        for v in valor if isinstance(valor, (list, tuple)) else [valor]:
            if v is not None:
                consulta.append((nombre, v))
    url = urllib.parse.urlunsplit(partes._replace(query=urllib.parse.urlencode(consulta)))
    cabeceras = {"User-Agent": tracker.user_agent}
    if not en_la_url:
        cabeceras["Authorization"] = f"Bearer {tracker.api_key}"
    return url, cabeceras


def atributos_torrent(cuerpo: Any) -> Dict[str, Any]:
    """Los atributos de /api/torrents/{id}: en `data.attributes`, en `attributes` o el objeto ya plano.
    ValueError si la respuesta no es un objeto."""
    datos = cuerpo.get("data", cuerpo) if isinstance(cuerpo, dict) else cuerpo
    if isinstance(datos, dict) and isinstance(datos.get("attributes"), dict):
        return datos["attributes"]
    if isinstance(datos, dict):
        return datos
    raise ValueError("la respuesta del torrent no es un objeto")


class Cliente:
    """Para la app. `enviar(metodo, url, cabeceras, **opciones)` hace la petición y devuelve lo que necesite quien
    llama; un error HTTP lo lanza con `status` y `retry_after` (como el IntegrationError de la app). `turno(prioridad)`
    espera hueco en el limitador y `apuntar_429(retry_after)` pausa a todos los que usan la clave."""

    def __init__(self, tracker: Tracker, enviar: Callable[..., Any], *, turno: Callable[[str], Any],
                 apuntar_429: Callable[[Optional[str]], Any]) -> None:
        self.tracker = tracker
        self._enviar = enviar
        self._turno = turno
        self._apuntar_429 = apuntar_429

    def pedir(self, metodo: str, ruta: str, params: Optional[Mapping[str, Any]] = None, *,
              prioridad: str = "interactiva", cabeceras: Optional[Mapping[str, str]] = None, **opciones: Any) -> Any:
        for en_la_url in (False, True):
            url, propias = peticion(self.tracker, ruta, params, en_la_url=en_la_url)
            self._turno(prioridad)
            try:
                return self._enviar(metodo, url, {**propias, **(cabeceras or {})}, **opciones)
            except Exception as exc:
                status = getattr(exc, "status", None)
                if status == 429:
                    self._apuntar_429(getattr(exc, "retry_after", None))
                if en_la_url or status not in RECHAZOS_DE_CLAVE:
                    raise


class ClienteAsincrono:
    """Para Uploadrr-EMU, sobre el httpx.AsyncClient que se le pasa: devuelve su respuesta, sea cual sea el código.
    `turno(prioridad)` es una corrutina."""

    def __init__(self, tracker: Tracker, cliente: Any, *, turno: Callable[[str], Awaitable[Any]],
                 apuntar_429: Callable[[Optional[str]], Any]) -> None:
        self.tracker = tracker
        self._cliente = cliente
        self._turno = turno
        self._apuntar_429 = apuntar_429

    async def pedir(self, metodo: str, ruta: str, params: Optional[Mapping[str, Any]] = None, *,
                    prioridad: str = "subida", cabeceras: Optional[Mapping[str, str]] = None,
                    **opciones: Any) -> Any:
        for en_la_url in (False, True):
            url, propias = peticion(self.tracker, ruta, params, en_la_url=en_la_url)
            await self._turno(prioridad)
            respuesta = await self._cliente.request(metodo, url, headers={**propias, **(cabeceras or {})}, **opciones)
            if respuesta.status_code == 429:
                self._apuntar_429(respuesta.headers.get("Retry-After"))
            if respuesta.status_code not in RECHAZOS_DE_CLAVE:
                break
        return respuesta
