// La subida que se está preparando en el asistente (pantalla /subir/<id>). Vive fuera de las secciones para que
// cualquiera pueda abrirla, para que el menú sepa que hay una a medias y para que sobreviva a cambiar de sección: la
// pantalla se queda montada, como las demás, y aquí está lo que hace falta para volver a ella.
import { useEffect, useRef, useSyncExternalStore } from 'react'
import { getLibraryItem } from '../api/library'
import type { LibraryItemResponse } from '../types/api'
import type { PreseleccionDePeticion } from '../components/asistente/useAsistente'

export interface AperturaAsistente {
  item: LibraryItemResponse
  preselect?: PreseleccionDePeticion
  requestId?: number
  // La sección desde la que se abrió: a ella se vuelve al cerrar.
  origen: string
}
// `n` cambia con cada apertura: es la clave que monta un asistente nuevo.
export type SesionAsistente = AperturaAsistente & { n: number }
// Lo que se apunta en la pestaña para rehacerla al recargar (el elemento se vuelve a pedir; lo editado no se guarda).
export type SesionGuardada = Omit<AperturaAsistente, 'item'> & { id: number }

const CLAVE = 'emuwarr_asistente'
export const SECCION_ASISTENTE = 'subir'

interface Estado { sesion: SesionAsistente | null; pendiente: AperturaAsistente | null }
let estado: Estado = { sesion: null, pendiente: null }
let n = 0
// Lo que el asistente montado dice de sí mismo: si tiene cambios hechos a mano, si está encolando y si ya terminó.
export interface EstadoDelAsistente { cambios: boolean; ocupado: boolean; terminado: boolean }
const SIN_ESTADO = (): EstadoDelAsistente => ({ cambios: false, ocupado: false, terminado: false })
let suEstado: () => EstadoDelAsistente = SIN_ESTADO
let recuperacion: Promise<boolean> | null = null
const oyentes = new Set<() => void>()
const alEncolar = new Set<() => void>()

function poner(nuevo: Partial<Estado>) {
  estado = { ...estado, ...nuevo }
  oyentes.forEach(f => f())
}

function irA(ruta: string, reemplazar = false) {
  if (window.location.pathname !== ruta) {
    if (reemplazar) window.history.replaceState({}, '', ruta)
    else window.history.pushState({}, '', ruta)
  }
  // AppShell sigue la URL: así cambia de sección sin que esto tenga que conocerlo.
  window.dispatchEvent(new PopStateEvent('popstate'))
}

function empezar(a: AperturaAsistente) {
  suEstado = SIN_ESTADO
  poner({ sesion: { ...a, n: ++n }, pendiente: null })
  const guardada: SesionGuardada = { id: a.item.id, preselect: a.preselect, requestId: a.requestId, origen: a.origen }
  try { sessionStorage.setItem(CLAVE, JSON.stringify(guardada)) } catch { /* sin almacenamiento no se rehace al recargar */ }
  irAlAsistente()
}

const mismaApertura = (s: AperturaAsistente, a: AperturaAsistente) =>
  s.item.id === a.item.id && s.requestId === a.requestId
  && JSON.stringify(s.preselect ?? null) === JSON.stringify(a.preselect ?? null)

/** Abre el asistente con ese elemento. Si ya hay otra subida en preparación, pregunta antes de dejarla. */
export function abrirAsistente(a: AperturaAsistente): void {
  const { sesion } = estado
  if (!sesion) { empezar(a); return }
  const suyo = suEstado()
  // Mientras encola no se puede dejar: el bucle seguiría encolando lo que se dijo que se dejaba.
  if (suyo.ocupado) { irAlAsistente(); return }
  // Ya encolada y con el resultado a la vista: no queda nada que perder.
  if (suyo.terminado) { empezar(a); return }
  // El mismo contenido con la misma petición es volver a ella; con otra temporada u otra petición, es otra subida.
  if (mismaApertura(sesion, a)) { irAlAsistente(); return }
  poner({ pendiente: a })
}

export function confirmarPendiente(): void { if (estado.pendiente) empezar(estado.pendiente) }
export function descartarPendiente(): void { poner({ pendiente: null }) }

/** Vuelve a la subida en preparación. */
export function irAlAsistente(): void {
  if (estado.sesion) irA(`/${SECCION_ASISTENTE}/${estado.sesion.item.id}`)
}

/** ¿Sigue siendo esa la subida en preparación? Una sustituida que aún responde ya no manda. */
export function esLaSesion(n: number): boolean { return estado.sesion?.n === n }

/** Se acabó (encolada, o cerrada a mano): ya no hay nada a lo que volver. */
export function cerrarAsistente(): void {
  suEstado = SIN_ESTADO
  try { sessionStorage.removeItem(CLAVE) } catch { /* nada que quitar */ }
  poner({ sesion: null })
}

/** Sale de la pantalla del asistente sin dejarla en el historial: sin subida no tiene nada que enseñar. */
export function salirDelAsistente(seccion: string): void { irA(`/${seccion}`, true) }

export function sesionGuardada(): SesionGuardada | null {
  try {
    const dato = JSON.parse(sessionStorage.getItem(CLAVE) ?? 'null')
    return dato && typeof dato.id === 'number' && typeof dato.origen === 'string' ? dato : null
  } catch { return null }
}

/** Tras recargar la pestaña: la misma subida, con el elemento recién pedido (lo editado no se guarda). Se llama al
 *  arrancar la aplicación, para que el menú vuelva a ofrecerla estés en la sección que estés. Dice si hay subida. */
export function recuperarAsistente(): Promise<boolean> {
  if (estado.sesion) return Promise.resolve(true)
  const g = sesionGuardada()
  if (!g) return Promise.resolve(false)
  recuperacion ??= getLibraryItem(g.id).then(item => {
    // Si entretanto se abrió otra, manda esa.
    if (!estado.sesion) poner({ sesion: { item, preselect: g.preselect, requestId: g.requestId, origen: g.origen, n: ++n } })
    return true
  }, () => {
    // El contenido ya no está en la biblioteca: no hay nada que rehacer.
    try { sessionStorage.removeItem(CLAVE) } catch { /* nada que quitar */ }
    return false
  }).finally(() => { recuperacion = null })
  return recuperacion
}

/** El asistente montado se describe: con cambios a mano, cerrar la pestaña avisa; ocupado o terminado decide qué
 *  pasa al pulsar «Subir» en otro contenido. */
export function registrarEstado(fn: () => EstadoDelAsistente): () => void {
  suEstado = fn
  return () => { if (suEstado === fn) suEstado = SIN_ESTADO }
}
export function asistenteConCambios(): boolean { return !!estado.sesion && suEstado().cambios }

/** Algo se ha encolado: las secciones que lo enseñan recargan. */
export function avisarSubidaEncolada(): void { alEncolar.forEach(f => f()) }

export function useAlEncolarSubida(hacer: () => void): void {
  const actual = useRef(hacer)
  actual.current = hacer
  useEffect(() => {
    const f = () => actual.current()
    alEncolar.add(f)
    return () => { alEncolar.delete(f) }
  }, [])
}

const suscribir = (f: () => void) => { oyentes.add(f); return () => { oyentes.delete(f) } }
export function useSesionAsistente(): Estado {
  return useSyncExternalStore(suscribir, () => estado)
}

/** Solo para los tests: como recién cargada la aplicación. */
export function reiniciarAsistente(): void {
  estado = { sesion: null, pendiente: null }
  suEstado = SIN_ESTADO
  recuperacion = null
  oyentes.forEach(f => f())
}
