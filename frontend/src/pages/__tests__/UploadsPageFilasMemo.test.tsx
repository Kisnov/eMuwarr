// @vitest-environment jsdom
/**
 * Subidas con progreso real: cuando cambia lo que lleva una subida, solo se repinta su fila. Antes `JobRow` no estaba
 * memorizado y cada aviso de progreso (dos por segundo) repintaba la tabla entera. Se cuentan los pintados de la
 * celda «Petición» de cada fila (la sustituye un contador): solo se pinta cuando se pinta la fila que la contiene.
 */
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/uploads', () => ({
  getUploads: vi.fn(), countUploads: vi.fn(), getUploadJob: vi.fn(), startUploadJob: vi.fn(),
  retryUploadJobQueued: vi.fn(), cancelUploadJob: vi.fn(), getUploadJobLogs: vi.fn(), deleteUploadJob: vi.fn(),
  getLote: vi.fn(), cancelarLote: vi.fn(), reintentarLote: vi.fn(), borrarLote: vi.fn(),
  contarSinTrailer: vi.fn(), marcarTrailerAMano: vi.fn(), fetchTrackersParaSubir: vi.fn(async () => []),
}))
vi.mock('../../api/instances', () => ({ getInstances: vi.fn(async () => []) }))
vi.mock('../../api/seeding', () => ({ getSeeding: vi.fn(async () => null) }))
vi.mock('../../api/eventos', () => ({
  suscribirse: vi.fn(() => () => {}), canalVivo: vi.fn(() => true), observarCanal: vi.fn(() => () => {}),
}))

const pintados = new Map<number, number>()
vi.mock('../../components/PeticionDeSubida', () => ({
  PeticionDeSubida: ({ job }: { job: { id: number } }) => {
    pintados.set(job.id, (pintados.get(job.id) ?? 0) + 1)
    return null
  },
}))

import { suscribirse } from '../../api/eventos'
import { contarSinTrailer, countUploads, getUploads } from '../../api/uploads'
import { SeccionVisible } from '../../context/SeccionVisible'
import { SyncContext } from '../../context/SyncContext'
import type { UploadJob } from '../../types/api'
import { desmontar, esperar, montar } from '../../components/__tests__/dom'
import { UploadsPage } from '../UploadsPage'

const trabajo = (extra: Partial<UploadJob>) =>
  ({ id: 1, status: 'completed', media_type: 'movie', created_at: '2026-11-04T10:00:00', ...extra } as UploadJob)
const viva = (id: number, pct: number) =>
  trabajo({ id, status: 'running', title: `Viva ${id}`, progress: { phases: [], global_pct: pct } as never })

// El estado «en el servidor»: lo que devuelven la lista completa y la de las vivas.
let porcentajes: Record<number, number>
const todas = () => [viva(1, porcentajes[1]), viva(2, porcentajes[2]), trabajo({ id: 3, title: 'Terminada' })]

async function pagina() {
  await montar(
    <SyncContext.Provider value={{ lastInvalidatedAt: 0, startAllSync: vi.fn(), isAnySyncing: false, todoPendiente: false } as never}>
      <SeccionVisible visible><UploadsPage onNavigate={vi.fn()} /></SeccionVisible>
    </SyncContext.Provider>,
  )
  await esperar(() => document.querySelectorAll('tbody tr').length === 3)
}

// Lo que llega a cada aviso de «subida-progreso» o «subida-estado»: la recarga se aglutina 400 ms.
const avisar = async (tipo: string) => {
  vi.useFakeTimers()
  try {
    await act(async () => {
      for (const [t, cb] of vi.mocked(suscribirse).mock.calls) if (t === tipo) (cb as (a: unknown) => void)({ tipo })
      await vi.advanceTimersByTimeAsync(500)
    })
  } finally { vi.useRealTimers() }
}

const barra = (id: number) =>
  document.querySelectorAll('tbody tr')[id - 1].querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')

beforeEach(() => {
  pintados.clear()
  porcentajes = { 1: 10, 2: 10 }
  vi.mocked(getUploads).mockImplementation(async q => (q?.status ? [viva(1, porcentajes[1]), viva(2, porcentajes[2])] : todas()))
  vi.mocked(countUploads).mockImplementation(async () => 3)
  vi.mocked(contarSinTrailer).mockResolvedValue(0)
})
afterEach(async () => {
  await desmontar()
  vi.clearAllMocks()
})

describe('las filas de Subidas', () => {
  it('el progreso de una subida no repinta las demás filas', async () => {
    await pagina()
    expect(barra(1)).toBe('10')
    const antes = new Map(pintados)

    porcentajes[1] = 60
    await avisar('subida-progreso')
    await esperar(() => barra(1) === '60')

    expect(pintados.get(1)).toBeGreaterThan(antes.get(1)!)
    expect(pintados.get(2)).toBe(antes.get(2))
    expect(pintados.get(3)).toBe(antes.get(3))
  })

  it('una recarga completa con los mismos datos no repinta ninguna fila', async () => {
    await pagina()
    const antes = new Map(pintados)
    vi.mocked(getUploads).mockClear()

    await avisar('subida-estado')
    await esperar(() => vi.mocked(getUploads).mock.calls.length > 0)
    await act(async () => { await Promise.resolve() })

    expect(Object.fromEntries(pintados)).toEqual(Object.fromEntries(antes))
  })

  it('una recarga completa en la que cambia una sola subida solo repinta esa fila', async () => {
    await pagina()
    const antes = new Map(pintados)

    porcentajes[2] = 90
    await avisar('subida-estado')
    await esperar(() => barra(2) === '90')

    expect(pintados.get(2)).toBeGreaterThan(antes.get(2)!)
    expect(pintados.get(1)).toBe(antes.get(1))
    expect(pintados.get(3)).toBe(antes.get(3))
  })
})
