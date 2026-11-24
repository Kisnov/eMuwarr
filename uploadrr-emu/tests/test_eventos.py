"""El canal --eventos de una subida entera de EMU: tests/golden/*.jsonl, el contrato y los caminos que no son una subida buena.

Regenerar los .jsonl: igual que los .txt (ver test_transcripcion_dorada.py)."""

import io
import json
import os
import shutil
import sys
from types import SimpleNamespace

import pytest
import qbittorrentapi
import torf

import upload
from contrato_eventos import validar, validar_linea_final
from src.trackers.EMU import EMU
from src.trackers.NOBS import NOBS
from flujo_emu import (
    EPISODIO,
    GOLDEN,
    NOMBRE_EPISODIO,
    NOMBRE_PELICULA,
    PELICULA,
    SEARCH,
    TOKEN,
    UPLOAD,
    Flujo,
    FakeTracker,
    Qbit,
    comparar_con_golden,
)
from test_transcripcion_dorada import ARGV_EPISODIO, ARGV_PELICULA

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="las rutas y los saltos de línea de Rich son los de Linux")


@pytest.fixture
def flujo(monkeypatch, capsys):
    tracker = FakeTracker()
    flujo = Flujo(monkeypatch, tracker, capsys)
    yield flujo
    flujo.limpiar()
    tracker.server.shutdown()


def _correr(flujo, ruta, argv):
    return flujo.correr([str(ruta), *argv, "--eventos", str(flujo.ruta_eventos)])


def _revisar(flujo):
    eventos = flujo.eventos()
    for evento in eventos:
        validar(evento)
    validar_linea_final(eventos)
    return eventos


def _codigos(eventos, ev):
    return [e for e in eventos if e["ev"] == ev]


def _resultado(eventos):
    return eventos[-1]["codigo"], eventos[-1]["ok"]


def _texto_dorado(nombre):
    return (GOLDEN / nombre).read_text(encoding="utf-8")


# Las tres subidas doradas: el texto es el mismo con y sin --eventos


@pytest.mark.parametrize(
    "video, argv, nombre",
    [(PELICULA, ARGV_PELICULA, "subida_emu_pelicula"), (EPISODIO, ARGV_EPISODIO, "subida_emu_episodio")],
)
def test_subida_buena_con_eventos(flujo, video, argv, nombre):
    flujo.tracker_que_acepta()

    codigo, salida = _correr(flujo, flujo.crear_video(video), argv)

    assert codigo == 0
    assert flujo.normalizar(salida) == _texto_dorado(nombre + ".txt")
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("subido", True)
    assert [e["ev"] for e in eventos][:2] == ["inicio", "fase"]
    assert eventos[0]["modo"] == "subida" and eventos[0]["reanuda"] is False
    assert [e["paso"] for e in _codigos(eventos, "paso_hecho")] == ["screenshots", "torrent"]
    assert [(e["torrent_id"], e["url"]) for e in _codigos(eventos, "subido")] == [(42, f"{flujo.tracker.url}/torrents/42")]
    assert _codigos(eventos, "cliente")[0]["ok"] is True
    # meta.json guarda la subida antes de anunciarla
    meta = json.loads((flujo.base / "tmp" / f"{video}.mkv" / "meta.json").read_text(encoding="utf-8"))
    assert meta["subido"] == {"id": 42, "url": f"{flujo.tracker.url}/torrents/42"}
    assert "eventos" not in meta
    comparar_con_golden(nombre + ".jsonl", flujo.eventos_normalizados())


def test_duplicado_con_eventos(flujo):
    ruta = flujo.crear_video(PELICULA)
    flujo.tracker_que_acepta(duplicados=[(NOMBRE_PELICULA, ruta.stat().st_size)])

    codigo, salida = _correr(flujo, ruta, ARGV_PELICULA)

    assert codigo == 1
    assert flujo.normalizar(salida) == _texto_dorado("subida_emu_duplicado.txt")
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("duplicado", False)
    assert not _codigos(eventos, "subido")
    comparar_con_golden("subida_emu_duplicado.jsonl", flujo.eventos_normalizados())


# Caminos que los goldens no recorren


def test_el_tracker_rechaza_la_subida(flujo):
    flujo.tracker_que_acepta()
    flujo.tracker.routes[("POST", UPLOAD)] = lambda q: (200, {"success": False, "message": "No"})

    codigo, _ = _correr(flujo, flujo.crear_video(PELICULA), ARGV_PELICULA)

    assert codigo == 1
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("rechazado", False)
    ultima = _codigos(eventos, "fase")[-1]
    assert (ultima["clave"], ultima["estado"]) == ("subida", "fallida")
    assert not _codigos(eventos, "subido") and not Qbit.anadidos


def test_la_busqueda_de_duplicados_cae(flujo):
    flujo.tracker_que_acepta()
    flujo.tracker.routes[("GET", SEARCH)] = lambda q: (500, b"error")

    codigo, _ = _correr(flujo, flujo.crear_video(PELICULA), ARGV_PELICULA)

    assert codigo == 1
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("busqueda_caida", False)
    assert flujo.tracker.calls("POST", UPLOAD) == []


def test_un_tipo_sin_id_en_emu_no_se_sube_con_el_tipo_0(flujo, monkeypatch):
    flujo.tracker_que_acepta()
    monkeypatch.setattr(EMU, "type_ids", {k: v for k, v in EMU.type_ids.items() if k != "WEBDL"})

    codigo, salida = _correr(flujo, flujo.crear_video(PELICULA), ARGV_PELICULA)

    assert codigo == 1
    assert "Unknown type on EMU: WEBDL" in salida  # el título de su panel en el resumen
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("tipo_desconocido", False)
    assert eventos[-1]["detalle"] == "Unknown type on EMU: WEBDL"
    assert flujo.tracker.calls("GET", SEARCH) == [] and flujo.tracker.calls("POST", UPLOAD) == []


def test_login_de_qbittorrent_fallido_no_deshace_la_subida(flujo, monkeypatch):
    flujo.tracker_que_acepta()

    def login():
        raise qbittorrentapi.LoginFailed()

    monkeypatch.setattr(Qbit, "auth_log_in", lambda self: login())

    codigo, salida = _correr(flujo, flujo.crear_video(PELICULA), ARGV_PELICULA)

    assert codigo == 0 and "INCORRECT QBIT LOGIN CREDENTIALS" in salida
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("subido", True)
    cliente = _codigos(eventos, "cliente")
    assert len(cliente) == 1 and (cliente[0]["ok"], cliente[0]["motivo"], cliente[0]["cliente"]) == (False, "login", "qbit")


def test_una_excepcion_sin_controlar_es_el_resultado(flujo, monkeypatch):
    flujo.tracker_que_acepta()

    async def falla(*_a, **_k):
        raise RuntimeError("clave-secreta")

    monkeypatch.setattr(upload, "handle_torrent_creation", falla)

    with pytest.raises(RuntimeError):
        _correr(flujo, flujo.crear_video(PELICULA), ARGV_PELICULA)

    eventos = _revisar(flujo)
    assert eventos[-1]["codigo"] == "excepcion" and eventos[-1]["detalle"] == "RuntimeError"
    assert "clave-secreta" not in flujo.ruta_eventos.read_text(encoding="utf-8")


def test_sin_video_da_su_resultado(flujo):
    ruta = flujo.contenido / "vacia"
    ruta.mkdir()
    flujo.tracker_que_acepta()

    codigo, _ = _correr(flujo, ruta, ARGV_PELICULA)

    assert codigo == 1
    assert _resultado(_revisar(flujo)) == ("sin_video", False)


def test_una_ruta_de_eventos_que_no_se_puede_escribir_no_corta_la_subida(flujo, capsys):
    flujo.tracker_que_acepta()
    imposible = flujo.raiz / "no-existe" / "eventos.jsonl"

    codigo, salida = flujo.correr([str(flujo.crear_video(PELICULA)), *ARGV_PELICULA, "--eventos", str(imposible)])

    assert codigo == 0 and "Torrent URL:" in salida
    assert salida.count("No se pueden escribir los eventos") == 1
    assert not imposible.exists()


def test_sin_la_opcion_no_se_escribe_nada(flujo):
    flujo.tracker_que_acepta()

    codigo, salida = flujo.correr([str(flujo.crear_video(PELICULA)), *ARGV_PELICULA])

    assert codigo == 0 and not flujo.ruta_eventos.exists()
    assert flujo.normalizar(salida) == _texto_dorado("subida_emu_pelicula.txt")


def test_argumentos_invalidos_ya_tienen_resultado(flujo):
    codigo, _ = flujo.correr(["--eventos", str(flujo.ruta_eventos)])  # sin ninguna ruta que subir

    assert codigo == 1
    assert _resultado(_revisar(flujo)) == ("sin_rutas", False)


# --config: el config de esta subida en vez de data/config.yaml


def _solo_con_config_propio(flujo, monkeypatch):
    """Mueve el config de la prueba fuera de data/: si se leyera el de siempre, no habría ninguno."""
    propio = flujo.raiz / "config-de-la-subida.yaml"
    preparar = flujo.preparar

    def preparar_y_mover():
        preparar()
        (flujo.base / "config.yaml").replace(propio)
        propio.write_text(propio.read_text(encoding="utf-8").replace(TOKEN, "clave-de-esta-subida"), encoding="utf-8")

    monkeypatch.setattr(flujo, "preparar", preparar_y_mover)
    return propio


def test_con_config_lee_ese_fichero_y_la_salida_no_cambia(flujo, monkeypatch):
    propio = _solo_con_config_propio(flujo, monkeypatch)
    flujo.tracker_que_acepta()

    codigo, salida = _correr(flujo, flujo.crear_video(PELICULA), [*ARGV_PELICULA, "--config", str(propio)])

    assert codigo == 0
    assert flujo.normalizar(salida) == _texto_dorado("subida_emu_pelicula.txt")
    assert _resultado(_revisar(flujo)) == ("subido", True)
    peticiones = zip(flujo.tracker.requests, flujo.tracker.autorizaciones)
    assert [auth for (metodo, ruta, _), auth in peticiones if (metodo, ruta) == ("POST", UPLOAD)] == [
        "Bearer clave-de-esta-subida"]
    meta = json.loads((flujo.base / "tmp" / f"{PELICULA}.mkv" / "meta.json").read_text(encoding="utf-8"))
    assert "config" not in meta  # la ruta es de esta ejecución: un --resume trae la suya


def test_un_config_que_no_existe_es_sin_config(flujo, monkeypatch):
    propio = _solo_con_config_propio(flujo, monkeypatch)

    codigo, _ = _correr(flujo, flujo.crear_video(PELICULA), [*ARGV_PELICULA, "--config=" + str(propio) + ".no"])

    assert codigo == 1
    assert _resultado(_revisar(flujo)) == ("sin_config", False)


# La misma subida a otro tracker: un segundo Uploadrr-EMU con -tk NOBS y --resume sobre el mismo fichero


def test_la_subida_a_nobs_tras_la_de_emu_reutiliza_capturas_y_torrent(flujo, monkeypatch):
    """Lo que hace la app con la subida a N.O.B.S de algo que acaba de subir a eMuwarez: el mismo fichero, --resume para
    no borrar el tmp/<nombre> que dejó la de eMuwarez, y el BASE.torrent de entonces. Mismas piezas: se siembran juntos."""
    flujo.config["TRACKERS"]["NOBS"] = {"api_key": TOKEN, "anon": False}
    monkeypatch.setattr(NOBS, "domain", flujo.tracker.url)
    ruta = flujo.crear_video(PELICULA)
    flujo.tracker_que_acepta()
    assert _correr(flujo, ruta, ARGV_PELICULA)[0] == 0
    base = flujo.base / "tmp" / f"{PELICULA}.mkv" / "BASE.torrent"
    hecho = base.stat().st_mtime_ns
    flujo.ruta_eventos.unlink()  # la app lo vacía antes de cada lanzamiento

    codigo, salida = _correr(flujo, ruta, ["-tk", "NOBS", *ARGV_PELICULA[2:], "--resume"])

    assert codigo == 0, salida
    assert "Step done: screenshots" in salida and "Step done: torrent" in salida
    assert base.stat().st_mtime_ns == hecho  # no se ha vuelto a hashear
    eventos = _revisar(flujo)
    assert eventos[0]["reanuda"] is True and _resultado(eventos) == ("subido", True)
    (emu, torrent_emu), (nobs, torrent_nobs) = flujo.tracker.bodies
    assert nobs["name"] == emu["name"] == NOMBRE_PELICULA
    assert (nobs["category_id"], nobs["type_id"], nobs["resolution_id"]) == ("1", "4", "3")  # los de NOBS.json

    def piezas(contenido):
        return torf.Torrent.read_stream(io.BytesIO(contenido), validate=False).metainfo["info"]["pieces"]

    assert piezas(torrent_nobs["torrent"]) == piezas(torrent_emu["torrent"])
    # El de N.O.B.S también va a qBittorrent (cada ejecución empieza con la lista del falso vacía).
    assert len(Qbit.anadidos) == 1 and _codigos(eventos, "cliente")[0]["ok"] is True


def test_la_url_de_los_ajustes_gana_a_la_del_tracker(flujo):
    """`TRACKERS.<X>.url` (la URL de ese tracker en Configuración de la app): se busca, se sube y se enlaza ahí."""
    flujo.config["TRACKERS"]["NOBS"] = {"api_key": TOKEN, "anon": False, "url": flujo.tracker.url + "/"}
    flujo.tracker_que_acepta()

    codigo, salida = _correr(flujo, flujo.crear_video(PELICULA), ["-tk", "NOBS", *ARGV_PELICULA[2:]])

    assert codigo == 0, salida
    assert NOBS.domain == "https://nobs.rawsmoke.net"  # la de la clase no cambia
    assert len(flujo.tracker.bodies) == 1
    assert f"Torrent URL: {flujo.tracker.url}/torrents/42" in salida


def test_la_url_de_emuwarez_no_se_cambia_desde_el_config(flujo):
    flujo.config["TRACKERS"]["EMU"] = {**flujo.config["TRACKERS"].get("EMU", {}), "url": "https://otro.example"}
    assert EMU(flujo.config).domain == EMU.domain.rstrip("/")


# --base-desde: la subida a N.O.B.S de algo que ya está en eMuwarez, cuando el tmp/<nombre> de entonces ya no está


def _info(contenido: bytes) -> dict:
    return torf.Torrent.read_stream(io.BytesIO(contenido), validate=False).metainfo["info"]


def _ya_en_emu(flujo, monkeypatch, argv=ARGV_PELICULA):
    """Una subida a eMuwarez (o al tracker de `argv`) hecha (id 42) cuyo tmp/<nombre> borró el mantenimiento. Devuelve
    (ruta, .torrent de ese tracker)."""
    flujo.config["TRACKERS"]["NOBS"] = {"api_key": TOKEN, "anon": False}
    monkeypatch.setattr(NOBS, "domain", flujo.tracker.url)
    ruta = flujo.crear_video(PELICULA)
    flujo.tracker_que_acepta()
    assert _correr(flujo, ruta, argv)[0] == 0
    firmado = flujo.tracker.routes[("GET", "/torrent/download/42.rsskey")](None)[1]  # el que da eMuwarez (source eMuwarez)
    atributos = {"name": NOMBRE_PELICULA, "download_link": f"{flujo.tracker.url}/torrent/download/42.rsskey"}
    flujo.tracker.routes[("GET", "/api/torrents/42")] = lambda q: (200, {"data": {"attributes": atributos}})
    os.chdir(flujo.base)  # las capturas dejan el directorio de trabajo dentro de tmp/<nombre>
    shutil.rmtree(flujo.base / "tmp" / f"{PELICULA}.mkv")
    flujo.ruta_eventos.unlink()
    flujo.tracker.bodies.clear()
    return ruta, firmado


def _sin_hashear(monkeypatch):
    async def hashear(*_a, **_k):
        raise AssertionError("se ha vuelto a hashear")
    monkeypatch.setattr("src.utils.torrentman.TorrentMaker.create", hashear)


ARGV_NOBS_BASE = ["-tk", "NOBS", *ARGV_PELICULA[2:], "--resume", "--base-desde", "EMU:42"]


def test_base_desde_hace_el_base_torrent_con_el_de_emu_y_no_hashea(flujo, monkeypatch):
    ruta, firmado = _ya_en_emu(flujo, monkeypatch)
    _sin_hashear(monkeypatch)

    codigo, salida = _correr(flujo, ruta, ARGV_NOBS_BASE)

    assert codigo == 0, salida
    assert "Step done: torrent" in salida
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("subido", True)
    emu = _info(firmado)
    base = _info((flujo.base / "tmp" / f"{PELICULA}.mkv" / "BASE.torrent").read_bytes())
    (nobs, torrent_nobs), = flujo.tracker.bodies
    subido = _info(torrent_nobs["torrent"])
    for info in (base, subido):
        assert info["name"] == emu["name"] == f"{PELICULA}.mkv"
        assert info.get("files") == emu.get("files") and info.get("length") == emu["length"]
        assert info["pieces"] == emu["pieces"] and info["piece length"] == emu["piece length"]
    assert base["source"] == "Uploadrr" and subido["source"] != emu["source"]  # otro info-hash: dos torrents en qBittorrent
    assert nobs["name"] == NOMBRE_PELICULA
    # El de eMuwarez no se queda en el tmp ni va a qBittorrent: solo el de N.O.B.S.
    guardados = sorted(p.name for p in (flujo.base / "tmp" / f"{PELICULA}.mkv").glob("[[]*.torrent"))
    assert guardados and all(n.startswith("[NOBS]") for n in guardados)
    assert len(Qbit.anadidos) == 1


def test_base_desde_tambien_de_nobs_a_emu(flujo, monkeypatch):
    """Al revés: lo que ya está en N.O.B.S se sube a eMuwarez con el torrent de N.O.B.S (su adaptador y su clave)."""
    ruta, firmado = _ya_en_emu(flujo, monkeypatch, ["-tk", "NOBS", *ARGV_PELICULA[2:]])
    _sin_hashear(monkeypatch)

    codigo, salida = _correr(flujo, ruta, [*ARGV_PELICULA, "--resume", "--base-desde", "NOBS:42"])

    assert codigo == 0, salida
    assert _resultado(_revisar(flujo)) == ("subido", True)
    nobs = _info(firmado)
    base = _info((flujo.base / "tmp" / f"{PELICULA}.mkv" / "BASE.torrent").read_bytes())
    (_emu, torrent_emu), = flujo.tracker.bodies
    for info in (base, _info(torrent_emu["torrent"])):
        assert info["pieces"] == nobs["pieces"] and info["piece length"] == nobs["piece length"]
    guardados = sorted(p.name for p in (flujo.base / "tmp" / f"{PELICULA}.mkv").glob("[[]*.torrent"))
    assert guardados and all(n.startswith("[EMU]") for n in guardados)


def test_base_desde_con_otro_fichero_falla_sin_subir_nada(flujo, monkeypatch):
    ruta, _firmado = _ya_en_emu(flujo, monkeypatch)
    otro = torf.Torrent(path=ruta, private=True)
    otro.generate()
    ruta.write_bytes(ruta.read_bytes() + b"otro")  # el fichero ya no es el que tiene eMuwarez
    flujo.tracker.routes[("GET", "/torrent/download/42.rsskey")] = lambda q: (200, otro.dump(validate=False))
    _sin_hashear(monkeypatch)

    codigo, salida = _correr(flujo, ruta, ARGV_NOBS_BASE)

    assert codigo == 1, salida
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("base_no_coincide", False)
    assert f"{PELICULA}.mkv" in eventos[-1]["detalle"]
    assert flujo.tracker.bodies == [] and not Qbit.anadidos
    assert not (flujo.base / "tmp" / f"{PELICULA}.mkv" / "BASE.torrent").exists()
    assert [e for e in eventos if e["ev"] == "paso_hecho" and e["paso"] == "torrent"] == []


def test_base_desde_sin_el_torrent_en_emu_falla(flujo, monkeypatch):
    ruta, _firmado = _ya_en_emu(flujo, monkeypatch)
    flujo.tracker.routes[("GET", "/api/torrents/42")] = lambda q: (404, b"")

    codigo, _ = _correr(flujo, ruta, ARGV_NOBS_BASE)

    assert codigo == 1
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("base_sin_torrent", False)
    assert "moderación" in eventos[-1]["detalle"]
    assert flujo.tracker.bodies == []


# --solo-cliente de punta a punta


def _solo_cliente(flujo, monkeypatch, enlace_ok=True, sin_enlace=(404, b""), argv=ARGV_PELICULA):
    ruta = flujo.crear_video(PELICULA)
    flujo.tracker_que_acepta()
    torrent = torf.Torrent(path=ruta, private=True)
    torrent.generate()
    torrent.source = "eMuwarez"
    firmado = torrent.dump(validate=False)
    descarga = f"{flujo.tracker.url}/torrent/download/42.rsskey"
    atributos = {"name": NOMBRE_PELICULA, "download_link": descarga}
    flujo.tracker.routes[("GET", "/api/torrents/42")] = lambda q: (200, {"data": {"attributes": atributos}}) if enlace_ok else sin_enlace
    flujo.tracker.routes[("GET", "/torrent/download/42.rsskey")] = lambda q: (200, firmado)

    monkeypatch.setattr("src.clients.ESPERA_INICIO_COMPROBACION", 0)  # el qBittorrent falso nunca «comprueba»
    return _correr(flujo, ruta, [*argv, "-sc", "42"])


def test_solo_cliente_bueno(flujo, monkeypatch):
    codigo, _ = _solo_cliente(flujo, monkeypatch)

    assert codigo == 0
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("cliente_ok", True)
    assert eventos[0]["modo"] == "solo_cliente"
    assert [e["ev"] for e in eventos if e["ev"] in ("subido", "paso_hecho")] == []
    cliente = _codigos(eventos, "cliente")
    assert len(cliente) == 1 and cliente[0]["ok"] is True and cliente[0]["cliente"] == "qbit"


def test_solo_cliente_con_otro_tracker_baja_el_torrent_de_ese(flujo, monkeypatch):
    """«Reenviar a qBittorrent» de una subida a N.O.B.S: el .torrent sale de N.O.B.S, no de eMuwarez."""
    flujo.config["TRACKERS"]["NOBS"] = {"api_key": TOKEN, "anon": False}
    monkeypatch.setattr(NOBS, "domain", flujo.tracker.url)
    argv = ["-tk", "NOBS", *ARGV_PELICULA[2:]]

    codigo, salida = _solo_cliente(flujo, monkeypatch, argv=argv)

    assert codigo == 0, salida
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("cliente_ok", True)
    assert [ruta for metodo, ruta, _ in flujo.tracker.requests if metodo == "GET"][:1] == ["/api/torrents/42"]
    guardados = [p.name for p in (flujo.base / "tmp" / f"{PELICULA}.mkv").glob("*.torrent")]
    assert guardados and all(nombre.startswith("[NOBS]") for nombre in guardados)
    assert len(Qbit.anadidos) == 1


def test_solo_cliente_con_dos_trackers_no_sabe_de_cual_es_el_id(flujo, monkeypatch):
    flujo.config["TRACKERS"]["NOBS"] = {"api_key": TOKEN, "anon": False}
    argv = ["-tk", "EMU,NOBS", *ARGV_PELICULA[2:]]

    codigo, _ = _solo_cliente(flujo, monkeypatch, argv=argv)

    assert codigo == 1
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("cliente_fallido", False)
    assert _codigos(eventos, "cliente")[0]["motivo"] == "sin_emu"
    assert not Qbit.anadidos


@pytest.mark.parametrize("sin_enlace, motivo", [
    ((404, b""), "no_visible"),  # la API aún no lo da: en eMuwarez, pendiente de moderación
    ((200, {"data": {"attributes": {}}}), "sin_torrent"),
], ids=["404", "sin-enlace"])
def test_solo_cliente_sin_torrent_que_bajar(flujo, monkeypatch, sin_enlace, motivo):
    codigo, _ = _solo_cliente(flujo, monkeypatch, enlace_ok=False, sin_enlace=sin_enlace)

    assert codigo == 1
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("cliente_fallido", False)
    assert _codigos(eventos, "cliente")[0]["motivo"] == motivo
    assert not Qbit.anadidos


def test_solo_cliente_que_qbittorrent_rechaza(flujo, monkeypatch):
    monkeypatch.setattr(Qbit, "torrents_add", lambda self, **kw: "Fails.")

    codigo, _ = _solo_cliente(flujo, monkeypatch)

    assert codigo == 1
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("cliente_fallido", False)
    assert _codigos(eventos, "cliente")[0]["motivo"] == "no_aceptado"


def test_el_aviso_de_pack_incompleto(monkeypatch, tmp_path):
    import asyncio

    from src.utils import eventos
    from src.utils.torrentman import TorrentMaker

    salida = "incomplete season pack\nSeason number: 3\nMissing: 4, 7"

    class Proceso:
        returncode = 0
        stdout = SimpleNamespace(read=None)

        async def wait(self):
            return 0

    datos = [salida.encode(), b""]

    async def leer(_n):
        return datos.pop(0)

    Proceso.stdout = SimpleNamespace(read=leer)

    async def crear(*_a, **_k):
        return Proceso()

    monkeypatch.setattr("src.utils.torrentman.asyncio.create_subprocess_exec", crear)
    monkeypatch.setattr("src.utils.torrentman.ProgressManager.get_instance", staticmethod(lambda: SimpleNamespace(update=lambda *_a, **_k: None)))
    fichero = tmp_path / "e.jsonl"
    eventos.configurar(str(fichero))
    try:
        asyncio.run(TorrentMaker({})._run_mkbrr(["mkbrr"]))
    finally:
        eventos.configurar(None)

    (linea,) = [json.loads(x) for x in fichero.read_text(encoding="utf-8").splitlines() if '"aviso"' in x]
    validar(linea)
    assert linea["datos"] == {"temporada": 3, "faltan": [4, 7]}


def test_una_ruta_relativa_no_se_parte_al_cambiar_de_directorio(tmp_path, monkeypatch):
    from src.utils import eventos

    (tmp_path / "otra").mkdir()
    monkeypatch.chdir(tmp_path)
    eventos.configurar("e.jsonl")
    try:
        eventos.inicio("x", "subida", False)
        os.chdir(tmp_path / "otra")  # como hace mediainfo a mitad de la ejecución
        eventos.resultado(False, "excepcion")
    finally:
        eventos.configurar(None)
    assert len((tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert not (tmp_path / "otra" / "e.jsonl").exists()
