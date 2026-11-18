"""Los eventos JSON Lines de Uploadrr-EMU (`--eventos`): su traducción, el seguimiento del fichero y el camino real.

Las transcripciones `tests/fixtures/golden_uploadrr/*.jsonl` son las que emite Uploadrr-EMU; el resto lanza el
subproceso de verdad (`upload_execution`) contra tests/fixtures/fake_uploadrr_emu.py en modo guion.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from emuwarez_uploadrr.database import connect, db_path, init_db
from emuwarez_uploadrr.services import subida_pasos as sp
from emuwarez_uploadrr.services.upload_execution import (
    CONFIRMATION_TOKEN,
    build_uploadrr_execution_command,
    get_upload_job,
    get_upload_job_logs,
    start_upload_job,
)
from emuwarez_uploadrr.services.upload_execution.comun import QBIT_LOGIN_INCORRECTO
from emuwarez_uploadrr.services.uploadrr_contract import build_uploadrr_emu_command
from emuwarez_uploadrr.services.uploadrr_eventos import (
    EstadoEventos,
    SeguidorEventos,
    admite_eventos,
    clasificar_con_eventos,
    ficha_de_estado,
    ruta_eventos,
)
from emuwarez_uploadrr.services.uploadrr_progress import RUNNING_DISPLAY_ORDER, PHASE_KEY_ALIASES

FIXTURES = Path(__file__).parent / "fixtures"
CONTRATO = FIXTURES / "contrato_eventos.json"
GOLDEN = sorted((FIXTURES / "golden_uploadrr").glob("*.jsonl"))
GOLDEN_BUENAS = [g for g in GOLDEN if "duplicado" not in g.name]
GOLDEN_DUPLICADO = [g for g in GOLDEN if "duplicado" in g.name]


def _eventos(ruta: Path) -> list[dict]:
    return [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines() if linea.strip()]


def _reproducir(ruta: Path) -> tuple[EstadoEventos, list[tuple]]:
    estado, efectos = EstadoEventos(), []
    for ev in _eventos(ruta):
        efectos += estado.aplicar(ev)
    return estado, efectos


# ---------------------------------------------------------------------------
# Las transcripciones doradas, por la traducción de eventos
# ---------------------------------------------------------------------------

def test_hay_transcripciones_doradas_de_eventos():
    assert len(GOLDEN_BUENAS) >= 2 and len(GOLDEN_DUPLICADO) == 1


@pytest.mark.parametrize("ruta", GOLDEN_BUENAS, ids=lambda r: r.name)
def test_las_fases_de_los_eventos_avanzan_en_orden_hasta_qbittorrent(ruta):
    _estado, efectos = _reproducir(ruta)
    fases = [(e[1], e[2]) for e in efectos if e[0] == "fase"]
    mostradas = [PHASE_KEY_ALIASES.get(clave, clave) for clave, _pct in fases]
    posiciones = [RUNNING_DISPLAY_ORDER.index(f) for f in mostradas]
    assert posiciones == sorted(posiciones), f"una fase retrocede: {[c for c, _p in fases]}"
    for esperada in ("torrent_create", "uploading_emu", "qbittorrent_add"):
        assert esperada in {c for c, _p in fases}
    assert fases[-1] == ("qbittorrent_add", 100)


@pytest.mark.parametrize("ruta", GOLDEN_BUENAS, ids=lambda r: r.name)
def test_los_pasos_la_ficha_y_qbittorrent_salen_de_los_eventos(ruta):
    estado, efectos = _reproducir(ruta)
    assert [e[1] for e in efectos if e[0] == "paso"] == ["screenshots", "torrent", "tracker"]
    assert ("qbit_ok",) in efectos and not [e for e in efectos if e[0] == "qbit_fallo"]
    assert (estado.url, estado.torrent_id) == ("https://emu-warez.com/torrents/42", "42")

    veredicto = clasificar_con_eventos(estado, 0)
    assert (veredicto["status"], veredicto["result_url"], veredicto["torrent_id"], veredicto["error_summary"]) == (
        "completed", "https://emu-warez.com/torrents/42", "42", None)


@pytest.mark.parametrize("ruta", GOLDEN_DUPLICADO, ids=lambda r: r.name)
def test_un_duplicado_por_eventos_falla_como_duplicado_sin_id(ruta):
    estado, efectos = _reproducir(ruta)
    claves = {e[1] for e in efectos if e[0] == "fase"}
    assert "duplicates" in claves and not claves & {"torrent_create", "uploading_emu", "qbittorrent_add"}
    assert not [e for e in efectos if e[0] == "paso"]

    veredicto = clasificar_con_eventos(estado, 1)
    assert veredicto["status"] == "failed"
    assert veredicto["result_url"] is None and veredicto["torrent_id"] is None
    assert "duplic" in veredicto["error_summary"].lower() and "Potential duplicate on" in veredicto["error_summary"]
    assert ficha_de_estado(estado) == {}


# ---------------------------------------------------------------------------
# El veredicto cuando resultado y código de salida no se ponen de acuerdo
# ---------------------------------------------------------------------------

def _estado(*eventos: dict) -> EstadoEventos:
    estado = EstadoEventos()
    for ev in eventos:
        estado.aplicar({"v": 1, **ev})
    return estado


INICIO = {"ev": "inicio", "uuid": "X.mkv", "modo": "subida", "reanuda": False}
SUBIDO = {"ev": "subido", "torrent_id": 4242, "url": "https://emu-warez.com/torrents/4242"}
OK = {"ev": "resultado", "ok": True, "codigo": "subido"}
MAL = {"ev": "resultado", "ok": False, "codigo": "rechazado", "detalle": "EMU Rejected Upload"}


@pytest.mark.parametrize("eventos, codigo, estado, error", [
    ([INICIO, SUBIDO, OK], 0, "completed", None),
    ([INICIO, OK], 0, "completed", None),  # el tracker no dio enlace: ok sin `subido`
    ([INICIO, MAL], 1, "failed", "eMuwarez rechazó la subida"),
    ([INICIO, MAL], 0, "failed", "eMuwarez rechazó la subida"),   # exit 0 pero resultado ok:false
    ([INICIO, OK], 1, "failed", "dijo que la subida fue bien, pero salió con exit code 1"),  # exit != 0 con ok:true
    ([INICIO], 0, "failed", "sin dar un resultado"),
    ([INICIO], -9, "failed", "murió a mitad de la subida (exit code -9)"),
])
def test_el_resultado_y_el_codigo_de_salida_tienen_que_coincidir(eventos, codigo, estado, error):
    veredicto = clasificar_con_eventos(_estado(*eventos), codigo)
    assert veredicto["status"] == estado
    assert (error in veredicto["error_summary"]) if error else veredicto["error_summary"] is None


def test_si_se_vio_subido_el_fallo_conserva_la_ficha():
    for eventos, codigo in (([INICIO, SUBIDO], -9), ([INICIO, SUBIDO, MAL], 1), ([INICIO, SUBIDO, OK], 1)):
        estado = _estado(*eventos)
        veredicto = clasificar_con_eventos(estado, codigo)
        assert veredicto["status"] == "failed" and veredicto["torrent_id"] == "4242"
        assert "sí llegó a subirse" in veredicto["error_summary"]
        assert ficha_de_estado(estado) == {
            "result_url": "https://emu-warez.com/torrents/4242", "result_torrent_id": "4242", "torrent_subido": 1}


@pytest.mark.parametrize("codigo, marca", [
    ("duplicado", "Potential duplicate on"), ("busqueda_caida", "Dupe search failed"), ("tmdb", "TMDb ID not found"),
    ("grupo_prohibido", "Banned group on"), ("contenido_prohibido", "Banned content on"),
    ("tipo_desconocido", "Unknown type on"),
])
def test_los_fallos_por_elemento_siguen_siendo_fallos_por_elemento_para_los_lotes(codigo, marca):
    """lotes.motivo_de_fallo decide por estas marcas si un fallo frena el lote entero: no pueden perderse."""
    from emuwarez_uploadrr.services.lotes import motivo_de_fallo
    veredicto = clasificar_con_eventos(_estado(INICIO, {"ev": "resultado", "ok": False, "codigo": codigo}), 1)
    assert marca in veredicto["error_summary"]
    if codigo != "busqueda_caida":
        assert motivo_de_fallo(veredicto["error_summary"]) is None


def test_cada_codigo_de_resultado_del_contrato_tiene_mensaje():
    from emuwarez_uploadrr.services.uploadrr_eventos import MENSAJES_RESULTADO
    codigos = json.loads(CONTRATO.read_text(encoding="utf-8"))["valores"]["codigo_resultado"]
    assert sorted(set(codigos) - set(MENSAJES_RESULTADO) - {"subido", "cliente_ok"}) == []


def test_cada_motivo_de_cliente_del_contrato_tiene_mensaje():
    from emuwarez_uploadrr.services.uploadrr_eventos import MOTIVOS_CLIENTE
    assert sorted(set(json.loads(CONTRATO.read_text(encoding="utf-8"))["valores"]["motivo"]) - set(MOTIVOS_CLIENTE)) == []


def test_aviso_pack_incompleto_dice_lo_mismo_que_el_texto():
    from emuwarez_uploadrr.services.upload_result_parser import season_pack_warning
    estado = _estado({"ev": "aviso", "codigo": "pack_incompleto", "datos": {"temporada": 2, "faltan": [3, 5]}})
    assert estado.aviso_pack == season_pack_warning("Incomplete season pack: season 2, missing episodes 3, 5")
    sin_temporada = _estado({"ev": "aviso", "codigo": "pack_incompleto", "datos": {"temporada": None, "faltan": []}})
    assert sin_temporada.aviso_pack == season_pack_warning("Incomplete season pack: season ?")


def test_cliente_sin_cliente_configurado_no_es_un_fallo_de_qbittorrent():
    estado = EstadoEventos()
    assert estado.aplicar({"ev": "cliente", "ok": False, "cliente": "ninguno", "motivo": "sin_cliente"}) == []
    assert estado.aplicar({"ev": "cliente", "ok": False, "cliente": "qbit", "motivo": "login"}) == [("qbit_fallo", "login")]


# ---------------------------------------------------------------------------
# Seguir el fichero
# ---------------------------------------------------------------------------

def test_el_seguidor_espera_a_la_linea_completa_y_tolera_basura(tmp_path):
    ruta, vistos = tmp_path / "e.jsonl", []
    seguidor = SeguidorEventos(ruta, vistos.append)
    seguidor.leer()  # el fichero aún no existe
    ruta.write_bytes(b'{"v": 1, "ev": "inicio"}\nesto no es json\n[1, 2]\n{"v": 1, "ev": "fase", "cl')
    seguidor.leer()
    assert [e["ev"] for e in vistos] == ["inicio"]
    with open(ruta, "ab") as f:
        f.write('ave": "analisis", "estado": "empieza"}\n{"v": 2, "ev": "futuro"}\n'.encode())
        f.write(b'{"v": 1, "ev": "resultado", "ok": true, "codigo": "subido"}')  # la última, sin salto de línea
    seguidor.leer()
    assert [e["ev"] for e in vistos] == ["inicio", "fase"]
    seguidor.terminar()
    assert [e["ev"] for e in vistos] == ["inicio", "fase", "resultado"]


def test_el_seguidor_no_deja_pasar_la_excepcion_del_que_escucha(tmp_path):
    ruta = tmp_path / "e.jsonl"
    ruta.write_text('{"v": 1, "ev": "a"}\n{"v": 1, "ev": "b"}\n', encoding="utf-8")
    vistos = []

    def escucha(ev):
        vistos.append(ev["ev"])
        raise RuntimeError("falla")

    SeguidorEventos(ruta, escucha).leer()
    assert vistos == ["a", "b"]


def test_un_evento_desconocido_se_ignora_y_se_avisa_una_sola_vez(caplog):
    estado = EstadoEventos()
    with caplog.at_level("WARNING", logger="emuwarez.upload"):
        for _ in range(3):
            assert estado.aplicar({"ev": "inventado"}) == []
            assert estado.aplicar({"ev": "fase", "clave": "nueva", "estado": "empieza"}) == []
            assert estado.aplicar({"ev": "paso_hecho", "paso": "otro"}) == []
    assert len([r for r in caplog.records if "desconocid" in r.getMessage()]) == 3


# ---------------------------------------------------------------------------
# El argv
# ---------------------------------------------------------------------------

_BASE = dict(python_path="/usr/bin/python3", uploadrr_script_path="/opt/Uploadrr/upload.py",
             prepared_path="/data/subidas/Peli.mkv", torrent_name="Peli.2024.1080p.WEB-DL", tmdb_id="603",
             category="movie", resolution="1080p", release_format="WEB-DL")


def test_el_flag_va_despues_de_la_ruta_y_absoluto(tmp_path):
    sin = build_uploadrr_emu_command(**_BASE)
    con = build_uploadrr_emu_command(**_BASE, eventos=str(tmp_path / "ev" / "1.jsonl"))
    assert con == sin + ["--eventos", str(tmp_path / "ev" / "1.jsonl")]
    assert con.index("--eventos") > con.index("/data/subidas/Peli.mkv")
    assert Path(con[-1]).is_absolute()
    assert build_uploadrr_emu_command(**_BASE, eventos="relativa.jsonl")[-1] == os.path.abspath("relativa.jsonl")


@pytest.mark.parametrize("contenido, admite", [
    (None, False), ('{"version": 1}', True), ('{"version": 2}', False), ("no es json", False), ("[1]", False)])
def test_solo_se_pasa_el_flag_si_el_checkout_trae_un_contrato_que_la_app_conoce(tmp_path, contenido, admite):
    if contenido is not None:
        (tmp_path / "contrato_eventos.json").write_text(contenido, encoding="utf-8")
    assert admite_eventos(tmp_path) is admite


@pytest.fixture
def db_conn(isolated_db):
    conn = connect(db_path())
    init_db(conn)
    yield conn
    conn.close()


def _poner_ajustes(db_conn, carpeta, python_path):
    for k, v in {"uploadrr.path": str(carpeta), "uploadrr.python_path": python_path,
                 "tmdb.api_key": "tmdb_secret_abc123", "emu.api_key": "emu_secret_xyz789",
                 "uploadrr.timeout": "30"}.items():
        db_conn.execute("INSERT OR REPLACE INTO settings (key, value, is_secret) VALUES (?, ?, 0)", (k, v))


def _crear_job(db_conn, tmp_path):
    prepared = tmp_path / "prepared.mkv"
    prepared.write_bytes(b"\x00" * 16)
    cur = db_conn.execute(
        "INSERT INTO upload_jobs (title, path, status, stage, source_path, prepared_path, uploadrr_command_json, "
        "created_at, updated_at) VALUES ('T', ?, 'ready', 'preflight', ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (str(prepared), str(prepared), str(prepared),
         json.dumps({"category": "movie", "tmdb_id": "1", "resolution": "1080p", "title": "T"})))
    db_conn.commit()
    return cur.lastrowid


def test_el_comando_del_trabajo_lleva_eventos_solo_con_contrato(db_conn, tmp_path, fake_uploadrr_emu_dir,
                                                                  fake_uploadrr_python_path):
    _poner_ajustes(db_conn, fake_uploadrr_emu_dir, fake_uploadrr_python_path)
    jid = _crear_job(db_conn, tmp_path)
    job = get_upload_job(jid, conn=db_conn)

    sin = build_uploadrr_execution_command(job, conn=db_conn)
    assert "--eventos" not in sin["args"] and sin["eventos"] is None

    shutil.copy(CONTRATO, fake_uploadrr_emu_dir / "contrato_eventos.json")
    con = build_uploadrr_execution_command(job, conn=db_conn)
    assert con["args"] == sin["args"] + ["--eventos", str(ruta_eventos(jid))] and con["eventos"] == str(ruta_eventos(jid))
    # El reenvío a qBittorrent usa su propio fichero: puede repetirse sobre el mismo trabajo.
    reenvio = build_uploadrr_execution_command(job, conn=db_conn, solo_cliente="4242")
    assert reenvio["args"][-2:] == ["--eventos", str(ruta_eventos(jid, reenvio=True))]
    assert ruta_eventos(jid) != ruta_eventos(jid, reenvio=True)


# ---------------------------------------------------------------------------
# Con el subproceso de verdad (el fake en modo guion)
# ---------------------------------------------------------------------------

def _lanzar(db_conn, tmp_path, carpeta, python_path, *, contrato=True):
    if contrato:
        shutil.copy(CONTRATO, carpeta / "contrato_eventos.json")
    _poner_ajustes(db_conn, carpeta, python_path)
    jid = _crear_job(db_conn, tmp_path)
    assert start_upload_job(jid, dry_run=False, start=True, confirmation=CONFIRMATION_TOKEN,
                            conn=db_conn)["action"] == "started"
    return jid


def _esperar(condicion, plazo=20.0):
    limite = time.monotonic() + plazo
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.05)
    return False


def _terminal(db_conn, jid):
    """Espera a que la subida termine del todo: estado final, su línea de cierre en el registro y el fichero de eventos borrado."""
    def hecho():
        if get_upload_job(jid, conn=db_conn)["status"] not in ("completed", "failed", "cancelled"):
            return False
        mensajes = [m["message"] for m in get_upload_job_logs(jid, limit=2000, conn=db_conn)["logs"]]
        return any(m.startswith("Proceso ") or "timeout" in m for m in mensajes) and not ruta_eventos(jid).exists()
    assert _esperar(hecho), "la subida no terminó"
    return get_upload_job(jid, conn=db_conn)


def _mensajes(db_conn, jid):
    return [m["message"] for m in get_upload_job_logs(jid, limit=2000, conn=db_conn)["logs"]]


def _fase(job, clave):
    fases = json.loads(job["progress_json"] or "{}").get("phases", [])
    return next((f["status"] for f in fases if f["key"] == clave), None)


def ev(nombre, **campos):
    return {"evento": {"ev": nombre, **campos}}


def fase(clave, estado, **campos):
    return ev("fase", clave=clave, estado=estado, **campos)


INICIO_G = ev("inicio", uuid="prepared.mkv", modo="subida", reanuda=False)
HASTA_EL_POST = [
    INICIO_G, fase("analisis", "empieza"), fase("analisis", "hecha"), fase("duplicados", "empieza"),
    fase("duplicados", "hecha"), fase("capturas", "empieza"), ev("paso_hecho", paso="screenshots"),
    fase("capturas", "hecha"), fase("torrent", "empieza"), ev("paso_hecho", paso="torrent"), fase("torrent", "hecha"),
    fase("subida", "empieza"),
]
SUBIDO_G = ev("subido", torrent_id=4242, url="https://fake-emu.invalid.test/torrents/4242")
CLIENTE_OK = [fase("cliente", "empieza"), fase("cliente", "hecha"),
              ev("cliente", ok=True, cliente="qbit", ruta="/downloads/x")]
RESULTADO_OK = ev("resultado", ok=True, codigo="subido")
TEXTO_BUENO = [{"out": "Torrent URL: https://fake-emu.invalid.test/torrents/4242"},
               {"out": "Torrent uploaded successfully!"}, {"out": "Added to: /downloads/x"}]


def test_la_subida_normal_se_decide_por_eventos_y_borra_su_fichero(
        db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path):
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path)
    job = _terminal(db_conn, jid)

    assert (job["status"], job["exit_code"]) == ("completed", 0)
    assert (job["result_url"], job["result_torrent_id"]) == ("https://fake-emu.invalid.test/torrents/999999", "999999")
    assert job["torrent_subido"] == 1
    assert sp.leer_subpasos(job["pasos_uploadrr_json"]) == ["screenshots", "torrent", "tracker"]
    assert _fase(job, "uploading_emu") == "done" and _fase(job, "qbittorrent_add") == "done"
    assert not ruta_eventos(jid).exists()


def test_los_eventos_mandan_cuando_el_texto_dice_otra_cosa(
        db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    """El texto cuenta una subida buena (URL, «Added to:»), los eventos un duplicado: es un duplicado."""
    guion_uploadrr([INICIO_G, *TEXTO_BUENO, {"out": "Step done: screenshots"},
                    fase("duplicados", "fallida"),
                    ev("resultado", ok=False, codigo="duplicado", detalle="Potential duplicate on EMU"), {"exit": 1}])
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path)
    job = _terminal(db_conn, jid)

    assert (job["status"], job["exit_code"]) == ("failed", 1)
    assert "Potential duplicate on EMU" in job["error_message"] and "Exit code: 1" in job["error_message"]
    # (`torrent_subido` no se mira: el registro lo marca al ver la línea `Torrent URL:`, también con eventos.)
    assert not job["result_url"] and not job["result_torrent_id"]
    assert sp.leer_subpasos(job["pasos_uploadrr_json"]) == []
    # El texto sí queda en el registro.
    assert "Torrent URL: https://fake-emu.invalid.test/torrents/4242" in _mensajes(db_conn, jid)


def test_el_id_y_la_ficha_son_los_de_subido_no_los_del_texto(
        db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    guion_uploadrr([*HASTA_EL_POST, SUBIDO_G, {"out": "Torrent URL: https://fake-emu.invalid.test/torrents/1"},
                    *CLIENTE_OK, RESULTADO_OK])
    job = _terminal(db_conn, _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path))

    assert job["status"] == "completed" and job["result_torrent_id"] == "4242"
    assert job["result_url"] == "https://fake-emu.invalid.test/torrents/4242"


def test_sin_eventos_decide_el_texto_como_siempre(
        db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    """Un Uploadrr-EMU que trae el contrato pero no escribe eventos (fichero sin `inicio`): camino de texto."""
    guion_uploadrr([{"out": "Step done: screenshots"}, {"out": "Step done: torrent"}, *TEXTO_BUENO])
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path)
    job = _terminal(db_conn, jid)

    assert job["status"] == "completed" and job["result_torrent_id"] == "4242"
    assert sp.leer_subpasos(job["pasos_uploadrr_json"]) == ["screenshots", "torrent", "tracker"]
    assert _fase(job, "qbittorrent_add") == "done"


def test_sin_contrato_no_se_pasa_el_flag_y_se_lee_el_texto(
        db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    # Aunque el fake emitiría eventos si se los pidieran, sin el contrato la app no se los pide.
    guion_uploadrr([{"out": "Step done: screenshots"}, *TEXTO_BUENO])
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, contrato=False)
    job = _terminal(db_conn, jid)

    assert job["status"] == "completed" and "--eventos" not in job["safe_command_json"]
    assert not ruta_eventos(jid).exists()


def test_muere_tras_subido_sin_resultado_conserva_el_torrent(
        db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    guion_uploadrr([*HASTA_EL_POST, SUBIDO_G, {"kill": 9}])
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path)
    job = _terminal(db_conn, jid)

    assert (job["status"], job["exit_code"]) == ("failed", -9)
    assert (job["result_url"], job["result_torrent_id"], job["torrent_subido"]) == (
        "https://fake-emu.invalid.test/torrents/4242", "4242", 1)
    assert "murió a mitad de la subida" in job["error_message"] and "sí llegó a subirse" in job["error_message"]
    assert sp.leer_subpasos(job["pasos_uploadrr_json"]) == ["screenshots", "torrent", "tracker"]


def test_exit_0_sin_resultado_es_fallido(
        db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    guion_uploadrr([*HASTA_EL_POST, *TEXTO_BUENO, {"exit": 0}])
    job = _terminal(db_conn, _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path))

    assert (job["status"], job["exit_code"]) == ("failed", 0)
    assert "sin dar un resultado" in job["error_message"]
    assert job["result_url"] and job["torrent_subido"] == 1  # la ficha impresa se conserva aunque falten eventos


def test_resultado_ok_con_exit_distinto_de_0_es_fallido(
        db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    guion_uploadrr([*HASTA_EL_POST, SUBIDO_G, *CLIENTE_OK, RESULTADO_OK, {"exit": 1}])
    job = _terminal(db_conn, _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path))

    assert (job["status"], job["exit_code"]) == ("failed", 1)
    assert "salió con exit code 1" in job["error_message"] and job["torrent_subido"] == 1


def test_resultado_fallido_con_exit_0_es_fallido(
        db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    guion_uploadrr([INICIO_G, ev("resultado", ok=False, codigo="tmdb")])
    job = _terminal(db_conn, _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path))

    assert (job["status"], job["exit_code"]) == ("failed", 0)
    assert "TMDb" in job["error_message"]


def test_el_login_de_qbittorrent_fallido_deja_la_subida_hecha_y_la_fase_fallida(
        db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    guion_uploadrr([*HASTA_EL_POST, SUBIDO_G, fase("cliente", "empieza"), fase("cliente", "fallida"),
                    ev("cliente", ok=False, cliente="qbit", motivo="login"), RESULTADO_OK])
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path)
    job = _terminal(db_conn, jid)

    assert job["status"] == "completed" and job["result_torrent_id"] == "4242"
    assert _fase(job, "uploading_emu") == "done" and _fase(job, "qbittorrent_add") == "failed"
    assert QBIT_LOGIN_INCORRECTO in _mensajes(db_conn, jid)


def test_linea_rota_basura_y_evento_desconocido_no_estorban(
        db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    guion_uploadrr([
        {"evento_raw": "esto no es json\n"}, INICIO_G, ev("evento_de_un_futuro", cosa=1),
        fase("fase_nueva", "empieza"), {"evento_raw": '{"v": 1, "ev": "fase", "cl'},  # una línea a medias...
        {"sleep": 0.3}, {"evento_raw": 'ave": "analisis", "estado": "empieza"}\n'},  # ...que se completa luego
        *HASTA_EL_POST[2:], SUBIDO_G, *CLIENTE_OK, RESULTADO_OK,
        {"evento_raw": '{"v": 1, "ev": "resultado", "ok": false, "codigo": "rech'},  # y una cortada al final
    ])
    job = _terminal(db_conn, _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path))

    assert (job["status"], job["exit_code"]) == ("completed", 0)
    assert job["result_torrent_id"] == "4242"
    assert sp.leer_subpasos(job["pasos_uploadrr_json"]) == ["screenshots", "torrent", "tracker"]


def test_el_fichero_de_un_intento_anterior_no_se_lee(
        db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    """Un fichero con un `resultado` viejo en la ruta del trabajo se vacía antes de lanzar."""
    guion_uploadrr([INICIO_G, ev("resultado", ok=False, codigo="tmdb")])
    _poner_ajustes(db_conn, fake_uploadrr_emu_dir, fake_uploadrr_python_path)
    shutil.copy(CONTRATO, fake_uploadrr_emu_dir / "contrato_eventos.json")
    jid = _crear_job(db_conn, tmp_path)
    ruta = ruta_eventos(jid)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps({"v": 1, "ev": "resultado", "ok": True, "codigo": "subido"}) + "\n", encoding="utf-8")
    start_upload_job(jid, dry_run=False, start=True, confirmation=CONFIRMATION_TOKEN, conn=db_conn)

    job = _terminal(db_conn, jid)
    assert job["status"] == "failed" and "TMDb" in job["error_message"]


def test_el_seguidor_corre_a_la_vez_que_el_proceso(
        db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path, guion_uploadrr):
    """Las fases se escriben mientras el proceso sigue vivo, no al terminar."""
    senal = tmp_path / "sigue"
    guion_uploadrr([INICIO_G, fase("analisis", "empieza"), fase("analisis", "hecha"), fase("duplicados", "hecha"),
                    {"touch": str(tmp_path / "llegado")}, {"wait_for": str(senal)}, RESULTADO_OK])
    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path)

    def en_vivo():
        job = get_upload_job(jid, conn=db_conn)
        return job["status"] == "running" and _fase(job, "verificacion_presubida") == "done"
    try:
        assert _esperar(en_vivo, 15), "la fase no se vio con el proceso vivo"
    finally:
        senal.write_text("")
    assert _terminal(db_conn, jid)["status"] == "completed"
