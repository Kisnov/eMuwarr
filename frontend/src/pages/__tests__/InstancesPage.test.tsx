// @vitest-environment jsdom
// Instancias: editar dice qué se edita y no pierde el foco; el botón de añadir no cambia de significado.
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { InstanceSafeResponse } from '../../types/api'

vi.mock('../../api/instances', () => ({
  getInstances: vi.fn(), addInstance: vi.fn(), updateInstance: vi.fn(), testInstance: vi.fn(),
  syncInstance: vi.fn(), deleteInstance: vi.fn(),
}))
vi.mock('../../api/dashboard', () => ({ getDashboardSummary: vi.fn() }))
vi.mock('../../api/integrationTests', () => ({ testArrConnection: vi.fn() }))
vi.mock('../../hooks/useRefrescoPorAvisos', () => ({ useRefrescoPorAvisos: () => {} }))

import { addInstance, getInstances, syncInstance, testInstance, updateInstance } from '../../api/instances'
import { getDashboardSummary } from '../../api/dashboard'
import { InstancesPage } from '../InstancesPage'
import { elegir, escribir } from '../../components/__tests__/dom'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const INSTANCIA: InstanceSafeResponse = {
  id: 7, type: 'radarr', name: 'Radarr HD', url: 'http://radarr:7878', api_key_masked: 'abcd****', enabled: true,
  is_4k: false, is_anime: false, last_test_status: null, last_test_at: null, created_at: '2026-11-01T10:00:00', updated_at: null,
}

let raiz: Root
let contenedor: HTMLDivElement

async function esperar() {
  await act(async () => { await new Promise(r => setTimeout(r, 0)) })
}
// Lo que el componente deja para el siguiente fotograma (mover el foco).
async function fotograma() {
  await act(async () => { await new Promise(r => requestAnimationFrame(() => r(null))) })
}

async function montar() {
  contenedor = document.createElement('div')
  document.body.appendChild(contenedor)
  raiz = createRoot(contenedor)
  await act(async () => { raiz.render(<InstancesPage />) })
  await esperar()
}

function boton(texto: string): HTMLButtonElement {
  const b = [...contenedor.querySelectorAll('button')].find(x => x.textContent?.trim() === texto)
  if (!b) throw new Error(`No hay botón «${texto}»`)
  return b
}

async function pulsar(el: HTMLElement) {
  await act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
  await esperar()
}

beforeEach(() => {
  vi.mocked(getInstances).mockResolvedValue([structuredClone(INSTANCIA)])
  vi.mocked(getDashboardSummary).mockResolvedValue({ instancias_caidas: [] } as unknown as Awaited<ReturnType<typeof getDashboardSummary>>)
})

afterEach(async () => {
  await act(async () => { raiz.unmount() })
  contenedor.remove()
  vi.clearAllMocks()
})

describe('Instancias', () => {
  it('«Editar» enseña qué instancia se edita, lleva el foco al primer campo y «Cancelar» lo devuelve a «Editar»', async () => {
    await montar()
    await pulsar(contenedor.querySelector<HTMLButtonElement>('button[aria-label="Editar Radarr HD"]')!)
    await fotograma()
    expect(contenedor.querySelector('.inst-titulo-edicion')?.textContent).toBe('Editar «Radarr HD»')
    const nombre = contenedor.querySelector<HTMLInputElement>('#inst-edicion-7 input')!
    expect(nombre.value).toBe('Radarr HD')
    expect(document.activeElement).toBe(nombre)
    expect(nombre.labels?.[0]?.textContent).toContain('máx. 15')
    // Un solo principal a la vista: con la edición abierta, el de la cabecera se apaga como en el alta.
    expect(boton('+ Añadir instancia').disabled).toBe(true)
    await pulsar(boton('Cancelar'))
    await fotograma()
    expect(document.activeElement).toBe(contenedor.querySelector('button[aria-label="Editar Radarr HD"]'))
    expect(boton('+ Añadir instancia').disabled).toBe(false)
  })

  it('el botón de la cabecera no se convierte en «Cancelar»: se desactiva y el formulario lleva su propio «Cancelar»', async () => {
    await montar()
    const anadir = boton('+ Añadir instancia')
    await pulsar(anadir)
    await fotograma()
    expect(anadir.textContent).toBe('+ Añadir instancia')
    expect(anadir.disabled).toBe(true)
    expect(anadir.getAttribute('aria-expanded')).toBe('true')
    expect(contenedor.querySelector('#inst-nueva')!.contains(document.activeElement)).toBe(true)
    await pulsar(boton('Cancelar'))
    await fotograma()
    expect(contenedor.querySelector('#inst-nueva')).toBeNull()
    expect(anadir.disabled).toBe(false)
    expect(document.activeElement).toBe(anadir)
  })

  it('abierto desde el estado vacío, «Cancelar» devuelve el foco a ese botón y no al de la cabecera', async () => {
    vi.mocked(getInstances).mockResolvedValue([])
    await montar()
    const delVacio = contenedor.querySelector<HTMLButtonElement>('.empty-state button, [class*="empty"] button')!
    expect(delVacio.textContent).toBe('+ Añadir instancia')
    await act(async () => { delVacio.focus() })
    await pulsar(delVacio)
    await fotograma()
    await pulsar(boton('Cancelar'))
    await fotograma()
    expect(document.activeElement).toBe(delVacio)
  })
})

// El control de un Campo: su etiqueta apunta a él con `for`. null si no está.
function control<T extends HTMLElement = HTMLInputElement>(etiqueta: string): T | null {
  const label = [...contenedor.querySelectorAll('label')].find(l => l.textContent?.trim().startsWith(etiqueta))
  return ((label && document.getElementById(label.htmlFor)) ?? null) as T | null
}
const botonSiHay = (texto: string) => [...contenedor.querySelectorAll('button')].find(x => x.textContent?.trim() === texto)

const CARPETA: InstanceSafeResponse = {
  ...INSTANCIA, id: 8, type: 'carpeta', name: 'Manual', url: '/data/torrents/manual', api_key_masked: '',
}

describe('Carpeta manual', () => {
  it('en el alta pide nombre y ruta, sin URL, API key, 4K ni Anime, y la envía sin clave', async () => {
    vi.mocked(addInstance).mockResolvedValue({ ok: true, message: 'creada' })
    await montar()
    await pulsar(boton('+ Añadir instancia'))
    await elegir(control<HTMLSelectElement>('Tipo')!, 'carpeta')
    expect(control('URL')).toBeNull()
    expect(control('API key')).toBeNull()
    const alta = contenedor.querySelector('#inst-nueva')!
    expect(alta.textContent).not.toContain('4K')
    // Una ruta sin guardar no se puede probar: se comprueba al añadirla.
    expect(alta.textContent).not.toContain('Probar conexión')
    const ruta = control('Ruta de la carpeta')!
    expect(ruta.placeholder).toBe('/data/torrents/manual')
    expect(ruta.classList.contains('inst-ruta')).toBe(true)
    expect(ruta.parentElement!.textContent).toContain('mismo disco que la carpeta de subidas')

    await escribir(control('Nombre')!, 'Manual')
    await escribir(ruta, 'data/torrents/manual')
    await pulsar(boton('Añadir'))
    expect(contenedor.textContent).toContain('La ruta tiene que ser absoluta: empieza por /.')
    expect(addInstance).not.toHaveBeenCalled()

    await escribir(ruta, ' /data/torrents/manual ')
    await pulsar(boton('Añadir'))
    expect(addInstance).toHaveBeenCalledWith({
      type: 'carpeta', name: 'Manual', url: '/data/torrents/manual', api_key: '', is_4k: false, is_anime: false,
    })
  })

  it('volver de carpeta a Radarr vacía la ruta y devuelve la URL y la API key', async () => {
    await montar()
    await pulsar(boton('+ Añadir instancia'))
    await elegir(control<HTMLSelectElement>('Tipo')!, 'carpeta')
    await escribir(control('Ruta de la carpeta')!, '/data/manual')
    await elegir(control<HTMLSelectElement>('Tipo')!, 'radarr')
    expect(control('URL')!.value).toBe('')
    expect(control('API key')).not.toBeNull()
    expect(control('Ruta de la carpeta')).toBeNull()
  })

  it('su tarjeta enseña la ruta y no la clave, y sus botones comprueban y escanean la carpeta', async () => {
    vi.mocked(getInstances).mockResolvedValue([structuredClone(CARPETA)])
    vi.mocked(testInstance).mockResolvedValue({
      ok: true, instance_id: 8, instance_name: 'Manual', version: null, username: null, error: null, message: '12 vídeos en la carpeta',
    })
    vi.mocked(syncInstance).mockResolvedValue({
      ok: true, imported: 12, missing: 0, instances: 1, errors: [],
      message: "Carpeta 'Manual' escaneada: 12 vídeos, 3 por identificar en TMDb",
    })
    await montar()
    const tarjeta = contenedor.querySelector('.inst-tarjeta')!
    expect(tarjeta.querySelector('.instance-card-url')!.textContent).toBe('/data/torrents/manual')
    expect(tarjeta.textContent).not.toContain('API key')
    expect(botonSiHay('Probar conexión')).toBeUndefined()
    expect(botonSiHay('Sincronizar')).toBeUndefined()
    await pulsar(boton('Comprobar carpeta'))
    expect(testInstance).toHaveBeenCalledWith(8)
    expect(tarjeta.textContent).toContain('12 vídeos en la carpeta')
    await pulsar(boton('Escanear'))
    expect(syncInstance).toHaveBeenCalledWith(8)
    expect(tarjeta.textContent).toContain('12 vídeos, 3 por identificar en TMDb')
  })

  it('lo que conviene mirar de la carpeta sale como aviso, no en verde', async () => {
    vi.mocked(getInstances).mockResolvedValue([structuredClone(CARPETA)])
    vi.mocked(testInstance).mockResolvedValue({
      ok: true, instance_id: 8, instance_name: 'Manual', version: null, username: null, error: null,
      message: '12 vídeos en la carpeta', warning: 'La carpeta está fuera de las rutas permitidas para subir (Ajustes)',
    })
    await montar()
    await pulsar(boton('Comprobar carpeta'))
    const tarjeta = contenedor.querySelector('.inst-tarjeta')!
    expect(tarjeta.textContent).toContain('12 vídeos en la carpeta. La carpeta está fuera de las rutas permitidas')
    expect(tarjeta.querySelector('.inst-resultado--aviso')).not.toBeNull()
  })

  it('si la carpeta no se puede leer lo dice tal cual', async () => {
    vi.mocked(getInstances).mockResolvedValue([structuredClone(CARPETA)])
    vi.mocked(testInstance).mockResolvedValue({
      ok: false, instance_id: 8, instance_name: 'Manual', version: null, username: null,
      error: 'No existe o no se puede leer la carpeta /data/torrents/manual dentro del contenedor',
    })
    await montar()
    await pulsar(boton('Comprobar carpeta'))
    expect(contenedor.querySelector('.inst-resultado--error')!.textContent)
      .toBe('No existe o no se puede leer la carpeta /data/torrents/manual dentro del contenedor')
  })

  it('al editarla cambia la ruta, sin campo de clave ni prueba de conexión', async () => {
    vi.mocked(getInstances).mockResolvedValue([structuredClone(CARPETA)])
    vi.mocked(updateInstance).mockResolvedValue({ ...CARPETA, url: '/data/otra' })
    await montar()
    await pulsar(contenedor.querySelector<HTMLButtonElement>('button[aria-label="Editar Manual"]')!)
    expect(control('API key')).toBeNull()
    expect(botonSiHay('Probar conexión')).toBeUndefined()
    const ruta = control('Ruta de la carpeta')!
    expect(ruta.value).toBe('/data/torrents/manual')
    await escribir(ruta, 'otra')
    await pulsar(boton('Guardar cambios'))
    expect(contenedor.textContent).toContain('La ruta tiene que ser absoluta: empieza por /.')
    expect(updateInstance).not.toHaveBeenCalled()
    await escribir(ruta, '/data/otra')
    await pulsar(boton('Guardar cambios'))
    expect(updateInstance).toHaveBeenCalledWith(8, expect.objectContaining({ url: '/data/otra', api_key: undefined }))
  })
})
