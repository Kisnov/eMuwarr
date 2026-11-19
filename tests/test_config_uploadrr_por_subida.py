"""El config de Uploadrr-EMU de cada subida (`--config`), la migración del config.yaml.bak y lo que pasa con un
Uploadrr-EMU que no lo admite.

Los `.yaml` de `fixtures/config_uploadrr_por_subida/` salieron del staging que había antes del fichero por subida
(dev 05e505f4) y prueban que el contenido no cambió. Con REGENERAR_CONFIG_DORADA=1 el test del dorado los reescribe
desde `preparar_config_de_la_subida`; revisa el diff antes de subirlos.
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
from emuwarez_uploadrr.web.app import _preparar_configs_de_uploadrr
from tests.test_eventos_uploadrr import CONTRATO, _crear_job, _esperar, _lanzar, _poner_ajustes, _terminal

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





def _qui_y_almacen(uploadrr: Path) -> None:
    # Solo el proxy de qui, una carpeta de torrents que sí existe (relativa a Uploadrr) y todas las capturas.
    (uploadrr / "torrents").mkdir()
    (uploadrr / "data" / "config.yaml").write_text(
        "DEFAULT:\n  tmdb: x\nTRACKERS:\n  BLU:\n    api_key: sin_useapi\nSCREENS:\n  per_episode: 3\n"
        "TORRENT_CLIENTS:\n  qbit: {'torrent_storage_dir': 'torrents', 'qui_proxy_url': 'http://vieja/proxy'}\n",
        encoding="utf-8")
    _ajustes(qbit__qui_proxy_url=" http://qui:7476/proxy/k ", emu__per_episode_screens="true",
             hosts=(["imgbox"], {}))


ESCENARIOS = {"completo": _completo, "qui_y_almacen": _qui_y_almacen}


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
def test_el_config_de_la_subida_es_el_dorado_y_el_del_usuario_no_se_toca(tmp_path, sin_registro, escenario):
    uploadrr = _uploadrr(tmp_path, escenario)
    antes = _bytes_de(uploadrr)
    ruta = ue.ruta_config(7)

    ue.preparar_config_de_la_subida(7, str(uploadrr), ruta)

    assert ruta == tmp_path / "config_uploadrr" / "7.yaml"
    if os.environ.get("REGENERAR_CONFIG_DORADA"):
        DORADOS.mkdir(exist_ok=True)
        (DORADOS / f"{escenario}.yaml").write_bytes(ruta.read_bytes())
    assert ruta.read_bytes() == (DORADOS / f"{escenario}.yaml").read_bytes()
    assert _bytes_de(uploadrr) == antes  # ni config.yaml ni .bak, que tampoco se crea


def test_cada_subida_tiene_el_suyo(tmp_path, sin_registro):
    uploadrr = _uploadrr(tmp_path, "completo")
    rutas = [ue.ruta_config(1), ue.ruta_config(2), ue.ruta_config(1, reenvio=True)]
    for jid, ruta in zip((1, 2, 1), rutas):
        ue.preparar_config_de_la_subida(jid, str(uploadrr), ruta)
    assert len(set(rutas)) == 3 and rutas[2].name == "1-reenvio.yaml"

    ue.borrar_config_de_la_subida(rutas[0])

    assert not rutas[0].exists()
    assert rutas[1].read_bytes() == rutas[2].read_bytes() == (DORADOS / "completo.yaml").read_bytes()


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

    _preparar_configs_de_uploadrr()

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
    assert _bytes_de(fake_uploadrr_emu_dir) == usuario
    # Lo que leyó es lo que prepara preparar_config_de_la_subida con estos ajustes.
    esperado = tmp_path / "esperado.yaml"
    ue.preparar_config_de_la_subida(jid, str(fake_uploadrr_emu_dir), esperado)
    assert leido["contenido"] == esperado.read_text(encoding="utf-8")


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


def test_con_un_uploadrr_que_no_lo_admite_la_subida_falla_sin_lanzarlo(db_conn, tmp_path, monkeypatch,
                                                                       fake_uploadrr_emu_dir,
                                                                       fake_uploadrr_python_path):
    copia = _copia_de_lo_leido(tmp_path, monkeypatch)
    usuario = _bytes_de(fake_uploadrr_emu_dir)
    _contrato_sin_config(fake_uploadrr_emu_dir)
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, contrato=False)
    # No hay proceso, así que no hay línea «Proceso …» que esperar (lo que espera _terminal).
    assert _esperar(lambda: get_upload_job(jid, conn=db_conn)["status"] == "failed"), "la subida no falló"
    job = get_upload_job(jid, conn=db_conn)

    assert job["error_message"] == str(ue.sin_config_por_subida(str(fake_uploadrr_emu_dir)))
    assert "es anterior a esta versión de eMuwarr" in job["error_message"]
    assert not copia.exists()  # Uploadrr-EMU no llegó a ejecutarse
    assert _bytes_de(fake_uploadrr_emu_dir) == usuario  # config.yaml intacto y sin .bak
    assert "config.yaml.bak" not in usuario and not (tmp_path / "config_uploadrr").exists()


# ---------------------------------------------------------------------------
# migrar_config_bak: el config.yaml.bak de las versiones anteriores pasa a ser config.yaml
# ---------------------------------------------------------------------------

solo_con_enlaces = pytest.mark.skipif(os.name == "nt", reason="enlaces simbólicos POSIX")


def _uploadrr_en_ajustes(carpeta: Path) -> None:
    conn = connect()
    init_db(conn)
    set_setting(conn, "uploadrr.path", str(carpeta))
    conn.commit()
    conn.close()


def test_migrar_sin_bak_no_cambia_nada(tmp_path):
    uploadrr = _uploadrr(tmp_path, "completo")
    _uploadrr_en_ajustes(uploadrr)
    antes = _bytes_de(uploadrr)

    assert ue.migrar_config_bak() is False
    assert _bytes_de(uploadrr) == antes


def test_migrar_con_un_bak_distinto_lo_pone_en_config_y_lo_aparta(tmp_path):
    uploadrr = _uploadrr(tmp_path, "completo")
    _uploadrr_en_ajustes(uploadrr)
    data = uploadrr / "data"
    (data / "config.yaml").write_text("DEFAULT:\n  tmdb: CLAVE_STAGEADA\n", encoding="utf-8")
    (data / "config.yaml.bak").write_bytes(b"DEFAULT:\n  tmdb: ''\n  add_logo: false\n")

    assert ue.migrar_config_bak() is True

    assert (data / "config.yaml").read_bytes() == b"DEFAULT:\n  tmdb: ''\n  add_logo: false\n"
    assert (data / "config.yaml.migrado").read_bytes() == (data / "config.yaml").read_bytes()
    assert not (data / "config.yaml.bak").exists()
    assert ue.migrar_config_bak() is False  # una sola vez


@pytest.mark.parametrize("donde", ["al escribir", "al sustituir"])
def test_migrar_si_falla_a_medias_no_deja_el_config_cortado(tmp_path, monkeypatch, donde):
    """Se escribe aparte y se renombra: un fallo (disco lleno) a media escritura o al sustituir deja config.yaml y
    el .bak como estaban."""
    from emuwarez_uploadrr.services.upload_execution import config as cfg

    uploadrr = _uploadrr(tmp_path, "completo")
    _uploadrr_en_ajustes(uploadrr)
    data = uploadrr / "data"
    antes, bak = b"DEFAULT:\n  tmdb: CLAVE_STAGEADA\n", b"DEFAULT:\n  tmdb: ''\n  add_logo: false\n"
    (data / "config.yaml").write_bytes(antes)
    (data / "config.yaml.bak").write_bytes(bak)
    fdopen = cfg.os.fdopen

    class EscribeLaMitad:
        def __init__(self, fd, modo):
            self.fichero = fdopen(fd, modo)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.fichero.close()

        def write(self, datos):
            self.fichero.write(datos[: len(datos) // 2])
            raise OSError(28, "No space left on device")

    def disco_lleno(*_a):
        raise OSError(28, "No space left on device")

    if donde == "al escribir":
        monkeypatch.setattr(cfg.os, "fdopen", EscribeLaMitad)
    else:
        monkeypatch.setattr(cfg.os, "replace", disco_lleno)
    with pytest.raises(OSError):
        ue.migrar_config_bak()

    assert (data / "config.yaml").read_bytes() == antes and (data / "config.yaml.bak").read_bytes() == bak
    assert sorted(f.name for f in data.iterdir()) == ["config.yaml", "config.yaml.bak"]  # sin el temporal


def test_migrar_con_un_bak_igual_solo_lo_renombra(tmp_path):
    uploadrr = _uploadrr(tmp_path, "completo")
    _uploadrr_en_ajustes(uploadrr)
    data = uploadrr / "data"
    (data / "config.yaml.bak").write_bytes((data / "config.yaml").read_bytes())
    antes = (data / "config.yaml").read_bytes()

    assert ue.migrar_config_bak() is True

    assert (data / "config.yaml").read_bytes() == antes
    assert (data / "config.yaml.migrado").read_bytes() == antes
    assert not (data / "config.yaml.bak").exists()


def test_migrar_sin_config_yaml_crea_el_config_desde_el_bak(tmp_path):
    uploadrr = tmp_path / "Uploadrr"
    (uploadrr / "data").mkdir(parents=True)
    _uploadrr_en_ajustes(uploadrr)
    (uploadrr / "data" / "config.yaml.bak").write_bytes(b"DEFAULT:\n  tmdb: ''\n")

    assert ue.migrar_config_bak() is True
    assert (uploadrr / "data" / "config.yaml").read_bytes() == b"DEFAULT:\n  tmdb: ''\n"


@solo_con_enlaces
def test_migrar_en_la_imagen_busca_el_bak_junto_al_destino_del_enlace(tmp_path):
    """Como la imagen: data/config.yaml es un enlace a /config/Uploadrr, y el .bak vive junto a su destino."""
    estado = tmp_path / "estado"
    estado.mkdir()
    data = tmp_path / "Uploadrr" / "data"
    data.mkdir(parents=True)
    (estado / "config.yaml").write_bytes(b"DEFAULT:\n  tmdb: CLAVE_STAGEADA\n")
    (estado / "config.yaml.bak").write_bytes(b"DEFAULT:\n  tmdb: ''\n")
    (data / "config.yaml").symlink_to(estado / "config.yaml")
    _uploadrr_en_ajustes(tmp_path / "Uploadrr")

    assert ue.migrar_config_bak() is True

    assert (data / "config.yaml").is_symlink()
    assert (estado / "config.yaml").read_bytes() == b"DEFAULT:\n  tmdb: ''\n"
    assert (estado / "config.yaml.migrado").read_bytes() == b"DEFAULT:\n  tmdb: ''\n"
    assert sorted(f.name for f in estado.iterdir()) == ["config.yaml", "config.yaml.migrado"]
    assert [f.name for f in data.iterdir()] == ["config.yaml"]


def test_al_arrancar_se_migra_el_bak_y_se_barren_los_config(tmp_path):
    uploadrr = _uploadrr(tmp_path, "completo")
    _uploadrr_en_ajustes(uploadrr)
    data = uploadrr / "data"
    (data / "config.yaml.bak").write_bytes(b"DEFAULT:\n  tmdb: ''\n")
    carpeta = tmp_path / "config_uploadrr"
    carpeta.mkdir()
    (carpeta / "3.yaml").write_text("TRACKERS:\n  EMU:\n    api_key: de-una-subida-muerta\n", encoding="utf-8")

    _preparar_configs_de_uploadrr()

    assert (data / "config.yaml").read_bytes() == b"DEFAULT:\n  tmdb: ''\n"
    assert (data / "config.yaml.migrado").exists() and not (data / "config.yaml.bak").exists()
    assert list(carpeta.iterdir()) == []


