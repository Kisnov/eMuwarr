// La preparación de una petición nombra su tracker: la de N.O.B.S no «ya existe en eMuwarez».
import { describe, expect, it } from 'vitest'
import { getReadinessLabel } from '../status'

describe('getReadinessLabel', () => {
  it('sin tracker, eMuwarez como siempre', () => {
    expect(getReadinessLabel('already_exists')).toBe('Ya existe en eMuwarez')
  })

  it('con el tracker de la petición', () => {
    expect(getReadinessLabel('already_exists', 'N.O.B.S')).toBe('Ya existe en N.O.B.S')
    expect(getReadinessLabel('filled', 'Milnueve')).toBe('Ya llena en Milnueve')
    expect(getReadinessLabel('no_castellano', 'Milnueve')).toBe('Sin castellano')
  })
})
