/**
 * Objetivos de la cuenta: progreso, cuánto falta al ritmo de la semana y la antigüedad en palabras.
 */
import { describe, expect, it } from 'vitest'
import type { PuntoCuenta, RitmosCuenta } from '../../types/api'
import { antiguedadTexto, mesesDesde, progreso, textoPlazo } from '../objetivos'

const TB = 1024 ** 4
const GB = 1024 ** 3
const HOY = new Date(2026, 10, 18)

const punto: PuntoCuenta = {
  dia: '2026-11-18', ratio: 20, subido_bytes: 100 * TB, bajado_bytes: 5 * TB, bonus: 1000, buffer_bytes: 95 * TB,
  seeding: 770, leeching: 0, hit_and_runs: 2, grupo: 'InsaneUser',
}
const ritmos: RitmosCuenta = { dias: 7, subido_dia: 0.5 * TB, bajado_dia: 15 * GB, bonus_hora: 10 }

describe('progreso', () => {
  it('subido: fracción y días al ritmo de la semana', () => {
    const p = progreso({ tipo: 'subido', valor: 150 }, punto, ritmos, null, HOY)
    expect(p.actual).toBe(100)
    expect(p.fraccion).toBeCloseTo(2 / 3)
    expect(p.dias).toBe(100)   // 50 TB a 0,5 TB/día
    expect(p.cumplido).toBe(false)
  })

  it('bonus: por hora pasa a días', () => {
    expect(progreso({ tipo: 'bonus', valor: 1240 }, punto, ritmos, null, HOY).dias).toBe(1)
  })

  it('ratio: despeja los días; si no llega nunca a este ritmo, no hay fecha', () => {
    // (100 + 0,5·d) TB / (5 TB + 15 GB·d) = 21
    const d = progreso({ tipo: 'ratio', valor: 21 }, punto, ritmos, null, HOY).dias!
    expect((100 * TB + 0.5 * TB * d) / (5 * TB + 15 * GB * d)).toBeCloseTo(21)
    expect(progreso({ tipo: 'ratio', valor: 40 }, punto, ritmos, null, HOY).dias).toBeNull()
  })

  it('lo cumplido no tiene plazo y la barra no pasa del 100 %', () => {
    const p = progreso({ tipo: 'seeding', valor: 500 }, punto, ritmos, null, HOY)
    expect(p).toEqual({ actual: 770, fraccion: 1, cumplido: true, dias: null })
  })

  it('sin ritmos o sin instantánea no inventa nada', () => {
    expect(progreso({ tipo: 'subido', valor: 150 }, punto, null, null, HOY).dias).toBeNull()
    expect(progreso({ tipo: 'subido', valor: 150 }, undefined, ritmos, null, HOY))
      .toEqual({ actual: null, fraccion: null, cumplido: false, dias: null })
  })

  it('antigüedad: sale de la fecha de alta, sin ella no se sabe', () => {
    const p = progreso({ tipo: 'antiguedad', valor: 12 }, punto, ritmos, '2026-02-18', HOY)
    expect(p.actual).toBeCloseTo(9, 0)
    expect(Math.round(p.dias! / 30)).toBe(3)
    expect(progreso({ tipo: 'antiguedad', valor: 12 }, punto, ritmos, null, HOY).actual).toBeNull()
  })
})

describe('antigüedad en palabras', () => {
  it.each([
    ['2026-02-20', '8 meses y 4 semanas'],
    ['2026-02-25', '8 meses y 3 semanas'],
    ['2025-11-18', '1 año'],
    ['2024-09-01', '2 años, 2 meses y 2 semanas'],
    ['2026-11-15', 'menos de una semana'],
    ['2026-11-04', '2 semanas'],
  ])('%s → %s', (alta, texto) => {
    expect(antiguedadTexto(alta, HOY)).toBe(texto)
  })

  it('mesesDesde cuenta meses con decimales', () => {
    expect(mesesDesde('2025-11-18', HOY)).toBeCloseTo(12, 1)
  })
})

describe('textoPlazo', () => {
  it.each([[0.4, '≈ 1 día'], [12, '≈ 12 días'], [100, '≈ 3 meses'], [1000, '≈ 2,7 años']])('%s días → %s', (d, t) => {
    expect(textoPlazo(d)).toBe(t)
  })
})
