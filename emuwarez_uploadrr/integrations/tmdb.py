"""TMDb integration — watch providers and metadata helpers (Phase 10A).

Uses the HTTP helper from base.py (request_json, over httpx).
"""
from __future__ import annotations

from .base import build_url, request_json, IntegrationError

_TMDB_BASE = "https://api.themoviedb.org/3"


def _get_tmdb_api_key(conn=None) -> str | None:
    _conn = None
    try:
        from ..database import connect, get_setting_canonical
        _conn = conn or connect()
        # Settings are written under the canonical dotted key (tmdb.api_key);
        # resolve both that and the legacy underscore alias (tmdb_api_key).
        return get_setting_canonical(_conn, "tmdb.api_key")
    except Exception:
        return None
    finally:
        if _conn is not None and conn is None:
            _conn.close()


def get_movie_watch_providers(tmdb_id: int, *, region: str = "ES", api_key: str | None = None, conn=None) -> dict:
    """Fetch /movie/{id}/watch/providers from TMDb. Returns full response dict."""
    _key = api_key or _get_tmdb_api_key(conn)
    if not _key:
        raise IntegrationError("TMDb API key no configurada")
    url = f"{_TMDB_BASE}/movie/{tmdb_id}/watch/providers?api_key={_key}"
    return request_json("GET", url)


def get_tv_watch_providers(tmdb_id: int, *, region: str = "ES", api_key: str | None = None, conn=None) -> dict:
    """Fetch /tv/{id}/watch/providers from TMDb."""
    _key = api_key or _get_tmdb_api_key(conn)
    if not _key:
        raise IntegrationError("TMDb API key no configurada")
    url = f"{_TMDB_BASE}/tv/{tmdb_id}/watch/providers?api_key={_key}"
    return request_json("GET", url)


def get_tv_title(tmdb_id: int, *, language: str = "es-ES", api_key: str | None = None, conn=None) -> str | None:
    """Localized TV series title from TMDb (/tv/{id}?language=xx). Returns the
    localized `name`, falling back to `original_name`; None if unavailable."""
    _key = api_key or _get_tmdb_api_key(conn)
    if not _key:
        raise IntegrationError("TMDb API key no configurada")
    url = f"{_TMDB_BASE}/tv/{tmdb_id}?api_key={_key}&language={language}"
    data = request_json("GET", url)
    if not isinstance(data, dict):
        return None
    name = (data.get("name") or "").strip()
    return name or (data.get("original_name") or "").strip() or None


def get_movie_title(tmdb_id: int, *, language: str = "es-ES", api_key: str | None = None, conn=None) -> str | None:
    """Localized movie title from TMDb (/movie/{id}?language=xx). Returns the
    localized `title`, falling back to `original_title`; None if unavailable."""
    _key = api_key or _get_tmdb_api_key(conn)
    if not _key:
        raise IntegrationError("TMDb API key no configurada")
    url = f"{_TMDB_BASE}/movie/{tmdb_id}?api_key={_key}&language={language}"
    data = request_json("GET", url)
    if not isinstance(data, dict):
        return None
    title = (data.get("title") or "").strip()
    return title or (data.get("original_title") or "").strip() or None


def get_images(kind: str, tmdb_id: str, *, language: str = "es-ES", api_key: str | None = None,
               conn=None) -> tuple[str | None, str | None]:
    """(poster_path, backdrop_path) de /movie/{id} o /tv/{id} (`kind` = "movie" o "tv"); el póster sale en ese
    idioma si TMDb lo tiene."""
    _key = api_key or _get_tmdb_api_key(conn)
    if not _key:
        raise IntegrationError("TMDb API key no configurada")
    data = request_json("GET", f"{_TMDB_BASE}/{kind}/{tmdb_id}?api_key={_key}&language={language}")
    if not isinstance(data, dict):
        return None, None
    return data.get("poster_path") or None, data.get("backdrop_path") or None


def find_images_by_imdb(imdb_id: str, *, language: str = "es-ES", api_key: str | None = None,
                        conn=None) -> tuple[str | None, str | None]:
    """(poster_path, backdrop_path) de lo que TMDb tenga con ese IMDb, película o serie: sirve cuando no se sabe
    cuál es (los documentales), porque TMDb numera aparte películas y series."""
    _key = api_key or _get_tmdb_api_key(conn)
    if not _key:
        raise IntegrationError("TMDb API key no configurada")
    data = request_json(
        "GET", f"{_TMDB_BASE}/find/{imdb_id}?api_key={_key}&external_source=imdb_id&language={language}")
    for key in ("movie_results", "tv_results"):
        resultados = data.get(key) if isinstance(data, dict) else None
        if resultados:
            return resultados[0].get("poster_path") or None, resultados[0].get("backdrop_path") or None
    return None, None


def search(kind: str, query: str, *, year: int | None = None, language: str = "es-ES", api_key: str | None = None,
           conn=None) -> list[dict]:
    """Los resultados de /search/movie o /search/tv (`kind` = "movie" o "tv") para ese título, filtrados por año si
    se da (el de estreno o el del primer episodio)."""
    _key = api_key or _get_tmdb_api_key(conn)
    if not _key:
        raise IntegrationError("TMDb API key no configurada")
    anio = "year" if kind == "movie" else "first_air_date_year"
    data = request_json("GET", build_url(_TMDB_BASE, f"search/{kind}",
                                         {"api_key": _key, "query": query, "language": language, anio: year}))
    resultados = data.get("results") if isinstance(data, dict) else None
    return [r for r in resultados if isinstance(r, dict) and r.get("id")] if isinstance(resultados, list) else []


def get_details(kind: str, tmdb_id: int | str, *, language: str = "es-ES", api_key: str | None = None,
                conn=None) -> dict:
    """/movie/{id} o /tv/{id} con sus `external_ids` (imdb_id, tvdb_id) en la misma llamada."""
    _key = api_key or _get_tmdb_api_key(conn)
    if not _key:
        raise IntegrationError("TMDb API key no configurada")
    data = request_json("GET", build_url(_TMDB_BASE, f"{kind}/{tmdb_id}",
                                         {"api_key": _key, "language": language, "append_to_response": "external_ids"}))
    if not isinstance(data, dict) or not data.get("id"):
        raise IntegrationError(f"TMDb no tiene la ficha {kind}/{tmdb_id}")
    return data


def map_tmdb_provider_to_emu_provider(provider_name: str) -> str | None:
    """Convenience wrapper — delegates to source_assistant."""
    from ..services.source_assistant import map_tmdb_provider_to_emu_provider as _map
    return _map(provider_name)
