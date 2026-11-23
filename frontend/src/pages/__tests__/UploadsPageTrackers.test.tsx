// @vitest-environment jsdom
/**
 * Subidas con el mismo elemento subido a varios trackers: una fila con el título y, debajo, una por cada otro tracker
 * con su estado y sus acciones. Lo de un solo tracker se ve como siempre; la cadena de reintentos va bajo su tracker.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/uploads', () => ({
  getUploads: vi.fn(), countUploads: vi.fn(), getUploadJob: vi.fn(), startUploadJob: vi.fn(),
  retryUploadJobQueued: vi.fn(), cancelUploadJob: vi.fn(), getUploadJobLogs: vi.fn(), deleteUploadJob: vi.fn(),
  getLote: vi.fn(), cancelarLote: vi.fn(), reintentarLote: vi.fn(), borrarLote: vi.fn(),
  contarSinTrailer: vi.fn(async () => 0), marcarTrailerAMano: vi.fn(), fetchTrackersParaSubir: vi.fn(async () => []),
}))
vi.mock('../../api/instances', () => ({ getInstances: vi.fn(async () => []) }))
vi.mock('../../api/seeding', () => ({ getSeeding: vi.fn(async () => null) }))
vi.mock('../../api/eventos', () => ({
  suscribirse: vi.fn(() => () => {}), canalVivo: vi.fn(() => true), observarCanal: vi.fn(() => () => {}),
}))

import { countUploads, getUploads } from '../../api/uploads'
import { SeccionVisible } from '../../context/SeccionVisible'
import { SyncContext } from '../../context/SyncContext'
import type { UploadJob } from '../../types/api'
import { desmontar, elegir, escribir, esperar, montar, pulsar, texto } from '../../components/__tests__/dom'
import { UploadsPage } from '../UploadsPage'

const trabajo = (extra: Partial<UploadJob>) =>
  ({ status: 'completed', media_type: 'movie', tracker: 'emu', tracker_nombre: 'eMuwarez', ...extra } as UploadJob)

const NOBS = { tracker: 'nobs', tracker_nombre: 'N.O.B.S' }
const MIL = { tracker: 'milnueve', tracker_nombre: 'Milnueve' }
const IMITATION = { library_item_id: 10, title: 'The Imitation Game' }
const SHUTTER = { library_item_id: 20, title: 'Shutter Island' }

async function pagina(jobs: UploadJob[]) {
  vi.mocked(getUploads).mockResolvedValue(jobs)
  vi.mocked(countUploads).mockResolvedValue(jobs.length)
  await montar(
    <SyncContext.Provider value={{ lastInvalidatedAt: 0, startAllSync: vi.fn(), isAnySyncing: false, todoPendiente: false } as never}>
      <SeccionVisible visible><UploadsPage onNavigate={vi.fn()} /></SeccionVisible>
    </SyncContext.Provider>,
  )
  await esperar(() => document.querySelectorAll('tbody tr').length > 0)
}

// Cada fila: el título (o el tracker, en las de debajo) y si es la de otro tracker.
const filas = () => [...document.querySelectorAll('tbody tr')].map(tr =>
  `${tr.classList.contains('sub-fila-tracker') ? '  ' : ''}${tr.querySelector('.sub-titulo')?.textContent}`)
const subtitulo = () => document.querySelector('.page-subtitle')?.textContent

afterEach(async () => {
  await desmontar()
  vi.clearAllMocks()
})

describe('el mismo elemento en varios trackers', () => {
  it('va en una sola fila con el título y, debajo, la del otro tracker con su estado y sus acciones', async () => {
    await pagina([
      trabajo({ id: 2, ...IMITATION, ...MIL, status: 'failed', created_at: '2026-11-20T10:01:00', result_url: null }),
      trabajo({ id: 1, ...IMITATION, ...NOBS, created_at: '2026-11-20T10:00:00', result_url: 'https://nobs.example/t/1' }),
    ])
    expect(filas()).toEqual(['The Imitation Game', '  N.O.B.S'])
    const [primera, otra] = document.querySelectorAll('tbody tr')
    // La primera lleva su tracker aunque no sea eMuwarez de más; la de debajo no repite póster ni tipo.
    expect(primera.querySelector('.sub-chips .sub-tracker-chip')?.textContent).toBe('Milnueve')
    expect(otra.querySelector('.sub-poster')).toBeNull()
    expect(otra.querySelector('.sub-col-tipo')).toBeNull()
    expect(primera.querySelector('.sub-estado .badge')?.textContent).toBe('Fallido')
    expect(otra.querySelector('.sub-estado .badge')?.textContent).toBe('Completado')
    expect(primera.querySelector('button[aria-label="Reintentar"]')).not.toBeNull()
    expect(otra.querySelector('a[aria-label="Ver en N.O.B.S"]')).not.toBeNull()
    expect(otra.querySelector('a.sub-titulo')?.getAttribute('href')).toBe('/uploads/1')
    expect(subtitulo()).toBe('2 subidas de 1 elemento')
  })

  it('con eMuwarez en el grupo, su chip también sale', async () => {
    await pagina([
      trabajo({ id: 2, ...IMITATION, created_at: '2026-11-20T10:01:00' }),
      trabajo({ id: 1, ...IMITATION, ...NOBS, created_at: '2026-11-20T10:00:00' }),
    ])
    expect(document.querySelector('tbody tr .sub-chips .sub-tracker-chip')?.textContent).toBe('eMuwarez')
  })

  it('una sola subida se ve como siempre: sin fila de debajo ni chip de eMuwarez', async () => {
    await pagina([
      trabajo({ id: 1, ...IMITATION, created_at: '2026-11-20T10:00:00' }),
      trabajo({ id: 2, ...SHUTTER, ...NOBS, created_at: '2026-11-20T09:00:00' }),
      // Sin elemento (una antigua): nunca se junta con otra.
      trabajo({ id: 3, title: 'Suelta', created_at: '2026-11-20T08:00:00' }),
      trabajo({ id: 4, title: 'Suelta', ...NOBS, created_at: '2026-11-20T07:00:00' }),
    ])
    expect(filas()).toEqual(['The Imitation Game', 'Shutter Island', 'Suelta', 'Suelta'])
    expect(document.querySelectorAll('.sub-fila-tracker')).toHaveLength(0)
    const chips = [...document.querySelectorAll('.sub-tracker-chip')].map(c => c.textContent)
    expect(chips).toEqual(['N.O.B.S', 'N.O.B.S'])
    expect(subtitulo()).toBe('4 subidas')
  })

  it('la cadena de reintentos de un tracker se despliega bajo su fila', async () => {
    await pagina([
      trabajo({ id: 3, ...IMITATION, ...NOBS, status: 'failed', retry_of_job_id: 1, retry_count: 1, created_at: '2026-11-20T10:05:00' }),
      trabajo({ id: 2, ...IMITATION, ...MIL, created_at: '2026-11-20T10:01:00' }),
      trabajo({ id: 1, ...IMITATION, ...NOBS, status: 'failed', created_at: '2026-11-20T10:00:00' }),
    ])
    expect(filas()).toEqual(['The Imitation Game', '  Milnueve'])
    await pulsar(document.querySelector('.sub-toggle-reintentos')!)
    const trs = [...document.querySelectorAll('tbody tr')]
    expect(trs).toHaveLength(3)
    expect(trs[1].classList.contains('sub-fila-antigua')).toBe(true)
    expect(trs[1].querySelector('a.sub-titulo')?.getAttribute('href')).toBe('/uploads/1')
    expect(trs[2].classList.contains('sub-fila-tracker')).toBe(true)
    expect(subtitulo()).toBe('2 subidas de 1 elemento')
  })

  it('un grupo se ordena por su subida más reciente', async () => {
    await pagina([
      trabajo({ id: 4, ...IMITATION, ...NOBS, created_at: '2026-11-20T12:00:00' }),
      trabajo({ id: 3, ...SHUTTER, created_at: '2026-11-20T11:00:00' }),
      trabajo({ id: 2, ...SHUTTER, ...NOBS, created_at: '2026-11-20T10:00:00' }),
      trabajo({ id: 1, ...IMITATION, created_at: '2026-11-20T09:00:00' }),
    ])
    expect(filas()).toEqual(['The Imitation Game', '  eMuwarez', 'Shutter Island', '  N.O.B.S'])
    await elegir(document.querySelector<HTMLSelectElement>('select[aria-label="Ordenar subidas"]')!, 'created_asc')
    expect(filas()).toEqual(['Shutter Island', '  N.O.B.S', 'The Imitation Game', '  eMuwarez'])
    await elegir(document.querySelector<HTMLSelectElement>('select[aria-label="Ordenar subidas"]')!, 'title')
    expect(filas()).toEqual(['Shutter Island', '  N.O.B.S', 'The Imitation Game', '  eMuwarez'])
  })

  it('la búsqueda deja el grupo entero', async () => {
    await pagina([
      trabajo({ id: 3, ...SHUTTER, created_at: '2026-11-20T11:00:00' }),
      trabajo({ id: 2, ...IMITATION, ...MIL, created_at: '2026-11-20T10:00:00' }),
      trabajo({ id: 1, ...IMITATION, ...NOBS, created_at: '2026-11-20T09:00:00' }),
    ])
    await escribir(document.querySelector<HTMLInputElement>('input[aria-label="Buscar subidas por título"]')!, 'imitation')
    expect(filas()).toEqual(['The Imitation Game', '  N.O.B.S'])
    expect(texto()).toContain('2 subidas de 1 elemento')
  })
})
