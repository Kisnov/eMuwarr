import { describe, expect, it } from 'vitest'
import { admiten, destinosDeEntrada, resumenDeCapitulos, resumenPorTracker } from '../elegibilidad'

const r = (tracker: string, nombre: string, permitido: boolean, variante: string | null = null) =>
  ({ tracker, nombre, permitido, motivo: '', variante })

const EMU_NO = r('emu', 'eMuwarez', false)
const NOBS_NO = r('nobs', 'N.O.B.S', false)
const MNV_SI = r('mnv', 'Milnueve', true, 'anime_catalan')

describe('elegibilidad por tracker', () => {
  it('un anime solo en catalán: solo Milnueve, primero y con su categoría', () => {
    expect(admiten([EMU_NO, NOBS_NO, MNV_SI])).toEqual(['Milnueve'])
    expect(resumenPorTracker([EMU_NO, NOBS_NO, MNV_SI]))
      .toBe('Milnueve ✓ (Anime en Catalan) · eMuwarez ✗ sin castellano · N.O.B.S ✗')
    expect(destinosDeEntrada([EMU_NO, NOBS_NO, MNV_SI])).toEqual(['mnv'])
  })

  it('si eMuwarez lo admite, de entrada solo eMuwarez, como siempre; si no lo admite nadie, también', () => {
    expect(destinosDeEntrada([r('emu', 'eMuwarez', true), r('nobs', 'N.O.B.S', true)])).toEqual(['emu'])
    expect(destinosDeEntrada([EMU_NO, NOBS_NO])).toEqual(['emu'])
  })

  it('los capítulos: cuántos admite cada tracker', () => {
    expect(resumenDeCapitulos([[EMU_NO, MNV_SI], [EMU_NO, r('mnv', 'Milnueve', false)]]))
      .toBe('Milnueve ✓ 1 de 2 · eMuwarez ✗ sin castellano')
  })
})
