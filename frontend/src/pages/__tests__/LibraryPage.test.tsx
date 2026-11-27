// @vitest-environment jsdom
/**
 * Biblioteca y «Subir por lotes»: el botón «Seleccionar» pone casillas en películas y series, lo marcado sobrevive a
 * filtrar, a aplicar una vista guardada y a cambiar de vista, la barra dice cuántas esconde el filtro, «Subir por
 * lotes» abre el modal con lo marcado, y salir de la sección lo limpia todo.
 */
import { useState } from 'react'
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/library', () => ({
  getLibrary: vi.fn(), getLibraryFilterOptions: vi.fn(async () => ({})), getLibraryItem: vi.fn(async () => ({})),
  TOP_LEVEL_PARENT_FILTER: 0, identificarSugeridas: vi.fn(), getTmdbCandidatos: vi.fn(), identificarTmdb: vi.fn(),
  getEmuComparison: vi.fn(() => new Promise(() => {})),
}))
vi.mock('../../api/instances', () => ({ getInstances: vi.fn(async () => []) }))
vi.mock('../../api/settings', () => ({ fetchSettings: vi.fn(async () => ({ upload_max_concurrent: 1 })) }))
vi.mock('../../api/uploads', async original => ({
  ...await original<typeof import('../../api/uploads')>(),
  fetchTrackersParaSubir: vi.fn(async () => [{ id: 'emu', nombre: 'eMuwarez' }]),
}))

import { getLibrary, getLibraryFilterOptions, getLibraryItem, getTmdbCandidatos, identificarSugeridas, identificarTmdb } from '../../api/library'
import { fetchTrackersParaSubir } from '../../api/uploads'
import { SeccionVisible } from '../../context/SeccionVisible'
import { SyncContext } from '../../context/SyncContext'
import type { LibraryItemResponse } from '../../types/api'
import { boton, desmontar, esperar, montar, pulsar, texto } from '../../components/__tests__/dom'
import { LibraryPage } from '../LibraryPage'

const item = (id: number, title: string, extra: Partial<LibraryItemResponse> = {}) => ({
  id, item_type: 'movie', title, year: 2021, local_status: 'Disponible', has_spanish_ok: 1, parent_id: null,
  ...extra,
}) as LibraryItemResponse

const DUNE = item(1, 'Dune')
const ALIEN = item(2, 'Alien')
const SERIE = item(3, 'Serie', { item_type: 'series' })

const SYNC = {
  lastInvalidatedAt: 0, libSyncing: false, startLibrarySync: vi.fn(), isAnySyncing: false,
  catalogSyncing: false, catalogStatus: null, startCatalogSync: vi.fn(),
}

let fijarVisible: (v: boolean) => void = () => {}
function Envoltorio() {
  const [visible, setVisible] = useState(true)
  fijarVisible = setVisible
  return (
    <SyncContext.Provider value={SYNC as never}>
      <SeccionVisible visible={visible}><LibraryPage onNavigate={vi.fn()} /></SeccionVisible>
    </SyncContext.Provider>
  )
}

const casilla = (titulo: string) => document.querySelector<HTMLInputElement>(`input[aria-label="Seleccionar «${titulo}»"]`)
const barra = () => document.querySelector('.lote-barra')

async function pagina(vista: 'list' | 'grid' = 'list') {
  localStorage.setItem('emuwarez_uploadrr_library_view', vista)
  await montar(<Envoltorio />)
  await esperar(() => texto().includes('Alien'))
}

beforeEach(() => {
  // Con búsqueda, solo lo que la cumple; sin ella, todo.
  vi.mocked(getLibrary).mockImplementation(async q => {
    const todos = [DUNE, ALIEN, SERIE]
    const items = q?.search ? todos.filter(i => i.title.toLowerCase().includes(String(q.search).toLowerCase())) : todos
    return { items, total: items.length, archivos: 2 } as never
  })
})
afterEach(async () => {
  await desmontar()
  vi.clearAllMocks()
  localStorage.clear()
})

describe('Seleccionar en Biblioteca', () => {
  it('lo que no tiene castellano no se marca; con otro tracker con clave sí: los idiomas los mira el lote', async () => {
    vi.mocked(getLibrary).mockResolvedValue({ items: [DUNE, ALIEN, item(4, 'Catalana', { has_spanish_ok: 0 })], total: 3 } as never)
    await pagina()
    await pulsar(boton('Seleccionar'))
    expect(casilla('Catalana')!.disabled).toBe(true)
    await desmontar()

    vi.mocked(fetchTrackersParaSubir).mockResolvedValueOnce([{ id: 'emu', nombre: 'eMuwarez' }, { id: 'mnv', nombre: 'Milnueve' }])
    await pagina()
    await pulsar(boton('Seleccionar'))
    await esperar(() => casilla('Catalana') && !casilla('Catalana')!.disabled)
    expect(casilla('Dune')!.disabled).toBe(false)
  })

  it('una clave añadida en Ajustes se nota al volver, sin recargar', async () => {
    vi.mocked(getLibrary).mockResolvedValue({ items: [DUNE, ALIEN, item(4, 'Catalana', { has_spanish_ok: 0 })], total: 3 } as never)
    await pagina()
    await pulsar(boton('Seleccionar'))
    expect(casilla('Catalana')!.disabled).toBe(true)

    vi.mocked(fetchTrackersParaSubir).mockResolvedValueOnce([{ id: 'emu', nombre: 'eMuwarez' }, { id: 'mnv', nombre: 'Milnueve' }])
    await act(async () => fijarVisible(false))
    await act(async () => fijarVisible(true))
    if (!casilla('Catalana')) await pulsar(boton('Seleccionar'))
    await esperar(() => casilla('Catalana') && !casilla('Catalana')!.disabled)
  })

  it('sin el modo selección no hay casillas ni barra; con él, en películas y series', async () => {
    await pagina()
    expect(casilla('Dune')).toBeNull()
    expect(barra()).toBeNull()
    await pulsar(boton('Seleccionar'))
    expect(casilla('Dune')).not.toBeNull()
    expect(casilla('Alien')).not.toBeNull()
    expect(casilla('Serie')).not.toBeNull()
    expect(barra()!.textContent).toContain('Marca las películas, series o temporadas que quieras subir juntas')
  })

  it('lo marcado sobrevive a filtrar, a una vista guardada y a cambiar de vista', async () => {
    localStorage.setItem('emuwarez_uploadrr_library_views', JSON.stringify([
      { id: 'v1', nombre: 'Solo Alien', filtros: { search: 'ali' } },
    ]))
    await pagina()
    await pulsar(boton('Seleccionar'))
    await pulsar(casilla('Dune')!)
    expect(barra()!.textContent).toContain('1 seleccionada')

    // La vista guardada filtra: Dune ya no se ve, pero sigue marcada.
    await pulsar(boton('Solo Alien'))
    await esperar(() => !texto().includes('Dune'))
    expect(barra()!.textContent).toContain('1 seleccionada (1 oculta por el filtro)')

    // En pósters también se marca, y la cuenta sigue.
    await pulsar(document.querySelector('button[aria-label="Vista de pósters"]')!)
    await pulsar(await esperar(() => casilla('Alien')))
    expect(casilla('Alien')!.checked).toBe(true)
    expect(barra()!.textContent).toContain('2 seleccionadas (1 oculta por el filtro)')

    // «Subir por lotes» abre el modal con las dos, en el orden en que se marcaron.
    await pulsar(boton('Subir por lotes'))
    const dialogo = document.querySelector('[role="dialog"]')!
    expect(dialogo.textContent).toContain('Subir por lotes')
    expect([...dialogo.querySelectorAll('.lote-fila-titulo')].map(e => e.textContent)).toEqual(['Dune (2021)', 'Alien (2021)'])
  })

  it('una serie marcada entra en el modal como sus temporadas', async () => {
    vi.mocked(getLibrary).mockImplementation(async q => {
      if (q?.parent_id === 3) {
        const temporadas = [1, 2].map(n => item(30 + n, `Serie - Temporada ${n}`, {
          item_type: 'season', parent_id: 3, season_number: n, resolution: '1080p', video_codec: 'x264',
        }))
        return { items: temporadas, total: 2 } as never
      }
      return { items: [DUNE, ALIEN, SERIE], total: 3, archivos: 2 } as never
    })
    await pagina()
    await pulsar(boton('Seleccionar'))
    await pulsar(casilla('Serie')!)
    await pulsar(casilla('Dune')!)
    await pulsar(boton('Subir por lotes'))
    const dialogo = document.querySelector('[role="dialog"]')!
    await esperar(() => dialogo.querySelectorAll('.lote-fila-titulo').length === 3)
    expect([...dialogo.querySelectorAll('.lote-fila-titulo')].map(e => e.textContent))
      .toEqual(['Serie (2021) - Temporada 1', 'Serie (2021) - Temporada 2', 'Dune (2021)'])
    expect(getLibrary).toHaveBeenCalledWith({ parent_id: 3, type: 'season', limit: 500 })
  })

  it('una temporada se marca desde su serie desplegada, y no cuenta como oculta por el filtro', async () => {
    vi.mocked(getLibrary).mockImplementation(async q => (q?.parent_id === 3
      ? { items: [item(31, 'Serie - Temporada 1', { item_type: 'season', parent_id: 3, season_number: 1 })], total: 1 }
      : { items: [DUNE, ALIEN, SERIE], total: 3, archivos: 2 }) as never)
    await pagina()
    await pulsar(boton('Seleccionar'))
    const filaSerie = [...document.querySelectorAll('tr.bib-fila')].find(tr => tr.textContent?.includes('Serie'))!
    await pulsar(filaSerie)
    await pulsar(await esperar(() => casilla('Serie - Temporada 1')))
    expect(casilla('Serie - Temporada 1')!.checked).toBe(true)
    expect(barra()!.textContent).toContain('1 seleccionada')
    expect(barra()!.textContent).not.toContain('oculta')
  })

  it('«Limpiar» desmarca todo y apagar «Seleccionar» también', async () => {
    await pagina()
    await pulsar(boton('Seleccionar'))
    await pulsar(casilla('Dune')!)
    await pulsar(boton('Limpiar'))
    expect(casilla('Dune')!.checked).toBe(false)
    await pulsar(casilla('Alien')!)
    await pulsar(boton('Seleccionar'))
    expect(barra()).toBeNull()
    await pulsar(boton('Seleccionar'))
    expect(casilla('Alien')!.checked).toBe(false)
  })

  it('salir de Biblioteca limpia la selección y quita el modo', async () => {
    await pagina()
    await pulsar(boton('Seleccionar'))
    await pulsar(casilla('Dune')!)
    await act(async () => { fijarVisible(false) })
    await act(async () => { fijarVisible(true) })
    expect(barra()).toBeNull()
    await pulsar(boton('Seleccionar'))
    expect(casilla('Dune')!.checked).toBe(false)
    expect(barra()!.textContent).not.toContain('seleccionada')
  })
})

describe('Columnas de la lista', () => {
  const cabeceras = () => [...document.querySelectorAll('thead th')]

  it('cada dato filtrable tiene su columna con su filtro en la cabecera, alineada con las celdas', async () => {
    await pagina()
    const nombres = cabeceras().map(th => th.textContent?.replace(/[▾▼▲]/g, '').trim())
    expect(nombres).toEqual([
      'TítuloTipo', 'Origen', 'Estado', 'Petición', 'Resolución', 'Proveedor', 'Source', 'Grupo',
      'Español', 'eMuwarez', 'Tamaño', 'Acciones',
    ])
    for (const col of ['Origen', 'Resolución', 'Proveedor', 'Source', 'Grupo']) {
      const th = cabeceras().find(c => c.textContent?.includes(col))!
      expect(th.querySelectorAll('.hf-trigger')).toHaveLength(1)
    }
    // El tipo no es columna: su filtro acompaña al nombre del título.
    expect(cabeceras()[0].querySelector('.hf-trigger')!.textContent).toContain('Tipo')
    expect(document.querySelectorAll('tr.bib-fila')[0].querySelectorAll(':scope > td')).toHaveLength(cabeceras().length)
  })

  it('el menú de la columna Grupo se abre desde su propia cabecera', async () => {
    await pagina()
    const th = cabeceras()[7]
    await pulsar(th.querySelector('.hf-trigger')!)
    expect(document.body.textContent).toContain('Desmarcar todo')
    expect(cabeceras()[0].querySelectorAll('.hf-trigger')).toHaveLength(1)
  })
})

describe('Pósters sin imagen', () => {
  it('la tarjeta sin póster lo marca para que el nombre salga una sola vez (el del marco)', async () => {
    vi.mocked(getLibrary).mockImplementation(async () => (
      { items: [{ ...DUNE, poster_url: '/dune.jpg' }, ALIEN], total: 2, archivos: 2 } as never))
    await pagina('grid')
    const tarjetas = [...document.querySelectorAll('.bib-tarjeta')]
    const de = (titulo: string) => tarjetas.find(t => t.textContent?.includes(titulo))!
    expect(de('Alien').classList.contains('bib-tarjeta-sin-poster')).toBe(true)
    expect(de('Alien').querySelector('.bib-tarjeta-sin-nombre')?.textContent).toBe('Alien')
    expect(de('Dune').classList.contains('bib-tarjeta-sin-poster')).toBe(false)
    expect(de('Dune').querySelector('.bib-tarjeta-sin-nombre')).toBeNull()
  })
})

describe('Carpeta manual', () => {
  const SUGERIDA = item(4, 'dune part two', {
    instance_type: 'carpeta', instance_name: 'Manual', instance_id: 5, por_identificar: true,
    sugerencia_tmdb: { tmdb_id: '693134', tipo: 'movie', titulo: 'Dune: Parte dos', anio: 2024, poster_url: null },
  })
  const filtroPorIdentificar = () => [...document.querySelectorAll('button[aria-pressed]')]
    .find(b => b.textContent?.startsWith('Por identificar'))

  beforeEach(() => {
    // jsdom no mide el texto, y el tooltip del menú de Origen ajusta su ancho con esto.
    Range.prototype.getClientRects = () => [] as unknown as DOMRectList
    vi.mocked(getLibraryFilterOptions).mockResolvedValue({ instances: [{ id: 5, type: 'carpeta', name: 'Manual' }] } as never)
    vi.mocked(getLibrary).mockImplementation(async q => (q?.por_identificar
      ? { items: [SUGERIDA], total: 3, archivos: 2 }
      : { items: [DUNE, ALIEN, SUGERIDA], total: 3, archivos: 2 }) as never)
  })

  it('«Por identificar» dice cuántas faltan, deja solo esas y confirma las sugeridas', async () => {
    vi.mocked(identificarSugeridas).mockResolvedValue({ ok: true, confirmadas: 1, message: '1 ficha confirmada' })
    await pagina()
    const filtro = await esperar(filtroPorIdentificar)
    expect(getLibrary).toHaveBeenCalledWith({ por_identificar: true, parent_id: 0, limit: 1 })
    expect(filtro.textContent).toBe('Por identificar 3')
    expect(boton('Confirmar las sugeridas')).toBeUndefined()

    await pulsar(filtro)
    await esperar(() => !texto().includes('Alien'))
    expect(getLibrary).toHaveBeenCalledWith(expect.objectContaining({ por_identificar: true, parent_id: 0, limit: 20000 }))
    expect(filtro.getAttribute('aria-pressed')).toBe('true')

    await pulsar(boton('Confirmar las sugeridas'))
    expect(identificarSugeridas).toHaveBeenCalledWith(undefined)
    await esperar(() => texto().includes('1 ficha confirmada'))
  })

  it('el filtro Origen nombra la carpeta como carpeta, no como Sonarr', async () => {
    await pagina()
    await esperar(filtroPorIdentificar)
    const origen = [...document.querySelectorAll('thead th')].find(th => th.textContent?.includes('Origen'))!
    await pulsar(origen.querySelector('.hf-trigger')!)
    expect(texto()).toContain('Carpeta: Manual')
    expect(texto()).not.toContain('Sonarr: Manual')
  })

  it('la chapa de la fila abre el diálogo de TMDb, y al confirmar se cierra y la lista se recarga', async () => {
    vi.mocked(getTmdbCandidatos).mockResolvedValue({ tipo: 'movie', consulta: 'dune part two', anio: 2024, candidatos: [] })
    vi.mocked(identificarTmdb).mockResolvedValue({ ok: true, message: 'Ficha confirmada', item_id: 4 })
    await pagina()
    const chapa = await esperar(() => document.querySelector<HTMLButtonElement>('tr.bib-fila button.bib-chip-identificar'))
    await pulsar(chapa)
    const dialogo = await esperar(() => document.querySelector('[role="dialog"]'))
    expect(dialogo.textContent).toContain('Identificar en TMDb')
    expect(getTmdbCandidatos).toHaveBeenCalledWith(4, undefined)
    const cargas = vi.mocked(getLibrary).mock.calls.length
    await pulsar(boton('Sí, es esta'))
    await esperar(() => !document.querySelector('[role="dialog"]'))
    expect(identificarTmdb).toHaveBeenCalledWith(4, '693134', 'movie')
    expect(vi.mocked(getLibrary).mock.calls.length).toBeGreaterThan(cargas)
    expect(texto()).toContain('Ficha confirmada')
  })
})

describe('Biblioteca: lo que se recarga sola tras una sincronización', () => {
  let invalidar: () => void = () => {}
  function ConMarca() {
    const [marca, setMarca] = useState(0)
    invalidar = () => setMarca(m => m + 1)
    return (
      <SyncContext.Provider value={{ ...SYNC, lastInvalidatedAt: marca } as never}>
        <SeccionVisible visible><LibraryPage onNavigate={vi.fn()} /></SeccionVisible>
      </SyncContext.Provider>
    )
  }
  afterEach(() => { history.replaceState(null, '', '/') })

  it('una recarga en segundo plano que falla deja la lista que ya se veía', async () => {
    localStorage.setItem('emuwarez_uploadrr_library_view', 'list')
    await montar(<ConMarca />)
    await esperar(() => texto().includes('Alien'))
    vi.mocked(getLibrary).mockRejectedValue(new Error('503'))
    const antes = vi.mocked(getLibrary).mock.calls.length
    await act(async () => { invalidar() })
    await esperar(() => vi.mocked(getLibrary).mock.calls.length > antes)
    await act(async () => { await new Promise(r => setTimeout(r, 20)) })
    expect(texto()).toContain('Alien')
    expect(texto()).not.toContain('Error al cargar la biblioteca')
  })

  it('la ficha abierta se vuelve a pedir y enseña lo de después de la sincronización', async () => {
    vi.mocked(getLibraryItem).mockResolvedValue(DUNE)
    localStorage.setItem('emuwarez_uploadrr_library_view', 'list')
    await montar(<ConMarca />)
    await esperar(() => texto().includes('Alien'))
    const enlace = [...document.querySelectorAll('a.lib-title-link')].find(a => a.textContent?.includes('Dune'))!
    await act(async () => { enlace.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 })) })
    await esperar(() => vi.mocked(getLibraryItem).mock.calls.length === 1)
    vi.mocked(getLibraryItem).mockResolvedValue({ ...DUNE, display_title: 'Dune al día' } as LibraryItemResponse)
    await act(async () => { invalidar() })
    await esperar(() => texto().includes('Dune al día'))
    expect(vi.mocked(getLibraryItem).mock.calls.map(c => c[0])).toEqual([1, 1])
  })

  const abrirFicha = async (titulo: string) => {
    const enlace = [...document.querySelectorAll('a.lib-title-link')].find(a => a.textContent?.includes(titulo))!
    await act(async () => { enlace.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 })) })
  }

  it('dos recargas de la ficha que responden al revés: se queda la más nueva', async () => {
    vi.mocked(getLibraryItem).mockResolvedValue(DUNE)
    localStorage.setItem('emuwarez_uploadrr_library_view', 'list')
    await montar(<ConMarca />)
    await esperar(() => texto().includes('Alien'))
    await abrirFicha('Dune')
    await esperar(() => vi.mocked(getLibraryItem).mock.calls.length === 1)
    let soltarVieja: () => void = () => {}
    vi.mocked(getLibraryItem)
      .mockImplementationOnce(() => new Promise(r => { soltarVieja = () => r({ ...DUNE, display_title: 'Dune vieja' } as LibraryItemResponse) }))
      .mockResolvedValueOnce({ ...DUNE, display_title: 'Dune nueva' } as LibraryItemResponse)
    await act(async () => { invalidar() })
    await act(async () => { invalidar() })
    await esperar(() => texto().includes('Dune nueva'))
    await act(async () => { soltarVieja() })
    await act(async () => { await new Promise(r => setTimeout(r, 20)) })
    expect(texto()).toContain('Dune nueva')
    expect(texto()).not.toContain('Dune vieja')
  })

  it('la tabla de temporadas de la ficha abierta se pone al día tras una sincronización, sin «Cargando…»', async () => {
    const temporada = (id: number, n: number) => item(id, `Serie - Temporada ${n}`, { item_type: 'season', parent_id: 3, season_number: n })
    let temporadas = [temporada(31, 1)]
    vi.mocked(getLibrary).mockImplementation(async q => (q?.parent_id === 3
      ? { items: temporadas, total: temporadas.length }
      : { items: [DUNE, ALIEN, SERIE], total: 3, archivos: 2 }) as never)
    vi.mocked(getLibraryItem).mockResolvedValue(SERIE)
    localStorage.setItem('emuwarez_uploadrr_library_view', 'list')
    await montar(<ConMarca />)
    await esperar(() => texto().includes('Alien'))
    await abrirFicha('Serie')
    await esperar(() => texto().includes('Temporada 1'))
    temporadas = [temporada(31, 1), temporada(32, 2)]
    await act(async () => { invalidar() })
    expect(document.querySelector('.hijos-aviso')).toBeNull()
    await esperar(() => texto().includes('Temporada 2'))
  })

  it('dos recargas de la tabla de temporadas que responden al revés: se queda la más nueva', async () => {
    const temporada = (id: number, n: number) => item(id, `Serie - Temporada ${n}`, { item_type: 'season', parent_id: 3, season_number: n })
    const respuestas: (() => Promise<unknown>)[] = []
    vi.mocked(getLibrary).mockImplementation(async q => (q?.parent_id === 3
      ? (respuestas.shift() ?? (async () => ({ items: [temporada(31, 1)], total: 1 })))()
      : { items: [DUNE, ALIEN, SERIE], total: 3, archivos: 2 }) as never)
    vi.mocked(getLibraryItem).mockResolvedValue(SERIE)
    localStorage.setItem('emuwarez_uploadrr_library_view', 'list')
    await montar(<ConMarca />)
    await esperar(() => texto().includes('Alien'))
    await abrirFicha('Serie')
    await esperar(() => texto().includes('Temporada 1'))
    let soltarVieja: () => void = () => {}
    respuestas.push(
      () => new Promise(r => { soltarVieja = () => r({ items: [temporada(31, 1)], total: 1 }) }),
      async () => ({ items: [temporada(31, 1), temporada(32, 2)], total: 2 }),
    )
    await act(async () => { invalidar() })
    await act(async () => { invalidar() })
    await esperar(() => texto().includes('Temporada 2'))
    await act(async () => { soltarVieja() })
    await act(async () => { await new Promise(r => setTimeout(r, 20)) })
    expect(texto()).toContain('Temporada 2')
  })
})
