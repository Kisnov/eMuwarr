/**
 * Llevar un elemento a la vista moviendo SOLO el contenedor que se desplaza (`.page-container`, el panel de la Guía…).
 *
 * `Element.scrollIntoView()` desplaza todos los ancestros, también la ventana y los marcos con `overflow: hidden`, y con
 * el `zoom` del body (utils/uiScale) los cálculos salen en un espacio distinto al de `scrollTop`. En el móvil, con la
 * interfaz al 50-60 %, saltar a la última tarjeta de Configuración desplazaba la propia ventana: la aplicación subía
 * y por debajo quedaba el fondo vacío, de modo que solo se podía usar la franja visible.
 *
 * Aquí se mide con `medir()` (espacio sin escalar, el mismo en que se expresa `scrollTop`) y se mueve solo el
 * contenedor. Si no hay ninguno que se desplace, se cae a `scrollIntoView` (jsdom, páginas sin contenedor).
 */
import { medir } from './uiScale'

export interface OpcionesDesplazar {
  /** Dónde queda el elemento dentro del contenedor. `nearest` solo mueve si no se ve entero. */
  block?: 'start' | 'center' | 'nearest'
  behavior?: ScrollBehavior
  /** Aire (px sin escalar) que se deja entre el borde del contenedor y el elemento con `start` y `nearest`. */
  margen?: number
}

/** El ancestro más cercano que de verdad se desplaza en vertical. */
export function contenedorDeScroll(el: Element): HTMLElement | null {
  for (let p = el.parentElement; p && p !== document.body && p !== document.documentElement; p = p.parentElement) {
    const o = getComputedStyle(p).overflowY
    if ((o === 'auto' || o === 'scroll' || o === 'overlay') && p.scrollHeight > p.clientHeight) return p
  }
  return null
}

/** El `scrollTop` al que hay que llevar el contenedor, o null si el elemento ya está bien donde está. */
export function destinoDeScroll(
  contenedor: { top: number; height: number; scrollTop: number },
  elemento: { top: number; height: number },
  { block = 'start', margen = 12 }: Pick<OpcionesDesplazar, 'block' | 'margen'> = {},
): number | null {
  const delta = elemento.top - contenedor.top
  const abajo = delta + elemento.height
  let destino: number
  if (block === 'center') {
    destino = contenedor.scrollTop + delta - (contenedor.height - elemento.height) / 2
  } else if (block === 'nearest') {
    if (delta < margen) destino = contenedor.scrollTop + delta - margen
    else if (abajo > contenedor.height - margen) destino = contenedor.scrollTop + abajo - contenedor.height + margen
    else return null
  } else {
    destino = contenedor.scrollTop + delta - margen
  }
  return Math.max(0, Math.round(destino))
}

export function desplazarAlContenedor(el: HTMLElement | null | undefined, opciones: OpcionesDesplazar = {}) {
  if (!el) return
  const contenedor = contenedorDeScroll(el)
  if (!contenedor) {
    el.scrollIntoView?.({ block: opciones.block ?? 'start', behavior: opciones.behavior })
    return
  }
  const c = medir(contenedor)
  const destino = destinoDeScroll(
    { top: c.top, height: c.height, scrollTop: contenedor.scrollTop },
    medir(el),
    opciones,
  )
  if (destino == null) return
  contenedor.scrollTo?.({ top: destino, behavior: opciones.behavior })
}
