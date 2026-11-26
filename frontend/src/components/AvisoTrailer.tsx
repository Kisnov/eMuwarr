// El aviso «Sin trailer»: eMuwarez exige un tráiler en cada subida. Uploadrr-EMU lo pone solo cuando TMDb tiene uno
// y, si no, la subida sale sin él y hay que añadirlo a mano editando el torrent. La campana ya lo decía («Requiere
// Trailer») pero se perdía entre los demás avisos: aquí se ve en la fila, arriba de la lista y en el detalle, hasta
// que el usuario dice que ya lo puso (upload_jobs.trailer_estado, services/trailer_check.py).
import { useState } from 'react'
import { marcarTrailerAMano } from '../api/uploads'
import type { UploadJob } from '../types/api'
import { IconoAviso } from './Iconos'
import { useTooltip } from './Tooltip'

const TORRENT_EN_EMU = /^(https?:\/\/[^/]+\/torrents\/\d+)\/?$/

/** La página de editar el torrent en eMuwarez (UNIT3D: /torrents/<id>/edit), donde se pone el tráiler a mano. */
export function urlEditarEnEmu(resultUrl?: string | null): string | null {
  const m = resultUrl ? TORRENT_EN_EMU.exec(resultUrl.trim()) : null
  return m ? `${m[1]}/edit` : null
}

export const faltaTrailer = (job: Pick<UploadJob, 'trailer_estado'>) => job.trailer_estado === 'falta'

type DeSuTracker = Pick<UploadJob, 'tracker' | 'tracker_nombre'>

// El tracker de la subida: el torrent se edita allí, no siempre en eMuwarez.
const nombreDe = (job: DeSuTracker) => job.tracker_nombre || 'eMuwarez'

const explicacion = (job: DeSuTracker) => !job.tracker || job.tracker === 'emu'
  ? 'eMuwarez exige un tráiler y TMDb no tiene ninguno para este contenido, así que la subida salió sin él.'
  : `TMDb no tiene ningún tráiler para este contenido, así que la subida a ${nombreDe(job)} salió sin él.`

/** La chapa de la fila: abre la edición del torrent en su tracker (o el detalle, si no se sabe su enlace). */
export function ChipSinTrailer({ job, onAbrir }: { job: UploadJob; onAbrir: () => void }) {
  const editar = urlEditarEnEmu(job.result_url)
  const tip = useTooltip(<>{explicacion(job)} {editar ? `Pulsa para editar el torrent en ${nombreDe(job)} y añadirlo.` : 'Pulsa para ver qué hacer.'}</>)
  const contenido = <><IconoAviso size={12} /> Sin trailer{tip.tooltipNode}</>
  return editar ? (
    <a className="sub-trailer-chip" href={editar} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()} {...tip.triggerProps}>
      {contenido}
    </a>
  ) : (
    <button type="button" className="sub-trailer-chip" onClick={e => { e.stopPropagation(); onAbrir() }} {...tip.triggerProps}>
      {contenido}
    </button>
  )
}

/** Arriba de la lista de Subidas: cuántas esperan su tráiler, y ver solo esas. */
export function BannerSinTrailer({ total, filtrando, onFiltrar }: {
  total: number
  filtrando: boolean
  onFiltrar: (solo: boolean) => void
}) {
  if (total <= 0 && !filtrando) return null
  return (
    <div className="sub-trailer-banner" role="status">
      <span className="sub-trailer-banner-icono"><IconoAviso size={26} /></span>
      <div className="sub-trailer-banner-texto">
        <strong className="sub-trailer-banner-titulo">
          {total === 1 ? '1 subida sin trailer' : `${total} subidas sin trailer`}
        </strong>
        <span>
          eMuwarez exige un tráiler en cada subida y TMDb no tenía ninguno. Añádelo a mano editando el torrent
          (pulsa en «Sin trailer» de la fila) y márcalo como añadido en su detalle.
        </span>
      </div>
      <button type="button" className="btn btn-sm sub-trailer-banner-boton" onClick={() => onFiltrar(!filtrando)}>
        {filtrando ? 'Ver todas' : total === 1 ? 'Ver cuál' : 'Ver solo estas'}
      </button>
    </div>
  )
}

/** En el detalle de la subida: qué falta, el enlace para ponerlo y «Ya lo he añadido» (o deshacerlo). */
export function AvisoTrailerDetalle({ job, onCambiado }: { job: UploadJob; onCambiado: () => void }) {
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const estado = job.trailer_estado
  if (estado !== 'falta' && estado !== 'a_mano') return null
  const editar = urlEditarEnEmu(job.result_url)

  const marcar = async (puesto: boolean) => {
    setEnviando(true); setError(null)
    try {
      await marcarTrailerAMano(job.id!, puesto)
      onCambiado()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'No se pudo guardar.')
    } finally {
      setEnviando(false)
    }
  }

  if (estado === 'a_mano') {
    return (
      <p className="dsub-trailer-a-mano">
        Tráiler añadido a mano.{' '}
        <button type="button" className="dsub-lote-ver" onClick={() => marcar(false)} disabled={enviando}>Deshacer</button>
        {error && <span className="error-text"> {error}</span>}
      </p>
    )
  }
  return (
    <div className="dsub-trailer-aviso" role="alert">
      <span className="dsub-trailer-aviso-icono"><IconoAviso size={30} /></span>
      <div className="dsub-trailer-aviso-texto">
        <strong className="dsub-trailer-aviso-titulo">Falta el trailer</strong>
        <span>
          {explicacion(job)} Edita el torrent en {nombreDe(job)} y pon el tráiler de YouTube en la descripción
          (<code>[youtube]ID[/youtube]</code>).
        </span>
        <div className="dsub-trailer-aviso-acciones">
          {editar && (
            <a className="btn btn-primary btn-sm" href={editar} target="_blank" rel="noreferrer">Editar en {nombreDe(job)}</a>
          )}
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => marcar(true)} disabled={enviando}>
            {enviando ? 'Guardando…' : 'Ya lo he añadido'}
          </button>
        </div>
        {error && <span className="error-text">{error}</span>}
      </div>
    </div>
  )
}
