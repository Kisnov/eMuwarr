# Ids de UNIT3D de NOBS y Milnueve (fase B)

Recogidos el 22/11/2026 de los formularios de subida (`/torrents/create`) con la sesión de Gabriel. Comparados con la
tabla de eMuwarez (`uploadrr-emu/data/trackers/EMU.json`, que lee `src/utils/ids_tracker.py`) y con la tabla genérica de
`uploadrr-emu/src/trackers/UNIT3D.py` (la que hereda `UNIT3D_TEMPLATE.py`). Los dos son UNIT3D: `/api/torrents/filter`
sin clave → 401 JSON `{"message":"Unauthenticated."}`.

Si el formulario cambia, estos ids dejan de valer: se vuelven a leer de las opciones de `category_id`, `type_id` y
`resolution_id` (y del objeto `cats` de Alpine para saber de qué clase es cada categoría).

## Categorías

La «clase» es la que el formulario usa para mostrar campos (`cats[cat].type`): `movie` y `tv` piden resolución,
`tv` además temporada y episodio.

| Id | eMuwarez | NOBS | Milnueve |
|---:|---|---|---|
| 1 | Películas (movie) | Movies (movie) | Películas (movie) |
| 2 | Series (tv) | TV (tv) | Series (tv) |
| 3 | music (heredado, no se usa) | Arcade (game) | Películas Anime (movie) |
| 4 | Documentales (solo etiqueta) | Anime Movies (movie) | Series Anime (tv) |
| 5 | — | Anime TV Shows (tv) | Música (no) |
| 6 | — | — | Manga (no) |
| 7 | — | E-Books (book) | VHS / Retro (movie) |
| 8 | — | Audiobooks (audiobook) | Anime en Catalan (tv) |
| 9 | — | Consolas (game) | Zona Documentales (movie) |
| 10 | — | PC (game) | Peliculas Latino (movie) |
| 11 | — | — | Libros (no) |
| 12 | — | — | Cómics (no) |
| 13 | — | — | Juegos/ROMSets (game) |
| 14 | — | — | Series latino (tv) |

Diferencias que importan:

- 1 y 2 coinciden en los tres. eMuwarez no tiene categoría de anime; NOBS (4, 5) y Milnueve (3, 4) sí: hará falta
  decidir el anime por ítem (Sonarr/Radarr ya saben si una biblioteca es de anime: `instances.is_anime`).
- Milnueve tiene categorías por idioma u origen: «Anime en Catalan» (8, clase tv: una película enviada allí se
  rechaza por no llevar temporada ni capítulo, así que las películas solo en catalán no se suben por ahora), «Peliculas Latino» (10), «Series latino» (14), «VHS / Retro» (7) y «Zona Documentales» (9). Cuál toca
  depende del audio y del origen, no solo de película/serie: preguntar al staff cuándo se usa cada una.
- El 3 de la tabla genérica (`music`) en NOBS es Arcade y en Milnueve Películas Anime: el adaptador no puede heredar
  ese token.

## Tipos

| Id | eMuwarez | NOBS | Milnueve |
|---:|---|---|---|
| 1 | Full Disc | Full Disc | Full Disc |
| 2 | Remux | Remux | Remux |
| 3 | Encode | Encode | Encode |
| 4 | WEB-DL | WEB-DL | WEB-DL |
| 5 | WEBRIP | WEBRip | WEBRip |
| 6 | HDTV | HDTV | HDTV |
| 7 | SD | EPUB (libro) | — |
| 8 | STEAMRIP | PDF (libro) | — |

Los tipos de vídeo (1-6) coinciden en los tres. NOBS sigue con tipos de libro (7-11: EPUB, PDF, MOBI, AZW3,
CBZ/CBR), audiolibro (12 M4B, 13 MP3) y juego (14-49: ScummVM, ROM, PC, PS1… Atari 2600), que el formulario solo
ofrece con su categoría (`data-meta`). eMuwarez tiene un tipo SD (7) que no existe en ninguno de los dos: allí el SD
va como Encode/WEB-DL/… con su resolución.

## Resoluciones

| Id | eMuwarez | NOBS y Milnueve (iguales) | Tabla genérica `UNIT3D.py` |
|---:|---|---|---|
| 1 | 4320p | 4320p | 4320p |
| 2 | 2160p | 2160p | 2160p |
| 3 | 1080p (y 1440p) | 1080p | 1080p, 1440p |
| 4 | 1080i | 1080i | 1080i |
| 5 | 720p | 720p | 720p |
| 6 | 576p (y 576i) | 576p | 576p |
| 7 | 540p | 576i | 576i |
| 8 | 480p (y 480i) | 480p | 480p |
| 9 | — | 480i | 480i |
| 10 | Otras | Other | 8640p y por defecto |

NOBS y Milnueve siguen la tabla de UNIT3D de serie: 576i y 480i tienen id propio (7 y 9) y no hay 540p. Con la tabla
de eMuwarez un 576i iría como 576p y un 540p como 576i: cada tracker necesita su `data/trackers/<TRACKER>.json`. El
1440p no tiene id en ninguno de los dos (la tabla genérica lo manda al 3, 1080p).

## Campos del formulario

Los dos formularios tienen los mismos campos, los de UNIT3D de serie. Obligatorios: `torrent`, `name`, `category_id`,
`type_id`, `description`; `resolution_id` si la categoría es movie o tv; `season_number` y `episode_number` si es tv.
Opcionales: `nfo`, portada y banner (solo categorías sin TMDb), `distributor_id` y `region_id` (solo Full Disc),
`tmdb`, `imdb`, `tvdb`, `mal`, `igdb`, `keywords`, `mediainfo`, `bdinfo`, anónimo y `personal_release`. NOBS añade
`isbn13` y `asin` para libros y audiolibros.

No hay ningún campo obligatorio que el payload de `UNIT3D.py` no envíe ya (`name`, `description`, `mediainfo`,
`category_id`, `type_id`, `resolution_id`, `tmdb`, `imdb`, `tvdb`, `mal`, `keywords`, `season_number`,
`episode_number`, `region_id`/`distributor_id` si los hay). Lo que eMuwarez no tiene y estos dos sí:

- **`mal`** (id de MyAnimeList): el formulario lo ofrece en los dos; para las categorías de anime convendría
  enviarlo. eMuwarr hoy no lo guarda.
- **Las categorías de anime y las de Milnueve por idioma/origen** (arriba): la categoría ya no sale solo de
  película/serie.
- **`isbn13`/`asin`** (NOBS, libros y audiolibros): fuera del alcance de eMuwarr.

## Normas de subida que afectan a eMuwarr

Resumen de sus guías (las pasó Gabriel el 22/11; NOBS: [Normas](https://nobs.rawsmoke.net/pages/1),
[FAQ](https://nobs.rawsmoke.net/pages/2), [Guía de subida](https://nobs.rawsmoke.net/wikis/2),
[Nombrado](https://nobs.rawsmoke.net/wikis/3); Milnueve: su guía de subida y nombrado y sus normas).

- **Nombre:** los dos piden `Título Año [SXX[EXX]] Fuente Resolución [Códec] [Bits] [HDR] Idiomas Códec_audio [SUB]`,
  con el título de distribución en España, idiomas con tres letras (`ESP`, `LAT`, `ING`, `JAP`…), `DUAL` si el
  castellano y la VO van con el mismo códec, `VOSE`/`VO` sin doblaje. Decisión del 22/11: se sube con la nomenclatura
  de eMuwarez, porque en la práctica nadie sigue una norma fija. Solo cuenta el nombre del torrent: no el del fichero
  ni el de la carpeta (FAQ de NOBS).
- **Audio:** las guías piden el castellano cuando existe el doblaje (mínimo recomendado castellano + VO). Sin doblaje
  castellano se acepta el latino (en Milnueve, en sus categorías Latino, 10 y 14) y la VOSE. Milnueve acepta además
  contenido solo en catalán (categoría «Anime en Catalan», 8; Gabriel, 22/11): allí no se exige el castellano.
- **Calidad:** Milnueve recomienda 1080p, 720p solo si no hay 1080p y SD solo para DVD o casos justificados. NOBS no
  pone mínimo en películas, pero en series pide 720p (1080p si existe). Ninguno acepta CAM/TS/TC ni reescalados.
- **Duplicados:** Milnueve rechaza la misma calidad y fuente si no mejora nada; en NOBS es duplicado la misma obra con
  el mismo tipo y la misma resolución ([Missing](https://nobs.rawsmoke.net/missing)), salvo que mejore claramente.
- **Tipo en NOBS:** sale del MediaInfo, no del nombre. WEB-DL = el flujo del servicio sin recodificar (reempaquetar
  con mkvmerge sigue siéndolo); WEBRip = cualquier recodificación de una fuente web, incluido un WEB-DL que pasó por
  Tdarr, HandBrake o ffmpeg (firma de encoder en la pista de vídeo). En octubre corrigieron 327 torrents por esto.
- **Descripción:** Milnueve, sinopsis/formato/calidad y capturas, nunca vacía. NOBS, MediaInfo completo, mínimo 4
  capturas (≥ 1280×720), la configuración del encode si es un encode y el tráiler en películas; el id de TMDb.
- **Torrent:** NOBS pide usar el torrent que devuelve el tracker (con su entropía), no el propio, y no distribuirlo
  fuera. Milnueve: ningún requisito sobre el fichero; el nombre del `.torrent` con puntos es solo una convención.
- **Seeding:** los dos con H&R de 96 h en 14 días (a partir del 50 % descargado); NOBS, ratio mínimo 0,4.
