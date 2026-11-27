import { describe, expect, it } from 'vitest'
import type { UploadJob } from '../../types/api'
import { agruparPorSerie, esBloqueSerie, estadosDeSerie, filasDeLista } from '../subidasPorSerie'

const subida = (id: number, extra: Partial<UploadJob> = {}) =>
  [{ id, status: 'completed', title: `T${id}`, ...extra } as UploadJob]

describe('agruparPorSerie', () => {
  it('junta bajo su serie los elementos que la comparten, donde va el primero', () => {
    const bloques = agruparPorSerie([
      subida(1, { serie_id: 7, serie_titulo: 'Serie' }), subida(2), subida(3, { serie_id: 7, serie_titulo: 'Serie' }),
    ])
    expect(bloques).toHaveLength(2)
    const [serie, suelta] = bloques
    expect(esBloqueSerie(serie) && [serie.serieId, serie.titulo, serie.elementos.map(g => g[0].id)])
      .toEqual([7, 'Serie', [1, 3]])
    expect(esBloqueSerie(suelta)).toBe(false)
  })

  it('una serie con un solo elemento, o sin serie, no se agrupa', () => {
    const bloques = agruparPorSerie([subida(1, { serie_id: 7 }), subida(2, { serie_id: 8 }), subida(3)])
    expect(bloques.every(b => !esBloqueSerie(b))).toBe(true)
  })

  it('cuenta las subidas de cada estado, también las de los otros trackers de un elemento', () => {
    const conOtroTracker = [...subida(2, { serie_id: 7 }), ...subida(4, { serie_id: 7, status: 'failed' })]
    const [serie] = agruparPorSerie([subida(1, { serie_id: 7 }), conOtroTracker, subida(3, { serie_id: 7, status: 'queued' })])
    expect(esBloqueSerie(serie) && estadosDeSerie(serie)).toEqual([['completed', 2], ['failed', 1], ['queued', 1]])
  })

  it('una serie abierta suelta sus elementos como filas propias; cerrada, solo la suya', () => {
    const bloques = agruparPorSerie([subida(1, { serie_id: 7 }), subida(2, { serie_id: 7 }), subida(3)])
    expect(filasDeLista(bloques, new Set())).toHaveLength(2)
    const abiertas = filasDeLista(bloques, new Set([7]))
    expect(abiertas.map(f => ('serie' in f ? 'serie' : `${f.grupo[0].id}${f.deSerie ? '*' : ''}`)))
      .toEqual(['serie', '1*', '2*', '3'])
  })
})
