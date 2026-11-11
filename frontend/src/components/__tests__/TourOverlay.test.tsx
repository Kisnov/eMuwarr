// @vitest-environment jsdom
/**
 * El tour guiado: es un diálogo (el foco entra, Tab no se sale, Escape cierra y el foco vuelve), resalta su objetivo
 * cuando existe, sigue adelante (centrado, sin recorte) cuando no existe, y en el móvil se salta los pasos de la barra
 * lateral, que ahí es un cajón cerrado.
 */
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TourProvider, useTour, type TourStep } from '../../context/TourContext'
import { pasosDelTour, TOUR_STEPS } from '../../tour/steps'
import { TourOverlay } from '../TourOverlay'
import { desmontar, esperar, montar, pulsar } from './dom'

const PASOS: TourStep[] = [
  { id: 'uno', selector: '[data-tour="uno"]', title: 'Primero', description: 'Primer paso.' },
  { id: 'dos', selector: '[data-tour="no-existe"]', title: 'Segundo', description: 'Un objetivo que no está en la página.' },
  { id: 'tres', selector: '[data-tour="uno"]', title: 'Tercero', description: 'Último paso.' },
]

function Arnes() {
  const { start } = useTour()
  return (
    <>
      <button id="abrir" onClick={() => start(PASOS)}>Abrir tour</button>
      <div data-tour="uno">Objetivo</div>
      <TourOverlay activePage="dashboard" navigate={vi.fn()} />
    </>
  )
}

const burbuja = () => document.querySelector<HTMLElement>('.tour-bubble')
const titulo = () => document.querySelector('.tour-bubble-title')?.textContent
const pulsarBoton = (nombre: string) => pulsar([...document.querySelectorAll('.tour-bubble button')].find(b => b.textContent === nombre))
const tecla = (key: string, opciones: KeyboardEventInit = {}) =>
  act(async () => { document.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...opciones })) })

beforeEach(() => {
  // jsdom no mide nada: el objetivo "uno" ocupa un rectángulo para que se pueda resaltar.
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const mide = this.dataset?.tour === 'uno'
    return { top: 100, left: 100, right: 300, bottom: 140, width: mide ? 200 : 0, height: mide ? 40 : 0, x: 100, y: 100, toJSON: () => ({}) }
  })
})
afterEach(async () => { await desmontar(); vi.restoreAllMocks() })

async function abrir() {
  await montar(<TourProvider><Arnes /></TourProvider>)
  const opener = document.getElementById('abrir') as HTMLButtonElement
  opener.focus()
  await pulsar(opener)
  return opener
}

describe('TourOverlay como diálogo', () => {
  it('se anuncia como diálogo con el título del paso y el foco entra en la burbuja', async () => {
    await abrir()
    const b = burbuja()!
    expect(b.getAttribute('role')).toBe('dialog')
    expect(b.getAttribute('aria-modal')).toBe('true')
    expect(document.getElementById(b.getAttribute('aria-labelledby')!)?.textContent).toBe('Primero')
    expect(document.activeElement).toBe(b)
  })

  it('Tab da la vuelta dentro de la burbuja, en los dos sentidos', async () => {
    await abrir()
    // Primer paso: Cerrar y Siguiente (Atrás no existe aún).
    const botones = [...document.querySelectorAll<HTMLElement>('.tour-bubble button')]
    expect(botones.map(b => b.textContent)).toEqual(['Cerrar', 'Siguiente'])

    botones[1].focus()
    await tecla('Tab')
    expect(document.activeElement).toBe(botones[0])

    await tecla('Tab', { shiftKey: true })
    expect(document.activeElement).toBe(botones[1])

    // Con el foco en la propia burbuja, Tab entra por el primer botón.
    burbuja()!.focus()
    await tecla('Tab')
    expect(document.activeElement).toBe(botones[0])
  })

  it('Escape lo cierra y el foco vuelve al botón que lo abrió', async () => {
    const opener = await abrir()
    await tecla('Escape')
    expect(burbuja()).toBeNull()
    expect(document.activeElement).toBe(opener)
  })

  it('resalta el objetivo si existe; si no existe, sigue adelante centrado y sin recorte', async () => {
    await abrir()
    await esperar(() => document.querySelector('.tour-cutout'))
    expect(document.querySelector('.tour-backdrop.no-target')).toBeNull()

    await pulsarBoton('Siguiente')
    expect(titulo()).toBe('Segundo')
    expect(document.querySelector('.tour-cutout')).toBeNull()
    expect(document.querySelector('.tour-backdrop.no-target')).not.toBeNull()
    expect(burbuja()).not.toBeNull()

    // El paso sin objetivo no bloquea el recorrido.
    await pulsarBoton('Siguiente')
    expect(titulo()).toBe('Tercero')
    expect(document.activeElement?.closest('.tour-bubble')).not.toBeNull()
  })
})

describe('TourOverlay: dónde va la burbuja', () => {
  it('encima de un objetivo pegado abajo, con su alto real: no lo tapa', async () => {
    // El objetivo, abajo de la ventana (jsdom: 768 px de alto); la burbuja mide 250 px, no los 180 que se suponían.
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const mide = this.dataset?.tour === 'uno'
      return { top: 680, left: 100, right: 300, bottom: 740, width: mide ? 200 : 0, height: mide ? 60 : 0, x: 100, y: 680, toJSON: () => ({}) }
    })
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('tour-bubble') ? 250 : 0
    })
    await abrir()
    await esperar(() => document.querySelector('.tour-cutout'))
    const top = parseFloat(burbuja()!.style.top)
    expect(top + 250).toBeLessThanOrEqual(680)
  })

  it('si la ventana cambia de tamaño sin cambiar de paso, la burbuja se vuelve a medir y no tapa el objetivo', async () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const mide = this.dataset?.tour === 'uno'
      return { top: 680, left: 100, right: 300, bottom: 740, width: mide ? 200 : 0, height: mide ? 60 : 0, x: 100, y: 680, toJSON: () => ({}) }
    })
    let altoBurbuja = 200
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('tour-bubble') ? altoBurbuja : 0
    })
    await abrir()
    await esperar(() => document.querySelector('.tour-cutout'))
    // Más estrecha: el texto ocupa más líneas y la burbuja crece.
    altoBurbuja = 300
    await act(async () => { window.dispatchEvent(new Event('resize')) })
    const top = parseFloat(burbuja()!.style.top)
    expect(top + 300).toBeLessThanOrEqual(680)
  })

  // jsdom mide 1024×768. El objetivo es una columna alta (más de media ventana) a la izquierda: no cabe la burbuja
  // (250 px) ni encima ni debajo.
  const medirObjetivo = (r: { top: number; left: number; right: number; bottom: number }) => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const mide = this.dataset?.tour === 'uno'
      const ancho = r.right - r.left, alto = r.bottom - r.top
      return { ...r, width: mide ? ancho : 0, height: mide ? alto : 0, x: r.left, y: r.top, toJSON: () => ({}) }
    })
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('tour-bubble') ? 250 : 0
    })
  }

  it('junto a un objetivo más alto que media ventana: a su lado, sin taparlo', async () => {
    medirObjetivo({ top: 80, left: 100, right: 400, bottom: 700 })
    await abrir()
    await esperar(() => document.querySelector('.tour-cutout'))
    const { left, top } = burbuja()!.style
    // A la derecha del objetivo (400 + 14), a la altura de su parte de arriba y entera en la ventana.
    expect(parseFloat(left)).toBe(414)
    expect(parseFloat(top)).toBe(80)
    expect(parseFloat(top) + 250).toBeLessThanOrEqual(768)
  })

  it('un objetivo alto pegado a la derecha: la burbuja pasa a su izquierda', async () => {
    medirObjetivo({ top: 80, left: 600, right: 1000, bottom: 700 })
    await abrir()
    await esperar(() => document.querySelector('.tour-cutout'))
    // 600 - 14 - 340
    expect(parseFloat(burbuja()!.style.left)).toBe(246)
  })

  it('sin sitio a los lados (un objetivo de todo el ancho), la burbuja entera dentro de la ventana, en el borde que menos lo tapa', async () => {
    medirObjetivo({ top: 60, left: 0, right: 1024, bottom: 700 })
    await abrir()
    await esperar(() => document.querySelector('.tour-cutout'))
    const top = parseFloat(burbuja()!.style.top)
    expect(top).toBeGreaterThanOrEqual(14)
    expect(top + 250).toBeLessThanOrEqual(768 - 14)
    // Arriba solapa 250 - 60 + 14 = 204 px; abajo 700 - 504 = 196: abajo tapa menos.
    expect(top).toBe(504)
  })

  it('los botones: «Cerrar» fantasma (no destruye nada), «Atrás» secundario y solo «Siguiente» principal', async () => {
    await abrir()
    await pulsarBoton('Siguiente')
    expect([...document.querySelectorAll('.tour-bubble button')].map(b => [b.textContent, b.className])).toEqual([
      ['Cerrar', 'btn btn-ghost btn-sm'],
      ['Atrás', 'btn btn-secondary btn-sm'],
      ['Siguiente', 'btn btn-primary btn-sm'],
    ])
  })
})

describe('pasos del tour según la pantalla', () => {
  it('en el móvil no están los pasos de la barra lateral; en escritorio, todos', () => {
    expect(TOUR_STEPS.some(s => s.id === 'sidebar-gauges')).toBe(true)
    expect(pasosDelTour(false)).toEqual(TOUR_STEPS)
    const movil = pasosDelTour(true)
    expect(movil.some(s => s.id === 'sidebar-gauges')).toBe(false)
    expect(movil.every(s => !s.selector.includes('sidebar'))).toBe(true)
    expect(movil.length).toBe(TOUR_STEPS.length - 1)
  })
})
