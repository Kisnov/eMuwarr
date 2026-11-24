import { api } from './client'
import type {
  UploadJob,
  UploadJobLogsResponse,
  UploadStartRequest,
  UploadStartResult,
  OkResponse,
  LoteResumen,
  LoteAccionResult,
  LoteBorrarResult,
  TrailerEstado,
} from '../types/api'
import type { NomenclatureFields, TrackRemuxOverride } from './nomenclature'
import { latestInChain } from '../utils/uploadStatus'

export interface UploadQueueResult {
  status: string
  job: UploadJob | null
  errors: string[]
}

// Queues "Subir a eMuwarez": the job appears immediately in Subidas as
// 'queued' with a grace period (see services/upload_queue.py) before the
// server actually renames/remuxes/starts it — nothing runs synchronously here.
// `checks` — the pre-upload checks result the wizard already computed and
// showed the user before they confirmed, persisted on the new job so its
// detail page's "Verificación pre-subida" section can show exactly that,
// without re-running anything server-side.
// null: lo que digan los grupos de Configuración.
export interface OpcionesTracker {
  anonimo: boolean | null
  personalRelease: boolean | null
}

export async function queueUpload(
  itemId: number,
  remuxOverrides?: Record<number, TrackRemuxOverride>,
  checks?: UploadChecksResult | null,
  // The wizard's already-merged (base plan + remuxOverrides) track table —
  // persisted immediately so the job detail page doesn't show "Pendiente"
  // until the pipeline actually runs the remux step.
  remuxSnapshot?: { ordered_tracks: unknown[] } | null,
  // Season-pack-only: Título/Source/Proveedor/Tag confirmed in the wizard's
  // editable season fields, applied uniformly across every episode.
  // Undefined/omitted for a single movie/episode, whose Título already
  // persists through saveNomenclatureFields instead.
  seasonOverrides?: {
    source?: string | null; provider?: string | null; tag?: string | null; title?: string | null
    // El idioma elegido para cada pista de audio sin idioma, por posición.
    idiomas?: Record<number, string>
  },
  // La petición desde la que se abrió el asistente: la subida la cubre y
  // Subidas la enseña.
  requestItemId?: number | null,
  // El lote al que pertenece (las tandas del asistente): solo viaja si se da, una subida suelta no lo manda.
  loteId?: string,
  // Anónimo / Personal release cambiados a mano en el Resumen. Sin ellos (null), lo que digan los grupos de
  // Configuración para el tag de la subida.
  opciones?: OpcionesTracker,
  // El id del tracker (emu, nobs, mnv). Sin él, eMuwarez. Una subida por tracker: para varios, una llamada por cada uno.
  tracker?: string,
  // La descripción propia (BBCode) de esta subida; vacía, solo la que genera Uploadrr-EMU.
  descripcion?: string,
): Promise<UploadQueueResult> {
  return api.post<UploadQueueResult>(`/library/${itemId}/upload/queue`, {
    ...(tracker ? { tracker } : {}),
    ...(descripcion?.trim() ? { descripcion } : {}),
    request_item_id: requestItemId ?? null,
    ...(loteId ? { lote_id: loteId } : {}),
    anonimo: opciones?.anonimo ?? null,
    personal_release: opciones?.personalRelease ?? null,
    remux_overrides: remuxOverrides ?? null,
    checks: checks ?? null,
    remux_snapshot: remuxSnapshot ?? null,
    source: seasonOverrides?.source ?? null,
    provider: seasonOverrides?.provider ?? null,
    tag: seasonOverrides?.tag ?? null,
    title: seasonOverrides?.title ?? null,
    idiomas: seasonOverrides?.idiomas ?? null,
  })
}

// Un tracker al que se puede subir ahora: eMuwarez y los que tienen su clave puesta (GET /upload/trackers).
export interface TrackerParaSubir {
  id: string
  nombre: string
}

export async function fetchTrackersParaSubir(): Promise<TrackerParaSubir[]> {
  return api.get<TrackerParaSubir[]>('/upload/trackers')
}

// «Subir también a…» (services/subir_tambien.py): lo que ya está en un tracker, a otro con el mismo fichero.
export interface SubirTambienCandidata {
  job_id: number
  title: string | null
  nombre: string
  result_url: string | null
  finished_at: string | null
  origen: TrackerParaSubir
  trackers: TrackerParaSubir[]
  // Por qué no se ofrecen los que ya lo tienen según su catálogo local.
  ya_lo_tienen?: string[]
}

export interface SubirTambienCandidatas {
  trackers: TrackerParaSubir[]
  subidas: SubirTambienCandidata[]
  // Los trackers sin catálogo local leído: en ellos no se ha mirado si ya está.
  avisos?: string[]
}

export interface SubirTambienResultado {
  job_id: number
  tracker: string
  status: 'queued' | 'blocked'
  nuevo_job_id: number | null
  motivo: string | null
}

export interface SubirTambienRespuesta {
  lote_id: string | null
  encolados: number
  saltados: number
  resultados: SubirTambienResultado[]
}

export async function getSubirTambien(): Promise<SubirTambienCandidatas> {
  return api.get<SubirTambienCandidatas>('/uploads/subir-tambien')
}

export async function subirTambien(jobIds: number[], trackers: string[]): Promise<SubirTambienRespuesta> {
  return api.post<SubirTambienRespuesta>('/uploads/subir-tambien', { job_ids: jobIds, trackers })
}

// Lo que dicen las normas de idioma de un tracker de un elemento (web/schemas.py::ReglasDeTracker).
export interface ReglasDeTracker {
  tracker: string
  permitido: boolean
  motivo: string
  variante: string | null
}

export async function fetchReglasTracker(itemId: number, tracker: string): Promise<ReglasDeTracker> {
  return api.get<ReglasDeTracker>(`/library/${itemId}/upload/reglas?tracker=${encodeURIComponent(tracker)}`)
}

// Qué trackers con clave admiten un elemento y, de una temporada, cada capítulo (web/schemas.py::ElegibilidadSubida).
export interface ReglasConNombre extends ReglasDeTracker {
  nombre: string
}

export interface ElegibilidadSubida {
  trackers: ReglasConNombre[]
  capitulos: { item_id: number; trackers: ReglasConNombre[] }[]
}

export async function fetchElegibilidad(itemId: number): Promise<ElegibilidadSubida> {
  return api.get<ElegibilidadSubida>(`/library/${itemId}/upload/elegibilidad`)
}

// Un elemento de «Subir por lotes» (web/schemas.py::UploadLoteElemento).
export interface ElementoLote {
  item_id: number
  // La petición que cubre (desde el Panel). Sin ella (desde Biblioteca), el servidor enlaza la abierta del elemento
  // si hay una sola.
  request_item_id?: number | null
  // Las comprobaciones que vio el usuario: contenido del elemento más las de entorno. Sin `ready`, se salta.
  checks: UploadChecksResult
  // Película o capítulo: el juego COMPLETO de campos de nomenclatura con el proveedor sugerido; el servidor lo
  // guarda como «save-fields». Solo cuando la interfaz sugirió un proveedor.
  campos?: NomenclatureFields | null
  // Solo pack: el proveedor elegido.
  provider?: string | null
  // Los trackers cuyo catálogo local ya lo tiene igual o mejor: para ellos se salta.
  omitir?: string[]
}

// Lo que hizo el servidor con cada elemento y tracker, en el orden mandado (web/schemas.py::UploadLoteResultado).
export interface ResultadoElementoLote {
  item_id: number
  tracker: string
  status: 'queued' | 'blocked'
  job_id: number | null
  errors: string[]
}

export interface LoteEncolado {
  // null si no se encoló ninguno: no queda un lote vacío.
  lote_id: string | null
  encolados: number
  saltados: number
  resultados: ResultadoElementoLote[]
}

// Encola varias subidas como un lote, en este orden (el de la cola). El que no se puede subir se salta con su
// motivo y el resto sigue. Si falta algo que haría fallar a todos (claves, Uploadrr-EMU), 409 con los motivos en
// `detail` (una lista) y nada encolado. `trackers`: a cuáles, en orden (una subida por elemento y tracker; las
// comprobaciones son las del primero). Sin ellos, eMuwarez.
export async function queueLote(elementos: ElementoLote[], trackers?: string[]): Promise<LoteEncolado> {
  return api.post<LoteEncolado>('/uploads/lote', { elementos, ...(trackers ? { trackers } : {}) })
}

export interface UploadsQuery {
  // Uno o varios estados separados por coma («running,preparing»).
  status?: string; limit?: number; offset?: number; search?: string; library_item_id?: number
  // Sin las filas de antes de que un trabajo solo naciera al pulsar Subir: se quitan en el servidor,
  // antes de aplicar el límite, para que no ocupen sitio en la página.
  hide_preparatory?: boolean
  // Solo las de ese lote, y de cada elemento solo su último intento (el servidor quita los anteriores).
  lote_id?: string
  // Solo las que salieron sin tráiler y nadie ha marcado todavía como «Ya lo he añadido».
  sin_trailer?: boolean
}

function uploadsQueryString(params?: UploadsQuery): string {
  const qs = new URLSearchParams()
  if (params?.status) qs.set('status', params.status)
  if (params?.hide_preparatory) qs.set('hide_preparatory', 'true')
  if (params?.limit != null) qs.set('limit', String(params.limit))
  if (params?.offset != null) qs.set('offset', String(params.offset))
  if (params?.search) qs.set('search', params.search)
  if (params?.library_item_id != null) qs.set('library_item_id', String(params.library_item_id))
  if (params?.lote_id) qs.set('lote_id', params.lote_id)
  if (params?.sin_trailer) qs.set('sin_trailer', 'true')
  const query = qs.toString()
  return query ? `?${query}` : ''
}

export async function getUploads(params?: UploadsQuery): Promise<UploadJob[]> {
  return api.get<UploadJob[]>(`/uploads${uploadsQueryString(params)}`)
}

// Cuántas subidas hay con esos filtros, sin el límite de la lista: el «M» de «N de M».
export async function countUploads(params?: Omit<UploadsQuery, 'limit' | 'offset'>): Promise<number> {
  return (await api.get<{ total: number }>(`/uploads/count${uploadsQueryString(params)}`)).total
}

// Cuántas subidas esperan a que se les ponga el tráiler a mano: el aviso de arriba de Subidas.
export async function contarSinTrailer(): Promise<number> {
  return countUploads({ hide_preparatory: true, sin_trailer: true })
}

// «Ya lo he añadido»: la subida deja de avisar de que le falta el tráiler. `puesto=false` lo deshace.
export async function marcarTrailerAMano(jobId: number, puesto = true): Promise<{ trailer_estado: TrailerEstado | null }> {
  return api.post<{ trailer_estado: TrailerEstado | null }>(`/uploads/${jobId}/trailer-a-mano`, { puesto })
}

// «Reenviar a qBittorrent»: vuelve a añadir el torrent de eMuwarez de una subida completada, sin subir nada.
export async function reenviarAQbit(jobId: number): Promise<{ message: string }> {
  return api.post<{ message: string }>(`/uploads/${jobId}/reenviar-qbit`)
}

// "Automático": no wizard involved. The job appears immediately in Subidas
// as 'staging' ("Preparando") while the server computes nomenclature/remux/
// checks previews in the background — see services/upload_queue.py.
// Precheck before opening/confirming the wizard: is there already a job for
// this item (any status, including Completado)? Used to show "ya existe una
// subida — al continuar se encadenará a su historial" instead of creating an
// unrelated duplicate row.
export async function getExistingJobForItem(itemId: number): Promise<UploadJob | null> {
  const jobs = await getUploads({ library_item_id: itemId, limit: 50 })
  return latestInChain(jobs)
}

export async function getUploadJob(jobId: number): Promise<UploadJob> {
  return api.get<UploadJob>(`/uploads/${jobId}`)
}

// `afterSequence`: solo las líneas posteriores a esa (la `sequence` de la última que ya se tiene), para seguir una
// subida en vivo sin bajar el registro entero cada vez.
export async function getUploadJobLogs(
  jobId: number,
  limit = 500,
  offset = 0,
  tail = false,
  afterSequence?: number,
): Promise<UploadJobLogsResponse> {
  const after = afterSequence != null ? `&after_sequence=${afterSequence}` : ''
  return api.get<UploadJobLogsResponse>(`/uploads/${jobId}/logs?limit=${limit}&offset=${offset}&tail=${tail}${after}`)
}

export async function startUploadJob(
  jobId: number,
  body: UploadStartRequest,
): Promise<UploadStartResult> {
  return api.post<UploadStartResult>(`/uploads/${jobId}/start`, body)
}

// Reintenta por el mismo camino en cola que queueUpload(), sin token de
// confirmación: solo crea un trabajo nuevo y no toca el original (fallido o cancelado).
// Por defecto reanuda desde el paso que falló si se puede; `desdeElPrincipio` lo rehace todo.
export async function retryUploadJobQueued(jobId: number, desdeElPrincipio = false): Promise<UploadQueueResult> {
  const consulta = desdeElPrincipio ? '?desde_el_principio=true' : ''
  return api.post<UploadQueueResult>(`/uploads/${jobId}/retry-queued${consulta}`, {})
}

// El resumen de un lote: cuántos de sus elementos hay en cada estado. 404 si no hay ninguno con ese lote_id.
export async function getLote(loteId: string): Promise<LoteResumen> {
  return api.get<LoteResumen>(`/uploads/lotes/${encodeURIComponent(loteId)}`)
}

// Cancela lo que queda del lote: con `soloPendientes`, solo las que esperan en la cola; sin él, también las que ya
// se preparan o suben.
export async function cancelarLote(loteId: string, soloPendientes: boolean): Promise<LoteAccionResult> {
  return api.post<LoteAccionResult>(`/uploads/lotes/${encodeURIComponent(loteId)}/cancelar`, { solo_pendientes: soloPendientes })
}

// Reencola cada elemento fallido o cancelado del lote, en su orden. Como el reintento de una sola, reanuda desde el
// paso que falló si se puede; `desdeElPrincipio` lo rehace todo.
export async function reintentarLote(loteId: string, desdeElPrincipio = false): Promise<LoteAccionResult> {
  return api.post<LoteAccionResult>(`/uploads/lotes/${encodeURIComponent(loteId)}/reintentar`, { desde_el_principio: desdeElPrincipio })
}

export async function cancelUploadJob(jobId: number): Promise<unknown> {
  return api.post<unknown>(`/uploads/${jobId}/cancel`, {})
}

// Only ever called after the styled confirmation card — the token itself
// isn't user-typed, it's the same constant the backend requires
// (services/upload_execution.py::DELETE_CONFIRMATION_TOKEN).
const DELETE_CONFIRMATION_TOKEN = 'BORRAR_SUBIDA'

export async function deleteUploadJob(jobId: number): Promise<OkResponse> {
  return api.post<OkResponse>(`/uploads/${jobId}/delete`, { confirmation: DELETE_CONFIRMATION_TOKEN })
}

// «Borrar lote»: cada subida terminada del lote, como «Borrar» en su fila (con la cadena de intentos entera). Las que
// siguen en cola o en marcha no se tocan. Mismo token que el borrado suelto.
export async function borrarLote(loteId: string): Promise<LoteBorrarResult> {
  return api.post<LoteBorrarResult>(`/uploads/lotes/${encodeURIComponent(loteId)}/borrar`, { confirmation: DELETE_CONFIRMATION_TOKEN })
}

export interface UploadCheck {
  id: string
  label: string
  status: 'ok' | 'fail' | 'skip'
  message: string
}
export interface UploadChecksResult {
  checks: UploadCheck[]
  ready: boolean
  // Temporada: el nombre del torrent del pack (su `season_pack_title`) si el pack está listo; null si no, y en
  // lo demás. Lo usa «Subir por lotes», que no abre el asistente para cada pack.
  nombre?: string | null
  // Temporada con el pack listo: los avisos del plan (proveedor, fuente, audio sin idioma) y si uno es el del audio.
  avisos?: string[]
  sin_idioma?: boolean
}

export interface AjustesComprobacion {
  source?: string | null
  tag?: string | null
  title?: string | null
  idiomas?: Record<number, string>
}

// `scope` parte la pasada para una selección de varias temporadas o
// capítulos: 'content' son las del elemento (una por elemento) y
// 'environment' las compartidas (una sola vez para toda la tanda). Sin
// scope, las hace todas, que es lo que necesita un solo elemento.
export async function runUploadChecks(
  itemId: number,
  scope?: 'content' | 'environment',
  // Temporada: el Proveedor elegido (utils/origenProveedor.ts::proveedorParaServidor), el mismo que la vista previa y la cola.
  provider?: string,
  // Temporada: lo editado a mano en el asistente (Source, Tag, Título e idiomas de las pistas sin idioma), para que el
  // `nombre` del pack sea el que saldrá. Sin nada, no viaja nada.
  ajustes?: AjustesComprobacion,
  // De quién son las normas de idioma (emu, nobs, mnv). Sin él, eMuwarez.
  tracker?: string,
): Promise<UploadChecksResult> {
  const qs = new URLSearchParams()
  if (scope) qs.set('scope', scope)
  if (tracker && tracker !== 'emu') qs.set('tracker', tracker)
  if (provider !== undefined) qs.set('provider', provider)
  if (ajustes?.source) qs.set('source', ajustes.source)
  if (ajustes?.tag) qs.set('tag', ajustes.tag)
  if (ajustes?.title) qs.set('title', ajustes.title)
  if (ajustes?.idiomas && Object.keys(ajustes.idiomas).length) qs.set('idiomas', JSON.stringify(ajustes.idiomas))
  const query = qs.toString()
  return api.post<UploadChecksResult>(`/library/${itemId}/upload/checks${query ? `?${query}` : ''}`, {})
}

export interface UploadChecksProgress {
  checks: UploadCheck[]
  done: boolean
  ready: boolean
}

// Poll this WHILE runUploadChecks() is in flight to see each check land as it
// actually completes (the full run is ~30s of real network calls).
export async function getUploadChecksProgress(itemId: number): Promise<UploadChecksProgress> {
  return api.get<UploadChecksProgress>(`/library/${itemId}/upload/checks/progress`)
}
