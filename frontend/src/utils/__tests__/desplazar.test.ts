// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { contenedorDeScroll, desplazarAlContenedor, destinoDeScroll } from '../desplazar'

describe('destinoDeScroll', () => {
  // Contenedor de 500 px de alto con el borde superior en y=100, ya desplazado 300 px.
  const c = { top: 100, height: 500, scrollTop: 300 }

  it('start: deja el elemento arriba con su margen', () => {
    // El elemento está a 400 px del borde del contenedor: scrollTop 300 + 400 - 12.
    expect(destinoDeScroll(c, { top: 500, height: 200 }, { block: 'start', margen: 12 })).toBe(688)
  })

  it('start: nunca va por debajo de cero', () => {
    expect(destinoDeScroll({ top: 100, height: 500, scrollTop: 0 }, { top: 90, height: 50 }, { block: 'start' })).toBe(0)
  })

  it('center: deja el elemento en el centro del contenedor', () => {
    // delta 400, centro: 300 + 400 - (500 - 100) / 2 = 500.
    expect(destinoDeScroll(c, { top: 500, height: 100 }, { block: 'center' })).toBe(500)
  })

  it('nearest: no mueve nada si el elemento se ve entero', () => {
    expect(destinoDeScroll(c, { top: 200, height: 100 }, { block: 'nearest' })).toBeNull()
  })

  it('nearest: sube lo justo si asoma por arriba y baja lo justo si asoma por abajo', () => {
    expect(destinoDeScroll(c, { top: 90, height: 100 }, { block: 'nearest', margen: 12 })).toBe(300 - 10 - 12)
    // Su borde inferior queda a 450 + 100 = 550 px del borde del contenedor, que mide 500: baja 50 más el margen de 12.
    expect(destinoDeScroll(c, { top: 550, height: 100 }, { block: 'nearest', margen: 12 })).toBe(362)
  })
})

describe('desplazarAlContenedor', () => {
  afterEach(() => { vi.restoreAllMocks(); document.body.innerHTML = '' })

  function escena({ desplazable }: { desplazable: boolean }) {
    document.body.innerHTML = `
      <div id="app" style="overflow: hidden">
        <div id="cont" style="overflow-y: auto"><div id="dentro"><section id="el"></section></div></div>
      </div>`
    const cont = document.getElementById('cont')!
    const el = document.getElementById('el')!
    Object.defineProperty(cont, 'scrollHeight', { value: desplazable ? 2000 : 400, configurable: true })
    Object.defineProperty(cont, 'clientHeight', { value: 400, configurable: true })
    cont.getBoundingClientRect = () => ({ top: 50, height: 400, bottom: 450, left: 0, right: 0, width: 0, x: 0, y: 50, toJSON() {} })
    el.getBoundingClientRect = () => ({ top: 650, height: 100, bottom: 750, left: 0, right: 0, width: 0, x: 0, y: 650, toJSON() {} })
    cont.scrollTop = 100
    return { cont, el }
  }

  it('mueve solo el contenedor que se desplaza, no la ventana ni el marco con overflow hidden', () => {
    const { cont, el } = escena({ desplazable: true })
    const dentro = vi.fn()
    el.scrollIntoView = dentro
    const salto = vi.fn()
    cont.scrollTo = salto
    const marco = vi.fn()
    document.getElementById('app')!.scrollTo = marco
    const ventana = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})

    desplazarAlContenedor(el, { block: 'start', behavior: 'smooth', margen: 12 })

    expect(salto).toHaveBeenCalledWith({ top: 100 + (650 - 50) - 12, behavior: 'smooth' })
    expect(dentro).not.toHaveBeenCalled()
    expect(marco).not.toHaveBeenCalled()
    expect(ventana).not.toHaveBeenCalled()
  })

  it('un contenedor que no desborda no cuenta: se cae a scrollIntoView', () => {
    const { el } = escena({ desplazable: false })
    const dentro = vi.fn()
    el.scrollIntoView = dentro
    expect(contenedorDeScroll(el)).toBeNull()
    desplazarAlContenedor(el, { block: 'center' })
    expect(dentro).toHaveBeenCalledWith({ block: 'center', behavior: undefined })
  })

  it('sin elemento no hace nada', () => {
    expect(() => desplazarAlContenedor(null)).not.toThrow()
  })
})
