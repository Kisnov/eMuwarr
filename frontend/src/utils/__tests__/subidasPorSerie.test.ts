import { describe, expect, it } from 'vitest'
import type { UploadJob } from '../../types/api'
import { agruparPorSerie, esBloqueSerie, estadosDeSerie } from '../subidasPorSerie'

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

  it('cuenta los elementos por el estado de su primera subida', () => {
    const [serie] = agruparPorSerie([
      subida(1, { serie_id: 7 }), subida(2, { serie_id: 7, status: 'queued' }), subida(3, { serie_id: 7 }),
    ])
    expect(esBloqueSerie(serie) && estadosDeSerie(serie)).toEqual([['completed', 2], ['queued', 1]])
  })
})
