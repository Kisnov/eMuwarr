"""Los eventos JSON Lines de Uploadrr-EMU (`--eventos RUTA`): detectar si los admite, seguir el fichero y traducirlos.

Con eventos, ellos deciden fases, pasos y resultado de la subida; el texto de stdout solo se registra. El esquema es
`contrato_eventos.json` (la copia de la app en `tests/fixtures/`; la de verdad vive en Uploadrr-EMU).

No escribe en la base: `EstadoEventos.aplicar` devuelve lo que hay que escribir y `upload_execution` lo escribe.
Nada de aquí lanza hacia el trabajo: lo que no se entiende (línea rota, evento o código de una versión más nueva)
se ignora y se avisa una sola vez en el log.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Callable, Optional

from ..database import db_path
from .trackers import EMU
from .upload_result_parser import texto_aviso_pack

logger = logging.getLogger("emuwarez.upload")

NOMBRE_CONTRATO = "contrato_eventos.json"
VERSIONES_CONOCIDAS = (1,)


def _contrato(carpeta_uploadrr: str | os.PathLike | None) -> dict:
    """El `contrato_eventos.json` del checkout de Uploadrr-EMU si su versión es una que la app conoce; si no, {}."""
    if not carpeta_uploadrr:
        return {}
    try:
        with open(Path(carpeta_uploadrr) / NOMBRE_CONTRATO, encoding="utf-8") as f:
            contrato = json.load(f)
        return contrato if contrato.get("version") in VERSIONES_CONOCIDAS else {}
    except (OSError, ValueError, AttributeError):
        return {}


def admite_eventos(carpeta_uploadrr: str | os.PathLike | None) -> bool:
    """Si el checkout de Uploadrr-EMU trae `contrato_eventos.json` con una versión que la app conoce."""
    return bool(_contrato(carpeta_uploadrr))


def admite_config(carpeta_uploadrr: str | os.PathLike | None) -> bool:
    """Si además admite `--config RUTA` (`"config": true`): cada subida lleva su propio config (upload_execution/config.py)."""
    return _contrato(carpeta_uploadrr).get("config") is True


def ruta_eventos(job_id: int, *, reenvio: bool = False) -> Path:
    """El fichero de eventos de este trabajo, junto a la base (el proceso de Uploadrr-EMU lo escribe con el mismo usuario).
    Un trabajo nuevo por intento; el reenvío a qBittorrent repite el mismo, por eso lleva su propio nombre."""
    return db_path().parent / "eventos_uploadrr" / f"{job_id}{'-reenvio' if reenvio else ''}.jsonl"


def preparar_fichero(ruta: str | os.PathLike) -> bool:
    """Lo deja vacío y creado: los eventos de un intento anterior nunca se leen. False si no se puede escribir."""
    try:
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        Path(ruta).write_bytes(b"")
        return True
    except OSError as exc:
        logger.warning("No se puede preparar el fichero de eventos de Uploadrr-EMU: %s", type(exc).__name__)
        return False


def borrar_fichero(ruta: str | os.PathLike | None) -> None:
    if ruta:
        try:
            Path(ruta).unlink(missing_ok=True)
        except OSError:
            logger.warning("No se pudo borrar el fichero de eventos de Uploadrr-EMU")


def barrer_eventos() -> int:
    """Al arrancar no hay ninguna subida viva: los ficheros de eventos que quedan son de una que murió con la aplicación."""
    borrados = 0
    try:
        ficheros = [f for f in (db_path().parent / "eventos_uploadrr").iterdir() if f.is_file() or f.is_symlink()]
    except OSError:
        return 0
    for fichero in ficheros:
        try:
            fichero.unlink()
            borrados += 1
        except OSError:
            logger.warning("No se pudo borrar un fichero de eventos de una subida interrumpida")
    return borrados


def _una_vez(avisados: set[str], clave: str, texto: str) -> None:
    """Avisa en el log una sola vez por clave: un Uploadrr-EMU más nuevo repetiría el aviso en cada línea."""
    if clave not in avisados:
        avisados.add(clave)
        logger.warning("Eventos de Uploadrr-EMU: %s", texto)


# (fase, estado) de un evento `fase` -> (clave cruda de uploadrr_progress, pct) que ya entiende advance_running_phase.
_FASES: dict[tuple[str, str], tuple[str, int]] = {
    ("analisis", "empieza"): ("queue_processing", 100),
    ("analisis", "hecha"): ("video_metadata", 100),
    ("duplicados", "empieza"): ("duplicates", 50),
    ("duplicados", "hecha"): ("duplicates", 100),
    ("capturas", "empieza"): ("screenshots_generate", 0),
    ("capturas", "hecha"): ("screenshots_upload", 100),
    ("subida_capturas", "empieza"): ("screenshots_upload", 0),
    ("subida_capturas", "hecha"): ("screenshots_upload", 100),
    ("torrent", "empieza"): ("hashing", 0),
    ("torrent", "hecha"): ("torrent_create", 100),
    ("subida", "empieza"): ("uploading_emu", 0),
    ("subida", "hecha"): ("uploading_emu", 100),
    ("cliente", "empieza"): ("qbittorrent_add", 0),
    ("cliente", "hecha"): ("qbittorrent_add", 100),
}
# `avance` con pct: solo estas dos fases lo emiten (el hash del torrent y las capturas subidas).
_AVANCES = {"subida_capturas": "screenshots_upload", "torrent": "hashing"}
_PASOS = ("screenshots", "torrent")

# Por qué no entró en qBittorrent (`cliente ok:false motivo:`), en una frase para el usuario.
MOTIVOS_CLIENTE: dict[str, str] = {
    "login": "credenciales incorrectas",
    "ya_estaba": "el torrent ya estaba en qBittorrent",
    "no_aceptado": "qBittorrent no aceptó el torrent",
    "no_parado": "no se pudo dejar el torrent parado",
    "no_arrancado": "no se pudo arrancar el torrent",
    "no_confirmado": "Uploadrr-EMU no pudo confirmar que se añadiera",
    "no_seed": "no hay nada que sembrar",
    "sin_torrent": "no se pudo descargar el .torrent firmado desde eMuwarez",
    "no_visible": "eMuwarez no enseña el torrent: estará pendiente de moderación (o lo han retirado)",
    "sin_cliente": "Uploadrr-EMU no tiene cliente de torrents configurado",
    "solo_cliente_no_qbit": "el cliente de Uploadrr-EMU no es qBittorrent",
    "cliente_no_soportado": "el cliente de torrents no está soportado",
    "sin_emu": "Uploadrr-EMU no tiene ese tracker configurado",
    "no_coincide": "el .torrent de eMuwarez no coincide con los ficheros",
    "error": "error al hablar con qBittorrent",
}

# Qué dice la app de cada `resultado.codigo` de un fallo. Los fragmentos en inglés son los que ya buscan los frenos del
# lote (`lotes.MARCAS_DE_FALLO_POR_ELEMENTO`): un duplicado o un grupo vetado siguen siendo fallo de ese ítem.
MENSAJES_RESULTADO: dict[str, str] = {
    "duplicado": "Ya hay un torrent igual en eMuwarez (Potential duplicate on EMU)",
    "busqueda_caida": "No se pudo buscar duplicados en eMuwarez (Dupe search failed on EMU)",
    "rechazado": "eMuwarez rechazó la subida",
    "tmdb": "No se encontró en TMDb (TMDb ID not found)",
    "sin_video": "No hay ningún vídeo en la ruta preparada",
    "cliente_fallido": "No se pudo añadir el torrent a qBittorrent",
    "excepcion": "Error inesperado de Uploadrr-EMU",
    "grupo_prohibido": "Grupo vetado en eMuwarez (Banned group on EMU)",
    "contenido_prohibido": "Contenido vetado en eMuwarez (Banned content on EMU)",
    "tracker_invalido": "Uploadrr-EMU no reconoce el tracker",
    "checks_fallidos": "Uploadrr-EMU no pasó las comprobaciones previas",
    "cancelado": "Uploadrr-EMU canceló la subida",
    "torrent_viejo": "El .torrent que había en el tmp de Uploadrr-EMU es de otra versión del fichero",
    "tmp_sucio": "Uploadrr-EMU no pudo limpiar su carpeta tmp",
    "error_prep": "Falló la preparación de la subida en Uploadrr-EMU",
    "args_invalidos": "Uploadrr-EMU rechazó los argumentos de la subida",
    "salida_inesperada": "Uploadrr-EMU salió con error sin explicar por qué",
    "sin_rutas": "Uploadrr-EMU no recibió ninguna ruta que subir",
    "sin_trackers": "Uploadrr-EMU no tiene ningún tracker válido",
    "sin_config": "Uploadrr-EMU no encuentra su config.yaml",
    "config_invalida": "El config.yaml de Uploadrr-EMU no es válido",
    "interrumpido": "Uploadrr-EMU fue interrumpido",
    "tipo_desconocido": "eMuwarez no tiene el tipo de esta copia (Unknown type on EMU)",
    "base_sin_torrent": "No se pudo bajar el torrent del otro tracker para hacer el de esta subida con las mismas piezas",
    "base_no_coincide": "El torrent del otro tracker no coincide con el fichero de la carpeta de subidas: no se sube "
                        "para no crear un torrent que no se pueda sembrar junto al otro",
}


class EstadoEventos:
    """Lo que dicen los eventos de una ejecución, acumulado. Se usa desde un solo hilo (el del seguidor)."""

    def __init__(self) -> None:
        self.inicio: Optional[dict] = None
        self.pasos: list[str] = []
        self.url: Optional[str] = None
        self.torrent_id: Optional[str] = None
        self.cliente: Optional[dict] = None
        self.aviso_pack: Optional[str] = None
        self.resultado: Optional[dict] = None
        self._avisados: set[str] = set()

    def aplicar(self, ev: dict) -> list[tuple]:
        """Registra un evento y devuelve lo que la app tiene que hacer con él, en orden:
        ("fase", clave_cruda, pct) · ("paso", clave) · ("qbit_ok",) · ("qbit_fallo", motivo)."""
        nombre = ev.get("ev")
        if nombre == "inicio":
            self.inicio = ev
            return []
        if nombre == "fase":
            clave, estado, pct = ev.get("clave"), ev.get("estado"), ev.get("pct")
            if estado == "avance" and clave in _AVANCES:
                return [("fase", _AVANCES[clave], max(0, min(100, pct)))] if isinstance(pct, int) else []
            if (clave, estado) in _FASES:
                return [("fase", *_FASES[(clave, estado)])]
            if estado != "fallida":  # una fase fallida la cuenta el resultado, no un avance
                _una_vez(self._avisados, f"fase:{clave}:{estado}", f"fase desconocida {clave!r}/{estado!r}, se ignora")
            return []
        if nombre == "paso_hecho":
            paso = ev.get("paso")
            if paso not in _PASOS:
                _una_vez(self._avisados, f"paso:{paso}", f"paso desconocido {paso!r}, se ignora")
                return []
            return [("paso", paso)]
        if nombre == "subido":
            url, torrent_id = ev.get("url"), ev.get("torrent_id")
            if not isinstance(url, str) or not isinstance(torrent_id, int) or isinstance(torrent_id, bool):
                _una_vez(self._avisados, "subido", "evento «subido» sin url o torrent_id válidos, se ignora")
                return []
            self.url, self.torrent_id = url, str(torrent_id)
            return [("paso", "tracker")]
        if nombre == "cliente":
            if not isinstance(ev.get("ok"), bool):
                _una_vez(self._avisados, "cliente", "evento «cliente» sin ok, se ignora")
                return []
            self.cliente = ev
            if ev["ok"]:
                return [("qbit_ok",)]
            motivo = ev.get("motivo")
            # Sin cliente configurado Uploadrr-EMU no intentó nada: como con el texto, no cuenta como fallo de qBittorrent.
            return [] if motivo == "sin_cliente" else [("qbit_fallo", motivo)]
        if nombre == "aviso":
            if ev.get("codigo") == "pack_incompleto" and isinstance(ev.get("datos"), dict):
                datos = ev["datos"]
                faltan = [str(n) for n in datos.get("faltan") or [] if isinstance(n, int)]
                temporada = datos.get("temporada")
                self.aviso_pack = texto_aviso_pack("?" if temporada is None else temporada, faltan)
            else:
                _una_vez(self._avisados, f"aviso:{ev.get('codigo')}", f"aviso desconocido {ev.get('codigo')!r}, se ignora")
            return []
        if nombre == "resultado":
            if not isinstance(ev.get("ok"), bool):
                _una_vez(self._avisados, "resultado", "evento «resultado» sin ok, se ignora")
                return []
            self.resultado = ev
            return []
        _una_vez(self._avisados, f"ev:{nombre}", f"evento desconocido {nombre!r}, se ignora")
        return []


def ficha_de_estado(estado: EstadoEventos) -> dict:
    """Lo que guarda una subida que falla después de subir el torrent (como `registro._ficha_del_torrent`, pero de `subido`)."""
    if not estado.url:
        return {}
    return {"result_url": estado.url, "result_torrent_id": estado.torrent_id, "torrent_subido": 1}


def del_tracker(texto: str, nombre: str = EMU.nombre, tk: str = "") -> str:
    """Los textos de este módulo y los de qBittorrent hablan de eMuwarez: en la subida a otro tracker, con su nombre
    (`nombre`) y, en el fragmento en inglés que buscan los frenos del lote, con su `-tk` (`tk`)."""
    if nombre != EMU.nombre:
        texto = texto.replace(EMU.nombre, nombre)
    return texto.replace(f" on {EMU.uploadrr})", f" on {tk})") if tk else texto


def mensaje_de_fallo(resultado: dict, nombre: str = EMU.nombre, tk: str = "") -> str:
    """El texto de un `resultado` con ok:false: el de su código y, si Uploadrr-EMU dio `detalle`, ese detalle.
    `nombre` y `tk`: los del tracker de la subida (del_tracker)."""
    codigo = resultado.get("codigo")
    texto = del_tracker(MENSAJES_RESULTADO.get(codigo) or f"Uploadrr-EMU terminó con el resultado «{codigo}»",
                        nombre, tk)
    detalle = resultado.get("detalle")
    if isinstance(detalle, str) and detalle.strip() and detalle.strip() not in texto:
        texto += f": {detalle.strip()}"
    return texto


def clasificar_con_eventos(estado: EstadoEventos, returncode: int | None, nombre: str = EMU.nombre,
                           tk: str = "") -> dict:
    """El veredicto de una ejecución con eventos, con las claves de `classify_upload_result` más `torrent_subido`.

    `resultado.ok` y el código de salida tienen que estar de acuerdo para que sea una subida buena; en cualquier
    otro caso es fallida y el mensaje dice cuál es el desacuerdo. `subido`, si llegó, se conserva siempre."""
    r = estado.resultado
    salida = f"exit code {returncode}"
    if r is None:
        # El proceso murió a mitad (o no pudo decir cómo acabó): sin resultado no se sabe si se subió.
        error = (f"Uploadrr-EMU terminó con {salida} sin dar un resultado: no se sabe si la subida llegó a hacerse."
                 if returncode == 0 else
                 f"Uploadrr-EMU murió a mitad de la subida ({salida}) sin dar un resultado.")
    elif r["ok"] and returncode == 0:
        error = None
    elif r["ok"]:
        error = f"Uploadrr-EMU dijo que la subida fue bien, pero salió con {salida}: se da por fallida."
    else:
        error = mensaje_de_fallo(r, nombre, tk)
        if returncode != 0:
            error += f"\nExit code: {returncode}"
    if error and estado.url:
        error += f" El torrent sí llegó a subirse a {nombre}."
    return {
        "status": "failed" if error else "completed",
        "result_url": estado.url,
        "torrent_id": estado.torrent_id,
        "error_summary": error,
        "exit_code": returncode,
        "season_pack_warning": estado.aviso_pack,
    }


class SeguidorEventos:
    """Lee el fichero de eventos según crece y pasa cada evento válido a `al_evento`. Nunca lanza.

    Tolera una última línea a medias (espera a su salto de línea), líneas que no son JSON y eventos de otra versión."""

    def __init__(self, ruta: str | os.PathLike, al_evento: Callable[[dict], None]) -> None:
        self._ruta = Path(ruta)
        self._al_evento = al_evento
        self._pos = 0
        self._resto = b""
        self._avisados: set[str] = set()
        self._candado = threading.Lock()  # lo lee el hilo del seguidor y, antes de `inicio`, el de cada tubería

    def leer(self) -> None:
        """Procesa las líneas completas que haya desde la última lectura."""
        with self._candado:
            try:
                with open(self._ruta, "rb") as f:
                    f.seek(self._pos)
                    datos = f.read()
            except OSError:
                return
            self._pos += len(datos)
            *lineas, self._resto = (self._resto + datos).split(b"\n")
            for linea in lineas:
                self._linea(linea)

    def seguir(self, parar: threading.Event, cada: float = 0.1) -> None:
        while not parar.is_set():
            self.leer()
            parar.wait(cada)

    def terminar(self) -> None:
        """Última lectura con el proceso ya muerto: una línea final sin salto de línea también cuenta si es JSON entero."""
        self.leer()
        with self._candado:
            resto, self._resto = self._resto, b""
            self._linea(resto)

    def _linea(self, crudo: bytes) -> None:
        crudo = crudo.strip()
        if not crudo:
            return
        try:
            ev = json.loads(crudo.decode("utf-8", errors="replace"))
        except ValueError:
            _una_vez(self._avisados, "json", "una línea no es JSON, se ignora")
            return
        if not isinstance(ev, dict) or not isinstance(ev.get("ev"), str):
            _una_vez(self._avisados, "forma", "una línea no es un evento, se ignora")
            return
        if ev.get("v") not in VERSIONES_CONOCIDAS:
            _una_vez(self._avisados, f"v:{ev.get('v')}", f"versión {ev.get('v')!r} del evento desconocida, se ignora")
            return
        try:
            self._al_evento(ev)
        except Exception:
            logger.exception("Eventos de Uploadrr-EMU: no se pudo aplicar el evento %r", ev.get("ev"))


__all__ = [
    "admite_eventos", "ruta_eventos", "preparar_fichero", "borrar_fichero", "EstadoEventos", "SeguidorEventos",
    "clasificar_con_eventos", "ficha_de_estado", "mensaje_de_fallo", "del_tracker", "MOTIVOS_CLIENTE", "MENSAJES_RESULTADO",
]
