// @vitest-environment jsdom
/** El logo de cada tracker sale de la propia app y siempre lleva su nombre, de texto alternativo o escrito al lado. */
import { afterEach, describe, expect, it } from 'vitest'
import { LogoTracker } from '../LogoTracker'
import { desmontar, montar } from './dom'

afterEach(desmontar)

describe('LogoTracker', () => {
  it.each([['emu', 'eMuwarez'], ['nobs', 'N.O.B.S'], ['mnv', 'Milnueve']])('%s tiene logo con su nombre', async (tracker, nombre) => {
    await montar(<LogoTracker tracker={tracker} nombre={nombre} />)
    const img = document.querySelector('img')!
    expect(img.getAttribute('alt')).toBe(nombre)
    expect(img.getAttribute('title')).toBe(nombre)
    expect(img.getAttribute('src')).toMatch(new RegExp(`${tracker}.*\.png`))
  })

  it('sin tracker es eMuwarez', async () => {
    await montar(<LogoTracker nombre="eMuwarez" />)
    expect(document.querySelector('img')!.getAttribute('src')).toMatch(/emu.*\.png/)
  })

  it('con el nombre al lado, el logo es decorativo', async () => {
    await montar(<LogoTracker tracker="nobs" nombre="N.O.B.S" conNombre />)
    expect(document.querySelector('img')!.getAttribute('alt')).toBe('')
    expect(document.body.textContent).toBe('N.O.B.S')
  })

  it('el de Milnueve, arte de píxel, se escala sin suavizar', async () => {
    await montar(<LogoTracker tracker="mnv" nombre="Milnueve" />)
    expect(document.querySelector('img')!.classList.contains('es-pixel')).toBe(true)
  })

  it('un tracker sin logo enseña su nombre', async () => {
    await montar(<LogoTracker tracker="otro" nombre="Otro" />)
    expect(document.querySelector('img')).toBeNull()
    expect(document.body.textContent).toBe('Otro')
  })
})
