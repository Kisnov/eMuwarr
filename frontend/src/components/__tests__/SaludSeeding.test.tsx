// @vitest-environment jsdom
/**
 * La salud del seeding en Subidas: la celda de la lista y el bloque del detalle, con el servidor simulado.
 */
import { act, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/seeding', () => ({ getSeeding: vi.fn() }))
vi.mock('../../api/uploads', () => ({ reenviarAQbit: vi.fn() }))

import { getSeeding, type SeedingItem, type SeedingResponse } from '../../api/seeding'
import { reenviarAQbit } from '../../api/uploads'
import { useSeeding } from '../../hooks/useSeeding'
import type { UploadJob } from '../../types/api'
import { SeedingBloque, SeedingCelda, nombreDelTorrent } from '../SaludSeeding'
import { boton, desmontar, esperar, montar, pulsar, texto } from './dom'

const m = vi.mocked(getSeeding)
const reenviar = vi.mocked(reenviarAQbit)

const subida = (parcial: Partial<UploadJob> = {}): UploadJob => ({ id: 7, status: 'completed', ...parcial }) as UploadJob

function respuesta(item: Partial<SeedingItem> | null, parcial: Partial<SeedingResponse> = {}): SeedingResponse {
  return {
    configurado: true, disponible: true, mensaje: null, actualizado: '2026-11-02T10:00:00+00:00',
    items: item ? [{ job_id: 7, estado: 'sembrando', etiqueta: 'Sembrando', ratio: 1.5, subido: 3221225472,
      progreso: 1, seeds: 12, peers: 3, ...item }] : [],
    ...parcial,
  }
}

function Pagina({ job, onVerRegistro, onReenviado }: { job: UploadJob; onVerRegistro?: () => void; onReenviado?: () => void }) {
  const seeding = useSeeding()
  return (
    <>
      <div data-testid="celda"><SeedingCelda job={job} seeding={seeding} /></div>
      <SeedingBloque job={job} seeding={seeding} onVerRegistro={onVerRegistro} onReenviado={onReenviado} />
    </>
  )
}

beforeEach(() => { m.mockReset(); reenviar.mockReset() })
afterEach(desmontar)

describe('SaludSeeding', () => {
  it('enseña estado y ratio en la lista y el bloque completo en el detalle, con cifras es-ES', async () => {
    m.mockResolvedValue(respuesta({}))
    await montar(<Pagina job={subida()} />)
    await esperar(() => texto().includes('Ratio'))
    expect(document.querySelector('[data-testid="celda"]')!.textContent).toBe('Sembrando · ratio 1,50')
    const bloque = document.querySelector('[data-testid="seeding-bloque"]')!.textContent!
    expect(bloque).toContain('3,0 GB')
    expect(bloque).toContain('100,0 %')
    expect(bloque).toContain('12 / 3')
    expect(bloque).toContain('Leído')
  })

  describe('Abrir en qBittorrent', () => {
    const enlace = () => [...document.querySelectorAll('a')].find(a => a.textContent?.includes('Abrir en qBittorrent'))

    it('sin URL pública no hay enlace ni «Copiar nombre»', async () => {
      m.mockResolvedValue(respuesta({}, { url_publica: null }))
      await montar(<Pagina job={subida({ prepared_path: '/data/uploads/Peli.2020.1080p' })} />)
      await esperar(() => texto().includes('Ratio'))
      expect(enlace()).toBeUndefined()
      expect(boton('Copiar nombre')).toBeUndefined()
    })

    it('con URL pública enseña el enlace a esa URL, en pestaña nueva y sin opener', async () => {
      m.mockResolvedValue(respuesta({}, { url_publica: 'https://qbit.midominio.es/' }))
      await montar(<Pagina job={subida({ prepared_path: '/data/uploads/Peli.2020.1080p' })} />)
      await esperar(() => enlace() !== undefined)
      const a = enlace()!
      expect(a.getAttribute('href')).toBe('https://qbit.midominio.es/')
      expect(a.getAttribute('target')).toBe('_blank')
      expect(a.getAttribute('rel')).toBe('noopener noreferrer')
    })

    it('el enlace sigue ahí aunque qBittorrent no responda (la URL no depende de la consulta)', async () => {
      m.mockResolvedValue(respuesta(null, { disponible: false, mensaje: 'No se pudo conectar con qBittorrent',
        url_publica: 'http://192.168.1.5:8080' }))
      await montar(<Pagina job={subida()} />)
      await esperar(() => enlace() !== undefined)
      expect(enlace()!.getAttribute('href')).toBe('http://192.168.1.5:8080')
    })

    it('«Copiar nombre» copia el último trozo de la ruta preparada', async () => {
      const escribirPortapapeles = vi.fn().mockResolvedValue(undefined)
      vi.stubGlobal('isSecureContext', true)
      Object.defineProperty(navigator, 'clipboard', { value: { writeText: escribirPortapapeles }, configurable: true })
      try {
        m.mockResolvedValue(respuesta({}, { url_publica: 'https://qbit.midominio.es' }))
        await montar(<Pagina job={subida({ prepared_path: '/data/uploads/Peli.2020.1080p-EMUWAREZ/' })} />)
        await esperar(() => boton('Copiar nombre') !== undefined)
        await pulsar(boton('Copiar nombre'))
        expect(escribirPortapapeles).toHaveBeenCalledWith('Peli.2020.1080p-EMUWAREZ')
        expect(boton('Nombre copiado')).toBeDefined()
      } finally {
        vi.unstubAllGlobals()
        Reflect.deleteProperty(navigator, 'clipboard')
      }
    })

    it('sin ruta preparada no se ofrece copiar el nombre, pero el enlace sí', async () => {
      m.mockResolvedValue(respuesta({}, { url_publica: 'https://qbit.midominio.es' }))
      await montar(<Pagina job={subida({ prepared_path: undefined })} />)
      await esperar(() => enlace() !== undefined)
      expect(boton('Copiar nombre')).toBeUndefined()
    })

    it('nombreDelTorrent acepta barras de las dos clases y una ruta vacía', () => {
      expect(nombreDelTorrent(subida({ prepared_path: 'C:\\uploads\\Peli 2020' }))).toBe('Peli 2020')
      expect(nombreDelTorrent(subida({ prepared_path: undefined }))).toBe('')
    })
  })

  it('el estado va siempre con texto, no solo con color', async () => {
    m.mockResolvedValue(respuesta({ estado: 'faltan_ficheros', etiqueta: 'Faltan ficheros', progreso: 0.4 }))
    await montar(<Pagina job={subida()} />)
    await esperar(() => texto().includes('Ratio'))
    expect(document.querySelector('[data-testid="celda"]')!.textContent).toContain('Faltan ficheros')
    expect(document.querySelector('.badge.status-error')).not.toBeNull()
    expect(texto()).toContain('carpeta de subidas')
  })

  it('«No se encuentra» cuando qBittorrent responde pero no hay torrent de esa subida', async () => {
    m.mockResolvedValue(respuesta({ estado: 'no_encontrado', etiqueta: 'No se encuentra' }, { items: [
      { job_id: 7, estado: 'no_encontrado', etiqueta: 'No se encuentra' }] }))
    await montar(<Pagina job={subida()} />)
    await esperar(() => document.querySelector('[data-testid="celda"]')!.textContent === 'No se encuentra')
    expect(texto()).toContain('Estado')
  })

  it('sin qBittorrent (no responde o no está configurado) la celda es un guion que dice «Sin datos de qBittorrent»', async () => {
    m.mockResolvedValue(respuesta(null, { disponible: false, mensaje: 'No se pudo conectar con qBittorrent' }))
    await montar(<Pagina job={subida()} />)
    await esperar(() => texto().includes('No se pudo conectar con qBittorrent'))
    const celda = document.querySelector('[data-testid="celda"]')!
    expect(celda.querySelector('[aria-hidden="true"]')!.textContent).toBe('—')
    expect(celda.querySelector('.solo-lector')!.textContent).toBe('Sin datos de qBittorrent')
    expect(texto()).toContain('Sin datos de qBittorrent')
  })

  it('si la petición falla (un 500, sin red) tampoco rompe: «Sin datos de qBittorrent»', async () => {
    m.mockRejectedValue(new Error('HTTP 500'))
    await montar(<Pagina job={subida()} />)
    await esperar(() => document.querySelector('[data-testid="celda"] .solo-lector')?.textContent === 'Sin datos de qBittorrent')
    expect(texto()).toContain('Esto no afecta a la subida')
  })

  it('una subida que no está completada no consulta ni enseña nada', async () => {
    m.mockResolvedValue(respuesta({}))
    await montar(<Pagina job={subida({ status: 'running' })} />)
    expect(document.querySelector('[data-testid="celda"]')!.textContent).toBe('—')
    expect(document.querySelector('[data-testid="seeding-bloque"]')).toBeNull()
  })

  it('«Actualizar» pide de nuevo saltándose la caché del servidor', async () => {
    m.mockResolvedValue(respuesta({}))
    await montar(<Pagina job={subida()} />)
    await esperar(() => boton('Actualizar') && !boton('Actualizar')!.disabled)
    m.mockResolvedValue(respuesta({ ratio: 2.25 }))
    await pulsar(boton('Actualizar'))
    await esperar(() => document.querySelector('[data-testid="celda"]')!.textContent === 'Sembrando · ratio 2,25')
    expect(m).toHaveBeenLastCalledWith(true)
    expect(m.mock.calls.slice(0, -1).every(c => c[0] === false)).toBe(true)
  })

  it('no hay reloj: pasado un buen rato no se vuelve a pedir nada solo', async () => {
    vi.useFakeTimers()
    try {
      m.mockResolvedValue(respuesta({}))
      await montar(<Pagina job={subida()} />)
      const antes = m.mock.calls.length
      await vi.advanceTimersByTimeAsync(10 * 60 * 1000)
      expect(m.mock.calls.length).toBe(antes)
    } finally {
      vi.useRealTimers()
    }
  })

  describe('Reenviar a qBittorrent', () => {
    const REENVIAR = 'Reenviar a qBittorrent'
    const noEncontrado = () => respuesta(null, { items: [{ job_id: 7, estado: 'no_encontrado', etiqueta: 'No se encuentra' }] })
    const qbitFallido = { phases: [{ key: 'qbittorrent_add', label: 'Subida a qBittorrent', status: 'failed', pct: 0 }] } as UploadJob['progress']

    it('sale cuando qBittorrent no tiene el torrent y se puede reenviar, con su ayuda', async () => {
      m.mockResolvedValue(noEncontrado())
      await montar(<Pagina job={subida({ puede_reenviar_qbit: true })} />)
      await esperar(() => boton(REENVIAR))
      expect(texto()).toContain('Ya está en eMuwarez, pero qBittorrent no lo tiene.')
      expect(boton(REENVIAR)!.getAttribute('aria-describedby')).toBe('reenvio-qbit-7')
    })

    it('sale cuando falló el añadido a qBittorrent aunque qBittorrent no responda', async () => {
      m.mockResolvedValue(respuesta(null, { disponible: false, mensaje: 'No se pudo conectar con qBittorrent' }))
      await montar(<Pagina job={subida({ puede_reenviar_qbit: true, progress: qbitFallido })} />)
      await esperar(() => boton(REENVIAR))
    })

    it('no sale si el torrent está en qBittorrent, si no se puede reenviar o si qBittorrent no responde', async () => {
      m.mockResolvedValue(respuesta({}))
      await montar(<Pagina job={subida({ puede_reenviar_qbit: true })} />)
      await esperar(() => texto().includes('Ratio'))
      expect(boton(REENVIAR)).toBeUndefined()
      await desmontar()

      m.mockResolvedValue(noEncontrado())
      await montar(<Pagina job={subida({ puede_reenviar_qbit: false, progress: qbitFallido })} />)
      await esperar(() => document.querySelector('[data-testid="celda"]')!.textContent === 'No se encuentra')
      expect(boton(REENVIAR)).toBeUndefined()
      await desmontar()

      m.mockResolvedValue(respuesta(null, { disponible: false, mensaje: 'No se pudo conectar con qBittorrent' }))
      await montar(<Pagina job={subida({ puede_reenviar_qbit: true })} />)
      await esperar(() => texto().includes('No se pudo conectar con qBittorrent'))
      expect(boton(REENVIAR)).toBeUndefined()
      expect(texto()).not.toContain('Ya está en eMuwarez')
    })

    it('al pulsarlo pide el reenvío, pasa a «Reenviando…» y avisa para recargar la subida', async () => {
      m.mockResolvedValue(noEncontrado())
      reenviar.mockResolvedValue({ message: 'Reenviando a qBittorrent.' })
      const onReenviado = vi.fn()
      await montar(<Pagina job={subida({ puede_reenviar_qbit: true })} onReenviado={onReenviado} />)
      await pulsar(await esperar(() => boton(REENVIAR)))
      expect(reenviar).toHaveBeenCalledWith(7)
      expect(onReenviado).toHaveBeenCalled()
      expect(boton('Reenviando…')!.disabled).toBe(true)
    })

    it('en curso enseña «Reenviando…» deshabilitado aunque ya no se pueda pedir otro', async () => {
      m.mockResolvedValue(noEncontrado())
      await montar(<Pagina job={subida({ puede_reenviar_qbit: false, reenvio_qbit_estado: 'en_curso' })} />)
      const b = await esperar(() => boton('Reenviando…'))
      expect(b.disabled).toBe(true)
      expect(boton(REENVIAR)).toBeUndefined()
    })

    it('un reenvío fallido lo dice y deja abrir el registro', async () => {
      m.mockResolvedValue(noEncontrado())
      const onVerRegistro = vi.fn()
      await montar(<Pagina job={subida({ puede_reenviar_qbit: true, reenvio_qbit_estado: 'fallido' })} onVerRegistro={onVerRegistro} />)
      await esperar(() => boton(REENVIAR))
      expect(texto()).toContain('El último reenvío a qBittorrent falló.')
      await pulsar(boton('Ver registro'))
      expect(onVerRegistro).toHaveBeenCalled()
    })

    it('uno bien lo dice y vuelve a leer el seeding al terminar', async () => {
      m.mockResolvedValue(noEncontrado())
      let cambiar: (j: UploadJob) => void = () => {}
      function Cambiante({ inicial }: { inicial: UploadJob }) {
        const [job, setJob] = useState(inicial)
        cambiar = setJob
        return <Pagina job={job} />
      }
      const job = subida({ puede_reenviar_qbit: false, reenvio_qbit_estado: 'en_curso' })
      await montar(<Cambiante inicial={job} />)
      await esperar(() => boton('Reenviando…') && !boton('Actualizar')!.disabled)
      const lecturas = m.mock.calls.length
      m.mockResolvedValue(respuesta({ estado: 'comprobando', etiqueta: 'Comprobando' }))
      await act(async () => cambiar({ ...job, reenvio_qbit_estado: 'ok', reenvio_qbit_at: '2026-11-11T10:00:00+00:00' }))
      await esperar(() => document.querySelector('[data-testid="celda"]')!.textContent?.startsWith('Comprobando'))
      expect(m.mock.calls.length).toBe(lecturas + 1)
      expect(texto()).toContain('Reenviado a qBittorrent el')
      expect(boton(REENVIAR)).toBeUndefined()
      expect(boton('Reenviando…')).toBeUndefined()
    })

    it('si el servidor no lo acepta enseña su motivo y el botón vuelve', async () => {
      m.mockResolvedValue(noEncontrado())
      reenviar.mockRejectedValue(new Error('Los ficheros de la subida ya no están en la carpeta de subidas.'))
      await montar(<Pagina job={subida({ puede_reenviar_qbit: true })} />)
      await pulsar(await esperar(() => boton(REENVIAR)))
      await esperar(() => texto().includes('ya no están en la carpeta de subidas'))
      expect(boton(REENVIAR)!.disabled).toBe(false)
    })
  })
})
