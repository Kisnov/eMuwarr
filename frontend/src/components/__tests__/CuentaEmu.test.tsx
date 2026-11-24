// @vitest-environment jsdom
/**
 * La franja «Tu cuenta en eMuwarez» del Panel lleva solo «Mis subidas»: Mensajes y Notificaciones ya están en la barra
 * superior (y en el cajón del móvil), y repetirlos la hacía caer a una segunda fila.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { EmuPerfil } from '../../types/api'

const PERFIL: EmuPerfil = {
  configured: true, username: 'Kisnov', group: 'Uploader', uploaded: '1.23 TiB', downloaded: '412.8 GiB',
  ratio: '3.05', buffer: '2.6 TiB', seeding: 148, leeching: 0, seedbonus: 125430, hit_and_runs: 0,
  updated_at: null, error: null,
}
const contexto = vi.hoisted(() => ({ perfil: null as EmuPerfil | null }))

vi.mock('../../context/PerfilEmuContext', () => ({
  tapar: (n: string) => '*'.repeat(Math.max(4, n.length)),
  usePerfilEmu: () => ({
    perfil: contexto.perfil,
    actualizar: async () => {},
    ocultarNombre: false,
    setOcultarNombre: () => {},
  }),
}))

import { CuentaEmu } from '../CuentaEmu'
import { desmontar, montar } from './dom'

describe('CuentaEmu', () => {
  afterEach(desmontar)
  beforeEach(() => { contexto.perfil = PERFIL })

  it('enseña las cifras y un único enlace, «Mis subidas»', async () => {
    await montar(<CuentaEmu />)
    const enlaces = [...document.querySelectorAll('.cuenta-emu-enlaces a')]
    expect(enlaces).toHaveLength(1)
    expect(enlaces[0].textContent).toContain('Mis subidas')
    expect(enlaces[0].getAttribute('href')).toContain('/users/Kisnov/uploads')
    expect(document.body.textContent).not.toContain('Mensajes')
    expect(document.body.textContent).not.toContain('Notificaciones')
    expect(document.querySelector('.cuenta-emu-cifras')!.textContent).toContain('Ratio')
  })

  it('solo con eMuwarez es una sola franja; con otro tracker, una más con su nombre y sus enlaces', async () => {
    await montar(<CuentaEmu />)
    expect(document.querySelector('.cuenta-emu-lista')).toBeNull()
    await desmontar()
    contexto.perfil = { ...PERFIL, otros: [{ ...PERFIL, tracker: 'nobs', nombre: 'N.O.B.S', url: 'https://nobs.rawsmoke.net', username: 'Gabi' }] }
    await montar(<CuentaEmu />)
    const franjas = [...document.querySelectorAll('.cuenta-emu-lista > .cuenta-emu')]
    expect(franjas.map(f => f.getAttribute('aria-label'))).toEqual(['Tu cuenta en eMuwarez', 'Tu cuenta en N.O.B.S'])
    expect(franjas[1].querySelector('.sub-tracker-chip')!.textContent).toBe('N.O.B.S')
    expect(franjas[1].querySelector('.cuenta-emu-enlaces a')!.getAttribute('href')).toBe('https://nobs.rawsmoke.net/users/Gabi/uploads')
  })

  it('sin nada que enseñar de eMuwarez, la de otro tracker sale igual', async () => {
    contexto.perfil = { ...PERFIL, username: null, error: null,
      otros: [{ ...PERFIL, tracker: 'nobs', nombre: 'N.O.B.S', url: 'https://nobs.rawsmoke.net', username: 'Gabi' }] }
    await montar(<CuentaEmu />)
    expect([...document.querySelectorAll('.cuenta-emu')].map(f => f.getAttribute('aria-label'))).toEqual(['Tu cuenta en N.O.B.S'])
  })
})
