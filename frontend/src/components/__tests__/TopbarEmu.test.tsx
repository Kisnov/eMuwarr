// @vitest-environment jsdom
/** Mensajes y notificaciones de eMuwarez llevan delante el logo de eMuwarez, no el de la app. */
import { describe, expect, it, vi } from 'vitest'

vi.mock('../NotificationBell', () => ({ NotificationBell: () => null }))
vi.mock('../SyncProgressBanner', () => ({ SyncProgressBanner: () => null }))
vi.mock('../../context/PerfilEmuContext', () => ({
  tapar: (n: string) => '*'.repeat(Math.max(4, n.length)),
  usePerfilEmu: () => ({ perfil: { username: 'Gabriel' }, actualizar: async () => {}, ocultarNombre: false, setOcultarNombre: () => {} }),
}))

import { TourProvider } from '../../context/TourContext'
import { Topbar } from '../Topbar'
import { montar } from './dom'

describe('Topbar', () => {
  it('el grupo de eMuwarez lleva su logo', async () => {
    const menu = { abierto: false, alternar: vi.fn(), botonRef: { current: null }, movil: false }
    await montar(<TourProvider><Topbar user={null} pageTitle="Panel" onLogout={vi.fn()} onBuscar={vi.fn()} menu={menu} /></TourProvider>)
    const logo = document.querySelector('.topbar-emu img')!
    expect(logo.getAttribute('alt')).toBe('eMuwarez')
    expect(logo.getAttribute('src')).toMatch(/emu.*\.png/)
  })
})
