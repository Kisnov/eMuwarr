"""El config de Uploadrr-EMU de cada subida (`--config`): el mismo contenido que el staging en el config.yaml compartido.

Los `.yaml` de `fixtures/config_uploadrr_por_subida/` salieron del staging tal como estaba antes de que existiera el
fichero por subida (dev 05e505f4). Con REGENERAR_CONFIG_DORADA=1 se reescriben desde el staging; revisa el diff.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from emuwarez_uploadrr.database import connect, db_path, init_db
from emuwarez_uploadrr.services.image_hosts import set_config
from emuwarez_uploadrr.services.settings import set_setting
from emuwarez_uploadrr.services.upload_execution import build_uploadrr_execution_command, get_upload_job
from emuwarez_uploadrr.services.upload_execution import config as ue
from emuwarez_uploadrr.services.uploadrr_eventos import admite_config, ruta_eventos
from emuwarez_uploadrr.web.app import _restore_interrupted_config
from tests.test_eventos_uploadrr import CONTRATO, _crear_job, _lanzar, _poner_ajustes, _terminal

DORADOS = Path(__file__).parent / "fixtures" / "config_uploadrr_por_subida"

# Un config de usuario con todo lo que toca el staging, comentarios incluidos.
BASE_COMPLETA = """\
# Config de prueba: los comentarios tienen que sobrevivir.
DEFAULT:
  tmdb: "CLAVE_TMDB_VIEJA"  # la de antes
  add_logo: true
  always_anon: true
  default_torrent_client: watch
  screens: 4

AUTO:
  keep_quiet: true
  delay: 0

SIGNATURES:
  use_global_sigs: default
  movie: "[img]https://i.ibb.co/5gt00Zv4/uploadrr-signature.webp[/img]"
  tv: "[img]https://i.ibb.co/5gt00Zv4/uploadrr-signature.webp[/img]"

SCREENS:
  img_hosts: [imgbox, ptpimg]
  API_KEYS: {'ptpimg': 'CLAVE_A_MANO'}
  per_episode: 2
  screen_count: 6

TRACKERS:
  default_trackers: EMU
  BLU:
    useAPI: true
    api_key: BLU_FALSA
  EMU:
    api_key: EMU_VIEJA
    anon: true
    qbit_tag: emu

TORRENT_CLIENTS:
  qbit: {'torrent_client': 'qbit', 'enable_search': True, 'qbit_url': 'http://127.0.0.1', 'qbit_port': '8080', 'qbit_tag': 'vieja', 'torrent_storage_dir': '/Users/example/torrents', 'content_layout': 'Original'} # un comentario
  watch: {'torrent_client': 'watch', 'watch_folder': '/tmp/watch'}
"""


def _ajustes(**valores) -> None:
    conn = connect()
    init_db(conn)
    hosts = valores.pop("hosts", None)
    if hosts is not None:
        set_config(conn, hosts[0], hosts[1])
    for clave, valor in valores.items():
        set_setting(conn, clave.replace("__", "."), valor)
    conn.commit()
    conn.close()


def _completo(uploadrr: Path) -> None:
    (uploadrr / "data" / "config.yaml").write_text(BASE_COMPLETA, encoding="utf-8")
    _ajustes(
        tmdb__api_key="TMDB_NUEVA", emu__api_key="EMU_NUEVA", emu__add_logo="false",
        qbit__url="http://qbit.local:8090", qbit__username="admin", qbit__password="pa:ss#1", qbit__tag="emuwarr",
        hosts=(["pixhost", "imgbb", "lensdump"], {"imgbb": "IMGBB_CLAVE", "lensdump": "LENS_CLAVE"}),
    )


def _desde_el_bak(uploadrr: Path) -> None:
    # Una subida que murió dejó config.yaml stageado: la base es el .bak, y sin ajustes solo cambian los fijos.
    (uploadrr / "data" / "config.yaml").write_text("DEFAULT:\n  tmdb: CLAVE_REAL_QUE_QUEDO\n", encoding="utf-8")
    (uploadrr / "data" / "config.yaml.bak").write_text(
        "DEFAULT:\n  tmdb: ''\n  add_logo: false\nSCREENS:\n  img_hosts: [imgbox]\n", encoding="utf-8")


def _qui_y_almacen(uploadrr: Path) -> None:
    # Solo el proxy de qui, una carpeta de torrents que sí existe (relativa a Uploadrr) y todas las capturas.
    (uploadrr / "torrents").mkdir()
    (uploadrr / "data" / "config.yaml").write_text(
        "DEFAULT:\n  tmdb: x\nTRACKERS:\n  BLU:\n    api_key: sin_useapi\nSCREENS:\n  per_episode: 3\n"
        "TORRENT_CLIENTS:\n  qbit: {'torrent_storage_dir': 'torrents', 'qui_proxy_url': 'http://vieja/proxy'}\n",
        encoding="utf-8")
    _ajustes(qbit__qui_proxy_url=" http://qui:7476/proxy/k ", emu__per_episode_screens="true",
             hosts=(["imgbox"], {}))


ESCENARIOS = {"completo": _completo, "desde_el_bak": _desde_el_bak, "qui_y_almacen": _qui_y_almacen}


def _uploadrr(tmp_path: Path, escenario: str) -> Path:
    uploadrr = tmp_path / "Uploadrr"
    (uploadrr / "data").mkdir(parents=True)
    ESCENARIOS[escenario](uploadrr)
    return uploadrr


@pytest.fixture
def sin_registro(monkeypatch):
    monkeypatch.setattr(ue, "append_upload_log", lambda *a, **k: None)


def _bytes_de(uploadrr: Path) -> dict[str, bytes]:
    return {f.name: f.read_bytes() for f in sorted((uploadrr / "data").iterdir())}


@pytest.mark.parametrize("escenario", ESCENARIOS)
def test_el_staging_da_el_config_dorado(tmp_path, sin_registro, escenario):
    uploadrr = _uploadrr(tmp_path, escenario)
    staged = ue._stage_uploadrr_config(1, str(uploadrr))
    obtenido = staged.read_bytes()
    if os.environ.get("REGENERAR_CONFIG_DORADA"):
        DORADOS.mkdir(exist_ok=True)
        (DORADOS / f"{escenario}.yaml").write_bytes(obtenido)
    assert obtenido == (DORADOS / f"{escenario}.yaml").read_bytes()


@pytest.mark.parametrize("escenario", ESCENARIOS)
def test_el_config_de_la_subida_es_el_dorado_y_el_del_usuario_no_se_toca(tmp_path, sin_registro, escenario):
    uploadrr = _uploadrr(tmp_path, escenario)
    antes = _bytes_de(uploadrr)
    ruta = ue.ruta_config(7)

    ue.preparar_config_de_la_subida(7, str(uploadrr), ruta)

    assert ruta == tmp_path / "config_uploadrr" / "7.yaml"
    assert ruta.read_bytes() == (DORADOS / f"{escenario}.yaml").read_bytes()
    assert _bytes_de(uploadrr) == antes  # ni config.yaml ni .bak, que tampoco se crea
    assert ue._config_stage_refcount == {}


def test_cada_subida_tiene_el_suyo(tmp_path, sin_registro):
    uploadrr = _uploadrr(tmp_path, "completo")
    rutas = [ue.ruta_config(1), ue.ruta_config(2), ue.ruta_config(1, reenvio=True)]
    for jid, ruta in zip((1, 2, 1), rutas):
        ue.preparar_config_de_la_subida(jid, str(uploadrr), ruta)
    assert len(set(rutas)) == 3 and rutas[2].name == "1-reenvio.yaml"

    ue.borrar_config_de_la_subida(rutas[0])

    assert not rutas[0].exists()
    assert rutas[1].read_bytes() == rutas[2].read_bytes() == (DORADOS / "completo.yaml").read_bytes()
    assert ue._config_stage_refcount == {}


@pytest.mark.skipif(os.name == "nt", reason="permisos POSIX")
def test_nace_privado_y_no_sigue_un_enlace(tmp_path, sin_registro):
    uploadrr = _uploadrr(tmp_path, "completo")
    ruta = ue.ruta_config(7)
    ruta.parent.mkdir(mode=0o755)
    ruta.parent.chmod(0o755)
    ajeno = tmp_path / "ajeno.txt"
    ajeno.write_text("no me toques", encoding="utf-8")
    ruta.symlink_to(ajeno)  # el que dejó un intento anterior, o alguien

    ue.preparar_config_de_la_subida(7, str(uploadrr), ruta)

    assert ruta.parent.stat().st_mode & 0o777 == 0o700
    assert not ruta.is_symlink() and ruta.stat().st_mode & 0o777 == 0o600
    assert ajeno.read_text(encoding="utf-8") == "no me toques"


def test_sin_proveedores_de_imagenes_no_se_escribe(tmp_path, sin_registro):
    uploadrr = _uploadrr(tmp_path, "completo")
    _ajustes(hosts=([], {}))
    with pytest.raises(ue.ConfigNoPreparada, match="proveedor de imágenes"):
        ue.preparar_config_de_la_subida(7, str(uploadrr), ue.ruta_config(7))
    assert not ue.ruta_config(7).exists()


def test_sin_config_del_usuario_no_se_escribe_y_uploadrr_dira_sin_config(tmp_path, sin_registro):
    uploadrr = tmp_path / "Uploadrr"
    (uploadrr / "data").mkdir(parents=True)
    ue.preparar_config_de_la_subida(7, str(uploadrr), ue.ruta_config(7))
    assert not ue.ruta_config(7).exists()


def test_al_arrancar_se_barren_los_que_quedaron(tmp_path):
    assert ue.barrer_configs_de_subidas() == 0  # sin carpeta
    carpeta = tmp_path / "config_uploadrr"
    carpeta.mkdir()
    for nombre in ("3.yaml", "3-reenvio.yaml"):
        (carpeta / nombre).write_text("TRACKERS:\n  EMU:\n    api_key: de-una-subida-muerta\n", encoding="utf-8")
    eventos = tmp_path / "eventos_uploadrr"
    eventos.mkdir()
    (eventos / "3.jsonl").write_text("", encoding="utf-8")

    _restore_interrupted_config()

    assert list(carpeta.iterdir()) == [] and (eventos / "3.jsonl").exists()


# ---------------------------------------------------------------------------
# El comando y el subproceso de verdad (tests/fixtures/fake_uploadrr_emu.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def db_conn(isolated_db):
    conn = connect(db_path())
    init_db(conn)
    yield conn
    conn.close()


def _contrato_sin_config(carpeta: Path) -> None:
    """El contrato de un Uploadrr-EMU anterior a `--config`."""
    texto = CONTRATO.read_text(encoding="utf-8").replace('  "config": true,\n', "")
    assert '"config"' not in texto
    (carpeta / "contrato_eventos.json").write_text(texto, encoding="utf-8")


@pytest.mark.parametrize(
    "contenido, admite",
    [(None, False), ('{"version": 1}', False), ('{"version": 1, "config": "si"}', False),
     ('{"version": 2, "config": true}', False), ("[1]", False), ('{"version": 1, "config": true}', True)],
)
def test_admite_config_solo_con_la_marca_del_contrato(tmp_path, contenido, admite):
    if contenido is not None:
        (tmp_path / "contrato_eventos.json").write_text(contenido, encoding="utf-8")
    assert admite_config(tmp_path) is admite


def test_el_comando_lleva_config_solo_si_uploadrr_lo_admite(db_conn, tmp_path, fake_uploadrr_emu_dir,
                                                             fake_uploadrr_python_path):
    _poner_ajustes(db_conn, fake_uploadrr_emu_dir, fake_uploadrr_python_path)
    job = get_upload_job(_crear_job(db_conn, tmp_path), conn=db_conn)
    jid = job["id"]
    _contrato_sin_config(fake_uploadrr_emu_dir)
    viejo = build_uploadrr_execution_command(job, conn=db_conn)
    assert "--config" not in viejo["args"] and viejo["config"] is None

    (fake_uploadrr_emu_dir / "contrato_eventos.json").write_bytes(CONTRATO.read_bytes())
    nuevo = build_uploadrr_execution_command(job, conn=db_conn)
    assert nuevo["args"] == viejo["args"] + ["--config", str(ue.ruta_config(jid))]
    assert nuevo["config"] == str(ue.ruta_config(jid))
    reenvio = build_uploadrr_execution_command(job, conn=db_conn, solo_cliente="4242")
    assert reenvio["args"][-2:] == ["--config", str(ue.ruta_config(jid, reenvio=True))]


def _copia_de_lo_leido(tmp_path, monkeypatch) -> Path:
    copia = tmp_path / "lo_que_leyo_uploadrr.json"
    monkeypatch.setenv("FAKE_UPLOADRR_CONFIG_COPIA", str(copia))
    return copia


def test_la_subida_lanza_con_su_config_y_lo_borra(db_conn, tmp_path, monkeypatch, fake_uploadrr_emu_dir,
                                                  fake_uploadrr_python_path):
    copia = _copia_de_lo_leido(tmp_path, monkeypatch)
    usuario = _bytes_de(fake_uploadrr_emu_dir)
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path)
    job = _terminal(db_conn, jid)

    assert job["status"] == "completed"
    assert f"--config {ue.ruta_config(jid)}" in json.loads(job["safe_command_json"])["args_preview"]
    leido = json.loads(copia.read_text(encoding="utf-8"))
    if os.name != "nt":
        assert (leido["modo"], leido["modo_carpeta"]) == (0o600, 0o700)
    assert not ue.ruta_config(jid).exists() and not ruta_eventos(jid).exists()
    assert _bytes_de(fake_uploadrr_emu_dir) == usuario and ue._config_stage_refcount == {}
    # Lo mismo que el staging habría dejado en config.yaml con estos ajustes.
    assert leido["contenido"] == ue._stage_uploadrr_config(jid, str(fake_uploadrr_emu_dir)).read_text(encoding="utf-8")


@pytest.mark.parametrize("guion", [[{"exit": 1}], [{"kill": 9}]], ids=["falla", "muere"])
def test_se_borra_tambien_si_uploadrr_falla_o_lo_matan(db_conn, tmp_path, monkeypatch, fake_uploadrr_emu_dir,
                                                       fake_uploadrr_python_path, guion_uploadrr, guion):
    if os.name == "nt" and guion[0].get("kill"):
        pytest.skip("señales POSIX")
    copia = _copia_de_lo_leido(tmp_path, monkeypatch)
    guion_uploadrr(guion)
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path)
    job = _terminal(db_conn, jid)

    assert job["status"] == "failed" and copia.exists()  # lo tuvo mientras corría
    assert not ue.ruta_config(jid).exists()


def test_con_un_uploadrr_que_no_lo_admite_se_stagea_como_siempre(db_conn, tmp_path, monkeypatch, fake_uploadrr_emu_dir,
                                                                fake_uploadrr_python_path):
    copia = _copia_de_lo_leido(tmp_path, monkeypatch)
    original = (fake_uploadrr_emu_dir / "data" / "config.yaml").read_bytes()
    _contrato_sin_config(fake_uploadrr_emu_dir)
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, contrato=False)
    job = _terminal(db_conn, jid)

    assert job["status"] == "completed"
    assert "--config" not in json.loads(job["safe_command_json"])["args_preview"] and not copia.exists()
    assert (fake_uploadrr_emu_dir / "data" / "config.yaml.bak").read_bytes() == original  # stageado...
    assert (fake_uploadrr_emu_dir / "data" / "config.yaml").read_bytes() == original  # ...y restaurado
    assert not (tmp_path / "config_uploadrr").exists() and ue._config_stage_refcount == {}
