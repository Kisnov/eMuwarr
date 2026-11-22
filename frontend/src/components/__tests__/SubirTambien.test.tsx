// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/uploads', () => ({ getSubirTambien: vi.fn(), subirTambien: vi.fn() }))

import { getSubirTambien, subirTambien } from '../../api/uploads'
import type { UploadJob } from '../../types/api'
import { SubirTambienBloque, SubirTambienModal } from '../SubirTambien'
import { boton, desmontar, esperar, montar, pulsar, texto } from './dom'

const NOBS = { id: 'nobs', nombre: 'N.O.B.S' }
const MNV = { id: 'mnv', nombre: 'Milnueve' }
const job = (extra: Partial<UploadJob>) => ({ id: 7, status: 'completed', ...extra } as UploadJob)
const casilla = (nombre: string) => [...document.querySelectorAll('label')]
  .find(l => l.textContent?.includes(nombre))?.querySelector('input') ?? undefined

beforeEach(() => vi.clearAllMocks())
afterEach(desmontar)

describe('SubirTambienBloque', () => {
  it('sin trackers que la acepten no sale', async () => {
    await montar(<SubirTambienBloque job={job({ subir_tambien_a: [] })} />)
    expect(document.querySelector('[data-testid="subir-tambien"]')).toBeNull()
  })

  it('un botón por tracker que encola esa subida y dice el resultado', async () => {
    vi.mocked(subirTambien).mockResolvedValue({
      lote_id: null, encolados: 1, saltados: 0,
      resultados: [{ job_id: 7, tracker: 'nobs', status: 'queued', nuevo_job_id: 9, motivo: null }],
    })
    const encolado = vi.fn()
    await montar(<SubirTambienBloque job={job({ subir_tambien_a: [NOBS, MNV] })} onEncolado={encolado} />)
    expect(boton('Subir también a Milnueve')).toBeTruthy()

    await pulsar(boton('Subir también a N.O.B.S'))

    expect(subirTambien).toHaveBeenCalledWith([7], ['nobs'])
    await esperar(() => texto().includes('Subida #9 a N.O.B.S en cola.'))
    expect(encolado).toHaveBeenCalled()
  })

  it('el motivo por el que no se encoló (qBittorrent) se enseña', async () => {
    vi.mocked(subirTambien).mockResolvedValue({
      lote_id: null, encolados: 0, saltados: 1,
      resultados: [{ job_id: 7, tracker: 'nobs', status: 'blocked', nuevo_job_id: null,
        motivo: 'El torrent de «Peli.mkv» no está entero en qBittorrent (incompleto, 40 %).' }],
    })
    const encolado = vi.fn()
    await montar(<SubirTambienBloque job={job({ subir_tambien_a: [NOBS] })} onEncolado={encolado} />)
    await pulsar(boton('Subir también a N.O.B.S'))
    await esperar(() => texto().includes('no está entero en qBittorrent'))
    expect(encolado).not.toHaveBeenCalled()
  })
})

describe('SubirTambienModal', () => {
  it('solo lo elegido a mano: una petición con las subidas y los trackers marcados', async () => {
    vi.mocked(getSubirTambien).mockResolvedValue({
      trackers: [NOBS, MNV],
      subidas: [
        { job_id: 1, title: 'Peli A', nombre: 'A.mkv', result_url: null, finished_at: null, trackers: [NOBS, MNV] },
        { job_id: 2, title: 'Peli B', nombre: 'B.mkv', result_url: null, finished_at: null, trackers: [MNV] },
        { job_id: 3, title: 'Peli C', nombre: 'C.mkv', result_url: null, finished_at: null, trackers: [NOBS] },
      ],
    })
    vi.mocked(subirTambien).mockResolvedValue({ lote_id: '0123456789ab', encolados: 3, saltados: 1, resultados: [] })
    const encolado = vi.fn()
    await montar(<SubirTambienModal onClose={() => {}} onEncolado={encolado} />)
    await esperar(() => texto().includes('Peli B'))
    expect(boton('Subir')?.getAttribute('aria-disabled')).toBe('true')

    await pulsar(casilla('Peli A'))
    await pulsar(casilla('Peli B'))
    await pulsar(boton('Subir 2 subidas'))

    expect(subirTambien).toHaveBeenCalledWith([1, 2], ['nobs', 'mnv'])
    await esperar(() => encolado.mock.calls.length > 0)
    expect(encolado).toHaveBeenCalledWith('0123456789ab')
  })

  it('sin la clave de otro tracker lo dice', async () => {
    vi.mocked(getSubirTambien).mockResolvedValue({ trackers: [], subidas: [] })
    await montar(<SubirTambienModal onClose={() => {}} onEncolado={() => {}} />)
    await esperar(() => texto().includes('Otros trackers'))
    expect(subirTambien).not.toHaveBeenCalled()
  })
})
