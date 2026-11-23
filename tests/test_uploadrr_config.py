"""Los editores de líneas del config.yaml de Uploadrr-EMU y el config que se prepara para cada subida.

Cada test trabaja bajo tmp_path: nada toca una instalación real de Uploadrr/.
"""
from __future__ import annotations

import pathlib

import pytest

from emuwarez_uploadrr.services.uploadrr_yaml import get_block_key, get_child_inline_mapping
from emuwarez_uploadrr.services.uploadrr_config import (
    FIRMA_ACTUAL,
    FIRMA_ANONIMA,
    FIRMAS_ANTERIORES,
    QBIT_CLIENT_KEY,
    SCREENS_API_KEY_HOSTS,
    read_config_lines,
    stage_emu_config,
    stage_firma_actual,
    stage_image_hosts,
    stage_qbittorrent_config,
    stage_tmdb_key,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_read_config_lines_missing_file_returns_empty(tmp_path):
    assert read_config_lines(tmp_path / "config.yaml") == []


def test_stage_tmdb_key_sets_default_tmdb():
    lines = ["DEFAULT:", "  other: 1"]
    staged, message = stage_tmdb_key(lines, "TMDB_REAL_KEY")
    assert "TMDB_REAL_KEY" in "\n".join(staged)
    assert "creado" in message or "actualizado" in message


def test_stage_tmdb_key_does_not_mutate_original_lines():
    lines = ["DEFAULT:", "  tmdb: 'old'"]
    staged, _ = stage_tmdb_key(lines, "new_key")
    assert lines == ["DEFAULT:", "  tmdb: 'old'"]
    assert "new_key" in "\n".join(staged) and "old" not in "\n".join(staged)


def test_stage_emu_config_sets_api_key_and_anon():
    lines: list[str] = []
    staged, message = stage_emu_config(lines, api_key="EMU_REAL_KEY", anon=True)
    text = "\n".join(staged)
    assert "EMU_REAL_KEY" in text
    assert "anon: True" in text or "anon: true" in text.lower()


def test_stage_image_hosts_sets_ordered_img_hosts_list():
    lines = ["SCREENS:", "  img_hosts: [imgbox]"]
    staged, message = stage_image_hosts(lines, img_hosts=["pixhost", "imgbb", "imgbox"])
    text = "\n".join(staged)
    assert "img_hosts: [pixhost, imgbb, imgbox]" in text
    assert "creado" in message or "actualizado" in message


def test_stage_image_hosts_sets_api_keys_inline_mapping():
    lines: list[str] = []
    staged, _ = stage_image_hosts(lines, api_keys={"imgbb": "IMGBB_REAL_KEY", "lensdump": "LENSDUMP_REAL_KEY"})
    text = "\n".join(staged)
    assert "IMGBB_REAL_KEY" in text
    assert "LENSDUMP_REAL_KEY" in text
    assert "API_KEYS" in text


def test_stage_image_hosts_skips_untouched_sections_when_nothing_given():
    lines = ["SCREENS:", "  img_hosts: [imgbox]"]
    staged, message = stage_image_hosts(lines)
    assert staged == lines
    assert message == "SCREENS sin cambios"


def test_stage_image_hosts_only_stages_hosts_that_take_a_key():
    """imghost.dev estaba excluido suponiendo que su clave iba en otro bloque
    del config del fork. Es un CheveretoTemplate como los demás y lee
    SCREENS.API_KEYS, así que sin ella revienta."""
    assert "imghost" in SCREENS_API_KEY_HOSTS
    # Los que no llevan clave no se cuelan.
    assert "imgbox" not in SCREENS_API_KEY_HOSTS


def test_stage_qbittorrent_config_sets_credentials_and_default_client():
    lines: list[str] = []
    staged, message = stage_qbittorrent_config(
        lines, url="http://192.168.1.10:8080", username="admin", password="secret",
    )
    mapping = get_child_inline_mapping(staged, "TORRENT_CLIENTS", QBIT_CLIENT_KEY)
    assert mapping["qbit_url"] == "http://192.168.1.10"
    assert mapping["qbit_port"] == "8080"
    assert mapping["qbit_user"] == "admin"
    assert mapping["qbit_pass"] == "secret"
    assert mapping["torrent_client"] == "qbit"
    text = "\n".join(staged)
    assert "default_torrent_client: 'qbit'" in text
    assert "creado" in message or "actualizado" in message


def test_stage_qbittorrent_config_overrides_stale_default_torrent_client():
    # Confirmed against the fork's own bundled example config.yaml: it ships
    # with "watch", not "qbit" — this is the actual bug that made a job
    # finish "successfully" while silently never touching qBittorrent's API.
    lines = ["DEFAULT:", "  default_torrent_client: watch"]
    staged, _ = stage_qbittorrent_config(lines, url="http://x:8080", username="u", password="p")
    text = "\n".join(staged)
    assert "default_torrent_client: watch" not in text
    assert "default_torrent_client: 'qbit'" in text


def test_stage_qbittorrent_config_preserves_unmanaged_existing_keys(tmp_path):
    # torrent_storage_dir/content_layout/enable_search aren't fields we
    # manage — overwriting the whole inline mapping wholesale would silently
    # wipe out whatever the user (or the fork's own template) already had
    # configured there.
    lines = [
        "TORRENT_CLIENTS:",
        "  qbit: {'torrent_client': 'qbit', 'enable_search': True, 'qbit_url': 'http://old', "
        f"'torrent_storage_dir': '{tmp_path.as_posix()}', 'content_layout': 'Original'}}",
    ]
    staged, _ = stage_qbittorrent_config(lines, url="http://new:9090", username="u2", password="p2")
    mapping = get_child_inline_mapping(staged, "TORRENT_CLIENTS", QBIT_CLIENT_KEY)
    assert mapping["qbit_url"] == "http://new"
    assert mapping["enable_search"] is True
    assert mapping["torrent_storage_dir"] == tmp_path.as_posix()
    assert mapping["content_layout"] == "Original"


def test_stage_qbittorrent_config_drops_a_storage_dir_that_does_not_exist():
    """El config de ejemplo del fork trae una ruta de macOS y cada subida
    acababa con "Invalid torrent_storage_dir path" en rojo. Si la carpeta no
    existe no hay .torrent que reutilizar, y buscar en qBittorrent tampoco
    sirve de nada."""
    lines = [
        "TORRENT_CLIENTS:",
        "  qbit: {'torrent_client': 'qbit', 'enable_search': True, "
        "'torrent_storage_dir': '/Users/example/Library/Application Support/qBittorrent/BT_backup', "
        "'content_layout': 'Original'}",
    ]
    staged, _ = stage_qbittorrent_config(lines, url="http://x:8080", username="u", password="p")
    mapping = get_child_inline_mapping(staged, "TORRENT_CLIENTS", QBIT_CLIENT_KEY)
    assert "torrent_storage_dir" not in mapping
    assert mapping["enable_search"] is False
    assert mapping["content_layout"] == "Original"


def test_stage_qbittorrent_config_resolves_a_relative_storage_dir_against_uploadrr(tmp_path):
    """El fork corre con cwd en su carpeta, así que una ruta relativa se
    comprueba contra ella y no contra la carpeta de la app."""
    (tmp_path / "BT_backup").mkdir()
    lines = [
        "TORRENT_CLIENTS:",
        "  qbit: {'torrent_client': 'qbit', 'enable_search': True, 'torrent_storage_dir': 'BT_backup'}",
    ]
    staged, _ = stage_qbittorrent_config(
        lines, url="http://x:8080", username="u", password="p", base_dir=str(tmp_path),
    )
    mapping = get_child_inline_mapping(staged, "TORRENT_CLIENTS", QBIT_CLIENT_KEY)
    assert mapping["torrent_storage_dir"] == "BT_backup"
    assert mapping["enable_search"] is True


def test_stage_qbittorrent_config_treats_an_unreadable_storage_dir_as_missing(monkeypatch):
    """Si comprobar la carpeta da error (sin permisos), se trata como que no
    está en vez de tumbar el staging entero."""
    llamadas = []

    def sin_permiso(*args, **kwargs):
        llamadas.append(args)
        raise PermissionError("denegado")

    monkeypatch.setattr("os.stat", sin_permiso)
    lines = [
        "TORRENT_CLIENTS:",
        "  qbit: {'torrent_client': 'qbit', 'enable_search': True, 'torrent_storage_dir': '/protegida/BT_backup'}",
    ]
    staged, _ = stage_qbittorrent_config(lines, url="http://x:8080", username="u", password="p")
    mapping = get_child_inline_mapping(staged, "TORRENT_CLIENTS", QBIT_CLIENT_KEY)
    assert "torrent_storage_dir" not in mapping
    assert mapping["enable_search"] is False
    # Que de verdad se llegó a mirar la carpeta, no que se saltó la comprobación.
    assert llamadas


def test_stage_blu_api_off_turns_off_the_blutopia_lookup():
    """Con useAPI activado el fork pregunta a Blutopia por el nombre de cada
    fichero ("Failed to search: HTTP 400" con la clave de ejemplo)."""
    from emuwarez_uploadrr.services.uploadrr_config import stage_blu_api_off
    lines = [
        "TRACKERS:",
        "  BLU:",
        "    useAPI: true",
        "    api_key: \"BLU_API_KEY\"",
        "  EMU:",
        "    useAPI: true",
    ]
    staged, message = stage_blu_api_off(lines)
    assert staged[2] == "    useAPI: false"
    # El resto, incluido el useAPI de otro tracker, no se toca.
    assert staged[3:] == lines[3:]
    assert message == "TRACKERS.BLU.useAPI actualizado"


def test_stage_blu_api_off_leaves_a_config_without_blu_alone():
    from emuwarez_uploadrr.services.uploadrr_config import stage_blu_api_off
    lines = ["TRACKERS:", "  EMU:", "    useAPI: true"]
    staged, message = stage_blu_api_off(lines)
    assert staged == lines
    assert message == "TRACKERS.BLU sin cambios"


def test_get_child_inline_mapping_returns_empty_when_absent():
    assert get_child_inline_mapping([], "TORRENT_CLIENTS", "qbit") == {}


def test_never_touches_real_uploadrr_path(tmp_path):
    """Structural guard: config_path is always caller-supplied (tmp_path in
    tests) — nothing in this module hardcodes or defaults to a real
    Uploadrr/ install path."""
    import emuwarez_uploadrr.services.uploadrr_config as mod
    import inspect

    source = inspect.getsource(mod)
    assert "uploadrr_dir(" not in source
    assert "Uploadrr/data/config.yaml" not in source


def test_stage_qbittorrent_splits_the_url_the_way_the_fork_expects():
    """La app guarda una sola url con el puerto dentro; el cliente qBittorrent
    del fork quiere host y puerto en dos claves (src/clients.py:
    host=qbit_url, port=qbit_port)."""
    from emuwarez_uploadrr.services.uploadrr_config import stage_qbittorrent_config
    from emuwarez_uploadrr.services.uploadrr_yaml import get_child_inline_mapping
    lines = ["TORRENT_CLIENTS:", "  qbit: {'torrent_client': 'qbit'}"]
    staged, _ = stage_qbittorrent_config(
        lines, url="http://192.168.0.136:8082", username="u", password="p")
    m = get_child_inline_mapping(staged, "TORRENT_CLIENTS", "qbit")
    assert m["qbit_url"] == "http://192.168.0.136"
    assert m["qbit_port"] == "8082"


def test_stage_qbittorrent_writes_the_tag_the_fork_reads():
    """qbit_tag es la clave que el fork aplica al torrent (clients.py:634)."""
    from emuwarez_uploadrr.services.uploadrr_config import stage_qbittorrent_config
    from emuwarez_uploadrr.services.uploadrr_yaml import get_child_inline_mapping
    lines = ["TORRENT_CLIENTS:", "  qbit: {'torrent_client': 'qbit'}"]
    staged, _ = stage_qbittorrent_config(
        lines, url="http://h:1", username="u", password="p", tag="Subidas")
    assert get_child_inline_mapping(staged, "TORRENT_CLIENTS", "qbit")["qbit_tag"] == "Subidas"


def test_clearing_the_tag_removes_it_from_the_fork_config():
    """Vaciar el campo en Ajustes tiene que quitar la etiqueta de verdad, no
    dejar la anterior puesta para siempre."""
    from emuwarez_uploadrr.services.uploadrr_config import stage_qbittorrent_config
    from emuwarez_uploadrr.services.uploadrr_yaml import get_child_inline_mapping
    lines = ["TORRENT_CLIENTS:", "  qbit: {'torrent_client': 'qbit', 'qbit_tag': 'Vieja'}"]
    for tag in ("", None, "   "):
        staged, _ = stage_qbittorrent_config(
            lines, url="http://h:1", username="u", password="p", tag=tag)
        assert "qbit_tag" not in get_child_inline_mapping(staged, "TORRENT_CLIENTS", "qbit")


def test_stage_add_logo_writes_the_key_the_fork_reads():
    """DEFAULT.add_logo es lo que mira src/utils/descgen.py del fork para
    incrustar el logo en la descripción."""
    from emuwarez_uploadrr.services.uploadrr_config import stage_add_logo
    from emuwarez_uploadrr.services.uploadrr_yaml import get_block_key
    lines = ["DEFAULT:", "  tmdb: xxx", "  add_logo: true"]
    for enabled, expected in ((False, "false"), (True, "true")):
        staged, _ = stage_add_logo(lines, enabled)
        assert get_block_key(staged, "DEFAULT", "add_logo") == expected


def test_stage_add_logo_creates_the_key_when_the_config_lacks_it():
    from emuwarez_uploadrr.services.uploadrr_config import stage_add_logo
    from emuwarez_uploadrr.services.uploadrr_yaml import get_block_key
    staged, _ = stage_add_logo(["DEFAULT:", "  tmdb: xxx"], False)
    assert get_block_key(staged, "DEFAULT", "add_logo") == "false"


def test_stage_per_episode_screens_leaves_only_the_first_episode_by_default():
    """SCREENS.per_episode a 0 es lo que hace que el fork solo capture el primer
    capítulo de un pack; con todos se respeta el valor del config."""
    from emuwarez_uploadrr.services.uploadrr_config import stage_per_episode_screens
    from emuwarez_uploadrr.services.uploadrr_yaml import get_block_key
    lines = ["SCREENS:", "  per_episode: 3 # for tv will take x screens for every episode"]
    staged, _ = stage_per_episode_screens(lines, False)
    assert get_block_key(staged, "SCREENS", "per_episode") == "0"
    staged, _ = stage_per_episode_screens(lines, True)
    assert staged == lines


def test_la_subida_captura_solo_el_primer_capitulo_salvo_que_se_pida(tmp_path, monkeypatch):
    base = "DEFAULT:\n  tmdb: ''\nSCREENS:\n  per_episode: 3\n"
    assert _per_episode(_preparar(tmp_path, monkeypatch, base)) == "0"

    _poner_ajuste("emu.per_episode_screens", "true")
    assert _per_episode(_preparar(tmp_path, monkeypatch, base, job_id=2)) == "3"


def test_la_subida_no_se_prepara_sin_proveedores_de_imagenes(tmp_path, monkeypatch):
    """Si el usuario quita todos los proveedores entre la comprobación previa y el lanzamiento, Uploadrr no arranca
    con los de su propio config.yaml: el trabajo falla con el motivo y no se escribe nada."""
    from emuwarez_uploadrr.database import connect, init_db
    from emuwarez_uploadrr.services.upload_execution import config as ue
    from emuwarez_uploadrr.services.image_hosts import set_config
    config = tmp_path / "Uploadrr" / "data" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("DEFAULT:\n  tmdb: ''\nSCREENS:\n  img_hosts: [imgbox]\n", encoding="utf-8")
    monkeypatch.setattr(ue, "append_upload_log", lambda *a, **k: None)
    conn = connect()
    init_db(conn)
    set_config(conn, [], {})
    conn.commit()
    conn.close()
    ruta = tmp_path / "subida.yaml"
    with pytest.raises(ue.ConfigNoPreparada, match="proveedor de imágenes"):
        ue.preparar_config_de_la_subida(1, str(tmp_path / "Uploadrr"), ruta)
    assert not ruta.exists()
    assert "img_hosts: [imgbox]" in config.read_text(encoding="utf-8")


def test_every_host_that_needs_a_key_gets_it_staged():
    """La lista de claves que se escriben en el config del fork estaba puesta a
    mano y se quedó en cinco: hamster e imghost.dev también necesitan clave y
    no estaban. Configurabas la suya en Ajustes, se guardaba, y la subida
    fallaba igual porque nunca llegaba allí.

    Derivarla de IMAGE_HOSTS lo arregla; este test es lo que impide que vuelva
    a separarse cuando se añada el octavo proveedor."""
    from emuwarez_uploadrr.services.uploadrr_config import SCREENS_API_KEY_HOSTS
    from emuwarez_uploadrr.services.image_hosts import IMAGE_HOSTS
    esperados = {h["id"] for h in IMAGE_HOSTS if h["needs_key"]}
    assert set(SCREENS_API_KEY_HOSTS) == esperados
    # Y ninguno de los que NO necesitan clave se cuela.
    assert not (set(SCREENS_API_KEY_HOSTS) & {h["id"] for h in IMAGE_HOSTS if not h["needs_key"]})


def test_stage_qbittorrent_config_with_qui_only():
    """Con el proxy de qui no hacen falta url ni credenciales: Uploadrr-EMU habla por qui."""
    lines = ["DEFAULT:", "  default_torrent_client: watch"]
    staged, _ = stage_qbittorrent_config(lines, qui_proxy_url=" http://qui:7476/proxy/clave ")
    mapping = get_child_inline_mapping(staged, "TORRENT_CLIENTS", QBIT_CLIENT_KEY)
    assert mapping["qui_proxy_url"] == "http://qui:7476/proxy/clave"
    assert "qbit_url" not in mapping and "qbit_pass" not in mapping
    assert "default_torrent_client: 'qbit'" in "\n".join(staged)


def test_stage_qbittorrent_config_without_qui_drops_a_previous_one():
    lines = ["TORRENT_CLIENTS:", "  qbit: {'torrent_client': 'qbit', 'qui_proxy_url': 'http://qui/proxy/vieja'}"]
    staged, _ = stage_qbittorrent_config(lines, url="http://x:8080", username="u", password="p")
    assert "qui_proxy_url" not in get_child_inline_mapping(staged, "TORRENT_CLIENTS", QBIT_CLIENT_KEY)


# --- EJEC-1: contraseñas y claves con caracteres que YAML trata aparte --------

_VALORES_DIFICILES = [
    "a\\b", "\\", "\\\\", "it's \"q\"", "'", "''", "tab\taqui", "con # almohadilla",
    "con: dos puntos", "llaves } { y , comas [ ]", "ñandú", "  con espacios  ", "true", "123",
    "dos\nlineas", "retorno\r\ny tab\t", "control\x01", "\x7f", "nel\x85", "separador\u2028", "mezcla \"q\" \n \\ '",
]


@pytest.mark.parametrize("valor", _VALORES_DIFICILES, ids=repr)
def test_la_contrasena_de_qbittorrent_se_lee_igual_que_se_escribio(valor):
    """repr() de Python escapaba con la barra invertida (que YAML no entiende
    entre comillas simples): una contraseña con ella se leía mal, y con las dos
    comillas el config.yaml no se podía leer."""
    yaml = pytest.importorskip("yaml")
    staged, _ = stage_qbittorrent_config([], url="http://x:8080", username=valor, password=valor)

    cargado = yaml.safe_load("\n".join(staged))["TORRENT_CLIENTS"][QBIT_CLIENT_KEY]
    assert cargado["qbit_pass"] == valor
    assert cargado["qbit_user"] == valor
    assert cargado["enable_search"] is False
    mapa = get_child_inline_mapping(staged, "TORRENT_CLIENTS", QBIT_CLIENT_KEY)
    assert mapa["qbit_pass"] == valor and mapa["qbit_user"] == valor


@pytest.mark.parametrize("valor", _VALORES_DIFICILES, ids=repr)
def test_la_clave_de_un_host_de_imagenes_se_lee_igual_que_se_escribio(valor):
    yaml = pytest.importorskip("yaml")
    staged, _ = stage_image_hosts([], api_keys={"imgbb": valor, "ptpimg": "otra"})

    assert yaml.safe_load("\n".join(staged))["SCREENS"]["API_KEYS"] == {"imgbb": valor, "ptpimg": "otra"}


def test_las_listas_de_un_cliente_sobreviven_al_staging():
    """Uploadrr acepta una lista en automatic_management_paths: perderla (o
    convertirla en una cadena) se llevaba por delante el resto de la entrada."""
    yaml = pytest.importorskip("yaml")
    lines = [
        "TORRENT_CLIENTS:",
        "  qbit: {'torrent_client': 'qbit', 'automatic_management_paths': ['/data/tv', '/data/mo vies', 'it''s'], "
        "'content_layout': 'Original'}",
    ]
    staged, _ = stage_qbittorrent_config(lines, url="http://x:8080", username="u", password="p")

    cargado = yaml.safe_load("\n".join(staged))["TORRENT_CLIENTS"][QBIT_CLIENT_KEY]
    assert cargado["automatic_management_paths"] == ["/data/tv", "/data/mo vies", "it's"]
    assert cargado["content_layout"] == "Original"
    assert cargado["qbit_pass"] == "p"
    assert get_child_inline_mapping(staged, "TORRENT_CLIENTS", QBIT_CLIENT_KEY)["automatic_management_paths"] == [
        "/data/tv", "/data/mo vies", "it's"]


def test_las_palabras_booleanas_de_yaml_11_se_leen_como_booleanos():
    """PyYAML (el que lee Uploadrr) toma `no` como False: guardarlo como la
    cadena 'no' dejaba la verificación del certificado activa."""
    lines = ["TORRENT_CLIENTS:", "  qbit: {VERIFY_WEBUI_CERTIFICATE: no, enable_search: Yes, other: off, on: 'no'}"]
    mapa = get_child_inline_mapping(lines, "TORRENT_CLIENTS", QBIT_CLIENT_KEY)
    assert mapa == {"VERIFY_WEBUI_CERTIFICATE": False, "enable_search": True, "other": False, "on": "no"}


def test_lo_que_pyyaml_lee_de_una_entrada_existente_es_lo_que_se_reescribe():
    """Escapes de comillas dobles de YAML que no son los de Python (la barra
    escapada, el guion bajo) y enteros con ceros delante (`08` es una cadena,
    `7` un entero): el valor y su tipo tienen que ser los mismos que ve Uploadrr
    al releer el config."""
    yaml = pytest.importorskip("yaml")
    lines = [
        "TORRENT_CLIENTS:",
        '  qbit: {automatic_management_paths: ["\\/data\\/tv", "a\\_b\\Nc", \'/data/it\'\'s\'], '
        "qbit_port: 8080, ceros: [08, 7, 0, -3], decimal: 1.5, texto: 08, nulos: [none, None, null, Null, ~], otra: none}",
    ]
    original = yaml.safe_load("\n".join(lines))["TORRENT_CLIENTS"][QBIT_CLIENT_KEY]

    staged, _ = stage_qbittorrent_config(lines, url="http://x:8080", username="u", password="p")

    cargado = yaml.safe_load("\n".join(staged))["TORRENT_CLIENTS"][QBIT_CLIENT_KEY]
    for clave in ("automatic_management_paths", "ceros", "decimal", "texto", "nulos", "otra"):
        assert cargado[clave] == original[clave], clave
        assert type(cargado[clave]) is type(original[clave]), clave
    assert cargado["ceros"] == ["08", 7, 0, -3]
    assert cargado["nulos"] == ["none", "None", None, None, None]  # `none` es una cadena, no null
    assert cargado["automatic_management_paths"][0] == "/data/tv"


def test_el_config_de_ejemplo_del_fork_se_sigue_leyendo():
    lines = [
        "TORRENT_CLIENTS:",
        "  qbit: {'torrent_client': 'qbit', 'enable_search': True, 'qbit_url': 'http://127.0.0.1', "
        "'qbit_port': '8080', 'content_layout': 'Original', 'VERIFY_WEBUI_CERTIFICATE': False} # un comentario",
    ]
    assert get_child_inline_mapping(lines, "TORRENT_CLIENTS", "qbit") == {
        "torrent_client": "qbit", "enable_search": True, "qbit_url": "http://127.0.0.1",
        "qbit_port": "8080", "content_layout": "Original", "VERIFY_WEBUI_CERTIFICATE": False,
    }


# --- El config de cada subida: preparar_config_de_la_subida ------------------

def _poner_ajuste(clave, valor):
    from emuwarez_uploadrr.database import connect, init_db
    from emuwarez_uploadrr.services.settings import set_setting
    conn = connect()
    init_db(conn)
    set_setting(conn, clave, valor)
    conn.commit()
    conn.close()


def _per_episode(config):
    return get_block_key(read_config_lines(config), "SCREENS", "per_episode")


def _preparar(tmp_path, monkeypatch, texto, job_id=1):
    """Prepara el config de una subida con config.yaml = `texto`; devuelve su ruta (el config.yaml no cambia)."""
    from emuwarez_uploadrr.services.upload_execution import config as ue
    config = tmp_path / "Uploadrr" / "data" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(texto, encoding="utf-8")
    monkeypatch.setattr(ue, "append_upload_log", lambda *a, **k: None)
    ruta = tmp_path / "subida.yaml"
    ue.preparar_config_de_la_subida(job_id, str(tmp_path / "Uploadrr"), ruta)
    assert config.read_text(encoding="utf-8") == texto
    assert [p.name for p in config.parent.iterdir()] == ["config.yaml"]
    return ruta


def test_stage_keep_quiet_off_replaces_a_true_and_creates_a_missing_key():
    from emuwarez_uploadrr.services.uploadrr_config import stage_keep_quiet_off
    from emuwarez_uploadrr.services.uploadrr_yaml import get_block_key
    staged, _ = stage_keep_quiet_off(["AUTO:", "  keep_quiet: true # silencio"])
    assert get_block_key(staged, "AUTO", "keep_quiet") == "false"
    staged, _ = stage_keep_quiet_off(["DEFAULT:", "  tmdb: x"])
    assert get_block_key(staged, "AUTO", "keep_quiet") == "false"


def test_la_subida_fuerza_keep_quiet_off(tmp_path, monkeypatch):
    ruta = _preparar(tmp_path, monkeypatch, "AUTO:\n  keep_quiet: true\n")
    assert get_block_key(read_config_lines(ruta), "AUTO", "keep_quiet") == "false"


@pytest.mark.parametrize("anterior", list(FIRMAS_ANTERIORES))
def test_cada_firma_de_serie_anterior_pasa_a_la_actual_y_una_propia_no_se_toca(anterior):
    actual = FIRMAS_ANTERIORES[anterior]
    propia = '  anon_pr_signature: "[img]https://ejemplo.test/mia.png[/img]"'
    staged, _ = stage_firma_actual([
        "SIGNATURES:", f'  signature: "[img=400]{anterior}[/img]"',
        f'  pr_signature: "[b]Personal Release[/b][img=400]{anterior}[/img]"', propia])
    assert staged[1] == f'  signature: "[img=400]{actual}[/img]"'
    assert actual in staged[2] and anterior not in staged[2]
    assert staged[3] == propia


def test_las_firmas_de_serie_ya_no_estan_en_imgbb():
    # imgbb va detrás de Cloudflare: en España los bloqueos de LaLiga lo tiran en días de partido.
    for url in (FIRMA_ACTUAL, FIRMA_ANONIMA):
        assert url.startswith("https://raw.githubusercontent.com/Kisnov/eMuwarr/")
        ruta = REPO_ROOT / url.split("/", 6)[6]
        assert ruta.is_file(), ruta
    ejemplo = (REPO_ROOT / "uploadrr-emu" / "data" / "backup" / "example_config.yaml").read_text(encoding="utf-8")
    assert "i.ibb.co" not in ejemplo.split("SIGNATURES:", 1)[1].split("\n\n", 1)[0]
    assert FIRMA_ACTUAL in ejemplo and FIRMA_ANONIMA in ejemplo


def test_al_preparar_la_subida_la_firma_de_serie_queda_al_dia(tmp_path, monkeypatch):
    anterior = next(iter(FIRMAS_ANTERIORES))
    ruta = _preparar(tmp_path, monkeypatch, f'SIGNATURES:\n  signature: "[img=400]{anterior}[/img]"\n')
    texto = ruta.read_text(encoding="utf-8")
    assert FIRMA_ACTUAL in texto and anterior not in texto


def test_stage_anon_off_neutralises_the_config_so_only_the_flag_decides():
    from emuwarez_uploadrr.services.uploadrr_config import stage_anon_off
    lines = ["DEFAULT:", "  always_anon: true", "TRACKERS:", "  EMU:", "    api_key: k", "    anon: true"]
    staged, _ = stage_anon_off(lines)
    assert get_block_key(staged, "DEFAULT", "always_anon") == "false"
    assert "    anon: false" in staged and "    anon: true" not in staged
    assert "    api_key: k" in staged
    staged, _ = stage_anon_off(["DEFAULT:", "  tmdb: x"])
    assert get_block_key(staged, "DEFAULT", "always_anon") == "false"
    assert "    anon: false" in staged


def test_la_subida_fuerza_el_anonimato_apagado(tmp_path, monkeypatch):
    ruta = _preparar(tmp_path, monkeypatch,
                     "DEFAULT:\n  always_anon: true\nTRACKERS:\n  EMU:\n    anon: true\n")
    lines = read_config_lines(ruta)
    assert get_block_key(lines, "DEFAULT", "always_anon") == "false"
    assert "    anon: false" in lines and "    anon: true" not in lines


def test_qbit_configurado_es_la_misma_condicion_para_probar_y_stagear():
    from emuwarez_uploadrr.integrations.qbittorrent import qbit_configurado
    assert qbit_configurado("http://qbit:8080", None)
    assert qbit_configurado("", "http://qui/proxy/k")
    assert not qbit_configurado("  ", None)
    assert not qbit_configurado(None, "")


def test_la_subida_pone_qbit_con_solo_la_url(tmp_path, monkeypatch):
    _poner_ajuste("qbit.url", "http://qbit.local:8080")
    ruta = _preparar(tmp_path, monkeypatch, "DEFAULT:\n  default_torrent_client: watch\n")
    assert get_block_key(read_config_lines(ruta), "DEFAULT", "default_torrent_client") == QBIT_CLIENT_KEY


def test_stage_oxipng_on_activa_oxipng_con_nivel_2():
    from emuwarez_uploadrr.services.uploadrr_config import stage_oxipng_on
    from emuwarez_uploadrr.services.uploadrr_yaml import get_block_key
    staged, _ = stage_oxipng_on(["SCREENS:", "  use_oxipng: false", "  oxipng_level: 4"])
    assert get_block_key(staged, "SCREENS", "use_oxipng") == "true"
    assert get_block_key(staged, "SCREENS", "oxipng_level") == "2"
    staged, _ = stage_oxipng_on(["DEFAULT:", "  tmdb: x"])
    assert get_block_key(staged, "SCREENS", "use_oxipng") == "true"
    assert get_block_key(staged, "SCREENS", "oxipng_level") == "2"


def test_la_subida_fuerza_oxipng(tmp_path, monkeypatch):
    ruta = _preparar(tmp_path, monkeypatch, "SCREENS:\n  use_oxipng: false\n")
    lineas = read_config_lines(ruta)
    assert get_block_key(lineas, "SCREENS", "use_oxipng") == "true"
    assert get_block_key(lineas, "SCREENS", "oxipng_level") == "2"


