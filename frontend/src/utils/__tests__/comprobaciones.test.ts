import { describe, expect, it } from 'vitest'
import type { UploadCheck } from '../../api/uploads'
import { motivoDelBloqueo, segunDestinos } from '../comprobaciones'

const check = (id: string, label: string, status: UploadCheck['status']): UploadCheck => ({ id, label, status, message: '' })

describe('motivoDelBloqueo', () => {
  it('nombra la primera comprobación fallida, en minúscula si empieza por mayúscula', () => {
    expect(motivoDelBloqueo([check('output', 'Directorio de salida escribible', 'ok'), check('spanish', 'No tiene castellano', 'fail')]))
      .toBe('No se puede subir: no tiene castellano')
  })

  it('respeta un nombre que empieza en minúscula y cuenta las demás fallidas', () => {
    expect(motivoDelBloqueo([
      check('emuwarez', 'eMuwarez no disponible', 'fail'),
      check('qbittorrent', 'qBittorrent no conectado', 'skip'),
      check('imghosts', 'Repo de imágenes no disponible', 'fail'),
      check('output', 'Directorio de salida no escribible', 'fail'),
    ])).toBe('No se puede subir: eMuwarez no disponible (y 2 más)')
  })

  it('sin ninguna fallida, o sin comprobaciones, la frase de siempre', () => {
    expect(motivoDelBloqueo([check('output', 'Directorio de salida escribible', 'ok')])).toBe('La subida no se puede realizar')
    expect(motivoDelBloqueo(undefined)).toBe('La subida no se puede realizar')
  })
})

describe('segunDestinos', () => {
  const sinEmu = { ready: false, checks: [check('imghosts', 'Repo de imágenes disponible', 'ok'),
    check('emuwarez', 'eMuwarez no disponible', 'fail')] }

  it('sin eMuwarez entre los destinos, su fallo no bloquea', () => {
    const r = segunDestinos(sinEmu, false)!
    expect(r.ready).toBe(true)
    expect(r.checks.find(c => c.id === 'emuwarez')!.status).toBe('skip')
  })

  it('con eMuwarez elegido, bloquea como siempre', () => {
    expect(segunDestinos(sinEmu, true)).toBe(sinEmu)
  })

  it('otra comprobación fallida sigue bloqueando aunque eMuwarez no sea destino', () => {
    const r = segunDestinos({ ready: false, checks: [check('spanish', 'No tiene castellano', 'fail'),
      check('emuwarez', 'eMuwarez no disponible', 'fail')] }, false)!
    expect(r.ready).toBe(false)
  })

  it('sin la de eMuwarez (la pasada se cortó antes) no toca nada', () => {
    const cortada = { ready: false, checks: [check('output', 'Directorio de salida no escribible', 'fail')] }
    expect(segunDestinos(cortada, false)).toBe(cortada)
  })
})
