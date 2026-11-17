import { api } from './client'
import type { EstadisticasCuenta, MisTorrents } from '../types/api'

export async function getEstadisticasCuenta(dias: number): Promise<EstadisticasCuenta> {
  return api.get<EstadisticasCuenta>(`/estadisticas/cuenta?dias=${dias}`)
}

export async function getMisSubidas(): Promise<MisTorrents> {
  return api.get<MisTorrents>('/estadisticas/mis-subidas')
}
