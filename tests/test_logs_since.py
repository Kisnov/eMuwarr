"""GET /api/logs?since=<seq>: solo lo que ha entrado desde la última lectura, y sin `since` todo sigue igual."""
from __future__ import annotations

import logging
from collections import deque

import pytest

from emuwarez_uploadrr.web import logging_setup


@pytest.fixture
def anillo(monkeypatch):
    """Un anillo vacío y propio, y una función para meterle líneas como lo haría el logging real."""
    monkeypatch.setattr(logging_setup, "_ring", deque(maxlen=logging_setup._RING_MAX))
    handler = logging_setup.RingBufferHandler()

    def emitir(mensaje: str, nivel: int = logging.INFO, origen: str = "emuwarez.prueba") -> None:
        handler.emit(logging.LogRecord(origen, nivel, __file__, 1, mensaje, None, None))

    return emitir


def _leer(client, auth_headers, **params) -> dict:
    resp = client.get("/api/logs", params=params, headers=auth_headers)
    assert resp.status_code == 200
    return resp.json()


def test_cada_linea_lleva_un_seq_creciente(anillo):
    for i in range(5):
        anillo(f"linea {i}")
    seqs = [r["seq"] for r in logging_setup.get_recent_logs()]  # de la más nueva a la más vieja
    assert seqs == sorted(seqs, reverse=True)
    assert len(set(seqs)) == 5


def test_sin_since_la_respuesta_es_la_de_siempre(client, auth_headers, anillo):
    anillo("marca-uno")
    anillo("marca-dos")
    datos = _leer(client, auth_headers, search="marca-")
    assert [i["message"] for i in datos["items"]] == ["marca-dos", "marca-uno"]
    assert datos["count"] == 2
    assert "truncated" not in datos


def test_con_since_solo_vuelve_lo_nuevo(client, auth_headers, anillo):
    anillo("marca-uno")
    anillo("marca-dos")
    ultimo = _leer(client, auth_headers, search="marca-")["items"][0]["seq"]

    vacio = _leer(client, auth_headers, search="marca-", since=ultimo)
    assert vacio == {"items": [], "count": 0, "truncated": False}

    anillo("marca-tres")
    anillo("marca-cuatro")
    nuevo = _leer(client, auth_headers, search="marca-", since=ultimo)
    assert [i["message"] for i in nuevo["items"]] == ["marca-cuatro", "marca-tres"]
    assert nuevo["count"] == 2 and nuevo["truncated"] is False
    # Sigue el hilo: la lectura siguiente parte de la más nueva que ya se tiene.
    assert _leer(client, auth_headers, search="marca-", since=nuevo["items"][0]["seq"])["items"] == []


def test_since_respeta_nivel_y_busqueda(client, auth_headers, anillo):
    anillo("marca-info")
    desde = _leer(client, auth_headers, search="marca-")["items"][0]["seq"]
    anillo("marca-info-b", logging.INFO)
    anillo("marca-error", logging.ERROR)
    anillo("otra cosa", logging.ERROR)
    solo_errores = _leer(client, auth_headers, search="marca-", level="ERROR", since=desde)
    assert [i["message"] for i in solo_errores["items"]] == ["marca-error"]


def test_since_trunca_a_limit_y_lo_avisa(client, auth_headers, anillo):
    anillo("marca-0")
    desde = _leer(client, auth_headers, search="marca-")["items"][0]["seq"]
    for i in range(1, 6):
        anillo(f"marca-{i}")
    datos = _leer(client, auth_headers, search="marca-", since=desde, limit=3)
    # Las 3 más nuevas: el cliente, al que le falta un tramo, sabe que debe leer la lista entera.
    assert [i["message"] for i in datos["items"]] == ["marca-5", "marca-4", "marca-3"]
    assert datos["truncated"] is True
    justo = _leer(client, auth_headers, search="marca-", since=desde, limit=5)
    assert justo["count"] == 5 and justo["truncated"] is False


def test_since_invalido_es_un_422(client, auth_headers):
    assert client.get("/api/logs?since=-1", headers=auth_headers).status_code == 422
    assert client.get("/api/logs?since=abc", headers=auth_headers).status_code == 422
