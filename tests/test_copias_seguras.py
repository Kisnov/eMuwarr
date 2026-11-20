"""Copias que no se pierden ni engañan (análisis de la base de 2026-11-19, bloque 2)."""
from __future__ import annotations

import datetime
import os
import stat
import types

import pytest

from emuwarez_uploadrr.database import connect, db, db_path, get_state, init_db, olvidar_esquema, set_state
from emuwarez_uploadrr.services import backup_service as bs
from emuwarez_uploadrr.services.notification_center import list_notifications
from emuwarez_uploadrr.services.scheduler import TASK_BACKUP, run_scheduled_task

solo_posix = pytest.mark.skipif(os.name == "nt", reason="los permisos son de POSIX")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    d = tmp_path / "config"
    d.mkdir()
    monkeypatch.setenv("CONFIG_DIR", str(d))
    olvidar_esquema()
    _marcar("antes")
    # Un segundo por copia: dos en el mismo segundo se llamarían igual.
    segundos = iter(range(60))

    class Reloj(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime(2026, 11, 20, 12, 0, next(segundos))

    monkeypatch.setattr(bs, "datetime", types.SimpleNamespace(datetime=Reloj))
    return d


def _marcar(valor: str) -> None:
    with db() as conn:
        set_state(conn, "marca", valor)


def _marca() -> str | None:
    with db() as conn:
        return get_state(conn, "marca")


def _disco_lleno(*_, **__):
    raise OSError(28, "No space left on device")


def _restaurar(archivo) -> dict:
    return bs.restore_config_backup(str(archivo), dry_run=False, apply=True, confirmation=bs.RESTORE_CONFIRMATION)


def test_restaurar_guarda_antes_lo_que_habia_y_esa_copia_lo_deshace(cfg):
    vieja = bs.create_config_backup()["archive_path"]
    _marcar("después")

    r = _restaurar(vieja)
    assert r["ok"] and _marca() == "antes"
    previa = r["copia_previa"]
    assert previa.startswith(bs.PREFIJO_ANTES_DE_RESTAURAR) and previa.endswith("_with_secrets.tar.gz")
    assert previa in r["message"]
    assert [(b["tipo"], b["con_claves"]) for b in bs.list_config_backups() if b["name"] == previa] == [
        ("antes_de_restaurar", True)]

    assert _restaurar(bs.get_backup_dir() / previa)["ok"] and _marca() == "después"


def test_deja_las_ultimas_cinco_de_antes_de_restaurar_y_nunca_la_que_se_restaura(cfg):
    carpeta = bs.get_backup_dir()
    # La más antigua es la que se restaura: la retención no puede llevársela a mitad.
    restaurada = carpeta / f"{bs.PREFIJO_ANTES_DE_RESTAURAR}20260101_000000_with_secrets.tar.gz"
    os.replace(bs.create_config_backup()["archive_path"], restaurada)
    for dia in range(2, 7):
        (carpeta / f"{bs.PREFIJO_ANTES_DE_RESTAURAR}2026010{dia}_000000_with_secrets.tar.gz").write_bytes(b"x")

    assert _restaurar(restaurada)["ok"]

    quedan = sorted(c.name for c in carpeta.glob(f"{bs.PREFIJO_ANTES_DE_RESTAURAR}*"))
    assert len(quedan) == 6 and quedan[0] == restaurada.name
    assert f"{bs.PREFIJO_ANTES_DE_RESTAURAR}20260102_000000_with_secrets.tar.gz" not in quedan


def test_sin_poder_guardar_lo_que_habia_no_se_restaura_nada(cfg, monkeypatch):
    vieja = bs.create_config_backup()["archive_path"]
    _marcar("después")

    monkeypatch.setattr(bs, "create_config_backup", _disco_lleno)
    r = _restaurar(vieja)
    assert not r["ok"] and "No se ha restaurado nada" in r["error"]
    assert _marca() == "después"


def test_una_copia_que_falla_a_medias_no_deja_nada_en_la_carpeta(cfg, monkeypatch):
    monkeypatch.setattr(bs, "_add_db_snapshot", _disco_lleno)
    with pytest.raises(OSError):
        bs.create_config_backup()
    assert list(bs.get_backup_dir().iterdir()) == []


def test_al_arrancar_se_barren_los_part_y_nada_mas(cfg):
    carpeta = bs.get_backup_dir()
    buena = bs.create_config_backup()["archive_path"]
    for resto in (".emuwarez_config_20260101_000000.tar.gz.part", ".subida_0a1b2c.part"):
        (carpeta / resto).write_bytes(b"x")

    assert bs.barrer_copias_a_medias() == 2
    assert [str(c) for c in carpeta.iterdir()] == [buena]


def test_una_copia_truncada_da_un_error_claro_y_no_toca_nada(cfg):
    buena = bs.get_backup_dir() / bs.create_config_backup()["archive_name"]
    datos = buena.read_bytes()
    truncada = buena.with_name("emuwarez_config_20260101_000000.tar.gz")
    truncada.write_bytes(datos[: len(datos) // 2])
    _marcar("después")

    for r in (bs.restore_config_backup(str(truncada), dry_run=True), _restaurar(truncada)):
        assert not r["ok"] and "incompleto o dañado" in r["error"]
    assert _marca() == "después"
    assert not list(bs.get_backup_dir().glob(f"{bs.PREFIJO_ANTES_DE_RESTAURAR}*"))


@solo_posix
def test_la_carpeta_de_copias_solo_la_abre_su_dueno(cfg):
    (cfg / "backups").mkdir(mode=0o755)
    os.chmod(cfg / "backups", 0o755)  # sin el umask del sistema
    assert stat.S_IMODE(bs.get_backup_dir().stat().st_mode) == 0o700


@pytest.mark.parametrize("como", ["error", "excepcion"])
def test_la_copia_programada_que_falla_deja_un_aviso_en_la_campana(isolated_db, monkeypatch, como):
    conn = connect(db_path())
    init_db(conn)
    try:
        if como == "error":
            monkeypatch.setenv("CONFIG_DIR", str(isolated_db / "no-existe"))
        else:
            monkeypatch.setattr(bs, "_add_db_snapshot", _disco_lleno)
        assert run_scheduled_task(conn, TASK_BACKUP, manual=True)["status"] == "failed"
        avisos = [(n["event_type"], n["level"]) for n in list_notifications(conn)]
        assert avisos == [("backup_failed", "error")]
    finally:
        conn.close()
