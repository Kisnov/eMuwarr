// @vitest-environment jsdom
/** El chip del tracker solo sale para los que no son eMuwarez: quien solo usa eMuwarez no ve nada nuevo. */
import { afterEach, describe, expect, it } from 'vitest'
import { ChipTracker } from '../ChipTracker'
import { desmontar, montar } from './dom'

afterEach(desmontar)

describe('ChipTracker', () => {
  it('dice el tracker de una fila que no es de eMuwarez', async () => {
    await montar(<ChipTracker tracker="nobs" nombre="N.O.B.S" />)
    expect(document.querySelector('.sub-tracker-chip')?.textContent).toBe('N.O.B.S')
    expect(document.querySelector('.sub-tracker-chip img')?.getAttribute('alt')).toBe('')
  })

  it.each([['emu', 'eMuwarez'], [undefined, undefined]])('con %s no pinta nada', async (tracker, nombre) => {
    await montar(<ChipTracker tracker={tracker} nombre={nombre} />)
    expect(document.querySelector('.sub-tracker-chip')).toBeNull()
  })
})
