"""El config de Uploadrr-EMU de cada subida (`--config`): el mismo contenido que el staging en el config.yaml compartido.

Los `.yaml` de `fixtures/config_uploadrr_por_subida/` salieron del staging tal como estaba antes de que existiera el
fichero por subida (dev 05e505f4). Con REGENERAR_CONFIG_DORADA=1 se reescriben desde el staging; revisa el diff.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from emuwarez_uploadrr.database import connect, init_db
from emuwarez_uploadrr.services.image_hosts import set_config
from emuwarez_uploadrr.services.settings import set_setting
from emuwarez_uploadrr.services.upload_execution import config as ue

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


@pytest.fixture(autouse=True)
def _sin_registro(monkeypatch):
    monkeypatch.setattr(ue, "append_upload_log", lambda *a, **k: None)


@pytest.mark.parametrize("escenario", ESCENARIOS)
def test_el_staging_da_el_config_dorado(tmp_path, escenario):
    uploadrr = _uploadrr(tmp_path, escenario)
    staged = ue._stage_uploadrr_config(1, str(uploadrr))
    obtenido = staged.read_bytes()
    if os.environ.get("REGENERAR_CONFIG_DORADA"):
        DORADOS.mkdir(exist_ok=True)
        (DORADOS / f"{escenario}.yaml").write_bytes(obtenido)
    assert obtenido == (DORADOS / f"{escenario}.yaml").read_bytes()
