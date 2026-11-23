import type { ReglasConNombre } from '../api/uploads'

// Lo que el asistente hace con /upload/elegibilidad (services/reglas_tracker.py::elegibilidad): qué trackers con clave
// admiten un pack o un capítulo por sus normas de idioma, y cómo decirlo en una línea.

// La categoría que le toca allí (`--variante`), como la llaman los trackers. Sin variante, la de siempre.
const CATEGORIA: Record<string, string> = { anime: 'Anime', latino: 'Latino', anime_catalan: 'Anime en Catalan' }

// Los nombres de los trackers que lo admiten, en el orden de la lista.
export function admiten(reglas: readonly ReglasConNombre[]): string[] {
  return reglas.filter(r => r.permitido).map(r => r.nombre)
}

// Los destinos marcados de entrada en el Resumen: eMuwarez si lo admite (lo de siempre); si no, los que lo admiten.
// Si no lo admite ninguno, eMuwarez: sus comprobaciones dirán por qué, como hasta ahora.
export function destinosDeEntrada(reglas: readonly ReglasConNombre[]): string[] {
  const si = reglas.filter(r => r.permitido).map(r => r.tracker)
  return si.length === 0 || si.includes('emu') ? ['emu'] : si
}

// «Milnueve ✓ (Anime en Catalan) · eMuwarez ✗ sin castellano · N.O.B.S ✗»: primero los que lo admiten. El «sin
// castellano» es la regla de eMuwarez y solo va con eMuwarez.
export function resumenPorTracker(reglas: readonly ReglasConNombre[]): string {
  const ordenadas = [...reglas.filter(r => r.permitido), ...reglas.filter(r => !r.permitido)]
  return ordenadas.map(r => {
    if (r.permitido) return `${r.nombre} ✓${r.variante && CATEGORIA[r.variante] ? ` (${CATEGORIA[r.variante]})` : ''}`
    return `${r.nombre} ✗${r.tracker === 'emu' ? ' sin castellano' : ''}`
  }).join(' · ')
}

// Lo mismo para una selección de capítulos: cuántos admite cada tracker. «Milnueve ✓ 12 de 12 · eMuwarez ✗».
export function resumenDeCapitulos(porCapitulo: readonly (readonly ReglasConNombre[])[]): string {
  const total = porCapitulo.length
  const trackers = porCapitulo[0] ?? []
  const cuenta = trackers.map(t => ({
    t, n: porCapitulo.filter(c => c.some(r => r.tracker === t.tracker && r.permitido)).length,
  }))
  const ordenadas = [...cuenta.filter(c => c.n > 0), ...cuenta.filter(c => c.n === 0)]
  return ordenadas.map(({ t, n }) => n > 0
    ? `${t.nombre} ✓ ${n} de ${total}`
    : `${t.nombre} ✗${t.tracker === 'emu' ? ' sin castellano' : ''}`).join(' · ')
}
