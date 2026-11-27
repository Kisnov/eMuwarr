"""Lo que se le entrega a Uploadrr para subir: enlaces dentro de /config/Uploadrr/uploads.

EL PROBLEMA. `torf` toma el nombre del torrent del nombre de la carpeta, y ese
nombre es la carpeta que recibe QUIEN SE DESCARGUE el release. Entregando la
carpeta de Sonarr tal cual, el torrent se llama "Season 1" — la ficha de
eMuwarez sí sale bien, porque el nombre se le pasa aparte con -mt, pero quien
lo baje se encuentra una carpeta "Season 1" en su disco.

LO QUE SE HACÍA ANTES, y por qué se quita: renombrar la carpeta de Sonarr al
título del release. Funcionaba, pero tocaba la biblioteca del usuario — y
Sonarr es quien gestiona esa carpeta. Además dejaba rutas guardadas apuntando
a un sitio que ya no existía, y de ahí salían nombres calculados con datos de
Radarr/Sonarr en vez de con el fichero.

LO QUE SE HACE AHORA. Una carpeta aparte, con el nombre del release, dentro de
/config/Uploadrr/uploads, y dentro ENLACES DUROS a los ficheros reales:

    …/Sherlock Hound/Season 1/cap1.mkv        ← intacto, de Sonarr
    /config/Uploadrr/uploads/Sherlock Hound S01 …/Sherlock.Hound.S01E01….mkv   ← enlace duro

Un enlace duro es otro nombre para los MISMOS datos: no ocupa disco, no copia
nada, y borrar uno de los dos no toca al otro. Uploadrr recibe la carpeta
nueva, el torrent se llama como debe, y la biblioteca no se toca.

CUÁNDO NO SE PUEDE. Un enlace duro exige que las dos rutas estén en el mismo
sistema de ficheros. Es habitual tener la configuración en un SSD y los medios
en otro disco, y ahí `os.link` falla con EXDEV. En ese caso se cae a un enlace
simbólico, que cruza sistemas de ficheros sin copiar, pero no es lo mismo:
Uploadrr-EMU lo resuelve y un archivo suelto sale con el nombre y la carpeta de
la biblioteca, y el original no cuenta el enlace (st_nlink), así que no se edita
en su sitio. La comprobación de la subida lo avisa.

TODO PASA POR AQUÍ, no solo los packs, y el nombre de eMuwarez se pone AQUÍ:
el fichero de la biblioteca conserva el nombre de Radarr/Sonarr (Jellyfin lo
lee así) y el enlace de uploads lleva el del release. torf nombra el torrent
con el nombre del fichero o de la carpeta, así que es lo que recibe quien lo baje.

CUANDO HAY QUE EDITAR (nombres de pistas, título interno): un enlace duro es el
MISMO fichero, y escribir en él cambia también el torrent que ya sembrabas.
Entonces se usa una copia por reflink (`clonar`): un inodo nuevo que comparte
los datos en disco hasta que se escribe en él, y solo copia lo que cambia.

Un fichero suelto se enlaza DIRECTAMENTE en uploads, nunca dentro de una
carpeta propia: metido en una, torf haría un torrent de varios ficheros con esa
carpeta dentro, y eso cambia el release (comprobado: singlefile vs multifile).

LO QUE NO HACE: borrarlo solo. Mientras siembres, esos enlaces son de donde
qBittorrent lee. Se borra al borrar la subida, no antes.
"""
from __future__ import annotations

import errno
import logging
import os
import shutil
import threading
from typing import Callable, Iterable, Optional

from ..integrations.base import texto_de_error

logger = logging.getLogger(__name__)

# Dentro de /config, que es el volumen que el usuario ya monta: no hay nada
# nuevo que configurar, y sobrevive a reconstruir la imagen.
RAIZ_SUBIDAS = os.environ.get("UPLOADRR_UPLOADS_DIR", "/config/Uploadrr/uploads")

# Crear un enlace y guardarlo en prepared_path, o comprobar que nadie lo usa y
# borrarlo, va de uno en uno: un borrado no se lleva el enlace de otra subida.
ENLACES = threading.Lock()
# El enlace de prueba (comprobar_enlace) lleva el mismo nombre en todos los hilos y suma un enlace al original mientras
# existe: de uno en uno, y quien cuenta enlaces (motivo_para_no_editar) no ve el de otro.
_PROBANDO = threading.RLock()


def _enlazar(origen: str, destino: str) -> str:
    """Enlace duro si se puede, simbólico si no. Devuelve cuál se usó."""
    try:
        os.link(origen, destino)
        return "duro"
    except OSError as e:
        if e.errno not in (errno.EXDEV, errno.EPERM, errno.EMLINK):
            raise
        # EXDEV: distinto sistema de ficheros. EPERM: algunos montajes (CIFS,
        # ciertos NFS) no permiten enlaces duros aunque no crucen nada.
        os.symlink(origen, destino)
        return "simbólico"


# El ioctl de Linux que usa `cp --reflink=always`.
_FICLONE = 0x40049409


def clonar(origen: str, destino: str) -> None:
    """Copia por reflink: inodo propio, mismos datos en disco hasta que se edita.

    Lanza OSError si el sistema de ficheros no lo admite (ext4, tmpfs, otro
    disco): nunca cae a una copia entera."""
    try:
        import fcntl
    except ImportError:
        raise OSError(errno.EOPNOTSUPP, "reflink no disponible en este sistema") from None
    with open(origen, "rb") as src:
        dst = open(destino, "xb")
        try:
            with dst:
                fcntl.ioctl(dst.fileno(), _FICLONE, src.fileno())
        except BaseException:
            os.unlink(destino)
            raise


def enlace_simple(origen: str, destino: str) -> str:
    """Lo que se hace con un fichero que no hay que editar."""
    return f"enlace {_enlazar(origen, destino)}"


def _nombre_suelto(nombre: str) -> bool:
    """Un nombre sin carpetas: con "/" o ".." el enlace (y el borrado previo)
    caería fuera de la carpeta de subidas."""
    return bool(nombre) and nombre not in (".", "..") and os.path.basename(nombre) == nombre


class NoSePudoPreparar(Exception):
    """Un fichero no se pudo dejar en la carpeta de subidas; el mensaje dice por qué."""


def preparar_carpeta_de_pack(
    nombre_release: str,
    ficheros: Iterable[tuple[str, str]],
    *,
    raiz: Optional[str] = None,
    preparar: Callable[[str, str], str] = enlace_simple,
) -> dict:
    """Deja lista la carpeta que se le pasa a Uploadrr y devuelve qué pasó.

    `nombre_release` es el título del pack, ya saneado como nombre de carpeta.
    `ficheros` son pares (ruta LOCAL del capítulo, nombre que lleva en el pack).
    `preparar(origen, destino)` pone cada uno y devuelve cómo lo hizo.

    Si la carpeta existe de antes (un reintento), se rehace: así no queda un
    capítulo viejo de una tanda anterior, ni un enlace propio que cuente como
    si otro compartiera el fichero.
    """
    raiz = raiz or RAIZ_SUBIDAS
    # Sin esto, un título vacío daba la propia raíz y se borraba entera.
    if not nombre_release.strip(" .") or os.path.basename(nombre_release) != nombre_release:
        return {"status": "blocked", "carpeta": None,
                "errors": [f"El título del pack no vale como carpeta: «{nombre_release}»"]}
    destino = os.path.join(raiz, nombre_release)

    ficheros = [(f, n) for f, n in ficheros if f]
    if not ficheros:
        return {"status": "blocked", "carpeta": None,
                "errors": ["No hay ficheros que enlazar."]}

    faltan = [f for f, _ in ficheros if not os.path.isfile(f)]
    if faltan:
        return {"status": "blocked", "carpeta": None,
                "errors": [f"No existe: {f}" for f in faltan]}
    malos = [n for _, n in ficheros if not _nombre_suelto(n)]
    if malos:
        return {"status": "blocked", "carpeta": None,
                "errors": [f"Nombre de capítulo no válido: «{n}»" for n in malos]}

    try:
        if os.path.isdir(destino):
            # Solo si es nuestra: nunca se borra algo que no hayamos creado.
            if en_carpeta_de_subidas(destino, raiz=raiz):
                shutil.rmtree(destino)
            else:
                return {"status": "blocked", "carpeta": None,
                        "errors": [f"La carpeta de destino no está bajo {raiz}: {destino}"]}
        os.makedirs(destino, exist_ok=True)
    except OSError as e:
        return {"status": "blocked", "carpeta": None,
                "errors": [f"No se pudo preparar {destino}: {texto_de_error(e)}"]}

    metodos: set[str] = set()
    try:
        for origen, nombre in ficheros:
            metodos.add(preparar(origen, os.path.join(destino, nombre)))
    except (OSError, NoSePudoPreparar) as e:
        shutil.rmtree(destino, ignore_errors=True)
        return {"status": "blocked", "carpeta": None,
                "errors": [texto_de_error(e) if isinstance(e, NoSePudoPreparar)
                           else f"No se pudieron enlazar los capítulos: {texto_de_error(e)}"]}

    return {"status": "ready", "carpeta": destino, "errors": [],
            "metodos": sorted(metodos), "cuantos": len(ficheros)}


def preparar_fichero_suelto(
    fichero: str,
    nombre: Optional[str] = None,
    *,
    raiz: Optional[str] = None,
    preparar: Callable[[str, str], str] = enlace_simple,
) -> dict:
    """Pone UN fichero (película o capítulo) en la carpeta de subidas.

    Directamente en la raíz, sin carpeta propia: dentro de una, torf haría un
    torrent de varios ficheros con esa carpeta dentro y el release dejaría de
    ser un fichero suelto.

    `nombre` es el de eMuwarez, que es lo que torf usa para nombrar el torrent;
    sin él se conserva el del fichero.
    """
    raiz = raiz or RAIZ_SUBIDAS
    if not fichero or not os.path.isfile(fichero):
        return {"status": "blocked", "ruta": None,
                "errors": [f"No existe: {fichero or '(sin ruta)'}"]}
    if nombre is not None and not _nombre_suelto(nombre):
        return {"status": "blocked", "ruta": None, "errors": [f"Nombre no válido: «{nombre}»"]}
    if not asegurar_raiz(raiz):
        return {"status": "blocked", "ruta": None,
                "errors": [f"No se pudo crear la carpeta de subidas: {raiz}"]}

    destino = os.path.join(raiz, nombre or os.path.basename(fichero))
    try:
        # Lo que dejó un intento anterior: se rehace, y así tampoco cuenta como
        # otro enlace del mismo fichero. Salvo que sea el propio fichero.
        if os.path.lexists(destino) and os.path.abspath(destino) != os.path.abspath(fichero):
            os.unlink(destino)
        metodo = preparar(fichero, destino)
    except (OSError, NoSePudoPreparar) as e:
        return {"status": "blocked", "ruta": None,
                "errors": [texto_de_error(e) if isinstance(e, NoSePudoPreparar)
                           else f"No se pudo enlazar en {raiz}: {texto_de_error(e)}"]}

    return {"status": "ready", "ruta": destino, "errors": [],
            "metodos": [metodo], "cuantos": 1}


def en_carpeta_de_subidas(ruta: str, *, raiz: Optional[str] = None) -> bool:
    """Si la ruta está en la carpeta de subidas, aunque sea un enlace simbólico
    que apunta a la biblioteca.

    Mira el realpath del PADRE, no el de la ruta: resolver el enlace la dejaría
    fuera de la raíz.
    """
    raiz = raiz or RAIZ_SUBIDAS
    # La propia carpeta de subidas («uploads/», «uploads/.», «uploads/x/..») no está dentro de sí misma.
    if os.path.basename(os.path.normpath(ruta)) in ("", ".", "..") or os.path.realpath(ruta) == os.path.realpath(raiz):
        return False
    padre, raiz = os.path.realpath(os.path.dirname(ruta)), os.path.realpath(raiz)
    # Por componentes: con startswith, /data/uploads-old pasaba por /data/uploads.
    return os.path.commonpath((padre, raiz)) == raiz


def dentro_de(ruta: str, carpeta: str) -> bool:
    """Si `ruta` es `carpeta` o está dentro, por componentes y sin resolver enlaces: «/x/./P/E01» está en «/x/P» y
    «/x/P2/E01», no."""
    ruta, carpeta = os.path.abspath(ruta), os.path.abspath(carpeta)
    return os.path.commonpath((ruta, carpeta)) == carpeta


def abarca_la_biblioteca(raices: Iterable[str], *, raiz: Optional[str] = None) -> Optional[str]:
    """Por qué no vale la carpeta de subidas si es una ruta permitida (allowed_roots) o la contiene: los archivos de la
    biblioteca contarían como «en la carpeta de subidas» y se renombrarían. None si no."""
    raiz = raiz or RAIZ_SUBIDAS
    real = os.path.realpath(raiz)
    dentro = next((r for r in raices if r and os.path.commonpath((os.path.realpath(r), real)) == real), None)
    if dentro is None:
        return None
    return (f"La carpeta de subidas ({raiz}) es o contiene la ruta permitida {dentro}, donde está la biblioteca: sus "
            "archivos se tomarían por enlaces de subida y se renombrarían. Pon UPLOADRR_UPLOADS_DIR en una carpeta "
            "propia, p. ej. /data/uploads/emu.")


def borrar_de_subidas(ruta: Optional[str], *, raiz: Optional[str] = None) -> bool:
    """Borra de la carpeta de subidas, sea una carpeta de pack o un fichero
    suelto. Solo borra ENLACES: lo de verdad sigue en la biblioteca.

    Nunca toca nada fuera de la carpeta de subidas — `prepared_path` puede
    apuntar a la biblioteca en subidas viejas, anteriores a que todo pasara por
    aquí, y borrar una subida no puede llevarse el fichero del usuario.
    """
    raiz = raiz or RAIZ_SUBIDAS
    if not ruta or not os.path.lexists(ruta):
        return False
    if not en_carpeta_de_subidas(ruta, raiz=raiz):
        logger.warning("No se borra %s: está fuera de %s", ruta, raiz)
        return False
    try:
        if os.path.isdir(ruta) and not os.path.islink(ruta):
            shutil.rmtree(ruta)
        else:
            os.unlink(ruta)
        return True
    except OSError:
        logger.warning("No se pudo borrar de la carpeta de subidas: %s", ruta, exc_info=True)
        return False


def borrar_carpeta_de_pack(carpeta: Optional[str], *, raiz: Optional[str] = None) -> bool:
    """Borra la carpeta de enlaces. Solo borra enlaces: los ficheros de verdad
    siguen donde estaban, que es la gracia de todo esto.

    Devuelve True si borró algo. No lanza: se llama al limpiar una subida y un
    fallo aquí no puede tumbar esa limpieza.
    """
    raiz = raiz or RAIZ_SUBIDAS
    if not carpeta or not os.path.isdir(carpeta):
        return False
    # Nunca fuera de lo nuestro, por si esta ruta llegara de un sitio raro.
    if not en_carpeta_de_subidas(carpeta, raiz=raiz):
        logger.warning("No se borra %s: está fuera de %s", carpeta, raiz)
        return False
    try:
        shutil.rmtree(carpeta)
        return True
    except OSError:
        logger.warning("No se pudo borrar la carpeta de subida %s", carpeta, exc_info=True)
        return False


def asegurar_raiz(raiz: Optional[str] = None) -> bool:
    """Deja la carpeta de subidas creada y lista. Se llama al arrancar, para
    que exista antes de la primera subida en vez de aparecer a mitad."""
    raiz = raiz or RAIZ_SUBIDAS
    try:
        os.makedirs(raiz, exist_ok=True)
        return True
    except OSError:
        logger.warning("No se pudo crear la carpeta de subidas %s", raiz, exc_info=True)
        return False


def _sembrado_por_un_simbolico(fichero: str, conn) -> Optional[str]:
    """Por qué no se edita `fichero` si una subida que siembra (o la de un enlace sembrado sin fila) apunta a él con
    un enlace simbólico: el fichero suelto que es su `prepared_path`, o un capítulo de la carpeta del pack. También si
    no se pudo mirar una de esas carpetas (una que ya no existe no siembra nada). None si no."""
    from ..database import db, get_json_state
    from .upload_execution.ciclo_de_vida import ENLACES_SEMBRADOS_SIN_FILA
    if conn is None:
        with db() as propia:
            return _sembrado_por_un_simbolico(fichero, propia)
    real = os.path.realpath(fichero)
    rutas = {f["prepared_path"] for f in conn.execute(
        "SELECT prepared_path FROM upload_jobs WHERE prepared_path IS NOT NULL "
        "AND (status NOT IN ('cancelled', 'failed') OR torrent_subido = 1)")}
    rutas |= set(get_json_state(conn, ENLACES_SEMBRADOS_SIN_FILA) or [])
    dudosa = None
    for ruta in rutas:
        try:
            enlaces = ([e.path for e in os.scandir(ruta)] if os.path.isdir(ruta) and not os.path.islink(ruta)
                       else [ruta])
        except FileNotFoundError:
            continue
        except OSError as e:
            dudosa = dudosa or f"no se pudo mirar si {ruta} lo siembra con un enlace simbólico ({texto_de_error(e)})"
            continue
        if any(os.path.islink(e) and os.path.realpath(e) == real for e in enlaces):
            return "lo siembra una subida a través de un enlace simbólico"
    return dudosa


def motivo_para_no_editar(fichero: str, conn) -> Optional[str]:
    """Por qué no se edita `fichero` en su sitio (un torrent puede sembrarlo), o None.

    Un enlace simbólico de la carpeta de subidas no suma a `st_nlink`: con la carpeta en otro disco, el original
    parece no compartido aunque otra subida lo siembre a través de él. Sin poder probar el enlace no se sabe, y no se
    edita; y un simbólico de una subida de cuando la carpeta estaba en otro disco tampoco lo dice la prueba de hoy."""
    with _PROBANDO:  # el enlace de prueba de otro hilo no se cuenta como enlace duro
        enlaces = os.stat(fichero).st_nlink
        if enlaces > 1:
            return f"lo comparten {enlaces} enlaces duros (un torrent que siembra o una subida anterior)"
        prueba = comprobar_enlace(fichero)
    if not prueba["ok"]:
        return f"no se puede comprobar el enlace en la carpeta de subidas ({prueba['error']})"
    if prueba["tipo"] == "simbólico":
        return ("se enlaza con un enlace simbólico (la carpeta de subidas está en otro disco), que no cuenta como "
                "enlace: puede sembrarlo un torrent")
    return _sembrado_por_un_simbolico(fichero, conn)


def comprobar_enlace(fichero: str, *, raiz: Optional[str] = None) -> dict:
    """Si se puede enlazar ESE fichero dentro de la carpeta de subidas.

    Se comprueba haciéndolo de verdad y deshaciéndolo, no deduciéndolo de los
    números de dispositivo: hay montajes (CIFS, algunos NFS) donde los dos
    lados dan el mismo `st_dev` y aun así `os.link` falla con EPERM. Cuesta
    microsegundos y no puede equivocarse.

    Devuelve {ok, tipo, error}. `tipo` es "duro" o "simbólico". El segundo
    significa que la carpeta de subidas está en otro disco: se puede subir, pero
    Uploadrr-EMU resuelve el enlace y un archivo suelto sale con el nombre de la
    biblioteca, sembrando el original.
    """
    raiz = raiz or RAIZ_SUBIDAS
    if not asegurar_raiz(raiz):
        return {"ok": False, "tipo": None,
                "error": f"No se pudo crear la carpeta de subidas: {raiz}"}
    if not fichero or not os.path.isfile(fichero):
        return {"ok": False, "tipo": None,
                "error": f"No existe el archivo con el que probar: {fichero or '(sin ruta)'}"}

    prueba = os.path.join(raiz, f".prueba-enlace-{os.getpid()}")
    with _PROBANDO:
        try:
            if os.path.lexists(prueba):
                os.unlink(prueba)
            tipo = _enlazar(fichero, prueba)
            return {"ok": True, "tipo": tipo, "error": None}
        except OSError as e:
            return {"ok": False, "tipo": None, "error": f"{raiz}: {texto_de_error(e)}"}
        finally:
            try:
                if os.path.lexists(prueba):
                    os.unlink(prueba)
            except OSError:
                pass
