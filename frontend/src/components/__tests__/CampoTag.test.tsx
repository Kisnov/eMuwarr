// @vitest-environment jsdom
/**
 * Los campos del asistente llevan la etiqueta y el «?» de ayuda dentro del mismo <label>. Un <label> sin `htmlFor`
 * etiqueta el primer control que envuelve, que era el botón del «?» y no el campo.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { CampoTag } from '../asistente/piezas'
import { desmontar, montar } from './dom'

afterEach(desmontar)

describe('CampoTag', () => {
  it('la etiqueta es del desplegable, no del botón de ayuda', async () => {
    await montar(<CampoTag value="GRUPO" opciones={['GRUPO', 'NOGRP']} onChange={() => {}} />)
    const etiqueta = document.querySelector('label')!
    const desplegable = document.querySelector('select')!
    expect(etiqueta.querySelector('button.menores-info')).not.toBeNull()
    expect(etiqueta.control).toBe(desplegable)
    expect(desplegable.labels).toHaveLength(1)
  })
})
