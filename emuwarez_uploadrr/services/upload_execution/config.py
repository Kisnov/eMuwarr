"""El config.yaml de Uploadrr-EMU de cada subida: un fichero propio (`--config`) o, con un Uploadrr-EMU que no lo admite,
el compartido, preparado y restaurado entre subidas en paralelo."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from ...database import db_path, get_setting_canonical
from ...integrations.base import texto_de_error
from ...integrations.qbittorrent import qbit_configurado
from ..settings import leer_ajuste
from ..image_hosts import fallback_order, get_host_key
from ..uploadrr_config_staging import (
    SCREENS_API_KEY_HOSTS,
    stage_anon_off,
    backup_config,
    default_uploadrr_config_path,
    read_baseline_lines,
    restore_config_from_backup,
    restore_stale_config,
    stage_add_logo,
    stage_blu_api_off,
    stage_emu_config,
    stage_image_hosts,
    stage_firma_actual,
    stage_keep_quiet_off,
    stage_oxipng_on,
    stage_per_episode_screens,
    stage_qbittorrent_config,
    stage_tmdb_key,
    staged_config_text,
    write_staged_config,
)
from ..uploadrr_contract import get_runtime_settings
from .comun import _get_db_conn, logger
from .registro import append_upload_log


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
            f"config.yaml no encontrado en {config_path}; no se stagean credenciales.",
            stream="system",
        )
        return None

    conn = _get_db_conn()
    try:
        tmdb_key = get_setting_canonical(conn, "tmdb.api_key")
        emu_key = get_setting_canonical(conn, "emu.api_key")
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
    finally:
        conn.close()

    if not img_hosts:
        # La comprobación previa ya lo para, pero la lista puede cambiar entre ella y el lanzamiento: sin esto,
        # Uploadrr tiraría de los proveedores de su config.yaml, que el usuario quitó.
        raise ConfigNoPreparada(
            "No hay ningún proveedor de imágenes en Configuración. No se lanza la subida: añade uno y reinténtala."
        )

    try:
        # Del config de verdad (.bak), no del config.yaml de ahora: puede ser el
        # que dejó stageado una subida que murió, o el de otra que sigue en curso.
        lines = read_baseline_lines(config_path)
        changed = False
        if tmdb_key:
            lines, _msg = stage_tmdb_key(lines, tmdb_key)
            changed = True
        if emu_key:
            lines, _msg = stage_emu_config(lines, api_key=emu_key)
            changed = True
        lines, _msg = stage_add_logo(lines, add_logo)
        lines, _msg = stage_per_episode_screens(lines, per_episode_screens)
        lines, _msg = stage_blu_api_off(lines)
        lines, _msg = stage_keep_quiet_off(lines)
        lines, _msg = stage_oxipng_on(lines)
        lines, _msg = stage_anon_off(lines)
        lines, _msg = stage_firma_actual(lines)
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


def _stage_uploadrr_config(job_id: int, cwd: str) -> Path | None:
    """Staging of DEFAULT.tmdb / TRACKERS.EMU.api_key into config.yaml.

    Returns the config_path if staging happened (so the caller knows to
    restore it later), or None if there was nothing to stage — in that case
    there is nothing to restore either.
    """
    lines = _lineas_preparadas(job_id, cwd)
    if lines is None:
        return None
    config_path = default_uploadrr_config_path(cwd)
    try:
        backup_config(config_path)
        if config_path.read_text(encoding="utf-8", errors="ignore") == staged_config_text(lines):
            return config_path  # ya está así: no se reescribe bajo una subida en curso
        write_staged_config(config_path, lines)
    except Exception as exc:
        raise _no_preparada(exc) from exc
    append_upload_log(
        job_id, "info",
        "config.yaml stageado (DEFAULT.tmdb / TRACKERS.EMU.api_key / SCREENS.img_hosts / TORRENT_CLIENTS.qbit). Backup creado.",
        stream="system",
    )
    return config_path


CARPETA_CONFIGS = "config_uploadrr"


def ruta_config(job_id: int, *, reenvio: bool = False) -> Path:
    """El config de Uploadrr-EMU de este trabajo, junto a la base; el reenvío a qBittorrent repite el trabajo con otro nombre."""
    return db_path().parent / CARPETA_CONFIGS / f"{job_id}{'-reenvio' if reenvio else ''}.yaml"


def preparar_config_de_la_subida(job_id: int, cwd: str, ruta: str | os.PathLike) -> None:
    """Escribe en `ruta` lo mismo que el staging dejaría en config.yaml; el config.yaml del usuario no se toca.

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
        "Config de Uploadrr-EMU preparado para esta subida (DEFAULT.tmdb / TRACKERS.EMU.api_key / SCREENS.img_hosts / "
        "TORRENT_CLIENTS.qbit).",
        stream="system",
    )


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


def _restore_uploadrr_config(job_id: int, config_path: Path | None) -> None:
    if config_path is None:
        return
    try:
        restored = restore_config_from_backup(config_path)
        if restored:
            append_upload_log(job_id, "info", "config.yaml restaurado desde backup.", stream="system")
        else:
            append_upload_log(job_id, "warning", "No había backup de config.yaml que restaurar.", stream="system")
    except Exception as exc:
        append_upload_log(job_id, "error", f"Fallo al restaurar config.yaml: {texto_de_error(exc)}", stream="system")


# The staged config.yaml (TMDb/EMU keys) is a SHARED resource: every job stages
# the exact same secrets from settings. To let uploads run in PARALLEL without
# clobbering each other's config (or restoring it out from under a still-running
# job), we reference-count it: the first concurrent job stages it, the last one
# to finish restores it. All overlapping jobs read the same correct config.
_config_stage_lock = threading.Lock()
_config_stage_refcount: dict[str, int] = {}


def _acquire_staged_config(job_id: int, cwd: str) -> Path | None:
    """Stage the config for this job, sharing a single staged file across
    concurrent uploads. Returns the config_path to release later, or None if
    nothing was staged."""
    config_path = default_uploadrr_config_path(cwd)
    key = str(config_path)
    with _config_stage_lock:
        if _config_stage_refcount.get(key, 0) > 0:
            # Ya lo stageó otra subida en curso. Se vuelve a calcular por si Ajustes
            # cambió desde entonces: solo reescribe si el resultado es otro.
            _stage_uploadrr_config(job_id, cwd)
            append_upload_log(job_id, "info", "config.yaml ya stageado por otra subida en curso; se reutiliza.", stream="system")
            _config_stage_refcount[key] += 1
            return config_path
        # Sin subidas en curso config.yaml debería ser el .bak: si no, es el
        # stageado de una que murió a medias.
        try:
            if restore_stale_config(config_path):
                append_upload_log(
                    job_id, "warning",
                    "config.yaml seguía stageado por una subida interrumpida; restaurado desde el backup.",
                    stream="system",
                )
        except OSError as exc:
            logger.warning("No se pudo restaurar el config.yaml interrumpido (%s): %s", config_path, exc)
        staged = _stage_uploadrr_config(job_id, cwd)
        if staged is None:
            return None
        _config_stage_refcount[key] = 1
        return staged


def restaurar_config_interrumpida() -> bool:
    """Al arrancar no hay ninguna subida viva: si config.yaml sigue stageado es
    el de una que murió sin restaurarlo, con las claves reales dentro."""
    conn = _get_db_conn()
    try:
        uploadrr_path = get_runtime_settings(conn)["uploadrr_path"]
    finally:
        conn.close()
    return bool(uploadrr_path) and restore_stale_config(default_uploadrr_config_path(uploadrr_path))


def _release_staged_config(job_id: int, config_path: Path | None) -> None:
    """Drop this job's reference; restore the config only when the last
    concurrent upload finishes."""
    if config_path is None:
        return
    key = str(config_path)
    with _config_stage_lock:
        n = _config_stage_refcount.get(key, 0)
        if n <= 1:
            _config_stage_refcount.pop(key, None)
            _restore_uploadrr_config(job_id, config_path)
        else:
            _config_stage_refcount[key] = n - 1
            append_upload_log(job_id, "info", "config.yaml sigue en uso por otra subida; no se restaura todavía.", stream="system")
