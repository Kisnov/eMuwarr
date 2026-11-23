"""La descripción propia de una subida (BBCode escrito en el Resumen): se guarda en el trabajo, sigue en sus reintentos y en
«Subir también a…», y Uploadrr-EMU la recibe como `-df <fichero>`, que la app escribe al lanzar y borra al acabar."""
from __future__ import annotations

import pytest

from emuwarez_uploadrr.services import subir_tambien as st
from emuwarez_uploadrr.services import upload_queue as uq
from emuwarez_uploadrr.services.upload_execution import build_uploadrr_execution_command, get_upload_job
from emuwarez_uploadrr.services.upload_execution import config as ue
from tests.test_eventos_uploadrr import _crear_job, _lanzar, _poner_ajustes, _terminal
from tests.test_subida_por_tracker import _clave, _fila, _peli, _trabajo, db_conn  # noqa: F401 (fixture)
from tests.test_subir_tambien import _emu_completada, _entero, _qbit, sin_hilos  # noqa: F401 (fixture)

_FIXTURES_COMPARTIDAS = (db_conn, sin_hilos)

TEXTO = "[b]Notas del encode[/b]\n[spoiler]log[/spoiler]"


@pytest.mark.parametrize(("enviada", "guardada"), [(TEXTO, TEXTO), ("  \n ", None), (None, None)])
def test_la_cola_guarda_la_descripcion(client, auth_headers, db_conn, tmp_path, sin_hilos, enviada, guardada):
    iid = _peli(db_conn, str(tmp_path / "a.mkv"))
    r = client.post(f"/api/library/{iid}/upload/queue", json={"descripcion": enviada}, headers=auth_headers)
    assert r.status_code == 200, r.text
    job_id = r.json()["job"]["id"]
    assert _fila(db_conn, job_id)["descripcion"] == guardada
    assert client.get(f"/api/uploads/{job_id}", headers=auth_headers).json()["descripcion"] == guardada


def test_la_api_rechaza_una_descripcion_enorme(client, auth_headers, db_conn, tmp_path):
    iid = _peli(db_conn, str(tmp_path / "a.mkv"))
    r = client.post(f"/api/library/{iid}/upload/queue", json={"descripcion": "x" * 30001}, headers=auth_headers)
    assert r.status_code == 422


def test_el_reintento_lleva_la_misma(db_conn, tmp_path):
    iid = _peli(db_conn, str(tmp_path / "a.mkv"))
    fallida = _trabajo(db_conn, iid, status="failed", stage="failed", descripcion=TEXTO)
    r = uq.queue_retry_for_job(fallida, conn=db_conn)
    assert _fila(db_conn, r["job"]["id"])["descripcion"] == TEXTO


def test_subir_tambien_copia_la_de_emuwarez(db_conn, monkeypatch, tmp_path, sin_hilos):
    _clave(db_conn, "nobs")
    _clave(db_conn, "mnv")
    _iid, emu, ruta = _emu_completada(db_conn, tmp_path, descripcion=TEXTO)
    _qbit(monkeypatch, db_conn, [_entero(ruta)])

    r = st.subir_tambien(db_conn, [emu], ["nobs", "mnv"])

    assert r["encolados"] == 2
    assert [_fila(db_conn, x["nuevo_job_id"])["descripcion"] for x in r["resultados"]] == [TEXTO, TEXTO]


def test_el_comando_lleva_df_solo_con_descripcion(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path):
    _poner_ajustes(db_conn, fake_uploadrr_emu_dir, fake_uploadrr_python_path)
    jid = _crear_job(db_conn, tmp_path)
    job = get_upload_job(jid, conn=db_conn)
    sin = build_uploadrr_execution_command(job, conn=db_conn)
    assert "-df" not in sin["args"] and sin["descripcion"] is None

    con = build_uploadrr_execution_command({**job, "descripcion": TEXTO}, conn=db_conn)
    assert con["args"][con["args"].index("-df") + 1] == str(ue.ruta_descripcion(jid))
    assert (con["descripcion"], con["descripcion_texto"]) == (str(ue.ruta_descripcion(jid)), TEXTO)
    # «Reenviar a qBittorrent» no sube nada: no la lleva.
    reenvio = build_uploadrr_execution_command({**job, "descripcion": TEXTO}, conn=db_conn, solo_cliente="4242")
    assert "-df" not in reenvio["args"] and reenvio["descripcion"] is None


def test_la_subida_lanza_con_su_descripcion_y_la_borra(db_conn, tmp_path, monkeypatch, fake_uploadrr_emu_dir,
                                                        fake_uploadrr_python_path):
    copia = tmp_path / "descripcion_leida.txt"
    monkeypatch.setenv("FAKE_UPLOADRR_DESCRIPCION_COPIA", str(copia))
    monkeypatch.setattr("tests.test_eventos_uploadrr._crear_job", _con_descripcion(_crear_job))

    jid = _lanzar(db_conn, tmp_path, fake_uploadrr_emu_dir, fake_uploadrr_python_path)
    job = _terminal(db_conn, jid)

    assert job["status"] == "completed"
    assert copia.read_bytes().decode("utf-8") == TEXTO  # la tuvo, tal cual, mientras corría
    assert not ue.ruta_descripcion(jid).exists()


def _con_descripcion(crear):
    def _crear(conn, tmp_path):
        jid = crear(conn, tmp_path)
        conn.execute("UPDATE upload_jobs SET descripcion = ? WHERE id = ?", (TEXTO, jid))
        conn.commit()
        return jid
    return _crear


def test_al_arrancar_se_barre_la_que_quedo(isolated_db):
    ruta = ue.ruta_descripcion(7)
    ue.preparar_descripcion_de_la_subida(ruta, TEXTO)
    assert ruta.read_text(encoding="utf-8") == TEXTO
    assert ue.barrer_configs_de_subidas() == 1 and not ruta.exists()
