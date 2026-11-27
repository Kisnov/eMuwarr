import { useEffect, useRef, useState } from 'react'
import { reenviarAQbit } from '../api/uploads'
import type { UploadJob } from '../types/api'
import type { SeedingEstado } from '../hooks/useSeeding'
import { formatDateTimeSeconds, formatNumero } from '../utils/format'
import { copiarTexto } from '../utils/portapapeles'
import { ExternalLinkButtonIcon } from './DetailCard'
import { useTooltip } from './Tooltip'
import { SIN_DATOS_QBIT, claseSeeding, resumenSeeding, seedingDe, textoSubido } from '../utils/seeding'
import { noLlegoAQbit } from '../utils/estadoDeSubida'

// La completada tiene su torrent sembrando; la que falló con el torrent ya en eMuwarez se puede reenviar.
const tieneTorrent = (job: UploadJob) => job.status === 'completed' || !!job.puede_reenviar_qbit

// La columna «Seeding» de la lista de Subidas.
export function SeedingCelda({ job, seeding }: { job: UploadJob; seeding: SeedingEstado }) {
  if (!tieneTorrent(job)) return <span className="sub-muted">—</span>
  if (seeding.cargando && !seeding.datos) return <span className="sub-muted sub-t11">Cargando…</span>
  if (!seeding.datos?.disponible) return <SinDatosQbit />
  const i = seedingDe(seeding.datos, job.id)
  if (!i) return <span className="sub-muted">—</span>
  return <span className={claseSeeding(i.estado)}>{resumenSeeding(i)}</span>
}

// Sin qBittorrent, un guion: «Sin datos de qBittorrent» repetido en cada fila completada no decía nada nuevo. El
// texto va en el tooltip y para el lector de pantalla, y el detalle de la subida lo explica.
function SinDatosQbit() {
  const { triggerProps, tooltipNode } = useTooltip(SIN_DATOS_QBIT)
  return (
    <span className="sub-muted" {...triggerProps}>
      <span aria-hidden="true">—</span><span className="solo-lector">{SIN_DATOS_QBIT}</span>
      {tooltipNode}
    </span>
  )
}

// El nombre del torrent en qBittorrent: lo último de la ruta preparada, que es la que se le entregó como ruta de guardado.
export const nombreDelTorrent = (job: UploadJob): string => (job.prepared_path ?? '').split(/[\\/]/).filter(Boolean).pop() ?? ''

// «Abrir en qBittorrent» (solo con la URL pública configurada) y «Copiar nombre». La WebUI no tiene enlace directo a
// un torrent: se abre la WebUI y el nombre copiado sirve para pegarlo en su filtro.
function AccionesQbit({ job, url }: { job: UploadJob; url: string }) {
  const [copia, setCopia] = useState<'ok' | 'error' | null>(null)
  const nombre = nombreDelTorrent(job)
  return (
    <>
      <a className="btn btn-secondary btn-sm dsub-sin-subrayado" href={url} target="_blank" rel="noopener noreferrer">
        <ExternalLinkButtonIcon /> Abrir en qBittorrent
      </a>
      {nombre && (
        <button type="button" className="btn btn-secondary btn-sm" aria-live="polite" title="Copia el nombre del torrent para buscarlo en qBittorrent"
          onClick={async () => setCopia((await copiarTexto(nombre)) ? 'ok' : 'error')}>
          {copia === 'ok' ? 'Nombre copiado' : copia === 'error' ? 'No se pudo copiar' : 'Copiar nombre'}
        </button>
      )}
    </>
  )
}

// El tracker de la subida: «Reenviar a qBittorrent» pide el torrent al suyo (N.O.B.S, Milnueve…), no a eMuwarez.
const ayudaReenvio = (tracker: string) => `Ya está en ${tracker}, pero qBittorrent no lo tiene. Vuelve a añadir el `
  + `torrent de ${tracker} sin subir nada; qBittorrent comprueba los ficheros antes de sembrar.`

// El bloque completo del detalle de una subida. Pide los datos al entrar (la caché del servidor evita machacar
// a qBittorrent) y «Actualizar» se salta esa caché.
export function SeedingBloque({ job, seeding, onVerRegistro, onReenviado }: {
  job: UploadJob
  seeding: SeedingEstado
  // «Ver registro» tras un reenvío fallido, y la recarga de la subida tras pedir uno.
  onVerRegistro?: () => void
  onReenviado?: () => void
}) {
  const { datos, cargando, cargar } = seeding
  const aplica = tieneTorrent(job)
  useEffect(() => { if (aplica) cargar() }, [aplica, cargar])
  // «Reenviar a qBittorrent»: `enviando` cubre desde el clic hasta que llega la subida con el estado nuevo.
  const [enviando, setEnviando] = useState(false)
  const [errorEnvio, setErrorEnvio] = useState<string | null>(null)
  const estadoReenvio = job.reenvio_qbit_estado ?? null
  useEffect(() => { setEnviando(false) }, [job.id, estadoReenvio, job.reenvio_qbit_at])
  useEffect(() => { setErrorEnvio(null) }, [job.id])
  // Al terminar bien se vuelve a leer el seeding: el servidor ya olvidó su caché.
  const estadoAnterior = useRef(estadoReenvio)
  useEffect(() => {
    if (estadoAnterior.current !== 'ok' && estadoReenvio === 'ok') cargar()
    estadoAnterior.current = estadoReenvio
  }, [estadoReenvio, cargar])
  if (!aplica) return null
  const i = seedingDe(datos, job.id)
  const sinDatos = !datos || !datos.disponible
  const reenviando = enviando || estadoReenvio === 'en_curso'
  // Solo cuando hace falta: el añadido a qBittorrent falló, o qBittorrent responde y no tiene el torrent.
  const ofrecerReenvio = !!job.puede_reenviar_qbit && (noLlegoAQbit(job) || (!sinDatos && (!i || i.estado === 'no_encontrado')))
  const idAyuda = `reenvio-qbit-${job.id}`
  const reenviar = async () => {
    setEnviando(true)
    setErrorEnvio(null)
    try {
      await reenviarAQbit(job.id!)
      onReenviado?.()
    } catch (e) {
      setEnviando(false)
      setErrorEnvio(e instanceof Error ? e.message : 'No se pudo reenviar a qBittorrent.')
    }
  }
  return (
    <div className="dsub-panel" data-testid="seeding-bloque">
      <h2 className="dsub-panel-titulo">
        Seeding en qBittorrent
        {cargando && <span className="spinner dsub-spinner-13" />}
      </h2>
      <div className="detail-card dsub-caja seeding-caja">
        {sinDatos ? (
          <div className="seeding-sin-datos">
            <strong>{SIN_DATOS_QBIT}</strong>
            <span className="sub-muted sub-t12">
              {datos?.mensaje ?? (cargando ? 'Consultando…' : 'No se pudo consultar. Esto no afecta a la subida.')}
            </span>
          </div>
        ) : !i ? (
          <div className="seeding-sin-datos">
            <span className={claseSeeding('no_encontrado')}>No se encuentra</span>
            <span className="sub-muted sub-t12">
              No hay un torrent único en qBittorrent que corresponda a esta subida
              (puede haberse borrado, estar en otra ruta o haber varios iguales).
            </span>
          </div>
        ) : (
          <dl className="seeding-datos">
            <div><dt>Estado</dt><dd><span className={claseSeeding(i.estado)}>{i.etiqueta}</span></dd></div>
            <div><dt>Ratio</dt><dd>{formatNumero(i.ratio, 2)}</dd></div>
            <div><dt>Subido</dt><dd>{textoSubido(i)}</dd></div>
            <div><dt>Progreso</dt><dd>{i.progreso != null ? `${formatNumero(i.progreso * 100, 1)} %` : '—'}</dd></div>
            <div><dt>Seeds / peers</dt><dd>{formatNumero(i.seeds)} / {formatNumero(i.peers)}</dd></div>
          </dl>
        )}
        {i?.estado === 'faltan_ficheros' && (
          <p className="seeding-aviso">
            qBittorrent no encuentra todos los ficheros de este torrent: comprueba que la carpeta de subidas
            sigue en su sitio y que qBittorrent la ve en la misma ruta.
          </p>
        )}
        {(ofrecerReenvio || reenviando) && <p className="seeding-reenvio" id={idAyuda}>{ayudaReenvio(job.tracker_nombre || 'eMuwarez')}</p>}
        <p className="seeding-reenvio" aria-live="polite" data-testid="reenvio-resultado">
          {reenviando ? null : errorEnvio ? (
            <span className="seeding-reenvio-error">{errorEnvio}</span>
          ) : estadoReenvio === 'ok' ? (
            <span className="seeding-reenvio-ok">
              Reenviado a qBittorrent{job.reenvio_qbit_at ? ` el ${formatDateTimeSeconds(job.reenvio_qbit_at)}` : ''}: qBittorrent
              comprobó los ficheros y lo siembra.
            </span>
          ) : estadoReenvio === 'fallido' && ofrecerReenvio ? (
            <>
              <span className="seeding-reenvio-error">El último reenvío a qBittorrent falló.</span>
              {onVerRegistro && <button type="button" className="btn btn-ghost btn-sm" onClick={onVerRegistro}>Ver registro</button>}
            </>
          ) : null}
        </p>
        <div className="seeding-pie">
          <span className="sub-muted sub-t11">
            {datos?.actualizado ? `Leído ${formatDateTimeSeconds(datos.actualizado)}` : ''}
          </span>
          <div className="seeding-acciones">
            {datos?.url_publica && <AccionesQbit job={job} url={datos.url_publica} />}
            {(ofrecerReenvio || reenviando) && (
              <button type="button" className="btn btn-secondary btn-sm" disabled={reenviando} aria-describedby={idAyuda}
                onClick={reenviar}>
                {reenviando ? 'Reenviando…' : 'Reenviar a qBittorrent'}
              </button>
            )}
            <button type="button" className="btn btn-secondary btn-sm" disabled={cargando} onClick={() => cargar(true)}>
              Actualizar
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
