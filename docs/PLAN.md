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
- **Por orden:**
  1. Lo que sí queda del «paso 2» de la unión con Uploadrr-EMU, pensado ya para varios trackers: una sola tabla de ids,
     un solo cliente UNIT3D, el limitador de llamadas compartido entre la app y el subproceso (uno por tracker) y la
     configuración por subida en vez del `config.yaml` compartido.
  2. **Fase A:** el tracker pasa a ser un dato. Base de datos (cada subida, petición, catálogo y estadística lleva su
     tracker), ajustes por tracker (URL, clave, usuario, grupos, anónimo), cola y Panel. eMuwarez queda como el primer
     adaptador, sin cambiar lo que ve hoy quien solo usa eMuwarez.
  3. **Fase B: NOBS (nobs.rawsmoke.net) y Milnueve (tracker.milnueve.cc).** Los dos son UNIT3D y ya se sube a ellos con
     otras herramientas: se puede empezar (Gabriel les avisa). El primero valida la abstracción.
  4. Después, los internacionales que ya tienen código en `uploadrr-emu/`, solo para contenido con el audio en inglés y
     donde haya permiso.
- **Esperando permiso:** Torrentland (torrentland.li; no acepta automatizaciones: hace falta el OK del staff por
  escrito antes de subir por API) y HDZero (hdzero.org, permiso improbable). Los cuatro son UNIT3D (`/api/torrents/filter`
  anónimo → 401 JSON, comprobado el 18/11).
- **Por resolver en la fase A:** de dónde salen los ids de cada tracker (su formulario de subida o el staff), una
  nomenclatura por tracker y un nombre de fichero por tracker (un enlace duro por tracker en la carpeta de subidas, sin
  ocupar más), etiqueta/categoría de qBittorrent por tracker, peticiones y catálogo por tracker sin pasar del límite de
  cada API, y si la app cambia de nombre (hoy «eMuwarr» es de eMuwarez).

## Por probar en el NAS (`:dev`)

- El límite compartido entre la CLI y el servidor (`flock` sobre el volumen de `CONFIG_DIR`).
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

- Que el filtro «Petición» de Biblioteca cuente la petición heredada: una serie pedida entera se sube en season
  packs, nunca por capítulos.
- Detectar el toque real con `pointerType` para los objetivos táctiles.
- Subir los documentales a la categoría Documentales (4) de eMuwarez: Prowlarr la mapea a TV/Documentary (5080), así
  que Radarr y Sonarr no la buscan por defecto, y mezcla películas y series. Siguen yendo a Películas o Series, como
  ya hace el staff (decidido el 18/11).

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
