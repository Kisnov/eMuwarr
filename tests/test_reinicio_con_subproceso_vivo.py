"""Arrancar la app con subidas a medias cuando el Uploadrr-EMU del arranque anterior sigue vivo.

Los subprocesos van en su propia sesión: si la app muere de golpe (kill -9, OOM) no caen con ella. Lo que ya cubren
test_upload_queue / test_subida_por_pasos (un trabajo por estado activo, sin subproceso) no se repite aquí.
"""
from __future__ import annotations

import subprocess
import sys

import psutil
import pytest

from emuwarez_uploadrr.database import connect, db_path, init_db
from emuwarez_uploadrr.services import upload_queue as uq
from emuwarez_uploadrr.services.upload_states import ACTIVE_JOB_STATUSES

# Estado activo -> fase en que estaba (las que usa el pipeline).
FASES = {"queued": "queued", "staging": "nomenclature", "preparing": "remux", "running": "running"}


@pytest.fixture
def db_conn(isolated_db):
    conn = connect(db_path())
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def uploadrr_vivo():
    """Un proceso en su propia sesión, como el Uploadrr-EMU que lanza la app; se mata al acabar."""
    procesos = []

    def lanzar() -> int:
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        procesos.append(p)
        return p.pid

    yield lanzar
    for p in procesos:
        p.kill()
        p.wait()


def _job(conn, status, stage, pid, torrent_subido=0):
    cur = conn.execute(
        "INSERT INTO upload_jobs (title, path, status, stage, process_id, torrent_subido, created_at, updated_at) "
        "VALUES ('T', '/data/t.mkv', ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (status, stage, pid, torrent_subido))
    conn.commit()
    return cur.lastrowid


def _fila(conn, jid):
    return conn.execute("SELECT status, stage, error_message, etapa_parada FROM upload_jobs WHERE id = ?", (jid,)).fetchone()


@pytest.mark.parametrize("estado", sorted(ACTIVE_JOB_STATUSES))
def test_cada_estado_activo_con_su_proceso_vivo_queda_fallido_y_reintentable(db_conn, uploadrr_vivo, estado):
    jid = _job(db_conn, estado, FASES[estado], uploadrr_vivo())

    assert uq.reconcile_interrupted_jobs(conn=db_conn) == 1

    fila = _fila(db_conn, jid)
    assert (fila["status"], fila["stage"]) == ("failed", "failed")
    assert "Puedes reintentar" in fila["error_message"]
    assert fila["etapa_parada"] == (FASES[estado] if estado != "queued" else None)


def test_si_el_torrent_ya_estaba_en_emuwarez_el_aviso_manda_a_reenviar_a_qbittorrent(db_conn, uploadrr_vivo):
    jid = _job(db_conn, "running", "running", uploadrr_vivo(), torrent_subido=1)

    uq.reconcile_interrupted_jobs(conn=db_conn)

    mensaje = _fila(db_conn, jid)["error_message"]
    assert "Reenviar a qBittorrent" in mensaje and "Puedes reintentar" not in mensaje


@pytest.mark.xfail(strict=True, reason=(
    "A DECIDIR: al reconciliar, el Uploadrr-EMU del arranque anterior sigue vivo (sesión propia) y la subida se da por "
    "fallida con «Puedes reintentar»; un reintento lanzaría un segundo Uploadrr-EMU sobre el mismo tmp/ y podría duplicar "
    "la subida. Nadie mira process_id al arrancar."))
def test_reconciliar_no_deja_un_uploadrr_huerfano_vivo(db_conn, uploadrr_vivo):
    pid = uploadrr_vivo()
    _job(db_conn, "running", "running", pid)

    uq.reconcile_interrupted_jobs(conn=db_conn)

    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
