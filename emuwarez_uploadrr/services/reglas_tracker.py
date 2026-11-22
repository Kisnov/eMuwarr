"""Las normas de idioma y de categoría de cada tracker (fase B del plan multitracker, `docs/PLAN.md`).

`reglas_de(tracker, item, anime=...)` dice si un elemento de la biblioteca se puede subir a ese tracker por sus idiomas,
por qué, y la variante de categoría que le toca. La variante va a Uploadrr-EMU como `--variante` y allí la tabla
`data/trackers/<TRACKER>.json` la convierte en el id (token `MOVIE_ANIME`, `TV_LATINO`…); None es la categoría de
siempre, Películas o Series.

- eMuwarez: la regla de siempre, castellano en el audio o, si no, en los subtítulos. Nunca lleva variante.
- N.O.B.S: castellano o latino en el audio, o subtítulos en español (VOSE). El anime va a sus categorías de anime.
- Milnueve: lo mismo y además el catalán (o valenciano) en el audio, sin exigir el castellano. Sin audio en castellano,
  el anime con catalán va a «Anime en Catalan» y lo que tiene latino a sus categorías Latino. VHS/Retro y Documentales
  no se eligen solas.

El castellano se pide en las dos guías cuando existe el doblaje, pero la app no puede saber si existe: no bloquea por
eso. Pura: no lee la base de datos; si es anime lo dice quien llama, con `es_anime`.
"""
from __future__ import annotations

import sqlite3
from typing import NamedTuple, Optional

from .emu_format import _split_languages, has_castellano
from .trackers import EMU, MNV, obtener

# Las que entiende `--variante` de Uploadrr-EMU (uploadrr-emu/src/args.py).
VARIANTES = ("anime", "latino", "anime_catalan")


class Reglas(NamedTuple):
    permitido: bool
    motivo: str
    variante: Optional[str]  # None: la categoría normal (Películas / Series)


def _short_langs(value: Optional[str], limit: int = 3) -> str:
    """Los primeros idiomas de una lista "spa/eng/fre/…" y cuántos quedan.

    Se repiten los duplicados que trae el fichero (dos pistas en inglés
    llegan como "eng/eng"), así que se colapsan: nombrar "eng, eng, ara" no
    dice nada que "eng, ara" no diga ya.
    """
    vistos: list[str] = []
    for part in str(value or "").split("/"):
        part = part.strip()
        if part and part not in vistos:
            vistos.append(part)
    if not vistos:
        return "sin detectar"
    if len(vistos) <= limit:
        return ", ".join(vistos)
    return f"{', '.join(vistos[:limit])} y {len(vistos) - limit} más"


def _reglas_emu(item: dict) -> Reglas:
    # eMuwarez wants Castilian present, dubbed or at least subtitled. The audio alone is enough: a film in Castilian
    # needs no subtitles. Without Castilian audio, Castilian subtitles are what makes it acceptable. Same rule a season
    # pack applies per episode (services/season_pack.py::_spanish_errors), which is why it reuses the same predicate.
    #
    # Unknown blocks: an item whose MediaInfo was never read can't be confirmed either way, and "we didn't look" is
    # not evidence that the rule is met.
    audio_langs = item.get("audio_languages")
    sub_langs = item.get("subtitle_languages")
    has_audio_es = has_castellano(audio_langs) == 1
    has_subs_es = has_castellano(sub_langs) == 1
    if has_audio_es:
        motivo = "Audio en castellano. No son obligatorios subtítulos en castellano."
    elif has_subs_es:
        # Sin la lista de subtítulos: un release puede traer 37 pistas y volcarlas aquí no dice nada que el mensaje no
        # diga ya. El audio sí se nombra, porque ahí lo que importa es en qué idioma está.
        motivo = f"Sin audio en castellano ({audio_langs or 'desconocido'}), pero subtítulos en castellano."
    else:
        # Aquí la comprobación FALLA y bloquea la subida, así que saber qué idiomas hay ayuda a decidir qué hacer. Pero
        # un release puede traer 37 pistas de subtítulos y volcarlas enteras convierte el aviso en un muro: se nombran
        # las primeras y se cuenta el resto.
        motivo = (
            f"Ni el audio ({_short_langs(audio_langs)}) ni los subtítulos "
            f"({_short_langs(sub_langs)}) están en castellano."
        )
    return Reglas(has_audio_es or has_subs_es, motivo, None)


def _reglas_otro(tracker, item: dict, anime: bool) -> Reglas:
    audio_langs = item.get("audio_languages")
    sub_langs = item.get("subtitle_languages")
    audio = _split_languages(audio_langs)
    subs = _split_languages(sub_langs)
    acepta_catalan = tracker.id == MNV.id
    castellano = "ESP" in audio
    latino = "LAT" in audio
    catalan = acepta_catalan and ("CAT" in audio or "VAL" in audio)
    vose = "ESP" in subs or "LAT" in subs

    if not (castellano or latino or catalan or vose):
        idiomas = "castellano, latino ni catalán" if acepta_catalan else "castellano ni latino"
        return Reglas(False, (
            f"{tracker.nombre} no lo admite: el audio ({_short_langs(audio_langs)}) no está en {idiomas}, "
            f"y los subtítulos ({_short_langs(sub_langs)}) no están en español."), None)

    if castellano:
        motivo = "Audio en castellano."
    elif latino:
        motivo = "Audio en latino, sin castellano."
    elif catalan:
        motivo = "Audio en catalán, sin castellano."
    else:
        motivo = f"Sin audio en español ({_short_langs(audio_langs)}), pero subtítulos en español (VOSE)."

    variante = "anime" if anime else None
    if acepta_catalan and not castellano:
        # Las categorías por idioma de Milnueve ganan a las de anime: allí el latino va aparte también en el anime.
        if anime and catalan:
            variante = "anime_catalan"
        elif latino:
            variante = "latino"
    return Reglas(True, motivo, variante)


def reglas_de(tracker_id: str, item: dict, *, anime: bool = False) -> Reglas:
    """Si `item` (una fila de library_items, con sus `audio_languages` / `subtitle_languages`) se puede subir a ese
    tracker, por qué, y su variante de categoría. Un tracker desconocido es un ValueError (trackers.obtener)."""
    tracker = obtener(tracker_id)
    if tracker.id == EMU.id:
        return _reglas_emu(item)
    return _reglas_otro(tracker, item, anime)


def es_anime(conn: sqlite3.Connection, item: dict) -> bool:
    """Si el elemento es anime: su instancia de Radarr/Sonarr está marcada como de anime (`instances.is_anime`), o
    tiene id de MyAnimeList, que solo se le pone si está en la tabla de anime de Fribb/anime-lists (anime_lists.py)."""
    if item.get("mal_id"):
        return True
    fila = conn.execute("SELECT is_anime FROM instances WHERE id = ?", (item.get("instance_id"),)).fetchone()
    return bool(fila and fila[0])


def reglas_para_subir(conn: sqlite3.Connection, item: dict, tracker_id: str) -> Reglas:
    """`reglas_de` para una subida de verdad: dice si es anime, y una temporada se mira capítulo a capítulo
    (season_pack.reglas_del_pack), con la variante del pack entero."""
    if item.get("item_type") == "season":
        from .season_pack import reglas_del_pack
        return reglas_del_pack(item, tracker_id, conn)
    return reglas_de(tracker_id, item, anime=obtener(tracker_id).id != EMU.id and es_anime(conn, item))


__all__ = ["VARIANTES", "Reglas", "reglas_de", "es_anime", "reglas_para_subir"]
