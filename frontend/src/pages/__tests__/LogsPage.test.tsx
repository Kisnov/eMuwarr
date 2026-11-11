// @vitest-environment jsdom
// Logs: copiar una línea lo confirma, y el teclado entra a la lista por una sola línea y se mueve con las flechas.
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LogEntry } from '../../api/logs'

vi.mock('../../api/logs', () => ({ getLogs: vi.fn() }))
// Sin la sección a la vista no corre el refresco de cada 3 segundos: solo se enciende en las pruebas del refresco.
const seccion = vi.hoisted(() => ({ visible: false }))
vi.mock('../../context/SeccionVisible', () => ({ useSeccionVisible: () => seccion.visible }))

import { getLogs } from '../../api/logs'
import { LogsPage } from '../LogsPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const LINEAS: LogEntry[] = [
  { ts: 3, time: '2026-11-06 11:00:03', level: 'ERROR', logger: 'app', message: 'tercera' },
  { ts: 2, time: '2026-11-06 11:00:02', level: 'INFO', logger: 'app', message: 'segunda' },
  { ts: 1, time: '2026-11-06 11:00:01', level: 'INFO', logger: 'app', message: 'primera' },
]

let raiz: Root
let contenedor: HTMLDivElement
const escribirPortapapeles = vi.fn()

async function esperar() {
  await act(async () => { await new Promise(r => setTimeout(r, 0)) })
}

async function montar() {
  contenedor = document.createElement('div')
  document.body.appendChild(contenedor)
  raiz = createRoot(contenedor)
  await act(async () => { raiz.render(<LogsPage />) })
  await esperar()
}

const lineas = () => [...contenedor.querySelectorAll<HTMLButtonElement>('.lg-linea__cuerpo')]
async function tecla(el: HTMLElement, key: string) {
  await act(async () => { el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })) })
  await esperar()
}

beforeEach(() => {
  vi.mocked(getLogs).mockResolvedValue({ items: structuredClone(LINEAS), count: 3 } as Awaited<ReturnType<typeof getLogs>>)
  escribirPortapapeles.mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: escribirPortapapeles }, configurable: true })
})

afterEach(async () => {
  await act(async () => { raiz.unmount() })
  contenedor.remove()
  seccion.visible = false
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('Logs', () => {
  it('copiar una línea lo dice en el contador', async () => {
    await montar()
    const contador = () => contenedor.querySelector('.lg-contador')?.textContent
    expect(contador()).toBe('3 líneas')
    await act(async () => { contenedor.querySelector<HTMLButtonElement>('.lg-copiar')!.click() })
    await esperar()
    expect(escribirPortapapeles).toHaveBeenCalledTimes(1)
    expect(contador()).toBe('Línea copiada')
  })

  it('solo una línea está en el orden del tabulador, los botones de copiar no, y las flechas mueven el foco', async () => {
    await montar()
    expect(lineas().map(b => b.tabIndex)).toEqual([0, -1, -1])
    // La lista no es otra parada: el primer Tab llega directo a la línea, donde funcionan las flechas y C.
    expect(contenedor.querySelector('.lg-lista')!.hasAttribute('tabindex')).toBe(false)
    expect([...contenedor.querySelectorAll<HTMLButtonElement>('.lg-copiar')].every(b => b.tabIndex === -1)).toBe(true)
    await act(async () => { lineas()[0].focus() })
    await tecla(lineas()[0], 'ArrowDown')
    expect(document.activeElement).toBe(lineas()[1])
    // La que tiene el foco pasa a ser la que entra con Tab.
    expect(lineas().map(b => b.tabIndex)).toEqual([-1, 0, -1])
    await tecla(lineas()[1], 'End')
    expect(document.activeElement).toBe(lineas()[2])
    await tecla(lineas()[2], 'Home')
    expect(document.activeElement).toBe(lineas()[0])
  })

  it('si un refresco se lleva la línea enfocada, el foco vuelve a la lista', async () => {
    await montar()
    await act(async () => { lineas()[2].focus() })
    expect(document.activeElement).toBe(lineas()[2])
    // Llega una línea nueva y la más vieja (la enfocada) sale de las que se enseñan.
    vi.mocked(getLogs).mockResolvedValue({
      items: [{ ts: 4, time: '2026-11-06 11:00:04', level: 'INFO', logger: 'app', message: 'cuarta' }, ...structuredClone(LINEAS).slice(0, 2)],
      count: 3,
    } as Awaited<ReturnType<typeof getLogs>>)
    const refrescar = [...contenedor.querySelectorAll('button')].find(b => b.textContent === 'Refrescar')!
    // Como el refresco automático: el foco sigue en la línea, que desaparece; sin el arreglo cae al body.
    await act(async () => { refrescar.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    await esperar()
    expect(lineas().map(l => l.textContent).join(' ')).not.toContain('primera')
    expect(contenedor.querySelector('.lg-lista')!.contains(document.activeElement)).toBe(true)
  })

  it('si el foco ya no estaba en la lista (clic fuera), un refresco no se lo devuelve', async () => {
    await montar()
    await act(async () => { lineas()[2].focus() })
    // Clic en una zona no interactiva: el foco sale de la línea, aunque esa línea siga siendo la «activa».
    await act(async () => { lineas()[2].blur() })
    expect(document.activeElement).toBe(document.body)
    vi.mocked(getLogs).mockResolvedValue({
      items: [{ ts: 4, time: '2026-11-06 11:00:04', level: 'INFO', logger: 'app', message: 'cuarta' }, ...structuredClone(LINEAS).slice(0, 2)],
      count: 3,
    } as Awaited<ReturnType<typeof getLogs>>)
    const refrescar = [...contenedor.querySelectorAll('button')].find(b => b.textContent === 'Refrescar')!
    await act(async () => { refrescar.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    await esperar()
    expect(lineas().map(l => l.textContent).join(' ')).not.toContain('primera')
    expect(document.activeElement).toBe(document.body)
  })

  it('la tecla C copia la línea enfocada', async () => {
    await montar()
    await act(async () => { lineas()[1].focus() })
    await tecla(lineas()[1], 'c')
    expect(escribirPortapapeles).toHaveBeenCalledTimes(1)
    expect(escribirPortapapeles.mock.calls[0][0]).toContain('segunda')
  })

  describe('refresco automático con `since`', () => {
    const CON_SEQ: LogEntry[] = LINEAS.map(l => ({ ...l, seq: 100 + l.ts }))
    const nueva = (ts: number, message: string): LogEntry =>
      ({ ts, seq: 100 + ts, time: `2026-11-06 11:00:0${ts}`, level: 'INFO', logger: 'app', message })
    type Resp = Awaited<ReturnType<typeof getLogs>>

    // Monta con la sección a la vista y relojes falsos, y deja hecha la primera lectura (la entera).
    async function montarConRefresco() {
      vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
      seccion.visible = true
      vi.mocked(getLogs).mockResolvedValue({ items: structuredClone(CON_SEQ), count: 3 } as Resp)
      await montar()
      expect(vi.mocked(getLogs)).toHaveBeenCalledTimes(1)
      expect(vi.mocked(getLogs).mock.calls[0][0]?.since).toBeUndefined()
    }
    async function pasan3Segundos() {
      await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    }
    // El mismo elemento del DOM (`toEqual` solo compara el contenido).
    const mismosElementos = (a: Element[], b: Element[]) => {
      expect(a).toHaveLength(b.length)
      a.forEach((el, i) => expect(el).toBe(b[i]))
    }
    const textos = () => lineas().map(l => l.textContent ?? '')

    it('pide solo lo posterior a la línea más nueva y suma lo que llega, sin tocar las que ya estaban', async () => {
      await montarConRefresco()
      const antes = lineas()
      vi.mocked(getLogs).mockResolvedValue({ items: [nueva(4, 'cuarta')], count: 1, truncated: false } as Resp)
      await pasan3Segundos()
      expect(vi.mocked(getLogs).mock.calls[1][0]).toMatchObject({ limit: 1000, since: 103 })
      expect(textos()).toHaveLength(4)
      expect(textos()[0]).toContain('cuarta')
      // Las tres de antes siguen siendo los mismos elementos del DOM: no se han repintado.
      mismosElementos(lineas().slice(1), antes)
      expect(contenedor.querySelector('.lg-contador')?.textContent).toBe('4 líneas')
      // Y la siguiente lectura parte de la nueva.
      vi.mocked(getLogs).mockResolvedValue({ items: [], count: 0, truncated: false } as Resp)
      await pasan3Segundos()
      expect(vi.mocked(getLogs).mock.calls[2][0]).toMatchObject({ since: 104 })
    })

    it('sin novedades no cambia nada: ni el foco ni las líneas', async () => {
      await montarConRefresco()
      await act(async () => { lineas()[1].focus() })
      const antes = lineas()
      vi.mocked(getLogs).mockResolvedValue({ items: [], count: 0, truncated: false } as Resp)
      await pasan3Segundos()
      expect(vi.mocked(getLogs).mock.calls[1][0]).toMatchObject({ since: 103 })
      mismosElementos(lineas(), antes)
      expect(document.activeElement).toBe(antes[1])
    })

    it('una línea nueva no le quita el foco a la que lo tenía', async () => {
      await montarConRefresco()
      await act(async () => { lineas()[1].focus() })
      const enfocada = document.activeElement
      vi.mocked(getLogs).mockResolvedValue({ items: [nueva(4, 'cuarta')], count: 1, truncated: false } as Resp)
      await pasan3Segundos()
      expect(textos()).toHaveLength(4)
      expect(document.activeElement).toBe(enfocada)
    })

    it('si han entrado más de las que caben (`truncated`), lo recibido sustituye a la lista', async () => {
      await montarConRefresco()
      vi.mocked(getLogs).mockResolvedValue({ items: [nueva(9, 'novena'), nueva(8, 'octava')], count: 2, truncated: true } as Resp)
      await pasan3Segundos()
      expect(textos().map(t => /novena|octava/.test(t))).toEqual([true, true])
    })

    it('«Refrescar» lee la lista entera, sin `since`', async () => {
      await montarConRefresco()
      const refrescar = [...contenedor.querySelectorAll('button')].find(b => b.textContent === 'Refrescar')!
      await act(async () => { refrescar.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
      expect(vi.mocked(getLogs).mock.calls[1][0]?.since).toBeUndefined()
    })

    it('si el servidor no manda `seq`, el refresco sigue leyendo la lista entera', async () => {
      vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
      seccion.visible = true
      await montar()  // las de LINEAS no llevan seq
      await pasan3Segundos()
      expect(vi.mocked(getLogs).mock.calls[1][0]?.since).toBeUndefined()
    })
  })
})
