# Plan: lo que queda pendiente

Solo lo que falta. Lo que ya está hecho (la revisión de octubre de 2026, las oleadas 3 y 4, la subida por lotes, el
rediseño de la interfaz…) se puede ver en el historial de commits y de PRs, y no se repite aquí. **Actualiza este
fichero en el mismo PR que cierra un punto: bórralo.**

## Por decidir

- **Subida por lotes:** si guardar los `campos` de un elemento y crear su trabajo van en una sola transacción. Se
  decide al probarla en el NAS.
- **Interfaz (auditoría con `impeccable`, #294):** quedaron sin arreglar dos detalles de tamaño:
  - las ayudas de frase entera a 11 px (Configuración, Tareas, la primera configuración y las comprobaciones del
    resumen del lote);
  - la caja interior de las tarjetas de Tareas.

## Por hacer: subir a más trackers (decidido el 18/11)

eMuwarr subirá también a otros trackers, no solo a eMuwarez. **Un solo repo con un adaptador por tracker**, no un clon:
lo valioso (cruce con Radarr/Sonarr, cola, enlaces duros, qBittorrent, Panel, avisos) no depende del tracker; por
tracker solo cambian la nomenclatura, los ids de categoría/tipo/resolución, las normas (idiomas, duplicados), la
descripción y el cliente.

- **Todo esto va en la rama `multitracker`**, no en `dev`: sus PR van contra ella, `dev` se fusiona en ella de vez en
  cuando (nunca rebase) y solo llega a `dev` cuando esté completo. Cada push construye `ghcr.io/kisnov/emuwarr:multitracker`
  para el NAS de Gabriel; `latest` y `dev` no cambian.
- **No se borran los trackers que no son EMU de `uploadrr-emu/src/trackers/`**: son la base de los adaptadores (allí ya
  están SP seedpool, RF reelflix, DARK darkpeers, OE onlyencodes, LT lat-team, TTR torrenteros, YU yu-scene,
  IHD infinityhd, RTF retroflix, TL TorrentLeech, HDT hd-torrents y `UNIT3D_TEMPLATE`).
- **La base ya está en `dev`** (#25, #27, #33, #34): una sola tabla de ids, un solo cliente UNIT3D (`cliente_unit3d.py`),
  el limitador de llamadas compartido entre la app y Uploadrr-EMU, y un config de Uploadrr-EMU por subida.
- **Por orden:**
  1. **Fase A:** el tracker pasa a ser un dato. Base de datos (cada subida, petición, catálogo y estadística lleva su
     tracker), ajustes por tracker (clave, usuario, grupos, anónimo), cola y Panel. Las URL de los tres son fijas, en
     `services/trackers.py` y la clase de Uploadrr-EMU: una errata no puede mandar la clave a otro sitio. eMuwarez
     queda como el primer adaptador, sin cambiar lo que ve hoy quien solo usa eMuwarez.
     - Ya está la base (esquema 63): `services/trackers.py` con eMuwarez (`emu`) y la columna `tracker` en las
       subidas, peticiones, catálogo, «Tus subidas» y el historial del perfil; las claves de peticiones, «Tus subidas»
       y el perfil incluyen el tracker. Las sincronizaciones de eMuwarez (peticiones, catálogo, «Tus subidas», perfil)
       solo escriben, borran y comparan filas de eMuwarez. La clave y el usuario de un tracker se leen con
       `settings.ajuste_de_tracker` (para eMuwarez, `emu.<nombre>`), nunca con la clave escrita a mano.
     - La cola, Peticiones y el Panel dicen de qué tracker es cada fila con un chip (`ChipTracker`), solo si no es
       eMuwarez: quien solo usa eMuwarez no ve nada nuevo.
     - Ya está el catálogo por tracker: cada sincronización (el botón y la completa semanal) lee eMuwarez y luego,
       de uno en uno, cada otro tracker con clave, con su clave y su cupo de la API (el limitador cuenta cada tracker en
       su fichero, el mismo que usa Uploadrr-EMU). El Resumen del asistente dice lo que ya hay en cada tracker elegido.
       Las columnas de la biblioteca (`emu_in_catalog`/`emu_aportes`) siguen siendo de eMuwarez. Falta probar en el
       NAS la primera lectura de N.O.B.S y Milnueve: los nombres de sus categorías de series
       (`emu_catalog._CATEGORIAS_SERIE`) salen de sus formularios, no de lo que devuelve su API.
     - «Subir por lotes» y «Subir también a…» miran el catálogo de cada tracker. La regla de duplicados y trumps es
       una sola y está en el servidor (`emu_catalog.conclusion_duplicados`, en cada comparación): el asistente, el
       lote, «Subir también» y la ficha de Biblioteca enseñan lo que dice, el navegador no la aplica. Cuenta la
       calidad y el audio: castellano que no tiene ninguno de los torrents es una mejora, aunque la calidad sea igual
       o peor. La columna de Biblioteca `emu_aportes` (su filtro «Aportas», el Panel y las peticiones) sale de la
       misma regla (`_aportes`): solo tiene algo si la conclusión es un trump (o, en una temporada que eMuwarez
       solo tiene en capítulos sueltos, si los tienes todos: el pack). Lo que un tracker ya tiene igual o mejor se
       salta solo para ese (en el lote, si lo tienen todos llega desmarcado y marcarlo a mano lo sube a
       todos), y un tracker sin catálogo leído no frena nada, solo se avisa. Biblioteca no: su chip y su filtro salen
       de `emu_in_catalog`, una columna de eMuwarez, y decir lo de los otros pediría una columna por tracker.
     - Ya están las peticiones por tracker: cada sincronización lee eMuwarez y luego cada otro tracker con clave, con
       su URL, su cupo y su tabla de ids (de los otros, solo las categorías de vídeo a las que se sube). El fallo de uno
       no frena a los demás ni borra sus peticiones, y solo un recorrido completo borra las que ya no están, del suyo.
       Su preparación mira el catálogo local y las normas de idioma de ese tracker, no las columnas de eMuwarez.
       Una subida solo se enlaza a una petición de su mismo tracker (la del asistente y la que un lote encuentra
       sola), y desde una petición de otro tracker el asistente propone subir a ese tracker.
     - «Tus subidas» y el perfil también leen cada tracker con clave, con su clave y su cupo: Estadísticas tiene unas
       «Tus subidas» por tracker y un selector para la evolución de la cuenta, el Panel una franja por tracker, y la
       moderación de una subida sale de la lectura de su tracker. Los avisos de la cuenta y los objetivos siguen
       siendo de eMuwarez.
  2. **Fase B: NOBS (nobs.rawsmoke.net) y Milnueve (tracker.milnueve.cc).** Los dos son UNIT3D y ya se sube a ellos con
     otras herramientas: se puede empezar (Gabriel les avisa). El primero valida la abstracción. Decidido el 22/11:
     - **Nomenclatura: la de eMuwarez, sin más.** Los dos son nuevos y cada uno sube como quiere; sus guías (formato
       `Título Año Fuente Resolución Idiomas Códec_audio`, con `ESP`/`ING`/`DUAL`/`VOSE`) se leen con flexibilidad.
       Solo cuenta el nombre del torrent (el campo `name` del formulario): ninguno de los dos exige nada al nombre del
       fichero ni de la carpeta (la FAQ de NOBS lo dice expresamente).
     - **Un solo fichero para los tres trackers, con cross-seed.** El mismo enlace de la carpeta de subidas, con el mismo
       nombre, para eMuwarez, NOBS y Milnueve: nada de un enlace ni un nombre por tracker. Así los tres torrents tienen
       los mismos ficheros (mismo `info.name`, mismos tamaños, mismo tamaño de pieza) y quien descargue de uno puede hacer
       cross-seed en los otros. Encaja con lo que ya hace Uploadrr-EMU: crea un `BASE.torrent` genérico (sin anuncio,
       `source` Uploadrr), lo sube a cada UNIT3D, descarga el torrent que devuelve cada tracker (con su anuncio y su
       `source`: otro infohash, mismos datos; NOBS pide justo eso, que se use su torrent) y lo añade a qBittorrent con la
       misma ruta. Cada tracker añadido no vuelve a hashear el fichero (un `BASE.torrent` para todos) ni ocupa disco.
     - **Las subidas que ya están en eMuwarez (unas 50) se suben también a NOBS y Milnueve, cross-seedables.** Sin
       volver a preparar nada: el fichero sigue en la carpeta de subidas sembrando, y el `BASE.torrent` sale del torrent
       de eMuwarez (`torrentman.from_existing`, que ya existe: cambia el anuncio y el `source` y conserva nombre, ficheros y
       piezas, sin hashear; el `tmp/<nombre>` original puede haberlo borrado el mantenimiento a los 7 días). Antes de cada una, buscar duplicados en ese tracker
       (Milnueve rechaza la misma calidad y fuente; NOBS, la misma obra con el mismo tipo y resolución) y comprobar que el
       fichero sigue ahí y completo en qBittorrent.
     - **Lo que sí exigen y hay que respetar** (detalle en `docs/trackers-ids.md`): el castellano va si existe el
       doblaje (sin él, Milnueve lo quiere en sus categorías Latino), pero Milnueve también acepta contenido solo en
       catalán (tiene «Anime en Catalan»): la regla de idioma de eMuwarr no se le aplica tal cual; descripción con MediaInfo y capturas (NOBS: mínimo 4,
       la configuración del encode si es un encode y el tráiler en películas); en NOBS el tipo sale del MediaInfo, no del
       nombre (un WEB-DL que pasó por Tdarr/HandBrake/ffmpeg es WEBRip), y puede no coincidir con el que se usó en
       eMuwarez.
     - Ya está: los adaptadores de Uploadrr-EMU (`-tk NOBS` / `-tk MNV`), sus claves en Configuración y la subida desde
       el Resumen del asistente: se eligen los trackers con clave y sale un trabajo por
       tracker (`upload_jobs.tracker`), con el mismo nombre y el mismo fichero. El segundo espera a que acabe el primero,
       reutiliza su preparación y lanza Uploadrr-EMU con `--resume` (mismo BASE.torrent: mismas piezas). «Reenviar a
       qBittorrent» pide el torrent al tracker de la subida.
     - Ya está (PR 4): «Subir también a…» una subida completada con eMuwarr a cualquier otro tracker con clave (las ~50
       de eMuwarez a N.O.B.S y Milnueve, o de uno de ellos a eMuwarez), desde el detalle de una subida o, varias elegidas
       a mano, desde Subidas (un trabajo por subida y tracker, en un lote). Mismo fichero y mismo nombre; si el
       `tmp/<nombre>` ya no tiene el BASE.torrent, Uploadrr-EMU lo hace con el torrent del otro tracker
       (`--base-desde <TK>:<id>`): mismas piezas, sin hashear. Antes, qBittorrent tiene que tener ese torrent entero con
       el fichero como contenido. Las subidas a mano quedan fuera.
     - Ya están sus normas de idioma y categoría (`services/reglas_tracker.py::reglas_de`): eMuwarez como siempre;
       N.O.B.S admite castellano, latino o VOSE, y Milnueve además el catalán. El anime (instancia marcada de anime o
       con id de MyAnimeList) va a sus categorías de anime; en Milnueve, sin castellano, el anime con catalán va a
       «Anime en Catalan» y lo que tiene latino a las Latino. Llega a Uploadrr-EMU como `--variante`, que la tabla
       del tracker convierte en el id. VHS/Retro y Documentales no se eligen solas. La subida por tracker ya las usa:
       las comprobaciones van con las del primer tracker elegido, el Resumen dice las de cada otro, un pack las mira
       capítulo a capítulo, lo que un tracker no admite no se encola para él y su trabajo lanza con su `--variante`.
       Con más de un tracker con clave, el paso «Contenido» del asistente deja elegir el pack o los capítulos que
       admite alguno (`/upload/elegibilidad`), dice cuáles, y el Resumen marca de entrada eMuwarez si lo admite y, si
       no, los que lo admiten; el plan del pack usa las normas del primero de ellos.
     - Queda: probarlo en el NAS (una subida vieja a N.O.B.S: los dos torrents sembrando el mismo fichero sin comprobar
       datos).
     - Queda: probar en el NAS un lote y una tanda con lo que solo admite Milnueve (una temporada de anime solo en
       catalán y una en castellano, a eMuwarez y Milnueve).
  3. Después, los internacionales que ya tienen código en `uploadrr-emu/`, solo para contenido con el audio en inglés y
     donde haya permiso.
     - Con 20 trackers o más, el Panel no puede enseñarlos todos: elegir qué trackers salen en el Panel (`Cuentas`
       ya recibe la lista a pintar). El resto, en Estadísticas, en tarjetas con más información; al abrir una, el
       detalle completo de ese tracker: todas sus estadísticas y las subidas hechas a él, con póster.
- **Por estudiar: nekoBT (nekobt.to; añadido y estudiado el 22/11).** Tracker público de anime centrado en fansubs y
  grupos, en open beta y en pie (≈ 9.500 usuarios y 47.000 torrents). Iría después de la fase B, con adaptador propio:
  - **No es UNIT3D:** `/api/torrents/filter` da un 404 HTML; tiene su propia API JSON en `https://nekobt.to/api/v1`
    ([documentación](https://wiki.nekobt.to/technical-details/json/)) y Torznab (`/api/torznab/api`). No sirven ni
    `cliente_unit3d.py` ni `UNIT3D_TEMPLATE`: hace falta un cliente nuevo, con su propio límite de llamadas (no es
    público; un 429 trae `retry_after`).
  - **Autenticación:** una clave de API generada en los ajustes de la cuenta, enviada como cookie `ssid` con la clave
    como valor ([API Keys](https://wiki.nekobt.to/technical-details/api-keys/)). Usar la cookie de la sesión en vez de
    una clave está castigado. Sin clave, `POST /api/v1/upload` → 401 JSON `{"message":"Unauthorized.","error":true}`.
  - **Subida:** `PUT /api/v1/upload/checks` (recomendado: corrige y analiza el título, devuelve avisos y fallos) y
    `POST /api/v1/upload` con JSON: `torrent` (el .torrent en base64), `title` (≤ 512), `category` (solo 1),
    `video_type`, `video_codec`, `level` (nivel de subtítulos, −1 a 3), `mtl`, `otl`, `hardsub`, `anonymous`, `hidden`,
    `primary_group` (id y miembros con su rol), `secondary_groups`, `audio_langs`, `sub_langs`, `fansub_langs` (códigos
    separados por comas, de `GET /api/v1/langs`: hay `es-es`, `es-419`, `ca`, `eu`, `gl`), `description` (Markdown,
    ≤ 8192), `mediainfo` e `ignore_warnings`. Responde `data.id`, o `fails`/`warns`. Los ids de tipo de vídeo (14 BD
    Remux, 13 BD Encode, 9 WEB-DL, 8 WEB Encode…) y de códec (1 H264, 2 H265, 3 AV1…) están en
    [Metadata](https://wiki.nekobt.to/info/metadata/).
  - **El .torrent es otro:** tiene que llevar su tracker público (`https://tracker.nekobt.to/api/tracker/public/announce`)
    y al menos uno de respaldo ([Uploading](https://wiki.nekobt.to/info/uploading/)), nunca el anuncio privado de otro
    tracker. La primera subida de la cuenta, o una cuyo título no reconoce, espera a que la apruebe el staff (hasta 24 h).
  - **Normas** ([Rules](https://wiki.nekobt.to/rules/)): solo anime (con ficha en AniDB, AniList o MAL; nada de imagen
    real); títulos en inglés o romaji; metadatos correctos (tipo, códec, nivel de subtítulos, idiomas); nada de
    codificación por hardware (NVENC, QuickSync); automatizar solo con clave de API y sin spam (no piden permiso previo,
    pero conviene avisar al staff); para subir hay que aprobar antes un examen. No prohíben el audio ni los subtítulos
    en español: se declaran con sus códigos de idioma.
  - **Identificación y nombre:** no pide ids de TMDb, IMDb, AniDB ni MAL. Deduce la serie o película del título con
    Sonarr/Radarr/AniList, así que el nombre tiene que seguir la nomenclatura de anime (`[Grupo] Título - 01 [...]`), y la
    etiqueta del título tiene que ser un grupo registrado en nekoBT, enviado como `primary_group`
    ([Groups](https://wiki.nekobt.to/info/groups/)).
  - **Encaje con eMuwarr:** peor que los UNIT3D: solo la parte de anime de la biblioteca, una nomenclatura propia, un
    .torrent público aparte y campos que eMuwarr hoy no conoce (nivel de subtítulos, MTL/OTL, grupos con roles).
- **Esperando permiso:** Torrentland (torrentland.li): su API es solo para staff y uploaders (con el rango), y Gabriel aún
  no lo tiene; con él iría por API como N.O.B.S y Milnueve. Rellenar su formulario desde el navegador está permitido (no
  toca la API), pero se descarta: frágil y distinto del resto. HDZero (hdzero.org, permiso improbable). Los dos son UNIT3D
  (`/api/torrents/filter` anónimo → 401 JSON, comprobado el 18/11).
- **Por resolver en la fase A:** de dónde salen los ids de cada tracker (su formulario de subida o el staff; los de
  NOBS y Milnueve están en `docs/trackers-ids.md`), una nomenclatura por tracker (para NOBS y Milnueve vale la de
  eMuwarez; el fichero es el mismo para todos, ver la fase B), y si la app cambia de nombre (hoy «eMuwarr» es de eMuwarez).
- **Después de la 2.0 (aprobado):** al subir a otro tracker algo ya subido, reutilizar las capturas del host de imágenes en
  vez de generarlas y subirlas otra vez; hoy se pierden con `tmp/<nombre>`, que el mantenimiento borra a los 7 días, y con él
  su `meta.json` (`image_list`). Vías: que el mantenimiento conserve `meta.json`, o que la app guarde las URL con la subida.
  Antes, ver si Uploadrr-EMU usa las URL sin las imágenes locales (`src/screenshots/generator.py`, `manager.py`).

## Por hacer: subir imágenes de disco (ISO)

Los trackers UNIT3D admiten un ISO, pero desde la app todavía no se puede subir: Uploadrr-EMU solo reconoce un disco
como carpeta (`BDMV`, `VIDEO_TS`, `HVDVD_TS`) y, como fichero suelto, solo MKV, MP4 y TS. La comprobación «formato» lo
para antes de preparar nada. Para hacerlo falta ver:

- qué tipo y qué ids piden eMuwarez, N.O.B.S y Milnueve para un disco (`uploadrr-emu/data/trackers/*.json`,
  `docs/trackers-ids.md`);
- de dónde salen el MediaInfo (o el BDInfo) y las capturas de un `.iso`, que no se abre como un vídeo;
- cómo se llama el release, y qué hace la app con sus pistas, que no puede etiquetar;
- una subida real en el NAS con un ISO.

## Por probar en el NAS (`:multitracker`)

- La carpeta manual: escanear una carpeta real con WEB-DL de películas y de series, identificar en TMDb (una a
  una y las sugeridas de una vez) y subir una película y un pack de temporada.
- El límite de «Subidas en paralelo» compartido entre la CLI y el servidor (`flock` sobre `CONFIG_DIR/plazas`).
- El límite de peticiones a eMuwarez compartido entre el servidor, la CLI y cada Uploadrr-EMU (`flock` sobre
  `CONFIG_DIR/limitador`): con un catálogo largo en marcha y dos subidas a la vez no debería llegar ningún 429, y una
  espera larga sale en el registro de la subida («límite de peticiones por minuto de la API; espera de N s»). Si el
  registro de la subida o `app.log` dicen que el límite compartido «no está disponible», `flock` no funciona en ese volumen.
- El campo y el enlace de qBittorrent.
- La subida por lotes: un duplicado real en Uploadrr-EMU, 50 hilos en el NAS y la rejilla con 200 seleccionadas.
- «Reenviar a qBittorrent».
- Las estadísticas del tracker por subida: los nombres de los atributos son los de UNIT3D y no se han probado contra
  el eMuwarez real.

## Por ver con dispositivos reales

Todo lo de interfaz se ha comprobado en Chromium (con emulación táctil), no en dispositivos:

- Lector de pantalla.
- iOS/Safari, Android, tableta y Firefox.
- El Panel a 2000 px.
- La banda del Panel con más de cuatro instancias caídas y el tour abierto.

## Por hacer: repositorio

- **Imagen de vista previa** del repo (Settings → General → Social preview, 1280×640): la que sale al compartir el
  enlace. Aplazada el 17/11.

## Descartado (para no volver a proponerlo)

- Una etiqueta de qBittorrent por tracker: qBittorrent ya filtra por tracker (el dominio del anuncio), y con la
  etiqueta general basta.
- Que el filtro «Petición» de Biblioteca cuente la petición heredada: una serie pedida entera se sube en season
  packs, nunca por capítulos.
- Detectar el toque real con `pointerType` para los objetivos táctiles.
- Subir los documentales a la categoría Documentales (4) de eMuwarez: Prowlarr la mapea a TV/Documentary (5080), así
  que Radarr y Sonarr no la buscan por defecto, y mezcla películas y series. Siguen yendo a Películas o Series (en
  eMuwarez hay muy pocos en Documentales, y una serie documental subida como serie no tuvo pegas; decidido el 18/11).

## Criterios de diseño

- Se mantiene el diseño original; solo se mejora para ver mejor el contenido nuevo.
- Esquinas redondeadas (la esquina cortada, demasiado contrastada, no).
- Compacto, pero solo un poco y sin regla fija: si dar aire mejora la lectura, se da. Se queda Inter.
- Cada pantalla nueva pasa por la skill `frontend-design`.

## Datos útiles

- `/data` del NAS es btrfs y reflink funciona.
- Los nombres de la biblioteca no cambian fuera de la carpeta de subidas. Las subidas van a `UPLOADRR_UPLOADS_DIR`
  con nombre de eMuwarez: enlace duro, o copia reflink cuando hay que editar un fichero compartido (salvo que el
  ajuste lo prohíba).
- Cleanuparr «unlinked» solo vigila las categorías de radarr y sonarr.
