import type { LibraryItemResponse } from '../types/api'
import { Tooltip } from './Tooltip'

// La chapa «Por identificar» de una fila de carpeta manual sin ficha de TMDb: abre el diálogo que la confirma.
export function ChipPorIdentificar({ item, onIdentificar, className }: {
  item: LibraryItemResponse
  onIdentificar: (item: LibraryItemResponse) => void
  className?: string
}) {
  const s = item.sugerencia_tmdb
  const ayuda = s
    ? `¿Es ${s.titulo}${s.anio ? ` (${s.anio})` : ''}? Confírmalo para poder subirlo.`
    : 'Busca su ficha en TMDb y confírmala para poder subirlo.'
  return (
    <Tooltip content={ayuda}>
      <button
        type="button"
        className={`badge status-pending bib-chip-identificar${className ? ` ${className}` : ''}`}
        onClick={e => { e.stopPropagation(); onIdentificar(item) }}
      >
        Por identificar
      </button>
    </Tooltip>
  )
}
