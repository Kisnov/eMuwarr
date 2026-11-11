import { api } from './client'

export interface LogEntry {
  /** Número de orden creciente: lo que se manda como `since` para pedir solo lo que entró después. */
  seq?: number
  ts: number
  time: string
  level: string
  logger: string
  message: string
}

export interface LogsResponse {
  items: LogEntry[]
  count: number
  /** Solo con `since`: había más de `limit` líneas nuevas, así que falta un tramo y hay que leer la lista entera. */
  truncated?: boolean
}

export function getLogs(params: { limit?: number; level?: string; search?: string; since?: number } = {}): Promise<LogsResponse> {
  const qs = new URLSearchParams()
  if (params.limit) qs.set('limit', String(params.limit))
  if (params.level) qs.set('level', params.level)
  if (params.search) qs.set('search', params.search)
  if (params.since !== undefined) qs.set('since', String(params.since))
  const q = qs.toString()
  return api.get<LogsResponse>(`/logs${q ? `?${q}` : ''}`)
}
