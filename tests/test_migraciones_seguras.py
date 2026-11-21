"""La cascada de migraciones no calla un bloqueo, no rehace `instances` en cada salto, corre una sola vez aunque
lleguen varios hilos y no empieza una copia previa sin sitio. Que restaurar una copia vieja la migra en el acto
está en test_base_al_arrancar.py.

Bloque 3 del análisis de la base de 2026-11-19 (MIG-3, MIG-5, MIG-6, MIG-7).
"""
from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from emuwarez_uploadrr import database
from emuwarez_uploadrr.database import (
    SCHEMA_VERSION,
    _anadir_columna,
    connect,
    db,
    db_path,
    get_state,
    init_db,
    olvidar_esquema,
    preparar_base,
    set_state,
)
from emuwarez_uploadrr.services import backup_service


@pytest.fixture(autouse=True)
def _esquema_limpio():
    olvidar_esquema()
    yield
    olvidar_esquema()


def _base_de_la_version_anterior() -> None:
    """Una base ya creada a la que se le pone el esquema anterior: el próximo init_db corre la cascada."""
    with db() as conn:
        set_state(conn, "schema_version", str(SCHEMA_VERSION - 1))
    olvidar_esquema()


def _copias_previas() -> list:
    return sorted((db_path().parent / "backups").glob("pre-migracion-*.db"))


# ---------------------------------------------------------------------------
# MIG-6: añadir una columna solo calla si ya está
# ---------------------------------------------------------------------------

class _Bloqueada:
    """Una conexión cuyo ALTER TABLE encuentra la base ocupada."""

    def __init__(self, real):
        self.real = real

    def execute(self, sql, *args):
        if sql.startswith("ALTER TABLE"):
            raise sqlite3.OperationalError("database is locked")
        return self.real.execute(sql, *args)


def test_anadir_una_columna_que_ya_esta_no_falla():
    with db() as conn:
        _anadir_columna(conn, "upload_jobs", "lote_id", "TEXT")
        _anadir_columna(conn, "upload_jobs", "columna_de_prueba", "TEXT")
        assert "columna_de_prueba" in {f[1] for f in conn.execute("PRAGMA table_info(upload_jobs)")}


def test_un_bloqueo_al_anadir_una_columna_no_se_calla():
    with db() as conn:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            _anadir_columna(_Bloqueada(conn), "upload_jobs", "columna_de_prueba", "TEXT")


def test_quitar_las_columnas_de_prioridad_dos_veces_no_falla():
    with db() as conn:
        database._migrate_drop_priority_columns(conn)
        assert "priority_score" not in {f[1] for f in conn.execute("PRAGMA table_info(request_readiness)")}


# ---------------------------------------------------------------------------
# MIG-7: `instances` no se reconstruye en cada salto de esquema
# ---------------------------------------------------------------------------

def test_un_salto_de_esquema_no_reutiliza_el_id_de_una_instancia_borrada():
    with db() as conn:
        for nombre in ("uno", "dos", "tres"):
            conn.execute("INSERT INTO instances (type, name, url, api_key) VALUES ('radarr', ?, 'http://x', 'k')",
                         (nombre,))
        conn.execute("DELETE FROM instances WHERE name = 'tres'")
    _base_de_la_version_anterior()

    preparar_base()

    with db() as conn:
        nueva = conn.execute(
            "INSERT INTO instances (type, name, url, api_key) VALUES ('sonarr', 'cuatro', 'http://x', 'k')").lastrowid
    assert nueva == 4


def test_la_cascada_repetida_deja_el_mismo_esquema():
    def esquema() -> list:
        with db() as conn:
            return conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()

    antes = [tuple(f) for f in esquema()]
    _base_de_la_version_anterior()
    preparar_base()

    assert [tuple(f) for f in esquema()] == antes


# ---------------------------------------------------------------------------
# MIG-5: una sola cascada aunque lleguen varios hilos
# ---------------------------------------------------------------------------

def test_varios_hilos_a_la_vez_migran_una_sola_vez(monkeypatch):
    _base_de_la_version_anterior()
    copias = []

    def copia_lenta(conn, version):
        copias.append(version)
        time.sleep(0.2)  # los demás hilos llegan mientras este migra
        return True

    monkeypatch.setattr(database, "_copia_antes_de_migrar", copia_lenta)
    errores = []

    def abrir():
        try:
            conn = connect()
            try:
                init_db(conn)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - se comprueba abajo
            errores.append(exc)

    hilos = [threading.Thread(target=abrir) for _ in range(6)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert errores == []
    assert copias == [SCHEMA_VERSION - 1]


# ---------------------------------------------------------------------------
# MIG-3: sin sitio no se empieza la copia previa, y se avisa
# ---------------------------------------------------------------------------

def test_sin_sitio_no_se_empieza_la_copia_y_la_campana_lo_dice_una_vez(monkeypatch):
    _base_de_la_version_anterior()
    disco = database.shutil.disk_usage(db_path().parent)
    with monkeypatch.context() as m:
        m.setattr(database.shutil, "disk_usage", lambda _ruta: disco._replace(free=0))
        preparar_base()

    assert _copias_previas() == []
    with db() as conn:
        assert get_state(conn, "schema_version") == str(SCHEMA_VERSION)
        assert backup_service.avisar_migracion_sin_copia(conn) is True
        assert backup_service.avisar_migracion_sin_copia(conn) is False
        avisos = conn.execute(
            "SELECT level, body FROM notifications WHERE event_type = 'migration_without_backup'").fetchall()
    assert len(avisos) == 1
    assert avisos[0]["level"] == "warning"
    assert f"esquema {SCHEMA_VERSION - 1} → {SCHEMA_VERSION}" in avisos[0]["body"]


def test_con_copia_previa_no_hay_aviso():
    _base_de_la_version_anterior()

    preparar_base()

    assert len(_copias_previas()) == 1
    with db() as conn:
        assert backup_service.avisar_migracion_sin_copia(conn) is False


def test_no_poder_retirar_copias_antiguas_no_cuenta_como_migrar_sin_copia(monkeypatch):
    _base_de_la_version_anterior()
    carpeta = db_path().parent / "backups"
    carpeta.mkdir(exist_ok=True)
    for dia in range(1, 5):
        (carpeta / f"pre-migracion-2020010{dia}_000000-v1.db").write_bytes(b"")

    def no_se_puede(self, missing_ok=False):
        raise PermissionError("no se puede borrar")

    with monkeypatch.context() as m:
        m.setattr(database.Path, "unlink", no_se_puede)
        preparar_base()

    assert len(_copias_previas()) == 5
    with db() as conn:
        assert get_state(conn, "migracion_sin_copia") is None


# ---------------------------------------------------------------------------
# Una restauración a la vez
# ---------------------------------------------------------------------------

def test_una_restauracion_espera_a_que_acabe_la_que_esta_en_marcha():
    with db() as conn:
        set_state(conn, "marca", "copia")
    copia = backup_service.create_config_backup()["archive_path"]
    with db() as conn:
        set_state(conn, "marca", "ahora")
    resultado = []
    hilo = threading.Thread(target=lambda: resultado.append(backup_service.restore_config_backup(
        copia, dry_run=False, apply=True, confirmation=backup_service.RESTORE_CONFIRMATION)))

    with backup_service._restaurando:  # otra restauración en marcha
        hilo.start()
        hilo.join(0.5)
        assert hilo.is_alive()
        with db() as conn:
            assert get_state(conn, "marca") == "ahora"
    hilo.join(30)

    assert resultado and resultado[0]["ok"] is True
    with db() as conn:
        assert get_state(conn, "marca") == "copia"
