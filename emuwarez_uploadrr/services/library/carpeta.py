"""Carpeta manual: una instancia de tipo `carpeta` es una carpeta del disco con vídeos que no vienen de Radarr/Sonarr.

El escaneo crea las mismas filas que una sincronización (película, o serie → temporada → capítulo) con lo que guessit
lee del nombre y MediaInfo del fichero. La ficha de TMDb la confirma el usuario: hasta entonces la fila no tiene
`tmdb_id` y no se puede subir. El escaneo conserva lo confirmado leyendo la fila guardada antes de escribirla.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import unicodedata
from typing import Optional

from ...integrations import tmdb
from ...integrations.base import IntegrationError, extract_release_group, texto_de_error
from ...models import InstanceConfig, LibraryItem, LocalStatus, MediaKind, NomenclatureStatus
from .sync import SyncAlreadyRunning, _sin_sincronizar, _update_season_rollup, recompute_after_sync, upsert_library_item

logger = logging.getLogger(__name__)

TIPO = "carpeta"
EXTENSIONES_VIDEO = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".mov", ".wmv"}
# Las búsquedas de TMDb de un escaneo: el resto, en el siguiente (o al abrir el diálogo de identificar).
BUSQUEDAS_POR_ESCANEO = 40
_POSTER = "https://image.tmdb.org/t/p/original"
_RE_MUESTRA = re.compile(r"(^|[\W_])sample([\W_]|$)", re.IGNORECASE)
# Lo que el escaneo copia de la fila guardada: lo confirmado en TMDb no sale del nombre del fichero.
_FICHA = ("tmdb_id", "imdb_id", "tvdb_id", "title", "original_title", "english_title", "year", "poster_url",
          "backdrop_url")


def ruta_de_carpeta(ruta: str) -> str:
    ruta = (ruta or "").strip()
    if not ruta.startswith("/"):
        raise ValueError("La ruta de la carpeta debe ser absoluta (empieza por /)")
    return os.path.normpath(ruta)


def _no_se_puede_leer(exc: OSError) -> None:
    # Un recorrido a medias no vale: lo que no se ha visto se podaría con lo confirmado en TMDb.
    raise IntegrationError(f"No se puede leer {exc.filename}: {exc.strerror}") from exc


def _videos(raiz: str) -> list[str]:
    from ..carpeta_subida import RAIZ_SUBIDAS
    subidas = os.path.realpath(RAIZ_SUBIDAS)
    encontrados = []
    for carpeta, subcarpetas, ficheros in os.walk(raiz, onerror=_no_se_puede_leer):
        # La carpeta de subidas no: sus enlaces son copias de lo que ya está en la biblioteca.
        subcarpetas[:] = sorted(d for d in subcarpetas if not d.startswith(".") and not _RE_MUESTRA.search(d)
                                and os.path.realpath(os.path.join(carpeta, d)) != subidas)
        for nombre in ficheros:
            ruta = os.path.join(carpeta, nombre)
            # «._x.mkv» es lo que deja un Mac al copiar; isfile descarta un enlace roto.
            if (os.path.splitext(nombre)[1].lower() in EXTENSIONES_VIDEO and not nombre.startswith(".")
                    and not _RE_MUESTRA.search(nombre) and os.path.isfile(ruta)):
                encontrados.append(ruta)
    return sorted(encontrados)


def probar_carpeta(ruta: str, conn: sqlite3.Connection | None = None) -> dict:
    """{ok, message, warning | error} para el botón «Comprobar carpeta». Nunca lanza."""
    try:
        ruta = ruta_de_carpeta(ruta)
    except ValueError as exc:
        return {"ok": False, "error": texto_de_error(exc)}
    if not os.path.isdir(ruta) or not os.access(ruta, os.R_OK | os.X_OK):
        return {"ok": False, "error": f"No existe o no se puede leer la carpeta {ruta} dentro del contenedor"}
    avisos = []
    if not os.access(ruta, os.W_OK):
        # La nomenclatura renombra en la carpeta de subidas; lo que escribe aquí es la edición de pistas y título.
        avisos.append("no se puede escribir en ella, así que al preparar las pistas y el título no se editan en el original")
    if conn is not None:
        from ..uploadrr_contract import get_allowed_roots, path_within_allowed_roots
        if not path_within_allowed_roots(ruta, get_allowed_roots(conn)):
            avisos.append("está fuera de las rutas permitidas para subir (Ajustes)")
    try:
        n = len(_videos(ruta))
    except IntegrationError as exc:
        return {"ok": False, "error": texto_de_error(exc)}
    return {"ok": True, "message": f"{n} vídeo{'s' if n != 1 else ''} en la carpeta",
            "warning": ("La carpeta " + " y ".join(avisos)) if avisos else None}


def _clave(texto: str) -> str:
    plano = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", plano.lower())


def _fuente(g: dict) -> Optional[str]:
    """El código de eMuwarez para lo que guessit llama `source` (+ `other`: Rip, Remux)."""
    fuente = g.get("source")
    otros = g.get("other") or []
    otros = otros if isinstance(otros, list) else [otros]
    if fuente == "Web":
        return "WEBRip" if "Rip" in otros else "WEB-DL"
    if fuente in ("Blu-ray", "Ultra HD Blu-ray"):
        return "BDRemux" if "Remux" in otros else "BluRay"
    if fuente in ("HDTV", "Digital TV", "TV"):
        return "HDTV"
    if fuente in ("DVD", "Video on Demand"):
        return "SD"
    return None


def analizar_nombre(relativa: str) -> dict:
    """Lo que guessit lee de la ruta relativa (las carpetas también cuentan: «Serie/Season 1/05.mkv»)."""
    from guessit import guessit
    g = guessit(relativa)
    episodios = g.get("episode")
    episodios = sorted({int(e) for e in (episodios if isinstance(episodios, list) else [episodios])
                        if isinstance(e, int)}) if episodios is not None else []
    temporada = g.get("season")
    if isinstance(temporada, list):
        temporada = temporada[0] if temporada else None
    es_capitulo = g.get("type") == "episode" and bool(episodios)
    resolucion = str(g.get("screen_size") or "")
    resolucion = "2160p" if resolucion == "4K" else resolucion
    return {
        "tipo": "episode" if es_capitulo else "movie",
        "titulo": str(g.get("title") or os.path.splitext(os.path.basename(relativa))[0]),
        "anio": g.get("year") if isinstance(g.get("year"), int) else None,
        # Sin temporada en el nombre (numeración absoluta del anime) va a la 1, como hace Sonarr con la mayoría.
        "temporada": int(temporada) if isinstance(temporada, int) else (1 if es_capitulo else None),
        "episodios": episodios if es_capitulo else [],
        "resolucion": resolucion if re.fullmatch(r"\d{3,4}p", resolucion) else None,
        "fuente": _fuente(g),
        "grupo": extract_release_group({"releaseGroup": str(g.get("release_group") or "")}),
    }


def _anio(fecha) -> Optional[int]:
    return int(fecha[:4]) if isinstance(fecha, str) and re.match(r"\d{4}", fecha) else None


def _candidato(kind: str, r: dict) -> dict:
    return {
        "tmdb_id": str(r["id"]),
        "titulo": r.get("title" if kind == "movie" else "name") or "",
        "titulo_original": r.get("original_title" if kind == "movie" else "original_name") or "",
        "anio": _anio(r.get("release_date" if kind == "movie" else "first_air_date")),
        "poster_url": f"{_POSTER}{r['poster_path']}" if r.get("poster_path") else None,
        "resumen": (r.get("overview") or "")[:300],
    }


def _buscar(kind: str, titulo: str, anio: Optional[int], conn) -> list[dict]:
    resultados = tmdb.search(kind, titulo, year=anio, conn=conn)
    if not resultados and anio:
        resultados = tmdb.search(kind, titulo, conn=conn)
    return [_candidato(kind, r) for r in resultados[:8]]


def _sugerencia(kind: str, titulo: str, anio: Optional[int], candidatos: list[dict]) -> Optional[dict]:
    """El candidato con el mismo título (o el original) y el mismo año; sin año en el nombre, solo si es el único con
    ese título. Es una propuesta: no se aplica sin que el usuario la confirme."""
    clave = _clave(titulo)
    iguales = [c for c in candidatos if clave and clave in (_clave(c["titulo"]), _clave(c["titulo_original"]))]
    if anio:
        iguales = [c for c in iguales if c["anio"] == anio]
    if len(iguales) != 1:
        return None
    c = iguales[0]
    return {"tmdb_id": c["tmdb_id"], "tipo": kind, "titulo": c["titulo"], "anio": c["anio"], "poster_url": c["poster_url"]}


def _guardada(conn, instance_id: int, item_type: str, external_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM library_items WHERE instance_id = ? AND item_type = ? AND external_id = ?",
        (instance_id, item_type, external_id),
    ).fetchone()
    return dict(row) if row else {}


def _raw(fila: dict) -> dict:
    try:
        datos = json.loads(fila.get("raw_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def _titulo_temporada(serie: str, n: int) -> str:
    return f"{serie} - Especiales" if n == 0 else f"{serie} - Temporada {n}"


def _titulo_capitulo(serie: str, temporada: int, episodios: list[int]) -> str:
    titulo = f"{serie} S{temporada:02d}E{episodios[0]:02d}"
    return titulo + (f"-E{episodios[-1]:02d}" if len(episodios) > 1 else "")


def _sin_cambios(guardada: dict, ruta: str, mtime: int, size: int) -> bool:
    """El fichero es el que leyó el último escaneo."""
    return guardada.get("path") == ruta and guardada.get("size_bytes") == size and _raw(guardada).get("mtime") == mtime


def _media(ruta: str, guardada: dict, mtime: int, size: int) -> dict:
    """Códec e idiomas de MediaInfo; si el fichero no ha cambiado desde el último escaneo, los guardados."""
    campos = ("video_codec", "audio_codec", "audio_languages", "subtitle_languages")
    if _sin_cambios(guardada, ruta, mtime, size) and guardada.get("video_codec"):
        return {c: guardada.get(c) for c in campos}
    from ..media_info_service import read_media_info_summary
    leido = read_media_info_summary(ruta) or {}
    return {c: leido.get(c) for c in campos}


def _resolucion_medida(conn, instance_id: int, ruta: str, external_id: str) -> Optional[str]:
    """La resolución de MediaInfo de un capítulo sin ella en el nombre; la guardada si el fichero no ha cambiado."""
    guardada = _guardada(conn, instance_id, MediaKind.EPISODE.value, external_id)
    estado = os.stat(ruta)
    if _sin_cambios(guardada, ruta, int(estado.st_mtime), int(estado.st_size)) and guardada.get("resolution"):
        return guardada["resolution"]
    from ..media_info_service import read_resolution
    return read_resolution(ruta)


class _Busquedas:
    """Las búsquedas de TMDb de un escaneo, con tope; sin clave de TMDb no busca."""

    def __init__(self, conn) -> None:
        from ..tmdb_titles import tmdb_configured
        self.conn = conn
        self.quedan = BUSQUEDAS_POR_ESCANEO if tmdb_configured(conn) else 0

    def completar(self, kind: str, titulo: str, anio: Optional[int], raw: dict) -> None:
        if "candidatos" in raw or self.quedan <= 0:
            return
        self.quedan -= 1
        try:
            candidatos = _buscar(kind, titulo, anio, self.conn)
        except IntegrationError as exc:
            logger.warning("No se pudo buscar «%s» en TMDb: %s", titulo, texto_de_error(exc))
            return
        raw["candidatos"] = candidatos
        raw["sugerencia"] = _sugerencia(kind, titulo, anio, candidatos)


def _item(instance: InstanceConfig, item_type: str, external_id: str, **campos) -> LibraryItem:
    return LibraryItem(id=None, item_type=item_type, instance_id=int(instance.id), instance_type=TIPO,
                       instance_name=instance.name, external_id=external_id, **campos)


def escanear(conn: sqlite3.Connection, instance: InstanceConfig, *, visto: str, tiene_filas: bool,
             on_progress=None, on_total=None) -> tuple[int, int]:
    """Escribe una fila por vídeo de la carpeta (con `seen_at=visto`); lo que ya no está lo poda sync_instance.
    Devuelve (importados, 0): todo lo que hay en una carpeta está disponible."""
    raiz = ruta_de_carpeta(instance.url)
    if not os.path.isdir(raiz) or not os.access(raiz, os.R_OK | os.X_OK):
        raise IntegrationError(f"No existe o no se puede leer la carpeta {raiz}")
    ficheros = _videos(raiz)
    if not ficheros and tiene_filas:
        raise IntegrationError(
            f"La carpeta «{instance.name}» no tiene vídeos y su biblioteca ya tiene elementos: no se borra nada, por "
            "si el disco no está montado. Si la has vaciado a propósito, quita la instancia.")
    if on_total:
        on_total(len(ficheros))
    busquedas = _Busquedas(conn)
    peliculas: list[tuple[str, dict]] = []
    series: dict[str, list[tuple[str, dict]]] = {}
    for ruta in ficheros:
        rel = os.path.relpath(ruta, raiz)
        info = analizar_nombre(rel)
        if info["tipo"] == "movie":
            peliculas.append((ruta, info))
        else:
            # Una serie por año (el Shōgun de 1980 no es el de 2024) y por resolución, como una instancia de Sonarr 4K
            # aparte: un pack nunca mezcla 1080p y 2160p. Sin ella en el nombre, la del fichero.
            info["resolucion"] = info["resolucion"] or _resolucion_medida(conn, instance.id, ruta, f"episode:{rel}")
            clave = f"{_clave(info['titulo']) or info['titulo']}:{info['anio'] or ''}:{info['resolucion'] or ''}"
            series.setdefault(clave, []).append((ruta, info))
    hechos = 0

    def _fichero(ruta: str, info: dict, item_type: str, external_id: str, ficha: dict, raw: dict, **campos) -> LibraryItem:
        nonlocal hechos
        guardada = _guardada(conn, instance.id, item_type, external_id)
        estado = os.stat(ruta)
        mtime, size = int(estado.st_mtime), int(estado.st_size)
        item = _item(
            instance, item_type, external_id, path=ruta, size_bytes=size, resolution=info["resolucion"],
            source=info["fuente"], release_group=info["grupo"], local_status=LocalStatus.AVAILABLE.value,
            nomenclature_status=NomenclatureStatus.UNCHECKED.value,
            raw_json=json.dumps({**raw, "mtime": mtime}, ensure_ascii=False),
            **_media(ruta, guardada, mtime, size), **ficha, **campos,
        )
        upsert_library_item(conn, item, notify_missing=False, seen_at=visto)
        hechos += 1
        if on_progress:
            on_progress(hechos)
        return item

    for ruta, info in peliculas:
        rel = os.path.relpath(ruta, raiz)
        external_id = f"peli:{rel}"
        guardada = _guardada(conn, instance.id, MediaKind.MOVIE.value, external_id)
        # Con la carpeta en otra ruta, otro fichero con el mismo nombre no hereda la ficha: solo si pesa lo mismo.
        if guardada.get("path") not in (None, ruta) and guardada.get("size_bytes") != os.path.getsize(ruta):
            guardada = {}
        raw = {k: v for k, v in _raw(guardada).items() if k in ("candidatos", "sugerencia")}
        ficha = _ficha_de(guardada) or {"title": info["titulo"], "year": info["anio"]}
        if not guardada.get("tmdb_id"):
            busquedas.completar("movie", info["titulo"], info["anio"], raw)
        raw["guessit"] = {"titulo": info["titulo"], "anio": info["anio"], "tipo": "movie"}
        _fichero(ruta, info, MediaKind.MOVIE.value, external_id, ficha, raw)

    for clave, capitulos in series.items():
        _escanear_serie(conn, instance, raiz, clave, capitulos, visto=visto, busquedas=busquedas, fichero=_fichero)
    return hechos, 0


def _ficha_de(guardada: dict) -> dict:
    """Lo confirmado en TMDb de una fila guardada ({} si no lo está)."""
    return {k: guardada.get(k) for k in _FICHA} if guardada.get("tmdb_id") else {}


def _escanear_serie(conn, instance: InstanceConfig, raiz: str, clave: str, capitulos: list[tuple[str, dict]], *,
                    visto: str, busquedas: _Busquedas, fichero) -> None:
    titulo = capitulos[0][1]["titulo"]
    anio = next((info["anio"] for _, info in capitulos if info["anio"]), None)
    external_id = f"serie:{clave}"
    guardada = _guardada(conn, instance.id, MediaKind.SERIES.value, external_id)
    raw = {k: v for k, v in _raw(guardada).items() if k in ("candidatos", "sugerencia", "temporadas")}
    ficha = _ficha_de(guardada) or {"title": titulo, "year": anio}
    if not guardada.get("tmdb_id"):
        busquedas.completar("tv", titulo, anio, raw)
    raw["guessit"] = {"titulo": titulo, "anio": anio, "tipo": "tv"}
    carpetas = {os.path.dirname(ruta) for ruta, _ in capitulos}
    serie_id = upsert_library_item(conn, _item(
        instance, MediaKind.SERIES.value, external_id, path=os.path.commonpath(sorted(carpetas)),
        local_status=LocalStatus.AVAILABLE.value, nomenclature_status=NomenclatureStatus.NA.value,
        raw_json=json.dumps(raw, ensure_ascii=False), **ficha,
    ), notify_missing=False, seen_at=visto)
    nombre = ficha["title"]
    ids = {k: ficha.get(k) for k in ("tmdb_id", "imdb_id", "tvdb_id")}
    temporadas = raw.get("temporadas") or {}
    por_temporada: dict[int, list[tuple[str, dict]]] = {}
    for ruta, info in capitulos:
        por_temporada.setdefault(info["temporada"], []).append((ruta, info))
    for n, lista in sorted(por_temporada.items()):
        temporada_id = upsert_library_item(conn, _item(
            instance, MediaKind.SEASON.value, f"season:{clave}:{n}", title=_titulo_temporada(nombre, n),
            year=ficha.get("year"), season_number=n, parent_id=serie_id, local_status=LocalStatus.AVAILABLE.value,
            nomenclature_status=NomenclatureStatus.NA.value, **ids,
        ), notify_missing=False, seen_at=visto)
        estreno = (temporadas.get(str(n)) or {}).get("estreno")
        items = []
        for ruta, info in sorted(lista, key=lambda c: c[1]["episodios"]):
            raw_capitulo = {"episodios": info["episodios"]}
            if estreno:
                # nomenclature.anio_de_temporada lee aquí el año de la temporada, como el airDate de Sonarr.
                raw_capitulo["episode"] = {"airDate": estreno}
            items.append(fichero(
                ruta, info, MediaKind.EPISODE.value, f"episode:{os.path.relpath(ruta, raiz)}",
                {"title": _titulo_capitulo(nombre, n, info["episodios"]), "year": ficha.get("year"),
                 "original_title": ficha.get("original_title"), "english_title": ficha.get("english_title"), **ids},
                raw_capitulo, season_number=n, episode_number=info["episodios"][0], parent_id=temporada_id,
            ))
        _update_season_rollup(conn, temporada_id, items)
        conn.commit()


# ---------------------------------------------------------------------------
# Identificar en TMDb
# ---------------------------------------------------------------------------

def ficha_principal(conn, item_id: int) -> dict:
    """La película o la serie de una fila de carpeta (sube de capítulo y temporada). LookupError si no es de carpeta."""
    fila = conn.execute("SELECT * FROM library_items WHERE id = ?", (item_id,)).fetchone()
    for _ in range(2):
        if fila is None or fila["item_type"] not in (MediaKind.SEASON.value, MediaKind.EPISODE.value):
            break
        fila = conn.execute("SELECT * FROM library_items WHERE id = ?", (fila["parent_id"],)).fetchone()
    if fila is None or fila["instance_type"] != TIPO or fila["item_type"] not in (MediaKind.MOVIE.value,
                                                                                  MediaKind.SERIES.value):
        raise LookupError(f"El elemento {item_id} no es de una carpeta manual")
    return dict(fila)


def _kind(fila: dict) -> str:
    return "movie" if fila["item_type"] == MediaKind.MOVIE.value else "tv"


def candidatos(conn, item_id: int, *, consulta: str | None = None, anio: int | None = None,
               tipo: str | None = None) -> dict:
    fila = ficha_principal(conn, item_id)
    guessit = _raw(fila).get("guessit") or {}
    kind = tipo if tipo in ("movie", "tv") else _kind(fila)
    consulta = (consulta or "").strip()
    if not consulta:
        # Sin texto, lo que leyó guessit del nombre, con su año.
        consulta = guessit.get("titulo") or fila["title"]
        anio = guessit.get("anio") if anio is None else anio
    return {"tipo": kind, "consulta": consulta, "anio": anio, "candidatos": _buscar(kind, consulta, anio, conn)}


def _iguales(conn, fila: dict) -> list[dict]:
    """La fila y las de su carpeta por identificar con el mismo título y año de guessit (la misma película o serie en
    otra resolución): se identifican juntas. Solo con año (sin él, la Dune de 1984 y la de 2021 serían la misma) y con
    un título comparable (_clave deja vacío uno en otro alfabeto)."""
    guessit = _raw(fila).get("guessit") or {}
    filas = [fila]
    clave = _clave(guessit.get("titulo"))
    if not guessit.get("anio") or not clave:
        return filas
    for otra in conn.execute(
        "SELECT * FROM library_items WHERE instance_id = ? AND item_type = ? AND tmdb_id IS NULL AND id != ?",
        (fila["instance_id"], fila["item_type"], fila["id"]),
    ).fetchall():
        suya = _raw(dict(otra)).get("guessit") or {}
        if _clave(suya.get("titulo")) == clave and suya.get("anio") == guessit["anio"]:
            filas.append(dict(otra))
    return filas


def identificar(conn, item_id: int, tmdb_id: str, tipo: str | None = None, *, recalcular: bool = True) -> int:
    """Pone la ficha de TMDb (título en castellano, original, año, IMDb/TVDb, póster) en la película o la serie, en
    sus temporadas y capítulos, y en sus copias en otra resolución. Devuelve el id de la película o la serie. Con una
    sincronización de biblioteca en marcha lanza SyncAlreadyRunning: el escaneo pisaría lo confirmado."""
    fila = ficha_principal(conn, item_id)
    kind = _kind(fila)
    if tipo and tipo != kind:
        raise ValueError("Esta ficha es una película: elige una película de TMDb" if kind == "movie"
                         else "Esta ficha es una serie: elige una serie de TMDb")
    tmdb_id = str(tmdb_id).strip()
    if not tmdb_id.isdigit():
        raise ValueError("El id de TMDb es un número")
    datos = tmdb.get_details(kind, tmdb_id, conn=conn)
    externos = datos.get("external_ids") or {}
    titulo = datos.get("title" if kind == "movie" else "name") or datos.get("original_title" if kind == "movie"
                                                                             else "original_name") or fila["title"]
    ficha = {
        "tmdb_id": tmdb_id,
        "imdb_id": datos.get("imdb_id") or externos.get("imdb_id") or None,
        "tvdb_id": str(externos["tvdb_id"]) if externos.get("tvdb_id") else None,
        "title": titulo,
        "original_title": datos.get("original_title" if kind == "movie" else "original_name") or None,
        "year": _anio(datos.get("release_date" if kind == "movie" else "first_air_date")),
        "poster_url": f"{_POSTER}{datos['poster_path']}" if datos.get("poster_path") else None,
        "backdrop_url": f"{_POSTER}{datos['backdrop_path']}" if datos.get("backdrop_path") else None,
    }
    temporadas = {
        str(t["season_number"]): {"capitulos": t.get("episode_count"), "estreno": t.get("air_date")}
        for t in datos.get("seasons") or [] if isinstance(t, dict) and t.get("season_number") is not None
    }
    comunes = {k: ficha[k] for k in ("tmdb_id", "imdb_id", "tvdb_id", "year", "original_title")}
    # TMDb ya está leído: con el candado solo se escribe, y se relee lo guardado por si un escaneo lo cambió mientras.
    with _sin_sincronizar():
        for principal in _iguales(conn, ficha_principal(conn, item_id)):
            raw = {k: v for k, v in _raw(principal).items() if k in ("guessit", "mtime")}
            if kind == "tv":
                raw["temporadas"] = temporadas
            _escribir(conn, principal["id"], {**ficha, "raw_json": json.dumps(raw, ensure_ascii=False)})
            if kind == "tv":
                _identificar_capitulos(conn, principal["id"], titulo, temporadas, comunes)
        conn.commit()
    if recalcular:
        recompute_after_sync(conn)
    return int(fila["id"])


def _identificar_capitulos(conn, serie_id: int, titulo: str, temporadas: dict, comunes: dict) -> None:
    for temporada in conn.execute("SELECT id, season_number FROM library_items WHERE parent_id = ? AND "
                                  "item_type = 'season'", (serie_id,)).fetchall():
        n = temporada["season_number"]
        _escribir(conn, temporada["id"], {**comunes, "title": _titulo_temporada(titulo, n)})
        estreno = (temporadas.get(str(n)) or {}).get("estreno")
        for capitulo in conn.execute("SELECT id, raw_json FROM library_items WHERE parent_id = ? AND "
                                     "item_type = 'episode'", (temporada["id"],)).fetchall():
            raw = _raw(dict(capitulo))
            raw.pop("episode", None)
            if estreno:
                raw["episode"] = {"airDate": estreno}
            episodios = raw.get("episodios") or [0]
            _escribir(conn, capitulo["id"], {**comunes, "title": _titulo_capitulo(titulo, n, episodios),
                                             "raw_json": json.dumps(raw, ensure_ascii=False)})


def _escribir(conn, item_id: int, campos: dict) -> None:
    columnas = ", ".join(f"{c} = ?" for c in campos)
    conn.execute(f"UPDATE library_items SET {columnas}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                 (*campos.values(), item_id))


def identificar_sugeridas(conn, instance_id: int | None = None) -> int:
    """Confirma la sugerencia de cada película o serie de carpeta por identificar que la tenga. Devuelve cuántas. Si
    empieza una sincronización a medias, para ahí; SyncAlreadyRunning solo si aún no había confirmado ninguna."""
    sql = ("SELECT id, raw_json FROM library_items WHERE instance_type = ? AND tmdb_id IS NULL "
           "AND item_type IN ('movie', 'series')")
    params: list = [TIPO]
    if instance_id is not None:
        sql += " AND instance_id = ?"
        params.append(instance_id)
    hechas = 0
    sincronizando = False
    try:
        for fila in conn.execute(sql, params).fetchall():
            sugerencia = _raw(dict(fila)).get("sugerencia")
            if not sugerencia:
                continue
            # La copia en otra resolución ya la identificó la suya (_iguales), o una sincronización la quitó.
            actual = conn.execute("SELECT tmdb_id FROM library_items WHERE id = ?", (fila["id"],)).fetchone()
            if actual is None or actual["tmdb_id"]:
                continue
            try:
                identificar(conn, fila["id"], sugerencia["tmdb_id"], sugerencia.get("tipo"), recalcular=False)
                hechas += 1
            except SyncAlreadyRunning:
                if not hechas:
                    raise
                sincronizando = True
                break
            except (IntegrationError, ValueError, LookupError) as exc:
                logger.warning("No se pudo confirmar la sugerencia del elemento %s: %s", fila["id"], texto_de_error(exc))
    finally:
        # Una vez para todas: recorre la biblioteca entera. La sincronización en marcha ya recalcula al acabar.
        if hechas and not sincronizando:
            recompute_after_sync(conn)
    return hechas


def error_de_varios_capitulos(fila: dict) -> Optional[str]:
    """Un capítulo de carpeta con varios en el fichero (S01E01-E02): la nomenclatura solo nombra `episode_number`, y el
    nombre se quedaría con el primero."""
    if fila.get("instance_type") != TIPO or fila.get("item_type") != MediaKind.EPISODE.value:
        return None
    if len(_raw(fila).get("episodios") or []) < 2:
        return None
    return (f"«{fila.get('title')}» lleva varios capítulos en un fichero y el nombre solo tendría el primero: "
            "todavía no se puede subir.")


def errores_de_temporada(season: dict, series: dict | None, episodios: list[dict]) -> list[str]:
    """Lo que impide el pack de una temporada de carpeta: la serie sin identificar, o que falte alguno de los capítulos
    que dice TMDb (en una carpeta solo hay filas de lo que hay en el disco, así que nada más lo detecta)."""
    if season.get("instance_type") != TIPO:
        return []
    if not (series or {}).get("tmdb_id"):
        return ["Identifica la serie en TMDb antes de subir la temporada (viene de una carpeta manual)."]
    varios = [e for e in (error_de_varios_capitulos(fila) for fila in episodios) if e]
    if varios:
        return varios[:1]
    n = season.get("season_number")
    esperados = ((_raw(series).get("temporadas") or {}).get(str(n)) or {}).get("capitulos")
    presentes = {e for fila in episodios for e in (_raw(fila).get("episodios") or [fila.get("episode_number")]) if e}
    previstos = set(range(1, (esperados or 0) + 1))
    if previstos <= presentes:
        return []
    faltan = sorted(previstos - presentes)
    return [f"Temporada incompleta: hay {len(previstos & presentes)} de {esperados} capítulos según TMDb "
            f"(faltan {', '.join(str(e) for e in faltan[:20])})."]


def contar_por_identificar(conn, instance_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM library_items WHERE instance_id = ? AND tmdb_id IS NULL AND item_type IN ('movie', 'series')",
        (instance_id,),
    ).fetchone()[0]


def por_identificar(fila: dict) -> bool:
    return fila.get("instance_type") == TIPO and not fila.get("tmdb_id")


def sugerencias(conn, ids: list[int]) -> dict[int, dict]:
    """{id: sugerencia} de esas filas (la lista de la biblioteca no lee raw_json)."""
    if not ids:
        return {}
    filas = conn.execute(
        "SELECT id, item_type, instance_type, tmdb_id, raw_json FROM library_items WHERE id IN "
        "(SELECT value FROM json_each(?))", (json.dumps(ids),),
    ).fetchall()
    return {f["id"]: s for f in filas if (s := sugerencia_de(dict(f)))}


def sugerencia_de(fila: dict) -> Optional[dict]:
    if not por_identificar(fila) or fila.get("item_type") not in (MediaKind.MOVIE.value, MediaKind.SERIES.value):
        return None
    return _raw(fila).get("sugerencia") or None
