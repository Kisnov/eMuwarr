import { api } from './client'
import type { EstadisticasCuenta, MisTorrents } from '../types/api'
import type { ObjetivosCuenta } from '../utils/objetivos'

export async function getEstadisticasCuenta(dias: number): Promise<EstadisticasCuenta> {
  return api.get<EstadisticasCuenta>(`/estadisticas/cuenta?dias=${dias}`)
}

export async function getObjetivos(): Promise<ObjetivosCuenta> {
  return api.get<ObjetivosCuenta>('/estadisticas/objetivos')
}

export async function guardarObjetivos(datos: ObjetivosCuenta): Promise<ObjetivosCuenta> {
  return api.put<ObjetivosCuenta>('/estadisticas/objetivos', datos)
}

export async function getMisSubidas(): Promise<MisTorrents> {
  return api.get<MisTorrents>('/estadisticas/mis-subidas')
}
