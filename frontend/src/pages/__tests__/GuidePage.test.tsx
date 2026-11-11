// @vitest-environment jsdom
// Guía: la sección abierta va en la URL, para poder enlazar a una sección concreta.
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GuidePage } from '../GuidePage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let raiz: Root
let contenedor: HTMLDivElement

async function montar() {
  contenedor = document.createElement('div')
  document.body.appendChild(contenedor)
  raiz = createRoot(contenedor)
  await act(async () => { raiz.render(<GuidePage />) })
}

const activa = () => contenedor.querySelector('[role="tab"][aria-selected="true"]')?.id

afterEach(async () => {
  await act(async () => { raiz.unmount() })
  contenedor.remove()
  window.history.replaceState(null, '', '/')
})

describe('Guía', () => {
  it('abre la sección que pide ?seccion= y, si no existe, la de partida', async () => {
    window.history.replaceState(null, '', '/guide?seccion=rules')
    await montar()
    expect(activa()).toBe('guia-pestana-rules')
    await act(async () => { raiz.unmount() })
    contenedor.remove()

    window.history.replaceState(null, '', '/guide?seccion=no-existe')
    await montar()
    expect(activa()).toBe('guia-pestana-formula')
  })

  it('elegir una sección la apunta en la URL sin añadir una entrada al historial', async () => {
    window.history.replaceState(null, '', '/guide')
    const antes = window.history.length
    await montar()
    await act(async () => { contenedor.querySelector<HTMLButtonElement>('#guia-pestana-upload')!.click() })
    expect(activa()).toBe('guia-pestana-upload')
    expect(window.location.search).toBe('?seccion=upload')
    expect(window.history.length).toBe(antes)
  })

  describe('la fila de secciones desplazable (móvil)', () => {
    // jsdom no da medidas: la fila mide 300 de ancho con 900 de contenido, y cada pestaña ocupa 100 desde 100·índice.
    const scrollTo = vi.fn()
    const originales: Array<[object, string, PropertyDescriptor | undefined]> = []
    const parchear = (objetivo: object, nombre: string, desc: PropertyDescriptor) => {
      originales.push([objetivo, nombre, Object.getOwnPropertyDescriptor(objetivo, nombre)])
      Object.defineProperty(objetivo, nombre, { configurable: true, ...desc })
    }
    afterEach(() => {
      for (const [o, n, d] of originales.splice(0)) { if (d) Object.defineProperty(o, n, d); else delete (o as Record<string, unknown>)[n] }
      scrollTo.mockReset()
    })

    function medir(ancho: number, contenido: number) {
      parchear(HTMLElement.prototype, 'scrollTo', { value: scrollTo })
      parchear(HTMLElement.prototype, 'scrollWidth', { get: () => contenido })
      parchear(HTMLElement.prototype, 'clientWidth', { get: () => ancho })
      parchear(HTMLElement.prototype, 'getBoundingClientRect', {
        value(this: HTMLElement) {
          const i = this.getAttribute('role') === 'tab' ? [...this.parentElement!.children].indexOf(this) : -1
          return i < 0 ? { left: 0, width: ancho } : { left: i * 100, width: 100 }
        },
      })
    }

    it('al abrir la guía por un enlace a una sección lejana, esa pestaña se trae al centro de la fila', async () => {
      medir(300, 900)
      window.history.replaceState(null, '', '/guide?seccion=rules')
      await montar()
      const pestanas = [...contenedor.querySelectorAll('[role="tab"]')]
      const i = pestanas.findIndex(p => p.id === 'guia-pestana-rules')
      expect(i).toBeGreaterThan(0)
      expect(scrollTo).toHaveBeenCalled()
      // Centrada: su posición (100·i) menos la mitad de lo que sobra de la fila ((300 − 100) / 2).
      expect(scrollTo.mock.lastCall![0].left).toBe(Math.max(0, i * 100 - 100))
    })

    it('al elegir otra sección con el teclado, la fila la sigue', async () => {
      medir(300, 900)
      window.history.replaceState(null, '', '/guide')
      await montar()
      scrollTo.mockClear()
      const lista = contenedor.querySelector('[role="tablist"]')!
      await act(async () => { lista.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true })) })
      const n = contenedor.querySelectorAll('[role="tab"]').length
      // (El panel del contenido también usa scrollTo, hacia arriba: aquí solo cuenta el de la fila.)
      const deLaFila = scrollTo.mock.calls.filter(([o]) => 'left' in o)
      expect(deLaFila).toHaveLength(1)
      expect(deLaFila[0][0].left).toBe((n - 1) * 100 - 100)
    })

    it('si la fila cabe entera (escritorio) no se mueve nada', async () => {
      medir(900, 900)
      window.history.replaceState(null, '', '/guide?seccion=rules')
      await montar()
      expect(scrollTo).not.toHaveBeenCalled()
    })
  })
})
