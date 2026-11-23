"""El config de Uploadrr-EMU de cada subida: un fichero propio (`--config`) hecho a partir del config.yaml del usuario,
que no se toca."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from ...database import db_path, get_setting_canonical
from ...integrations.base import texto_de_error
from ...integrations.qbittorrent import qbit_configurado
from ..settings import leer_ajuste
from ..image_hosts import fallback_order, get_host_key
from ..uploadrr_config import (
    SCREENS_API_KEY_HOSTS,
    stage_anon_off,
    default_uploadrr_config_path,
    read_config_lines,
    stage_add_logo,
    stage_blu_api_off,
    stage_emu_config,
    stage_image_hosts,
    stage_firma_actual,
    stage_keep_quiet_off,
    stage_oxipng_on,
    stage_per_episode_screens,
    stage_qbittorrent_config,
    stage_sin_firma,
    stage_tmdb_key,
    stage_tracker_config,
    staged_config_text,
)
from ..trackers import EMU, TRACKERS
from ..uploadrr_contract import get_runtime_settings
from .comun import _get_db_conn, logger
from .registro import append_upload_log
from ..settings import ajuste_de_tracker


class ConfigNoPreparada(RuntimeError):
    """El config.yaml de Uploadrr no se pudo preparar: lanzarlo así lo haría fallar con las claves sin poner."""


def _lineas_preparadas(job_id: int, cwd: str) -> list[str] | None:
    """El config de verdad con DEFAULT.tmdb / TRACKERS.EMU.api_key / SCREENS / TORRENT_CLIENTS.qbit de Ajustes.

    None si no hay nada que preparar (config.yaml missing, or no secrets configured). Si preparar el config falla lanza
    ConfigNoPreparada: el job termina fallido sin lanzar Uploadrr.
    """
    config_path = default_uploadrr_config_path(cwd)
    if not config_path.exists():
        append_upload_log(
            job_id, "warning",
            f"config.yaml no encontrado en {config_path}; no se prepara el config de la subida.",
            stream="system",
        )
        return None

    conn = _get_db_conn()
    try:
        tmdb_key = get_setting_canonical(conn, "tmdb.api_key")
        emu_key = ajuste_de_tracker(conn, "api_key")
        # Los otros trackers (fase B) con clave puesta: su bloque TRACKERS.<X> va en el config de la subida.
        claves_otros = {
            t.uploadrr: clave for t in TRACKERS.values() if t is not EMU
            if (clave := (ajuste_de_tracker(conn, "api_key", t.id) or "").strip())
        }
        img_hosts = fallback_order(conn)
        img_api_keys = {
            host_id: key for host_id in SCREENS_API_KEY_HOSTS
            if (key := get_host_key(conn, host_id))
        }
        qbit_url = get_setting_canonical(conn, "qbit.url")
        qbit_tag = get_setting_canonical(conn, "qbit.tag")
        # El logo va en la descripción salvo que se haya apagado a mano.
        add_logo = leer_ajuste(conn, "emu_add_logo")
        # Las capturas de un pack, solo del primer capítulo salvo que se pidan todas.
        per_episode_screens = leer_ajuste(conn, "emu_per_episode_screens")
        qbit_username = get_setting_canonical(conn, "qbit.username")
        qbit_password = get_setting_canonical(conn, "qbit.password")
        qbit_qui = get_setting_canonical(conn, "qbit.qui_proxy_url")
        fila = conn.execute("SELECT tracker FROM upload_jobs WHERE id = ?", (job_id,)).fetchone()
        otro_tracker = bool(fila and fila[0] and fila[0] != EMU.id)
    finally:
        conn.close()

    if not img_hosts:
        # La comprobación previa ya lo para, pero la lista puede cambiar entre ella y el lanzamiento: sin esto,
        # Uploadrr tiraría de los proveedores de su config.yaml, que el usuario quitó.
        raise ConfigNoPreparada(
            "No hay ningún proveedor de imágenes en Configuración. No se lanza la subida: añade uno y reinténtala."
        )

    try:
        lines = read_config_lines(config_path)
        changed = False
        if tmdb_key:
            lines, _msg = stage_tmdb_key(lines, tmdb_key)
            changed = True
        if emu_key:
            lines, _msg = stage_emu_config(lines, api_key=emu_key)
            changed = True
        for tracker, clave in claves_otros.items():
            lines, _msg = stage_tracker_config(lines, tracker, api_key=clave)
        lines, _msg = stage_add_logo(lines, add_logo)
        lines, _msg = stage_per_episode_screens(lines, per_episode_screens)
        lines, _msg = stage_blu_api_off(lines)
        lines, _msg = stage_keep_quiet_off(lines)
        lines, _msg = stage_oxipng_on(lines)
        lines, _msg = stage_anon_off(lines)
        lines, _msg = stage_firma_actual(lines)
        if otro_tracker:
            lines, _msg = stage_sin_firma(lines)
        changed = True
        # Siempre hay al menos uno (ver arriba): esto es lo que hace que la
        # preferencia del usuario (p. ej. Pixhost) llegue de verdad a Uploadrr-EMU
        # en vez de que use el orden de img_hosts de su propio config.yaml.
        lines, _msg = stage_image_hosts(lines, img_hosts=img_hosts, api_keys=img_api_keys)
        changed = True
        if qbit_configurado(qbit_url, qbit_qui):
            # Without this, Uploadrr-EMU falls back to whatever
            # DEFAULT.default_torrent_client its own config.yaml ships with
            # (confirmed: "watch", not "qbit") — the job finishes
            # "successfully" but silently never touches qBittorrent's API.
            lines, _msg = stage_qbittorrent_config(
                lines, url=qbit_url or "", username=qbit_username or "", password=qbit_password or "", tag=qbit_tag,
                base_dir=cwd, qui_proxy_url=qbit_qui,
            )
            changed = True
        if not changed:
            append_upload_log(
                job_id, "warning",
                "Sin tmdb.api_key/emu.api_key configuradas; config.yaml no se modifica.",
                stream="system",
            )
            return None
        return lines
    except Exception as exc:
        raise _no_preparada(exc) from exc


def _no_preparada(exc: Exception) -> ConfigNoPreparada:
    return ConfigNoPreparada(
        f"No se pudo preparar el config.yaml de Uploadrr ({texto_de_error(exc)}). No se lanza la subida: con el config sin preparar fallaría."
    )


CARPETA_CONFIGS = "config_uploadrr"


def ruta_config(job_id: int, *, reenvio: bool = False) -> Path:
    """El config de Uploadrr-EMU de este trabajo, junto a la base; el reenvío a qBittorrent repite el trabajo con otro nombre."""
    return db_path().parent / CARPETA_CONFIGS / f"{job_id}{'-reenvio' if reenvio else ''}.yaml"


def ruta_descripcion(job_id: int) -> Path:
    """La descripción propia de este trabajo (`-df`), junto a su config: se borra igual al acabar y al arrancar."""
    return db_path().parent / CARPETA_CONFIGS / f"{job_id}-descripcion.txt"


def preparar_descripcion_de_la_subida(ruta: str | os.PathLike, texto: str) -> None:
    """Escribe la descripción propia en `ruta`, como el config (0600, O_EXCL). Si no se puede, la subida no se lanza:
    saldría sin ella."""
    ruta = Path(ruta)
    try:
        ruta.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        ruta.unlink(missing_ok=True)  # la de un intento que no se borró
        with os.fdopen(os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as destino:
            destino.write(texto.encode("utf-8"))
    except OSError as exc:
        raise ConfigNoPreparada(
            f"No se pudo preparar la descripción de la subida ({texto_de_error(exc)}). No se lanza: saldría sin ella."
        ) from exc


def sin_config_por_subida(cwd: str) -> ConfigNoPreparada:
    """Un Uploadrr-EMU que no anuncia `--config` (otro `uploadrr.path`, más viejo): ya no se escribe en su config.yaml."""
    return ConfigNoPreparada(
        f"El Uploadrr-EMU de {cwd} es anterior a esta versión de eMuwarr: no admite el config por subida (--config). "
        "No se lanza: actualízalo o deja la ruta de Uploadrr-EMU que trae la imagen."
    )


def preparar_config_de_la_subida(job_id: int, cwd: str, ruta: str | os.PathLike) -> None:
    """Escribe en `ruta` el config.yaml del usuario con lo de Ajustes; el config.yaml no se toca.

    Lleva claves: la carpeta es 0700 y el fichero nace 0600 (O_EXCL: nunca se abre uno que ya estaba). Sin config.yaml
    no se escribe nada y Uploadrr-EMU sale con `sin_config`, como sin `--config`.
    """
    lines = _lineas_preparadas(job_id, cwd)
    if lines is None:
        return
    ruta = Path(ruta)
    try:
        ruta.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(ruta.parent, 0o700)  # mkdir no lo cambia si ya existía
        ruta.unlink(missing_ok=True)  # el de un intento que no se borró
        with os.fdopen(os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as destino:
            destino.write(staged_config_text(lines).encode("utf-8"))
    except Exception as exc:
        borrar_config_de_la_subida(ruta)
        raise _no_preparada(exc) from exc
    append_upload_log(
        job_id, "info",
        f"Config de Uploadrr-EMU preparado para esta subida (DEFAULT.tmdb / TRACKERS.{_uploadrr_del_trabajo(job_id)}"
        ".api_key / SCREENS.img_hosts / TORRENT_CLIENTS.qbit).",
        stream="system",
    )


def _uploadrr_del_trabajo(job_id: int) -> str:
    """El bloque TRACKERS.<X> de la subida (`upload_jobs.tracker`); eMuwarez si no se sabe (solo es para el registro)."""
    try:
        conn = _get_db_conn()
        try:
            fila = conn.execute("SELECT tracker FROM upload_jobs WHERE id = ?", (job_id,)).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — el registro no para la subida
        fila = None
    tracker = TRACKERS.get(fila[0] if fila and fila[0] else "") or EMU
    return tracker.uploadrr


def borrar_config_de_la_subida(ruta: str | os.PathLike | None) -> None:
    if ruta:
        try:
            Path(ruta).unlink(missing_ok=True)
        except OSError:
            logger.warning("No se pudo borrar el config de Uploadrr-EMU de una subida")


def barrer_configs_de_subidas() -> int:
    """Al arrancar no hay ninguna subida viva: los config que quedan son de una que murió con la aplicación."""
    carpeta = db_path().parent / CARPETA_CONFIGS
    borrados = 0
    try:
        ficheros = [f for f in carpeta.iterdir() if f.is_file() or f.is_symlink()]
    except OSError:
        return 0
    for fichero in ficheros:
        try:
            fichero.unlink()
            borrados += 1
        except OSError:
            logger.warning("No se pudo borrar un config de Uploadrr-EMU de una subida interrumpida")
    return borrados


def migrar_config_bak() -> bool:
    """Una vez, al arrancar: las versiones anteriores dejaban junto a config.yaml un config.yaml.bak, el config de verdad
    (el que se editaba a mano), y escribían las claves en config.yaml. Si el .bak difiere, pasa a ser config.yaml; luego
    se aparta como config.yaml.migrado. True si había .bak.

    En la imagen config.yaml es un enlace a /config/Uploadrr/data y la imagen ya no trae el del .bak: se busca junto al
    destino del enlace."""
    conn = _get_db_conn()
    try:
        uploadrr_path = get_runtime_settings(conn)["uploadrr_path"]
    finally:
        conn.close()
    if not uploadrr_path:
        return False
    config = default_uploadrr_config_path(uploadrr_path).resolve()
    bak = config.with_name("config.yaml.bak")
    if not bak.is_file():
        return False
    contenido = bak.read_bytes()
    if not config.is_file() or config.read_bytes() != contenido:
        # Aparte y renombrado: un fallo a medias (disco lleno) no deja config.yaml cortado. Toma el modo del que
        # sustituye (o el del .bak), que os.replace no conserva; el temporal nace privado.
        modo = stat.S_IMODE((config if config.is_file() else bak).stat().st_mode)
        temporal = config.with_name("config.yaml.migrando")
        temporal.unlink(missing_ok=True)
        try:
            with os.fdopen(os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as destino:
                destino.write(contenido)
            os.chmod(temporal, modo)
            os.replace(temporal, config)
        except OSError:
            temporal.unlink(missing_ok=True)
            raise
    bak.replace(config.with_name("config.yaml.migrado"))
    return True
