/**
 * Qué cuenta como «la sesión ya no vale». Solo un 401: un fallo de red, un 502
 * de un proxy o el 503 de una base ocupada no dicen nada del token, y tratarlos
 * como una sesión caducada cerraba la sesión de quien no había hecho nada.
 */
import { describe, it, expect } from 'vitest'
import { ApiClientError, esSesionCaducada, extractErrorDetail } from '../client'

describe('esSesionCaducada', () => {
  it('un 401 es una sesión caducada', () => {
    expect(esSesionCaducada(new ApiClientError('No autorizado', 401, null))).toBe(true)
  })

  it.each([0, 403, 404, 500, 502, 503])('un %i no lo es', status => {
    expect(esSesionCaducada(new ApiClientError('x', status, null))).toBe(false)
  })

  it('un error que no viene de la API tampoco', () => {
    expect(esSesionCaducada(new TypeError('Failed to fetch'))).toBe(false)
    expect(esSesionCaducada(null)).toBe(false)
  })
})

describe('extractErrorDetail', () => {
  it('lee detail como texto', () => {
    expect(extractErrorDetail({ detail: 'Mal' }, 'Bad Request')).toBe('Mal')
  })

  it('lee detail.message cuando detail es un objeto', () => {
    expect(extractErrorDetail({ detail: { message: 'Ya se reintentó' } }, 'Bad Request')).toBe('Ya se reintentó')
  })

  it('cae al texto por defecto si el objeto no trae message', () => {
    expect(extractErrorDetail({ detail: { code: 1 } }, 'Bad Request')).toBe('Bad Request')
  })
})
