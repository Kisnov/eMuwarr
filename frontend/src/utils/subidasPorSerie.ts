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

/** Cuántas subidas de la serie hay en cada estado, contando la de cada tracker. */
export function estadosDeSerie(bloque: BloqueSerie): [string, number][] {
  const cuenta = new Map<string, number>()
  for (const j of bloque.elementos.flat()) cuenta.set(j.status, (cuenta.get(j.status) ?? 0) + 1)
  return [...cuenta.entries()]
}

/** Lo que pinta la lista, fila a fila: una serie abierta suelta sus elementos, para que se pinten solo los que se ven. */
export type FilaDeLista = { serie: BloqueSerie; abierta: boolean } | { grupo: UploadJob[]; deSerie: boolean }

export function filasDeLista(bloques: Bloque[], abiertas: ReadonlySet<number>): FilaDeLista[] {
  return bloques.flatMap((b): FilaDeLista[] => {
    if (!esBloqueSerie(b)) return [{ grupo: b, deSerie: false }]
    const abierta = abiertas.has(b.serieId)
    return [{ serie: b, abierta }, ...(abierta ? b.elementos.map(grupo => ({ grupo, deSerie: true })) : [])]
  })
}
