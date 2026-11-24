import { memo, useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { getUploads, countUploads, contarSinTrailer, getUploadJob, startUploadJob, retryUploadJobQueued, cancelUploadJob, getLote, fetchTrackersParaSubir, getSubirTambien } from '../api/uploads'
import { useInstancias } from '../hooks/useInstancias'
import { ApiClientError } from '../api/client'
import type { UploadJob, InstanceSafeResponse, LoteResumen } from '../types/api'
import { EmptyState, FilaVacia } from '../components/EmptyState'
import { LoadingState } from '../components/LoadingState'
import { GruposDeRelease } from '../components/GruposDeRelease'
import { ErrorState } from '../components/ErrorState'
import { HeaderFilter } from '../components/HeaderFilter'
import { ChipTracker } from '../components/ChipTracker'
import { UploadJobDetailPage } from '../components/UploadJobDetailPage'
import { DeleteUploadModal } from '../components/DeleteUploadModal'
import { PeticionDeSubida } from '../components/PeticionDeSubida'
import { RellenarPeticion } from '../components/RellenarPeticion'
import { ServiceLabel } from '../components/ServiceIcon'
import { CancelButtonIcon, RetryButtonIcon, DeleteButtonIcon, ExternalLinkButtonIcon, ValueChip, PlainValue, TypeValueIcon } from '../components/DetailCard'
import { HoverTitle, useTooltip } from '../components/Tooltip'
import { formatDateTimeSeconds, timeAgo } from '../utils/format'
import { posterUrl, buildDeepLink } from '../utils/libraryItem'
import { getDetailIdFromPath, pushDetailPath, closeDetailPath } from '../utils/detailUrl'
import { hayMasPorCargar } from '../utils/paginarSubidas'
import { CLAVE_ABRIR_LOTE, EVENTO_ABRIR_LOTE, PATRON_LOTE_ID } from '../utils/lote'
import {
  UPLOAD_TYPE_LABEL, STATUS_CLASS, statusLabel, phaseLabel, currentPhaseInfo,
  progressCounter, queuedWaitText, formatSecondsHM, ACTIVE_JOB_STATUSES, fundirPorId, conservarIguales,
} from '../utils/uploadStatus'
import { ChipPendienteModeracion } from '../components/ChipPendienteModeracion'
import { useSeeding, type SeedingEstado } from '../hooks/useSeeding'
import { SeedingCelda } from '../components/SaludSeeding'
import { useRefrescoPorAvisos, AVISOS_SUBIDA, AVISOS_SUBIDA_PROGRESO } from '../hooks/useRefrescoPorAvisos'
import { useAlSalir, useSeccionVisible } from '../context/SeccionVisible'
import { IconoDesplegar, IconoSubida, IconoTipo } from '../components/Iconos'
import { Modal } from '../components/Modal'
import { BannerLote } from '../components/BannerLote'
import { SubirTambienModal } from '../components/SubirTambien'
import { BannerSinTrailer, ChipSinTrailer, faltaTrailer } from '../components/AvisoTrailer'
import { FilasVirtuales } from '../components/FilasVirtuales'
import { UMBRAL_VIRTUAL } from '../utils/ventana'

// Backend-real gates — mirror cancel_upload_job()/queue_retry_for_job() in
// services/upload_execution.py / upload_queue.py exactly, so the button is
// only enabled when the action would actually succeed.
//
// 'preparing' SÍ entra: no se interrumpe a media faena (puede estar
// renombrando o remuxeando), pero se marca y el trabajo se rinde en el
// siguiente punto limpio. Excluirlo dejaba un agujero real: si la aplicación
// se paraba durante esa fase, el job se quedaba en 'preparing' para siempre y
// no había ningún botón capaz de tocarlo.
// Reintentar excludes 'completed' on purpose: retrying an already-successful
// upload isn't supported server-side.
const CANCELLABLE_STATUSES = new Set(['staging', 'queued', 'running', 'preparing'])
const RETRYABLE_STATUSES = new Set(['failed', 'cancelled'])
// Borrar solo lo terminado (mirror de delete_upload_job). 'completed' entra
// porque borrar el registro de una subida que salió bien no deshace nada: el
// torrent ya está en eMuwarez y el archivo sigue en su sitio; lo que se borra
// es el historial de esta aplicación.
const DELETABLE_STATUSES = new Set(['failed', 'cancelled', 'completed'])

const CONFIRMATION_TOKEN = 'INICIAR_UPLOADRR'
// Las columnas de la tabla (para las filas que ocupan el ancho entero).
const COLUMNAS = 9
// Lo que mide una fila antes de pintarla (FilasVirtuales la corrige con la media de las que va midiendo).
const ALTO_FILA = 65
const claveDeGrupo = (grupo: UploadJob[]) => grupo[0].id!

// Only real upload states — a job now only exists once the user uploads, so
// the preparatory "ready"/"blocked" states no longer appear here. 'preparing'
// isn't offered as its own filter option — it displays as "Subiendo" same as
// 'running' (see STATUS_LABEL), and the "Subiendo" filter option matches both.
const STATUS_OPTIONS = ['', 'staging', 'queued', 'running', 'completed', 'failed', 'cancelled']
// Preparatory states never represent a real upload — hide any stale leftovers.
const HIDDEN_STATES = new Set(['draft', 'preflight', 'blocked', 'ready', 'config_staging'])

// Cuántas subidas se piden de una vez, y cuántas como mucho (el máximo que acepta GET /uploads). Pasado el
// límite, «Cargar más» pide otra tanda: la lista es siempre las más recientes, sin huecos.
const PAGINA = 100
const MAX_LIMITE = 2000

// "Orden: Estado" ordenaba por el CÓDIGO interno, no por lo que se ve, y salía
// una secuencia sin sentido para nadie: Cancelado, Completado, Fallido, En
// cola, Subiendo, Preparando. Se ordena por el ciclo de vida de una subida, que
// además dice más que el alfabético: lo que está en marcha arriba y lo
// terminado abajo (auditoría de tablas).
const ORDEN_DE_ESTADO = ['staging', 'queued', 'preparing', 'running', 'completed', 'failed', 'cancelled']
const ordenDeEstado = (estado?: string | null): number => {
  const i = ORDEN_DE_ESTADO.indexOf(estado ?? '')
  return i === -1 ? ORDEN_DE_ESTADO.length : i    // lo desconocido, al final
}

function StartModal({ job, onClose, onDone }: { job: UploadJob; onClose: () => void; onDone: () => void }) {
  const [confirm, setConfirm] = useState('')
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const jobId = job.id!

  const handleDryRun = async () => {
    setLoading(true); setError(null); setMessage(null)
    try {
      const r = await startUploadJob(jobId, { dry_run: true, start: false, confirmation: null })
      setMessage(r.command_preview ? `Comando previsto:\n${r.command_preview}` : r.message || 'Dry-run completado.')
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error.') }
    finally { setLoading(false) }
  }

  const handleStart = async () => {
    if (confirm !== CONFIRMATION_TOKEN) return
    setLoading(true); setError(null); setMessage(null)
    try {
      const r = await startUploadJob(jobId, { dry_run: false, start: true, confirmation: CONFIRMATION_TOKEN })
      setMessage(r.message || 'Uploadrr iniciado.')
      onDone()
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error al iniciar.') }
    finally { setLoading(false) }
  }

  return (
    <Modal titulo={<>Iniciar Uploadrr: job {jobId}</>} onClose={onClose} className="sub-modal-560">
      <div className="modal-body">
        <p className="sub-mb-8"><strong>{job.title || '—'}</strong></p>
        <div className="sub-aviso">
          <ul className="sub-aviso-lista">
            <li>Esta acción puede publicar/subir contenido a eMuwarez.</li>
            <li>No se crearán peticiones automáticamente.</li>
            <li>No se modificará el archivo original ni el título MKV.</li>
          </ul>
        </div>
        <label className="sub-etiqueta">
          Escribe <strong>{CONFIRMATION_TOKEN}</strong> para confirmar:
        </label>
        <input type="text" value={confirm} onChange={e => setConfirm(e.target.value)} placeholder={CONFIRMATION_TOKEN}
          className={`sub-confirmar${confirm === CONFIRMATION_TOKEN ? ' sub-confirmar-ok' : ''}`} />
        {message && <pre className="sub-resultado">{message}</pre>}
        {error && <p className="error-text sub-mt-8">{error}</p>}
      </div>
      <div className="detail-panel__actions sub-modal-acciones">
        <button className="btn btn-secondary" onClick={handleDryRun} disabled={loading}>Dry-run</button>
        <button className="btn btn-primary" onClick={handleStart} disabled={confirm !== CONFIRMATION_TOKEN || loading}>{loading ? 'Iniciando…' : 'Iniciar subida'}</button>
        <button className="btn btn-secondary" onClick={onClose}>Cerrar</button>
      </div>
    </Modal>
  )
}

// Una sola columna «Fecha»: cuándo terminó o, si no ha terminado, cuándo se creó, en relativo («hace 2 h») como en
// Peticiones; las dos fechas exactas van en el tooltip. Con Creado y Finalizado en dos columnas de fecha y hora la
// tabla se desbordaba a 1280 px y el título se quedaba en 70 px.
function FechaDeFila({ job }: { job: UploadJob }) {
  const exactas = [
    job.created_at ? `Creada: ${formatDateTimeSeconds(job.created_at)}` : null,
    job.finished_at ? `Finalizada: ${formatDateTimeSeconds(job.finished_at)}` : null,
  ].filter(Boolean).join(' · ')
  const { triggerProps, tooltipNode } = useTooltip(exactas || null)
  const valor = job.finished_at ?? job.created_at
  return (
    <td className="sub-nowrap sub-sec sub-fecha sub-meta" {...triggerProps}>
      {valor ? timeAgo(valor) : <span className="sub-muted">—</span>}
      {tooltipNode}
    </td>
  )
}

// El primer renglón de un error: la lista no es sitio para la pila entera (el motivo completo está en el detalle).
const primeraLinea = (texto: string) => texto.split('\n').find(l => l.trim())?.trim() ?? texto

// El estado de una subida y, debajo, lo que dice de ella: cómo va (barra y fase), cuánto tardó o por qué falló.
function EstadoDeFila({ job }: { job: UploadJob }) {
  const viva = job.status === 'preparing' || job.status === 'running'
  const pct = Math.max(0, Math.min(100, job.progress?.global_pct ?? 0))
  const phaseInfo = viva ? currentPhaseInfo(job) : null
  const fase = phaseInfo?.label ?? (viva ? phaseLabel(job.progress_phase) : null)
  const transcurrido = viva ? progressCounter(job) : null
  const clase = STATUS_CLASS[job.status] || 'badge req-unknown'
  const parpadea = job.status === 'preparing' || job.status === 'running'
  const motivo = job.status === 'failed' && job.error_message ? primeraLinea(job.error_message) : null
  return (
    <div className="sub-estado">
      <span className={parpadea ? `${clase} badge-blink` : clase}>{statusLabel(job.status)}</span>
      {job.status === 'completed' && job.pendiente_moderacion && <ChipPendienteModeracion />}
      {job.status === 'staging' && <span className="sub-estado-pie">Generando nomenclatura</span>}
      {job.status === 'queued' && <span className="sub-estado-pie">{queuedWaitText(job)}</span>}
      {viva && (
        <>
          <div className="sub-barra" role="progressbar" aria-label="Progreso global de la subida" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
            {/* Valor calculado: la fracción de la subida que va hecha. */}
            <div className="sub-barra-relleno" style={{ transform: `scaleX(${pct / 100})` }} />
          </div>
          <span className="sub-estado-pie">
            {fase}{phaseInfo?.counterText ? ` (${phaseInfo.counterText})` : ''}{transcurrido ? ` · ${transcurrido}` : ''}
          </span>
        </>
      )}
      {!viva && job.duration_seconds != null && (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') && (
        <span className="sub-estado-pie">{job.status === 'completed' ? 'en' : 'tras'} {formatSecondsHM(job.duration_seconds)}</span>
      )}
      {motivo && <span className="sub-estado-error" title={job.error_message ?? undefined}>{motivo}</span>}
    </div>
  )
}

// Un botón de la fila: solo el icono, con su nombre para el lector de pantalla y el tooltip.
function AccionDeFila({ etiqueta, tono, clase, onClick, children }: {
  etiqueta: string; tono?: 'peligro'; clase?: string; onClick: () => void; children: React.ReactNode
}) {
  const { triggerProps, tooltipNode } = useTooltip(etiqueta)
  return (
    <button type="button" className={`sub-accion${tono ? ` sub-accion-${tono}` : ''}${clase ? ` ${clase}` : ''}`} aria-label={etiqueta} onClick={onClick} {...triggerProps}>
      {children}
      {tooltipNode}
    </button>
  )
}

// One table row — used both for the latest attempt in a retry chain (normal
// row) and for its older attempts (indented/muted, revealed by expanding the
// chip of retries next to the title). `intentosAnteriores` is only passed on the latest-attempt row.
//
// Va memorizada: con el progreso real llegando cada medio segundo, solo tiene que repintarse la fila cuya subida
// cambió. Para que el memo sirva, todas las props son valores o funciones con identidad estable (nada de objetos o
// flechas hechos en cada pintado del padre): por eso el chip de reintentos son dos valores y `onOpen` recibe el id.
const JobRow = memo(function JobRow({ job, instances, seeding, onOpen, indent, enGrupo, otrosTrackers, trackersAbiertos, otroFallido, onToggleTrackers, intentosAnteriores, desplegada, onToggleCadena, onCancel, onRetry, onDelete, onFiltrarLote, onSubirTambien }: {
  job: UploadJob
  seeding: SeedingEstado
  instances: InstanceSafeResponse[]
  onOpen: (jobId: number) => void
  indent?: boolean
  // El mismo elemento subido a varios trackers: la primera fila lleva el título; las otras, solo su tracker.
  enGrupo?: 'primera' | 'otra'
  // En la primera: los otros trackers («N.O.B.S · eMuwarez»), si se ven sus filas y si alguna no terminó bien.
  otrosTrackers?: string
  trackersAbiertos?: boolean
  otroFallido?: boolean
  onToggleTrackers?: (elementoId: number, abiertos: boolean) => void
  // Cuántos intentos anteriores tiene la cadena y si están desplegados (solo en la fila del último intento).
  intentosAnteriores?: number
  desplegada?: boolean
  onToggleCadena?: (jobId: number) => void
  onCancel: (jobId: number) => void
  onDelete: (job: UploadJob) => void
  onRetry: (job: UploadJob) => void
  // Pulsar el chip «Lote · N»: ver solo las subidas de ese lote.
  onFiltrarLote?: (loteId: string) => void
  // Solo si se puede subir también a otro tracker.
  onSubirTambien?: (jobId: number) => void
}) {
  const canCancel = CANCELLABLE_STATUSES.has(job.status)
  const canDelete = DELETABLE_STATUSES.has(job.status)
  // Un intento anterior de una cadena ya se reintentó: el servidor solo acepta reintentar el último.
  const canRetry = !indent && RETRYABLE_STATUSES.has(job.status)
  const deepLink = buildDeepLink(job, instances)
  const { triggerProps: emuTip, tooltipNode: emuTooltip } = useTooltip(job.result_url ? `Ver en ${job.tracker_nombre || 'eMuwarez'}` : null)
  const { triggerProps: originTip, tooltipNode: originTooltip } = useTooltip(deepLink ? `Abrir en ${job.instance_name ?? job.instance_type}` : null)
  const hayCadena = !!intentosAnteriores && !!onToggleCadena
  const retryToggleTip = useTooltip(hayCadena ? (desplegada ? 'Ocultar intentos anteriores' : 'Ver intentos anteriores') : null)
  const abrir = () => onOpen(job.id!)
  // El chip del lote: solo si el lote tiene más de un elemento (uno que se quedó en uno no agrupa nada), y no en los
  // intentos anteriores, que van debajo de la fila que ya lo lleva.
  const loteId = !indent && job.lote_id && (job.lote_total ?? 0) > 1 ? job.lote_id : null
  const loteTip = useTooltip(loteId ? 'Ver solo las subidas de este lote' : null)
  const otra = enGrupo === 'otra'
  const trackersTip = useTooltip(otrosTrackers ? (trackersAbiertos ? 'Ocultar las subidas a los otros trackers' : 'Ver las subidas a los otros trackers') : null)
  return (
    // Por debajo de 760 px la fila es una tarjeta (subidas.css): título y la acción que toca, el estado, y en gris
    // tipo · origen · fecha y los chips. Las clases sub-col-* y sub-meta dicen qué va en cada línea.
    <tr className={indent ? 'sub-tarjeta sub-fila-antigua' : otra ? 'sub-tarjeta sub-fila-tracker' : 'sub-tarjeta'}>
      <td className="sub-celda-titulo">
        <div className={`sub-titulo-fila${indent ? ' sub-titulo-fila-antigua' : otra ? ' sub-titulo-fila-tracker' : ''}`}>
          {!otra && <div className="poster-thumb sub-poster">
            {job.poster_url ? (
              <img
                src={posterUrl(job.poster_url, 'w92')}
                alt=""
                loading="lazy"
                decoding="async"
                className="sub-poster-img"
                onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none' }}
              />
            ) : (
              <span className="sub-poster-icono">
                <IconoTipo serie={job.media_type === 'series' || job.media_type === 'episode'} />
              </span>
            )}
          </div>}
          <div className="sub-titulo-texto">
            {indent && <span className="sub-muted sub-no-encoge">↳</span>}
            <HoverTitle as="span" className={otra ? 'lib-title-link sub-titulo sub-tracker-chip' : 'lib-title-link sub-titulo'}
              href={`/uploads/${job.id}`} onClick={abrir} tip="Ver progreso / detalle"
              aria-label={otra ? `${job.title} en ${job.tracker_nombre}` : undefined}>
              {otra ? job.tracker_nombre : job.title || '—'}
            </HoverTitle>
            {!otra && job.year ? <span className="sub-muted sub-t11 sub-no-encoge sub-anio">({job.year})</span> : null}
          </div>
          {/* Aparte del título: en la tarjeta de móvil bajan a la última línea. */}
          <div className="sub-chips">
            {!otra && <ChipTracker tracker={job.tracker} nombre={job.tracker_nombre} siempre={enGrupo === 'primera'} />}
            {otrosTrackers && onToggleTrackers && (
              <button
                type="button"
                className={`sub-reintentos-chip sub-toggle-trackers${otroFallido ? ' sub-toggle-trackers-fallido' : ''}`}
                aria-expanded={!!trackersAbiertos}
                onClick={() => onToggleTrackers(job.library_item_id!, !!trackersAbiertos)}
                {...trackersTip.triggerProps}
              >
                + {otrosTrackers}
                <IconoDesplegar abierto={!!trackersAbiertos} size={10} />
                {trackersTip.tooltipNode}
              </button>
            )}
            {loteId && onFiltrarLote && (
              <button
                type="button"
                className="sub-lote-chip"
                onClick={e => { e.stopPropagation(); onFiltrarLote(loteId) }}
                {...loteTip.triggerProps}
              >
                Lote · {job.lote_total}
                {loteTip.tooltipNode}
              </button>
            )}
            {hayCadena && (
              <button
                type="button"
                className="sub-reintentos-chip sub-toggle-reintentos"
                aria-expanded={desplegada}
                aria-label={intentosAnteriores === 1 ? '1 intento anterior' : `${intentosAnteriores} intentos anteriores`}
                onClick={() => onToggleCadena(job.id!)}
                {...retryToggleTip.triggerProps}
              >
                <RetryButtonIcon /> {job.retry_count ?? intentosAnteriores}
                <IconoDesplegar abierto={!!desplegada} size={10} />
                {retryToggleTip.tooltipNode}
              </button>
            )}
            {!hayCadena && !indent && (job.retry_count ?? 0) > 0 && (
              <span className="sub-reintentos-chip" title="Intentos anteriores">
                <RetryButtonIcon /> {job.retry_count}
              </span>
            )}
            {!indent && faltaTrailer(job) && <ChipSinTrailer job={job} onAbrir={abrir} />}
            {job.result_url && (
              <a className="hover-link sub-enlace-emu" href={job.result_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                aria-label={`Ver en ${job.tracker_nombre || 'eMuwarez'}`} {...emuTip}>
                <ExternalLinkButtonIcon />
                {emuTooltip}
              </a>
            )}
          </div>
        </div>
      </td>
      {otra ? <><td className="sub-sec" /><td className="sub-sec" /></> : <>
      <td className="sub-nowrap sub-sec sub-meta sub-col-tipo">
        {job.media_type
          ? <PlainValue icon={<TypeValueIcon />}>{UPLOAD_TYPE_LABEL[job.media_type] ?? job.media_type}</PlainValue>
          : <span className="sub-muted">—</span>}
      </td>
      <td className="sub-nowrap sub-sec sub-meta">
        {job.instance_type
          ? (
            deepLink
              ? <ValueChip linkable><a className="hover-link" href={deepLink} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()} {...originTip}><ServiceLabel type={job.instance_type} label={job.instance_name ?? job.instance_type} size={14} />{originTooltip}</a></ValueChip>
              : <ServiceLabel type={job.instance_type} label={job.instance_name ?? job.instance_type} size={14} />
          )
          : <span className="sub-muted">—</span>}
      </td>
      </>}
      <td className="col-prioridad-baja sub-nowrap">
        <GruposDeRelease grupos={job.release_groups ?? []} junto />
      </td>
      <td className="col-prioridad-baja sub-nowrap sub-celda-peticion">
        <PeticionDeSubida job={job} />
      </td>
      <td className="sub-celda-estado">
        <EstadoDeFila job={job} />
      </td>
      <td className="sub-nowrap sub-sec sub-col-seeding"><SeedingCelda job={job} seeding={seeding} /></td>
      <FechaDeFila job={job} />
      <td className="col-actions">
        <div className="sub-acciones-fila">
          <RellenarPeticion job={job} compacto />
          {onSubirTambien && (
            <AccionDeFila etiqueta="Subir también a otro tracker" onClick={() => onSubirTambien(job.id!)}><IconoSubida size={14} /></AccionDeFila>
          )}
          {canRetry && (
            <AccionDeFila etiqueta="Reintentar" onClick={() => onRetry(job)}><RetryButtonIcon /></AccionDeFila>
          )}
          {canCancel && (
            <AccionDeFila etiqueta="Cancelar" tono="peligro" onClick={() => onCancel(job.id!)}><CancelButtonIcon /></AccionDeFila>
          )}
          {canDelete && (
            <AccionDeFila
              etiqueta={(job.retry_count ?? 0) > 0 ? 'Borrar, con todos sus intentos' : 'Borrar'}
              tono="peligro"
              clase="sub-accion-borrar"
              onClick={() => onDelete(job)}
            ><DeleteButtonIcon /></AccionDeFila>
          )}
        </div>
      </td>
    </tr>
  )
})

export function UploadsPage({ onNavigate }: { onNavigate?: (page: string) => void } = {}) {
  const [jobs, setJobs] = useState<UploadJob[]>([])
  // Only needed to build the Origen deep link (same as Biblioteca's own column).
  const instances = useInstancias()
  // El seeding de qBittorrent: se lee al entrar y con «Actualizar» del detalle, sin reloj.
  const seeding = useSeeding()
  // Volver aquí no vuelve a enseñar "Cargando…" ni reinicia los filtros: la
  // sección no se desmonta al cambiar de pestaña, se queda montada y oculta
  // (ver AppShell). Esto solo corre la PRIMERA vez que se entra.
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState('')
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState('created_desc')
  const [typeFilter, setTypeFilter] = useState('')
  // Solo las subidas de un lote (el chip «Lote · N»): lo filtra el servidor, que devuelve el último intento de cada
  // elemento.
  const [loteFiltro, setLoteFiltro] = useState<string | null>(null)
  // El aviso «Sin trailer»: cuántas esperan que se les ponga a mano (de todas, no solo las cargadas) y si la lista
  // enseña solo esas (lo filtra el servidor).
  const [sinTrailer, setSinTrailer] = useState(0)
  const [soloSinTrailer, setSoloSinTrailer] = useState(false)
  // El resumen de ese lote (contadores y qué se puede hacer): se pide con la lista, en el mismo load().
  const [loteResumen, setLoteResumen] = useState<LoteResumen | null>(null)
  // El lote que el servidor ya no conoce (404): se borraron todas sus subidas, por ejemplo con «Borrar lote».
  const [loteSinSubidas, setLoteSinSubidas] = useState<string | null>(null)
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null)
  // «Subir también a…»: el modal para elegir varias subidas a eMuwarez y subirlas a N.O.B.S o Milnueve.
  // `inicial`: abierto desde una fila, con esa subida marcada.
  const [subirTambien, setSubirTambien] = useState<{ inicial?: number } | null>(null)
  const abrirSubirTambien = useCallback((jobId: number) => setSubirTambien({ inicial: jobId }), [])
  // Solo se ofrece con la clave de otro tracker puesta: quien solo usa eMuwarez no ve nada nuevo.
  const [hayOtrosTrackers, setHayOtrosTrackers] = useState(false)
  // Si la sección se ve: la sección sigue montada al ir a Configuración, así que se pregunta otra vez al volver (una
  // clave puesta allí entretanto enseña el botón sin recargar la página).
  const subidasALaVista = useSeccionVisible()
  useEffect(() => {
    if (!subidasALaVista) return
    let vigente = true
    fetchTrackersParaSubir().then(t => { if (vigente) setHayOtrosTrackers(t.some(x => x.id !== 'emu')) }, () => {})
    return () => { vigente = false }
  }, [subidasALaVista])
  // Las subidas que llevan el botón de la fila: se vuelve a preguntar cuando cambia cuántas hay completadas.
  const [paraSubirTambien, setParaSubirTambien] = useState<Set<number>>(new Set())
  const completadas = useMemo(() => jobs.filter(j => j.status === 'completed').length, [jobs])
  useEffect(() => {
    if (!subidasALaVista || !hayOtrosTrackers) return
    let vigente = true
    getSubirTambien().then(d => { if (vigente) setParaSubirTambien(new Set(d.subidas.map(s => s.job_id))) }, () => {})
    return () => { vigente = false }
  }, [subidasALaVista, hayOtrosTrackers, completadas])
  // Cuántas se piden (las más recientes), cuántas hay en total con el filtro de estado, y si ya llegó la primera
  // respuesta (hasta entonces «no está en la lista» no quiere decir nada).
  const [limite, setLimite] = useState(PAGINA)
  const [total, setTotal] = useState<number | null>(null)
  const [cargado, setCargado] = useState(false)
  // Por qué no se pudo reintentar: se enseña en el detalle y, si se pulsó en la lista, sobre la tabla.
  const [retryError, setRetryError] = useState<string | null>(null)
  // Si cancelar falla se dice sobre la tabla o en el detalle: antes se tragaba el error y el botón parecía no hacer nada.
  const [cancelError, setCancelError] = useState<string | null>(null)

  // Keeps /uploads/:id in sync with whichever job's detail page is open.
  // Con identidad fija (solo toca setters y la URL): las filas memorizadas la reciben como prop.
  const openUpload = useCallback((id: number) => {
    pushDetailPath('uploads', id)
    setRetryError(null)
    setCancelError(null)
    setSelectedJobId(id)
  }, [])
  function closeUpload() {
    closeDetailPath('uploads')
    setRetryError(null)
    setCancelError(null)
    setSelectedJobId(null)
  }
  const [startJob, setStartJob] = useState<UploadJob | null>(null)
  const [deleteJob, setDeleteJob] = useState<UploadJob | null>(null)
  // Retry chains: which terminal (latest-attempt) job ids currently have
  // their older attempts expanded below them.
  const [expandedChains, setExpandedChains] = useState<Set<number>>(new Set())
  // Plegado elegido a mano de las subidas a otros trackers, por elemento de la biblioteca.
  const [trackersElegidos, setTrackersElegidos] = useState<Map<number, boolean>>(new Map())
  const toggleTrackers = useCallback((elementoId: number, abiertos: boolean) => {
    setTrackersElegidos(prev => new Map(prev).set(elementoId, !abiertos))
  }, [])

  // Guards against out-of-order responses: the 2.5s background poll (below)
  // and an explicit load() (e.g. right after deleting a job) can both be
  // in-flight at once, and network timing doesn't guarantee the one that
  // started first also RESOLVES first. Without this, a slower poll that
  // started before a delete can resolve after it and overwrite `jobs` with
  // stale pre-delete data — the deleted job "comes back" until the next
  // refresh. Only ever apply the response from the most recently ISSUED call.
  const loadSeqRef = useRef(0)
  // Cargas completas sin acabar: mientras haya una, la de progreso no se lanza (llegaría antes y la completa, con
  // datos de antes, haría ir la barra hacia atrás).
  const cargasEnVuelo = useRef(0)
  const load = useCallback(async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) { setLoading(true); setError(null) }
    const seq = ++loadSeqRef.current
    cargasEnVuelo.current++
    try {
      // The "Subiendo" filter option (value 'running') must also catch
      // 'preparing' jobs — they display identically as "Subiendo" now (see
      // STATUS_LABEL) — so the server filters on both, before paginating and counting.
      const filtros = {
        status: statusFilter === 'running' ? 'running,preparing' : statusFilter || undefined,
        hide_preparatory: true,
        lote_id: loteFiltro ?? undefined,
        sin_trailer: soloSinTrailer || undefined,
      }
      // Si falla solo el recuento, la lista se enseña igual y el total queda sin conocer.
      // El resumen del lote: un 404 (ya no queda ninguna) lo quita; otro fallo deja el que había.
      const [data, cuantas, resumen, faltan] = await Promise.all([
        getUploads({ ...filtros, limit: limite }),
        countUploads(filtros).catch(() => null),
        loteFiltro
          ? getLote(loteFiltro).catch(e => (e instanceof ApiClientError && e.status === 404 ? null : undefined))
          : Promise.resolve(null),
        // Si falla, el aviso se queda como estaba.
        contarSinTrailer().catch(() => null),
      ])
      if (seq === loadSeqRef.current) {
        // Las subidas que no cambiaron conservan su objeto: sus filas no se repintan.
        setJobs(prev => conservarIguales(prev, data))
        setTotal(cuantas)
        if (faltan != null) setSinTrailer(faltan)
        if (resumen !== undefined) {
          setLoteResumen(resumen)
          setLoteSinSubidas(resumen === null ? loteFiltro : null)
        }
        setCargado(true)
        setError(null)
      }
    } catch {
      if (!opts?.silent) setError('Error al cargar los trabajos de subida.')
    }
    finally {
      cargasEnVuelo.current--
      if (!opts?.silent) setLoading(false)
    }
  }, [statusFilter, limite, loteFiltro, soloSinTrailer])

  // El aviso de progreso solo cambia cuánto llevan las vivas: se piden ellas (unos 1,5 kB cada una) y se funden en la
  // lista que hay, en vez de la lista entera y su recuento dos veces por segundo. Qué subidas hay lo trae la recarga
  // completa de `subida-estado`. Sin ninguna viva en la tabla no se pide nada.
  const jobsRef = useRef(jobs)
  jobsRef.current = jobs
  const cargarProgreso = useCallback(async () => {
    if (cargasEnVuelo.current || !jobsRef.current.some(j => ACTIVE_JOB_STATUSES.has(j.status))) return
    const seq = loadSeqRef.current
    try {
      const vivas = await getUploads({
        status: [...ACTIVE_JOB_STATUSES].join(','), hide_preparatory: true, lote_id: loteFiltro ?? undefined, limit: 500,
      })
      // Una carga completa lanzada mientras tanto manda.
      if (seq === loadSeqRef.current) setJobs(prev => fundirPorId(prev, vivas))
    } catch { /* el siguiente aviso lo vuelve a intentar */ }
  }, [loteFiltro])

  useEffect(() => { load() }, [load])

  // Opened from the dashboard "Actividad de subida" (clicking a title): pick
  // up the stashed job id once the list has loaded and show its detail page.
  //
  // Depende de `cargado` y de `seVe`: la sección ya no se levanta de cero en
  // cada visita, así que sin eso solo se recogía el salto si los datos
  // llegaban justo después (ver context/SeccionVisible). Se abre aunque no esté
  // en la lista (una antigua): la ficha la pide por su id.
  //
  // También el lote recién encolado desde «Subir por lotes» (Biblioteca): la tabla, filtrada por él.
  useEffect(() => {
    if (!subidasALaVista || !cargado) return
    let lote: string | null = null
    try { lote = sessionStorage.getItem(CLAVE_ABRIR_LOTE) } catch { /* ignore */ }
    if (lote) {
      try { sessionStorage.removeItem(CLAVE_ABRIR_LOTE) } catch { /* ignore */ }
      if (PATRON_LOTE_ID.test(lote)) verLote(lote)
    }
    let pending: string | null = null
    try { pending = sessionStorage.getItem('emuwarez_open_upload') } catch { /* ignore */ }
    if (!pending) return
    try { sessionStorage.removeItem('emuwarez_open_upload') } catch { /* ignore */ }
    openUpload(Number(pending))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- se recoge al llegar los datos o volver a la sección; verLote solo fija estado
  }, [cargado, subidasALaVista])

  // La sección sigue montada al cambiar de pestaña: el seeding se vuelve a leer cada vez que se ve (el servidor lo
  // guarda 60 s, así que no es una consulta más a qBittorrent).
  const recargarSeeding = seeding.cargar
  useEffect(() => {
    if (subidasALaVista) recargarSeeding()
  }, [subidasALaVista, recargarSeeding])

  function quitarFiltros() {
    setSearch('')
    setStatusFilter(''); setTypeFilter('')
    setLoteFiltro(null)
    setSoloSinTrailer(false)
  }
  const filtrando = !!(search || statusFilter || typeFilter || loteFiltro || soloSinTrailer)
  // Viendo solo las que no tienen tráiler, cuando ya no queda ninguna se vuelve a la lista entera.
  useEffect(() => {
    if (soloSinTrailer && cargado && sinTrailer === 0) setSoloSinTrailer(false)
  }, [soloSinTrailer, cargado, sinTrailer])
  // Cuántos elementos tiene el lote filtrado: lo dice cualquiera de sus filas (antes de recargar, la del chip pulsado).
  const loteTotal = loteFiltro ? jobs.find(j => j.lote_id === loteFiltro)?.lote_total ?? null : null
  // Solo el del lote que se mira: al cambiar de lote, el anterior no vale hasta que llegue el nuevo.
  const resumenDelLote = loteResumen?.lote_id === loteFiltro ? loteResumen : null
  // El lote filtrado ya no tiene ninguna subida: la tabla vacía no es «sin resultados» sino «lote vacío».
  const loteVacio = loteFiltro !== null && loteSinSubidas === loteFiltro
  // «Ver el lote» desde el detalle de una de sus subidas: de vuelta a la tabla, con el lote entero (sin otros filtros).
  const verLote = (loteId: string) => {
    closeUpload()
    setSearch(''); setStatusFilter(''); setTypeFilter('')
    setLoteFiltro(loteId)
  }
  // El mismo salto que CLAVE_ABRIR_LOTE, pero con Subidas ya montada: la campana, al pulsar el aviso de un lote.
  const verLoteRef = useRef(verLote)
  verLoteRef.current = verLote
  useEffect(() => {
    const onAbrirLote = (e: Event) => {
      const lote = (e as CustomEvent<string>).detail
      if (typeof lote !== 'string' || !PATRON_LOTE_ID.test(lote)) return
      try { sessionStorage.removeItem(CLAVE_ABRIR_LOTE) } catch { /* ignore */ }
      verLoteRef.current(lote)
    }
    window.addEventListener(EVENTO_ABRIR_LOTE, onAbrirLote)
    return () => window.removeEventListener(EVENTO_ABRIR_LOTE, onAbrirLote)
  }, [])
  // Las que faltan por cargar (sin total conocido, si la lista llena el límite pedido). La búsqueda por título y el
  // filtro de tipo miran solo las cargadas.
  const hayMas = hayMasPorCargar(total, jobs.length, limite, MAX_LIMITE)
  const cargarMas = () => setLimite(l => Math.min(l + PAGINA, MAX_LIMITE))
  // Al salir, de vuelta a la tabla sin filtros y con el orden por defecto.
  useAlSalir(() => {
    closeUpload()
    quitarFiltros()
    setSortBy('created_desc')
  })

  // Deep link: /uploads/:id — resolve straight from the URL. selectedJob's
  // own `jobs.find(...)` gracefully returns undefined until `jobs` finishes
  // loading, so this doesn't need to wait on it like the sessionStorage
  // hand-off above does. On mount this only OPENS (never force-closes on a
  // bare /uploads) — the sessionStorage/CustomEvent hand-offs above land on a
  // bare /uploads path themselves and may resolve the same tick, so
  // unconditionally clearing here on mount would win the race and wipe out
  // whatever they just opened. Closing on a bare path is left to real
  // back/forward navigation (popstate), which happens well after mount.
  useEffect(() => {
    const id = getDetailIdFromPath('uploads')
    if (id != null) setSelectedJobId(id)

    const onPopState = () => setSelectedJobId(getDetailIdFromPath('uploads'))
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  // Same hand-off, but live — the sessionStorage read above only re-checks
  // when `jobs` changes, so clicking a notification while Subidas is
  // ALREADY the open page (no reload, `jobs` unchanged) would otherwise do
  // nothing. NotificationBell dispatches this same-tick, in addition to
  // stashing sessionStorage for the cross-page case.
  useEffect(() => {
    const onOpenUpload = (e: Event) => {
      const id = (e as CustomEvent<number>).detail
      if (id == null) return
      try { sessionStorage.removeItem('emuwarez_open_upload') } catch { /* ignore */ }
      openUpload(id)
    }
    window.addEventListener('emuwarez:open-upload', onOpenUpload)
    return () => window.removeEventListener('emuwarez:open-upload', onOpenUpload)
  }, [openUpload]) // identidad fija (useCallback sin dependencias): el oyente se pone una sola vez

  // Mientras haya algún trabajo vivo (staging/queued/preparing/running), la
  // tabla se mantiene al día. Ahora lo hace el aviso del servidor, y el sondeo
  // de 2,5 s queda de red de seguridad para cuando el canal está caído.
  const hasLiveJob = jobs.some(j => j.status === 'running' || j.status === 'preparing' || j.status === 'queued' || j.status === 'staging')
  // La cadencia depende de si hay algo en marcha, pero el sondeo NO se apaga
  // del todo: es la red de seguridad de la que dependía enterarse de una
  // subida nueva cuando el aviso se perdía, y apagarla dejaba la tabla clavada
  // hasta recargar la página. Mirando y sin nada vivo, preguntar una vez cada
  // 30 s no se nota.
  useRefrescoPorAvisos(AVISOS_SUBIDA, () => load({ silent: true }),
    { cadaSinCanal: hasLiveJob ? 2500 : 30_000, soloVisible: AVISOS_SUBIDA_PROGRESO, recargarProgreso: cargarProgreso })

  // Cancelar mientras se preparan ficheros no interrumpe nada a media faena,
  // así que se avisa antes: lo que pasa de verdad no es obvio desde el botón.
  const [confirmCancel, setConfirmCancel] = useState<UploadJob | null>(null)
  const handleCancel = async (jobId: number) => {
    const job = jobs.find(j => j.id === jobId)
    if (job?.status === 'preparing') { setConfirmCancel(job); return }
    await doCancel(jobId)
  }
  // Solo se enseña el fallo de la acción más reciente: cada reintento o cancelación toma un número y un fallo que
  // llega tarde de una acción anterior se descarta.
  const ultimaAccion = useRef(0)
  const doCancel = async (jobId: number) => {
    setConfirmCancel(null)
    const mia = ++ultimaAccion.current
    setCancelError(null)
    setRetryError(null)
    const titulo = jobs.find(j => j.id === jobId)?.title
    try {
      await cancelUploadJob(jobId)
    } catch (e: unknown) {
      if (mia !== ultimaAccion.current) return
      const motivo = e instanceof Error ? e.message : 'error desconocido'
      setCancelError(titulo ? `No se pudo cancelar «${titulo}»: ${motivo}` : `No se pudo cancelar la subida: ${motivo}`)
      return
    }
    await load()
  }

  // Sin confirmación: reintentar solo crea un trabajo nuevo en cola (el mismo camino que
  // «Subir a eMuwarez») y no toca el original, fallido o cancelado. Salta a la ficha del
  // trabajo nuevo para ver su progreso en el momento. Los errores se enseñan, no se tragan:
  // un fallo silencioso aquí parecía un botón que no hacía nada.
  const handleRetryQueued = async (job: UploadJob, desdeElPrincipio = false) => {
    const mia = ++ultimaAccion.current
    setRetryError(null)
    setCancelError(null)
    try {
      const r = await retryUploadJobQueued(job.id!, desdeElPrincipio)
      await load()
      // Si mientras tanto se pulsó otra acción, abrir esta ficha taparía lo que pasó con aquella.
      if (r.job?.id != null && mia === ultimaAccion.current) openUpload(r.job.id)
    } catch (e: unknown) {
      if (mia !== ultimaAccion.current) return
      setRetryError(e instanceof Error ? e.message : 'No se pudo reintentar la subida.')
    }
  }

  // Cancelar y reintentar leen `jobs` y `load`, que cambian a cada recarga: a las filas memorizadas les llega una
  // función con identidad fija que llama a la versión del último pintado.
  const handleCancelRef = useRef(handleCancel)
  handleCancelRef.current = handleCancel
  const alCancelar = useCallback((jobId: number) => handleCancelRef.current(jobId), [])
  const handleRetryRef = useRef(handleRetryQueued)
  handleRetryRef.current = handleRetryQueued
  const alReintentar = useCallback((job: UploadJob) => handleRetryRef.current(job), [])

  // Same sessionStorage handoff RequestsPage uses to open a specific
  // library item's detail page in Biblioteca.
  const goToLibraryItem = (id: number) => {
    try { sessionStorage.setItem('emuwarez_open_library_item', String(id)) } catch { /* ignore */ }
    onNavigate?.('library')
  }

  // Retry chains: a job created via "Reintentar" points back at the job it
  // retried (retry_of_job_id), and the original is never touched — so a
  // retried item would otherwise show up twice (or more) as unrelated-
  // looking rows. Only the LATEST attempt in each chain (the one nothing
  // else retries) shows as a normal row; its older attempts are reachable
  // by expanding the Reintentos cell instead of cluttering the main list.
  const jobById = useMemo(() => new Map(jobs.map(j => [j.id!, j])), [jobs])
  const hasNewerRetry = useMemo(() => {
    const s = new Set<number>()
    for (const j of jobs) if (j.retry_of_job_id != null) s.add(j.retry_of_job_id)
    return s
  }, [jobs])
  const ancestorsOf = (job: UploadJob): UploadJob[] => {
    const chain: UploadJob[] = []
    let cur = job.retry_of_job_id != null ? jobById.get(job.retry_of_job_id) : undefined
    while (cur) {
      chain.push(cur)
      cur = cur.retry_of_job_id != null ? jobById.get(cur.retry_of_job_id) : undefined
    }
    return chain // most recent ancestor first, original last
  }
  const toggleChain = useCallback((jobId: number) => {
    setExpandedChains(prev => {
      const next = new Set(prev)
      if (next.has(jobId)) next.delete(jobId); else next.add(jobId)
      return next
    })
  }, [])

  // Client-side filter + sort over the loaded jobs (status stays server-side).
  const grupos = useMemo(() => {
    const q = search.trim().toLowerCase()
    const out = jobs.filter(j => {
      if (HIDDEN_STATES.has(j.status)) return false
      if (hasNewerRetry.has(j.id!)) return false // shown nested under its retry instead
      const titleOk = !q || (j.title || '').toLowerCase().includes(q)
      const typeOk = !typeFilter || j.media_type === typeFilter
      return titleOk && typeOk
    })
    const dt = (s?: string | null) => (s ? new Date(s).getTime() : 0)
    const comparar = (a: UploadJob, b: UploadJob) => {
      switch (sortBy) {
        case 'created_asc': return dt(a.created_at) - dt(b.created_at)
        case 'title': return (a.title || '').localeCompare(b.title || '')
        case 'status': return ordenDeEstado(a.status) - ordenDeEstado(b.status)
        case 'duration_desc': return (b.duration_seconds ?? -1) - (a.duration_seconds ?? -1)
        default: return dt(b.created_at) - dt(a.created_at) // created_desc
      }
    }
    // Hay una cadena por elemento y tracker: las puntas del mismo elemento son sus subidas a cada tracker y van juntas,
    // en el orden elegido; el grupo va donde va la primera. Dos del mismo tracker (un filtro de estado deja ver un
    // intento anterior como punta) no se juntan.
    const porElemento = new Map<number, UploadJob[]>()
    for (const j of out.sort(comparar)) {
      const grupo = j.library_item_id != null ? porElemento.get(j.library_item_id) : undefined
      if (grupo && !grupo.some(o => o.tracker === j.tracker)) grupo.push(j)
      else porElemento.set(grupo || j.library_item_id == null ? -j.id! : j.library_item_id, [j])
    }
    return [...porElemento.values()]
  }, [jobs, search, sortBy, typeFilter, hasNewerRetry])
  const subidasVisibles = grupos.reduce((n, g) => n + g.length, 0)

  // La de la lista o, si es más antigua que las cargadas (enlace /uploads/123, notificación, Panel), la pedida por su id.
  const [jobSuelto, setJobSuelto] = useState<UploadJob | null>(null)
  const enLista = selectedJobId != null ? jobs.find(j => j.id === selectedJobId) : undefined
  // Depende de si está en la lista, no de la lista entera: cada recarga la sustituye y cancelaría la petición en vuelo.
  const seleccionadaEnLista = enLista != null
  useEffect(() => {
    if (selectedJobId == null || !cargado) return
    if (seleccionadaEnLista) { setJobSuelto(null); return }
    let alive = true
    getUploadJob(selectedJobId)
      .then(j => { if (alive) setJobSuelto(j) })
      .catch(e => {
        // Ya no existe (la borraron): fuera de una ficha vacía.
        if (alive && e instanceof ApiClientError && e.status === 404) closeUpload()
      })
    return () => { alive = false }
  }, [selectedJobId, cargado, seleccionadaEnLista])
  const selectedJob = enLista ?? (jobSuelto?.id === selectedJobId ? jobSuelto : undefined)
  const recargarSuelta = () => {
    if (jobSuelto && selectedJobId === jobSuelto.id) {
      getUploadJob(jobSuelto.id!).then(j => setJobSuelto(actual => (actual?.id === j.id ? j : actual))).catch(() => {})
    }
  }
  // «Ya lo he añadido» (o deshacerlo) o «Reenviar a qBittorrent» en el detalle: la lista y el aviso de arriba, al día;
  // y la ficha suelta (una subida que no está entre las cargadas), pedida otra vez.
  const recargarSubida = () => {
    load({ silent: true })
    recargarSuelta()
  }
  // Los avisos solo recargan la lista: la ficha suelta se pide otra vez cuando cambia (un reenvío que termina).
  useRefrescoPorAvisos(AVISOS_SUBIDA, recargarSuelta, {
    cadaSinCanal: 2500, activo: jobSuelto?.reenvio_qbit_estado === 'en_curso', filtro: aviso => aviso.job_id === jobSuelto?.id,
  })

  // The list row (above) never carries display_title — only the single-job
  // GET computes the canonical "Serie (Año)"-style title, to avoid a
  // parent-walk query per list row — so fetch it once per selection and
  // merge just that field into the (still live-polled) list-derived job,
  // instead of replacing it outright and losing further progress updates.
  // Lo mismo con `subir_tambien_a` (los trackers a los que se puede subir también): solo lo trae el GET de una.
  const [selectedTitle, setSelectedTitle] = useState<{
    id: number; display_title: string | null; subir_tambien_a: UploadJob['subir_tambien_a']
  } | null>(null)
  useEffect(() => {
    if (selectedJobId == null) { setSelectedTitle(null); return }
    let alive = true
    getUploadJob(selectedJobId).then(j => {
      if (alive) setSelectedTitle({ id: selectedJobId, display_title: j.display_title ?? null, subir_tambien_a: j.subir_tambien_a })
    }).catch(() => {})
    return () => { alive = false }
  }, [selectedJobId])
  const selectedJobWithTitle = selectedJob && selectedTitle && selectedTitle.id === selectedJob.id
    ? { ...selectedJob, display_title: selectedTitle.display_title, subir_tambien_a: selectedTitle.subir_tambien_a }
    : selectedJob

  // La fila del último intento de una cadena y, si está desplegada, sus intentos anteriores.
  const pintarCadena = (job: UploadJob, enGrupo?: 'primera' | 'otra', loteArriba?: string | null, otras?: UploadJob[], trackersAbiertos?: boolean) => {
    const ancestors = ancestorsOf(job)
    const expanded = expandedChains.has(job.id!)
    return [
      <JobRow
        key={job.id}
        job={job}
        instances={instances}
        seeding={seeding}
        onOpen={openUpload}
        enGrupo={enGrupo}
        otrosTrackers={otras?.map(o => o.tracker_nombre).join(' · ')}
        trackersAbiertos={trackersAbiertos}
        otroFallido={otras?.some(o => RETRYABLE_STATUSES.has(o.status))}
        onToggleTrackers={otras ? toggleTrackers : undefined}
        intentosAnteriores={ancestors.length > 0 ? ancestors.length : undefined}
        desplegada={expanded}
        onToggleCadena={toggleChain}
        onCancel={alCancelar}
        onDelete={setDeleteJob}
        onRetry={alReintentar}
        // Con la lista ya filtrada por ese lote, el banner lo dice: la chapa en cada fila sobra.
        // Y debajo de otra del mismo lote, también.
        onSubirTambien={job.id != null && paraSubirTambien.has(job.id) ? abrirSubirTambien : undefined}
        onFiltrarLote={job.lote_id === loteFiltro || (enGrupo === 'otra' && job.lote_id === loteArriba) ? undefined : setLoteFiltro}
      />,
      ...(expanded ? ancestors.map(a => (
        <JobRow key={a.id} job={a} instances={instances} seeding={seeding} indent onOpen={openUpload} onCancel={alCancelar} onRetry={alReintentar} onDelete={setDeleteJob} />
      )) : []),
    ]
  }
  // Un elemento: la cadena de cada tracker, una debajo de otra; las de los otros trackers, si el grupo está abierto.
  const pintarGrupo = (grupo: UploadJob[]) => {
    const [primera, ...otras] = grupo
    if (!otras.length) return pintarCadena(primera)
    const abiertos = trackersElegidos.get(primera.library_item_id!) ?? otras.some(o => o.status !== 'completed')
    return [
      ...pintarCadena(primera, 'primera', null, otras, abiertos),
      ...(abiertos ? otras.flatMap(o => pintarCadena(o, 'otra', primera.lote_id)) : []),
    ]
  }
  const scrollRef = useRef<HTMLDivElement>(null)
  const theadRef = useRef<HTMLTableSectionElement>(null)

  const modals = (
    <>
      {confirmCancel && (
        <Modal titulo="Cancelar una subida que se está preparando" onClose={() => setConfirmCancel(null)} className="sub-modal-520">
          <div className="modal-body">
            <p className="sub-t13">
              <strong>«{confirmCancel.title}»</strong> está preparando ficheros: puede estar renombrando
              o reordenando pistas. Eso no se puede interrumpir a mitad sin dejar un archivo a medias.
            </p>
            <p className="sub-t13">
              Se marcará como <strong>cancelada</strong> y se detendrá al terminar el paso en curso, no
              al instante. Si el proceso ya no existe (por ejemplo, porque se paró la aplicación), queda
              cancelada directamente.
            </p>
            <p className="sub-muted sub-t13">
              En ambos casos podrás <strong>reintentarla</strong> después. Revisa el archivo antes de
              hacerlo, por si quedó algo a medio renombrar.
            </p>
            <div className="sub-modal-pie">
              <button className="btn btn-secondary" type="button" onClick={() => setConfirmCancel(null)}>
                Dejarla seguir
              </button>
              <button className="btn btn-primary" type="button" onClick={() => doCancel(confirmCancel.id!)}>
                Cancelar la subida
              </button>
            </div>
          </div>
        </Modal>
      )}
      {startJob && <StartModal job={startJob} onClose={() => setStartJob(null)} onDone={() => { setStartJob(null); load() }} />}
      {deleteJob && (
        <DeleteUploadModal
          job={deleteJob}
          onClose={() => setDeleteJob(null)}
          onDeleted={() => { setDeleteJob(null); closeUpload(); load() }}
        />
      )}
    </>
  )

  // Dedicated detail page — shown in place of the list when a job is
  // selected, instead of the old JobProgressPanel modal.
  if (selectedJobWithTitle) {
    return (
      <div className="sub-pagina">
        <UploadJobDetailPage
          job={selectedJobWithTitle}
          onBack={closeUpload}
          onStart={j => setStartJob(j)}
          onRetry={(j, desdeElPrincipio) => handleRetryQueued(j, desdeElPrincipio)}
          onCancel={id => handleCancel(id)}
          onDelete={j => setDeleteJob(j)}
          onOpenLibraryItem={goToLibraryItem}
          onVerLote={verLote}
          onTrailerCambiado={recargarSubida}
          onReenviado={recargarSubida}
          seeding={seeding}
          retryError={retryError ?? cancelError}
        />
        {modals}
      </div>
    )
  }

  return (
    <div data-tour="uploads-content" className="sub-pagina">
      {modals}

      <div className="page-header sub-no-encoge">
        <div>
          <h1 className="page-title">Subidas</h1>
          <div className="page-subtitle">
            {subidasVisibles} subidas{grupos.length < subidasVisibles ? ` de ${grupos.length} ${grupos.length === 1 ? 'elemento' : 'elementos'}` : ''}{total != null && jobs.length < total ? ` · ${jobs.length} de ${total} cargadas` : ''}
          </div>
        </div>
        <div className="sub-fila">
          {hasLiveJob && <span className="sub-en-vivo"><span aria-hidden="true">●</span> actualizando en vivo</span>}
          {hayOtrosTrackers && (
            <button type="button" className="btn btn-secondary" onClick={() => setSubirTambien({})}>
              Subir también a…
            </button>
          )}
        </div>
      </div>
      {subirTambien && (
        <SubirTambienModal inicial={subirTambien.inicial} onClose={() => setSubirTambien(null)}
          onEncolado={loteId => { if (loteId) verLote(loteId); else load({ silent: true }) }} />
      )}

      <div className="filters-bar sub-no-encoge">
        <input
          className="form-input sub-buscar"
          type="search"
          aria-label="Buscar subidas por título"
          autoComplete="off"
          placeholder="Buscar título…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        <select className="form-input form-select sub-orden" aria-label="Ordenar subidas" value={sortBy} onChange={e => setSortBy(e.target.value)}>
          <option value="created_desc">Orden: Reciente primero</option>
          <option value="created_asc">Orden: Antiguo primero</option>
          <option value="title">Orden: Título (A-Z)</option>
          <option value="status">Orden: Estado</option>
          <option value="duration_desc">Orden: Duración</option>
        </select>
      </div>

      {!error && <BannerSinTrailer total={sinTrailer} filtrando={soloSinTrailer} onFiltrar={setSoloSinTrailer} />}

      {/* Con la lista sin cargar, las acciones del lote no tienen sobre qué actuar. */}
      {loteFiltro && !error && (
        <BannerLote
          key={loteFiltro}
          loteId={loteFiltro}
          resumen={resumenDelLote}
          totalConocido={loteTotal}
          sinSubidas={loteVacio}
          onQuitar={() => setLoteFiltro(null)}
          onHecho={() => load()}
        />
      )}

      {/* Ver RequestsPage: solo bloquea si no hay nada que enseñar todavía. */}
      {loading && jobs.length === 0 && !filtrando ? <LoadingState label="Cargando Subidas…" /> : error ? <ErrorState message={error} onRetry={load}
        extra={loteFiltro ? <button className="btn btn-secondary btn-sm" onClick={() => setLoteFiltro(null)}>Quitar filtro del lote</button> : undefined} /> : jobs.length === 0 && !filtrando ? (
        <EmptyState
          icon={<IconoSubida />}
          title="Sin subidas"
          body="Sube algo desde Biblioteca o desde una petición lista."
          action={onNavigate && <button className="btn btn-primary btn-sm" onClick={() => onNavigate('library')}>Ir a Biblioteca</button>}
        />
      ) : (
        <div className="activity-section sub-seccion">
          {(retryError || cancelError) && (
            <div className="sub-error-accion" role="alert">
              <span>{retryError ?? cancelError}</span>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setRetryError(null); setCancelError(null) }}>Cerrar</button>
            </div>
          )}
          <div className="sub-scroll" ref={scrollRef}>
            <table className="data-table">
              <thead ref={theadRef}>
                <tr>
                  <th className="sub-th-titulo">Título</th>
                  <th className="sub-sec"><HeaderFilter label="Tipo" value={typeFilter} onChange={setTypeFilter} options={[
                    { value: '', label: 'Todos' }, { value: 'movie', label: 'Película' }, { value: 'series', label: 'Serie' }, { value: 'season', label: 'Temporada' }, { value: 'episode', label: 'Episodio' },
                  ]} /></th>
                  <th className="sub-sec">Origen</th>
                  <th className="col-prioridad-baja">Grupo</th>
                  <th className="col-prioridad-baja">Petición</th>
                  <th><HeaderFilter label="Estado" value={statusFilter} onChange={setStatusFilter} options={[
                    { value: '', label: 'Todos' }, ...STATUS_OPTIONS.filter(Boolean).map(s => ({ value: s, label: statusLabel(s) })),
                  ]} /></th>
                  <th className="sub-sec sub-col-seeding">Seeding</th>
                  <th className="sub-sec">Fecha</th>
                  <th className="col-actions sub-th-acciones">Acciones</th>
                </tr>
              </thead>
              {grupos.length === 0 && loteVacio && (
                <FilaVacia
                  columnas={COLUMNAS}
                  icon={<IconoSubida />}
                  title="Este lote ya no tiene subidas"
                  body="Se borraron todas las que lo formaban."
                  action={<button className="btn btn-primary btn-sm" onClick={() => setLoteFiltro(null)}>Quitar filtro del lote</button>}
                />
              )}
              {grupos.length === 0 && filtrando && !loteVacio && (
                <FilaVacia
                  columnas={COLUMNAS}
                  icon={<IconoSubida />}
                  title="Sin resultados"
                  body={hayMas
                    ? 'No hay subidas cargadas que coincidan con los filtros. Quedan más sin cargar.'
                    : 'No hay subidas que coincidan con los filtros.'}
                  action={
                    <div className="sub-vacio-acciones">
                      <button className="btn btn-primary btn-sm" onClick={quitarFiltros}>Quitar filtros</button>
                      {hayMas && <button className="btn btn-secondary btn-sm" onClick={cargarMas}>Cargar más</button>}
                    </div>
                  }
                />
              )}
              {/* Pasadas las UMBRAL_VIRTUAL subidas solo se pintan las que se ven: cada elemento es la fila del último
                  intento de cada tracker con sus intentos anteriores desplegados, en su propio <tbody>. */}
              {grupos.length > UMBRAL_VIRTUAL ? (
                <FilasVirtuales
                  items={grupos}
                  clave={claveDeGrupo}
                  alturaEstimada={ALTO_FILA}
                  columnas={COLUMNAS}
                  contenedor={scrollRef}
                  cabecera={theadRef}
                  pintar={pintarGrupo}
                />
              ) : (
                <tbody>{grupos.flatMap(pintarGrupo)}</tbody>
              )}
            </table>
          </div>
          {hayMas && (
            <div className="sub-mas">
              <span className="sub-muted sub-t12">
                {total != null ? `${jobs.length} de ${total} subidas cargadas` : `${jobs.length} subidas cargadas`}
              </span>
              <button type="button" className="btn btn-secondary btn-sm" onClick={cargarMas}>Cargar más</button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
