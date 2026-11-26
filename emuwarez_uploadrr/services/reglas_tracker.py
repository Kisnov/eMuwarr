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
eso. Lo que no tiene diálogo (todo el audio en `zxx`, como Flow) no tiene nada que doblar y pasa en todos. Pura: no lee
la base de datos; si es anime lo dice quien llama, con `es_anime`.
"""
from __future__ import annotations

import os
import sqlite3
from typing import NamedTuple, Optional

from .emu_format import _split_languages, has_castellano, solo_sin_dialogo
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
    if solo_sin_dialogo(item.get("audio_languages")):
        return Reglas(True, "Sin diálogo (zxx): no hay nada que doblar.", "anime" if anime else None)
    if tracker.id == EMU.id:
        return _reglas_emu(item)
    return _reglas_otro(tracker, item, anime)


def motivo_audio_sin_idioma(posiciones: list[int]) -> str:
    """Por qué no se sube con una pista de audio sin idioma (`posiciones`, desde 1; vacío si no se sabe cuál)."""
    cual = f" {', '.join(str(p) for p in posiciones)}" if posiciones else ""
    return (f"La pista de audio{cual} no tiene idioma (und): falta el dato, ponlo en el asistente de subida («Ver "
            "pistas»; en un pack, junto al campo Audio), o «Sin diálogo (zxx)» si de verdad no tiene diálogo.")


def aviso_no_mkv(ruta: str | None) -> Optional[str]:
    """El aviso de una pista sin idioma en un fichero que no es MKV, que no bloquea: la app solo etiqueta pistas de un
    MKV. None en un MKV o en un ISO (que no se sube: uploadrr_contract.motivo_formato_no_subible)."""
    ext = os.path.splitext(ruta or "")[1].lower()
    if ext in (".mkv", ".iso"):
        return None
    return (f"Es un {ext.lstrip('.').upper() or 'archivo sin extensión'}: la mayoría de trackers piden MKV. Conviértelo "
            "a MKV (mkvmerge lo hace sin recodificar) y podrás ponerles el idioma a las pistas en el asistente.")


def con_idiomas_elegidos(item: dict, *, overrides: dict | None = None, idiomas: dict | None = None) -> dict:
    """`item` con el audio que tendrá su fichero con los idiomas elegidos en «Ver pistas» (`overrides` por id de pista
    o `idiomas` por posición), que es lo que miran las normas. Sin nada elegido, o sin leer sus pistas, tal cual."""
    from .mkv_remux import idiomas_de_las_pistas, idiomas_elegidos, idiomas_por_posicion
    if not (idiomas_elegidos(overrides) or idiomas_por_posicion(idiomas)):
        return item
    from .path_mapping import resolve_local_path, ruta_de_lectura
    # El mismo fichero que lee «Ver pistas».
    ruta = ruta_de_lectura(item)
    pistas = idiomas_de_las_pistas(resolve_local_path(ruta) or ruta, overrides=overrides, idiomas=idiomas)
    return {**item, "audio_languages": " / ".join(dict.fromkeys(pistas))} if pistas else item


def es_anime(conn: sqlite3.Connection, item: dict) -> bool:
    """Si el elemento es anime: su instancia de Radarr/Sonarr está marcada como de anime (`instances.is_anime`), o
    tiene id de MyAnimeList, que solo se le pone si está en la tabla de anime de Fribb/anime-lists (anime_lists.py)."""
    if item.get("mal_id"):
        return True
    fila = conn.execute("SELECT is_anime FROM instances WHERE id = ?", (item.get("instance_id"),)).fetchone()
    return bool(fila and fila[0])


def reglas_para_subir(conn: sqlite3.Connection, item: dict, tracker_id: str, *, overrides: dict | None = None,
                      idiomas: dict | None = None) -> Reglas:
    """`reglas_de` para una subida de verdad: dice si es anime, y una temporada se mira capítulo a capítulo
    (season_pack.reglas_del_pack), con la variante del pack entero. Con los idiomas elegidos en «Ver pistas»
    (con_idiomas_elegidos; un pack, por posición en `idiomas`)."""
    if item.get("item_type") == "season":
        from .season_pack import reglas_del_pack
        return reglas_del_pack(item, tracker_id, conn, idiomas=idiomas)
    item = con_idiomas_elegidos(item, overrides=overrides, idiomas=idiomas)
    reglas = reglas_de(tracker_id, item, anime=obtener(tracker_id).id != EMU.id and es_anime(conn, item))
    sin_tipo = _motivo_sin_tipo(conn, item, tracker_id) if reglas.permitido else None
    return Reglas(False, sin_tipo, None) if sin_tipo else reglas


def _motivo_sin_tipo(conn: sqlite3.Connection, item: dict, tracker_id: str) -> Optional[str]:
    """Si el tracker no tiene el tipo de la copia (N.O.B.S y Milnueve no tienen «SD»), por qué no se sube ahí. El
    formato, el mismo de la comprobación «tipo»: el de su propuesta de nomenclatura o, sin ella, el que se le deducirá.
    El de un pack sale de su plan: ese se mira al correr el trabajo (upload_queue._paso_nombre)."""
    from .nomenclature import _infer_source_from_item, get_latest_nomenclature_proposal
    from .upload_preflight import formato_de_subida
    from .uploadrr_contract import motivo_sin_tipo
    propuesta = get_latest_nomenclature_proposal(item["id"], conn=conn) if item.get("id") else None
    return motivo_sin_tipo(tracker_id, formato_de_subida(item, propuesta) if propuesta else _infer_source_from_item(item))


def elegibilidad(conn: sqlite3.Connection, item: dict) -> dict:
    """Qué trackers de los que se puede subir (trackers.para_subir) admiten `item` por sus normas, y, de una temporada,
    cada capítulo por separado: el paso «Contenido» del asistente decide con esto si deja elegir el pack entero o
    capítulos sueltos, y el Resumen qué trackers marca de entrada. Las mismas reglas que se miran al encolar
    (reglas_para_subir), sin red.

    {"trackers": [{tracker, nombre, permitido, motivo, variante}], "capitulos": [{item_id, trackers: [...]}]}"""
    from .trackers import para_subir
    destinos = para_subir(conn)

    def de(fila: dict) -> list[dict]:
        return [{"tracker": t.id, "nombre": t.nombre, **reglas_para_subir(conn, fila, t.id)._asdict()}
                for t in destinos]

    capitulos: list[dict] = []
    if item.get("item_type") == "season":
        from .season_pack import _get_season_episodes
        capitulos = [{"item_id": ep["id"], "trackers": de(ep)} for ep in _get_season_episodes(item["id"], conn=conn)]
    return {"trackers": de(item), "capitulos": capitulos}


__all__ = ["VARIANTES", "Reglas", "reglas_de", "motivo_audio_sin_idioma", "aviso_no_mkv", "con_idiomas_elegidos",
           "es_anime",
           "reglas_para_subir", "elegibilidad"]
