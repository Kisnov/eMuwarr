// Objetivos de tu cuenta de eMuwarez: cuánto te falta y cuándo llegas al ritmo de la última semana. Sin React.
import type { PuntoCuenta, RitmosCuenta } from '../types/api'

export type TipoObjetivo = 'subido' | 'ratio' | 'bonus' | 'seeding' | 'antiguedad'

export interface Objetivo {
  tipo: TipoObjetivo
  valor: number   // subido en TB, antigüedad en meses, el resto en su cifra
}

export interface ObjetivosCuenta {
  fecha_alta: string | null   // YYYY-MM-DD
  objetivos: Objetivo[]
}

export const TIPOS: Record<TipoObjetivo, { nombre: string; unidad: string }> = {
  subido: { nombre: 'Subido', unidad: 'TB' },
  ratio: { nombre: 'Ratio', unidad: '' },
  bonus: { nombre: 'Bonus', unidad: 'puntos' },
  seeding: { nombre: 'Seeding', unidad: 'torrents' },
  antiguedad: { nombre: 'Antigüedad', unidad: 'meses' },
}

const TB = 1024 ** 4
const DIA_MS = 86_400_000
const DIAS_MES = 365.25 / 12

function diaLocal(fecha: string): Date {
  const [a, m, d] = fecha.split('-').map(Number)
  return new Date(a, m - 1, d)
}

/** Meses (con decimales) desde la fecha de alta hasta `hoy`. */
export function mesesDesde(fecha: string, hoy: Date): number {
  return (hoy.getTime() - diaLocal(fecha).getTime()) / DIA_MS / DIAS_MES
}

/** La antigüedad como la escribe un tracker: «1 año, 2 meses y 3 semanas». */
export function antiguedadTexto(fecha: string, hoy: Date): string {
  const alta = diaLocal(fecha)
  let meses = (hoy.getFullYear() - alta.getFullYear()) * 12 + hoy.getMonth() - alta.getMonth()
  if (hoy.getDate() < alta.getDate()) meses--
  const desde = new Date(alta.getFullYear(), alta.getMonth() + meses, alta.getDate())
  const semanas = Math.floor((hoy.getTime() - desde.getTime()) / DIA_MS / 7)
  const partes: string[] = []
  const anos = Math.floor(meses / 12)
  if (anos) partes.push(`${anos} ${anos === 1 ? 'año' : 'años'}`)
  if (meses % 12) partes.push(`${meses % 12} ${meses % 12 === 1 ? 'mes' : 'meses'}`)
  if (semanas) partes.push(`${semanas} ${semanas === 1 ? 'semana' : 'semanas'}`)
  if (partes.length === 0) return 'menos de una semana'
  return partes.length === 1 ? partes[0] : `${partes.slice(0, -1).join(', ')} y ${partes[partes.length - 1]}`
}

export interface Progreso {
  actual: number | null       // en la unidad del objetivo
  fraccion: number | null     // 0..1
  cumplido: boolean
  dias: number | null         // días que faltan al ritmo actual; null si no se sabe (o si ya está cumplido)
}

/** Dónde estás respecto a un objetivo y cuántos días faltan al ritmo de la última semana. */
export function progreso(o: Objetivo, ultimo: PuntoCuenta | undefined, r: RitmosCuenta | null,
  fechaAlta: string | null, hoy: Date): Progreso {
  let actual: number | null = null
  let dias: number | null = null
  const p = ultimo
  switch (o.tipo) {
    case 'subido':
      if (p?.subido_bytes != null) {
        actual = p.subido_bytes / TB
        if (r?.subido_dia) dias = (o.valor * TB - p.subido_bytes) / r.subido_dia
      }
      break
    case 'bonus':
      if (p?.bonus != null) {
        actual = p.bonus
        if (r?.bonus_hora) dias = (o.valor - p.bonus) / (r.bonus_hora * 24)
      }
      break
    case 'seeding':
      actual = p?.seeding ?? null
      break
    case 'ratio':
      actual = p?.ratio ?? null
      // (S + s·d) / (D + b·d) = R  →  d = (R·D − S) / (s − R·b), si sale positivo.
      if (p?.subido_bytes != null && p.bajado_bytes != null && r?.subido_dia != null && r.bajado_dia != null) {
        const divisor = r.subido_dia - o.valor * r.bajado_dia
        const d = (o.valor * p.bajado_bytes - p.subido_bytes) / divisor
        if (divisor > 0 && d > 0) dias = d
      }
      break
    case 'antiguedad':
      if (fechaAlta) {
        actual = mesesDesde(fechaAlta, hoy)
        dias = (o.valor - actual) * DIAS_MES
      }
      break
  }
  const cumplido = actual !== null && actual >= o.valor
  return {
    actual,
    fraccion: actual === null ? null : Math.max(0, Math.min(1, actual / o.valor)),
    cumplido,
    dias: cumplido || dias === null || !Number.isFinite(dias) || dias <= 0 ? null : dias,
  }
}

/** «≈ 12 días», «≈ 3 meses», «≈ 2 años». */
export function textoPlazo(dias: number): string {
  if (dias < 60) {
    const n = Math.max(1, Math.round(dias))
    return `≈ ${n} ${n === 1 ? 'día' : 'días'}`
  }
  if (dias < 730) return `≈ ${Math.round(dias / DIAS_MES)} meses`
  return `≈ ${formatAnos(dias / 365.25)} años`
}

const formatAnos = (n: number) => n.toLocaleString('es-ES', { maximumFractionDigits: 1 })
