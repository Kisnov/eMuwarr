// Los elementos de una tanda (temporadas o capítulos), todos a la vista y uno elegido: sustituye a los desplegables
// con los que se recorrían de uno en uno. Cada fila es un botón con su título y lo que se sepa de ella.
export function ListaTanda({ etiqueta, ids, actual, titulo, onElegir, detalle, accion, deshabilitada }: {
  etiqueta: string
  ids: readonly number[]
  actual: number | null
  titulo: (id: number) => string
  onElegir: (id: number) => void
  // Lo que se sabe de cada una (su nombre, por dónde va, si no se subirá), dentro de su botón.
  detalle?: (id: number) => React.ReactNode
  // Otra acción de la fila, junto a su botón.
  accion?: (id: number) => React.ReactNode
  deshabilitada?: boolean
}) {
  return (
    <ul className="asist-lista-subida asist-lista-elegible" aria-label={etiqueta}>
      {ids.map(id => (
        <li key={id}>
          <button
            type="button" className="asist-lista-boton" disabled={deshabilitada}
            aria-current={id === actual ? 'true' : undefined} onClick={() => onElegir(id)}
          >
            <span className="asist-lista-titulo">{titulo(id)}</span>
            {detalle?.(id)}
          </button>
          {accion?.(id)}
        </li>
      ))}
    </ul>
  )
}
