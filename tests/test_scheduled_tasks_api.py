"""API tests for /api/tasks/scheduled — Phase 11D.

All underlying sync work is mocked — never real Radarr/Sonarr/eMuwarez
network traffic.
"""
from __future__ import annotations

from unittest.mock import patch


def test_list_requires_auth(client):
    assert client.get("/api/tasks/scheduled").status_code == 401


def test_update_requires_auth(client):
    assert client.put("/api/tasks/scheduled/sync-library", json={"interval_minutes": 30}).status_code == 401


def test_run_requires_auth(client):
    assert client.post("/api/tasks/scheduled/sync-library/run").status_code == 401


def test_logs_requires_auth(client):
    assert client.get("/api/tasks/scheduled/sync-library/logs").status_code == 401


def test_list_returns_default_tasks(client, auth_headers):
    """Se compara contra DEFAULT_TASKS, no contra una lista escrita a mano:
    esta prueba llevaba tiempo en rojo por exigir una tarea que se retiró."""
    from emuwarez_uploadrr.services.scheduler import DEFAULT_TASKS

    resp = client.get("/api/tasks/scheduled", headers=auth_headers)
    assert resp.status_code == 200
    keys = {t["task_key"] for t in resp.json()}
    assert keys == {t[0] for t in DEFAULT_TASKS}


def test_la_sincronizacion_de_peticiones_vuelve_a_ser_una_tarea_programada(client, auth_headers):
    """Cada 24 h, y el botón sigue valiendo (con su límite de una por hora, en services/requests.py)."""
    from emuwarez_uploadrr.services.scheduler import OBSOLETE_TASKS

    tareas = {t["task_key"]: t for t in client.get("/api/tasks/scheduled", headers=auth_headers).json()}
    assert tareas["sync-emu-requests"]["interval_minutes"] == 1440
    assert "sync-emu-requests" not in OBSOLETE_TASKS


def test_update_interval_success(client, auth_headers):
    resp = client.put("/api/tasks/scheduled/sync-library", json={"interval_minutes": 45}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["interval_minutes"] == 45


def test_update_interval_below_minimum_returns_422(client, auth_headers):
    resp = client.put("/api/tasks/scheduled/sync-library", json={"interval_minutes": 5}, headers=auth_headers)
    assert resp.status_code == 422


def test_update_interval_above_a_year_returns_422(client, auth_headers):
    resp = client.put("/api/tasks/scheduled/sync-library", json={"interval_minutes": 10**10}, headers=auth_headers)
    assert resp.status_code == 422


def test_update_interval_unknown_task_returns_404(client, auth_headers):
    resp = client.put("/api/tasks/scheduled/not-a-task", json={"interval_minutes": 30}, headers=auth_headers)
    assert resp.status_code == 404


def test_run_library_sync_now(client, auth_headers):
    with patch("emuwarez_uploadrr.services.library.sync_all") as mock_sync:
        mock_sync.return_value = {"imported": 2, "missing": 0, "instances": 1, "errors": []}
        resp = client.post("/api/tasks/scheduled/sync-library/run", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["task_key"] == "sync-library"


def test_run_emu_sync_now(client, auth_headers):
    with patch("emuwarez_uploadrr.services.requests.sync_emu_requests") as mock_sync:
        mock_sync.return_value = {"status": "ok", "imported": 3, "matched": 1}
        resp = client.post("/api/tasks/scheduled/sync-emu-requests/run", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert resp.json()["summary"] == "3 peticiones importadas, 1 cruces"


def test_run_unknown_task_returns_404(client, auth_headers):
    resp = client.post("/api/tasks/scheduled/not-a-task/run", headers=auth_headers)
    assert resp.status_code == 404


def test_get_logs_for_known_task(client, auth_headers):
    # Patch list_instances (not sync_all itself) so sync_all()'s own
    # internal task_runs logging still runs for real, with zero instances
    # to iterate — no real network call, but a genuine task_runs row.
    with patch("emuwarez_uploadrr.services.library.sync.list_instances", return_value=[]):
        client.post("/api/tasks/scheduled/sync-library/run", headers=auth_headers)

    resp = client.get("/api/tasks/scheduled/sync-library/logs", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_key"] == "sync-library"
    assert len(data["logs"]) >= 1
    assert data["logs"][0]["status"] == "completed"


def test_get_logs_for_unknown_task_returns_404(client, auth_headers):
    resp = client.get("/api/tasks/scheduled/not-a-task/logs", headers=auth_headers)
    assert resp.status_code == 404


def test_run_response_never_leaks_secrets(client, auth_headers):
    secret = "super_secret_radarr_key_value"
    with patch("emuwarez_uploadrr.services.library.sync_all") as mock_sync:
        mock_sync.return_value = {"imported": 1, "missing": 0, "instances": 1, "errors": [f"instance error with {secret}"]}
        resp = client.post("/api/tasks/scheduled/sync-library/run", headers=auth_headers)
    # This just documents the contract: summary text is our own constructed
    # string, not a raw dump of instance errors, so a leaked key in an
    # instance-level error message would not automatically appear here.
    assert secret not in resp.text


# --- «Ver logs» de las tareas que no apuntaban sus ejecuciones como ellas mismas --------------------------------------

def _logs(client, auth_headers, task_key):
    resp = client.get(f"/api/tasks/scheduled/{task_key}/logs", headers=auth_headers)
    assert resp.status_code == 200
    return resp.json()


def test_leer_tus_subidas_deja_su_ejecucion_en_ver_logs(client, auth_headers):
    with patch("emuwarez_uploadrr.services.mis_torrents.sincronizar", return_value=("completed", "85 subidas, 0 sin seeders")):
        client.post("/api/tasks/scheduled/leer-mis-subidas-emu/run", headers=auth_headers)
    datos = _logs(client, auth_headers, "leer-mis-subidas-emu")
    assert [(r["status"], r["summary"]) for r in datos["logs"]] == [("completed", "85 subidas, 0 sin seeders")]
    assert datos["sin_historial"] is False


def test_con_una_subida_en_marcha_no_se_apunta_cada_intento(client, auth_headers):
    with patch("emuwarez_uploadrr.services.mis_torrents.sincronizar", return_value=("busy", "Hay una subida en marcha")):
        client.post("/api/tasks/scheduled/leer-mis-subidas-emu/run", headers=auth_headers)
    assert _logs(client, auth_headers, "leer-mis-subidas-emu")["logs"] == []


def test_la_descarga_completa_del_catalogo_ensena_las_lecturas_del_catalogo(client, auth_headers):
    from emuwarez_uploadrr.database import connect, db_path
    from emuwarez_uploadrr.services.tasks import finish_task, start_task
    conn = connect(db_path())
    try:
        finish_task(conn, start_task(conn, "sync-emu-catalog", "Sincronizando catálogo eMuwarez (completo)"), "completed",
                    "13000 torrents")
    finally:
        conn.close()
    assert [r["summary"] for r in _logs(client, auth_headers, "sync-emu-catalog-full")["logs"]] == ["13000 torrents"]


def test_las_tareas_sin_historial_ensenan_su_ultima_ejecucion(client, auth_headers):
    antes = _logs(client, auth_headers, "vigilar-instancias")
    assert antes == {"task_key": "vigilar-instancias", "logs": [], "sin_historial": True}
    with patch("emuwarez_uploadrr.services.vigilancia_instancias.vigilar_instancias",
               return_value=("completed", "2 instancias responden")):
        client.post("/api/tasks/scheduled/vigilar-instancias/run", headers=auth_headers)
    datos = _logs(client, auth_headers, "vigilar-instancias")
    assert datos["sin_historial"] is True
    assert [(r["status"], r["summary"]) for r in datos["logs"]] == [("completed", "2 instancias responden")]
    assert datos["logs"][0]["started_at"]

