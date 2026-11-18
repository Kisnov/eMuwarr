"""Objetivos de tu cuenta de eMuwarez y fecha de alta: lo que se guarda y lo que se rechaza."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

URL = "/api/estadisticas/objetivos"


def test_exige_sesion(client):
    assert client.get(URL).status_code == 401
    assert client.put(URL, json={}).status_code == 401


def test_sin_nada_guardado(client, auth_headers):
    assert client.get(URL, headers=auth_headers).json() == {"fecha_alta": None, "objetivos": []}


def test_guarda_y_devuelve(client, auth_headers):
    cuerpo = {"fecha_alta": "2026-02-20", "objetivos": [{"tipo": "subido", "valor": 150}, {"tipo": "antiguedad", "valor": 12}]}
    assert client.put(URL, json=cuerpo, headers=auth_headers).json() == cuerpo
    assert client.get(URL, headers=auth_headers).json() == cuerpo
    # Se sustituye entero: lo que no llega se quita.
    assert client.put(URL, json={"objetivos": []}, headers=auth_headers).json() == {"fecha_alta": None, "objetivos": []}


@pytest.mark.parametrize("cuerpo", [
    {"fecha_alta": "20/02/2026"},
    {"fecha_alta": (date.today() + timedelta(days=30)).isoformat()},   # lejos: el parametrize se evalúa al recoger
    {"objetivos": [{"tipo": "ratio", "valor": 0}]},
    {"objetivos": [{"tipo": "ratio", "valor": -1}]},
    {"objetivos": [{"tipo": "invitaciones", "valor": 3}]},
    {"objetivos": [{"tipo": "ratio", "valor": 2}, {"tipo": "ratio", "valor": 3}]},
])
def test_rechaza_lo_que_no_vale(client, auth_headers, cuerpo):
    assert client.put(URL, json=cuerpo, headers=auth_headers).status_code == 422
    assert client.get(URL, headers=auth_headers).json() == {"fecha_alta": None, "objetivos": []}
