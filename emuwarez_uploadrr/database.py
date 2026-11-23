"""SQLite persistence shared by the CLI and the future web backend."""
from __future__ import annotations

import contextlib
import datetime
import functools
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from .constants import CONFIG_DIR

logger = logging.getLogger(__name__)

DB_FILE = CONFIG_DIR / "emuwarez_uploadrr.db"

SCHEMA_VERSION = 63

# Fase A del plan multitracker: el tracker de las filas de antes de que hubiera varios. Es `services.trackers.POR_DEFECTO`
# (aquí en literal para que la base no importe servicios; test_tracker_en_la_base.py comprueba que coinciden).
TRACKER_POR_DEFECTO = "emu"

# Phase 10E: canonical settings namespace fix.
#
# The setup wizard / UI (web/services/setup_service.py) writes dotted keys
# (uploadrr.path, tmdb.api_key, emu.api_key). Older code paths (and several
# existing test fixtures) read/write the legacy underscore form
# (uploadrr_path, tmdb_api_key, emu_api_key). Both forms can coexist in the
# `settings` table; the dotted key always wins when both are present.
CANONICAL_SETTING_ALIASES: dict[str, str] = {
    "uploadrr.path": "uploadrr_path",
    "uploadrr.python_path": "uploadrr_python_path",
    "uploadrr.runtime_type": "uploadrr_runtime_type",
    "uploadrr.timeout": "uploadrr_timeout",
    "uploadrr.allowed_roots": "uploadrr_allowed_roots",
    "tmdb.api_key": "tmdb_api_key",
    "emu.api_key": "emu_api_key",
}


def db_path() -> Path:
    """Return the active database path, respecting CONFIG_DIR env var at call time."""
    env_config = os.environ.get("CONFIG_DIR")
    base = Path(env_config) if env_config else CONFIG_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base / "emuwarez_uploadrr.db"


class BaseMasNuevaError(RuntimeError):
    """La base la dejó una versión más nueva de la aplicación; este código no puede abrirla."""


class _Conexion(sqlite3.Connection):
    """La conexión recuerda su fichero, para que init_db sepa si ya lo comprobó."""
    ruta: Optional[str] = None


# Ficheros cuyo esquema ya se comprobó (y migró) al arrancar este proceso.
_esquema_listo: set[str] = set()
# Una sola cascada a la vez en el proceso: tras restaurar una copia vieja, cada hilo de petición empezaba la suya.
_cascada = threading.Lock()


def connect(path: Optional[Path] = None, *, entre_hilos: bool = False) -> sqlite3.Connection:
    ruta = str(path or db_path())
    conn = sqlite3.connect(ruta, factory=_Conexion, check_same_thread=not entre_hilos)
    conn.ruta = ruta
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # Concurrent requests each open their own connection (see require_auth,
    # every endpoint's _get_conn()). Without this, two connections racing to
    # write at the same moment (e.g. a table-rebuild migration) get an
    # immediate "database is locked"/"table already exists" error instead of
    # one simply waiting its turn.
    conn.execute("PRAGMA busy_timeout = 5000")
    # En WAL, NORMAL no corrompe la base: un corte de luz pierde como mucho los últimos commits, y no hace un fsync
    # por commit (la sincronización hace uno por fila).
    conn.execute("PRAGMA synchronous = NORMAL")
    # El -wal vuelve a 64 MB tras cada checkpoint, en vez de quedarse del tamaño de la mayor escritura.
    conn.execute("PRAGMA journal_size_limit = 67108864")
    # El SQLite de Debian llena de ceros, y escribe en el WAL, cada página que se libera. FAST solo borra lo que se
    # escribe igualmente; las copias sin claves ya pasan por VACUUM.
    conn.execute("PRAGMA secure_delete = FAST")
    conn.create_collation("TITLE_SORT", _title_sort_collation)
    return conn


@contextlib.contextmanager
def db(path: Optional[Path] = None, *, entre_hilos: bool = False) -> Iterator[sqlite3.Connection]:
    """Una conexión lista: confirma al salir bien, deshace si falla y siempre
    cierra (el `with connect()` de sqlite3 solo confirma o deshace).
    `entre_hilos` para la que FastAPI abre en un hilo y cierra en otro."""
    conn = connect(path, entre_hilos=entre_hilos)
    try:
        init_db(conn)
        yield conn
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except sqlite3.Error:
            # Si deshacer también falla, que no tape el error de verdad.
            logger.warning("No se pudo deshacer la transacción", exc_info=True)
        raise
    finally:
        conn.close()


def preparar_base(path: Optional[Path] = None) -> None:
    """Comprueba y migra la base una sola vez, al arrancar. A partir de ahí
    init_db no vuelve a mirarla en este proceso (hasta `olvidar_esquema`)."""
    ruta = str(path or db_path())
    _esquema_listo.discard(ruta)
    conn = connect(ruta)
    try:
        init_db(conn)
    finally:
        conn.close()
    _esquema_listo.add(ruta)


def olvidar_esquema() -> None:
    """Tras restaurar una copia la base puede ser de otra versión: la próxima
    init_db vuelve a comprobarla."""
    _esquema_listo.clear()


@functools.lru_cache(maxsize=131072)
def _title_sort_key(s: Optional[str]) -> str:
    """Sort key for library titles: símbolos < números < letras, con los
    acentos plegados a su vocal base (Émesis ordena junto a las «E», no al
    final). NOCASE de SQLite solo ignora mayúsculas, no acentos, y deja los
    caracteres de código alto (á, ¡, ¿) fuera de sitio. En caché porque la
    intercalación la pide en cada comparación: ordenar la biblioteca entera
    eran cientos de miles de llamadas por título."""
    import unicodedata
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    out: list[str] = []
    for c in s:
        # Category prefix per character so the ordering symbol<number<letter
        # holds at every position, not just the first char.
        cat = "2" if c.isalpha() else "1" if c.isdigit() else "0"
        out.append(cat)
        out.append(c)
    return "".join(out)


def _title_sort_collation(a: str, b: str) -> int:
    ka, kb = _title_sort_key(a), _title_sort_key(b)
    return (ka > kb) - (ka < kb)


def _anadir_columna(conn: sqlite3.Connection, tabla: str, columna: str, definicion: str) -> None:
    """ALTER TABLE ADD COLUMN de una migración. Solo calla si la columna ya está, no un «database is locked»: con ese
    callado la versión se guardaba igual y la columna faltaba para siempre."""
    try:
        conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")
    except sqlite3.OperationalError:
        if columna not in {fila[1] for fila in conn.execute(f"PRAGMA table_info({tabla})")}:
            raise


def _migrate_instances(conn: sqlite3.Connection) -> None:
    """Add columns to instances that were not present in schema v2."""
    new_cols = [
        ("is_4k", "INTEGER NOT NULL DEFAULT 0"),
        ("is_anime", "INTEGER NOT NULL DEFAULT 0"),
        ("last_test_status", "TEXT"),
        ("last_test_at", "TEXT"),
    ]
    for col, col_def in new_cols:
        _anadir_columna(conn, "instances", col, col_def)


def _rebuild_instances_table(
    conn: sqlite3.Connection, type_check_values: str, unique_clause: str = "UNIQUE(type, name)"
) -> None:
    """Shared table-rebuild step for the widen/narrow/unique-name migrations
    below. Caller is responsible for the transaction (BEGIN/COMMIT/ROLLBACK)
    and the idempotency guard — this just issues the DDL/DML statements one
    at a time (never `executescript()`, which issues its own implicit commit
    and would break a caller-managed transaction)."""
    # El contador de AUTOINCREMENT, no el id más alto que quede: el de una instancia borrada no se reutiliza.
    contador = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'instances'").fetchone()
    conn.execute(f"""
        CREATE TABLE instances_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL CHECK(type IN ({type_check_values})),
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            api_key TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            is_4k INTEGER NOT NULL DEFAULT 0,
            is_anime INTEGER NOT NULL DEFAULT 0,
            last_test_status TEXT,
            last_test_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            {unique_clause}
        )
    """)
    conn.execute("""
        INSERT INTO instances_new (
            id, type, name, url, api_key, enabled, is_4k, is_anime,
            last_test_status, last_test_at, created_at, updated_at
        )
        SELECT
            id, type, name, url, api_key, enabled, is_4k, is_anime,
            last_test_status, last_test_at, created_at, updated_at
        FROM instances
    """)
    conn.execute("DROP TABLE instances")
    conn.execute("ALTER TABLE instances_new RENAME TO instances")
    if contador:
        if not conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'instances'",
                            (contador[0],)).rowcount:
            conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('instances', ?)", (contador[0],))


def _migrate_instances_widen_type_check(conn: sqlite3.Connection) -> None:
    """Widen instances.type CHECK to include 'seerr' (Phase 11B).

    SQLite cannot ALTER a CHECK constraint directly — this is the first
    table-rebuild migration in the project: create a new table with the
    wider constraint, copy all rows over an explicit column list (never
    `SELECT *`, to stay correct regardless of physical column order after
    years of ALTER TABLE ADD COLUMN), drop the old table, rename. Idempotent:
    skipped if the constraint already includes 'seerr'. No FK/index cleanup
    needed — `instances` has no FKs pointing to it and its only index is the
    UNIQUE(type, name) constraint, recreated verbatim on the new table.

    Concurrency: every request opens its own connection (require_auth, each
    endpoint's _get_conn()), and this runs on every one of them via
    init_db(). Without an explicit lock, two connections can both pass the
    guard check before either finishes rebuilding, and the second's
    `CREATE TABLE instances_new` fails with "table already exists" — caught
    nowhere, which for require_auth meant a live 401 on a perfectly valid
    token. BEGIN IMMEDIATE + the busy_timeout set in connect() serialize
    this: the second caller waits for the lock, then re-checks the guard
    (already migrated) and no-ops.
    """
    prior_isolation = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'instances'"
        ).fetchone()
        # Con UNIQUE(name) la tabla ya pasó por las tres reconstrucciones: sin esto se repetían en cada salto de
        # esquema, y cada una dejaba el AUTOINCREMENT en el id más alto que quedaba (un id borrado se reutilizaba).
        if not row or not row["sql"] or "'seerr'" in row["sql"] or "UNIQUE(name)" in row["sql"]:
            conn.execute("COMMIT")
            return
        _rebuild_instances_table(conn, "'radarr','sonarr','seerr'")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.isolation_level = prior_isolation


def _migrate_instances_narrow_type_check(conn: sqlite3.Connection) -> None:
    """Narrow instances.type CHECK back to ('radarr','sonarr'), removing
    'seerr' (Seerr integration removed — it never had a working request
    sync, see removal notes for this phase).

    Deletes any existing 'seerr' rows first (their request-sync was never
    implemented, so there is no real data to preserve) and the now-orphaned
    scheduled task row, then rebuilds the table with the narrower CHECK —
    same table-rebuild pattern as `_migrate_instances_widen_type_check`,
    including the same BEGIN IMMEDIATE locking for the same concurrency
    reason (see that function's docstring). Idempotent: skipped if the
    constraint no longer contains 'seerr'.
    """
    prior_isolation = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'instances'"
        ).fetchone()
        if not row or not row["sql"] or "'seerr'" not in row["sql"]:
            conn.execute("COMMIT")
            return
        conn.execute("DELETE FROM instances WHERE type = 'seerr'")
        conn.execute("DELETE FROM scheduled_tasks WHERE task_key = 'seerr_requests_sync'")
        _rebuild_instances_table(conn, "'radarr','sonarr'")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.isolation_level = prior_isolation


def _migrate_instances_unique_name(conn: sqlite3.Connection) -> None:
    """Make instances.name globally unique (was UNIQUE(type, name) — two
    instances of different types could share a name, e.g. a Radarr and a
    Sonarr both named "Main"). Requested so the Biblioteca origin filter and
    Radarr/Sonarr deep links can identify an instance by name unambiguously.

    Same BEGIN IMMEDIATE + table-rebuild pattern as
    _migrate_instances_widen_type_check/_migrate_instances_narrow_type_check
    (SQLite can't ALTER a table-level UNIQUE constraint directly).

    Nothing before this migration stopped two different-typed instances
    from sharing a name, so real conflicts must be resolved before the
    rebuild, not just prevented going forward — `init_db()` must always
    converge, never fail startup over pre-existing data:
      1. Keep the lowest-id (earliest-created) row's name unchanged.
      2. Every other row sharing that name is renamed to "{name} ({type})".
      3. If that disambiguated name is itself already taken (pathological
         case — matches an already-decided name), append " #{id}" as a
         final, deterministic tie-breaker (id is unique by construction).
    Every rename is logged so an operator can see what happened.
    """
    prior_isolation = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'instances'"
        ).fetchone()
        if not row or not row["sql"] or "UNIQUE(name)" in row["sql"]:
            conn.execute("COMMIT")
            return

        rows = conn.execute("SELECT id, type, name FROM instances ORDER BY id").fetchall()
        claimed: dict[str, int] = {}
        for r in rows:
            name = r["name"]
            if name not in claimed:
                claimed[name] = r["id"]
                continue
            new_name = f"{name} ({r['type']})"
            if new_name in claimed:
                new_name = f"{new_name} #{r['id']}"
            claimed[new_name] = r["id"]
            conn.execute("UPDATE instances SET name = ? WHERE id = ?", (new_name, r["id"]))
            logger.warning(
                "Migración de nombre único: renombrada instancia #%s (%s) de '%s' a '%s' "
                "por conflicto con otra instancia de distinto tipo.",
                r["id"], r["type"], name, new_name,
            )

        _rebuild_instances_table(conn, "'radarr','sonarr'", unique_clause="UNIQUE(name)")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.isolation_level = prior_isolation


def _migrate_instances_carpeta(conn: sqlite3.Connection) -> None:
    """Admite el tipo 'carpeta' (carpeta manual: `url` es su ruta, sin clave). Va después de las tres reconstrucciones
    de arriba, que la dejan en ('radarr','sonarr') con UNIQUE(name); el mismo BEGIN IMMEDIATE que ellas."""
    prior_isolation = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'instances'"
        ).fetchone()
        if not row or not row["sql"] or "'carpeta'" in row["sql"]:
            conn.execute("COMMIT")
            return
        _rebuild_instances_table(conn, "'radarr','sonarr','carpeta'", unique_clause="UNIQUE(name)")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.isolation_level = prior_isolation


def _migrate_nomenclature(conn: sqlite3.Connection) -> None:
    """Create nomenclature_proposals table (Phase 6A) and add Phase 6B columns."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nomenclature_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            library_item_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            current_name TEXT,
            current_path TEXT,
            proposed_title TEXT,
            proposed_filename TEXT,
            proposed_folder TEXT,
            proposed_path TEXT,
            media_type TEXT,
            tmdb_id TEXT,
            imdb_id TEXT,
            resolution TEXT,
            source TEXT,
            video_codec TEXT,
            audio_summary TEXT,
            subtitle_summary TEXT,
            release_tag TEXT,
            warnings_json TEXT,
            errors_json TEXT,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Phase 6B columns (idempotent)
    for col, col_def in [
        ("applied_path", "TEXT"),
        ("applied_at", "TEXT"),
        ("operation_id", "INTEGER"),
    ]:
        _anadir_columna(conn, "nomenclature_proposals", col, col_def)


def _migrate_filesystem_operations(conn: sqlite3.Connection) -> None:
    """Create filesystem_operations table (Phase 6B)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS filesystem_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'planned',
            library_item_id INTEGER,
            proposal_id INTEGER,
            upload_job_id INTEGER,
            source_path TEXT,
            destination_path TEXT,
            base_path TEXT,
            dry_run INTEGER NOT NULL DEFAULT 1,
            warnings_json TEXT,
            errors_json TEXT,
            result_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            applied_at TEXT
        )
    """)


def _migrate_library_items_phase6b(conn: sqlite3.Connection) -> None:
    """Add Phase 6B columns to library_items."""
    for col, col_def in [
        ("prepared_path", "TEXT"),
        ("nomenclature_applied_at", "TEXT"),
        # Audio channels (e.g. "5.1") and HDR/DV range, from Radarr/Sonarr
        # mediaInfo — needed to build the eMuwarez audio/HDR segments.
        ("audio_channels", "TEXT"),
        ("dynamic_range", "TEXT"),
    ]:
        _anadir_columna(conn, "library_items", col, col_def)


def _migrate_upload_jobs(conn: sqlite3.Connection) -> None:
    """Add Phase 6A columns to upload_jobs."""
    new_cols = [
        ("request_item_id", "INTEGER"),
        ("stage", "TEXT"),
        ("source_path", "TEXT"),
        ("prepared_path", "TEXT"),
        ("uploadrr_command_json", "TEXT"),
        ("preflight_json", "TEXT"),
        ("progress_json", "TEXT"),
        ("error_message", "TEXT"),
        ("started_at", "TEXT"),
        ("finished_at", "TEXT"),
    ]
    for col, col_def in new_cols:
        _anadir_columna(conn, "upload_jobs", col, col_def)


def _migrate_upload_jobs_phase6c(conn: sqlite3.Connection) -> None:
    """Add Phase 6C columns to upload_jobs."""
    new_cols = [
        ("exit_code", "INTEGER"),
        ("process_id", "INTEGER"),
    ]
    for col, col_def in new_cols:
        _anadir_columna(conn, "upload_jobs", col, col_def)


def _migrate_upload_job_logs(conn: sqlite3.Connection) -> None:
    """Create upload_job_logs table (Phase 6C) and add Phase 6D columns."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS upload_job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_job_id INTEGER NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            stream TEXT,
            created_at TEXT NOT NULL,
            raw_json TEXT,
            FOREIGN KEY (upload_job_id) REFERENCES upload_jobs(id)
        )
    """)
    # Phase 6D columns (idempotent)
    for col, col_def in [
        ("sequence", "INTEGER"),
        ("is_secret_redacted", "INTEGER NOT NULL DEFAULT 1"),
    ]:
        _anadir_columna(conn, "upload_job_logs", col, col_def)


def _migrate_upload_jobs_phase6d(conn: sqlite3.Connection) -> None:
    """Add Phase 6D columns to upload_jobs and create upload_artifacts table."""
    new_cols = [
        ("result_url", "TEXT"),
        ("result_torrent_id", "TEXT"),
        ("result_tracker", "TEXT"),
        ("result_category", "TEXT"),
        ("duration_seconds", "INTEGER"),
        ("retry_of_job_id", "INTEGER"),
        ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
        ("last_log_at", "TEXT"),
        ("last_error_at", "TEXT"),
        ("last_success_at", "TEXT"),
        ("safe_command_json", "TEXT"),
    ]
    for col, col_def in new_cols:
        _anadir_columna(conn, "upload_jobs", col, col_def)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS upload_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_job_id INTEGER NOT NULL,
            artifact_type TEXT NOT NULL,
            name TEXT,
            path TEXT,
            url TEXT,
            value TEXT,
            created_at TEXT NOT NULL,
            raw_json TEXT,
            FOREIGN KEY (upload_job_id) REFERENCES upload_jobs(id)
        )
    """)


def _migrate_upload_jobs_queue(conn: sqlite3.Connection) -> None:
    """Add queued_until to upload_jobs — the queued (grace-period) job created
    the instant the user clicks "Subir a eMuwarez" carries this so the UI can
    show a countdown and a Cancel button before the server actually starts
    renaming/remuxing/uploading."""
    _anadir_columna(conn, "upload_jobs", "queued_until", "TEXT")


def _migrate_upload_jobs_snapshots(conn: sqlite3.Connection) -> None:
    """Add nomenclature/remux snapshot columns to upload_jobs — persisted
    read-only copies of the field breakdown and track plan actually applied,
    so the job detail page can show them after the fact even though the
    live nomenclature/remux services never keep this data themselves."""
    for col, col_def in [
        ("nomenclature_snapshot_json", "TEXT"),
        ("remux_snapshot_json", "TEXT"),
    ]:
        _anadir_columna(conn, "upload_jobs", col, col_def)


def _migrate_upload_jobs_manual_refs(conn: sqlite3.Connection) -> None:
    """Add nomenclature_proposal_id/remux_overrides_json to upload_jobs — for
    a manual upload, these pin the exact nomenclature proposal the user
    confirmed in the wizard (so a retry can reuse it instead of falling back
    to "whichever is latest") and persist the per-track remux overrides
    (previously only kept in the in-memory _PENDING_REMUX_OVERRIDES dict,
    lost on a process restart and unavailable to a retry's new job row).
    An automatic (staging-pipeline) job never sets nomenclature_proposal_id —
    its absence is what queue_retry_for_job() uses to tell manual from
    automatic jobs apart."""
    _anadir_columna(conn, "upload_jobs", "nomenclature_proposal_id", "INTEGER")
    _anadir_columna(conn, "upload_jobs", "remux_overrides_json", "TEXT")


def _migrate_upload_jobs_checks_snapshot(conn: sqlite3.Connection) -> None:
    """Add checks_snapshot_json — the pre-upload checks (renameable/hardlink
    or season_pack, output dir, qBittorrent, image hosts, eMuwarez) result at
    the moment the job was queued. For a manual upload these are the exact
    results the wizard already computed and showed the user before they
    confirmed; run_upload_checks() itself never persists anything, so without
    this the job detail page would have nothing to show for this section."""
    _anadir_columna(conn, "upload_jobs", "checks_snapshot_json", "TEXT")


def _migrate_upload_jobs_season_overrides(conn: sqlite3.Connection) -> None:
    """Add season_overrides_json — Source/Proveedor/Tag edited by hand for a
    season pack in the wizard's editable season fields, applied uniformly
    across every episode. Previously this choice only ever reached the preview
    snapshot: the real apply step (season_pack.apply_season_pack) recomputed
    from scratch with no override at all, silently discarding it."""
    _anadir_columna(conn, "upload_jobs", "season_overrides_json", "TEXT")


def _migrate_upload_jobs_request_snapshot(conn: sqlite3.Connection) -> None:
    """Esquema 41: el título y el enlace de la petición que cubrió la subida,
    copiados al encolarla. La fila de request_items se borra cuando la petición
    deja de estar abierta en eMuwarez, y el historial de Subidas tiene que seguir
    diciendo qué petición cubrió cada subida."""
    for col in ("request_title", "request_url"):
        _anadir_columna(conn, "upload_jobs", col, "TEXT")
    # Las subidas de antes que ya llevaban la petición (CLI, rutas antiguas) la copian mientras aún existe.
    conn.execute(
        "UPDATE upload_jobs SET "
        "request_title = (SELECT title FROM request_items WHERE id = upload_jobs.request_item_id), "
        "request_url = (SELECT url FROM request_items WHERE id = upload_jobs.request_item_id) "
        "WHERE request_item_id IS NOT NULL AND request_title IS NULL"
    )


def _migrate_requests(conn: sqlite3.Connection) -> None:
    """Add Phase 5B columns to request_items and request_matches."""
    for col, col_def in [
        ("reviewed_at", "TEXT"),
        ("last_sync_at", "TEXT"),
        # eMuwarez requests table shows a 👍 "votes" count (how many other users
        # want it) alongside points/comments.
        ("votes", "INTEGER"),
        # Series/documentary scope: whole series ('all'), one season ('season')
        # or a single episode ('episode'), with the season/episode numbers.
        ("req_scope", "TEXT"),
        ("req_season", "INTEGER"),
        ("req_episode", "INTEGER"),
    ]:
        _anadir_columna(conn, "request_items", col, col_def)


def _migrate_request_items_phase8b(conn: sqlite3.Connection) -> None:
    """Add Phase 8B readiness columns to request_items."""
    new_cols = [
        ("readiness_status", "TEXT"),
        ("readiness_action", "TEXT"),
        ("readiness_best_library_item_id", "INTEGER"),
        ("readiness_updated_at", "TEXT"),
    ]
    for col, col_def in new_cols:
        _anadir_columna(conn, "request_items", col, col_def)


def _migrate_drop_priority_columns(conn: sqlite3.Connection) -> None:
    """Remove the numeric "priority" ranking system (score + high/medium/low
    bucket) from request_readiness/request_items —
    the user explicitly asked for no "priority" concept anywhere in
    requests/queue. The 'blocked' classification is KEPT, but it already
    lives on the independent `status` column (e.g. status='file_missing' or
    'import_error' both already imply blocked) — priority_bucket was always a
    redundant derived label on top of that, never the source of truth.

    Plain ALTER TABLE DROP COLUMN (SQLite >= 3.35, confirmed available here)
    is enough — unlike the instances.type migrations, these columns carry no
    CHECK/UNIQUE constraint, so no table-rebuild is needed. SQLite refuses to
    drop a column that still has an index on it, so idx_rr_bucket
    must be dropped first. Idempotent: only what is still there is dropped.
    """
    conn.execute("DROP INDEX IF EXISTS idx_rr_bucket")
    for table, columns in (
        ("request_readiness", ("priority_score", "priority_bucket")),
        ("request_items", ("readiness_priority_score", "readiness_priority_bucket")),
    ):
        presentes = {fila[1] for fila in conn.execute(f"PRAGMA table_info({table})")}
        for col in columns:
            if col in presentes:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {col}")


def _migrate_request_items_extra_ids(conn: sqlite3.Connection) -> None:
    """Add mal_id / tvdb_id to request_items (populated at sync from the eMuwarez
    request when present — mal_id mainly for anime requests)."""
    for col in ("mal_id", "tvdb_id"):
        _anadir_columna(conn, "request_items", col, "TEXT")


def _migrate_request_readiness_tables(conn: sqlite3.Connection) -> None:
    """Create Phase 8B request readiness and candidates tables."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS request_readiness (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_item_id INTEGER NOT NULL UNIQUE,
            best_library_item_id INTEGER,
            status TEXT NOT NULL,
            recommended_action TEXT,
            compatibility_score REAL,
            upload_readiness_score REAL,
            blockers_json TEXT,
            reasons_json TEXT,
            candidate_summary_json TEXT,
            last_evaluated_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_rr_request_item ON request_readiness(request_item_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_rr_best_lib ON request_readiness(best_library_item_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_rr_status ON request_readiness(status)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_rr_action ON request_readiness(recommended_action)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS request_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_item_id INTEGER NOT NULL,
            library_item_id INTEGER NOT NULL,
            candidate_rank INTEGER NOT NULL,
            match_status TEXT NOT NULL,
            match_score REAL,
            compatibility_status TEXT,
            compatibility_score REAL,
            readiness_status TEXT,
            readiness_score REAL,
            blockers_json TEXT,
            reasons_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_rc_request ON request_candidates(request_item_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_rc_library ON request_candidates(library_item_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_rc_rank ON request_candidates(candidate_rank)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_rc_readiness ON request_candidates(readiness_status)
    """)


def _migrate_library_items_phase8a(conn: sqlite3.Connection) -> None:
    """Add Phase 8A diagnostic/filesystem columns to library_items."""
    new_cols = [
        ("file_exists", "INTEGER"),
        ("file_is_video", "INTEGER"),
        ("file_size_on_disk", "INTEGER"),
        ("file_verified_at", "TEXT"),
        ("file_verification_error", "TEXT"),
        ("arr_queue_status", "TEXT"),
        ("arr_queue_title", "TEXT"),
        ("arr_queue_progress", "REAL"),
        ("arr_queue_timeleft", "TEXT"),
        ("arr_queue_error", "TEXT"),
        ("arr_history_status", "TEXT"),
        ("arr_history_event_type", "TEXT"),
        ("arr_history_error", "TEXT"),
        ("arr_history_source_title", "TEXT"),
        ("diagnostic_status", "TEXT"),
        ("diagnostic_summary", "TEXT"),
        ("recommended_action", "TEXT"),
        ("recommendation_reasons_json", "TEXT"),
        ("recommendation_updated_at", "TEXT"),
    ]
    for col, col_def in new_cols:
        _anadir_columna(conn, "library_items", col, col_def)


def _migrate_library_items_phase11e(conn: sqlite3.Connection) -> None:
    """Add poster_url to library_items (Phase 11E — grid/poster view)."""
    _anadir_columna(conn, "library_items", "poster_url", "TEXT")


def _migrate_library_items_title_slug(conn: sqlite3.Connection) -> None:
    """Add title_slug to library_items — Sonarr's own web UI deep-link
    convention is /series/{titleSlug} (Radarr's is /movie/{tmdbId}, which
    tmdb_id already covers). Sonarr's raw API response already includes
    titleSlug (saved whole into raw_json since day one), this just gives it
    a dedicated, queryable column like tmdb_id/imdb_id/tvdb_id already have."""
    _anadir_columna(conn, "library_items", "title_slug", "TEXT")


def _migrate_library_items_media_info(conn: sqlite3.Connection) -> None:
    """Add video_codec/audio_codec/audio_languages/subtitle_languages to
    library_items — parsed from Radarr/Sonarr's own movieFile/episodeFile
    `mediaInfo` sub-object (Phase 6A of the nomenclature/MediaInfo batch),
    replacing the old crude guess of video_codec from a substring match
    against the quality profile NAME."""
    new_cols = [
        ("video_codec", "TEXT"),
        ("audio_codec", "TEXT"),
        ("audio_languages", "TEXT"),
        ("subtitle_languages", "TEXT"),
        # Nomenclature strict check (MediaInfo): the expected name and when it
        # was last checked, so the UI can show the diff. Status uses the
        # existing nomenclature_status column ('No comprobada' by default now).
        ("nomenclature_expected", "TEXT"),
        ("nomenclature_checked_at", "TEXT"),
    ]
    for col, col_type in new_cols:
        _anadir_columna(conn, "library_items", col, col_type)

    # Clear stale verdicts from the OLD tag-based heuristic. Nomenclature can
    # now only be judged by reading MediaInfo, so anything the new checker has
    # NOT actually run (nomenclature_checked_at IS NULL) must fall back to the
    # honest default: 'No comprobada' when a file is on disk, 'N/A' otherwise.
    # Idempotent — checked items (checked_at set) are never touched.
    conn.execute(
        "UPDATE library_items SET nomenclature_status = CASE "
        "  WHEN local_status IN ('Disponible','Subido','Subiendo') AND path IS NOT NULL "
        "  THEN 'No comprobada' ELSE 'N/A' END "
        "WHERE nomenclature_checked_at IS NULL "
        "  AND nomenclature_status IS NOT 'No comprobada' "
        "  AND nomenclature_status IS NOT 'N/A'"
    )


def _migrate_library_items_hierarchy(conn: sqlite3.Connection) -> None:
    """Add parent_id/season_number/episode_number to library_items — lets
    episodes point at their season row and seasons at their series row
    (adjacency list, one column reused at both levels), and gives
    season/episode numbers real queryable columns instead of only being
    baked into the display title string. Purely additive."""
    new_cols = [
        ("parent_id", "INTEGER"),
        ("season_number", "INTEGER"),
        ("episode_number", "INTEGER"),
        # Título en inglés resuelto en la sincronización (esquema 37). Las
        # bibliotecas sincronizadas antes lo tienen vacío y el asistente sigue
        # preguntando a TMDb hasta la siguiente sincronización.
        ("english_title", "TEXT"),
    ]
    for col, col_type in new_cols:
        _anadir_columna(conn, "library_items", col, col_type)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_library_parent ON library_items(parent_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_library_tvdb ON library_items(tvdb_id)")


def _migrate_dominio_emu_warez(conn: sqlite3.Connection) -> None:
    """Esquema 39: eMuwarez pasó de emuwarez.com a emu-warez.com, y el viejo ya
    no responde. Los enlaces guardados (torrents subidos, peticiones, avisos,
    ajustes) se reescriben para que sigan abriéndose."""
    for tabla, columna in (
        ("upload_jobs", "result_url"), ("upload_artifacts", "url"), ("upload_artifacts", "value"),
        ("request_items", "url"), ("request_items", "emu_id"), ("notifications", "link"), ("settings", "value"), ("app_state", "value"),
    ):
        try:
            filas = conn.execute(
                f"SELECT rowid, {columna} FROM {tabla} WHERE {columna} LIKE '%//emuwarez.com%'").fetchall()
        except sqlite3.OperationalError:
            continue                    # tabla o columna que esta base aún no tiene
        for rowid, valor in filas:
            # Solo el host entero: emuwarez.com.example.org no es eMuwarez.
            nuevo = re.sub(r"//emuwarez\.com(?![\w.-])", "//emu-warez.com", valor, flags=re.IGNORECASE)
            if nuevo != valor:
                conn.execute(f"UPDATE {tabla} SET {columna} = ? WHERE rowid = ?", (nuevo, rowid))


def _activar_evento_en_la_campana(conn: sqlite3.Connection, evento: str) -> None:
    """Un canal sin configurar recibe todo, así que para una instalación nueva no hace falta nada. Pero quien ya
    guardó su selección de eventos tiene una lista cerrada, y un evento nuevo se quedaría fuera: se le añade, porque
    en la campana va activado por defecto. A los bots NO se les toca: ahí los avisos nuevos son opt-in a propósito."""
    try:
        fila = conn.execute(
            "SELECT value FROM settings WHERE key = 'notify.ui.events'").fetchone()
    except sqlite3.OperationalError:
        return
    if not fila or not fila["value"]:
        return                      # sin configurar lo recibe todo; con la selección vacía, el usuario no quiere ninguno
    codigos = [c.strip() for c in fila["value"].split(",") if c.strip()]
    if evento in codigos:
        return
    codigos.append(evento)
    conn.execute("UPDATE settings SET value = ? WHERE key = 'notify.ui.events'",
                 (",".join(codigos),))


def _migrate_ui_recibe_aviso_de_version(conn: sqlite3.Connection) -> None:
    """Esquema 38: el aviso de versión nueva, activado en la campana."""
    _activar_evento_en_la_campana(conn, "version_available")


def _migrate_tmdb_title_cache(conn: sqlite3.Connection) -> None:
    """Cache of TMDb-resolved titles by (tmdb_id, media_type, language), so the
    Sonarr title localization only hits TMDb once per series instead of on every
    library sync. Purely additive."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tmdb_title_cache (
            tmdb_id TEXT NOT NULL,
            media_type TEXT NOT NULL,      -- 'tv' | 'movie'
            language TEXT NOT NULL,
            title TEXT,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (tmdb_id, media_type, language)
        )
    """)


def _migrate_source_engine_cache(conn: sqlite3.Connection) -> None:
    """Cache for services/source_engine (the multi-tool source detector) —
    keyed by the file's own identity (path+size+mtime) plus the engine's own
    version, so a code/rule change invalidates old rows automatically.
    Purely additive."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_engine_cache (
            path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mtime INTEGER NOT NULL,
            engine_version TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (path, size_bytes, mtime, engine_version)
        )
    """)


def _migrate_library_items_track_remux(conn: sqlite3.Connection) -> None:
    """Track reorder/rename via mkvmerge (Phase 12A) — status of the last
    remux applied to this item's file, so the UI can show a badge without
    re-running mkvmerge -J on every page load. Purely additive."""
    new_cols = [
        ("track_remux_status", "TEXT"),        # 'aplicado' | 'error' | NULL
        ("track_remux_applied_at", "TEXT"),
        ("track_remux_error", "TEXT"),
    ]
    for col, col_type in new_cols:
        _anadir_columna(conn, "library_items", col, col_type)


def _migrate_library_items_mal_id(conn: sqlite3.Connection) -> None:
    """MAL (MyAnimeList) id, resolved from the Fribb/anime-lists table against
    tvdb_id/tmdb_id at upsert time (see services/anime_lists.py) — Radarr/
    Sonarr never provide this themselves. Purely additive."""
    _anadir_columna(conn, "library_items", "mal_id", "TEXT")


def _migrate_nomenclature_torrent_name(conn: sqlite3.Connection) -> None:
    """El título del aporte (con espacios y acentos) aparte del nombre de archivo
    (con puntos): `proposed_filename` guardaba uno u otro según quién escribiera."""
    _anadir_columna(conn, "nomenclature_proposals", "proposed_torrent_name", "TEXT")


def _migrate_library_items_tvmaze_id(conn: sqlite3.Connection) -> None:
    """TVmaze id, que Sonarr ya trae en su payload (`tvMazeId`) y no se estaba
    guardando. 50 de 52 series lo traen con valor.

    No se añade tvRageId, que Sonarr también manda: solo lo trae la mitad y
    sus fichas devuelven 404 (comprobado con dos ids reales), así que el
    enlace no llevaría a ninguna parte. Purely additive."""
    _anadir_columna(conn, "library_items", "tvmaze_id", "TEXT")


def _migrate_library_items_release_group(conn: sqlite3.Connection) -> None:
    """Grupo de release del archivo, el `releaseGroup` que Radarr/Sonarr ya
    mandan en movieFile/episodeFile. Se rellena en la siguiente sincronización.
    Purely additive."""
    _anadir_columna(conn, "library_items", "release_group", "TEXT")


def _migrate_library_items_backdrop_url(conn: sqlite3.Connection) -> None:
    """Add backdrop_url to library_items — the fanart/backdrop counterpart to
    poster_url (Phase 11E), same Radarr/Sonarr `images` array, just
    coverType=fanart instead of poster (see integrations/base.py::
    extract_backdrop_url). Used as the dedicated page's hero background."""
    _anadir_columna(conn, "library_items", "backdrop_url", "TEXT")


def _migrate_library_items_missing_since(conn: sqlite3.Connection) -> None:
    """Add missing_since to library_items — the timestamp of the most recent
    transition INTO local_status='Faltante' (set by upsert_library_item,
    never bumped by a resync that leaves it Faltante). Recomendaciones'
    "Faltante" group used to sort by Radarr/Sonarr's own `added` date (when
    the item was added to Radarr/Sonarr), which can be much older than when
    it actually became a missing/recommendable candidate (e.g. added months
    ago as an unreleased pre-order, only just released) — an item added
    recently for an unrelated reason could then wrongly outrank a genuinely
    newer missing detection."""
    _anadir_columna(conn, "library_items", "missing_since", "TEXT")

    # Backfill for content that was already Faltante before this column
    # existed: added_at (when we first synced/inserted this row) is an
    # imperfect but far better proxy than leaving it NULL, which would keep
    # falling back to Radarr/Sonarr's own "added" date — an unrelated event
    # (see get_dashboard_recommendations). From here on, upsert_library_item
    # keeps it accurate on every real transition.
    #
    # Deliberately NOT gated behind "column didn't already exist" — this ran
    # once already on a deploy that only had the ALTER TABLE above (no
    # backfill yet), so the column already existing here must NOT skip this
    # UPDATE, or anyone who deployed that version first would have their
    # Faltante rows stuck at missing_since=NULL forever. The WHERE clause
    # alone (only rows still NULL) already makes this safe to run on every
    # migration pass.
    conn.execute(
        "UPDATE library_items SET missing_since = added_at "
        "WHERE local_status = 'Faltante' AND missing_since IS NULL"
    )


def _migrate_anime_id_map(conn: sqlite3.Connection) -> None:
    """Local mirror of the Fribb/anime-lists table (tvdb_id/season -> mal_id,
    tmdb movie id -> mal_id) — see services/anime_lists.py. Downloaded/
    refreshed on demand, same "external catalog mirrored into a local table"
    shape as emu_catalog above. Purely additive."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS anime_id_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tvdb_id TEXT,
            season_number INTEGER,
            tmdb_movie_id TEXT,
            mal_id TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_anime_id_map_tvdb ON anime_id_map(tvdb_id, season_number)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_anime_id_map_tmdb_movie ON anime_id_map(tmdb_movie_id)")


def _migrate_emu_catalog(conn: sqlite3.Connection) -> None:
    """eMuwarez torrent catalog index (populated by the on-demand eMuwarez
    sync) + the per-library-item comparison result. Purely additive."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS emu_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            torrent_id TEXT,
            category TEXT,
            tmdb_id TEXT,
            tvdb_id TEXT,
            season_number INTEGER,
            resolution TEXT,
            source TEXT,
            codec TEXT,
            has_spanish INTEGER,          -- 1 yes, 0 no, NULL unknown
            size INTEGER,
            name TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_emu_catalog_tmdb ON emu_catalog(tmdb_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_emu_catalog_tvdb ON emu_catalog(tvdb_id, season_number)")
    # Per-item comparison result (movies + seasons).
    for col, col_def in [
        ("emu_in_catalog", "INTEGER"),   # 1 present, 0 not, NULL not computed
        ("emu_aportes", "TEXT"),          # JSON list of {dim,detail}
    ]:
        _anadir_columna(conn, "library_items", col, col_def)


def _migrate_request_items_posters(conn: sqlite3.Connection) -> None:
    """Esquema 42: el póster y el fondo de TMDb de las peticiones que no casan con nada de la biblioteca,
    de donde salía el póster. '' = TMDb no tiene."""
    for col in ("tmdb_poster_url", "tmdb_backdrop_url"):
        _anadir_columna(conn, "request_items", col, "TEXT")


def _migrate_request_items_last_seen(conn: sqlite3.Connection) -> None:
    """Cuándo vio por última vez la sincronización cada petición: lo que no sale
    en un recorrido completo de eMuwarez es que ya no está abierta, y se borra."""
    _anadir_columna(conn, "request_items", "last_seen_at", "TEXT")


def _migrate_request_items_aviso_pendiente(conn: sqlite3.Connection, activar_evento: bool = True) -> None:
    """Esquema 49: las peticiones que la sincronización vio por primera vez y aún no se han mirado para avisar de
    «nueva y lista para subir». Una columna y no una clave de app_state para que sobreviva a una sincronización
    que se corta a medias, y para que el aviso salga una sola vez por petición. Las que ya hay no avisan."""
    _anadir_columna(conn, "request_items", "aviso_pendiente", "INTEGER NOT NULL DEFAULT 0")
    if activar_evento:
        _activar_evento_en_la_campana(conn, "request_new_ready")  # quien ya guardó sus eventos no se queda sin el nuevo


def _migrate_library_items_last_seen(conn: sqlite3.Connection) -> None:
    """Esquema 43: cuándo vio la sincronización cada elemento; lo que un recorrido completo no ve ya no está en
    Radarr/Sonarr y se borra, y sus subidas guardan lo que enseñaban de la ficha. El índice evita repasar todas
    las subidas por cada fila borrada: borrar una instancia de 35.000 capítulos con 2.000 subidas tardaba 3,5 s
    escribiendo, y 0,4 s con él."""
    for tabla, col in (("library_items", "last_seen_at"), ("upload_jobs", "library_snapshot_json")):
        _anadir_columna(conn, tabla, col, "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_upload_jobs_library ON upload_jobs(library_item_id)")
    # Borrar una instancia dejaba lo suyo en la biblioteca, y ninguna sincronización lo iba a quitar. Sin el
    # IS NOT NULL, con la tabla de instancias vacía también caerían las filas sin instancia.
    borrar_biblioteca(conn, "instance_id IS NOT NULL AND instance_id NOT IN (SELECT id FROM instances)")


def _migrate_upload_jobs_pasos(conn: sqlite3.Connection) -> None:
    """Esquema 47: dónde se paró una subida (`etapa_parada`, la fase en que estaba al fallar o cancelarse), si su
    reintento reutiliza los ficheros ya preparados (`reutiliza_preparacion`) y la huella de los originales con que
    se prepararon (`huellas_preparacion_json`), para no reutilizar lo preparado de un original que ha cambiado."""
    for nombre, definicion in (("etapa_parada", "TEXT"), ("reutiliza_preparacion", "INTEGER NOT NULL DEFAULT 0"),
                               ("huellas_preparacion_json", "TEXT")):
        _anadir_columna(conn, "upload_jobs", nombre, definicion)


def _migrate_upload_jobs_pasos_uploadrr(conn: sqlite3.Connection) -> None:
    """Esquema 51: qué pasos dijo Uploadrr-EMU que había terminado (`pasos_uploadrr_json`, las líneas
    `Step done:` de su salida) y si lanzarlo con `--resume` para que reutilice lo que dejó la subida anterior
    (`reanuda_uploadrr`)."""
    for nombre, definicion in (("pasos_uploadrr_json", "TEXT"), ("reanuda_uploadrr", "INTEGER NOT NULL DEFAULT 0")):
        _anadir_columna(conn, "upload_jobs", nombre, definicion)


def _migrate_upload_jobs_lote(conn: sqlite3.Connection) -> None:
    """Esquema 52: `lote_id`, la etiqueta que agrupa las subidas lanzadas de una vez (una tanda del asistente o un
    lote). Sin tabla propia: lo que se sabe del lote se deduce de sus filas. Anulable: las subidas sueltas y las
    viejas no tienen lote."""
    _anadir_columna(conn, "upload_jobs", "lote_id", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_upload_jobs_lote ON upload_jobs(lote_id)")


def _migrate_upload_jobs_opciones_tracker(conn: sqlite3.Connection) -> None:
    """Esquema 53: `anonimo` / `personal_release`, lo elegido a mano en el asistente para esa subida (NULL: lo que
    digan los grupos de Configuración, services/opciones_tracker.py)."""
    for nombre in ("anonimo", "personal_release"):
        _anadir_columna(conn, "upload_jobs", nombre, "INTEGER")


def _migrate_upload_jobs_trailer_estado(conn: sqlite3.Connection) -> None:
    """Esquema 54: `trailer_estado`, lo que dijo la comprobación del tráiler al terminar la subida (services/
    trailer_check.py): 'incluido', 'falta' (eMuwarez lo exige y hay que ponerlo a mano) o 'a_mano' (el usuario dijo
    que ya lo puso). NULL: no se comprobó o no se pudo. Las de antes se deducen de lo que dejaron: el artefacto
    `trailer_url` o la línea de aviso del registro. Siempre con `IS NULL`: no pisa lo que el usuario marcó."""
    _anadir_columna(conn, "upload_jobs", "trailer_estado", "TEXT")
    conn.execute(
        "UPDATE upload_jobs SET trailer_estado = 'incluido' WHERE trailer_estado IS NULL AND id IN ("
        "SELECT upload_job_id FROM upload_artifacts WHERE artifact_type = 'trailer_url')"
    )
    conn.execute(
        "UPDATE upload_jobs SET trailer_estado = 'falta' WHERE trailer_estado IS NULL AND status = 'completed' "
        "AND id IN (SELECT upload_job_id FROM upload_job_logs WHERE message LIKE 'No se encontró un trailer%')"
    )


def _migrate_upload_jobs_reenvio_qbit(conn: sqlite3.Connection) -> None:
    """Esquema 55: «Reenviar a qBittorrent» (services/upload_execution/reenvio_qbit.py). `reenvio_qbit_estado` es NULL,
    'en_curso', 'ok' o 'fallido', y `reenvio_qbit_at` cuándo cambió por última vez. El `status` de la subida no cambia."""
    for nombre in ("reenvio_qbit_estado", "reenvio_qbit_at"):
        _anadir_columna(conn, "upload_jobs", nombre, "TEXT")


def _migrate_upload_jobs_copia_de(conn: sqlite3.Connection) -> None:
    """Esquema 61: «Subir también a…» (services/subir_tambien.py). `copia_de_job_id` es la subida completada a otro
    tracker cuyo fichero y torrent reutiliza esta (mismo nombre, mismas piezas); NULL en las demás."""
    _anadir_columna(conn, "upload_jobs", "copia_de_job_id", "INTEGER")


def _migrate_upload_jobs_descripcion(conn: sqlite3.Connection) -> None:
    """Esquema 63: la descripción propia (BBCode) que se escribió en el Resumen para esta subida; Uploadrr-EMU la pone
    tras el tráiler y antes de las capturas (`-df`). NULL: solo la que genera Uploadrr-EMU."""
    _anadir_columna(conn, "upload_jobs", "descripcion", "TEXT")


def _migrate_perfil_historial_completo(conn: sqlite3.Connection) -> None:
    """Esquema 56: el historial de la cuenta guarda también buffer, seeding, leeching, H&R y grupo, que ya trae la
    misma lectura de /api/user. Las filas de antes se quedan con esas columnas a NULL."""
    tipos = {"buffer_bytes": "INTEGER", "seeding": "INTEGER", "leeching": "INTEGER", "hit_and_runs": "INTEGER",
             "grupo": "TEXT"}
    for nombre, tipo in tipos.items():
        _anadir_columna(conn, "perfil_historial", nombre, tipo)


def _migrate_mis_torrents(conn: sqlite3.Connection) -> None:
    """Esquema 57: tus subidas en eMuwarez (services/mis_torrents.py). `emu_mis_torrents` es la última lectura y
    `emu_mis_torrents_dia` una foto al día de cada torrent, para saber cuánto se ha bajado en la última semana."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS emu_mis_torrents (
            tracker TEXT NOT NULL DEFAULT 'emu',
            torrent_id INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            tamano INTEGER,
            subido_at TEXT,
            seeders INTEGER,
            leechers INTEGER,
            completados INTEGER,
            freeleech TEXT,
            doble_subida INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (tracker, torrent_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS emu_mis_torrents_dia (
            tracker TEXT NOT NULL DEFAULT 'emu',
            dia TEXT NOT NULL,              -- YYYY-MM-DD, local
            torrent_id INTEGER NOT NULL,
            seeders INTEGER,
            leechers INTEGER,
            completados INTEGER,
            PRIMARY KEY (tracker, torrent_id, dia)
        )
    """)


def _migrate_imgbox_a_pixhost(conn: sqlite3.Connection) -> None:
    """Esquema 53: imgbox deja de ir siempre el último (lleva meses caído) y pasa a ser un proveedor más, que se puede
    quitar. Quien ya tenía la lista guardada lo tenía de último sin verlo en ella: se cambia por Pixhost, que tampoco
    pide clave, para que su orden siga terminando en un proveedor sin clave. Sin lista guardada no se toca nada:
    image_hosts.get_enabled_hosts ya da Pixhost de primeras. Solo pasa al cruzar el esquema 53 (init_db), así
    que lo que el usuario quite después se queda quitado."""
    fila = conn.execute("SELECT value FROM settings WHERE key = 'imghost.enabled'").fetchone()
    if fila is None:
        return
    try:
        ids = json.loads(fila[0]) if fila[0] else []
    except (TypeError, ValueError):
        ids = []
    if not isinstance(ids, list):
        ids = []
    ids = [i for i in ids if i != "imgbox"]
    if "pixhost" not in ids:
        ids.append("pixhost")
    conn.execute("UPDATE settings SET value = ? WHERE key = 'imghost.enabled'", (json.dumps(ids),))


def _migrate_perfil_historial(conn: sqlite3.Connection) -> None:
    """Esquema 50: una instantánea al día de tu cuenta de eMuwarez (la sacan las lecturas horarias del perfil),
    para dibujar la evolución en Estadísticas. `dia` es el día local del servidor (TZ del contenedor).
    Solo añade una tabla."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS perfil_historial (
            tracker TEXT NOT NULL DEFAULT 'emu',
            dia TEXT NOT NULL,             -- YYYY-MM-DD, local
            ratio REAL,
            subido_bytes INTEGER,
            bajado_bytes INTEGER,
            bonus REAL,
            usuario TEXT,                  -- de qué cuenta es: si el perfil cambia de usuario, se empieza de cero
            actualizado_at TEXT NOT NULL,
            PRIMARY KEY (tracker, dia)
        )
    """)


def _migrate_upload_jobs_torrent_subido(conn: sqlite3.Connection) -> None:
    """Esquema 44: `torrent_subido` (qBittorrent siembra del enlace de esa subida, antes se buscaba en todo el
    registro) y el índice del registro, que sirve a MAX(sequence) y a leer el final o lo posterior a una línea."""
    _anadir_columna(conn, "upload_jobs", "torrent_subido", "INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_upload_job_logs_job_sequence ON upload_job_logs(upload_job_id, sequence)"
    )
    # Las de antes, por lo que dejaron escrito (QBIT_AÑADIDO de upload_execution, y la línea fija de Uploadrr-EMU).
    # Siempre, no solo con la columna nueva: si una pasada cayó entre el ALTER y este UPDATE, la siguiente lo completa.
    conn.execute(
        "UPDATE upload_jobs SET torrent_subido = 1 WHERE torrent_subido = 0 AND id IN ("
        "SELECT upload_job_id FROM upload_job_logs "
        "WHERE message = 'qBittorrent: torrent añadido correctamente.' OR message LIKE 'Torrent URL: %')"
    )


def _migrate_auth_sessions_last_used(conn: sqlite3.Connection) -> None:
    """Esquema 45: cuándo se usó por última vez cada sesión, para que caduque por inactividad. Las que ya hay cuentan
    como usadas ahora: nadie se queda fuera al actualizar solo por no tener el dato."""
    _anadir_columna(conn, "auth_sessions", "last_used_at", "TEXT")
    conn.execute("UPDATE auth_sessions SET last_used_at = CURRENT_TIMESTAMP WHERE last_used_at IS NULL AND revoked_at IS NULL")


def _migrate_quitar_peticiones_de_ejemplo(conn: sqlite3.Connection) -> None:
    """Las peticiones de ejemplo se generaron mientras no había endpoint real;
    ahora que se leen de eMuwarez, se borran con lo que colgaba de ellas. Solo una base anterior tiene la marca."""
    if "is_example" in {fila[1] for fila in conn.execute("PRAGMA table_info(request_items)")}:
        borrar_peticiones(conn, "is_example = 1")


_TABLAS_SIN_USO = (
    "operational_queue_items", "operational_queue_runs", "operational_action_events",
    "library_diagnostic_runs", "library_item_diagnostics",
)
_COLUMNAS_SIN_USO = (
    ("library_items", ("arr_queue_raw_json", "arr_history_raw_json", "diagnostic_reasons_json")),
    ("request_items", ("emu_catalog_checked_at", "emu_catalog_result", "is_example")),
)


# Esquema 60: las tablas cuya clave pasa a incluir el tracker. Una base nueva ya las crea así.
_CLAVES_CON_TRACKER: tuple[tuple[str, str], ...] = (
    ("request_items", "UNIQUE(tracker, emu_id)"),
    ("emu_mis_torrents", "PRIMARY KEY (tracker, torrent_id)"),
    ("emu_mis_torrents_dia", "PRIMARY KEY (tracker, torrent_id, dia)"),
    ("perfil_historial", "PRIMARY KEY (tracker, dia)"),
)
# Las que solo ganan la columna: su clave es un id propio.
_COLUMNA_TRACKER_SIN_CLAVE = ("upload_jobs", "emu_catalog")


def _columnas(conn: sqlite3.Connection, tabla: str) -> set[str]:
    return {fila[1] for fila in conn.execute(f"PRAGMA table_info({tabla})")}


def _rehacer_con_tracker(conn: sqlite3.Connection, tabla: str, clave: str) -> None:
    """Rehace `tabla` con la columna `tracker` delante y `clave` como clave (SQLite no cambia una clave con ALTER).
    Las columnas salen de PRAGMA table_info, no del texto del CREATE: ese lleva comentarios y lo que añadieron las
    migraciones. Ninguna de estas tablas tiene CHECK ni REFERENCES. Quien llama abre la transacción con las claves
    foráneas apagadas: soltar request_items con ellas encendidas borraría los cruces en cascada."""
    sql_viejo = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (tabla,)).fetchone()[0]
    autoincremento = "AUTOINCREMENT" in sql_viejo.upper()
    clave_primaria_nueva = clave.startswith("PRIMARY KEY")
    definiciones = [f"tracker TEXT NOT NULL DEFAULT '{TRACKER_POR_DEFECTO}'"]
    nombres = []
    for _, nombre, tipo, no_nulo, defecto, pk in conn.execute(f"PRAGMA table_info({tabla})").fetchall():
        definicion = f"{nombre} {tipo}".strip()
        if pk and not clave_primaria_nueva:
            definicion += " PRIMARY KEY" + (" AUTOINCREMENT" if autoincremento else "")
        elif no_nulo or pk:
            definicion += " NOT NULL"
        if defecto is not None:
            definicion += f" DEFAULT {defecto}"
        definiciones.append(definicion)
        nombres.append(nombre)
    indices = [fila[0] for fila in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL", (tabla,))]
    secuencia = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (tabla,)).fetchone() \
        if autoincremento else None
    columnas = ", ".join(nombres)
    conn.execute(f"DROP TABLE IF EXISTS {tabla}_nueva")
    conn.execute(f"CREATE TABLE {tabla}_nueva ({', '.join(definiciones + [clave])})")
    conn.execute(f"INSERT INTO {tabla}_nueva ({columnas}) SELECT {columnas} FROM {tabla}")
    conn.execute(f"DROP TABLE {tabla}")
    conn.execute(f"ALTER TABLE {tabla}_nueva RENAME TO {tabla}")
    for sql in indices:
        conn.execute(sql)
    if secuencia is not None:
        # Que no se reutilicen los ids de las últimas filas borradas: la biblioteca y las subidas guardan el id.
        conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = ? AND seq < ?", (secuencia[0], tabla, secuencia[0]))
        conn.execute("INSERT INTO sqlite_sequence (name, seq) SELECT ?, ? WHERE NOT EXISTS "
                     "(SELECT 1 FROM sqlite_sequence WHERE name = ?)", (tabla, secuencia[0], tabla))


def _migrate_tracker_en_tablas(conn: sqlite3.Connection) -> None:
    """Esquema 60 (fase A del plan multitracker): cada subida, petición, fila del catálogo, de «Tus subidas» y del
    historial del perfil lleva su tracker. Las que había son de eMuwarez (`TRACKER_POR_DEFECTO`, el valor por
    defecto de la columna). Donde el id del tracker era la clave (`emu_id`, `torrent_id`, el día del perfil), la clave
    pasa a ser el par: el mismo número en dos trackers son dos filas.

    Segura dos veces: solo rehace las tablas que aún no tienen la columna. Va en su propia transacción, con las claves
    foráneas apagadas (PRAGMA foreign_keys no cambia dentro de una transacción), y comprueba al final que los cruces
    no han perdido su petición."""
    for tabla in _COLUMNA_TRACKER_SIN_CLAVE:
        _anadir_columna(conn, tabla, "tracker", f"TEXT NOT NULL DEFAULT '{TRACKER_POR_DEFECTO}'")
    if all("tracker" in _columnas(conn, tabla) for tabla, _ in _CLAVES_CON_TRACKER):
        return
    conn.commit()
    previo = conn.isolation_level
    conn.isolation_level = None
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Lo que ya estuviera huérfano no puede impedir arrancar; solo lo que rompiera esta migración.
            huerfanos = len(conn.execute("PRAGMA foreign_key_check(request_matches)").fetchall())
            for tabla, clave in _CLAVES_CON_TRACKER:
                if "tracker" not in _columnas(conn, tabla):
                    _rehacer_con_tracker(conn, tabla, clave)
            if len(conn.execute("PRAGMA foreign_key_check(request_matches)").fetchall()) > huerfanos:
                raise sqlite3.IntegrityError("Tras rehacer request_items hay cruces sin su petición")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.isolation_level = previo


def _migrate_quitar_esquema_sin_uso(conn: sqlite3.Connection) -> None:
    """Esquema 59: fuera las tablas y columnas que ya nadie escribía ni leía. Una base nueva no las llega a crear."""
    for tabla in _TABLAS_SIN_USO:
        conn.execute(f"DROP TABLE IF EXISTS {tabla}")
    for tabla, columnas in _COLUMNAS_SIN_USO:
        presentes = {fila[1] for fila in conn.execute(f"PRAGMA table_info({tabla})")}
        for columna in columnas:
            if columna in presentes:
                conn.execute(f"ALTER TABLE {tabla} DROP COLUMN {columna}")


def _migrate_qbit_port_into_url(conn: sqlite3.Connection) -> None:
    """El puerto de qBittorrent deja de tener campo propio y pasa a ir dentro
    de la URL.

    Tenerlo aparte solo servía para escribirlo dos veces y para que "el puerto
    de la URL no coincide con el puerto en el que escucha su WebUI" fuera un
    error de verdad, difícil de ver desde la interfaz.

    Aquí se dobla el valor ya guardado dentro de la URL, para que a quien lo
    tuviera configurado no se le caiga la conexión en silencio en cuanto el
    campo desaparezca. Se usa el MISMO build_base_url que usa la conexión
    real, así que la URL migrada es exactamente la que se estaba llamando
    antes, no una reconstrucción parecida.
    """
    row = conn.execute("SELECT value FROM settings WHERE key = 'qbit.port'").fetchone()
    port = ((row["value"] if row else None) or "").strip()
    if port:
        url_row = conn.execute("SELECT value FROM settings WHERE key = 'qbit.url'").fetchone()
        url = ((url_row["value"] if url_row else None) or "").strip()
        if url:
            from .integrations.qbittorrent import build_base_url
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = 'qbit.url'",
                (build_base_url(url, port),),
            )
    # Fuera en cualquier caso: una sola fuente de verdad, o el día de mañana
    # alguien lee el puerto viejo y no cuadra con el de la URL.
    conn.execute("DELETE FROM settings WHERE key = 'qbit.port'")


def _migrate_library_items_spanish_audio(conn: sqlite3.Connection) -> None:
    """Per-item Castilian flags. `has_spanish_audio` is the Español column in
    Biblioteca; `has_spanish_subs` is the same question for subtitle tracks;
    `has_spanish_ok` is the one the upload rules actually gate on: Castilian
    present EITHER way, since dubbed content needs no subtitles at all (see
    services/season_pack.py::_spanish_errors).

    All three are rolled up for seasons/series (see
    services/library.py::recompute_spanish_flags), so none can be a plain
    expression over the *_languages columns — only movie/episode leaves carry
    those, and a season's verdict is "every episode I hold satisfies it", which
    is not the same as OR-ing the two separate rollups. Purely additive."""
    for col in ("has_spanish_audio", "has_spanish_subs", "has_spanish_ok"):
        _anadir_columna(conn, "library_items", col, "INTEGER")  # 1 yes, 0 no, NULL unknown/not computed


def _migrate_notifications_table(conn: sqlite3.Connection) -> None:
    """In-app notification feed backing the topbar bell. Each row is one event
    (request published, upload completed, request match found, …). read_at is
    NULL until the user opens/reads it. Purely additive."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT,
            link TEXT,
            level TEXT NOT NULL DEFAULT 'info',
            entity_type TEXT,
            entity_id INTEGER,
            poster_url TEXT,
            content_title TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            read_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(read_at)")
    # Existing installs predate poster_url/content_title — CREATE TABLE IF NOT
    # EXISTS above is a no-op for them, so add the columns here too.
    for col in ("poster_url", "content_title"):
        _anadir_columna(conn, "notifications", col, "TEXT")


def _migrate_nomenclature_phase10a(conn: sqlite3.Connection) -> None:
    """Add Phase 10A editable fields and MKV title columns to nomenclature_proposals."""
    new_cols = [
        ("fields_json",                   "TEXT"),
        ("source_final",                  "TEXT"),
        ("provider_final",                "TEXT"),
        ("provider_options_json",         "TEXT"),
        ("tmdb_available_providers_json", "TEXT"),
        ("internal_mkv_title",            "TEXT"),
        ("internal_title_status",         "TEXT"),
        ("internal_title_applied_at",     "TEXT"),
        ("internal_title_error",          "TEXT"),
    ]
    for col, col_def in new_cols:
        _anadir_columna(conn, "nomenclature_proposals", col, col_def)


def _migrate_nomenclature_year_series(conn: sqlite3.Connection) -> None:
    """Add year/series_title/season_number to nomenclature_proposals — the
    proposal dict always used the real year to build proposed_title/
    proposed_filename, but never stored it as its own column, so anything
    reading the saved proposal (the automated upload job's nomenclature
    snapshot table) saw a blank Año/Serie/Temporada even though the
    generated name itself had it right."""
    new_cols = [
        ("year",           "TEXT"),
        ("series_title",   "TEXT"),
        ("season_number",  "INTEGER"),
    ]
    for col, col_def in new_cols:
        _anadir_columna(conn, "nomenclature_proposals", col, col_def)


def _migrate_settings_phase10a(conn: sqlite3.Connection) -> None:
    """Insert default Phase 10A settings if not already present."""
    defaults = [
        ("default_release_tag",      "EMUWAREZ",                              0),
        ("provider_priority_order",  '["AMZN","NF","DSNP","MAX","ATVP"]',     0),
        ("tmdb_watch_region",        "ES",                                     0),
    ]
    for key, value, is_secret in defaults:
        conn.execute(
            """INSERT OR IGNORE INTO settings (key, value, is_secret)
               VALUES (?, ?, ?)""",
            (key, value, is_secret),
        )


def _migrate_settings_phase10e(conn: sqlite3.Connection) -> None:
    """Insert default Phase 10E Uploadrr-EMU runtime settings if not already present.

    Defaults are conservative and require operator review: python_path
    assumes the Docker image layout (/usr/local/bin/python); host/manual
    deployments must override it with the path to Uploadrr-EMU's own venv.
    allowed_roots is deliberately NOT seeded here — leaving the row absent
    lets get_allowed_roots() apply its own DEFAULT_ALLOWED_ROOTS ("/data")
    as the single source of truth for that default, rather than duplicating
    it here (see services/uploadrr_contract.py::get_allowed_roots).
    """
    defaults = [
        ("uploadrr.runtime_type", "uploadrr_emu", 0),
        ("uploadrr.python_path", "/usr/local/bin/python", 0),
        ("uploadrr.timeout", "7200", 0),
    ]
    for key, value, is_secret in defaults:
        conn.execute(
            """INSERT OR IGNORE INTO settings (key, value, is_secret)
               VALUES (?, ?, ?)""",
            (key, value, is_secret),
        )


def _migrate_scheduled_tasks_table(conn: sqlite3.Connection) -> None:
    """Create the scheduled_tasks table (Phase 11D — automatic tasks).

    Bookkeeping only (interval, last/next run, last status/summary) — the
    actual run history/logs reuse the pre-existing task_runs table (see
    services/tasks.py), keyed by task_key == task_runs.name.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            interval_minutes INTEGER NOT NULL,
            min_interval_minutes INTEGER NOT NULL,
            manual_allowed INTEGER NOT NULL DEFAULT 1,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_run_at TEXT,
            last_status TEXT,
            last_summary TEXT,
            next_run_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _migrate_scheduled_tasks_run_at(conn: sqlite3.Connection) -> None:
    """Esquema 46: `run_at` (HH:MM), la hora fija de una tarea programada. NULL = cada N minutos."""
    _anadir_columna(conn, "scheduled_tasks", "run_at", "TEXT")


def _migrate_library_items_raw_json_recortado(conn: sqlite3.Connection) -> int:
    """Esquema 58: raw_json guarda solo lo que se lee de él (CAMPOS_RAW_JSON* de radarr.py y sonarr.py); cada capítulo
    llevaba la serie entera de Sonarr. Recorta las filas que ya hay y devuelve cuántas. Sin sitio para el WAL (como mucho
    lo que ocupa la base) o si el disco se llena a medias, no toca nada: lo hace la próxima sincronización de cada
    instancia."""
    if not conn.execute("SELECT 1 FROM library_items WHERE raw_json IS NOT NULL LIMIT 1").fetchone():
        return 0  # base nueva
    fichero = conn.execute("PRAGMA database_list").fetchone()[2]
    if fichero and shutil.disk_usage(Path(fichero).parent).free < Path(fichero).stat().st_size:
        logger.warning("No hay sitio para recortar raw_json al migrar: lo hará la próxima sincronización")
        return 0
    conn.commit()  # el recorte, en su propia transacción: un SQLITE_FULL puede deshacer la transacción entera
    try:
        recortadas = conn.execute("""
            UPDATE library_items SET raw_json = CASE
                WHEN NOT json_valid(raw_json) THEN NULL
                WHEN item_type = 'movie' THEN json_object('added', raw_json -> '$.added',
                    'monitored', raw_json -> '$.monitored', 'status', raw_json -> '$.status')
                WHEN item_type = 'series' THEN json_object('added', raw_json -> '$.added')
                WHEN item_type = 'episode' THEN json_object('episode', json_object(
                    'airDate', raw_json -> '$.episode.airDate', 'airDateUtc', raw_json -> '$.episode.airDateUtc',
                    'monitored', raw_json -> '$.episode.monitored', 'title', raw_json -> '$.episode.title'))
            END
            WHERE raw_json IS NOT NULL""").rowcount
        conn.commit()
        return recortadas
    except sqlite3.OperationalError:
        conn.rollback()
        logger.warning("No se pudo recortar raw_json al migrar: lo hará la próxima sincronización", exc_info=True)
        return 0


def _compactar_tras_migrar(conn: sqlite3.Connection) -> None:
    """VACUUM tras una migración que deja la base medio vacía. Escribe en el WAL otra copia de lo que queda vivo: sin
    el doble de eso libre en el disco no se hace (las páginas libres se reutilizan igual). Si falla, se arranca igual."""
    fichero = conn.execute("PRAGMA database_list").fetchone()[2]
    if not fichero:
        return  # en memoria
    pagina, paginas, libres = (conn.execute(f"PRAGMA {p}").fetchone()[0]
                               for p in ("page_size", "page_count", "freelist_count"))
    vivo = (paginas - libres) * pagina
    if shutil.disk_usage(Path(fichero).parent).free < 2 * vivo:
        logger.warning("No hay sitio para compactar la base (%d MB libres hacen falta): compáctala desde Diagnóstico",
                       2 * vivo // 2**20)
        return
    try:
        conn.execute("PRAGMA temp_store = MEMORY")  # la copia temporal del VACUUM, en RAM y no en /tmp
        conn.execute("VACUUM")
    except sqlite3.Error:
        logger.warning("No se pudo compactar la base tras migrar", exc_info=True)
    try:  # también tras un VACUUM fallido, que puede haber dejado el WAL lleno
        conn.execute("PRAGMA temp_store = DEFAULT")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        logger.warning("No se pudo vaciar el WAL tras compactar", exc_info=True)


_COPIAS_ANTES_DE_MIGRAR = 3


def _version_guardada(conn: sqlite3.Connection) -> Optional[int]:
    try:
        row = conn.execute("SELECT value FROM app_state WHERE key = 'schema_version'").fetchone()
    except sqlite3.OperationalError:
        return None  # app_state doesn't exist yet — fresh DB, run the full cascade
    try:
        return int(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def _copia_antes_de_migrar(conn: sqlite3.Connection, version: int) -> bool:
    """Deja en backups/ una copia de la base antes de tocar el esquema, que es lo único con lo que se vuelve atrás
    tras una migración. Sin sitio para ella no la empieza: llenaba el disco hasta fallar. Devuelve si la base queda
    copiada; si no, se migra igual e init_db lo deja dicho para la campana."""
    try:
        fichero = conn.execute("PRAGMA database_list").fetchone()[2]
        if not fichero:
            return True  # en memoria
        carpeta = Path(fichero).parent / "backups"
        carpeta.mkdir(mode=0o700, parents=True, exist_ok=True)
        ahora = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        destino = carpeta / f"pre-migracion-{ahora}-v{version}.db"
        if destino.exists():
            return True
        ocupa = sum(f.stat().st_size for f in (Path(fichero), Path(fichero + "-wal")) if f.exists())
        if shutil.disk_usage(carpeta).free < ocupa:
            logger.warning("No hay sitio para copiar la base antes de migrar (hacen falta %d MB)", ocupa // 2**20)
            return False
        conn.commit()  # VACUUM INTO no corre dentro de una transacción
        # Creada antes en 0600 (lleva todas las claves): VACUUM INTO la crearía con el umask y quedaría legible.
        os.close(os.open(destino, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        try:
            conn.execute("VACUUM INTO ?", (str(destino),))
        except Exception:
            destino.unlink(missing_ok=True)  # una copia vacía no puede contar entre las últimas
            raise
    except (sqlite3.Error, OSError):
        logger.warning("No se pudo copiar la base antes de migrar", exc_info=True)
        return False
    try:
        copias = sorted(carpeta.glob("pre-migracion-*.db"))
        # La primera de esta versión es la única anterior a un intento fallido de migrar: los
        # reintentos de un arranque en bucle copian ya una base a medias y no deben llevársela.
        intocable = next((c for c in copias if c.name.endswith(f"-v{version}.db")), None)
        for vieja in copias[:-_COPIAS_ANTES_DE_MIGRAR]:
            if vieja != intocable:
                vieja.unlink(missing_ok=True)
    except OSError:  # la copia nueva ya está hecha
        logger.warning("No se pudieron retirar las copias antiguas de antes de migrar", exc_info=True)
    return True


def init_db(conn: Optional[sqlite3.Connection] = None) -> None:
    """Create tables and run the migration cascade — idempotent, but NOT
    cheap: ~35 migration functions, several of which rebuild whole tables.

    The web app and `web serve` call `preparar_base` once at startup; after
    that this returns at once for that file. Anywhere else it reads the stored
    schema version first and returns early when it matches, so the cascade
    never runs twice (concurrent passes took write locks and caused
    `database is locked` errors).

    A base left by a newer version is refused (BaseMasNuevaError) instead of
    being stamped with the old number, and an older one is copied to
    backups/ before it is migrated.

    The cascade is not one transaction: the `instances` rebuilds and the pre-migration copy commit what came before
    them, and the version is stored only at the end. A pass that fails halfway leaves its first migrations applied and
    the next start runs all of them again, so every `_migrate_*` must be safe to run twice; one that rewrites data
    is called here only when `desde` crosses its schema.
    """
    own = conn is None
    conn = conn or connect()
    candado = False
    try:
        if getattr(conn, "ruta", None) in _esquema_listo:
            return
        if _version_guardada(conn) == SCHEMA_VERSION:
            return
        _cascada.acquire()
        candado = True
        version = _version_guardada(conn)  # otro hilo pudo migrarla mientras este esperaba
        if version == SCHEMA_VERSION:
            return
        if version is not None and version > SCHEMA_VERSION:
            raise BaseMasNuevaError(
                f"La base de datos es de una versión más nueva (esquema {version}) que esta "
                f"aplicación (esquema {SCHEMA_VERSION}). Usa una imagen igual o más nueva; para "
                f"volver atrás, restaura la copia backups/pre-migracion-*-v{SCHEMA_VERSION}.db."
            )
        sin_copia = version is not None and not _copia_antes_de_migrar(conn, version)
        # Las migraciones que cambian ajustes solo corren al cruzar su esquema: repetidas en cada salto deshacían
        # lo que el usuario había cambiado después.
        desde = version or 0
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL CHECK(type IN ('radarr','sonarr')),
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                api_key TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                is_4k INTEGER NOT NULL DEFAULT 0,
                is_anime INTEGER NOT NULL DEFAULT 0,
                last_test_status TEXT,
                last_test_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(type, name)
            );

            CREATE TABLE IF NOT EXISTS library_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_type TEXT NOT NULL,
                title TEXT NOT NULL,
                year INTEGER,
                original_title TEXT,
                english_title TEXT,
                instance_id INTEGER,
                instance_type TEXT,
                instance_name TEXT,
                external_id TEXT,
                tmdb_id TEXT,
                imdb_id TEXT,
                tvdb_id TEXT,
                title_slug TEXT,
                path TEXT,
                quality TEXT,
                resolution TEXT,
                source TEXT,
                size_bytes INTEGER,
                local_status TEXT NOT NULL,
                request_status TEXT NOT NULL DEFAULT 'Sin petición',
                nomenclature_status TEXT NOT NULL DEFAULT 'N/A',
                request_id INTEGER,
                added_at TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                raw_json TEXT,
                UNIQUE(instance_id, item_type, external_id)
            );

            CREATE INDEX IF NOT EXISTS idx_library_status ON library_items(local_status);
            CREATE INDEX IF NOT EXISTS idx_library_request_status ON library_items(request_status);
            CREATE INDEX IF NOT EXISTS idx_library_nomenclature ON library_items(nomenclature_status);
            CREATE INDEX IF NOT EXISTS idx_library_tmdb ON library_items(tmdb_id);
            CREATE INDEX IF NOT EXISTS idx_library_imdb ON library_items(imdb_id);

            CREATE TABLE IF NOT EXISTS request_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracker TEXT NOT NULL DEFAULT 'emu',
                emu_id TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT,
                requested_source TEXT,
                requested_resolution TEXT,
                emu_status TEXT,
                points INTEGER,
                comments INTEGER,
                created_at TEXT,
                tmdb_id TEXT,
                imdb_id TEXT,
                url TEXT,
                author TEXT,
                author_visible INTEGER NOT NULL DEFAULT 0,
                raw_json TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tracker, emu_id)
            );

            CREATE INDEX IF NOT EXISTS idx_requests_tmdb ON request_items(tmdb_id);
            CREATE INDEX IF NOT EXISTS idx_requests_imdb ON request_items(imdb_id);
            CREATE INDEX IF NOT EXISTS idx_requests_title ON request_items(title);

            CREATE TABLE IF NOT EXISTS request_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                library_item_id INTEGER NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
                request_item_id INTEGER NOT NULL REFERENCES request_items(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0,
                resolution_match INTEGER,
                source_match INTEGER,
                type_match INTEGER,
                reasons TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(library_item_id, request_item_id)
            );

            CREATE TABLE IF NOT EXISTS task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT,
                summary TEXT,
                details TEXT
            );

            CREATE TABLE IF NOT EXISTS upload_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                library_item_id INTEGER REFERENCES library_items(id) ON DELETE SET NULL,
                title TEXT NOT NULL,
                path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                dupe_status TEXT NOT NULL DEFAULT 'No comprobado',
                dupe_message TEXT,
                uploadrr_description TEXT,
                trailer_url TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            -- v3: web auth and settings tables
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_login_at TEXT
            );

            CREATE TABLE IF NOT EXISTS auth_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT,
                revoked_at TEXT,
                user_agent TEXT,
                ip_address TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_token ON auth_sessions(token_hash);
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON auth_sessions(user_id);

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                is_secret INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        _migrate_instances(conn)
        _migrate_instances_widen_type_check(conn)
        _migrate_requests(conn)
        _migrate_nomenclature(conn)
        _migrate_upload_jobs(conn)
        _migrate_upload_jobs_phase6c(conn)
        _migrate_upload_job_logs(conn)
        _migrate_filesystem_operations(conn)
        _migrate_library_items_phase6b(conn)
        _migrate_upload_jobs_phase6d(conn)
        _migrate_library_items_phase8a(conn)
        _migrate_request_items_phase8b(conn)
        _migrate_request_readiness_tables(conn)
        _migrate_nomenclature_phase10a(conn)
        _migrate_settings_phase10a(conn)
        _migrate_settings_phase10e(conn)
        _migrate_scheduled_tasks_table(conn)
        _migrate_library_items_phase11e(conn)
        _migrate_library_items_title_slug(conn)
        _migrate_instances_narrow_type_check(conn)
        _migrate_request_items_extra_ids(conn)
        _migrate_drop_priority_columns(conn)
        _migrate_instances_unique_name(conn)
        _migrate_library_items_media_info(conn)
        _migrate_library_items_hierarchy(conn)
        _migrate_notifications_table(conn)
        _migrate_emu_catalog(conn)
        _migrate_tmdb_title_cache(conn)
        if desde < 38:
            _migrate_ui_recibe_aviso_de_version(conn)
        _migrate_library_items_track_remux(conn)
        _migrate_upload_jobs_queue(conn)
        _migrate_upload_jobs_snapshots(conn)
        _migrate_upload_jobs_manual_refs(conn)
        _migrate_upload_jobs_checks_snapshot(conn)
        _migrate_upload_jobs_season_overrides(conn)
        _migrate_nomenclature_year_series(conn)
        _migrate_source_engine_cache(conn)
        _migrate_library_items_mal_id(conn)
        _migrate_anime_id_map(conn)
        _migrate_library_items_backdrop_url(conn)
        _migrate_library_items_missing_since(conn)
        _migrate_library_items_spanish_audio(conn)
        _migrate_qbit_port_into_url(conn)
        _migrate_library_items_tvmaze_id(conn)
        _migrate_library_items_release_group(conn)
        _migrate_dominio_emu_warez(conn)
        _migrate_nomenclature_torrent_name(conn)
        _migrate_quitar_peticiones_de_ejemplo(conn)
        _migrate_request_items_last_seen(conn)
        _migrate_upload_jobs_request_snapshot(conn)
        _migrate_request_items_posters(conn)
        _migrate_library_items_last_seen(conn)
        _migrate_upload_jobs_torrent_subido(conn)
        _migrate_auth_sessions_last_used(conn)
        _migrate_scheduled_tasks_run_at(conn)
        _migrate_upload_jobs_pasos(conn)
        _migrate_request_items_aviso_pendiente(conn, activar_evento=desde < 49)
        _migrate_perfil_historial(conn)
        _migrate_upload_jobs_pasos_uploadrr(conn)
        _migrate_upload_jobs_lote(conn)
        _migrate_upload_jobs_opciones_tracker(conn)
        if desde < 53:
            _migrate_imgbox_a_pixhost(conn)
        _migrate_upload_jobs_trailer_estado(conn)
        _migrate_upload_jobs_reenvio_qbit(conn)
        _migrate_perfil_historial_completo(conn)
        _migrate_mis_torrents(conn)
        recortadas = _migrate_library_items_raw_json_recortado(conn) if desde < 58 else 0
        _migrate_quitar_esquema_sin_uso(conn)
        _migrate_tracker_en_tablas(conn)
        _migrate_upload_jobs_copia_de(conn)
        _migrate_instances_carpeta(conn)  # esquema 62: en dev era el 60
        _migrate_upload_jobs_descripcion(conn)
        set_state(conn, "schema_version", str(SCHEMA_VERSION))
        if sin_copia:
            set_state(conn, "migracion_sin_copia", str(version))  # backup_service.avisar_migracion_sin_copia
        conn.commit()
        if recortadas:
            # Fuera de la cascada y con la versión ya guardada: si no hay sitio o se corta, la migración no se repite.
            _compactar_tras_migrar(conn)
    finally:
        if candado:
            _cascada.release()
        if own:
            conn.close()


def borrar_peticiones(conn: sqlite3.Connection, donde: str, params: tuple = ()) -> int:
    """Borra las peticiones que cumplan `donde` y lo que depende de ellas.

    Los cruces (request_matches) caen por la clave foránea; el resto guarda el
    id sin clave, y la biblioteca puede llevar el estado de una petición que
    ya no existe. `donde` lo escribe el código, nunca viene de fuera."""
    ids = f"SELECT id FROM request_items WHERE {donde}"
    conn.execute(
        "UPDATE library_items SET request_status = 'Sin petición', request_id = NULL "
        f"WHERE request_id IN ({ids})", params)
    for tabla in ("request_readiness", "request_candidates"):
        conn.execute(f"DELETE FROM {tabla} WHERE request_item_id IN ({ids})", params)
    return conn.execute(f"DELETE FROM request_items WHERE {donde}", params).rowcount


def borrar_biblioteca(conn: sqlite3.Connection, donde: str, params: tuple = ()) -> tuple[int, list[int]]:
    """Borra los elementos de la biblioteca que cumplan `donde`, con sus temporadas y capítulos, y lo que depende
    de ellos. Devuelve cuántas filas borró y las peticiones que casaban con alguna, para volver a cruzarlas.

    Los cruces caen por la clave foránea y las subidas se quedan en el historial sin enlace (SET NULL), con su copia
    de la ficha; el resto guarda el id sin clave. `donde` lo escribe el código, nunca viene de fuera."""
    ids = ("WITH RECURSIVE rama(id) AS ("
           f"SELECT id FROM library_items WHERE {donde} "
           "UNION SELECT h.id FROM library_items h JOIN rama ON h.parent_id = rama.id) SELECT id FROM rama")
    # Una petición de temporada casa con la serie pero lleva su estado en la temporada (library_items.request_id).
    peticiones = [r[0] for r in conn.execute(
        f"SELECT request_item_id FROM request_matches WHERE library_item_id IN ({ids}) "
        f"UNION SELECT request_item_id FROM request_candidates WHERE library_item_id IN ({ids}) "
        f"UNION SELECT request_id FROM library_items WHERE request_id IS NOT NULL AND id IN ({ids})", params * 3)]
    # Quien llama vuelve a calcular su preparación; la migración no puede, así que al menos no apuntan a nada borrado.
    for tabla, col in (("request_readiness", "best_library_item_id"), ("request_items", "readiness_best_library_item_id")):
        conn.execute(f"UPDATE {tabla} SET {col} = NULL WHERE {col} IN ({ids})", params)
    # Lo que Subidas enseña de la ficha y sigue siendo cierto sin ella: el enlace a Radarr/Sonarr y el estado de la
    # petición ya no lo serían.
    conn.execute(
        "UPDATE upload_jobs SET library_snapshot_json = (SELECT json_object('item_type', li.item_type, "
        "'poster_url', li.poster_url, 'backdrop_url', li.backdrop_url, 'instance_type', li.instance_type, "
        "'instance_name', li.instance_name, 'year', li.year) FROM library_items li "
        f"WHERE li.id = upload_jobs.library_item_id) WHERE library_item_id IN ({ids})", params)
    for tabla in ("request_candidates", "nomenclature_proposals", "filesystem_operations"):
        conn.execute(f"DELETE FROM {tabla} WHERE library_item_id IN ({ids})", params)
    return conn.execute(f"DELETE FROM library_items WHERE id IN ({ids})", params).rowcount, peticiones


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def get_state(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def get_setting_canonical(
    conn: sqlite3.Connection, dotted_key: str, default: Optional[str] = None
) -> Optional[str]:
    """Read a `settings` value by its canonical dotted key.

    Falls back to the legacy underscore alias (see CANONICAL_SETTING_ALIASES)
    when the dotted key is unset or empty. The dotted key always wins when
    both are present, since setup/UI only ever write the dotted form.
    """
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (dotted_key,)).fetchone()
        if row and row["value"] not in (None, ""):
            return row["value"]
        legacy_key = CANONICAL_SETTING_ALIASES.get(dotted_key)
        if legacy_key:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (legacy_key,)).fetchone()
            if row and row["value"] not in (None, ""):
                return row["value"]
    except sqlite3.OperationalError:
        pass
    return default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO app_state(key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """,
        (key, value),
    )


def get_json_state(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    value = get_state(conn, key)
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def set_json_state(conn: sqlite3.Connection, key: str, value: Any) -> None:
    set_state(conn, key, json.dumps(value, ensure_ascii=False, sort_keys=True))


__all__ = [
    "DB_FILE", "SCHEMA_VERSION", "db_path", "connect", "db", "init_db", "preparar_base", "olvidar_esquema",
    "BaseMasNuevaError", "borrar_peticiones", "borrar_biblioteca",
    "row_to_dict", "rows_to_dicts", "get_state", "set_state",
    "get_json_state", "set_json_state",
    "CANONICAL_SETTING_ALIASES", "get_setting_canonical",
]
