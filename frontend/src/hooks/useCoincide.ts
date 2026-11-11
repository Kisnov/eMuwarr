import { useEffect, useState } from 'react'

/** Si una media query coincide ahora, y se actualiza al cambiar el ancho de la ventana. Sin `matchMedia` (jsdom) no coincide. */
export function useCoincide(consulta: string): boolean {
  const [coincide, setCoincide] = useState(() => window.matchMedia?.(consulta).matches ?? false)

  useEffect(() => {
    const lista = window.matchMedia?.(consulta)
    if (!lista) return
    const alCambiar = () => setCoincide(lista.matches)
    alCambiar()
    lista.addEventListener('change', alCambiar)
    return () => lista.removeEventListener('change', alCambiar)
  }, [consulta])

  return coincide
}
