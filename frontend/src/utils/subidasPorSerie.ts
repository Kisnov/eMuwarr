import type { UploadJob } from '../types/api'

/** Las subidas de una misma serie, plegables bajo una fila; cada elemento es el grupo de un elemento (sus trackers). */
export interface BloqueSerie { serieId: number; titulo: string; elementos: UploadJob[][] }
export type Bloque = UploadJob[] | BloqueSerie

export const esBloqueSerie = (b: Bloque): b is BloqueSerie => !Array.isArray(b)

/**
 * Junta bajo su serie los elementos (temporadas o capítulos) que comparten una, como en Biblioteca. La serie va donde
 * va su primer elemento en el orden que traen; una serie con un solo elemento no se agrupa.
 */
export function agruparPorSerie(grupos: UploadJob[][]): Bloque[] {
  const cuantos = new Map<number, number>()
  for (const g of grupos) {
    const id = g[0].serie_id
    if (id != null) cuantos.set(id, (cuantos.get(id) ?? 0) + 1)
  }
  const series = new Map<number, BloqueSerie>()
  const salida: Bloque[] = []
  for (const g of grupos) {
    const id = g[0].serie_id
    if (id == null || (cuantos.get(id) ?? 0) < 2) { salida.push(g); continue }
    let bloque = series.get(id)
    if (!bloque) {
      bloque = { serieId: id, titulo: g[0].serie_titulo || g[0].title || '—', elementos: [] }
      series.set(id, bloque)
      salida.push(bloque)
    }
    bloque.elementos.push(g)
  }
  return salida
}

/** Cuántos elementos de la serie hay en cada estado, por el de su primera subida. */
export function estadosDeSerie(bloque: BloqueSerie): [string, number][] {
  const cuenta = new Map<string, number>()
  for (const g of bloque.elementos) cuenta.set(g[0].status, (cuenta.get(g[0].status) ?? 0) + 1)
  return [...cuenta.entries()]
}
