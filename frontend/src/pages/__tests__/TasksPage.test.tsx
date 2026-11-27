// @vitest-environment jsdom
// Tareas: un cambio de horario o de opción, que se guarda solo, confirma que se ha guardado; y una tarea que falla
// por algo que se arregla en otra pantalla lleva a esa pantalla.
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ScheduledTask } from '../../api/scheduledTasks'

vi.mock('../../api/scheduledTasks', () => ({
  getScheduledTasks: vi.fn(), updateScheduledTaskSchedule: vi.fn(), updateScheduledTaskOptions: vi.fn(),
  runScheduledTaskNow: vi.fn(), getScheduledTaskLogs: vi.fn(),
}))
vi.mock('../../hooks/useRefrescoPorAvisos', () => ({ useRefrescoPorAvisos: () => {} }))

import { getScheduledTasks, getScheduledTaskLogs, updateScheduledTaskSchedule, updateScheduledTaskOptions } from '../../api/scheduledTasks'
import { TasksPage } from '../TasksPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const TAREA: ScheduledTask = {
  task_key: 'sync-emu-requests', name: 'Sincronizar peticiones', description: 'Descarga las peticiones de eMuwarez.',
  interval_minutes: 60, min_interval_minutes: 60, run_at: null, timezone: 'Europe/Madrid',
  opciones: [{ clave: 'dias', etiqueta: 'Días que se conservan', valor: 7, minimo: 1, maximo: 30, nota: '' }],
  manual_allowed: false, enabled: true, last_run_at: null, last_status: 'failed',
  last_summary: 'No hay API key de eMuwarez configurada', next_run_at: null,
}

let raiz: Root
let contenedor: HTMLDivElement

async function esperar() {
  await act(async () => { await new Promise(r => setTimeout(r, 0)) })
}

async function montar(onNavigate?: (p: string) => void) {
  contenedor = document.createElement('div')
  document.body.appendChild(contenedor)
  raiz = createRoot(contenedor)
  await act(async () => { raiz.render(<TasksPage onNavigate={onNavigate} />) })
  await esperar()
}

const texto = () => contenedor.textContent ?? ''

beforeEach(() => {
  vi.mocked(getScheduledTasks).mockResolvedValue([structuredClone(TAREA)])
  vi.mocked(updateScheduledTaskSchedule).mockResolvedValue(undefined as unknown as Awaited<ReturnType<typeof updateScheduledTaskSchedule>>)
  vi.mocked(updateScheduledTaskOptions).mockResolvedValue(undefined as unknown as Awaited<ReturnType<typeof updateScheduledTaskOptions>>)
})

afterEach(async () => {
  await act(async () => { raiz.unmount() })
  contenedor.remove()
  vi.clearAllMocks()
  vi.useRealTimers()
})

describe('Tareas', () => {
  it('cambiar el intervalo confirma «Horario guardado» y la confirmación se va sola', async () => {
    await montar()
    const select = contenedor.querySelector<HTMLSelectElement>('select[id$="-intervalo"]')!
    vi.useFakeTimers()
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, '90')
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(updateScheduledTaskSchedule).toHaveBeenCalledWith('sync-emu-requests', { interval_minutes: 90 })
    expect(texto()).toContain('✓ Horario guardado')
    await act(async () => { await vi.advanceTimersByTimeAsync(3100) })
    expect(texto()).not.toContain('✓ Horario guardado')
  })

  it('el intervalo enseña el que tiene la tarea aunque no esté en la lista, y se puede volver a semanal', async () => {
    vi.mocked(getScheduledTasks).mockResolvedValue([
      { ...structuredClone(TAREA), task_key: 'vigilar-instancias', interval_minutes: 5, min_interval_minutes: 5 },
      { ...structuredClone(TAREA), task_key: 'sync-emu-catalog-full', interval_minutes: 1440, min_interval_minutes: 1440 },
    ])
    await montar()
    const [vigilar, catalogo] = [...contenedor.querySelectorAll<HTMLSelectElement>('select[id$="-intervalo"]')]
    expect(vigilar.value).toBe('5')
    expect(vigilar.selectedOptions[0].textContent).toBe('5 min')
    expect([...catalogo.options].map(o => o.value)).toEqual(['1440', '10080'])
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(catalogo, '10080')
      catalogo.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await esperar()
    expect(updateScheduledTaskSchedule).toHaveBeenCalledWith('sync-emu-catalog-full', { interval_minutes: 10080 })
  })

  it('una opción guardada al salir del campo dice «Guardado»', async () => {
    await montar()
    const campo = contenedor.querySelector<HTMLInputElement>('input[type="number"]')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(campo, '10')
      campo.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => { campo.dispatchEvent(new FocusEvent('focusout', { bubbles: true })) })
    await esperar()
    expect(updateScheduledTaskOptions).toHaveBeenCalledWith('sync-emu-requests', { dias: 10 })
    expect(texto()).toContain('✓ Guardado')
  })

  it('si falla por la clave de eMuwarez, enlaza a Configuración', async () => {
    const navegar = vi.fn()
    await montar(navegar)
    const enlace = [...contenedor.querySelectorAll('a')].find(a => a.textContent?.includes('Revisar eMuwarez en Configuración'))!
    expect(enlace.getAttribute('href')).toBe('/settings')
    await act(async () => { enlace.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 })) })
    expect(navegar).toHaveBeenCalledWith('settings')
  })

  it('«Ver logs» de una tarea sin historial lo dice y enseña su última ejecución', async () => {
    vi.mocked(getScheduledTaskLogs).mockResolvedValue({
      task_key: 'sync-emu-requests', sin_historial: true,
      logs: [{ id: 0, name: 'sync-emu-requests', status: 'completed', started_at: '2026-11-19T14:55:00+00:00',
        finished_at: '2026-11-19T14:55:00+00:00', summary: '2 instancias responden', details: null }],
    })
    await montar()
    const boton = [...contenedor.querySelectorAll('button')].find(b => b.textContent === 'Ver logs')!
    await act(async () => { boton.click() })
    await esperar()
    const cuerpo = document.body.textContent ?? ''
    expect(cuerpo).toContain('no guarda historial')
    expect(cuerpo).toContain('2 instancias responden')
    expect(cuerpo).not.toContain('Sin ejecuciones registradas todavía')
  })

  it('una tarea que va bien no enseña el enlace', async () => {
    vi.mocked(getScheduledTasks).mockResolvedValue([{ ...structuredClone(TAREA), last_status: 'success', last_summary: 'OK' }])
    await montar(vi.fn())
    expect(contenedor.querySelector('a.tareas-arreglo')).toBeNull()
  })
})
