"""Backup and restore of CONFIG_DIR — keys left out unless asked for, confirmation required for restore."""
from __future__ import annotations

import datetime
import os
import posixpath
import re
import secrets
import shutil
import sqlite3
import tarfile
import tempfile
import zlib
from pathlib import Path

from ..database import (
    CANONICAL_SETTING_ALIASES, SCHEMA_VERSION, _version_guardada, get_state, olvidar_esquema, preparar_base,
)
from ..integrations.base import texto_de_error
from .settings import all_settings, es_secreto, get_setting_canonical, set_setting

RESTORE_CONFIRMATION = "RESTAURAR_CONFIG"
_DB_NAME = "emuwarez_uploadrr.db"
_CABECERA_SQLITE = b"SQLite format 3\x00"
# Las copias de la tarea programada, las de antes de restaurar y las subidas desde el navegador llevan su marca en el
# nombre: la retención solo toca las dos primeras. Las pre-migracion-*.db de database.py no encajan en el patrón.
PREFIJO_AUTOMATICA = "emuwarez_config_auto_"
PREFIJO_ANTES_DE_RESTAURAR = "emuwarez_config_antes_de_restaurar_"
PREFIJO_SUBIDA = "emuwarez_config_subida_"
_COPIAS_ANTES_DE_RESTAURAR = 5
_NOMBRE_COPIA = re.compile(r"^emuwarez_config_[A-Za-z0-9_]+\.tar\.gz$")
LIMITE_SUBIDA_BYTES = 1024 ** 3
# Un .tar.gz truncado (disco lleno, contenedor parado a medias) no da TarError sino estos.
_TAR_ILEGIBLE = (tarfile.TarError, OSError, EOFError, zlib.error)
# Lo que no va en la copia: las copias anteriores, los enlaces de subida y los
# temporales de Uploadrr (capturas, torrents; en una instalación real, 1,7 GB).
# Una carpeta "backups" a cualquier profundidad tampoco: la copia no se mete a sí misma.
# config_uploadrr/ son los config de las subidas en curso, con claves: ni en la copia con secretos.
_FUERA_DE_LA_COPIA = (("Uploadrr", "uploads"), ("Uploadrr", "tmp"), ("config_uploadrr",))
# Lo que solo va en la copia con secretos. El config.yaml de Uploadrr-EMU (y el
# config.yaml.bak que dejaron las versiones anteriores, también apartado como
# config.yaml.migrado o si no se pudo migrar) pueden llevar claves de cualquier
# tracker y con cualquier nombre: taparlas línea a línea no da garantías. Las cookies
# son sesiones abiertas. config.py, config.json y backup/old_config.py son
# configuraciones antiguas de Uploadrr que aún reconoce (su .gitignore las
# deja fuera por lo mismo). config.json y config.enc.json en la raíz son del
# CLI antiguo (services/cli_config.py) y llevan la API key de eMuwarez.
_CON_CLAVES = (
    ("Uploadrr", "data", "config.yaml"),
    ("Uploadrr", "data", "config.yaml.bak"),
    ("Uploadrr", "data", "config.yaml.migrado"),
    ("Uploadrr", "data", "cookies"),
    ("Uploadrr", "data", "config.py"),
    ("Uploadrr", "data", "config.json"),
    ("Uploadrr", "data", "backup", "old_config.py"),
    ("config.json",),
    ("config.enc.json",),
)


def _fuera_de_la_copia(rel_parts: tuple[str, ...], include_secrets: bool = False) -> bool:
    fuera = _FUERA_DE_LA_COPIA if include_secrets else _FUERA_DE_LA_COPIA + _CON_CLAVES
    return "backups" in rel_parts or any(rel_parts[:len(p)] == p for p in fuera)


def _es_sqlite(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(16) == _CABECERA_SQLITE


def _vaciar_claves(snapshot: Path) -> None:
    """Deja vacías las claves de la instantánea: los ajustes secretos
    (settings.es_secreto) y la API key de cada instancia de Radarr/Sonarr.

    El VACUUM no sobra: un UPDATE cambia la fila pero el valor anterior puede
    seguir en los bytes del fichero, y la base viva ya trae en sus páginas
    libres las claves que se cambiaron antes. VACUUM reescribe el fichero solo
    con lo que está vivo.
    """
    conn = sqlite3.connect(str(snapshot))
    conn.row_factory = sqlite3.Row
    try:
        # La instantánea hereda el modo WAL de la base viva. Fuera de WAL, el
        # VACUUM reescribe el propio .db, que es lo único que entra en la copia.
        conn.execute("PRAGMA journal_mode = DELETE")
        for key, value in all_settings(conn).items():
            if value and es_secreto(key):
                set_setting(conn, key, "")
        try:
            conn.execute("UPDATE instances SET api_key = ''")
        except sqlite3.OperationalError:
            pass  # una base sin la tabla: no hay claves que vaciar
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()


def _add_db_snapshot(
    tar: tarfile.TarFile, db_file: Path, arcname: str, *, include_secrets: bool = False,
) -> None:
    """Mete en la copia una instantánea coherente de la base de datos, con las
    claves vacías salvo que se pidan.

    Con la app en marcha la base está en modo WAL: copiar el .db, el -wal y el
    -shm uno detrás de otro mientras alguien escribe da una copia rota. La API
    de backup de SQLite lee todo dentro de una misma transacción.
    """
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / db_file.name
        # mode=rw: si la base ha desaparecido, falla en vez de crear una vacía.
        src = sqlite3.connect(db_file.resolve().as_uri() + "?mode=rw", uri=True, timeout=30)
        dst = sqlite3.connect(str(snapshot))
        try:
            src.backup(dst)
        except sqlite3.DatabaseError:
            # Una base SQLite que falla no se copia en crudo: saldría rota.
            if _es_sqlite(db_file):
                raise
            snapshot = db_file
        finally:
            dst.close()
            src.close()
        if snapshot != db_file and not include_secrets:
            _vaciar_claves(snapshot)
        tar.add(snapshot, arcname=arcname)


def _get_config_dir() -> Path:
    env = os.environ.get("CONFIG_DIR")
    if env:
        return Path(env)
    return Path.home() / ".config" / "emuwarez-renamer"


def _get_backup_dir(base: Path | None = None) -> Path:
    config = base if base is not None else _get_config_dir()
    d = config / "backups"
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)  # los ficheros ya son 0600; así tampoco se ven sus nombres ni tamaños
    return d


def get_backup_dir() -> Path:
    """Public accessor for the directory backups are read from/written to."""
    return _get_backup_dir()


def _abrir_0600(path: Path):
    """Abre `path` para escribir, creado solo legible por su dueño (la copia puede llevar claves)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(path, 0o600)  # si ya existía con otro modo
    return os.fdopen(fd, "wb")


def get_config_inventory() -> dict:
    """Returns a summary of config files — counts only, no secret values."""
    config_dir = _get_config_dir()
    if not config_dir.exists():
        return {"config_dir": str(config_dir), "exists": False, "files": [], "total_size_bytes": 0}
    files = []
    total = 0
    for f in sorted(config_dir.rglob("*")):
        rel_parts = f.relative_to(config_dir).parts
        # Lo mismo que excluye create_config_backup en la copia normal, la que
        # hace la interfaz: este inventario es lo que se le enseña al usuario
        # como "esto es lo que se va a copiar", así que contar aquí algo que la
        # copia no lleva sería mentir.
        if _fuera_de_la_copia(rel_parts):
            continue
        if f.is_file():
            size = f.stat().st_size
            total += size
            files.append({"name": str(f.relative_to(config_dir)), "size_bytes": size})
    return {
        "config_dir": str(config_dir),
        "exists": True,
        "files": files,
        "total_size_bytes": total,
    }


def create_config_backup(
    *,
    output_dir: Path | None = None,
    include_secrets: bool = False,
    confirmation: str | None = None,
    prefijo: str = "emuwarez_config_",
) -> dict:
    """Creates a .tar.gz backup of CONFIG_DIR in output_dir (or default backups/).
    prefijo: PREFIJO_AUTOMATICA o PREFIJO_ANTES_DE_RESTAURAR, que es lo que reconoce la retención de cada una.
    El archivo nace 0600: puede llevar todas las claves.

    By default the keys stay out: the database goes with every secret setting
    and every instance API key empty, and the files that carry keys
    (_CON_CLAVES) are skipped. include_secrets=True keeps them all and
    requires confirmation=RESTAURAR_CONFIG (reused token).
    The archive always excludes the backups/ subdirectory.
    """
    if include_secrets and confirmation != RESTORE_CONFIRMATION:
        return {
            "ok": False,
            "error": f"Para incluir secretos, pasa confirmation='{RESTORE_CONFIRMATION}'",
        }

    config_dir = _get_config_dir()
    if not config_dir.exists():
        return {"ok": False, "error": f"CONFIG_DIR no existe: {config_dir}"}

    backup_dir = _get_backup_dir(config_dir) if output_dir is None else Path(output_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "_with_secrets" if include_secrets else ""
    archive_name = f"{prefijo}{ts}{suffix}.tar.gz"
    archive_path = backup_dir / archive_name
    # Se escribe aparte y se renombra al acabar: una a medias no puede pasar por buena ni contar en la retención.
    parcial = backup_dir / f".{archive_name}.part"

    # "backups": no meter copias dentro de copias.
    #
    # "Uploadrr/uploads": ahí viven los enlaces de lo que se está subiendo o
    # sembrando (services/carpeta_subida.py). Con enlaces SIMBÓLICOS entrarían
    # como enlaces rotos, inútiles; con enlaces DUROS —que es lo que se usa
    # cuando /config y los medios comparten sistema de ficheros— tar no los
    # distingue de un fichero normal y metería CADA PELÍCULA ENTERA en la copia
    # (comprobado: 5 MB de película en una copia que debía pesar 1 KB). Una
    # copia de configuración no puede engordar con el catálogo.
    try:
        with _abrir_0600(parcial) as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
            for item in sorted(config_dir.rglob("*")):
                rel = item.relative_to(config_dir)
                if _fuera_de_la_copia(rel.parts, include_secrets):
                    continue
                # La base antes que is_file(): si desaparece por el camino, la copia falla.
                if item.name == _DB_NAME:
                    _add_db_snapshot(tar, item, str(rel), include_secrets=include_secrets)
                    continue
                if not item.is_file() or item.name in (f"{_DB_NAME}-wal", f"{_DB_NAME}-shm"):
                    continue
                try:
                    tar.add(item, arcname=str(rel))
                except FileNotFoundError:
                    # Un temporal que desaparece entre listarlo y copiarlo.
                    continue
        os.replace(parcial, archive_path)
    finally:
        parcial.unlink(missing_ok=True)

    size = archive_path.stat().st_size
    return {
        "ok": True,
        "archive_path": str(archive_path),
        "archive_name": archive_name,
        "size_bytes": size,
        "include_secrets": include_secrets,
        "config_dir": str(config_dir),
    }


def list_config_backups(directory: Path | None = None) -> list[dict]:
    """Lists existing backup archives, newest first."""
    config_dir = _get_config_dir()
    backup_dir = directory if directory is not None else _get_backup_dir(config_dir)
    if not backup_dir.exists():
        return []
    backups = []
    for f in sorted(backup_dir.glob("emuwarez_config_*.tar.gz"), reverse=True):
        stat = f.stat()
        backups.append({
            "name": f.name,
            "path": str(f),
            "size_bytes": stat.st_size,
            "created_at": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "tipo": _tipo_de_copia(f.name),
            # Una subida se guarda con nombre propio sin mirar qué lleva: puede traer claves (None = no se sabe).
            "con_claves": None if _tipo_de_copia(f.name) == "subida" else f.name.endswith("_with_secrets.tar.gz"),
        })
    return backups


def resolver_copia(nombre: str) -> Path | None:
    """La copia con ese nombre dentro de get_backup_dir(), o None si no es una de las que lista
    list_config_backups. La API no acepta rutas: el patrón no admite ni `/` ni `.` fuera de la
    extensión, así que no lee ningún tar de fuera de esa carpeta."""
    if not _NOMBRE_COPIA.match(nombre):
        return None
    ruta = get_backup_dir() / nombre
    # is_file() sigue los enlaces: un enlace con nombre de copia llevaría a un tar de fuera.
    return ruta if ruta.is_file() and not ruta.is_symlink() else None


def _motivo_de_rechazo(tar: tarfile.TarFile, config_dir: Path) -> str | None:
    """Por qué no se puede restaurar esta copia, o None si se puede: algún miembro se saldría de
    config_dir (el filtro "data" de tarfile) o su base es de un esquema más nuevo que este código.
    Se mira antes de tocar nada: la base se coloca primero y un rechazo a medias dejaría la
    instalación a medio restaurar."""
    miembros = tar.getmembers()
    for miembro in miembros:
        try:
            tarfile.data_filter(miembro, str(config_dir))
        except tarfile.FilterError as exc:
            return f"La copia no es segura ({miembro.name}): {exc}"
        # Solo nombres canónicos y relativos: «./emuwarez_uploadrr.db», «x/../emuwarez_uploadrr.db» o
        # «/emuwarez_uploadrr.db» (el filtro le quita la barra) son la base para la extracción pero no para
        # las comprobaciones de abajo, que la buscan por su nombre.
        if miembro.name != posixpath.normpath(miembro.name) or miembro.name.startswith("/"):
            return f"La copia no es válida: nombre no canónico «{miembro.name}»"
    # Lo que el filtro no ve porque mira cada miembro por separado: un nombre repetido (la segunda base se
    # colocaría sin haberse mirado), la base como algo que no es un fichero, o un fichero que es a la vez carpeta
    # (la extracción fallaría con la base ya cambiada). Las copias de la aplicación nunca lo tienen.
    nombres = [posixpath.normpath(m.name) for m in miembros]
    visto: set[str] = set()
    for nombre in nombres:
        if nombre in visto:
            return f"La copia no es válida: repite «{nombre}»"
        visto.add(nombre)
    ficheros = {n for m, n in zip(miembros, nombres) if not m.isdir()}
    for nombre in nombres:
        padre = posixpath.dirname(nombre)
        while padre:
            if padre in ficheros:
                return f"La copia no es válida: «{padre}» es a la vez un fichero y una carpeta"
            padre = posixpath.dirname(padre)
    if any(n == _DB_NAME and not m.isfile() for m, n in zip(miembros, nombres)):
        return "La copia no es válida: su base de datos no es un fichero normal"
    base = next((m for m in miembros if m.name == _DB_NAME), None)
    if base is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / _DB_NAME
        with tar.extractfile(base) as origen, open(ruta, "wb") as destino:
            shutil.copyfileobj(origen, destino)
        version = _version_de_base(ruta)
        base_de_la_copia_es_sqlite = _es_sqlite(ruta)
        legible = _base_legible(ruta) if base_de_la_copia_es_sqlite else True
    if not legible:
        # La cabecera dice SQLite pero no se puede leer (truncada o dañada): pondría una base rota.
        return "La copia no es válida: su base de datos está dañada o incompleta"
    viva = config_dir / _DB_NAME
    if not base_de_la_copia_es_sqlite and viva.exists() and viva.stat().st_size > 0 and _es_sqlite(viva):
        # _colocar_base la copiaría en crudo encima de una base que sí lo es, sin copia previa.
        return "La copia no es válida: su base de datos no es una base SQLite y la actual sí"
    if version is not None and version > SCHEMA_VERSION:
        return (
            f"La copia es de una versión más nueva (esquema {version}) que esta aplicación "
            f"(esquema {SCHEMA_VERSION}): restaurarla dejaría la aplicación sin arrancar. "
            "Actualiza la imagen y restaura entonces."
        )
    return None


def _base_legible(ruta: Path) -> bool:
    """Si SQLite puede leer la base entera (PRAGMA quick_check sin errores)."""
    conn = sqlite3.connect(str(ruta))
    try:
        return conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


def _version_de_base(ruta: Path) -> int | None:
    """El esquema de una base, o None si no es SQLite o no lo dice."""
    conn = sqlite3.connect(str(ruta))
    try:
        return _version_guardada(conn)
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()


def _tipo_de_copia(nombre: str) -> str:
    if nombre.startswith(PREFIJO_AUTOMATICA):
        return "automatica"
    if nombre.startswith(PREFIJO_ANTES_DE_RESTAURAR):
        return "antes_de_restaurar"
    return "subida" if nombre.startswith(PREFIJO_SUBIDA) else "manual"


def _retirar_antiguas(carpeta: Path, prefijo: str, conservar: int, salvo: tuple[str, ...] = ()) -> list[str]:
    """Borra las copias con ese prefijo más antiguas y deja las `conservar` últimas (mínimo 1), sin contar `salvo`."""
    # El nombre lleva la fecha: ordena como el tiempo aunque el fichero se haya copiado o tocado.
    copias = sorted(c for c in carpeta.glob(f"{prefijo}*.tar.gz") if c.name not in salvo)
    retiradas = []
    for vieja in copias[:max(0, len(copias) - max(1, conservar))]:
        vieja.unlink(missing_ok=True)
        retiradas.append(vieja.name)
    return retiradas


def retirar_copias_automaticas(conservar: int, directorio: Path | None = None) -> list[str]:
    """Borra las copias automáticas más antiguas y deja las `conservar` últimas (mínimo 1).
    Las manuales, las subidas, las de antes de restaurar y las pre-migracion-*.db no se tocan."""
    return _retirar_antiguas(directorio if directorio is not None else get_backup_dir(), PREFIJO_AUTOMATICA, conservar)


def crear_copia_automatica(*, conservar: int, incluir_claves: bool = False, output_dir: Path | None = None) -> dict:
    """La copia de la tarea programada y, si salió bien, la retención. Sin claves salvo que se pida:
    con ellas sigue el camino de la copia manual con claves (nombre `_with_secrets`, archivo 0600)."""
    resultado = create_config_backup(
        output_dir=output_dir, prefijo=PREFIJO_AUTOMATICA, include_secrets=incluir_claves,
        confirmation=RESTORE_CONFIRMATION if incluir_claves else None,
    )
    if resultado["ok"]:
        resultado["retiradas"] = retirar_copias_automaticas(conservar, Path(resultado["archive_path"]).parent)
    return resultado


def _motivo_de_copia_invalida(ruta: Path) -> str | None:
    """Por qué no se queda una copia subida. Pasa las mismas comprobaciones que la restauración
    (_motivo_de_rechazo: filtro "data", nombres canónicos, base de un esquema que este código abre),
    para no guardar en backups/ algo que luego no se podría restaurar."""
    try:
        with tarfile.open(ruta, "r:gz") as tar:
            if _DB_NAME not in tar.getnames():
                return "El archivo no es un backup de esta aplicación: no lleva la base de datos"
            return _motivo_de_rechazo(tar, _get_config_dir())
    except _TAR_ILEGIBLE:
        return "El archivo no es un backup (.tar.gz) válido"


def barrer_copias_a_medias() -> int:
    """Al arrancar: los .part de las copias y subidas que se cortaron con la aplicación (pueden llevar claves)."""
    restos = list(get_backup_dir().glob(".*.part"))
    for resto in restos:
        resto.unlink(missing_ok=True)
    return len(restos)


def avisar_migracion_sin_copia(conn: sqlite3.Connection) -> bool:
    """Al arrancar: si init_db actualizó la base sin poder copiarla antes, lo dice en la campana, una vez."""
    version = get_state(conn, "migracion_sin_copia")
    if not version:
        return False
    from .notification_center import emit
    conn.execute("DELETE FROM app_state WHERE key = 'migracion_sin_copia'")
    # Fuera de EVENT_TYPES a propósito, como backup_failed: la campana lo guarda aunque el usuario cerrara su lista.
    emit(conn, "migration_without_backup", "La base de datos se actualizó sin copia previa",
         f"No se pudo guardar la copia de antes de actualizar (esquema {version} → {SCHEMA_VERSION}); comprueba el "
         "espacio libre en el disco. Para volver a la versión anterior solo vale una copia de seguridad de antes.",
         level="warning")
    return True


class CopiaSubida:
    """Una copia que llega del navegador a trozos. Va a un temporal y solo se queda, con nombre propio
    en backups/, si es un .tar.gz con la base de datos dentro. Restaurarla es cosa de restore_config_backup."""

    def __init__(self) -> None:
        self._carpeta = get_backup_dir()
        self._temporal = self._carpeta / f".subida_{secrets.token_hex(6)}.part"
        self._fichero = _abrir_0600(self._temporal)
        self._bytes = 0

    def escribir(self, trozo: bytes) -> None:
        self._bytes += len(trozo)
        if self._bytes > LIMITE_SUBIDA_BYTES:
            raise ValueError(f"El archivo pasa de {LIMITE_SUBIDA_BYTES // 1024 ** 2} MB")
        self._fichero.write(trozo)

    def terminar(self) -> dict:
        self._fichero.close()
        motivo = _motivo_de_copia_invalida(self._temporal)
        if motivo:
            raise ValueError(motivo)
        nombre = f"{PREFIJO_SUBIDA}{datetime.datetime.now():%Y%m%d_%H%M%S}.tar.gz"
        os.replace(self._temporal, self._carpeta / nombre)
        return {"ok": True, "name": nombre, "size_bytes": self._bytes}

    def descartar(self) -> None:
        """Siempre al final: borra el temporal si la copia no llegó a quedarse."""
        self._fichero.close()
        self._temporal.unlink(missing_ok=True)


def restore_config_backup(
    backup_path: str,
    *,
    dry_run: bool = True,
    apply: bool = False,
    confirmation: str | None = None,
) -> dict:
    """Restores a backup archive to CONFIG_DIR.

    dry_run=True (default) previews only.
    apply=True requires confirmation=RESTAURAR_CONFIG and dry_run=False.
    Keys the backup doesn't carry (empty in its database, files not in it)
    stay as they are in the current install, except in a PREFIJO_ANTES_DE_RESTAURAR copy. Before applying, the current
    CONFIG_DIR goes to a PREFIJO_ANTES_DE_RESTAURAR copy with keys; without it nothing is restored.
    """
    path = Path(backup_path)
    if not path.exists():
        return {"ok": False, "dry_run": dry_run, "error": f"Archivo no encontrado: {backup_path}"}

    do_apply = apply and not dry_run
    if do_apply and confirmation != RESTORE_CONFIRMATION:
        return {
            "ok": False,
            "dry_run": dry_run,
            "error": f"Confirmación requerida: pasa confirmation='{RESTORE_CONFIRMATION}'",
        }

    config_dir = _get_config_dir()

    try:
        with tarfile.open(str(path), "r:gz") as tar:
            members = tar.getnames()
            motivo = _motivo_de_rechazo(tar, config_dir)
    except _TAR_ILEGIBLE:
        motivo = "El archivo no es un backup (.tar.gz) válido: está incompleto o dañado"
    if motivo:
        return {"ok": False, "dry_run": dry_run, "error": motivo}

    if dry_run or not do_apply:
        return {
            "ok": True,
            "dry_run": True,
            "action": "preview",
            "archive": str(path),
            "target_dir": str(config_dir),
            "files_in_archive": len(members),
            "files": members[:50],
            "message": "Previsualización — no se han restaurado archivos",
        }

    previa = None
    if config_dir.exists():
        # Lo que se va a sobrescribir, con claves: es lo único con lo que se deshace una restauración equivocada.
        try:
            previa = create_config_backup(
                include_secrets=True, confirmation=RESTORE_CONFIRMATION, prefijo=PREFIJO_ANTES_DE_RESTAURAR)
        except (OSError, sqlite3.Error) as exc:
            return {"ok": False, "dry_run": False,
                    "error": f"No se ha restaurado nada: no se pudo guardar antes la configuración actual "
                             f"({texto_de_error(exc)})"}
        _retirar_antiguas(Path(previa["archive_path"]).parent, PREFIJO_ANTES_DE_RESTAURAR,
                          _COPIAS_ANTES_DE_RESTAURAR, salvo=(path.name, previa["archive_name"]))

    # La de antes de restaurar va tal cual: también las claves vacías y los ficheros con claves que no estaban.
    exacta = path.name.startswith(PREFIJO_ANTES_DE_RESTAURAR)
    config_dir.mkdir(parents=True, exist_ok=True)
    conservadas: list[str] = []
    with tarfile.open(str(path), "r:gz") as tar:
        resto = []
        for member in tar.getmembers():
            if member.name != _DB_NAME or not member.isfile():
                resto.append(member)
                continue
            # La base primero: si falla, el resto de ficheros sigue como estaba.
            with tempfile.TemporaryDirectory() as tmp:
                restaurada = Path(tmp) / _DB_NAME
                with tar.extractfile(member) as origen, open(restaurada, "wb") as destino:
                    shutil.copyfileobj(origen, destino)
                conservadas = _colocar_base(restaurada, config_dir / _DB_NAME, fusionar=not exacta)
        tar.extractall(path=config_dir, members=resto, filter="data")
    if exacta:
        _quitar_claves_que_no_estaban(config_dir, set(members))
    if (config_dir / _DB_NAME).exists() and _es_sqlite(config_dir / _DB_NAME):
        # Una copia de un esquema anterior se migra aquí, una vez, y no desde cada petición que llegue después.
        preparar_base(config_dir / _DB_NAME)

    message = f"Restauración completada: {len(members)} archivos"
    if conservadas:
        message += (
            f". Se han conservado {len(conservadas)} claves de la instalación actual"
            " que el backup no llevaba"
        )
    if previa:
        message += f". Lo que había antes está en {previa['archive_name']}: restáurala para deshacerlo"
    return {
        "ok": True,
        "dry_run": False,
        "action": "restored",
        "archive": str(path),
        "target_dir": str(config_dir),
        "files_restored": len(members),
        "secrets_kept": conservadas,
        "copia_previa": previa["archive_name"] if previa else None,
        "message": message,
    }


def _quitar_claves_que_no_estaban(config_dir: Path, en_la_copia: set[str]) -> None:
    """Borra los ficheros de _CON_CLAVES que la copia no lleva: en una copia con claves, es que no estaban."""
    for partes in _CON_CLAVES:
        ruta = config_dir.joinpath(*partes)
        # La copia no entra por un enlace: lo que hay detrás no falta, no lo vio.
        if any(config_dir.joinpath(*partes[:i]).is_symlink() for i in range(1, len(partes) + 1)):
            continue
        for f in ruta.rglob("*") if ruta.is_dir() else [ruta]:
            if f.is_file() and f.relative_to(config_dir).as_posix() not in en_la_copia:
                f.unlink()


def _canonica(key: str) -> str:
    """La grafía con punto de un alias antiguo (tmdb_api_key -> tmdb.api_key)."""
    return next((punto for punto, alias in CANONICAL_SETTING_ALIASES.items() if alias == key), key)


def _conservar_claves(restaurada: Path, viva: Path) -> list[str]:
    """Pone en la base restaurada las claves de la instalación actual que la
    copia no trae, y devuelve cuáles.

    Una copia normal las lleva vacías (_vaciar_claves): restaurarla tal cual
    dejaría la instalación sin ninguna. Lo que la copia sí trae manda.
    """
    actual = sqlite3.connect(str(viva), timeout=30)
    actual.row_factory = sqlite3.Row
    try:
        ajustes = {k: v for k, v in all_settings(actual).items() if v and es_secreto(k)}
        try:
            instancias = {
                (r["type"], r["name"]): r["api_key"]
                for r in actual.execute("SELECT type, name, api_key FROM instances")
                if r["api_key"]
            }
        except sqlite3.OperationalError:
            instancias = {}
    finally:
        actual.close()

    conservadas: list[str] = []
    conn = sqlite3.connect(str(restaurada))
    conn.row_factory = sqlite3.Row
    try:
        # Por la grafía con punto: si la copia trae el alias antiguo con valor,
        # la clave está, y ponerle al lado la actual la taparía. Se decide
        # antes de escribir nada, o la clave con punto recién puesta haría
        # creer que su alias también venía en la copia.
        faltan = [k for k in ajustes if not get_setting_canonical(conn, _canonica(k))]
        for key in faltan:
            set_setting(conn, key, ajustes[key])
            conservadas.append(key)
        for (tipo, nombre), api_key in instancias.items():
            try:
                cur = conn.execute(
                    "UPDATE instances SET api_key = ? WHERE type = ? AND name = ? AND api_key = ''",
                    (api_key, tipo, nombre),
                )
            except sqlite3.OperationalError:
                break  # una copia sin la tabla: no hay instancias que completar
            if cur.rowcount:
                conservadas.append(f"{tipo}:{nombre}")
        conn.commit()
    finally:
        conn.close()
    return conservadas


def _colocar_base(restaurada: Path, viva: Path, fusionar: bool = True) -> list[str]:
    """Pone la base de la copia en el sitio de la actual, conservando las
    claves que la copia no trae (con fusionar). Devuelve cuáles ha conservado.

    Se coloca con la API de backup de SQLite y no sobrescribiendo el .db: con
    la app en marcha, junto a la base vive su -wal, y SQLite lo aplicaría
    encima del fichero nuevo.
    """
    # Un fichero vacío sí vale: para SQLite es una base sin nada.
    viva_no_es_base = viva.exists() and viva.stat().st_size > 0 and not _es_sqlite(viva)
    if viva_no_es_base or not _es_sqlite(restaurada):
        # La de la copia no es una base (_add_db_snapshot la copió en crudo),
        # o la actual no lo es: no hay nada que fusionar, va tal cual.
        shutil.copyfile(restaurada, viva)
        olvidar_esquema()
        return []
    conservadas = _conservar_claves(restaurada, viva) if fusionar and viva.exists() else []
    src = sqlite3.connect(str(restaurada))
    dst = sqlite3.connect(str(viva), timeout=30)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
        olvidar_esquema()  # la restaurada puede ser de otra versión de esquema
    return conservadas
