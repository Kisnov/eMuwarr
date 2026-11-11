// @vitest-environment jsdom
/**
 * Con el dedo no hay arrastre (el mousedown de compatibilidad llega tras el touchend), así que cada fila trae subir y
 * bajar: mueven como las flechas del teclado, anuncian la posición, se apagan en el borde y no pierden el foco.
 */
import { act, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SortableList } from '../SortableList'
import { desmontar, montar } from './dom'

function Arnes() {
  const [ids, setIds] = useState(['AMZN', 'NF', 'DSNP'])
  return (
    <>
      <output id="orden">{ids.join(',')}</output>
      <SortableList ids={ids} onChange={setIds} renderRow={(id, _i, { botones }) => <div>{id}{botones}</div>} />
    </>
  )
}

const orden = () => document.getElementById('orden')!.textContent
const btn = (etiqueta: string) => document.querySelector<HTMLButtonElement>(`button[aria-label="${etiqueta}"]`)!
const pulsar = (b: HTMLButtonElement) => act(async () => { b.click() })

afterEach(desmontar)

describe('SortableList con el dedo', () => {
  it('bajar y subir reordenan la lista y anuncian la nueva posición', async () => {
    await montar(<Arnes />)
    await pulsar(btn('Bajar AMZN'))
    expect(orden()).toBe('NF,AMZN,DSNP')
    expect(document.querySelector('[role="status"]')!.textContent).toBe('AMZN: posición 2 de 3')
    await pulsar(btn('Subir DSNP'))
    expect(orden()).toBe('NF,DSNP,AMZN')
  })

  it('en el borde el botón se apaga, y el foco pasa al otro', async () => {
    await montar(<Arnes />)
    expect(btn('Subir AMZN').disabled).toBe(true)
    expect(btn('Bajar DSNP').disabled).toBe(true)
    btn('Subir NF').focus()
    await pulsar(btn('Subir NF'))
    expect(orden()).toBe('NF,AMZN,DSNP')
    expect(document.activeElement).toBe(btn('Bajar NF'))
    btn('Bajar AMZN').focus()
    await pulsar(btn('Bajar AMZN'))
    expect(orden()).toBe('NF,DSNP,AMZN')
    expect(document.activeElement).toBe(btn('Subir AMZN'))
    btn('Subir AMZN').focus()
    await pulsar(btn('Subir AMZN'))
    expect(document.activeElement).toBe(btn('Subir AMZN'))
  })
})

describe('SortableList: el deslizamiento de las filas', () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  // jsdom no maqueta: cada fila hija de la lista vale 44 px de alto y su `top` es su sitio en la lista más el translateY
  // que lleve puesto, que es lo que vería getBoundingClientRect() en un navegador.
  function simularMaquetacion() {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const lista = this.parentElement
      const esFila = lista?.firstElementChild?.getAttribute('role') === 'status' && this !== lista.firstElementChild
      const fila = esFila ? [...lista!.children].indexOf(this) - 1 : 0
      const dy = Number(/translateY\((-?[\d.]+)px\)/.exec(this.style.transform)?.[1] ?? 0)
      const top = esFila ? fila * 44 + dy : 0
      return { top, bottom: top + 44, left: 0, right: 0, width: 0, height: esFila ? 44 : 0, x: 0, y: top, toJSON() {} } as DOMRect
    })
    const pendientes: FrameRequestCallback[] = []
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { pendientes.push(cb); return pendientes.length })
    return () => { pendientes.splice(0).forEach(cb => cb(0)) }
  }
  const fila = (id: string) => [...document.querySelector('[role="status"]')!.parentElement!.children]
    .find(n => n.getAttribute('role') !== 'status' && n.textContent === id) as HTMLElement

  it('una fila que ya se movió arranca del sitio donde estaba, no del de antes del primer movimiento', async () => {
    const terminarDeslizamiento = simularMaquetacion()
    await montar(<Arnes />)

    // AMZN pasa del puesto 0 al 1: arranca 44 px por encima de su nuevo sitio (donde estaba).
    await pulsar(btn('Bajar AMZN'))
    expect(orden()).toBe('NF,AMZN,DSNP')
    expect(fila('AMZN').style.transform).toBe('translateY(-44px)')
    await act(async () => { terminarDeslizamiento() })
    expect(fila('AMZN').style.transform).toBe('')

    // Del 1 al 2: arranca desde el 1 (−44), no desde el 0 (−88), que era el sitio de antes del primer movimiento.
    await pulsar(btn('Bajar AMZN'))
    expect(orden()).toBe('NF,DSNP,AMZN')
    expect(fila('AMZN').style.transform).toBe('translateY(-44px)')
  })
})
