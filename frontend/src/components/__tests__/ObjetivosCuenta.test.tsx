// @vitest-environment jsdom
/**
 * Objetivos de la cuenta en Estadísticas: lo que enseña, y el editor que guarda la fecha de alta y las metas.
 */
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/estadisticas', () => ({ getObjetivos: vi.fn(), guardarObjetivos: vi.fn() }))

import { getObjetivos, guardarObjetivos } from '../../api/estadisticas'
import type { PuntoCuenta, RitmosCuenta } from '../../types/api'
import { ObjetivosCuenta } from '../ObjetivosCuenta'
import { boton, desmontar, esperar, montar, pulsar, texto } from './dom'

const leer = vi.mocked(getObjetivos)
const guardar = vi.mocked(guardarObjetivos)

const TB = 1024 ** 4
const ultimo: PuntoCuenta = {
  dia: '2026-11-18', ratio: 20, subido_bytes: 100 * TB, bajado_bytes: 5 * TB, bonus: 1000, buffer_bytes: 95 * TB,
  seeding: 770, leeching: 0, hit_and_runs: 2, grupo: 'InsaneUser',
}
const ritmos: RitmosCuenta = { dias: 7, subido_dia: 0.5 * TB, bajado_dia: 0, bonus_hora: 10 }

function escribir(el: HTMLInputElement | HTMLSelectElement, valor: string) {
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, valor)
  el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
}

beforeEach(() => { leer.mockReset(); guardar.mockReset() })
afterEach(desmontar)

describe('ObjetivosCuenta', () => {
  it('sin nada guardado invita a añadir objetivos', async () => {
    leer.mockResolvedValue({ fecha_alta: null, objetivos: [] })
    await montar(<ObjetivosCuenta visible ultimo={ultimo} ritmos={ritmos} />)
    await esperar(() => boton('Añadir objetivos'))
    expect(texto()).toContain('Ponte una meta')
  })

  it('enseña antigüedad, progreso y cuándo se llega', async () => {
    leer.mockResolvedValue({ fecha_alta: '2026-02-20', objetivos: [{ tipo: 'subido', valor: 150 }, { tipo: 'seeding', valor: 500 }] })
    await montar(<ObjetivosCuenta visible ultimo={ultimo} ritmos={ritmos} />)
    await esperar(() => document.querySelector('.est-objetivos-lista li'))
    expect(texto()).toContain('Antigüedad:')
    const filas = [...document.querySelectorAll('.est-objetivos-lista li')].map(li => li.textContent)
    expect(filas[0]).toContain('100,00 TB / 150,00 TB')
    expect(filas[0]).toContain('≈ 3 meses')
    expect(filas[1]).toContain('Cumplido')
    const barras = [...document.querySelectorAll('[role="progressbar"]')].map(b => b.getAttribute('aria-valuenow'))
    expect(barras).toEqual(['67', '100'])
  })

  it('el editor guarda la fecha de alta y un objetivo nuevo', async () => {
    leer.mockResolvedValue({ fecha_alta: null, objetivos: [] })
    guardar.mockImplementation(async d => d)
    await montar(<ObjetivosCuenta visible ultimo={ultimo} ritmos={ritmos} />)
    await pulsar(await esperar(() => boton('Añadir objetivos')))
    await act(async () => { escribir(document.querySelector<HTMLInputElement>('input[type="date"]')!, '2026-02-20') })
    await pulsar(boton('Añadir objetivo')!)
    await act(async () => {
      escribir(document.querySelector<HTMLSelectElement>('select[aria-label="Tipo de objetivo"]')!, 'ratio')
    })
    await act(async () => { escribir(document.querySelector<HTMLInputElement>('input[type="number"]')!, '25') })
    await pulsar(boton('Guardar')!)
    expect(guardar).toHaveBeenCalledWith({ fecha_alta: '2026-02-20', objetivos: [{ tipo: 'ratio', valor: 25 }] })
    await esperar(() => document.querySelector('.est-objetivos-lista li'))
    expect(texto()).toContain('Ratio')
  })

  it('si el servidor lo rechaza, lo dice y sigue en el editor', async () => {
    leer.mockResolvedValue({ fecha_alta: null, objetivos: [{ tipo: 'bonus', valor: 5000 }] })
    guardar.mockRejectedValueOnce(new Error('422'))
    await montar(<ObjetivosCuenta visible ultimo={ultimo} ritmos={ritmos} />)
    await pulsar(await esperar(() => boton('Editar')))
    await pulsar(boton('Guardar')!)
    await esperar(() => document.querySelector('[role="alert"]'))
    expect(boton('Cancelar')).toBeTruthy()
  })

  it('si no se pueden cargar, lo dice y deja reintentar', async () => {
    leer.mockRejectedValueOnce(new Error('503'))
    await montar(<ObjetivosCuenta visible ultimo={ultimo} ritmos={ritmos} />)
    await esperar(() => texto().includes('No se pudieron cargar tus objetivos'))
    leer.mockResolvedValueOnce({ fecha_alta: null, objetivos: [] })
    await pulsar(boton('Reintentar'))
    await esperar(() => boton('Añadir objetivos'))
    expect(texto()).not.toContain('No se pudieron cargar')
  })

  it('cambiar el tipo no rehace la fila (no se pierde el foco)', async () => {
    leer.mockResolvedValue({ fecha_alta: null, objetivos: [{ tipo: 'subido', valor: 150 }] })
    await montar(<ObjetivosCuenta visible ultimo={ultimo} ritmos={ritmos} />)
    await pulsar(await esperar(() => boton('Editar')))
    const antes = document.querySelector<HTMLSelectElement>('select[aria-label="Tipo de objetivo"]')!
    await act(async () => { escribir(antes, 'ratio') })
    expect(document.querySelector('select[aria-label="Tipo de objetivo"]')).toBe(antes)
    expect(antes.value).toBe('ratio')
  })

  it('mientras guarda no se puede tocar el formulario', async () => {
    leer.mockResolvedValue({ fecha_alta: null, objetivos: [{ tipo: 'bonus', valor: 5000 }] })
    let terminar: (d: Awaited<ReturnType<typeof guardarObjetivos>>) => void = () => {}
    guardar.mockReturnValueOnce(new Promise(r => { terminar = r }))
    await montar(<ObjetivosCuenta visible ultimo={ultimo} ritmos={ritmos} />)
    await pulsar(await esperar(() => boton('Editar')))
    await pulsar(boton('Guardar')!)
    expect(document.querySelector<HTMLFieldSetElement>('fieldset')!.disabled).toBe(true)
    await act(async () => { terminar({ fecha_alta: null, objetivos: [{ tipo: 'bonus', valor: 5000 }] }) })
    expect(document.querySelector('fieldset')).toBeNull()
  })

  it('sin cifra actual la barra no anuncia un 0 %', async () => {
    leer.mockResolvedValue({ fecha_alta: null, objetivos: [{ tipo: 'bonus', valor: 5000 }] })
    await montar(<ObjetivosCuenta visible ultimo={{ ...ultimo, bonus: null }} ritmos={ritmos} />)
    await esperar(() => document.querySelector('[role="progressbar"]'))
    expect(document.querySelector('[role="progressbar"]')!.hasAttribute('aria-valuenow')).toBe(false)
  })

  it('si el redondeo iguala la cifra y la meta sin estar cumplida, enseña más decimales', async () => {
    leer.mockResolvedValue({ fecha_alta: null, objetivos: [{ tipo: 'ratio', valor: 1.234 }] })
    await montar(<ObjetivosCuenta visible ultimo={{ ...ultimo, ratio: 1.231 }} ritmos={ritmos} />)
    await esperar(() => document.querySelector('.est-objetivo-cifras'))
    expect(document.querySelector('.est-objetivo-cifras')!.textContent).toContain('1,23100 / 1,23400')
  })
})
