import { describe, expect, it } from 'vitest'
import {
  admiten, destinosDeEntrada, destinosDeEntradaDeVarios, destinosQueAdmiten, resumenDeCapitulos, resumenPorTracker, soloA,
} from '../elegibilidad'

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

  it('desde una petición de otro tracker, el destino de entrada es ese tracker si lo admite', () => {
    const los_dos = [r('emu', 'eMuwarez', true), r('nobs', 'N.O.B.S', true)]
    expect(destinosDeEntradaDeVarios([los_dos], 'nobs')).toEqual(['nobs'])
    expect(destinosDeEntradaDeVarios([los_dos], 'emu')).toEqual(['emu'])
    expect(destinosDeEntradaDeVarios([los_dos])).toEqual(['emu'])
    // Si no lo admite (o no se sabe), lo de siempre.
    expect(destinosDeEntradaDeVarios([[r('emu', 'eMuwarez', true), NOBS_NO]], 'nobs')).toEqual(['emu'])
    // Sin saber aún qué admite, el de la petición; y lo que él no admite va a los suyos de siempre.
    expect(destinosDeEntradaDeVarios([undefined], 'nobs')).toEqual(['nobs'])
    expect(destinosDeEntradaDeVarios([undefined])).toEqual(['emu'])
    expect(destinosDeEntradaDeVarios([los_dos, [EMU_NO, NOBS_NO, MNV_SI]], 'nobs')).toEqual(['nobs', 'mnv'])
    expect(destinosDeEntradaDeVarios([[r('emu', 'eMuwarez', true), MNV_SI], [EMU_NO, MNV_SI]], 'emu')).toEqual(['emu', 'mnv'])
  })

  it('los capítulos: cuántos admite cada tracker', () => {
    expect(resumenDeCapitulos([[EMU_NO, MNV_SI], [EMU_NO, r('mnv', 'Milnueve', false)]]))
      .toBe('Milnueve ✓ 1 de 2 · eMuwarez ✗ sin castellano')
  })
})

describe('varios elementos (un lote, una tanda)', () => {
  const EMU_SI = r('emu', 'eMuwarez', true)
  const DESTINOS = [{ id: 'emu', nombre: 'eMuwarez' }, { id: 'mnv', nombre: 'Milnueve' }]

  it('de entrada, eMuwarez y los que admiten lo que eMuwarez no; sin saber de uno, eMuwarez', () => {
    expect(destinosDeEntradaDeVarios([[EMU_SI, MNV_SI], [EMU_NO, MNV_SI]])).toEqual(['emu', 'mnv'])
    expect(destinosDeEntradaDeVarios([[EMU_NO, MNV_SI]])).toEqual(['mnv'])
    expect(destinosDeEntradaDeVarios([undefined])).toEqual(['emu'])
  })

  it('va solo a los elegidos que lo admiten, y lo dice si no son todos', () => {
    expect(destinosQueAdmiten(DESTINOS, [EMU_NO, MNV_SI])).toEqual([DESTINOS[1]])
    expect(destinosQueAdmiten(DESTINOS, undefined)).toEqual(DESTINOS)
    expect(soloA(DESTINOS, [EMU_NO, MNV_SI])).toBe('Milnueve')
    expect(soloA(DESTINOS, [EMU_SI, MNV_SI])).toBeNull()
    // Si no lo admite ninguno, no va a ninguno: lo dicen sus comprobaciones.
    expect(soloA(DESTINOS, [EMU_NO, r('mnv', 'Milnueve', false)])).toBeNull()
  })
})
