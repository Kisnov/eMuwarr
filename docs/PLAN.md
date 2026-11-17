# Plan: lo que queda pendiente

Solo lo que falta. Lo que ya está hecho (la revisión de octubre de 2026, las oleadas 3 y 4, la subida por lotes, el
rediseño de la interfaz…) se puede ver en el historial de commits y de PRs, y no se repite aquí. **Actualiza este
fichero en el mismo PR que cierra un punto: bórralo.**

## Por decidir

- **Subida por lotes:** si guardar los `campos` de un elemento y crear su trabajo van en una sola transacción. Se
  decide al probarla en el NAS.
- **Interfaz (auditoría con `impeccable`, #294):** quedaron sin arreglar tres detalles de tamaño:
  - las ayudas de frase entera a 11 px (Configuración, Tareas, la primera configuración y las comprobaciones del
    resumen del lote);
  - el nombre del torrent a 9 px en el asistente a 390 px;
  - la caja interior de las tarjetas de Tareas.

## Por hacer: revisión de diseño con `impeccable critique` (17/11)

Panel, asistente de subida y Biblioteca: 28/40 en las heurísticas de Nielsen. Los informes completos quedan en
`.impeccable/critique/` (sin versionar), y `/impeccable polish` los lee. Por orden:

- **El paso 1 del asistente** pinta seis valores derivados (Año, Resolución, Temporada/Episodio, Códec, Audio,
  Subtítulos) como `input disabled` al 50 % de opacidad: pasarlos a filas de solo lectura y dejar arriba los editables
  (Título, Source, Tag). `asistente/PasoArchivo.tsx` y `PasoTemporada.tsx`.
- **En el móvil**, cada carril del Panel tiene su propio scroll al 70 % del alto (`panel.css`): dejarlos a su alto
  natural. El nombre del torrent a 9 px de «Por decidir» es parte de lo mismo.
- **«Todo en orden»** del Panel no mira si eMuwarez o TMDb responden (`avisosDeAtencion` en `utils/hoy.ts`): con la
  clave caducada tranquiliza y la subida se bloquea después.
- **Menores:** «Panel» sale dos veces (barra superior y h1); las cifras de la franja no enlazan a su sección; la ruta
  solo se ve tras «Ver ruta»; «ideas» en la barra de selección; tres líneas con una palabra suelta.

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
