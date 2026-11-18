"""El límite de peticiones por minuto que Uploadrr-EMU comparte con la app (src/utils/limitador.py).

El módulo es una copia exacta del de la app (emuwarez_uploadrr/integrations/limitador_compartido.py, cuyos tests
cubren los procesos); aquí, el formato del fichero, el modo sin directorio y el fallo abierto."""

import ast
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from src.utils import limitador

DORADO = Path(__file__).parent / "golden" / "limitador.json"
con_fcntl = pytest.mark.skipif(limitador.fcntl is None, reason="sin fcntl no hay límite entre procesos")


def run(coro):
    return asyncio.run(coro)


def test_es_python_3_9():
    ast.parse(Path(limitador.__file__).read_text(encoding="utf-8"), feature_version=(3, 9))


def test_el_fichero_dorado_se_lee_igual():
    # Lo mismo comprueba la app con su copia (tests/test_limitador_compartido.py).
    marcas, penalizado_hasta = limitador.leer(DORADO.read_bytes(), 1060.0)
    assert marcas == [1010.5, 1059.0] and penalizado_hasta == 1100.0


@con_fcntl
def test_formato_y_nombre_del_fichero(tmp_path):
    limitador.reservar(tmp_path, "EMU", " clave-de-prueba \n", reloj=lambda: 1000.0)
    fichero = tmp_path / f"EMU-{hashlib.sha256(b'clave-de-prueba').hexdigest()[:16]}.json"
    assert json.loads(fichero.read_text()) == {"marcas": [1000.0], "penalizado_hasta": 0.0}


def test_sin_directorio_no_hace_nada(monkeypatch):
    def nunca(*a, **k):
        raise AssertionError("sin LIMITADOR_DIR ni CONFIG_DIR no se toca ningún fichero")

    monkeypatch.setattr(limitador, "reservar", nunca)
    monkeypatch.setattr(limitador, "penalizar", nunca)
    assert run(limitador.esperar("EMU", "k")) == 0
    limitador.apuntar_429("EMU", "k", "5")


def test_config_dir_basta(monkeypatch, tmp_path):
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    assert limitador.directorio_por_entorno() == tmp_path / "limitador"
    monkeypatch.setenv("LIMITADOR_DIR", str(tmp_path / "otro"))
    assert limitador.directorio_por_entorno() == tmp_path / "otro"


def test_directorio_inservible_deja_pasar_y_avisa_una_vez(monkeypatch, tmp_path):
    fichero = tmp_path / "no-es-un-directorio"
    fichero.write_text("x")
    monkeypatch.setenv("LIMITADOR_DIR", str(fichero))
    monkeypatch.setattr(limitador, "_avisado", False)
    avisos = []
    assert run(limitador.esperar("EMU", "k", avisar=avisos.append)) < 1
    limitador.apuntar_429("EMU", "k", "5", avisar=avisos.append)
    assert run(limitador.esperar("EMU", "k", avisar=avisos.append)) < 1
    assert len(avisos) == 1 and "no está disponible" in avisos[0]


@con_fcntl
def test_una_subida_espera_acotado_y_luego_pide(monkeypatch, tmp_path):
    monkeypatch.setenv("LIMITADOR_DIR", str(tmp_path))
    limitador.penalizar(tmp_path, "EMU", "k", 60)
    avisos = []
    esperado = run(limitador.esperar("EMU", "k", espera_maxima=0.3, avisar=avisos.append))
    assert 0.3 <= esperado < 2
    assert "espera de 60 s" in avisos[0] and "se pide igualmente" in avisos[1]


@con_fcntl
def test_una_pausa_corta_se_espera_con_asyncio(monkeypatch, tmp_path):
    monkeypatch.setenv("LIMITADOR_DIR", str(tmp_path))
    limitador.penalizar(tmp_path, "EMU", "k", 0.3)
    dormidas = []
    dormir = asyncio.sleep

    async def espia(segundos):
        dormidas.append(segundos)
        await dormir(segundos)

    monkeypatch.setattr(limitador.asyncio, "sleep", espia)
    assert 0.2 < run(limitador.esperar("EMU", "k")) < 2
    assert dormidas and max(dormidas) <= 0.3
