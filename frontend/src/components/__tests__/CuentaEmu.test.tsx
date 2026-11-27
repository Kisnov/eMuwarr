// @vitest-environment jsdom
/**
 * Tus cuentas del Panel llevan solo «Mis subidas»: Mensajes y Notificaciones ya están en la barra superior (y en el cajón
 * del móvil). Cada tracker sale con su logo; las dos disposiciones (tarjetas y tabla) enseñan lo mismo.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { EmuPerfil } from '../../types/api'

const PERFIL: EmuPerfil = {
  configured: true, username: 'Kisnov', group: 'Uploader', uploaded: '1.23 TiB', downloaded: '412.8 GiB',
  ratio: '3.05', buffer: '2.6 TiB', seeding: 148, leeching: 0, seedbonus: 125430, hit_and_runs: 2,
  updated_at: null, error: null,
}
const NOBS: EmuPerfil = { ...PERFIL, tracker: 'nobs', nombre: 'N.O.B.S', url: 'https://nobs.rawsmoke.net', username: 'Gabi', hit_and_runs: 0 }
const contexto = vi.hoisted(() => ({ perfil: null as EmuPerfil | null, ocultarNombre: false }))

vi.mock('../../context/PerfilEmuContext', () => ({
  tapar: (n: string) => '*'.repeat(Math.max(4, n.length)),
  usePerfilEmu: () => ({
    perfil: contexto.perfil,
    actualizar: async () => {},
    ocultarNombre: contexto.ocultarNombre,
    setOcultarNombre: () => {},
  }),
}))

import { CuentaEmu, Cuentas } from '../CuentaEmu'
import { desmontar, montar, pulsar } from './dom'

const cuentas = () => [...document.querySelectorAll('.cuenta-emu')]

describe('CuentaEmu', () => {
  afterEach(desmontar)
  beforeEach(() => { contexto.perfil = PERFIL; contexto.ocultarNombre = false })

  it('enseña las cifras y un único enlace, «Mis subidas»', async () => {
    await montar(<CuentaEmu />)
    const enlaces = [...document.querySelectorAll('.cuenta-emu-enlace')]
    expect(enlaces).toHaveLength(1)
    expect(enlaces[0].textContent).toContain('Mis subidas')
    expect(enlaces[0].getAttribute('href')).toContain('/users/Kisnov/uploads')
    expect(document.body.textContent).not.toContain('Mensajes')
    expect(document.body.textContent).not.toContain('Notificaciones')
    expect(document.body.textContent).toContain('Ratio')
  })

  it('sin nada que enseñar de eMuwarez, la de otro tracker sale igual', async () => {
    contexto.perfil = { ...PERFIL, username: null, error: null, otros: [NOBS] }
    await montar(<CuentaEmu />)
    expect(cuentas().map(f => f.getAttribute('aria-label'))).toEqual(['Tu cuenta en N.O.B.S'])
  })
})

describe('Cuentas', () => {
  afterEach(desmontar)
  beforeEach(() => { contexto.ocultarNombre = false })

  it('con varios trackers, una tarjeta por tracker con su logo, sus cifras y su «Mis subidas»', async () => {
    await montar(<Cuentas cuentas={[PERFIL, NOBS]} onAnadirTracker={() => {}} />)
    expect(document.querySelector('.cuentas-tarjetas')).not.toBeNull()
    expect(cuentas().map(f => f.getAttribute('aria-label'))).toEqual(['Tu cuenta en eMuwarez', 'Tu cuenta en N.O.B.S'])
    expect(cuentas().map(f => f.querySelector('img')!.getAttribute('alt'))).toEqual(['eMuwarez', 'N.O.B.S'])
    expect(cuentas()[1].querySelector('.cuenta-emu-enlace')!.getAttribute('href')).toBe('https://nobs.rawsmoke.net/users/Gabi/uploads')
    expect(cuentas()[0].textContent).toContain('1,23 TiB')
    expect(cuentas()[0].querySelector('.es-aviso')!.textContent).toContain('2')
    expect(cuentas()[1].querySelector('.es-aviso')).toBeNull()
    // «Añadir tracker» solo acompaña a un tracker solo.
    expect(document.querySelector('.cuenta-anadir')).toBeNull()
  })

  it('pinta las cuentas que recibe, sean cuantas sean', async () => {
    const seis = Array.from({ length: 6 }, (_, i) => ({ ...NOBS, tracker: `t${i}`, nombre: `T${i}` }))
    await montar(<Cuentas cuentas={seis} />)
    expect(cuentas()).toHaveLength(6)
  })

  it('con un solo tracker y la variante de tabla, una fila', async () => {
    await montar(<Cuentas cuentas={[PERFIL]} unaCuenta="tabla" onAnadirTracker={() => {}} />)
    expect(document.querySelectorAll('.cuentas-tabla tbody tr')).toHaveLength(1)
    expect(document.querySelector('.cuenta-anadir')).toBeNull()
  })

  it('con un solo tracker y la variante de tarjeta, «Añadir tracker» lleva a Configuración', async () => {
    const onAnadirTracker = vi.fn()
    await montar(<Cuentas cuentas={[PERFIL]} unaCuenta="tarjeta" onAnadirTracker={onAnadirTracker} />)
    const anadir = document.querySelector<HTMLAnchorElement>('.cuenta-anadir')!
    expect(anadir.getAttribute('href')).toBe('/settings')
    expect(anadir.textContent).toContain('Añadir tracker')
    await pulsar(anadir)
    expect(onAnadirTracker).toHaveBeenCalledTimes(1)
  })

  it.each(['tabla', 'tarjeta'] as const)('con el usuario oculto lo tapa (%s)', async unaCuenta => {
    contexto.ocultarNombre = true
    await montar(<Cuentas cuentas={[PERFIL]} unaCuenta={unaCuenta} />)
    expect(document.body.textContent).not.toContain('Kisnov')
    expect(document.querySelector('.cuenta-emu-nombre')!.textContent).toBe('******')
  })

  it.each(['tabla', 'tarjeta'] as const)('sin leer aún, enseña el error con el logo del tracker (%s)', async unaCuenta => {
    await montar(<Cuentas cuentas={[{ ...NOBS, username: null, ratio: null, error: 'La clave no vale' }]} unaCuenta={unaCuenta} />)
    expect(document.querySelector('.cuenta-emu-error')!.textContent).toContain('La clave no vale')
    expect(document.querySelector('.cuenta-emu img')!.getAttribute('alt')).toBe('N.O.B.S')
    expect(document.querySelector('.cuenta-emu-enlace')).toBeNull()
  })
})
