// @vitest-environment jsdom
/**
 * Identificar en TMDb (carpeta manual): abre con la búsqueda que el servidor saca del nombre del fichero, busca otra,
 * confirma un resultado, la sugerencia del escaneo o un id pegado (número o dirección de themoviedb.org), y los fallos
 * se dicen dentro del diálogo sin cerrarlo.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/library', () => ({ getTmdbCandidatos: vi.fn(), identificarTmdb: vi.fn() }))

import { getTmdbCandidatos, identificarTmdb, type CandidatosTmdb } from '../../api/library'
import type { LibraryItemResponse } from '../../types/api'
import { IdentificarTmdbModal } from '../IdentificarTmdbModal'
import { boton, desmontar, elegir, escribir, esperar, montar, pulsar, texto } from './dom'

const FILA = {
  id: 9, item_type: 'movie', title: 'dune part two', year: 2024, local_status: 'Disponible', instance_type: 'carpeta',
  por_identificar: true, sugerencia_tmdb: null,
} as LibraryItemResponse

const DEL_FICHERO: CandidatosTmdb = {
  tipo: 'movie', consulta: 'dune part two', anio: 2024,
  candidatos: [
    { tmdb_id: '693134', titulo: 'Dune: Parte dos', titulo_original: 'Dune: Part Two', anio: 2024, poster_url: 'https://image.tmdb.org/t/p/original/a.jpg', resumen: 'Paul Atreides se une a los Fremen.' },
    { tmdb_id: '438631', titulo: 'Dune', titulo_original: 'Dune', anio: 2021, poster_url: null, resumen: null },
  ],
}

const onDone = vi.fn()
const onClose = vi.fn()

// El control de un Campo: su etiqueta apunta a él con `for`.
function control<T extends HTMLElement>(etiqueta: string): T {
  const label = [...document.querySelectorAll('label')].find(l => l.textContent?.trim().startsWith(etiqueta))
  const el = label && document.getElementById(label.htmlFor)
  if (!el) throw new Error(`No hay campo «${etiqueta}»`)
  return el as T
}
const dialogo = () => document.querySelector('[role="dialog"]')!
const alerta = () => dialogo().querySelector('[role="alert"]')?.textContent ?? null
const esEsta = (titulo: string) => document.querySelector<HTMLButtonElement>(`button[aria-label^="Es esta: ${titulo}"]`)!

async function abrir(item: LibraryItemResponse = FILA) {
  await montar(<IdentificarTmdbModal item={item} onClose={onClose} onDone={onDone} />)
  await esperar(() => !texto().includes('Buscando en TMDb'))
}

beforeEach(() => {
  vi.mocked(getTmdbCandidatos).mockResolvedValue(structuredClone(DEL_FICHERO))
  vi.mocked(identificarTmdb).mockResolvedValue({ ok: true, message: 'Ficha confirmada: Dune: Parte dos (2024)', item_id: 9 })
})
afterEach(async () => {
  await desmontar()
  vi.clearAllMocks()
})

describe('Identificar en TMDb', () => {
  it('abre con la búsqueda del servidor: título, año y tipo rellenos, y sus resultados', async () => {
    vi.mocked(getTmdbCandidatos).mockResolvedValue({ ...structuredClone(DEL_FICHERO), tipo: 'tv', consulta: 'dune parte dos', anio: 2023 })
    await abrir()
    expect(getTmdbCandidatos).toHaveBeenCalledWith(9, undefined)
    expect(control<HTMLInputElement>('Título').value).toBe('dune parte dos')
    expect(control<HTMLInputElement>('Año').value).toBe('2023')
    expect(control<HTMLSelectElement>('Tipo').value).toBe('tv')
    const filas = [...dialogo().querySelectorAll('.bib-ident-cand')]
    expect(filas).toHaveLength(2)
    expect(filas[0].textContent).toContain('Dune: Parte dos (2024)')
    expect(filas[0].textContent).toContain('Dune: Part Two')
    expect(filas[0].textContent).toContain('Paul Atreides se une a los Fremen.')
    // El título original igual que el traducido no se repite.
    expect(filas[1].querySelector('.bib-ident-original')).toBeNull()
    expect(filas[0].querySelector('img')?.getAttribute('src')).toBe('https://image.tmdb.org/t/p/w154/a.jpg')
  })

  it('busca con lo escrito, el año y el tipo, y enseña lo nuevo', async () => {
    await abrir()
    vi.mocked(getTmdbCandidatos).mockResolvedValue({
      tipo: 'tv', consulta: 'dune prophecy', anio: null,
      candidatos: [{ tmdb_id: '90228', titulo: 'Dune: La profecía', titulo_original: 'Dune: Prophecy', anio: 2024, poster_url: null, resumen: null }],
    })
    await escribir(control('Título'), 'dune prophecy')
    await escribir(control('Año'), '')
    await elegir(control('Tipo'), 'tv')
    await pulsar(boton('Buscar'))
    await esperar(() => texto().includes('Dune: La profecía'))
    expect(getTmdbCandidatos).toHaveBeenLastCalledWith(9, { q: 'dune prophecy', anio: null, tipo: 'tv' })
    expect(dialogo().querySelectorAll('.bib-ident-cand')).toHaveLength(1)
  })

  it('un año que no son cuatro cifras se dice en su campo y no busca', async () => {
    await abrir()
    await escribir(control('Año'), '24')
    await pulsar(boton('Buscar'))
    expect(texto()).toContain('El año, con cuatro cifras (o vacío).')
    expect(getTmdbCandidatos).toHaveBeenCalledTimes(1)
  })

  it('«Es esta» confirma ese resultado con el tipo con el que se buscó y avisa a quien lo abrió', async () => {
    await abrir()
    // Cambiar el desplegable sin buscar no cambia el tipo de lo que ya está en la lista.
    await elegir(control('Tipo'), 'tv')
    await pulsar(esEsta('Dune: Parte dos'))
    await esperar(() => onDone.mock.calls.length > 0)
    expect(identificarTmdb).toHaveBeenCalledWith(9, '693134', 'movie')
    expect(onDone).toHaveBeenCalledWith('Ficha confirmada: Dune: Parte dos (2024)')
  })

  it('sin resultados lo dice y propone qué cambiar', async () => {
    vi.mocked(getTmdbCandidatos).mockResolvedValue({ tipo: 'movie', consulta: 'xyz', anio: 1999, candidatos: [] })
    await abrir()
    expect(texto()).toContain('TMDb no devuelve nada para «xyz» de 1999')
  })

  it('pegar la dirección de una ficha usa su id y su tipo; un número solo, el tipo del desplegable', async () => {
    await abrir()
    await escribir(control('Id o enlace de TMDb'), 'https://www.themoviedb.org/tv/1399-juego-de-tronos?language=es-ES')
    await pulsar(boton('Usar este id'))
    await esperar(() => onDone.mock.calls.length > 0)
    expect(identificarTmdb).toHaveBeenLastCalledWith(9, '1399', 'tv')

    await desmontar()
    onDone.mockClear()
    await abrir()
    await escribir(control('Id o enlace de TMDb'), ' 603 ')
    await pulsar(boton('Usar este id'))
    await esperar(() => onDone.mock.calls.length > 0)
    expect(identificarTmdb).toHaveBeenLastCalledWith(9, '603', 'movie')
  })

  it('lo que no es un id ni una ficha de TMDb se dice en el campo y no se envía', async () => {
    await abrir()
    await escribir(control('Id o enlace de TMDb'), 'https://www.imdb.com/title/tt0133093/')
    await pulsar(boton('Usar este id'))
    expect(alerta()).toContain('Pega el número o la dirección de la ficha')
    expect(identificarTmdb).not.toHaveBeenCalled()
  })

  it('si el servidor rechaza la ficha lo dice dentro del diálogo, que sigue abierto y se puede reintentar', async () => {
    vi.mocked(identificarTmdb).mockRejectedValueOnce(new Error('TMDb no responde: inténtalo en un rato'))
    await abrir()
    await pulsar(esEsta('Dune: Parte dos'))
    await esperar(() => alerta())
    expect(alerta()).toBe('TMDb no responde: inténtalo en un rato')
    expect(onDone).not.toHaveBeenCalled()
    expect(esEsta('Dune: Parte dos').disabled).toBe(false)
    await pulsar(esEsta('Dune: Parte dos'))
    await esperar(() => onDone.mock.calls.length > 0)
  })

  it('un fallo al buscar (sin clave de TMDb) también se dice dentro', async () => {
    vi.mocked(getTmdbCandidatos).mockRejectedValue(new Error('Configura la API key de TMDb en Configuración'))
    await abrir()
    expect(alerta()).toBe('Configura la API key de TMDb en Configuración')
  })

  it('con la sugerencia del escaneo, la pregunta y un clic la confirman', async () => {
    await abrir({
      ...FILA,
      sugerencia_tmdb: { tmdb_id: '693134', tipo: 'movie', titulo: 'Dune: Parte dos', anio: 2024, poster_url: null },
    })
    expect(dialogo().querySelector('.bib-ident-sugerencia')?.textContent).toContain('¿Es Dune: Parte dos (2024)?')
    // Es el único botón principal del diálogo.
    expect(dialogo().querySelectorAll('.btn-primary')).toHaveLength(1)
    await pulsar(boton('Sí, es esta'))
    await esperar(() => onDone.mock.calls.length > 0)
    expect(identificarTmdb).toHaveBeenCalledWith(9, '693134', 'movie')
  })

  it('en un capítulo avisa de que la ficha es la de la serie y busca con el id de la fila', async () => {
    await abrir({ ...FILA, id: 31, item_type: 'episode', title: 'severance 1x02' })
    expect(texto()).toContain('La ficha se aplica a toda la serie.')
    expect(getTmdbCandidatos).toHaveBeenCalledWith(31, undefined)
  })
})
