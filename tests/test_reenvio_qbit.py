"""«Reenviar a qBittorrent» (services/upload_execution/reenvio_qbit.py, esquema 55).

Una subida completada cuyo torrent no llegó a qBittorrent (o se borró después) se vuelve a añadir con Uploadrr-EMU
`--resume --solo-cliente <id>`, sin crear otra fila y sin tocar su estado. El subproceso va simulado.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from emuwarez_uploadrr.database import _migrate_upload_jobs_reenvio_qbit, connect, db_path, init_db
from emuwarez_uploadrr.services import eventos, seeding_qbit
from emuwarez_uploadrr.services.upload_execution import (
    DELETE_CONFIRMATION_TOKEN,
    QBIT_AÑADIDO,
    _PROCESOS,
    build_uploadrr_execution_command,
    delete_upload_job,
    get_upload_job,
    get_upload_job_logs,
    mark_phase_done,
    mark_phase_failed,
    puede_reenviar_qbit,
    reenviar_a_qbittorrent,
)
from emuwarez_uploadrr.services.upload_execution import reenvio_qbit
from emuwarez_uploadrr.services.upload_execution.comun import QBIT_LOGIN_INCORRECTO
from emuwarez_uploadrr.services.upload_queue import reconcile_interrupted_jobs
from emuwarez_uploadrr.services.uploadrr_contract import build_uploadrr_emu_command

POPEN = "emuwarez_uploadrr.services.upload_execution.ejecucion.subprocess.Popen"
CLAVE_EMU = "clave_emu_secreta_123"


@pytest.fixture
def db_conn(isolated_db):
    conn = connect(db_path())
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def uploadrr(db_conn, tmp_path):
    carpeta = tmp_path / "Uploadrr"
    carpeta.mkdir()
    (carpeta / "upload.py").write_text("# stub")
    # Anuncia --config (y --eventos): sin él la app no lo lanza.
    shutil.copy(Path(__file__).parent / "fixtures" / "contrato_eventos.json", carpeta / "contrato_eventos.json")
    for clave, valor in (("uploadrr_path", str(carpeta)), ("emu_api_key", CLAVE_EMU), ("tmdb_api_key", "tmdb_x")):
        db_conn.execute("INSERT OR REPLACE INTO settings (key, value, is_secret) VALUES (?, ?, 0)", (clave, valor))
    db_conn.commit()
    return carpeta


@pytest.fixture
def avisos(monkeypatch):
    publicados: list[tuple[str, dict]] = []
    monkeypatch.setattr(reenvio_qbit, "publicar", lambda tipo, datos=None: publicados.append((tipo, datos or {})) or 1)
    return publicados


def _job(conn, tmp_path, *, status="completed", torrent_id="4242", con_ficheros=True, estado=None, reanuda=0):
    preparado = tmp_path / "subidas" / "Peli.2024.1080p.WEB-DL-EMUWAREZ.mkv"
    if con_ficheros:
        preparado.parent.mkdir(parents=True, exist_ok=True)
        preparado.write_bytes(b"x")
    cmd = {"category": "movie", "tmdb_id": "603", "resolution": "1080p", "title": "Peli.2024.1080p.WEB-DL",
           "release_format": "WEB-DL"}
    cur = conn.execute(
        "INSERT INTO upload_jobs (title, path, status, stage, prepared_path, uploadrr_command_json, result_url,"
        " result_torrent_id, finished_at, duration_seconds, reanuda_uploadrr, reenvio_qbit_estado, progress_json,"
        " created_at, updated_at) VALUES ('Peli', '', ?, 'finished', ?, ?, ?, ?, '2026-11-01T10:00:00+00:00', 321, ?,"
        " ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (status, str(preparado), json.dumps(cmd), f"https://emu-warez.com/torrents/{torrent_id}" if torrent_id else None,
         torrent_id, reanuda, estado, json.dumps({"phases": [], "global_pct": 100})),
    )
    conn.commit()
    return cur.lastrowid


def _proc(returncode=0, stdout=()):
    proc = MagicMock()
    proc.pid = 4321
    proc.stdout = iter(stdout)
    proc.stderr = iter([])
    proc.wait.return_value = returncode
    proc.poll.return_value = returncode
    return proc


def _fila(conn, jid):
    return dict(conn.execute("SELECT * FROM upload_jobs WHERE id = ?", (jid,)).fetchone())


def _mensajes(jid):
    return [log["message"] for log in get_upload_job_logs(jid, limit=1000)["logs"]]


def _qbit_fallido(jid):
    """Las fases de una subida que llegó a eMuwarez pero no a qBittorrent."""
    mark_phase_done(jid, "uploading_emu")
    mark_phase_failed(jid, "qbittorrent_add")
    assert _fase(jid, "qbittorrent_add") == "failed"


def _fase(jid, clave):
    fases = json.loads(get_upload_job(jid)["progress_json"] or "{}").get("phases", [])
    return next((f["status"] for f in fases if f["key"] == clave), None)


# ---------------------------------------------------------------------------
# El argv: el de una subida normal más --resume --solo-cliente <id>
# ---------------------------------------------------------------------------

_BASE = dict(python_path="/usr/bin/python3", uploadrr_script_path="/opt/Uploadrr/upload.py",
             prepared_path="/data/subidas/Peli.mkv", torrent_name="Peli.2024.1080p.WEB-DL", tmdb_id="603",
             category="movie", resolution="1080p", release_format="WEB-DL")


def test_el_argv_es_el_de_la_subida_con_resume_y_solo_cliente():
    normal = build_uploadrr_emu_command(**_BASE)
    assert build_uploadrr_emu_command(**_BASE, solo_cliente=42) == normal + ["--resume", "--solo-cliente", "42"]
    # Si la subida ya reanudaba, --resume no se repite.
    reanudada = build_uploadrr_emu_command(**_BASE, reanudar=True)
    assert build_uploadrr_emu_command(**_BASE, reanudar=True, solo_cliente="42") == reanudada + ["--solo-cliente", "42"]


def test_el_comando_del_trabajo_solo_añade_solo_cliente(db_conn, tmp_path, uploadrr):
    job = get_upload_job(_job(db_conn, tmp_path))
    normal = build_uploadrr_execution_command(job, conn=db_conn)
    reenvio = build_uploadrr_execution_command(job, conn=db_conn, solo_cliente="4242")

    def sin_ficheros(args):  # --eventos y --config llevan los ficheros de cada ejecución
        return [a for i, a in enumerate(args) if i == 0 or args[i - 1] not in ("--eventos", "--config")]

    assert sin_ficheros(reenvio["args"]) == sin_ficheros(normal["args"])[:-2] + [
        "--resume", "--solo-cliente", "4242", "--eventos", "--config"]
    assert reenvio["executable"] == normal["executable"] and reenvio["cwd"] == normal["cwd"]


# ---------------------------------------------------------------------------
# Qué se puede reenviar
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cambios, error", [
    (dict(status="failed"), "invalid_status"),
    (dict(torrent_id=None), "sin_torrent_id"),
    (dict(con_ficheros=False), "sin_ficheros"),
    (dict(estado="en_curso"), "en_curso"),
])
def test_no_se_reenvia_sin_lo_necesario(db_conn, tmp_path, uploadrr, cambios, error):
    jid = _job(db_conn, tmp_path, **cambios)
    antes = _fila(db_conn, jid)
    with patch(POPEN) as popen:
        resultado = reenviar_a_qbittorrent(jid, wait=True)
    assert (resultado["ok"], resultado["error"]) == (False, error)
    assert not popen.called
    assert _fila(db_conn, jid) == antes
    assert puede_reenviar_qbit(get_upload_job(jid)) is False


def test_una_subida_que_no_existe(db_conn):
    assert reenviar_a_qbittorrent(999)["error"] == "not_found"


def test_se_puede_reenviar_una_completada_con_id_y_ficheros(db_conn, tmp_path, uploadrr):
    assert puede_reenviar_qbit(get_upload_job(_job(db_conn, tmp_path))) is True


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_se_puede_reenviar_la_que_fallo_con_el_torrent_ya_en_emuwarez(db_conn, tmp_path, uploadrr, status):
    """Un reinicio o un corte tras «Torrent URL:» la dejaba fallida sin salida: reintentarla choca con su propio torrent."""
    jid = _job(db_conn, tmp_path, status=status)
    db_conn.execute("UPDATE upload_jobs SET torrent_subido = 1 WHERE id = ?", (jid,))
    db_conn.commit()
    assert puede_reenviar_qbit(get_upload_job(jid)) is True
    with patch(POPEN, return_value=_proc(0)):
        assert reenviar_a_qbittorrent(jid, wait=True)["ok"] is True
    despues = _fila(db_conn, jid)
    assert (despues["status"], despues["reenvio_qbit_estado"]) == (status, "ok")


def test_al_arrancar_la_interrumpida_con_el_torrent_subido_no_dice_que_se_reintente(db_conn, tmp_path, uploadrr):
    jid = _job(db_conn, tmp_path, status="running")
    db_conn.execute("UPDATE upload_jobs SET torrent_subido = 1 WHERE id = ?", (jid,))
    db_conn.commit()
    reconcile_interrupted_jobs()
    fila = _fila(db_conn, jid)
    assert fila["status"] == "failed" and "Reenviar a qBittorrent" in fila["error_message"]
    assert puede_reenviar_qbit(get_upload_job(jid)) is True


# ---------------------------------------------------------------------------
# Bien y mal, con el subproceso simulado
# ---------------------------------------------------------------------------

def test_reenvio_bien(db_conn, tmp_path, uploadrr, avisos, monkeypatch):
    jid = _job(db_conn, tmp_path)
    _qbit_fallido(jid)
    antes = _fila(db_conn, jid)
    monkeypatch.setattr(seeding_qbit, "_cache", ("huella", 0.0, [], ""))
    proc = _proc(0, ["Adding and rechecking torrent\n", f"Added to: qbit (clave {CLAVE_EMU})\n"])
    with patch(POPEN, return_value=proc) as popen:
        assert reenviar_a_qbittorrent(jid, wait=True)["ok"] is True

    argv = popen.call_args.args[0]
    assert argv[argv.index("--solo-cliente") - 1:argv.index("--solo-cliente") + 2] == ["--resume", "--solo-cliente", "4242"]
    despues = _fila(db_conn, jid)
    assert despues["reenvio_qbit_estado"] == "ok" and despues["reenvio_qbit_at"]
    for campo in ("status", "stage", "finished_at", "duration_seconds", "result_url", "result_torrent_id",
                  "trailer_estado", "safe_command_json", "exit_code"):
        assert despues[campo] == antes[campo], campo
    assert _fase(jid, "qbittorrent_add") == "done"
    assert seeding_qbit._cache is None
    assert jid not in _PROCESOS
    mensajes = _mensajes(jid)
    assert mensajes[0].startswith("Reenvío a qBittorrent: se vuelve a añadir el torrent 4242")
    assert QBIT_AÑADIDO in mensajes
    assert mensajes[-1].startswith("Reenvío a qBittorrent hecho")
    assert not any(CLAVE_EMU in m for m in mensajes)
    assert avisos.count((eventos.EVENTO_SUBIDA_ESTADO, {"job_id": jid})) == 2   # en_curso y ok


def test_reenvio_fallido_no_toca_la_subida(db_conn, tmp_path, uploadrr, avisos):
    jid = _job(db_conn, tmp_path)
    _qbit_fallido(jid)
    antes = _fila(db_conn, jid)
    proc = _proc(1, ["Adding and rechecking torrent\n", "Torrent client add failed: Connection refused\n"])
    with patch(POPEN, return_value=proc):
        reenviar_a_qbittorrent(jid, wait=True)

    despues = _fila(db_conn, jid)
    assert despues["reenvio_qbit_estado"] == "fallido"
    for campo in ("status", "finished_at", "result_url", "result_torrent_id", "duration_seconds", "torrent_subido"):
        assert despues[campo] == antes[campo], campo
    assert _fase(jid, "qbittorrent_add") == "failed"
    mensajes = _mensajes(jid)
    assert "qBittorrent: falló al añadir el torrent (Connection refused)." in mensajes
    assert mensajes[-1] == "Reenvío a qBittorrent fallido (exit code 1). La subida a eMuwarez sigue igual."
    # Fallido, se puede volver a intentar.
    assert puede_reenviar_qbit(get_upload_job(jid)) is True


def test_si_no_se_puede_lanzar_queda_fallido(db_conn, tmp_path, uploadrr):
    jid = _job(db_conn, tmp_path)
    with patch(POPEN, side_effect=FileNotFoundError("python3")):
        reenviar_a_qbittorrent(jid, wait=True)
    assert _fila(db_conn, jid)["reenvio_qbit_estado"] == "fallido"
    assert _fila(db_conn, jid)["status"] == "completed"
    assert _mensajes(jid)[-1].startswith("Reenvío a qBittorrent fallido: ")


def test_no_se_lanzan_dos_a_la_vez(db_conn, tmp_path, uploadrr):
    jid = _job(db_conn, tmp_path)
    assert reenvio_qbit._escribir_estado(jid, "en_curso", solo_si_libre=True) is True
    assert reenvio_qbit._escribir_estado(jid, "en_curso", solo_si_libre=True) is False


# ---------------------------------------------------------------------------
# Borrar y arrancar
# ---------------------------------------------------------------------------

def test_no_se_borra_mientras_se_reenvia(db_conn, tmp_path, uploadrr):
    jid = _job(db_conn, tmp_path, estado="en_curso")
    resultado = delete_upload_job(jid, confirmation=DELETE_CONFIRMATION_TOKEN, conn=db_conn)
    assert (resultado["ok"], resultado["error"]) == (False, "reenvio_en_curso")
    assert get_upload_job(jid) is not None

    db_conn.execute("UPDATE upload_jobs SET reenvio_qbit_estado = 'fallido' WHERE id = ?", (jid,))
    db_conn.commit()
    assert delete_upload_job(jid, confirmation=DELETE_CONFIRMATION_TOKEN, conn=db_conn)["ok"] is True


def test_al_arrancar_un_reenvio_a_medias_queda_fallido_y_la_subida_igual(db_conn, tmp_path, uploadrr):
    jid = _job(db_conn, tmp_path, estado="en_curso")
    antes = _fila(db_conn, jid)
    reconcile_interrupted_jobs()
    despues = _fila(db_conn, jid)
    assert despues["reenvio_qbit_estado"] == "fallido"
    for campo in ("status", "stage", "finished_at", "error_message", "result_url"):
        assert despues[campo] == antes[campo], campo
    assert any("durante el reenvío a qBittorrent" in m for m in _mensajes(jid))


# ---------------------------------------------------------------------------
# La migración (esquema 55)
# ---------------------------------------------------------------------------

def test_la_migracion_añade_las_columnas_y_se_puede_repetir(db_conn):
    columnas = lambda: {f[1] for f in db_conn.execute("PRAGMA table_info(upload_jobs)")}  # noqa: E731
    assert {"reenvio_qbit_estado", "reenvio_qbit_at"} <= columnas()
    db_conn.execute("ALTER TABLE upload_jobs DROP COLUMN reenvio_qbit_estado")
    db_conn.execute("ALTER TABLE upload_jobs DROP COLUMN reenvio_qbit_at")
    _migrate_upload_jobs_reenvio_qbit(db_conn)
    _migrate_upload_jobs_reenvio_qbit(db_conn)
    assert {"reenvio_qbit_estado", "reenvio_qbit_at"} <= columnas()


# ---------------------------------------------------------------------------
# La API
# ---------------------------------------------------------------------------

def test_la_api_pide_sesion(client):
    assert client.post("/api/uploads/1/reenviar-qbit").status_code == 401


def test_la_api_reenvia_y_expone_el_estado(client, auth_headers, db_conn, tmp_path, uploadrr):
    jid = _job(db_conn, tmp_path)
    detalle = client.get(f"/api/uploads/{jid}", headers=auth_headers).json()
    assert (detalle["puede_reenviar_qbit"], detalle["reenvio_qbit_estado"]) == (True, None)

    with patch.object(reenvio_qbit, "_reenviar") as reenviar:
        resp = client.post(f"/api/uploads/{jid}/reenviar-qbit", headers=auth_headers)
        for _ in range(100):   # el hilo llama al simulado enseguida
            if reenviar.called:
                break
            time.sleep(0.02)
    assert resp.status_code == 200 and reenviar.called
    detalle = client.get(f"/api/uploads/{jid}", headers=auth_headers).json()
    assert (detalle["puede_reenviar_qbit"], detalle["reenvio_qbit_estado"]) == (False, "en_curso")
    assert detalle["status"] == "completed"

    resp = client.post(f"/api/uploads/{jid}/reenviar-qbit", headers=auth_headers)
    assert resp.status_code == 409 and resp.json()["detail"] == "Ya se está reenviando a qBittorrent."


def test_la_api_dice_404_y_409(client, auth_headers, db_conn, tmp_path, uploadrr):
    assert client.post("/api/uploads/999/reenviar-qbit", headers=auth_headers).status_code == 404
    jid = _job(db_conn, tmp_path, status="failed")
    resp = client.post(f"/api/uploads/{jid}/reenviar-qbit", headers=auth_headers)
    assert resp.status_code == 409 and resp.json()["detail"] == "Solo se reenvía a qBittorrent una subida que ya está en eMuwarez."


# ---------------------------------------------------------------------------
# Con eventos (`--eventos`): deciden ellos, el texto solo se registra
# ---------------------------------------------------------------------------

CONTRATO = Path(__file__).parent / "fixtures" / "contrato_eventos.json"


def _proc_con_eventos(eventos_escritos, returncode=0, stdout=()):
    """Un Popen simulado que, al lanzarse, escribe esos eventos en el fichero de `--eventos` de su argv."""
    def lanzar(argv, **_kw):
        ruta = argv[argv.index("--eventos") + 1]
        with open(ruta, "a", encoding="utf-8") as f:
            for ev in eventos_escritos:
                f.write(json.dumps({"v": 1, "ts": "1970-01-01T00:00:00.000Z", **ev}) + chr(10))
        return _proc(returncode, stdout)
    return lanzar


INICIO_SC = {"ev": "inicio", "uuid": "Peli.mkv", "modo": "solo_cliente", "reanuda": True}


def test_reenvio_con_eventos_bien(db_conn, tmp_path, uploadrr, avisos, monkeypatch):
    shutil.copy(CONTRATO, uploadrr / "contrato_eventos.json")
    jid = _job(db_conn, tmp_path)
    _qbit_fallido(jid)
    monkeypatch.setattr(seeding_qbit, "_cache", ("huella", 0.0, [], ""))
    eventos_ok = [INICIO_SC, {"ev": "fase", "clave": "cliente", "estado": "empieza"},
                  {"ev": "fase", "clave": "cliente", "estado": "hecha"},
                  {"ev": "cliente", "ok": True, "cliente": "qbit", "ruta": "/d"},
                  {"ev": "resultado", "ok": True, "codigo": "cliente_ok"}]
    (uploadrr / "data").mkdir()
    (uploadrr / "data" / "config.yaml").write_text("DEFAULT:\n  tmdb: ''\n", encoding="utf-8")
    lanzar, leidos = _proc_con_eventos(eventos_ok), []

    def lanzar_y_leer_config(argv, **kw):
        leidos.append(Path(argv[argv.index("--config") + 1]).read_text(encoding="utf-8"))
        return lanzar(argv, **kw)

    # El texto no dice nada de «Added to:»: con eventos lo que cuenta es `cliente_ok`.
    with patch(POPEN, side_effect=lanzar_y_leer_config) as popen:
        assert reenviar_a_qbittorrent(jid, wait=True)["ok"] is True

    argv = popen.call_args.args[0]
    assert argv[-7:-4] == ["--resume", "--solo-cliente", "4242"] and argv[-4] == "--eventos" and argv[-2] == "--config"
    assert Path(argv[-3]).name == f"{jid}-reenvio.jsonl" and not Path(argv[-3]).exists()
    # Su propio config, con las claves de Ajustes, borrado al terminar; el config.yaml del usuario no se toca.
    assert Path(argv[-1]).name == f"{jid}-reenvio.yaml" and not Path(argv[-1]).exists()
    assert f"api_key: '{CLAVE_EMU}'" in leidos[0]
    assert (uploadrr / "data" / "config.yaml").read_text(encoding="utf-8") == "DEFAULT:\n  tmdb: ''\n"
    assert not (uploadrr / "data" / "config.yaml.bak").exists()
    assert _fila(db_conn, jid)["reenvio_qbit_estado"] == "ok"
    assert _fase(jid, "qbittorrent_add") == "done"
    mensajes = _mensajes(jid)
    assert QBIT_AÑADIDO in mensajes and mensajes[-1].startswith("Reenvío a qBittorrent hecho")


@pytest.mark.parametrize("returncode, texto", [(1, ""), (0, "Added to: /d")])
def test_reenvio_con_eventos_fallido(db_conn, tmp_path, uploadrr, avisos, returncode, texto):
    """`cliente_fallido` manda aunque el texto dijera «Added to:» o el exit fuera 0."""
    shutil.copy(CONTRATO, uploadrr / "contrato_eventos.json")
    jid = _job(db_conn, tmp_path)
    _qbit_fallido(jid)
    eventos_mal = [INICIO_SC, {"ev": "fase", "clave": "cliente", "estado": "empieza"},
                   {"ev": "fase", "clave": "cliente", "estado": "fallida"},
                   {"ev": "cliente", "ok": False, "cliente": "qbit", "motivo": "login"},
                   {"ev": "resultado", "ok": False, "codigo": "cliente_fallido", "detalle": "credenciales incorrectas"}]
    with patch(POPEN, side_effect=_proc_con_eventos(eventos_mal, returncode, [texto + chr(10)] if texto else [])):
        reenviar_a_qbittorrent(jid, wait=True)

    despues = _fila(db_conn, jid)
    assert despues["reenvio_qbit_estado"] == "fallido" and despues["status"] == "completed"
    assert _fase(jid, "qbittorrent_add") == "failed"
    mensajes = _mensajes(jid)
    assert QBIT_LOGIN_INCORRECTO in mensajes and QBIT_AÑADIDO not in mensajes
    assert "credenciales incorrectas" in mensajes[-1] and mensajes[-1].startswith("Reenvío a qBittorrent fallido")


def test_reenvio_con_eventos_sin_resultado_es_fallido(db_conn, tmp_path, uploadrr, avisos):
    shutil.copy(CONTRATO, uploadrr / "contrato_eventos.json")
    jid = _job(db_conn, tmp_path)
    with patch(POPEN, side_effect=_proc_con_eventos([INICIO_SC], returncode=0)):
        reenviar_a_qbittorrent(jid, wait=True)
    assert _fila(db_conn, jid)["reenvio_qbit_estado"] == "fallido"
    assert "murió sin dar un resultado" in _mensajes(jid)[-1]


def test_reenvio_con_contrato_pero_sin_eventos_lee_el_texto(db_conn, tmp_path, uploadrr, avisos):
    shutil.copy(CONTRATO, uploadrr / "contrato_eventos.json")
    jid = _job(db_conn, tmp_path)
    with patch(POPEN, return_value=_proc(0, ["Added to: qbit" + chr(10)])):
        reenviar_a_qbittorrent(jid, wait=True)
    assert _fila(db_conn, jid)["reenvio_qbit_estado"] == "ok" and QBIT_AÑADIDO in _mensajes(jid)


def test_los_mensajes_del_reenvio_no_hablan_de_subir():
    from emuwarez_uploadrr.services.upload_execution.comun import mensaje_qbit_fallo
    assert "justo después de subir" in mensaje_qbit_fallo("sin_torrent")
    reenvio = mensaje_qbit_fallo("sin_torrent", reenvio=True)
    assert "subir" not in reenvio and "más arriba" not in reenvio
    assert "pendiente de moderación" in mensaje_qbit_fallo("no_visible", reenvio=True)
