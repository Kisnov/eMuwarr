"""Límite de peticiones por minuto a la API de un tracker, compartido entre procesos.

La misma API key la usan a la vez el servidor de eMuwarr (sus hilos), su CLI y cada Uploadrr-EMU que lanza. El estado
de cada (tracker, huella de la key) vive en `<dir>/<TRACKER>-<huella16>.json`, que se lee y se reescribe con un
`flock` exclusivo cogido un instante; se duerme siempre fuera. El núcleo suelta el cerrojo si el proceso muere.

- Formato: {"marcas": [epoch, ...], "penalizado_hasta": epoch}. Nunca se guarda la key, solo su huella.
- Reloj de pared (time.time): el monótono no se puede comparar entre procesos. Al leer se descartan las marcas que ya
  salieron de la ventana y las del futuro (un salto de reloj); un fichero ilegible (un kill a mitad de escritura)
  cuenta como vacío.
- Ventana deslizante. `subida` e `interactiva` pueden llenar la capacidad; `recorrido` (catálogo, peticiones) se
  queda en `capacidad - reserva`, así una subida no espera a un catálogo de 130 páginas.
- Un tracker sin capacidad en CAPACIDAD no se limita.

Este fichero existe dos veces, idéntico: emuwarez_uploadrr/integrations/limitador_compartido.py (la app) y
uploadrr-emu/src/utils/limitador.py (Uploadrr-EMU, que no importa la app). Solo stdlib y Python 3.9.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Sequence, Tuple

try:
    import fcntl
except ImportError:  # Windows: no hay límite entre procesos
    fcntl = None  # type: ignore[assignment]

VENTANA = 60.0
CAPACIDAD = {"EMU": 28}  # eMuwarez deja 30 por minuto; dos de margen para otras apps con la misma key
RESERVA = 8  # huecos que los recorridos largos no pueden usar
ESPERA_429 = 60.0  # tras un 429 sin Retry-After: una ventana entera
PENALIZACION_MAXIMA = 600.0  # un Retry-After mayor, o una pausa más lejana en el fichero, no se cree
ESPERA_MAXIMA_SUBIDA = 120.0  # pasado esto la petición sale igualmente: siguen los reintentos ante un 429
_ESPERA_CERROJO = 2.0
_FUTURO = 1.0  # una marca más adelantada que esto es de un reloj que ha saltado
_AVISO_ESPERA = 10.0
_TRAMO = 1.0

_avisado = False


class NoDisponible(Exception):
    """El fichero compartido no se puede usar: sin fcntl, sin directorio o con el cerrojo ocupado demasiado rato."""


def directorio_por_entorno(entorno=os.environ) -> Optional[Path]:
    """`LIMITADOR_DIR` o `CONFIG_DIR/limitador`; None si no hay ninguno."""
    if entorno.get("LIMITADOR_DIR"):
        return Path(entorno["LIMITADOR_DIR"])
    if entorno.get("CONFIG_DIR"):
        return Path(entorno["CONFIG_DIR"]) / "limitador"
    return None


def huella(clave: Optional[str]) -> str:
    return hashlib.sha256((clave or "").strip().encode()).hexdigest()


def ruta(directorio: Path, tracker: str, clave: Optional[str]) -> Path:
    return Path(directorio) / f"{tracker}-{huella(clave)[:16]}.json"


def limite(prioridad: str, capacidad: int, reserva: int = RESERVA) -> int:
    return capacidad - reserva if prioridad == "recorrido" else capacidad


def espera(marcas: Sequence[float], penalizado_hasta: float, ahora: float, limite: int, ventana: float = VENTANA) -> float:
    """Segundos hasta que cabe otra petición (0 = cabe). `marcas`: ordenadas y ya dentro de la ventana."""
    if penalizado_hasta > ahora:
        return penalizado_hasta - ahora
    if len(marcas) < limite:
        return 0.0
    # Hay hueco cuando la marca que deja `limite - 1` más nuevas sale de la ventana.
    return marcas[len(marcas) - limite] + ventana - ahora


def segundos_retry_after(valor: Optional[str], defecto: float = ESPERA_429) -> float:
    """Retry-After en segundos más uno (el entero puede quedarse corto); una fecha o nada da `defecto`."""
    texto = (valor or "").strip()
    return float(int(texto) + 1) if texto.isdecimal() else defecto


def leer(contenido: bytes, ahora: float, ventana: float = VENTANA) -> Tuple[List[float], float]:
    """(marcas vigentes ordenadas, penalizado_hasta) de un fichero; uno ilegible cuenta como vacío."""
    try:
        datos = json.loads(contenido.decode("utf-8"))
        marcas = [float(t) for t in datos.get("marcas", [])]
        penalizado_hasta = float(datos.get("penalizado_hasta", 0.0))
    except (ValueError, TypeError, AttributeError):
        return [], 0.0
    marcas = sorted(t for t in marcas if ahora - ventana < t <= ahora + _FUTURO)
    if penalizado_hasta > ahora + PENALIZACION_MAXIMA:
        penalizado_hasta = 0.0
    return marcas, penalizado_hasta


@contextlib.contextmanager
def _cerrojo(directorio: Path, tracker: str, clave: Optional[str]) -> Iterator[int]:
    if fcntl is None:
        raise NoDisponible("sin fcntl")
    try:
        Path(directorio).mkdir(parents=True, exist_ok=True)
        fd = os.open(ruta(directorio, tracker, clave), os.O_RDWR | os.O_CREAT, 0o666)
    except OSError as exc:
        raise NoDisponible(str(exc)) from exc
    try:
        try:
            os.fchmod(fd, 0o666)  # que la app y el CLI, con usuarios distintos, abran el mismo fichero
        except OSError:
            pass
        hasta = time.monotonic() + _ESPERA_CERROJO
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= hasta:
                    raise NoDisponible("cerrojo ocupado") from None
                time.sleep(0.005)
            except OSError as exc:
                raise NoDisponible(str(exc)) from exc
        try:
            yield fd
        except OSError as exc:
            raise NoDisponible(str(exc)) from exc
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)  # aunque un hijo recién lanzado tenga el descriptor duplicado
    finally:
        os.close(fd)


def _leer_fd(fd: int, ahora: float, ventana: float) -> Tuple[List[float], float]:
    return leer(os.pread(fd, 1 << 16, 0), ahora, ventana)


def _escribir_fd(fd: int, marcas: Sequence[float], penalizado_hasta: float) -> None:
    datos = json.dumps({"marcas": list(marcas), "penalizado_hasta": penalizado_hasta}).encode()
    os.ftruncate(fd, 0)
    os.pwrite(fd, datos, 0)


def reservar(directorio: Path, tracker: str, clave: Optional[str], prioridad: str = "subida", *,
             capacidad: Optional[int] = None, reserva: int = RESERVA, ventana: float = VENTANA,
             reloj: Callable[[], float] = time.time) -> float:
    """Apunta una petición si cabe y devuelve 0; si no, devuelve lo que falta sin apuntar nada. Lanza NoDisponible."""
    capacidad = CAPACIDAD.get(tracker) if capacidad is None else capacidad
    if capacidad is None:
        return 0.0
    with _cerrojo(directorio, tracker, clave) as fd:
        ahora = reloj()
        marcas, penalizado_hasta = _leer_fd(fd, ahora, ventana)
        falta = espera(marcas, penalizado_hasta, ahora, limite(prioridad, capacidad, reserva), ventana)
        if falta <= 0:
            marcas.append(ahora)
        _escribir_fd(fd, marcas, penalizado_hasta)
    return max(falta, 0.0)


def penalizar(directorio: Path, tracker: str, clave: Optional[str], segundos: float = ESPERA_429, *,
              ventana: float = VENTANA, reloj: Callable[[], float] = time.time) -> float:
    """Un 429: nadie usa esta key en este tracker hasta dentro de `segundos`. Devuelve hasta cuándo (epoch).
    Lanza NoDisponible."""
    if tracker not in CAPACIDAD:
        return 0.0
    with _cerrojo(directorio, tracker, clave) as fd:
        ahora = reloj()
        marcas, penalizado_hasta = _leer_fd(fd, ahora, ventana)
        penalizado_hasta = max(penalizado_hasta, ahora + min(max(segundos, 0.0), PENALIZACION_MAXIMA))
        _escribir_fd(fd, marcas, penalizado_hasta)
    return penalizado_hasta


def _avisar_una_vez(avisar: Callable[[str], object], exc: Exception) -> None:
    global _avisado
    if not _avisado:
        _avisado = True
        avisar(f"El límite de peticiones compartido no está disponible ({exc}); las peticiones salen sin esperar")


async def esperar(tracker: str, clave: Optional[str], prioridad: str = "subida", *,
                  espera_maxima: Optional[float] = None, avisar: Callable[[str], object] = print) -> float:
    """Espera con asyncio.sleep a que haya hueco y apunta la petición; devuelve lo esperado. Sin directorio en el
    entorno no hace nada; si el fichero no sirve, deja pasar y avisa una vez por proceso."""
    directorio = directorio_por_entorno()
    if directorio is None or tracker not in CAPACIDAD:
        return 0.0
    espera_maxima = ESPERA_MAXIMA_SUBIDA if espera_maxima is None else espera_maxima
    inicio = time.monotonic()
    avisado = False
    while True:
        try:
            falta = reservar(directorio, tracker, clave, prioridad)
        except NoDisponible as exc:
            _avisar_una_vez(avisar, exc)
            return time.monotonic() - inicio
        esperado = time.monotonic() - inicio
        if falta <= 0:
            return esperado
        if esperado >= espera_maxima:
            avisar(f"{tracker}: {esperado:.0f} s esperando al límite de peticiones; se pide igualmente")
            return esperado
        if not avisado and falta >= _AVISO_ESPERA:
            avisado = True
            avisar(f"{tracker}: límite de peticiones por minuto de la API; espera de {falta:.0f} s")
        await asyncio.sleep(min(falta, _TRAMO, espera_maxima - esperado))


def apuntar_429(tracker: str, clave: Optional[str], retry_after: Optional[str] = None, *,
                avisar: Callable[[str], object] = print) -> None:
    """Un 429 recibido: pausa a todos los procesos con esta key. Sin directorio en el entorno no hace nada."""
    directorio = directorio_por_entorno()
    if directorio is None:
        return
    try:
        penalizar(directorio, tracker, clave, segundos_retry_after(retry_after))
    except NoDisponible as exc:
        _avisar_una_vez(avisar, exc)
