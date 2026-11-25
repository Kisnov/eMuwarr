"""eMuwarez API connector.

eMuwarez runs on the UNIT3D platform (https://emu-warez.com). Every request goes out through `cliente`
(cliente_unit3d, the same client Uploadrr-EMU uses), which also puts the key in the request: `Authorization: Bearer`,
or `?api_token=` once if the tracker rejects that.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable, Optional

from . import cliente_unit3d, emu_limitador
from .base import IntegrationError, normalize_base_url, request_json
from .emu_limitador import EMU_ESPERA_429
from .limitador_compartido import segundos_retry_after
from .. import __version__
from ..constants import DEFAULT_UPLOADRR_PATH, EMU_BASE_URL
from ..models import RequestItem

logger = logging.getLogger("emuwarez.emu")

# La tabla de ids de cada tracker es la de Uploadrr-EMU: la de la imagen o, en el repo, la de uploadrr-emu/.
def _ruta_ids(tracker: str) -> Path:
    candidatas = [Path(c) / "data" / "trackers" / f"{tracker}.json"
                  for c in (DEFAULT_UPLOADRR_PATH, Path(__file__).resolve().parents[2] / "uploadrr-emu")]
    return next((r for r in candidatas if r.is_file()), candidatas[0])


RUTA_IDS_EMU = _ruta_ids("EMU")


def _token(t: str) -> str:
    """Como los compara Uploadrr-EMU (src/trackers/unit3d_tabla.py)."""
    return t.upper().replace("-", "")


def _cargar_etiquetas(ruta: Path, nombre: str = "eMuwarez") -> Optional[dict[str, Any]]:
    """id -> etiqueta por sección (las entradas sin `label` no se enseñan); en `subibles`, las categorías con token, y
    en `tokens_de_tipo`, los `-t` que tiene. None y un aviso si no se puede leer."""
    try:
        tabla = json.loads(ruta.read_text(encoding="utf-8"))
        etiquetas = {s: {e["id"]: e["label"] for e in tabla[s] if "label" in e}
                     for s in ("categories", "types", "resolutions")}
        return {**etiquetas, "subibles": {e["id"]: e.get("label") for e in tabla["categories"] if e.get("tokens")},
                "tokens_de_tipo": {_token(t) for e in tabla["types"] for t in e.get("tokens", ())}}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("No se puede leer la tabla de ids de %s (%s): las peticiones llevarán el id, no el nombre",
                       nombre, exc)
        return None


_ETIQUETAS = _cargar_etiquetas(RUTA_IDS_EMU)
# El filtro de categorías de la API sale de EMU_CATEGORIAS.
EMU_CATEGORIAS = (_ETIQUETAS or {}).get("categories", {})
EMU_TIPOS = (_ETIQUETAS or {}).get("types", {})
EMU_RESOLUCIONES = (_ETIQUETAS or {}).get("resolutions", {})
# Las de los otros UNIT3D (N.O.B.S, Milnueve), por su nombre en Uploadrr-EMU.
_ETIQUETAS_OTROS = {t: _cargar_etiquetas(_ruta_ids(t), t) for t in ("NOBS", "MNV")}


def _etiquetas_de(tracker: str) -> Optional[dict[str, Any]]:
    return _ETIQUETAS if tracker == "EMU" else _ETIQUETAS_OTROS[tracker]


def tiene_tipo(tracker: str, tipo: str) -> bool:
    """Si `tracker` tiene el tipo `tipo` (el `-t` de Uploadrr-EMU); sin su tabla no se sabe, y no se dice que no."""
    etiquetas = _etiquetas_de(tracker)
    return etiquetas is None or _token(tipo) in etiquetas["tokens_de_tipo"]


def _categorias_de_peticiones(tracker: str) -> list[int]:
    """Las categorías cuyas peticiones se leen: en eMuwarez las que tienen nombre; en otro, las de vídeo a las que se
    sube (las que tienen token: fuera libros, juegos o música)."""
    if tracker == "EMU":
        return list(EMU_CATEGORIAS)
    # Sin su tabla no se sabe cuáles son de vídeo: sin filtro llegarían también libros, juegos y música.
    if _etiquetas_de(tracker) is None:
        raise IntegrationError(f"No se puede leer la tabla de ids de {tracker}: no se leen sus peticiones")
    return list(_etiquetas_de(tracker)["subibles"])


def _etiqueta(etiquetas: Optional[dict], seccion: str, nombre: str, valor: Any) -> Optional[str]:
    """Sin tabla, «Categoría 4» en vez de nada."""
    n = _entero(valor)
    if n is None:
        return None
    return etiquetas[seccion].get(n) if etiquetas is not None else f"{nombre} {n}"


# ---------------------------------------------------------------------------
# UNIT3D-pattern functions
# ---------------------------------------------------------------------------

USER_AGENT = cliente_unit3d.user_agent(__version__)


def _enviar(metodo: str, url: str, cabeceras: dict[str, str]) -> Any:
    return request_json(metodo, url, headers=cabeceras)


def cliente(api_key: str, base_url: str | None = None, enviar=None, *, tracker: str = "EMU") -> cliente_unit3d.Cliente:
    """El cliente de eMuwarez: cada petición espera su turno en el limitador compartido (emu_limitador) y un 429 pausa
    a todos. `enviar` (por defecto request_json, que devuelve el JSON y lanza IntegrationError) lo cambia la prueba de
    conexión, que quiere los errores de http_cliente. Otro UNIT3D va con su `base_url` y su `tracker` (NOBS, MNV)."""
    return cliente_unit3d.Cliente(
        cliente_unit3d.Tracker(normalize_base_url(base_url or EMU_BASE_URL), api_key, USER_AGENT),
        enviar or _enviar,
        turno=lambda prioridad: emu_limitador.esperar_turno(
            api_key, interactiva=prioridad == "interactiva", tracker=tracker),
        apuntar_429=lambda retry_after: emu_limitador.penalizar(
            api_key, segundos_retry_after(retry_after, EMU_ESPERA_429), tracker),
    )


def emu_test_connection(api_key: str, base_url: str | None = None, *, interactiva: bool = True,
                        tracker: str = "EMU") -> dict[str, Any]:
    """Test connectivity to eMuwarez (GET /api/torrents/filter?perPage=1).

    `interactiva=False` cuando es el primer paso de un recorrido largo: espera su turno en vez de fallar."""
    return _pedir(api_key, base_url, "/api/torrents/filter", {"perPage": "1"}, interactiva=interactiva, tracker=tracker)


def emu_get_requests(
    api_key: str,
    base_url: str | None = None,
    page: int = 1,
    per_page: int = 100,
    tracker: str = "EMU",
) -> dict[str, Any]:
    """Una página de las peticiones abiertas (sin llenar o reclamadas) de `tracker`, de las categorías de
    _categorias_de_peticiones; el filtro lo aplica el servidor.

    Devuelve la respuesta entera: data[] y meta.last_page / meta.total.
    """
    return _pedir_pagina(api_key, base_url, "/api/requests/filter", {
        "page": page, "perPage": per_page, "filled": 0, "category_id[]": _categorias_de_peticiones(tracker)}, tracker)


def emu_get_request_detail(
    api_key: str,
    request_id: int,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Fetch detail for a single EMU request by ID."""
    return _pedir(api_key, base_url, f"/api/requests/{request_id}", interactiva=True)


def emu_get_torrent(api_key: str, torrent_id: int, base_url: str | None = None, *,
                    tracker: str = "EMU", nombre: str = "eMuwarez") -> dict[str, Any]:
    """Un torrent (/api/torrents/{id}) con sus contadores del tracker: seeders, leechers, times_completed...
    UNIT3D los da en `attributes` (con o sin el envoltorio `data`); devuelve ese diccionario, o la respuesta entera
    si viene plana."""
    data = _pedir(api_key, base_url, f"/api/torrents/{int(torrent_id)}", interactiva=True, tracker=tracker)
    try:
        return cliente_unit3d.atributos_torrent(data)
    except ValueError:
        raise IntegrationError(f"{nombre} ha devuelto el torrent en un formato que no se reconoce") from None


def emu_get_user(api_key: str, base_url: str | None = None, *, tracker: str = "EMU",
                 nombre: str = "eMuwarez") -> dict[str, Any]:
    """Tu cuenta (/api/user): usuario, grupo, subido, bajado, ratio, buffer, seeding, leeching, bonus y H&R."""
    try:
        data = _pedir(api_key, base_url, "/api/user", interactiva=True, tracker=tracker)
    except IntegrationError as exc:
        if exc.status in _MENSAJE_POR_ESTADO:
            raise IntegrationError(_MENSAJE_POR_ESTADO[exc.status].format(nombre), status=exc.status) from None
        raise
    if not isinstance(data, dict) or not data.get("username"):
        raise IntegrationError(f"{nombre} ha devuelto tu perfil en un formato que no se reconoce")
    return data


def emu_fetch_all_pages(
    api_key: str, base_url: str | None = None, progress=None, tracker: str = "EMU", nombre: str = "eMuwarez",
) -> tuple[list[dict[str, Any]], Optional[int]]:
    """Recorre las peticiones abiertas página a página, al ritmo del limitador compartido (emu_limitador), con el cupo
    de `tracker` (EMU, NOBS, MNV); `nombre` es el que sale en los errores.

    Devuelve (filas, total). `total` es el meta.total del tracker, o None si no
    vino o cambió durante el recorrido: entonces no se puede dar por completo.
    Lanza IntegrationError si el recorrido no puede terminar.

    `progress(pagina, ultima_pagina, filas_hasta_ahora)` se llama tras cada página.
    """
    filas: list[dict[str, Any]] = []
    totales: set[Optional[int]] = set()
    pagina = ultima = 1
    while pagina <= ultima:
        try:
            data = emu_get_requests(api_key, base_url=base_url, page=pagina, tracker=tracker)
        except IntegrationError as exc:
            if exc.status in _MENSAJE_POR_ESTADO:
                raise IntegrationError(_MENSAJE_POR_ESTADO[exc.status].format(nombre), status=exc.status) from None
            raise
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise IntegrationError(f"{nombre} ha devuelto las peticiones en un formato que no se reconoce")
        filas.extend(data["data"])
        meta = data["meta"] if isinstance(data.get("meta"), dict) else {}
        ultima = _entero(meta.get("last_page")) or 1
        if ultima > EMU_PETICIONES_MAX_PAGINAS:
            raise IntegrationError(
                f"{nombre} tiene más de {EMU_PETICIONES_MAX_PAGINAS} páginas de peticiones abiertas; no se sincronizan")
        totales.add(_entero(meta.get("total")))
        if progress:
            try:
                progress(pagina, ultima, len(filas))
            except Exception:  # noqa: BLE001 — el progreso es lo de menos
                pass
        pagina += 1
    return filas, (totales.pop() if len(totales) == 1 else None)


# eMuwarez limita la API a 30 peticiones por minuto por clave (x-ratelimit-limit: 30) y perPage a 100, así que el
# catálogo entero (~13k torrents) son ~130 páginas seguidas. El ritmo lo pone emu_limitador, compartido por todas
# las peticiones de la key, también las de otros procesos: cada recorrido ya no se pausa por su cuenta.
EMU_PETICIONES_MAX_PAGINAS = 50
# Si aun así llega un 429, el limitador pausa a todos lo que pida Retry-After (o EMU_ESPERA_429 s) y la página se repite.
EMU_REINTENTOS_429 = 3

# Con estos estados, en la primera página, se prueba otra vez sin pedir un orden (puede ser lo que rechaza).
_RECHAZO_DE_ORDEN = (400, 422, 500)

# Lo que se enseña en vez del texto de request_json, que lleva la URL.
_MENSAJE_POR_ESTADO = {
    401: "{} ha rechazado la API key (¿ratio por debajo de 1?)",
    403: "{} ha rechazado la API key (¿ratio por debajo de 1?)",
    429: "{} ha limitado las peticiones; prueba en unos minutos",
}


def _pedir(api_key: str, base_url: str | None, ruta: str, params: dict[str, Any] | None = None, *,
           interactiva: bool, tracker: str = "EMU") -> Any:
    return cliente(api_key, base_url, tracker=tracker).pedir(
        "GET", ruta, params, prioridad="interactiva" if interactiva else "recorrido")


def _pedir_pagina(api_key: str, base_url: str | None, ruta: str, params: dict[str, Any], tracker: str = "EMU") -> Any:
    """Una página de un recorrido largo: un 429 se repite, tras la pausa que ya ha puesto el cliente."""
    for intento in range(EMU_REINTENTOS_429 + 1):
        try:
            return _pedir(api_key, base_url, ruta, params, interactiva=False, tracker=tracker)
        except IntegrationError as e:
            if e.status != 429 or intento == EMU_REINTENTOS_429:
                raise


def emu_fetch_all_torrents(
    api_key: str,
    base_url: str | None = None,
    per_page: int = 100,
    max_pages: int = 500,
    progress=None,
    stop_after_id: int | None = None,
    uploader: str | None = None,
    tracker: str = "EMU",
) -> list[dict[str, Any]]:
    """Fetch eMuwarez torrents (paginated /api/torrents/filter), newest first. Con `uploader`, solo los de ese usuario
    (UNIT3D deja fuera los anónimos).
    Returns the flat list of raw torrent objects ({type, id, attributes}).

    Incremental: when `stop_after_id` is given, the walk stops as soon as a
    torrent already known (id <= stop_after_id) is reached — so only the
    newly-uploaded torrents come back, instead of re-downloading the whole
    catalog. Se pide ordenado por created_at desc (UNIT3D ordena por defecto por bumped_at, y un torrent
    antiguo resubido saldría el primero y cortaría el recorrido): los ids bajan página a página y el primer
    id conocido significa que lo que sigue también se conoce. Si el tracker rechaza el orden en la primera
    página, el recorrido completo sigue sin él (su orden por defecto) en vez de dejar la sincronización sin
    funcionar; el incremental falla, porque sin orden no puede fiarse de dónde parar.

    El ritmo lo pone el limitador compartido (emu_limitador), con el cupo de `tracker` (EMU, NOBS, MNV).

    `progress(page, total_pages, items_so_far, total_items)` is called after each
    page (total_pages/total_items from the API `meta`, or None if absent)."""
    all_items: list[dict[str, Any]] = []
    total_pages: int | None = None
    total_items: int | None = None
    page = 1
    reached_known = False
    orden = {"sortField": "created_at", "sortDirection": "desc"}
    filtro = {"uploader": uploader} if uploader else {}

    def pedir(n: int) -> Any:
        return _pedir_pagina(api_key, base_url, "/api/torrents/filter", {
            "perPage": str(per_page), "page": str(n), **filtro, **orden}, tracker)

    while page <= max_pages and not reached_known:
        try:
            data = pedir(page)
        except IntegrationError as exc:
            # Sin orden, un torrent resubido saldría el primero y cortaría el recorrido incremental: solo se prueba
            # sin él en el completo.
            if page > 1 or not orden or exc.status not in _RECHAZO_DE_ORDEN or stop_after_id is not None:
                raise
            orden = {}
            data = pedir(page)
        items = data.get("data") if isinstance(data, dict) else None
        meta = data.get("meta") if isinstance(data, dict) else None
        if isinstance(meta, dict):
            total_pages = meta.get("last_page") or total_pages
            total_items = meta.get("total") or total_items
        # Una página que no trae la lista no es el final: cortar ahí daría por completa una lectura a medias.
        if not isinstance(items, list):
            from ..services.trackers import nombre_de
            raise IntegrationError(f"{nombre_de(tracker)} ha devuelto la página {page} en un formato que no se reconoce")
        if not items:
            _avisar(progress, page, total_pages, len(all_items), total_items)
            break
        if stop_after_id is not None:
            for t in items:
                try:
                    tid = int(t.get("id") or 0)
                except (TypeError, ValueError):
                    tid = 0
                if tid <= stop_after_id:
                    reached_known = True
                    break
                all_items.append(t)
        else:
            all_items.extend(items)
        _avisar(progress, page, total_pages, len(all_items), total_items)
        # Una página corta solo es la última si el tracker no dice cuántas páginas o cuántos torrents hay: uno que
        # limite `perPage` por debajo de lo pedido las da todas cortas.
        if total_pages:
            ultima = page >= total_pages
        elif total_items:
            # El incremental acaba al dar con el id conocido (o en una página vacía): el total es el del catálogo.
            ultima = stop_after_id is None and len(all_items) >= total_items
        else:
            ultima = len(items) < per_page
        if ultima:
            break
        page += 1
    # Acabar las páginas permitidas sin ver el final no es el catálogo entero: guardarlo borraría lo que falta.
    if page > max_pages and not reached_known:
        raise IntegrationError(f"El catálogo tiene más de {max_pages} páginas: no se guarda una lectura a medias")
    return all_items


def _avisar(progress, *datos) -> None:
    if progress:
        try:
            progress(*datos)
        except Exception:  # noqa: BLE001 — progress is best-effort
            pass


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _entero(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _id_externo(value: Any) -> Optional[str]:
    """tmdb/tvdb/mal como texto; None si falta o es 0 (UNIT3D guarda 0 para «sin id»)."""
    n = _entero(value)
    return str(n) if n else None


_ESPECIALES = re.compile(r"\b(?:especiales?|specials?)\b", re.I)


def normalize_request(raw: dict[str, Any], tracker: str = "EMU", base_url: str | None = None) -> RequestItem:
    """Una fila plana de /api/requests/filter de `tracker`, con los nombres de su tabla y el enlace a su web
    (`base_url`). Sea cual sea el campo que falte, no falla."""
    etiquetas = _etiquetas_de(tracker)
    emu_id = str(raw.get("id", ""))
    imdb = _entero(raw.get("imdb"))
    usuario = raw.get("user")
    autor = usuario if usuario and str(usuario).lower() != "anonymous" else None
    titulo = str(raw.get("name") or "Sin título")
    temporada, capitulo = _entero(raw.get("season_number")), _entero(raw.get("episode_number"))
    # UNIT3D guarda 0 para «sin temporada»: es la serie entera salvo que el nombre diga Especiales.
    if temporada == 0 and not capitulo and not _ESPECIALES.search(titulo):
        temporada = None
    return RequestItem(
        id=None,
        emu_id=emu_id,
        title=titulo,
        category=_etiqueta(etiquetas, "categories", "Categoría", raw.get("category_id")),
        requested_source=_etiqueta(etiquetas, "types", "Tipo", raw.get("type_id")),
        requested_resolution=_etiqueta(etiquetas, "resolutions", "Resolución", raw.get("resolution_id")),
        emu_status=raw.get("status"),
        points=_entero(raw.get("bounty")),
        created_at=raw.get("created"),
        tmdb_id=_id_externo(raw.get("tmdb")),
        imdb_id=f"tt{imdb:07d}" if imdb else None,
        tvdb_id=_id_externo(raw.get("tvdb")),
        mal_id=_id_externo(raw.get("mal")),
        url=f"{normalize_base_url(base_url or EMU_BASE_URL)}/requests/{emu_id}",
        author=autor,
        author_visible=autor is not None,
        req_scope="episode" if capitulo else "season" if temporada is not None else None,
        req_season=temporada,
        req_episode=capitulo,
        raw_json=json.dumps(raw, ensure_ascii=False),
    )


def normalize_requests(raw_items: Iterable[dict[str, Any]], tracker: str = "EMU",
                       base_url: str | None = None) -> list[RequestItem]:
    return [normalize_request(item, tracker, base_url) for item in raw_items
            if isinstance(item, dict) and item.get("id") is not None]
