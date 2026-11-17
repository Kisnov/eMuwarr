// @vitest-environment jsdom
/**
 * Tus subidas en eMuwarez, en Estadísticas: sin leer todavía, con datos (totales, tabla, orden y enlaces) y si falla.
 */
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/estadisticas', () => ({ getMisSubidas: vi.fn() }))

import { getMisSubidas } from '../../api/estadisticas'
import type { MiTorrent, MisTorrents } from '../../types/api'
import { MisSubidasEmu } from '../MisSubidasEmu'
import { desmontar, esperar, montar, texto } from './dom'

const pedir = vi.mocked(getMisSubidas)

const torrent = (id: number, extra: Partial<MiTorrent> = {}): MiTorrent => ({
  torrent_id: id, nombre: `Torrent ${id}`, tamano: 1024 ** 3, subido_at: `2026-11-0${id}T10:00:00Z`, seeders: 5,
  leechers: 0, completados: 10, completados_semana: 2, freeleech: null, doble_subida: false, ...extra,
})

const respuesta = (torrents: MiTorrent[], extra: Partial<MisTorrents> = {}): MisTorrents => ({
  leido_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
  torrents,
  totales: { subidas: torrents.length, seeders: 7, leechers: 0, completados: 75, completados_semana: 4, sin_seeders: 1 },
  ...extra,
})

const nombres = () => [...document.querySelectorAll('tbody th[scope="row"]')].map(c => c.textContent)

beforeEach(() => pedir.mockReset())
afterEach(desmontar)

describe('MisSubidasEmu', () => {
  it('sin ninguna lectura todavía, dice cómo se leen', async () => {
    pedir.mockResolvedValue(respuesta([], { leido_at: null }))
    await montar(<MisSubidasEmu visible />)
    await esperar(() => texto().includes('Aún no se han leído'))
    expect(document.querySelectorAll('.stat-card')).toHaveLength(0)
  })

  it('no pide nada mientras la sección no se ve', async () => {
    await montar(<MisSubidasEmu visible={false} />)
    expect(pedir).not.toHaveBeenCalled()
  })

  it('enseña los totales y la tabla, con enlace a cada torrent y las que no tienen seeders marcadas', async () => {
    pedir.mockResolvedValue(respuesta([
      torrent(1, { completados: 60 }),
      torrent(2, { seeders: 0, completados: 5, completados_semana: null }),
      torrent(3, { completados: 10 }),
    ]))
    await montar(<MisSubidasEmu visible />)
    await esperar(() => document.querySelector('tbody'))

    const tarjetas = Object.fromEntries([...document.querySelectorAll('.stat-card')].map(t => [
      t.querySelector('.stat-card-label')!.textContent, t.querySelector('.stat-card-value')!.textContent]))
    expect(tarjetas).toEqual({ Subidas: '3', Completadas: '75', 'Completadas esta semana': '4', Seeders: '7', 'Sin seeders': '1' })
    expect(texto()).toContain('Leídas hace 2 h')
    expect(texto()).toContain('Las subidas anónimas no salen')

    expect(nombres()).toEqual(['Torrent 1', 'Torrent 3', 'Torrent 2'])   // las más completadas primero
    expect(document.querySelector('tbody a')!.getAttribute('href')).toBe('https://emu-warez.com/torrents/1')
    const sinSeeders = [...document.querySelectorAll('tbody tr')][2]
    expect(sinSeeders.querySelector('.est-sin-seeders')!.textContent).toBe('0')
    expect(sinSeeders.textContent).toContain('—')                       // sin foto de hace una semana
  })

  it('se puede ordenar por menos seeders o por las más recientes', async () => {
    pedir.mockResolvedValue(respuesta([torrent(1, { seeders: 9 }), torrent(2, { seeders: 0 }), torrent(3, { seeders: 4 })]))
    await montar(<MisSubidasEmu visible />)
    await esperar(() => document.querySelector('tbody'))
    const orden = document.querySelector<HTMLSelectElement>('select[aria-label="Orden de tus subidas"]')!
    const elegir = async (valor: string) => {
      await act(async () => { orden.value = valor; orden.dispatchEvent(new Event('change', { bubbles: true })) })
    }
    await elegir('seeders')
    expect(nombres()).toEqual(['Torrent 2', 'Torrent 3', 'Torrent 1'])
    await elegir('recientes')
    expect(nombres()).toEqual(['Torrent 3', 'Torrent 2', 'Torrent 1'])
  })

  it('si falla, lo dice sin romper la página', async () => {
    pedir.mockRejectedValueOnce(new Error('503'))
    await montar(<MisSubidasEmu visible />)
    await esperar(() => texto().includes('No se pudieron cargar tus subidas'))
  })
})
