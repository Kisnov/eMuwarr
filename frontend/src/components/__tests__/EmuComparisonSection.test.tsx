// @vitest-environment jsdom
// La ficha de la comparación con eMuwarez: dice lo que concluye el servidor, no lo decide ella.
import { afterEach, describe, expect, it } from 'vitest'
import type { ConclusionDuplicados, EmuComparison } from '../../api/library'
import { EmuComparisonContent } from '../EmuComparisonSection'
import { desmontar, montar, texto } from './dom'

const comparacion = (conclusion: ConclusionDuplicados, aportes: EmuComparison['aportes'] = []): EmuComparison => ({
  available: true, message: null, in_catalog: 1, mine_available: true, aportes, conclusion,
  mine: { resolution: '1080p', source: 'WEB-DL', codec: 'x264', has_es: true, size: 1 },
  matches: [{ name: 'Peli 1080p WEB-DL VOSE', resolution: '1080p', source: 'WEB-DL', codec: 'x264', has_spanish: 0, size: 2, veredicto: 'igual' }],
})

afterEach(desmontar)

describe('EmuComparisonContent', () => {
  it('una mejora dice la frase del servidor y en qué mejora', async () => {
    await montar(<EmuComparisonContent data={comparacion({
      nivel: 'trump', clase: 'mejor', ya_lo_tiene: false,
      texto: 'Aportas audio ESP que no tiene ninguno: puede ser un trump. Revisa las reglas de trumps antes de subir.',
    }, [{ dim: 'idioma', detail: 'ESP' }])} />)
    expect(texto()).toContain('Aportas audio ESP que no tiene ninguno: puede ser un trump.')
    expect([...document.querySelectorAll('.comp-chip-aporte')].map(c => c.textContent)).toEqual(['ESP'])
  })

  it('un duplicado dice la frase del servidor y ningún aporte', async () => {
    await montar(<EmuComparisonContent data={comparacion({
      nivel: 'duplicado', clase: 'igual', ya_lo_tiene: true,
      texto: 'Ya hay 1 torrent con tu misma calidad: la subida sería un duplicado.',
    }, [{ dim: 'source', detail: 'WEB-DL' }])} />)
    expect(document.querySelector('.comp-veredicto')!.textContent).toBe('Ya hay 1 torrent con tu misma calidad: la subida sería un duplicado.')
    expect(document.querySelector('.comp-chip-aporte')).toBeNull()
  })
})
