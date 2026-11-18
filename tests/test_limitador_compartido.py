"""El límite de peticiones a eMuwarez compartido entre procesos (integrations/limitador_compartido.py): la app, el CLI
y cada Uploadrr-EMU cuentan en el mismo fichero. Los «otros procesos» son subprocesos de verdad (Hijo, el de las plazas)."""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import time
from pathlib import Path

import pytest

from emuwarez_uploadrr.integrations import emu, emu_limitador, limitador_compartido as lc
from emuwarez_uploadrr.integrations.base import IntegrationError
from emuwarez_uploadrr.integrations.emu_limitador import Limitador
from tests.test_plazas_compartidas import RAIZ, Hijo

CLAVE = "CLAVESECRETA123"
COPIA_UPLOADRR = RAIZ / "uploadrr-emu" / "src" / "utils" / "limitador.py"
DORADO = RAIZ / "uploadrr-emu" / "tests" / "golden" / "limitador.json"

con_fcntl = pytest.mark.skipif(lc.fcntl is None, reason="sin fcntl no hay límite entre procesos")


@pytest.fixture
def hijos():
    lista: list[Hijo] = []

    def _crear(codigo, tmp_path, *args) -> Hijo:
        h = Hijo(codigo, tmp_path, *args)
        lista.append(h)
        return h

    yield _crear
    for h in lista:
        h.terminar()


@pytest.fixture
def directorio(tmp_path):
    return tmp_path / "limitador"  # el de directorio_por_entorno() con CONFIG_DIR = tmp_path


def _nunca_mas_de(marcas, limite, ventana):
    marcas = sorted(marcas)
    return all(marcas[i + limite] - marcas[i] >= ventana for i in range(len(marcas) - limite))


# --- Las dos copias ---------------------------------------------------------

def test_la_copia_de_uploadrr_es_identica():
    """Uploadrr-EMU no importa la app: lleva el mismo módulo, byte a byte."""
    assert COPIA_UPLOADRR.read_bytes() == Path(lc.__file__).read_bytes()


def test_el_fichero_dorado_se_lee_igual():
    # Lo mismo comprueba uploadrr-emu/tests/test_limitador.py con su copia.
    marcas, penalizado_hasta = lc.leer(DORADO.read_bytes(), 1060.0)
    assert marcas == [1010.5, 1059.0] and penalizado_hasta == 1100.0


@con_fcntl
def test_formato_y_nombre_del_fichero(directorio):
    lc.reservar(directorio, "EMU", f"  {CLAVE}\n", reloj=lambda: 1000.0)
    lc.reservar(directorio, "EMU", CLAVE, reloj=lambda: 1001.0)  # la key se normaliza: el mismo fichero
    fichero = directorio / f"EMU-{hashlib.sha256(CLAVE.encode()).hexdigest()[:16]}.json"
    assert [p.name for p in directorio.iterdir()] == [fichero.name]
    assert json.loads(fichero.read_text()) == {"marcas": [1000.0, 1001.0], "penalizado_hasta": 0.0}
    assert CLAVE not in fichero.read_text()


# --- Reloj y fichero raro ---------------------------------------------------

@con_fcntl
@pytest.mark.parametrize("contenido", [b'{"marcas": [99', b"[1, 2]", b'{"marcas": 3}', b"\xff\xfe"],
                         ids=["cortado", "lista", "marcas-no-lista", "binario"])
def test_un_fichero_ilegible_cuenta_como_vacio(directorio, contenido):
    directorio.mkdir()
    lc.ruta(directorio, "EMU", CLAVE).write_bytes(contenido)
    assert lc.reservar(directorio, "EMU", CLAVE, capacidad=1, reloj=lambda: 1000.0) == 0


@con_fcntl
def test_marcas_viejas_y_futuras_no_cuentan(directorio):
    directorio.mkdir()
    lc.ruta(directorio, "EMU", CLAVE).write_text(json.dumps({"marcas": [939.0, 940.0, 5000.0], "penalizado_hasta": 0}))
    # 939 y 940 ya salieron de la ventana y 5000 es de un reloj que saltó: con capacidad 2 no queda ninguna.
    assert lc.reservar(directorio, "EMU", CLAVE, capacidad=2, reloj=lambda: 1000.0) == 0
    assert json.loads(lc.ruta(directorio, "EMU", CLAVE).read_text())["marcas"] == [1000.0]


@con_fcntl
def test_una_pausa_absurda_no_bloquea(directorio):
    directorio.mkdir()
    lc.ruta(directorio, "EMU", CLAVE).write_text(json.dumps({"marcas": [], "penalizado_hasta": 1000.0 + 86400}))
    assert lc.reservar(directorio, "EMU", CLAVE, reloj=lambda: 1000.0) == 0
    lc.penalizar(directorio, "EMU", CLAVE, 86400, reloj=lambda: 1000.0)  # un Retry-After enorme se acota
    assert lc.reservar(directorio, "EMU", CLAVE, reloj=lambda: 1000.0) == lc.PENALIZACION_MAXIMA


def test_un_tracker_sin_capacidad_no_se_limita(directorio):
    assert lc.reservar(directorio, "BLU", CLAVE) == 0 and lc.penalizar(directorio, "BLU", CLAVE) == 0
    assert not directorio.exists()


@pytest.mark.parametrize("valor, segundos", [("7", 8.0), (" 0 ", 1.0), (None, 60.0), ("", 60.0),
                                             ("Wed, 21 Oct 2015 07:28:00 GMT", 60.0), ("-3", 60.0)])
def test_retry_after(valor, segundos):
    assert lc.segundos_retry_after(valor) == segundos


# --- Varios procesos --------------------------------------------------------

_HIJO_RESERVAS = """
import sys, time
from emuwarez_uploadrr.integrations import limitador_compartido as l
n, cap, ventana = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
d = l.directorio_por_entorno()
print("LISTO", flush=True)
sys.stdin.readline()
for _ in range(n):
    visto = []
    def reloj():
        visto.append(time.time())
        return visto[-1]
    while True:
        falta = l.reservar(d, "EMU", "CLAVE", "subida", capacidad=cap, ventana=ventana, reloj=reloj)
        if falta <= 0:
            break
        time.sleep(min(falta, 0.02))
    print(repr(visto[-1]), flush=True)
"""


@con_fcntl
def test_cuatro_procesos_nunca_pasan_de_la_capacidad_en_ninguna_ventana(tmp_path, hijos):
    capacidad, ventana, por_hijo = 8, 1.0, 12
    grupo = [hijos(_HIJO_RESERVAS, tmp_path, por_hijo, capacidad, ventana) for _ in range(4)]
    for h in grupo:
        assert h.linea() == "LISTO"
    inicio = time.time()
    for h in grupo:
        h.proc.stdin.write("\n")
        h.proc.stdin.flush()
    marcas = [float(h.linea(timeout=30)) for h in grupo for _ in range(por_hijo)]
    assert len(marcas) == 48
    assert _nunca_mas_de(marcas, capacidad, ventana)
    assert time.time() - inicio >= 48 / capacidad - 1 - 0.5  # se han repartido de verdad en ventanas


_HIJO_RECORRIDO = """
import sys, time
from emuwarez_uploadrr.integrations import limitador_compartido as l
d = l.directorio_por_entorno()
apuntadas = 0
while l.reservar(d, "EMU", "CLAVE", "recorrido", capacidad=10, reserva=4) == 0:
    apuntadas += 1
print("LLENO", apuntadas, flush=True)
fin = time.monotonic() + float(sys.argv[1])
while time.monotonic() < fin:  # el recorrido sigue insistiendo
    if l.reservar(d, "EMU", "CLAVE", "recorrido", capacidad=10, reserva=4) == 0:
        apuntadas += 1
    time.sleep(0.005)
print("FIN", apuntadas, flush=True)
"""


@con_fcntl
def test_la_subida_tiene_hueco_aunque_un_recorrido_llene_el_suyo(tmp_path, hijos, directorio):
    recorrido = hijos(_HIJO_RECORRIDO, tmp_path, 1.0)
    assert recorrido.linea() == "LLENO 6"
    for _ in range(4):  # la reserva, mientras el otro proceso sigue pidiendo
        assert lc.reservar(directorio, "EMU", "CLAVE", "subida", capacidad=10, reserva=4) == 0
    assert lc.reservar(directorio, "EMU", "CLAVE", "subida", capacidad=10, reserva=4) > 0
    assert lc.reservar(directorio, "EMU", "CLAVE", "recorrido", capacidad=10, reserva=4) > 0
    assert recorrido.linea() == "FIN 6"


_HIJO_429 = """
import sys
from emuwarez_uploadrr.integrations import limitador_compartido as l
l.apuntar_429("EMU", "CLAVE", sys.argv[1] or None)
print("PENALIZADO", flush=True)
"""


@con_fcntl
@pytest.mark.parametrize("retry_after, segundos", [("3", 4.0), ("", lc.ESPERA_429)], ids=["retry-after", "sin-cabecera"])
def test_un_429_en_otro_proceso_pausa_a_este(tmp_path, hijos, directorio, retry_after, segundos):
    assert hijos(_HIJO_429, tmp_path, retry_after).linea() == "PENALIZADO"
    falta = lc.reservar(directorio, "EMU", "CLAVE")
    assert segundos - 1 < falta <= segundos
    # La app, que no hizo nada, también espera: una consulta interactiva corta enseguida.
    lim = Limitador(directorio=lambda: directorio, espera_maxima_interactiva=1)
    with pytest.raises(IntegrationError, match="límite de peticiones"):
        lim.esperar_turno("CLAVE", interactiva=True)


_HIJO_A_MEDIAS = """
import os, sys
from emuwarez_uploadrr.integrations import limitador_compartido as l
with l._cerrojo(l.directorio_por_entorno(), "EMU", "CLAVE") as fd:
    os.ftruncate(fd, 0)
    os.pwrite(fd, b'{"marcas": [17', 0)
    print("ESCRIBIENDO", flush=True)
    sys.stdin.readline()
"""


@con_fcntl
def test_kill_9_a_mitad_de_escritura(tmp_path, hijos, directorio, monkeypatch):
    hijo = hijos(_HIJO_A_MEDIAS, tmp_path)
    assert hijo.linea() == "ESCRIBIENDO"
    monkeypatch.setattr(lc, "_ESPERA_CERROJO", 0.1)
    with pytest.raises(lc.NoDisponible, match="ocupado"):  # mientras lo tiene, nadie se queda colgado
        lc.reservar(directorio, "EMU", "CLAVE")
    hijo.matar()
    assert lc.reservar(directorio, "EMU", "CLAVE", reloj=lambda: 1000.0) == 0  # cerrojo suelto, JSON roto = vacío
    assert json.loads(lc.ruta(directorio, "EMU", "CLAVE").read_text())["marcas"] == [1000.0]


# --- Falla abierto ----------------------------------------------------------

def test_directorio_inservible_la_app_cuenta_en_memoria_y_avisa_una_vez(tmp_path, caplog):
    fichero = tmp_path / "no-es-un-directorio"
    fichero.write_text("x")
    with pytest.raises(lc.NoDisponible):
        lc.reservar(fichero, "EMU", CLAVE)
    caplog.set_level(logging.WARNING, logger="emuwarez.emu")
    lim = Limitador(directorio=lambda: fichero, capacidad=2, reserva_interactiva=0, espera_maxima_interactiva=1)
    lim.esperar_turno(CLAVE, interactiva=True)
    lim.esperar_turno(CLAVE, interactiva=True)
    with pytest.raises(IntegrationError):  # el límite sigue valiendo dentro del proceso
        lim.esperar_turno(CLAVE, interactiva=True)
    assert caplog.text.count("límite de peticiones compartido") == 1


def test_sin_fcntl_no_hay_fichero(monkeypatch, directorio):
    monkeypatch.setattr(lc, "fcntl", None)
    with pytest.raises(lc.NoDisponible):
        lc.reservar(directorio, "EMU", CLAVE)
    lim = Limitador(directorio=lambda: directorio)
    assert lim.esperar_turno(CLAVE) == 0
    lim.penalizar(CLAVE, 60)
    with pytest.raises(IntegrationError, match="prueba dentro de 60 s"):
        lim.esperar_turno(CLAVE, interactiva=True)


# --- La app -----------------------------------------------------------------

def test_el_limitador_de_la_aplicacion_usa_el_fichero_compartido_y_el_reloj_de_pared(monkeypatch, tmp_path):
    monkeypatch.undo()  # el de conftest es uno en memoria con reloj falso
    assert emu_limitador.LIMITADOR._directorio is emu_limitador.directorio_compartido
    assert inspect.signature(Limitador).parameters["reloj"].default is time.time
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("LIMITADOR_DIR", raising=False)
    assert emu_limitador.directorio_compartido() == tmp_path / "limitador"
    monkeypatch.setenv("LIMITADOR_DIR", str(tmp_path / "otro"))
    assert emu_limitador.directorio_compartido() == tmp_path / "otro"


@con_fcntl
def test_las_peticiones_de_la_app_quedan_en_el_fichero(directorio):
    lim = Limitador(directorio=lambda: directorio)
    lim.esperar_turno(CLAVE)
    lim.penalizar(CLAVE, 30)
    texto = lc.ruta(directorio, "EMU", CLAVE).read_text()
    datos = json.loads(texto)
    assert len(datos["marcas"]) == 1 and datos["penalizado_hasta"] > time.time() + 25
    assert CLAVE not in texto and lim._claves == {}  # nada en memoria


def test_el_429_de_emu_respeta_retry_after(http_falso, limitador_emuwarez):
    from tests.http_falso import respuesta

    http_falso.responde(respuesta(429, headers={"Retry-After": "7"}))
    with pytest.raises(IntegrationError):
        emu.emu_get_user(CLAVE)
    estado = limitador_emuwarez._claves[lc.huella(CLAVE)]
    assert estado.penalizado_hasta - limitador_emuwarez.reloj.ahora == pytest.approx(8.0)


def test_la_prueba_de_conexion_respeta_retry_after(http_falso, limitador_emuwarez):
    from emuwarez_uploadrr.services.integration_tests import test_emuwarez_connection
    from tests.http_falso import respuesta

    http_falso.responde(respuesta(429, headers={"Retry-After": "20"}))
    assert test_emuwarez_connection(api_key=CLAVE, base_url="https://t.x")["ok"] is False
    estado = limitador_emuwarez._claves[lc.huella(CLAVE)]
    assert estado.penalizado_hasta - limitador_emuwarez.reloj.ahora == pytest.approx(21.0)


def test_uploadrr_recibe_el_directorio_del_limitador(monkeypatch, tmp_path):
    from emuwarez_uploadrr.services.upload_execution import ejecucion, procesos

    monkeypatch.delenv("LIMITADOR_DIR", raising=False)
    lanzados = []

    class Proceso:
        def __init__(self, argv, **kw):
            lanzados.append(kw["env"])

    monkeypatch.setattr(ejecucion.subprocess, "Popen", Proceso)
    monkeypatch.setattr(procesos, "_apagando", False)
    try:
        ejecucion._lanzar_uploadrr(987654, ["python", "upload.py"], str(tmp_path))
    finally:
        ejecucion._PROCESOS.pop(987654, None)
    assert lanzados[0]["LIMITADOR_DIR"] == str(tmp_path / "limitador")  # CONFIG_DIR = tmp_path en los tests
