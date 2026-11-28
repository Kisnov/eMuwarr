// @vitest-environment jsdom
/**
 * El asistente de subida como pantalla (/subir/<id>): cómo se abre desde cualquier sección, que solo hay una subida
 * en preparación a la vez, que sigue ahí al cambiar de sección y al recargar, y cómo se vuelve a ella y se cierra.
 */
import { useEffect, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/library', () => ({ getLibraryItem: vi.fn() }))
vi.mock('../../api/system', () => ({ getSystemStats: vi.fn(() => new Promise(() => { /* sin datos */ })) }))
// Sin AppShell alrededor: «entrar» en la sección es montarla.
vi.mock('../../context/SeccionVisible', () => ({
  useSeccionVisible: () => true,
  useAlEntrar: (hacer: () => void) => { useEffect(() => { hacer() }, []) }, // eslint-disable-line react-hooks/exhaustive-deps -- una vez, al montar
}))
// El asistente de verdad tiene su propio test: aquí basta con ver con qué se monta y que guarda su estado.
vi.mock('../../components/asistente/PantallaAsistente', () => ({
  PantallaAsistente: (p: {
    item: { id: number; title: string }; preselect?: unknown; requestId?: number
    onClose: () => void; onDone?: () => void; onNavigate?: (s: string) => void
  }) => {
    const [toques, setToques] = useState(0)
    return (
      <div data-testid="asistente">
        <span data-testid="props">{JSON.stringify({ id: p.item.id, preselect: p.preselect ?? null, requestId: p.requestId ?? null })}</span>
        <button type="button" onClick={() => setToques(n => n + 1)}>Tocar {toques}</button>
        <button type="button" onClick={p.onClose}>Cerrar</button>
        <button type="button" onClick={() => { p.onDone?.(); p.onNavigate?.('uploads'); p.onClose() }}>Encolar</button>
      </div>
    )
  },
}))

import { getLibraryItem } from '../../api/library'
import { ConfirmarOtraSubida } from '../../components/ConfirmarOtraSubida'
import { Sidebar } from '../../components/Sidebar'
import { boton, desmontar, esperar, montar, pulsar, texto } from '../../components/__tests__/dom'
import type { LibraryItemResponse } from '../../types/api'
import {
  abrirAsistente, asistenteConCambios, recuperarAsistente, registrarEstado, reiniciarAsistente, sesionGuardada,
  useAlEncolarSubida,
} from '../../utils/asistenteSesion'
import { SubirPage } from '../SubirPage'

const DUNE = { id: 5, item_type: 'movie', title: 'Dune' } as LibraryItemResponse
const CONAN = { id: 9, item_type: 'series', title: 'Detective Conan' } as LibraryItemResponse
const SIN_NADA = { cambios: false, ocupado: false, terminado: false }

// Lo que hace AppShell.navigate: cambia la URL.
const onNavigate = vi.fn((seccion: string) => { window.history.pushState({}, '', `/${seccion}`) })
const ruta = () => window.location.pathname
const props = () => JSON.parse(document.querySelector('[data-testid="props"]')!.textContent!)

function Arnes({ alEncolar }: { alEncolar?: () => void }) {
  useAlEncolarSubida(() => alEncolar?.())
  return (
    <>
      <Sidebar active="library" onNavigate={onNavigate} />
      <ConfirmarOtraSubida />
      <SubirPage onNavigate={onNavigate} />
    </>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  reiniciarAsistente()
  window.history.replaceState({}, '', '/library')
  window.matchMedia = vi.fn(() => ({
    matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia
})
afterEach(desmontar)

describe('abrir', () => {
  it('lleva a /subir/<id> y monta el asistente con el elemento, su preselección y su petición', async () => {
    await montar(<Arnes />)
    expect(document.querySelector('[data-testid="asistente"]')).toBeNull()
    await pulsarAbrir({ item: DUNE, origen: 'requests', preselect: { season: 2, episode: null }, requestId: 77 })
    expect(ruta()).toBe('/subir/5')
    expect(props()).toEqual({ id: 5, preselect: { season: 2, episode: null }, requestId: 77 })
  })

  it('mientras hay una en preparación el menú ofrece volver a ella, y deja de ofrecerlo al cerrarla', async () => {
    await montar(<Arnes />)
    expect(boton('Subida en preparación')).toBeUndefined()
    await pulsarAbrir({ item: DUNE, origen: 'library' })
    window.history.pushState({}, '', '/uploads')
    await pulsar(boton('Subida en preparación'))
    expect(ruta()).toBe('/subir/5')
    await pulsar(boton('Cerrar'))
    expect(boton('Subida en preparación')).toBeUndefined()
  })

  it('volver a pulsar «Subir» en el mismo contenido vuelve a la subida a medias, sin empezar de cero', async () => {
    await montar(<Arnes />)
    await pulsarAbrir({ item: DUNE, origen: 'library' })
    await pulsar(boton('Tocar 0'))
    window.history.pushState({}, '', '/library')
    await pulsarAbrir({ item: DUNE, origen: 'library' })
    expect(ruta()).toBe('/subir/5')
    expect(boton('Tocar 1')).toBeDefined()
  })
})

describe('solo una a la vez', () => {
  async function conDuneAMedias() {
    await montar(<Arnes />)
    await pulsarAbrir({ item: DUNE, origen: 'library' })
    await pulsar(boton('Tocar 0'))
    window.history.pushState({}, '', '/library')
    await pulsarAbrir({ item: CONAN, origen: 'library' })
  }

  it('abrir otro contenido pregunta antes de dejar la que está a medias', async () => {
    await conDuneAMedias()
    expect(texto()).toContain('Ya hay una subida en preparación')
    expect(texto()).toContain('Estás preparando «Dune»')
    expect(props().id).toBe(5)
    expect(ruta()).toBe('/library')
  })

  it('«Volver a la que estaba» la conserva tal como estaba', async () => {
    await conDuneAMedias()
    await pulsar(boton('Volver a la que estaba'))
    expect(ruta()).toBe('/subir/5')
    expect(boton('Tocar 1')).toBeDefined()
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })

  it('«Dejarla y empezar esta» monta un asistente nuevo con el otro contenido', async () => {
    await conDuneAMedias()
    await pulsar(boton('Dejarla y empezar esta'))
    expect(ruta()).toBe('/subir/9')
    expect(props().id).toBe(9)
    expect(boton('Tocar 0')).toBeDefined()
  })
})

describe('cerrar y encolar', () => {
  it('cerrar a mano vuelve a la sección desde la que se abrió', async () => {
    await montar(<Arnes />)
    await pulsarAbrir({ item: DUNE, origen: 'requests' })
    await pulsar(boton('Cerrar'))
    expect(onNavigate).toHaveBeenCalledWith('requests')
    expect(document.querySelector('[data-testid="asistente"]')).toBeNull()
    expect(sesionGuardada()).toBeNull()
  })

  it('al encolar avisa a las secciones para que recarguen y se queda en Subidas', async () => {
    const alEncolar = vi.fn()
    await montar(<Arnes alEncolar={alEncolar} />)
    await pulsarAbrir({ item: DUNE, origen: 'requests' })
    await pulsar(boton('Encolar'))
    expect(alEncolar).toHaveBeenCalledTimes(1)
    expect(onNavigate).toHaveBeenCalledTimes(1)
    expect(ruta()).toBe('/uploads')
  })
})

describe('recargar la pestaña', () => {
  it('rehace la subida: vuelve a pedir el elemento y conserva la preselección y la petición', async () => {
    await montar(<Arnes />)
    await pulsarAbrir({ item: DUNE, origen: 'requests', preselect: { season: 2 }, requestId: 77 })
    await desmontar()
    reiniciarAsistente()   // la memoria se pierde al recargar; lo apuntado en la pestaña, no
    vi.mocked(getLibraryItem).mockResolvedValue(DUNE)
    await montar(<Arnes />)
    await esperar(() => document.querySelector('[data-testid="asistente"]'))
    expect(getLibraryItem).toHaveBeenCalledWith(5)
    expect(props()).toEqual({ id: 5, preselect: { season: 2 }, requestId: 77 })
  })

  it('sin nada apuntado, /subir no tiene nada que enseñar y devuelve a Biblioteca sin dejar rastro en el historial', async () => {
    window.history.replaceState({}, '', '/subir/5')
    const entradas = window.history.length
    await montar(<Arnes />)
    await esperar(() => ruta() === '/library')
    expect(window.history.length).toBe(entradas)
    expect(getLibraryItem).not.toHaveBeenCalled()
  })

  it('si el elemento ya no existe, vuelve a donde se abrió y olvida la subida', async () => {
    await montar(<Arnes />)
    await pulsarAbrir({ item: DUNE, origen: 'requests' })
    await desmontar()
    reiniciarAsistente()
    vi.mocked(getLibraryItem).mockRejectedValue(new Error('404'))
    await montar(<Arnes />)
    await esperar(() => ruta() === '/requests')
    expect(sesionGuardada()).toBeNull()
  })
})

describe('cambios sin guardar', () => {
  it('solo cuentan mientras hay una subida en preparación y el asistente dice que los tiene', async () => {
    expect(asistenteConCambios()).toBe(false)
    await montar(<Arnes />)
    await pulsarAbrir({ item: DUNE, origen: 'library' })
    const quitar = registrarEstado(() => ({ ...SIN_NADA, cambios: true }))
    expect(asistenteConCambios()).toBe(true)
    quitar()
    expect(asistenteConCambios()).toBe(false)
    registrarEstado(() => ({ ...SIN_NADA, cambios: true }))
    await pulsar(boton('Cerrar'))
    expect(asistenteConCambios()).toBe(false)
  })
})

describe('pulsar «Subir» con una subida ya en el asistente', () => {
  it('el mismo contenido desde otra petición es otra subida: pregunta', async () => {
    await montar(<Arnes />)
    await pulsarAbrir({ item: CONAN, origen: 'library' })
    await pulsarAbrir({ item: CONAN, origen: 'requests', preselect: { season: 2 }, requestId: 77 })
    expect(texto()).toContain('Ya hay una subida en preparación')
    await pulsar(boton('Dejarla y empezar esta'))
    expect(props()).toEqual({ id: 9, preselect: { season: 2 }, requestId: 77 })
  })

  it('mientras encola no se puede dejar: vuelve a ella sin preguntar', async () => {
    await montar(<Arnes />)
    await pulsarAbrir({ item: DUNE, origen: 'library' })
    registrarEstado(() => ({ ...SIN_NADA, ocupado: true }))
    window.history.pushState({}, '', '/library')
    await pulsarAbrir({ item: CONAN, origen: 'library' })
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    expect(ruta()).toBe('/subir/5')
    expect(props().id).toBe(5)
  })

  it('ya encolada y con el resultado a la vista, la nueva la sustituye sin preguntar', async () => {
    await montar(<Arnes />)
    await pulsarAbrir({ item: DUNE, origen: 'library' })
    registrarEstado(() => ({ ...SIN_NADA, terminado: true }))
    await pulsarAbrir({ item: CONAN, origen: 'library' })
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    expect(props().id).toBe(9)
  })

  it('un asistente dejado por otra subida ya no cierra ni lleva a ningún sitio aunque responda tarde', async () => {
    await montar(<Arnes />)
    await pulsarAbrir({ item: DUNE, origen: 'library' })
    // Lo que el asistente de Dune recibió para cerrar y navegar, como lo usaría una petición suya que llega tarde.
    const encolarDeDune = boton('Encolar')!
    const alPulsar = Object.entries(encolarDeDune).find(([k]) => k.startsWith('__reactProps'))![1] as { onClick: () => void }
    await pulsarAbrir({ item: CONAN, origen: 'library' })
    await pulsar(boton('Dejarla y empezar esta'))
    onNavigate.mockClear()
    const { act } = await import('react')
    await act(async () => { alPulsar.onClick() })
    expect(onNavigate).not.toHaveBeenCalled()
    expect(props().id).toBe(9)
    expect(sesionGuardada()?.id).toBe(9)
  })
})

describe('al arrancar la aplicación', () => {
  it('recargar en otra sección también rehace la subida: el menú vuelve a ofrecerla', async () => {
    await montar(<Arnes />)
    await pulsarAbrir({ item: DUNE, origen: 'library' })
    await desmontar()
    reiniciarAsistente()
    window.history.replaceState({}, '', '/uploads')
    vi.mocked(getLibraryItem).mockResolvedValue(DUNE)
    await montar(<Sidebar active="uploads" onNavigate={onNavigate} />)
    expect(boton('Subida en preparación')).toBeUndefined()
    const { act } = await import('react')
    await act(async () => { await recuperarAsistente() })   // lo que hace AppShell al montarse
    expect(boton('Subida en preparación')).toBeDefined()
    expect(ruta()).toBe('/uploads')
  })
})

// «Subir» se pulsa en otra sección: aquí, la llamada directa que hacen Biblioteca, el Panel y Peticiones.
async function pulsarAbrir(a: Parameters<typeof abrirAsistente>[0]) {
  const { act } = await import('react')
  await act(async () => { abrirAsistente(a) })
}
