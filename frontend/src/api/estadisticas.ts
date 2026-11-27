import { api } from './client'
import type { EstadisticasCuenta, MisTorrents } from '../types/api'
import type { ObjetivosCuenta } from '../utils/objetivos'

export async function getEstadisticasCuenta(dias: number, tracker = 'emu'): Promise<EstadisticasCuenta> {
  return api.get<EstadisticasCuenta>(`/estadisticas/cuenta?dias=${dias}&tracker=${tracker}`)
}

export async function getObjetivos(): Promise<ObjetivosCuenta> {
  return api.get<ObjetivosCuenta>('/estadisticas/objetivos')
}

export async function guardarObjetivos(datos: ObjetivosCuenta): Promise<ObjetivosCuenta> {
  return api.put<ObjetivosCuenta>('/estadisticas/objetivos', datos)
}

export async function getMisSubidas(tracker = 'emu'): Promise<MisTorrents> {
  return api.get<MisTorrents>(`/estadisticas/mis-subidas?tracker=${tracker}`)
}
