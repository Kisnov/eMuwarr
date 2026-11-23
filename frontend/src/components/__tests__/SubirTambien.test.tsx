// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/uploads', () => ({ getSubirTambien: vi.fn(), subirTambien: vi.fn() }))

import { getSubirTambien, subirTambien } from '../../api/uploads'
import type { UploadJob } from '../../types/api'
import { MAX_ELEMENTOS_LOTE } from '../../utils/lote'
import { SubirTambienBloque, SubirTambienModal } from '../SubirTambien'
import { boton, desmontar, esperar, montar, pulsar, texto } from './dom'

const EMU = { id: 'emu', nombre: 'eMuwarez' }
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

  it('de otro tracker a eMuwarez: la ayuda nombra el tracker de origen', async () => {
    await montar(<SubirTambienBloque job={job({ tracker: 'nobs', tracker_nombre: 'N.O.B.S', subir_tambien_a: [EMU] })} />)
    expect(texto()).toContain('las mismas piezas que en N.O.B.S')
    expect(boton('Subir también a eMuwarez')).toBeTruthy()
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
      trackers: [EMU, NOBS, MNV],
      subidas: [
        { job_id: 1, title: 'Peli A', nombre: 'A.mkv', result_url: null, finished_at: null, origen: EMU, trackers: [NOBS, MNV] },
        { job_id: 2, title: 'Peli B', nombre: 'B.mkv', result_url: null, finished_at: null, origen: NOBS, trackers: [EMU, MNV] },
        { job_id: 3, title: 'Peli C', nombre: 'C.mkv', result_url: null, finished_at: null, origen: EMU, trackers: [NOBS] },
      ],
    })
    vi.mocked(subirTambien).mockResolvedValue({ lote_id: '0123456789ab', encolados: 3, saltados: 1, resultados: [] })
    const encolado = vi.fn()
    await montar(<SubirTambienModal onClose={() => {}} onEncolado={encolado} />)
    await esperar(() => texto().includes('Peli B'))
    expect(texto()).toContain('en N.O.B.S · falta en eMuwarez, Milnueve')
    expect(boton('Subir')?.getAttribute('aria-disabled')).toBe('true')

    await pulsar(casilla('Peli A'))
    await pulsar(casilla('Peli B'))
    await pulsar(boton('Subir 2 subidas'))

    expect(subirTambien).toHaveBeenCalledWith([1, 2], ['emu', 'nobs', 'mnv'])
    await esperar(() => encolado.mock.calls.length > 0)
    expect(encolado).toHaveBeenCalledWith('0123456789ab')
  })

  it('no deja marcar más subidas que un lote, enseña el tope y deja desmarcar', async () => {
    const subidas = Array.from({ length: MAX_ELEMENTOS_LOTE + 1 }, (_, i) => (
      { job_id: i + 1, title: `Peli ${i + 1}.`, nombre: `${i + 1}.mkv`, result_url: null, finished_at: null, origen: EMU,
        trackers: [NOBS] }))
    vi.mocked(getSubirTambien).mockResolvedValue({ trackers: [EMU, NOBS], subidas })
    await montar(<SubirTambienModal onClose={() => {}} onEncolado={() => {}} />)
    await esperar(() => texto().includes('Peli 1.'))
    expect(texto()).toContain(`máximo ${MAX_ELEMENTOS_LOTE} a la vez`)
    for (let i = 1; i <= MAX_ELEMENTOS_LOTE; i++) await pulsar(casilla(`Peli ${i}.`))
    const ultima = casilla(`Peli ${MAX_ELEMENTOS_LOTE + 1}.`)!
    expect(ultima.disabled).toBe(true)
    expect(casilla('Peli 1.')!.disabled).toBe(false)  // las marcadas se pueden desmarcar
    await pulsar(casilla('Peli 1.'))
    expect(ultima.disabled).toBe(false)
    await pulsar(ultima)
    await pulsar(boton(`Subir ${MAX_ELEMENTOS_LOTE} subidas`))
    expect(vi.mocked(subirTambien).mock.lastCall?.[0]).toHaveLength(MAX_ELEMENTOS_LOTE)
  })

  it('sin la clave de otro tracker lo dice', async () => {
    vi.mocked(getSubirTambien).mockResolvedValue({ trackers: [], subidas: [] })
    await montar(<SubirTambienModal onClose={() => {}} onEncolado={() => {}} />)
    await esperar(() => texto().includes('Otros trackers'))
    expect(subirTambien).not.toHaveBeenCalled()
  })
})
