import { describe, expect, it } from 'vitest'
import { marcadosEn } from '../useConfigForm'

describe('marcadosEn', () => {
  it('cambiar solo las mayúsculas de un grupo conserva su marca, con la escritura nueva', () => {
    expect(marcadosEn(['grupo'], ['Grupo', 'OTRO'])).toEqual(['Grupo'])
  })

  it('quitar un grupo quita su marca', () => {
    expect(marcadosEn(['GRUPO', 'OTRO'], ['OTRO'])).toEqual(['OTRO'])
  })
})
