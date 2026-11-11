/**
 * La casilla para marcar un elemento para «Subir por lotes»: en Biblioteca, en la fila de la lista y en las
 * tarjetas de póster; en el Panel, en las filas de «Listo para subir»; en Peticiones, en la celda Título de las que
 * se pueden subir. El clic no sale de ella: ni despliega la fila ni abre la ficha. `titulo` (por defecto el `title` del elemento) es el nombre que oye el lector.
 */
export function CasillaLote<T extends { title: string | null }>({
  item, titulo, seleccionado, bloqueada = false, motivo = null, onAlternar, className,
}: {
  item: T
  titulo?: string
  seleccionado: boolean
  // Lleno el lote: las que no están marcadas no se pueden marcar.
  bloqueada?: boolean
  // Por qué este elemento no se puede subir (Biblioteca): sin marcar, la casilla queda apagada y lo dice. Una ya
  // marcada se deja desmarcar siempre.
  motivo?: string | null
  onAlternar: (item: T) => void
  className?: string
}) {
  const apagada = !seleccionado && (bloqueada || !!motivo)
  const explicacion = seleccionado ? undefined : motivo ? `No se puede subir: ${motivo}` : bloqueada ? 'El lote ya está lleno' : undefined
  return (
    <label
      className={className ? `checkbox-label lote-casilla ${className}` : 'checkbox-label lote-casilla'}
      title={explicacion}
      onClick={e => e.stopPropagation()}
      onMouseDown={e => e.stopPropagation()}
    >
      <input
        type="checkbox"
        checked={seleccionado}
        disabled={apagada}
        onChange={() => onAlternar(item)}
        aria-description={explicacion}
        aria-label={`Seleccionar «${titulo ?? item.title ?? ''}»`}
      />
    </label>
  )
}
