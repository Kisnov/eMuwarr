"""Copias que no se pierden ni engañan (análisis de la base de 2026-11-19, bloque 2)."""
from __future__ import annotations

import datetime
import os
import stat
import tarfile
import types

import pytest

from emuwarez_uploadrr.database import connect, db, db_path, get_state, init_db, olvidar_esquema, set_state
from emuwarez_uploadrr.services import backup_service as bs
from emuwarez_uploadrr.services.notification_center import list_notifications
from emuwarez_uploadrr.services.settings import get_setting_canonical, set_setting
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


def test_deja_las_ultimas_cinco_de_antes_de_restaurar_y_nunca_la_restaurada_ni_la_nueva(cfg):
    carpeta = bs.get_backup_dir()
    restaurada = carpeta / f"{bs.PREFIJO_ANTES_DE_RESTAURAR}20260101_000000_with_secrets.tar.gz"
    os.replace(bs.create_config_backup()["archive_path"], restaurada)
    # Con el reloj atrasado, la nueva y la restaurada son las más antiguas: tampoco se van.
    for dia in range(1, 7):
        (carpeta / f"{bs.PREFIJO_ANTES_DE_RESTAURAR}2027010{dia}_000000_with_secrets.tar.gz").write_bytes(b"x")

    previa = _restaurar(restaurada)["copia_previa"]

    quedan = sorted(c.name for c in carpeta.glob(f"{bs.PREFIJO_ANTES_DE_RESTAURAR}*"))
    assert len(quedan) == 7 and quedan[:2] == [restaurada.name, previa]
    assert f"{bs.PREFIJO_ANTES_DE_RESTAURAR}20270101_000000_with_secrets.tar.gz" not in quedan


def test_deshacer_devuelve_tambien_las_claves_que_estaban_vacias(cfg):
    with db() as conn:
        set_setting(conn, "tmdb.api_key", "clave-de-la-copia")
    con_clave = bs.create_config_backup(include_secrets=True, confirmation=bs.RESTORE_CONFIRMATION)["archive_path"]
    with db() as conn:
        set_setting(conn, "tmdb.api_key", "")

    previa = _restaurar(con_clave)["copia_previa"]
    assert _restaurar(bs.get_backup_dir() / previa)["ok"]
    with db() as conn:
        assert not get_setting_canonical(conn, "tmdb.api_key")


def test_deshacer_quita_los_ficheros_con_claves_que_trajo_la_restauracion(cfg):
    traidos = [cfg / "config.json", cfg / "Uploadrr" / "data" / "cookies" / "emu.pkl"]
    for f in traidos:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("clave")
    con_claves = bs.create_config_backup(include_secrets=True, confirmation=bs.RESTORE_CONFIRMATION)["archive_path"]
    for f in traidos:
        f.unlink()

    previa = _restaurar(con_claves)["copia_previa"]
    assert all(f.exists() for f in traidos)
    registro = cfg / "logs" / "app.log"  # lo que no lleva claves se queda aunque la copia no lo tenga
    registro.parent.mkdir()
    registro.write_text("x")

    assert _restaurar(bs.get_backup_dir() / previa)["ok"]
    assert not any(f.exists() for f in traidos) and registro.exists()


@solo_posix
def test_deshacer_no_borra_nada_detras_de_un_enlace(cfg):
    registro = cfg / "logs" / "app.log"
    registro.parent.mkdir()
    registro.write_text("x")
    (cfg / "Uploadrr" / "data").mkdir(parents=True)
    (cfg / "Uploadrr" / "data" / "cookies").symlink_to(registro.parent, target_is_directory=True)

    previa = _restaurar(bs.create_config_backup()["archive_path"])["copia_previa"]
    assert _restaurar(bs.get_backup_dir() / previa)["ok"]
    assert registro.exists()


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
    _marcar("después")

    for corte in (len(datos) // 2, 2):  # a medias, o solo la cabecera de gzip
        truncada.write_bytes(datos[:corte])
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


def test_dos_copias_en_el_mismo_segundo_no_se_mezclan(cfg, monkeypatch):
    """Compartían el .part: la segunda lo vaciaba y la primera escribía en la ya renombrada, o fallaba al renombrar."""
    class Reloj(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime(2026, 11, 20, 12, 0, 0)

    monkeypatch.setattr(bs, "datetime", types.SimpleNamespace(datetime=Reloj))
    original = bs._add_db_snapshot
    otra: list[dict] = []

    def _con_otra_a_la_vez(*args, **kwargs):
        if not otra:
            otra.append({})
            otra[0] = bs.create_config_backup()
        original(*args, **kwargs)

    monkeypatch.setattr(bs, "_add_db_snapshot", _con_otra_a_la_vez)
    r = bs.create_config_backup()

    assert r["ok"] and otra[0]["ok"]
    with tarfile.open(r["archive_path"], "r:gz") as tar:
        assert "emuwarez_uploadrr.db" in tar.getnames()
    assert not list(bs.get_backup_dir().glob(".*.part"))
