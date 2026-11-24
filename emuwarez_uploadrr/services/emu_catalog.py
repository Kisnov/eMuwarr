"""eMuwarez catalog sync + comparison.

On-demand (button, 1/hour, no scheduled task): download the whole eMuwarez
torrent catalog, index it, and for every movie/season in the local library work
out whether it's already on eMuwarez and, if so, what your copy would *add*
(resolution / source / a more efficient codec / Spanish audio it lacks).

Each run also downloads the catalog of every other tracker with a key (N.O.B.S, Milnueve), one after another, into
the same table under its `tracker`: the library columns (`emu_in_catalog`, `emu_aportes`) stay eMuwarez's, and
"is it already on X" is read from the table by tracker (filas_del_catalogo, get_item_comparison).

eMuwarez has no language field, so "has Spanish" is parsed from the torrent name
(same nomenclature the tracker uses). What your copy adds (`emu_aportes`) comes from
conclusion_duplicados, the same rule as the detail card — see _aportes.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sqlite3
import threading
from typing import Optional

from ..database import get_state, set_state, get_json_state, set_json_state
from .settings import leer_ajuste
from ..integrations import emu
from ..integrations.base import IntegrationError, texto_de_error
from .tasks import start_task, finish_task
from .upload_states import ACTIVE_JOB_STATUSES
from .eventos import EVENTO_SYNC_EMPEZADA, EVENTO_SYNC_TERMINADA, publicar
from .trackers import EMU, TRACKERS, Tracker, tiene_clave
from .settings import ajuste_de_tracker

logger = logging.getLogger("emuwarez.emu")

SYNC_STATE_KEY = "last_emu_catalog_sync_utc"
# Written only when a sync genuinely finishes (unlike SYNC_STATE_KEY, which is
# stamped at the START of a sync — before anything is downloaded — so a page
# refresh can't relaunch a stalled one). get_item_comparison() must check THIS
# key, not SYNC_STATE_KEY: otherwise a sync that started but got stuck/crashed
# before finishing still reads as "synced", and every item confidently shows
# "no está en eMuwarez" against a catalog table that's actually still empty.
SYNC_COMPLETED_STATE_KEY = "last_emu_catalog_completed_utc"
# Highest torrent id stored, so the next sync only fetches newer torrents
# (incremental). Empty/unset → the catalog has never been fully downloaded, so
# the next sync is a full pass.
MAX_ID_STATE_KEY = "last_emu_catalog_max_id"
# Torrent count of the last full download, used only to estimate the progress
# bar's total on a (rare) subsequent full sync.
COUNT_STATE_KEY = "last_emu_catalog_torrents"
# Timestamp of the last FULL download, throttled apart from the incremental one.
FULL_SYNC_STATE_KEY = "last_emu_catalog_full_utc"
# Last {movies, seasons, episodes} "contenido único" counts we notified about —
# so a resync that doesn't change anything doesn't re-notify (see
# _notify_if_unique_content_changed below).
UNIQUE_CONTENT_COUNTS_STATE_KEY = "notify.content_unique_last_counts"
MIN_SYNC_SECONDS = 3600        # incremental: 1/hour
MIN_FULL_SYNC_SECONDS = 86400  # full re-download: 1/day
# Una descarga completa que trae menos que esto de lo que se esperaba no sustituye al catálogo guardado.
MIN_CATALOG_VS_TOTAL = 0.98     # contra el meta.total de eMuwarez, si lo manda
MIN_CATALOG_VS_PREVIOUS = 0.90  # contra lo que había, si no


def _clave(base: str, tracker: Tracker) -> str:
    """La clave de estado del catálogo de `tracker`: la de siempre para eMuwarez, con su id detrás para los demás."""
    return base if tracker is EMU else f"{base}.{tracker.id}"

# --- Parsing -----------------------------------------------------------------

_CODEC_TIER = {"AV1": 3, "HEVC": 2, "X265": 2, "H265": 2, "AVC": 1, "H264": 1, "X264": 1, "VP9": 1}
_CODEC_LABEL = {"AV1": "AV1", "HEVC": "H.265", "X265": "H.265", "H265": "H.265",
                "AVC": "H.264", "H264": "H.264", "X264": "H.264", "VP9": "VP9"}
_CODEC_RE = re.compile(r"\b(AV1|x265|H\.?265|HEVC|x264|H\.?264|AVC|VP9)\b", re.I)

_ES_TOKENS = {"ESP", "CAST", "CASTELLANO", "ESPANOL", "LATINO", "LAT", "DUAL", "MULTI"}
_ORIG_TOKENS = {"ING", "ENG", "JAP", "JPN", "COR", "KOR", "ITA", "FRA", "ALE", "GER", "RUS", "POR", "VO", "VOSE", "VOST", "VOSTFR"}
_SEASON_RE = re.compile(r"\bS(\d{1,3})(?:E\d{1,4})?\b", re.I)
_SEASON_RANGE_RE = re.compile(r"\bS(\d{1,3})\s*-\s*S?(\d{1,3})\b", re.I)
_SEASON_WORD_RE = re.compile(r"\b(?:temporadas?|seasons?)\s+(\d{1,3})(?:\s*-\s*(\d{1,3}))?\b", re.I)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _norm_codec(name: str) -> Optional[str]:
    m = _CODEC_RE.search(name or "")
    if not m:
        return None
    key = m.group(1).upper().replace(".", "")
    return _CODEC_LABEL.get(key)


def _codec_tier(codec: Optional[str]) -> int:
    if not codec:
        return 0
    return _CODEC_TIER.get(codec.upper().replace(".", ""), 0)


def _has_spanish(name: str) -> Optional[int]:
    """1 = Spanish audio, 0 = explicitly non-Spanish, None = unknown."""
    tokens = set(re.findall(r"[A-Za-z0-9+]+", (name or "").upper()))
    if tokens & _ES_TOKENS:
        return 1
    if tokens & _ORIG_TOKENS:
        return 0
    return None


def _season_range(first: int, last: int) -> list[int]:
    return list(range(first, last + 1)) if first <= last and last - first < 100 else [first]


def _seasons_in(text: str) -> list[int]:
    m = _SEASON_RANGE_RE.search(text)
    if m:
        return _season_range(int(m.group(1)), int(m.group(2)))
    tokens = _SEASON_RE.findall(text)
    if tokens:
        return [int(tokens[-1])]  # el último: «The S1 Files S02» es la 2
    m = _SEASON_WORD_RE.search(text)
    if m:
        return _season_range(int(m.group(1)), int(m.group(2) or m.group(1)))
    return []


def _parse_seasons(name: str) -> list[int]:
    """Las temporadas que trae un torrent: S02, S01-S03, S01-03, «Temporada 2». Lo que va antes del año es el título."""
    name = name or ""
    year = _YEAR_RE.search(name)
    return (_seasons_in(name[year.end():]) if year else []) or _seasons_in(name)


def _row_seasons(row: dict) -> list[int]:
    """Las temporadas de una fila del catálogo: se leen del nombre, y lo guardado (la primera) es el respaldo."""
    return _parse_seasons(row["name"]) or ([row["season_number"]] if row["season_number"] is not None else [])


# Las categorías de series de eMuwarez, N.O.B.S y Milnueve (docs/trackers-ids.md); «Anime en Catalan» lleva las dos.
_CATEGORIAS_SERIE = frozenset({"Series", "TV", "Anime TV Shows", "Series Anime", "Series latino"})


def _is_series(row: dict) -> bool:
    return row["category"] in _CATEGORIAS_SERIE or bool(_row_seasons(row))


# --- Throttle ----------------------------------------------------------------

def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_iso(v: Optional[str]) -> Optional[dt.datetime]:
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(v)
    except ValueError:
        return None


def seconds_until_next_sync(conn: sqlite3.Connection, mode: str = "incremental") -> int:
    """Seconds left on the throttle for a given mode. Incremental is gated by the
    last sync of any kind (1/hour); full by the last full sync (1/day)."""
    if mode == "full":
        last = _parse_iso(get_state(conn, FULL_SYNC_STATE_KEY))
        interval = MIN_FULL_SYNC_SECONDS
    else:
        last = _parse_iso(get_state(conn, SYNC_STATE_KEY))
        interval = MIN_SYNC_SECONDS
    if not last:
        return 0
    return max(0, int(interval - (_utcnow() - last).total_seconds()))


def get_sync_status(conn: sqlite3.Connection) -> dict:
    last = get_state(conn, SYNC_STATE_KEY)
    last_full = get_state(conn, FULL_SYNC_STATE_KEY)
    wait = seconds_until_next_sync(conn, "incremental")
    wait_full = seconds_until_next_sync(conn, "full")
    return {
        "last_sync_at": last, "can_sync": wait <= 0, "wait_seconds": wait,
        "min_interval_seconds": MIN_SYNC_SECONDS,
        # Per-mode throttles for the two-button sync card.
        "incremental_can_sync": wait <= 0, "incremental_wait_seconds": wait,
        "full_can_sync": wait_full <= 0, "full_wait_seconds": wait_full,
        "last_full_sync_at": last_full,
    }


# --- Catalog storage ---------------------------------------------------------

def _index_catalog(torrents: list[dict]) -> list[dict]:
    """Flatten raw torrent objects into catalog rows."""
    rows: list[dict] = []
    for t in torrents:
        a = t.get("attributes", {})
        name = a.get("name") or ""
        rows.append({
            "torrent_id": str(t.get("id") or ""),
            "category": a.get("category"),
            "tmdb_id": str(a["tmdb_id"]) if a.get("tmdb_id") else None,
            "tvdb_id": str(a["tvdb_id"]) if a.get("tvdb_id") else None,
            "season_number": min(_parse_seasons(name), default=None),
            "resolution": a.get("resolution"),
            "source": a.get("type"),
            "codec": _norm_codec(name),
            "has_spanish": _has_spanish(name),
            "size": a.get("size"),
            "name": name,
        })
    return rows


_INSERT_CATALOG_SQL = (
    "INSERT INTO emu_catalog (tracker, torrent_id, category, tmdb_id, tvdb_id, season_number, "
    "resolution, source, codec, has_spanish, size, name) VALUES "
    "(:tracker, :torrent_id, :category, :tmdb_id, :tvdb_id, :season_number, :resolution, :source, "
    ":codec, :has_spanish, :size, :name)"
)


def _sin_repetidos(rows: list[dict], tracker: Tracker = EMU) -> list[dict]:
    """Una fila por torrent: si el tracker recibe una subida mientras se pagina, la lista se desplaza y el último
    torrent de una página vuelve a salir en la siguiente (contaba dos veces en los aportes). Cada fila sale ya con su
    tracker."""
    return [{**r, "tracker": tracker.id} for r in {r["torrent_id"]: r for r in rows}.values()]


def _store_catalog(conn: sqlite3.Connection, rows: list[dict], tracker: Tracker = EMU) -> None:
    """Full replace — used on the first (complete) download."""
    conn.execute("DELETE FROM emu_catalog WHERE tracker = ?", (tracker.id,))
    conn.executemany(_INSERT_CATALOG_SQL, _sin_repetidos(rows, tracker))
    conn.commit()


def _append_catalog(conn: sqlite3.Connection, rows: list[dict], tracker: Tracker = EMU) -> None:
    """Incremental — add newly-uploaded torrents without dropping the rest. Los que ya estaban (una lectura anterior
    que se cortó antes de apuntar hasta dónde llegó) se sustituyen."""
    rows = _sin_repetidos(rows, tracker)
    if rows:
        conn.executemany("DELETE FROM emu_catalog WHERE tracker = ? AND torrent_id = ?",
                         [(tracker.id, r["torrent_id"]) for r in rows])
        conn.executemany(_INSERT_CATALOG_SQL, rows)
        conn.commit()


def _check_catalog_complete(rows: list[dict], total: Optional[int], previous: int) -> None:
    """Sustituir el catálogo con una descarga cortada a mitad (una página vacía o corta con un 200) lo dejaría
    sin torrents y daría por únicos los que ya están en eMuwarez: sin cambiar nada, se avisa del fallo."""
    got = len({r["torrent_id"] for r in rows})
    expected, minimum = (total, MIN_CATALOG_VS_TOTAL) if total else (previous, MIN_CATALOG_VS_PREVIOUS)
    if expected and got < expected * minimum:
        raise IntegrationError(
            f"La descarga del catálogo está incompleta ({got} de unos {expected} torrents); se conserva el anterior")


def _max_torrent_id(rows: list[dict]) -> int:
    best = 0
    for r in rows:
        try:
            best = max(best, int(r.get("torrent_id") or 0))
        except (TypeError, ValueError):
            pass
    return best


# --- Comparison --------------------------------------------------------------

def _fmt_size(n: Optional[int]) -> str:
    if not n:
        return ""
    gb = n / 1e9
    return f"{gb:.1f} GB" if gb >= 1 else f"{n / 1e6:.0f} MB"


# Real-quality ranking. An "aporte" only counts when your copy *improves* on
# the best eMuwarez has — a lower resolution or a worse source is not a
# contribution, even if it's a combo the tracker technically lacks. Ranks are
# case-insensitive and span BOTH vocabularies: Radarr/Sonarr's source labels
# (BluRay, BDRemux, WEBRip…) and eMuwarez's UNIT3D `type` values (Encode = a
# BluRay re-encode ≈ local "BluRay"; Remux ≈ "BDRemux"; Full Disc = untouched
# disc; WEBRIP is uppercase there; SD is low).
_RES_RANK = {"4320p": 8, "2160p": 7, "1080p": 6, "1080i": 5, "720p": 4, "576p": 3, "540p": 2, "480p": 1}
_SRC_RANK = {
    "full disc": 7, "bdremux": 6, "remux": 6, "bluray": 5, "encode": 5,
    "web-dl": 4, "webdl": 4, "webrip": 3, "hdtv": 2, "dvd": 1, "sd": 1,
}


def _res_rank(v: Optional[str]) -> int:
    return _RES_RANK.get((v or "").strip(), 0)


def _src_rank(v: Optional[str]) -> int:
    return _SRC_RANK.get((v or "").strip().lower(), 0)


# Source tokens as they appear in release names — used to recover the true
# source when eMuwarez's `type` field is mislabeled (e.g. a BluRay encode
# tagged "WEBRIP"). Order doesn't matter; we take the best-ranked hit.
_NAME_SRC_RE = re.compile(
    r"\b(BDRemux|BD-Remux|Remux|Full[\s.]?Disc|BluRay|Blu-Ray|BDRip|BRRip|WEB[\s.-]?DL|WEBRip|WEB|HDTV|DVDRip|DVD)\b",
    re.I,
)
_NAME_SRC_NORM = {"bd-remux": "bdremux", "blu-ray": "bluray", "bdrip": "bluray", "brrip": "bluray",
                  "web": "web-dl", "web.dl": "web-dl", "webdl": "web-dl", "dvdrip": "dvd", "full disc": "full disc"}


def _name_src_rank(name: Optional[str]) -> int:
    """Best source rank among the source tokens found in a release name."""
    best = 0
    for tok in _NAME_SRC_RE.findall(name or ""):
        key = re.sub(r"[\s.]+", " ", tok).strip().lower()
        key = _NAME_SRC_NORM.get(key, key)
        best = max(best, _src_rank(key))
    return best


def _match_src_rank(m: dict) -> int:
    """Effective source rank of an eMuwarez torrent: the better of its `type`
    field and whatever the release name reveals (names beat mislabeled types)."""
    return max(_src_rank(m.get("source")), _name_src_rank(m.get("name")))


def _aporta_audio(mine: dict, row: dict) -> bool:
    """Tienes audio en castellano y ese torrent dice expresamente que no (desconocido no cuenta)."""
    return bool(mine.get("has_es")) and row["has_spanish"] == 0


# Lo que acaba un nombre de eMuwarez tras el último guion es el grupo de release («Peli (2020) 1080p WEB-DL x265-Ryodan»);
# «DL» de un WEB-DL sin grupo no lo es.
_GRUPO_FINAL_RE = re.compile(r"-([A-Za-z0-9][A-Za-z0-9_.]{0,24})\s*$")
_NO_ES_GRUPO = frozenset({"dl", "rip", "ray", "disc", "remux"})  # «WEB-DL», «WEB-Rip», «Blu-Ray»…


def grupo_del_nombre(name: Optional[str]) -> Optional[str]:
    """El grupo de release que cierra el nombre de un torrent (tras el último guion), o None si no lo lleva."""
    m = _GRUPO_FINAL_RE.search(name or "")
    if not m or m.group(1).lower() in _NO_ES_GRUPO:
        return None
    return m.group(1)


def _decide(mine: dict, match: dict) -> tuple[Optional[str], Optional[str]]:
    """Tu copia frente a un torrent del catálogo: "mejor", "igual" o "peor", y la dimensión que lo decide. Resolución,
    luego source, luego códec; solo cuenta lo que se sabe de los dos lados (None si no hay nada que comparar)."""
    pares = (("resolucion", _res_rank(mine.get("resolution")), _res_rank(match.get("resolution"))),
             ("source", _src_rank(mine.get("source")), _match_src_rank(match)),
             ("codec", _codec_tier(mine.get("codec")), _codec_tier(match.get("codec"))))
    comparables = [(dim, mio, suyo) for dim, mio, suyo in pares if mio and suyo]
    for dim, mio, suyo in comparables:
        if mio != suyo:
            return ("mejor" if mio > suyo else "peor"), dim
    return ("igual" if comparables else None), None


def _comparados(mine: dict, rows: list[dict], itype: str) -> list[dict]:
    """Tu copia frente a cada torrent del catálogo: lo que lee conclusion_duplicados."""
    return [{"name": r["name"], "resolution": r["resolution"], "source": r["source"],
             "codec": r["codec"], "has_spanish": r["has_spanish"], "size": r["size"],
             "release_group": grupo_del_nombre(r["name"]),
             "aporta_audio": _aporta_audio(mine, r),
             # Un capítulo suelto no es el pack de la temporada: no se le compara.
             "veredicto": None if itype == "season" and _parse_episode_number(r["name"]) is not None
             else _decide(mine, r)[0]}
            for r in rows]


def _solo_capitulos(rows: list[dict], eps: list[dict]) -> tuple[bool, bool]:
    """Si de una temporada el catálogo solo tiene capítulos sueltos, y si tienes todos esos capítulos."""
    sueltos = {_parse_episode_number(r["name"]) for r in rows}
    tuyos = {e["episode_number"] for e in eps if e["local_status"] in ("Disponible", "Subido", "Subiendo")}
    return bool(sueltos) and None not in sueltos, bool(sueltos - {None}) and sueltos - {None} <= tuyos


def _aportes(mine: dict, matches: list[dict], eps: Optional[list[dict]] = None) -> list[dict]:
    """Lo que tu copia mejora ({dim, detail}), solo si conclusion_duplicados dice que es un trump. `eps`: los
    capítulos de una temporada."""
    if eps is not None:
        solo, todos = _solo_capitulos(matches, eps)
        if solo:
            # Sin pack en el catálogo, la ficha dice «Aportas» si tienes todos sus capítulos.
            return [{"dim": "pack", "detail": "Pack de temporada"}] if todos else []
    conclusion = conclusion_duplicados(matches)
    if not conclusion or conclusion["nivel"] != "trump":
        return []
    veredictos = [m["veredicto"] for m in matches]
    aportes: list[dict] = []
    if "mejor" in veredictos and "igual" not in veredictos and "peor" not in veredictos:
        # La dimensión frente al torrent más parecido: la última en decidir.
        decididos = [(_decide(mine, m)[1], m) for m in matches if m["veredicto"] == "mejor"]
        dim = max((d for d, _ in decididos), key=("resolucion", "source", "codec").index)
        detail = mine[{"resolucion": "resolution", "source": "source", "codec": "codec"}[dim]]
        sizes = [m["size"] for d, m in decididos if d == "codec" and m["size"]]
        if dim == "codec" and sizes and mine.get("size") and min(sizes) > mine["size"]:
            detail += f" (−{_fmt_size(min(sizes) - mine['size'])})"
        aportes.append({"dim": dim, "detail": detail})
    if all(m["aporta_audio"] for m in matches):
        aportes.append({"dim": "idioma", "detail": "ESP"})
    return aportes


def _temporada_en_castellano(eps: list[dict]) -> bool:
    """Si el pack va en castellano: todos los capítulos con fichero, no uno solo."""
    con_fichero = [e for e in eps if e["size_bytes"]]
    return bool(con_fichero) and all(_episode_lang_has_es(e["audio_languages"]) for e in con_fichero)


def _episode_lang_has_es(langs: Optional[str]) -> bool:
    if not langs:
        return False
    up = langs.upper()
    return any(tok in up for tok in ("ESP", "ES ", "SPA", "CAST", "DUAL", "MULTI", "LATINO"))


def recompute_emu_catalog_status(conn: sqlite3.Connection) -> int:
    """Re-run the (purely local, no network) eMuwarez comparison against
    whatever catalog snapshot is already stored — safe to call after a plain
    Radarr/Sonarr library sync so newly-added/changed items get emu_in_catalog
    computed right away, instead of staying NULL (and so invisible to
    Recomendaciones' Group 2/3, which require it resolved to exactly 0) until
    someone happens to also run a full eMuwarez catalog sync.

    No-ops (returns 0) if eMuwarez has never actually finished a catalog sync
    — same SYNC_COMPLETED_STATE_KEY guard get_item_comparison() uses — so a
    fresh install with no eMu API key yet doesn't get every single item
    confidently marked "not on eMuwarez" against an empty catalog table."""
    if not get_state(conn, SYNC_COMPLETED_STATE_KEY):
        return 0
    return _compute_and_store_item_status(conn)


def _compute_and_store_item_status(conn: sqlite3.Connection) -> int:
    """Compute emu_in_catalog/emu_aportes for every movie, season and series."""
    # Load the catalog into in-memory indexes.
    cat = [dict(r) for r in conn.execute("SELECT * FROM emu_catalog WHERE tracker = ?", (EMU.id,)).fetchall()]
    # TMDb numera aparte películas y series: un mismo id no es el mismo contenido. Las series se indexan por su
    # tvdb y por su tmdb (hay torrents con uno solo), y una temporada de un pack S01-S03 está en las tres.
    movies: dict[str, list] = {}
    seasons_idx: dict[tuple, list] = {}
    for c in cat:
        if _is_series(c):
            for n in _row_seasons(c):
                for kind in ("tvdb", "tmdb"):
                    if c[f"{kind}_id"]:
                        seasons_idx.setdefault((kind, c[f"{kind}_id"], n), []).append(c)
        elif c["tmdb_id"]:
            movies.setdefault(c["tmdb_id"], []).append(c)

    def season_rows(tvdb, tmdb, n) -> list[dict]:
        found: dict[int, dict] = {}
        for kind, ident in (("tvdb", tvdb), ("tmdb", tmdb)):
            if ident:
                for c in seasons_idx.get((kind, str(ident), n), []):
                    found[c["id"]] = c
        return list(found.values())

    # Honor the same "Ignorar especiales" setting the Estado/Nomenclatura
    # rollups use, so the three series statuses stay consistent. Default True
    # (only an explicit "false" counts specials).
    ignore_specials = leer_ajuste(conn, "series_ignore_specials")

    updated = 0

    def save(item_id: int, in_cat: Optional[int], aportes: list[dict]) -> None:
        nonlocal updated
        conn.execute(
            "UPDATE library_items SET emu_in_catalog = ?, emu_aportes = ? WHERE id = ?",
            (in_cat, json.dumps(aportes, ensure_ascii=False), item_id),
        )
        updated += 1

    # Movies
    for m in conn.execute("SELECT id, tmdb_id, resolution, source, video_codec, audio_languages, size_bytes "
                          "FROM library_items WHERE item_type = 'movie'").fetchall():
        m = dict(m)
        matches = movies.get(str(m["tmdb_id"]), []) if m["tmdb_id"] else []
        if not matches:
            save(m["id"], 0, [])
            continue
        mine = {"resolution": m["resolution"], "source": m["source"],
                "codec": _norm_codec(m["video_codec"] or "") or m["video_codec"],
                "has_es": _episode_lang_has_es(m["audio_languages"]), "size": m["size_bytes"]}
        save(m["id"], 1, _aportes(mine, _comparados(mine, matches, "movie")))

    # Seasons (aggregate their episodes) — series' tvdb_id + id come from the join.
    seasons = conn.execute(
        "SELECT se.id AS id, se.season_number AS season_number, "
        "sr.id AS series_id, sr.tvdb_id AS tvdb_id, sr.tmdb_id AS tmdb_id "
        "FROM library_items se JOIN library_items sr ON se.parent_id = sr.id "
        "WHERE se.item_type = 'season'"
    ).fetchall()
    # For the series rollup we count real seasons on/off eMuwarez. Specials
    # (season 0/None) are excluded from the count — the tracker rarely carries
    # them, so counting them would flag almost every complete series as partial
    # (the special still gets its own "No" badge when you expand the series).
    series_total: dict[int, int] = {}
    series_in_count: dict[int, int] = {}
    series_aportes: dict[int, list] = {}
    for s in seasons:
        s = dict(s)
        eps = [dict(e) for e in conn.execute(
            "SELECT resolution, source, video_codec, audio_languages, size_bytes, episode_number, local_status "
            "FROM library_items WHERE parent_id = ? AND item_type = 'episode'", (s["id"],)
        ).fetchall()]
        matches = season_rows(s["tvdb_id"], s["tmdb_id"], s["season_number"]) if s["season_number"] is not None else []
        mine = {
            "resolution": _mode([e["resolution"] for e in eps]),
            "source": _mode([e["source"] for e in eps]),
            "codec": _best_codec([e["video_codec"] for e in eps]),
            "has_es": _temporada_en_castellano(eps),
            "size": sum(e["size_bytes"] or 0 for e in eps) or None,
        }
        aportes = _aportes(mine, _comparados(mine, matches, "season"), eps)
        save(s["id"], 1 if matches else 0, aportes)
        # Accumulate into the parent series' rollup. A numberless season is
        # never counted; season 0 (specials) counts unless "Ignorar especiales".
        pid = s["series_id"]
        sn = s["season_number"]
        counts = sn is not None and not (ignore_specials and sn == 0)
        if counts:
            series_total[pid] = series_total.get(pid, 0) + 1
            series_in_count[pid] = series_in_count.get(pid, 0) + (1 if matches else 0)
        for a in aportes:
            series_aportes.setdefault(pid, [])
            if a["detail"] not in [x["detail"] for x in series_aportes[pid]]:
                series_aportes[pid].append(a)

    # Episodes — each matched to its own eMuwarez torrent (parsed SxxExx), so a
    # series that only exists as loose episodes still shows "Sí" per capítulo.
    for e in conn.execute(
        "SELECT e.id AS id, e.season_number AS season_number, e.episode_number AS episode_number, "
        "e.resolution AS resolution, e.source AS source, e.video_codec AS video_codec, "
        "e.audio_languages AS audio_languages, e.size_bytes AS size_bytes, sr.tvdb_id AS tvdb_id, "
        "sr.tmdb_id AS tmdb_id "
        "FROM library_items e JOIN library_items se ON e.parent_id = se.id "
        "JOIN library_items sr ON se.parent_id = sr.id "
        "WHERE e.item_type = 'episode'"
    ).fetchall():
        e = dict(e)
        matches = []
        if e["season_number"] is not None and e["episode_number"] is not None:
            # The catalog has no episode_number column — parse it from the name (e.g. "…S01E08…") so each
            # episode can be matched to its torrent.
            matches = [c for c in season_rows(e["tvdb_id"], e["tmdb_id"], e["season_number"])
                       if _parse_episode_number(c["name"] or "") == e["episode_number"]]
        if not matches:
            save(e["id"], 0, [])
            continue
        mine = {"resolution": e["resolution"], "source": e["source"],
                "codec": _norm_codec(e["video_codec"] or "") or e["video_codec"],
                "has_es": _episode_lang_has_es(e["audio_languages"]), "size": e["size_bytes"]}
        save(e["id"], 1, _aportes(mine, _comparados(mine, matches, "episode")))

    # Series rollup: 0 = no seasons on eMuwarez, 1 = all seasons on eMuwarez,
    # 2 = partial (some seasons on, some off) — so a mixed series is no longer
    # hidden behind a plain "Sí".
    for sr in conn.execute("SELECT id FROM library_items WHERE item_type = 'series'").fetchall():
        sid = sr["id"]
        total = series_total.get(sid, 0)
        incount = series_in_count.get(sid, 0)
        if total == 0 or incount == 0:
            status = 0
        elif incount == total:
            status = 1
        else:
            status = 2
        save(sid, status, series_aportes.get(sid, []))

    conn.commit()
    return updated


_SXXEXX_RE = re.compile(r"S\d{1,3}\s*E(\d{1,4})", re.I)


def _parse_episode_number(name: str) -> Optional[int]:
    """Episode number from a torrent name (…S01E08… → 8). None for a season
    pack / anything without an SxxExx marker."""
    m = _SXXEXX_RE.search(name or "")
    return int(m.group(1)) if m else None


def _mode(values: list) -> Optional[str]:
    vals = [v for v in values if v]
    if not vals:
        return None
    return max(set(vals), key=vals.count)


def _best_codec(codecs: list) -> Optional[str]:
    best, best_tier = None, -1
    for c in codecs:
        nc = _norm_codec(c or "") or c
        t = _codec_tier(nc)
        if t > best_tier:
            best, best_tier = nc, t
    return best


# --- Per-item catalog rows ----------------------------------------------------

def _series_ids(conn: sqlite3.Connection, series_id: Optional[int]) -> tuple[Optional[str], Optional[str]]:
    row = conn.execute("SELECT tvdb_id, tmdb_id FROM library_items WHERE id = ?", (series_id,)).fetchone()
    return (row["tvdb_id"], row["tmdb_id"]) if row else (None, None)


def _series_rows(conn: sqlite3.Connection, tvdb, tmdb, tracker: Tracker = EMU) -> list[dict]:
    """Los torrents de series con ese tvdb o ese tmdb: el mismo criterio que _compute_and_store_item_status."""
    ids = [(col, str(v)) for col, v in (("tvdb_id", tvdb), ("tmdb_id", tmdb)) if v]
    if not ids:
        return []
    rows = conn.execute("SELECT * FROM emu_catalog WHERE tracker = ? AND ("
                        + " OR ".join(f"{col} = ?" for col, _ in ids) + ")",
                        [tracker.id, *(v for _, v in ids)]).fetchall()
    return [r for r in map(dict, rows) if _is_series(r)]


def filas_del_catalogo(conn: sqlite3.Connection, item: dict, tracker: Tracker = EMU) -> list[dict]:
    """Los torrents de `tracker` de una película, temporada, capítulo o serie de la biblioteca."""
    kind = item["item_type"]
    if kind == "movie":
        if not item["tmdb_id"]:
            return []
        rows = conn.execute("SELECT * FROM emu_catalog WHERE tracker = ? AND tmdb_id = ?",
                            (tracker.id, str(item["tmdb_id"]))).fetchall()
        return [r for r in map(dict, rows) if not _is_series(r)]
    if kind == "series":
        return _series_rows(conn, item["tvdb_id"], item["tmdb_id"], tracker)
    if kind == "season":
        if item["season_number"] is None:
            return []
        tvdb, tmdb = _series_ids(conn, item["parent_id"])
        return [r for r in _series_rows(conn, tvdb, tmdb, tracker) if item["season_number"] in _row_seasons(r)]
    if kind == "episode":
        # Series → temporada → capítulo: el tvdb/tmdb salen de la serie y el capítulo se reconoce por su SxxExx.
        season = conn.execute("SELECT parent_id FROM library_items WHERE id = ?", (item["parent_id"],)).fetchone()
        tvdb, tmdb = _series_ids(conn, season["parent_id"] if season else None)
        season_n, ep_n = item["season_number"], item["episode_number"]
        if season_n is None or ep_n is None:
            return []
        return [r for r in _series_rows(conn, tvdb, tmdb, tracker)
                if season_n in _row_seasons(r) and _parse_episode_number(r["name"]) == ep_n]
    return []


def catalogo_cubre(conn: sqlite3.Connection, item_id: int, resolucion: Optional[str], tipo: Optional[str]) -> bool:
    """Si eMuwarez ya tiene un torrent de ese contenido con la resolución y el tipo pedidos (None = da igual)."""
    item = conn.execute("SELECT * FROM library_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        return False
    item = dict(item)
    filas = filas_del_catalogo(conn, item)
    if item["item_type"] in ("season", "series"):
        # Un capítulo suelto no cubre una temporada entera.
        filas = [r for r in filas if _parse_episode_number(r["name"] or "") is None]
    cumplen = [r for r in filas
               if (not resolucion or r["resolution"] == resolucion)
               and (not tipo or (r["source"] or "").lower() == tipo.lower())]
    if item["item_type"] != "series":
        return bool(cumplen)
    # Una serie entera está cubierta si cada temporada que cuenta (como en el resumen de la serie) tiene la variante.
    ignorar_especiales = leer_ajuste(conn, "series_ignore_specials")
    temporadas = [n for (n,) in conn.execute(
        "SELECT season_number FROM library_items WHERE parent_id = ? AND item_type = 'season' "
        "AND season_number IS NOT NULL", (item_id,)) if not (ignorar_especiales and n == 0)]
    return bool(temporadas) and all(any(n in _row_seasons(r) for r in cumplen) for n in temporadas)


# --- Per-item comparison detail (for the detail card) ------------------------

def catalogo_leido(conn: sqlite3.Connection, tracker: Tracker) -> Optional[str]:
    """Cuándo acabó la última lectura entera del catálogo local de `tracker`; None si no se ha leído nunca."""
    return get_state(conn, _clave(SYNC_COMPLETED_STATE_KEY, tracker))


def conclusion_duplicados(matches: list[dict], tracker: Tracker = EMU) -> Optional[dict]:
    """La conclusión de «En <tracker> ya hay», de lo que aporta tu copia frente a cada torrent (`veredicto` de
    calidad y `aporta_audio`): `nivel` (duplicado, trump, mixto, peor o sin-comparar), `clase` (el veredicto con que se
    pinta), `texto` y `ya_lo_tiene` (duplicado o peor: el lote lo salta para ese tracker y «Subir también» no lo
    ofrece). None sin torrents. Audio en castellano que no tiene ninguno es una mejora aunque la calidad no lo sea. No
    es un permiso: cada tracker tiene sus reglas de trumps y solo ellas deciden si una subida se acepta."""
    if not matches:
        return None
    veredictos = [m["veredicto"] for m in matches]
    mejor, igual, peor = (veredictos.count(v) for v in ("mejor", "igual", "peor"))
    # Si algún torrent no se pudo comparar, lo que sigue solo vale para los que sí.
    parcial = None in veredictos
    nota = " Alguna comparación falta: la conclusión es parcial." if parcial else ""

    def dicho(nivel: str, clase: Optional[str], texto: str) -> dict:
        return {"nivel": nivel, "clase": clase, "texto": texto, "ya_lo_tiene": nivel in ("duplicado", "peor")}

    if all(m["aporta_audio"] for m in matches):
        aunque = f", aunque tu copia empeora {peor} de lo que hay" if peor else ""
        return dicho("trump", "mejor", f"Aportas audio ESP que no tiene ninguno{aunque}: puede ser un trump. "
                                       "Revisa las reglas de trumps antes de subir.")
    if mejor + igual + peor == 0:
        return dicho("sin-comparar", None,
                     "No se pudo comparar la calidad con lo que ya hay: revisa a mano si sería un duplicado.")
    if igual:
        hay = "hay 1 torrent" if igual == 1 else f"hay {igual} torrents"
        return dicho("duplicado", "igual", f"Ya {hay} con tu misma calidad: la subida sería un duplicado.")
    if mejor and not peor:
        cuales = "el torrent que hay" if mejor == 1 else f"los {mejor} torrents que hay"
        return dicho("trump", "mejor",
                     f"Tu copia mejora {cuales}: puede ser un trump. Revisa las reglas de trumps antes de subir.{nota}")
    if mejor:
        return dicho("mixto", "igual", f"Tu copia mejora {mejor} y empeora {peor} de lo que hay: no es un trump claro.{nota}")
    lo = "comparado" if parcial else "que ya hay"
    return dicho("peor", "peor", f"Tu copia es peor que todo lo {lo}: {tracker.nombre} probablemente la rechace.{nota}")


def motivo_ya_lo_tiene(tracker: Tracker) -> str:
    return f"Ya hay uno igual o mejor en {tracker.nombre} (catálogo local)."


def get_item_comparison(conn: sqlite3.Connection, item_id: int, *,
                        resolution: Optional[str] = None, source: Optional[str] = None, tracker: Tracker = EMU) -> dict:
    """Your copy vs every `tracker` torrent that matches this movie/season, for
    the detail-card comparison table. Movies match by tmdb_id, seasons by
    (series tvdb_id, season_number). `resolution` y `source`, si vienen, sustituyen a los de tu copia: son los
    que el asistente de subida va a poner en el nombre. `synced_at`: cuándo acabó la última lectura de su catálogo."""
    item = conn.execute("SELECT * FROM library_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        return {"available": False, "message": "Elemento no encontrado", "matches": []}
    item = dict(item)
    itype = item["item_type"]

    # The catalog table is empty both when it's genuinely never been synced
    # AND (indistinguishably, by row count alone) when it HAS been synced but
    # this specific title just isn't there — an empty `matches` list further
    # down can't tell those apart. Check the *completed* marker (not the
    # attempt-started throttle timestamp), so a never-finished sync (stuck,
    # crashed, or still running) reads as "not yet synced" instead of
    # confidently claiming "contenido único" for every single item.
    synced_at = catalogo_leido(conn, tracker)
    if not synced_at:
        mensaje = ("Sincroniza con eMuwarez para ver la comparación." if tracker is EMU
                   else f"El catálogo de {tracker.nombre} se lee al sincronizar el de eMuwarez.")
        return {"available": False, "message": mensaje, "matches": []}


    rows = filas_del_catalogo(conn, item, tracker)
    if itype == "movie":
        mine = {"resolution": item["resolution"], "source": item["source"],
                "codec": _norm_codec(item["video_codec"] or "") or item["video_codec"],
                "has_es": _episode_lang_has_es(item["audio_languages"]), "size": item["size_bytes"],
                "release_groups": [item["release_group"]] if item["release_group"] else []}
    elif itype == "season":
        eps = [dict(e) for e in conn.execute(
            "SELECT resolution, source, video_codec, audio_languages, size_bytes, release_group, episode_number, "
            "local_status FROM library_items WHERE parent_id = ? AND item_type = 'episode'", (item_id,)
        ).fetchall()]
        mine = {
            "resolution": _mode([e["resolution"] for e in eps]),
            "source": _mode([e["source"] for e in eps]),
            "codec": _best_codec([e["video_codec"] for e in eps]),
            "has_es": _temporada_en_castellano(eps),
            "size": sum(e["size_bytes"] or 0 for e in eps) or None,
            "release_groups": sorted({e["release_group"] for e in eps if e["release_group"]}, key=str.casefold),
        }
    elif itype == "episode":
        mine = {"resolution": item["resolution"], "source": item["source"],
                "codec": _norm_codec(item["video_codec"] or "") or item["video_codec"],
                "has_es": _episode_lang_has_es(item["audio_languages"]), "size": item["size_bytes"],
                "release_groups": [item["release_group"]] if item["release_group"] else []}
    else:
        # Series is a rollup (drill into its seasons).
        # El resumen de una serie solo lo hay de eMuwarez (sus columnas de la biblioteca).
        return {"available": False, "message": "La comparación es a nivel de película, temporada o capítulo",
                "matches": [], **({"in_catalog": item.get("emu_in_catalog"),
                                   "aportes": _safe_json(item.get("emu_aportes"))} if tracker is EMU else {})}

    if resolution:
        mine["resolution"] = resolution
    if source:
        mine["source"] = source
    matches = _comparados(mine, rows, itype)
    result = {
        "available": True,
        "message": None,
        "synced_at": synced_at,
        "mine": mine,
        # Whether you actually have the file on disk. For a "Faltante" item there
        # is no local copy, so the "Tu copia" comparison would be meaningless.
        "mine_available": mine.get("size") is not None,
        "matches": matches,
        "in_catalog": 1 if matches else 0,
        "aportes": _aportes(mine, matches, eps if itype == "season" else None),
        "conclusion": conclusion_duplicados(matches, tracker),
    }
    if itype == "season":
        # Distinguish "there is a season pack" from "only loose episodes": contributing the pack could be new, but
        # only if you have all the episodes.
        result["only_episodes"], result["have_all_episodes"] = _solo_capitulos(matches, eps)
    return result


def get_items_comparison(conn: sqlite3.Connection, item_ids: list[int], *, resolution: Optional[str] = None,
                         source: Optional[str] = None, tracker: Tracker = EMU) -> dict:
    """La comparación de cada elemento (una tanda del asistente, una fila del lote) y la conclusión de todos sus
    torrents juntos."""
    comparaciones = [get_item_comparison(conn, i, resolution=resolution, source=source, tracker=tracker)
                     for i in item_ids]
    return {"comparaciones": comparaciones,
            "conclusion": conclusion_duplicados([m for c in comparaciones for m in c["matches"]], tracker)}


def _safe_json(raw):
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return []


# --- Public sync -------------------------------------------------------------

# Live progress of the eMuwarez sync, so the UI can show a card of what it's
# doing. Single-process app → an in-memory dict is enough (the sync is on-demand,
# 1/hour). Polled via GET /library/emu-sync-progress.
_SYNC_LOCK = threading.Lock()
# `error_otros`: lo que falló al leer el catálogo de otro tracker (lo de eMuwarez vale igual).
_SYNC_PROGRESS: dict = {"active": False, "done": False, "phase": "", "torrents": 0, "items": 0,
                        "page": 0, "total_pages": None, "eta_seconds": None, "error": None, "error_otros": None}


def get_sync_progress() -> dict:
    with _SYNC_LOCK:
        return dict(_SYNC_PROGRESS)


def _set_progress(**kw) -> None:
    with _SYNC_LOCK:
        _SYNC_PROGRESS.update(kw)


# Las subidas que gastan llamadas de eMuwarez: todas las activas menos las que esperan en la cola.
ESTADOS_EN_MARCHA = ACTIVE_JOB_STATUSES - {"queued"}


def motivo_subida_en_marcha(conn: sqlite3.Connection) -> Optional[str]:
    """Una subida en cola (`queued`) no gasta llamadas, así que no cuenta: si no, un lote largo dejaría horas sin sincronizar.
    Las que se preparan o suben, sí."""
    en_marcha = tuple(sorted(ESTADOS_EN_MARCHA))
    if conn.execute(
        f"SELECT 1 FROM upload_jobs WHERE status IN ({','.join('?' * len(en_marcha))}) LIMIT 1", en_marcha
    ).fetchone():
        return "Hay una subida en marcha; vuelve a probar cuando termine."
    return None


def start_emu_sync_background(force: bool = False, mode: str = "incremental") -> dict:
    """Start the catalog sync in a background thread (so the UI can poll
    progress). Returns {status:'started'} or {status:'throttled', wait_seconds}.
    mode: "incremental" (only new torrents) or "full" (re-download + reconcile).
    Con una subida en marcha devuelve {status:'busy', message}."""
    from ..database import connect, db_path, get_state
    conn = connect(db_path())
    is_full = mode == "full"
    try:
        motivo = motivo_subida_en_marcha(conn)
        if motivo:
            return {"status": "busy", "message": motivo}
        wait = seconds_until_next_sync(conn, mode)
        if wait > 0 and not force:
            return {"status": "throttled", "wait_seconds": wait}
        with _SYNC_LOCK:
            if _SYNC_PROGRESS.get("active"):
                return {"status": "started"}  # one already running
        # Record the throttle NOW (at start), not at the end — otherwise a slow
        # or stalled download leaves no throttle and a page refresh lets you
        # relaunch it. Saved so a failed run can restore it. A full pass also
        # counts as a sync, so it bumps both timestamps.
        prev = get_state(conn, SYNC_STATE_KEY)
        prev_full = get_state(conn, FULL_SYNC_STATE_KEY)
        now_iso = _utcnow().isoformat()
        set_state(conn, SYNC_STATE_KEY, now_iso)
        if is_full:
            set_state(conn, FULL_SYNC_STATE_KEY, now_iso)
        conn.commit()
    finally:
        conn.close()
    _set_progress(active=True, done=False, error=None, error_otros=None, phase="Iniciando…", torrents=0, items=0,
                  page=0, total_pages=None, eta_seconds=None)
    publicar(EVENTO_SYNC_EMPEZADA, {"que": "catalogo"})

    def _run() -> None:
        c = connect(db_path())
        torrents_emu = 0
        try:
            sync_emu_catalog(c, force=True, mode=mode)  # throttle already recorded above
            torrents_emu = get_sync_progress()["torrents"]
        except Exception as exc:  # noqa: BLE001 — surface the error to the card
            _set_progress(error=texto_de_error(exc), phase="Error")
            # A failed sync shouldn't lock you out — restore the previous
            # throttle timestamp(s).
            try:
                cc = connect(db_path())
                set_state(cc, SYNC_STATE_KEY, prev or "")
                if is_full:
                    set_state(cc, FULL_SYNC_STATE_KEY, prev_full or "")
                cc.commit()
                cc.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            # Luego cada otro tracker con clave, de uno en uno: cada uno gasta su propio cupo de la API.
            fallos: list[str] = []
            for tracker in (t for t in TRACKERS.values() if t is not EMU and tiene_clave(c, t)):
                try:
                    sync_emu_catalog(c, force=True, mode=mode, tracker=tracker)
                except Exception as exc:  # noqa: BLE001 — su fallo no deshace lo de eMuwarez
                    fallos.append(f"{tracker.nombre}: {texto_de_error(exc)}")
                    _set_progress(error_otros="; ".join(fallos))
        finally:
            c.close()
            # El aviso del final cuenta los torrents de eMuwarez, no los del último tracker.
            _set_progress(active=False, done=True, torrents=torrents_emu)
            # Aquí termina la sincronización del catálogo, salga bien o mal.
            publicar(EVENTO_SYNC_TERMINADA, {"que": "catalogo"})

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


def _notify_if_unique_content_changed(conn: sqlite3.Connection) -> None:
    """"Contenido único" (movies/seasons/episodes not yet on eMuwarez) just got
    recomputed above — reuse the exact same counts already shown on the
    Dashboard (get_dashboard_summary) so the notification always matches what
    the user sees there, and only actually notify when they changed since the
    last sync (comparing every sync would spam the same numbers repeatedly).
    Best-effort — never raises, mirrors services/library.py::_notify_new_missing.
    """
    try:
        from .library import get_dashboard_summary
        from .notification_center import emit
        summary = get_dashboard_summary(conn)
        counts = {
            "movies": summary["unique_movies"],
            "seasons": summary["unique_seasons"],
            "episodes": summary["unique_episodes"],
        }
        prev = get_json_state(conn, UNIQUE_CONTENT_COUNTS_STATE_KEY)
        set_json_state(conn, UNIQUE_CONTENT_COUNTS_STATE_KEY, counts)
        if prev == counts:
            return
        emit(
            conn, "content_unique_changed",
            "Nuevo contenido único",
            f"Tu biblioteca tiene {counts['movies']} películas, {counts['seasons']} temporadas y "
            f"{counts['episodes']} episodios que todavía no están en eMuwarez.",
            link="/library",
            level="info",
        )
    except Exception:
        logger.exception("No se pudo emitir la notificación de contenido único")


def sync_emu_catalog(conn: sqlite3.Connection, *, force: bool = False, mode: str = "incremental",
                     tracker: Tracker = EMU) -> dict:
    """Download `tracker`'s catalog (eMuwarez by default) and, for eMuwarez, recompute per-item status. 1/hour.

    mode="full" re-downloads the whole catalog and replaces it (reconciles
    torrents deleted on the tracker); mode="incremental" only fetches torrents
    newer than the last sync and appends them (fast, but can't detect deletions).
    A first sync with no baseline is always full regardless of mode."""
    wait = seconds_until_next_sync(conn, mode)
    if wait > 0 and not force:
        return {"status": "throttled", "wait_seconds": wait, "torrents": 0, "items": 0}

    nombre = tracker.nombre
    task_id = start_task(conn, "sync-emu-catalog", f"Sincronizando catálogo {nombre} ({'completo' if mode == 'full' else 'nuevo'})")
    try:
        api_key = ajuste_de_tracker(conn, "api_key", tracker.id)
        if not api_key:
            raise IntegrationError(f"No hay API key de {nombre} configurada")

        # Fail fast (and clearly) if eMuwarez itself is unreachable, instead of only
        # discovering it mid-download — this is a single cheap request (perPage=1)
        # bound by request_json's own timeout, so a down/unreachable tracker surfaces
        # as an error within seconds rather than the sync silently hanging.
        _set_progress(phase=f"Comprobando conexión con {nombre}…", page=0, total_pages=None, eta_seconds=None)
        try:
            # primer paso del recorrido: espera su turno
            emu.emu_test_connection(api_key, tracker.url, interactiva=False, tracker=tracker.uploadrr)
        except IntegrationError as exc:
            raise IntegrationError(f"No se pudo conectar con {nombre}: {texto_de_error(exc)}") from exc

        import time
        # Incremental: only fetch torrents newer than the highest id we already have.
        # First sync ever (no stored max) → full download.
        try:
            known_max_id = int(get_state(conn, _clave(MAX_ID_STATE_KEY, tracker)) or 0)
        except (TypeError, ValueError):
            known_max_id = 0
        # Incremental unless a full pass is requested (or there's no baseline yet).
        incremental = (mode != "full") and known_max_id > 0
        # Estimate the page total (the API doesn't send last_page): a full pass is
        # ~ceil(previous_count / 100). Only meaningful for a full re-download.
        try:
            prev_count = int(get_state(conn, _clave(COUNT_STATE_KEY, tracker)) or 0)
        except (TypeError, ValueError):
            prev_count = 0
        if not prev_count:
            # Fall back to whatever is already stored (e.g. a catalog downloaded
            # before this bookkeeping existed) so the first full bar has an estimate.
            prev_count = int(conn.execute("SELECT COUNT(*) AS n FROM emu_catalog WHERE tracker = ?",
                                          (tracker.id,)).fetchone()["n"])
        est_full_pages = ((prev_count + 99) // 100) if (prev_count and not incremental) else None

        phase_label = f"Buscando torrents nuevos en {nombre}…" if incremental else f"Descargando catálogo de {nombre}…"
        logger.info("Sincronización de %s iniciada (modo %s)", nombre, "incremental" if incremental else "completo")
        _set_progress(phase=phase_label, page=0, total_pages=None, eta_seconds=None)
        _dl_start = time.time()
        reported_total: dict[str, Optional[int]] = {"n": None}

        def _on_page(page: int, total_pages, items_so_far: int, total_items) -> None:
            reported_total["n"] = total_items or reported_total["n"]
            tp = total_pages or est_full_pages
            elapsed = time.time() - _dl_start
            eta = None
            if tp and page > 0 and page < tp:
                eta = max(0, int(elapsed / page * (tp - page)))
            _set_progress(phase=phase_label, page=page,
                          total_pages=tp, torrents=items_so_far, eta_seconds=eta)

        torrents = emu.emu_fetch_all_torrents(
            api_key, tracker.url, progress=_on_page,
            stop_after_id=known_max_id if incremental else None, tracker=tracker.uploadrr,
        )
        rows = _index_catalog(torrents)

        if incremental:
            _set_progress(phase="Guardando torrents nuevos…", torrents=len(rows), eta_seconds=None)
            _append_catalog(conn, rows, tracker)
        else:
            _check_catalog_complete(rows, reported_total["n"], prev_count)
            _set_progress(phase="Guardando catálogo…", torrents=len(rows), eta_seconds=None)
            _store_catalog(conn, rows, tracker)

        # Track the highest id + total count for the next incremental / estimate.
        new_max_id = max(known_max_id, _max_torrent_id(rows))
        total_now = int(conn.execute("SELECT COUNT(*) AS n FROM emu_catalog WHERE tracker = ?",
                                      (tracker.id,)).fetchone()["n"])
        if new_max_id > 0:
            set_state(conn, _clave(MAX_ID_STATE_KEY, tracker), str(new_max_id))
        set_state(conn, _clave(COUNT_STATE_KEY, tracker), str(total_now))

        items = 0
        if tracker is EMU:  # las columnas de la biblioteca son las de eMuwarez
            _set_progress(phase="Calculando comparaciones con tu biblioteca…")
            items = _compute_and_store_item_status(conn)
            _set_progress(items=items)
        _set_progress(phase="Listo")
        now_iso = _utcnow().isoformat()
        set_state(conn, _clave(SYNC_COMPLETED_STATE_KEY, tracker), now_iso)
        if tracker is EMU:  # los límites de 1/hora y 1/día son los de la tanda entera, que empieza por eMuwarez
            set_state(conn, SYNC_STATE_KEY, now_iso)
            if not incremental:  # a full pass also refreshes the 1/day full throttle
                set_state(conn, FULL_SYNC_STATE_KEY, now_iso)
            _notify_if_unique_content_changed(conn)
        conn.commit()
        summary = f"{len(rows)} torrents {'nuevos' if incremental else 'descargados'}, {total_now} en catálogo"
        if tracker is EMU:
            summary += f", {items} elementos comparados"
        finish_task(conn, task_id, "completed", summary)
        logger.info("Sincronización de %s terminada (%s): %s", nombre, "incremental" if incremental else "completo",
                    summary)
        return {"status": "ok", "torrents": len(rows), "items": items, "incremental": incremental}
    except Exception as exc:
        finish_task(conn, task_id, "failed", texto_de_error(exc))
        raise
