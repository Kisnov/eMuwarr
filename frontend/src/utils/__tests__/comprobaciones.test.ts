import { describe, expect, it } from 'vitest'
import type { UploadCheck } from '../../api/uploads'
import { motivoDelBloqueo } from '../comprobaciones'

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
