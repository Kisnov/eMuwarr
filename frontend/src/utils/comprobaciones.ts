import type { UploadCheck } from '../api/uploads'

// Por qué no se puede subir: la primera comprobación fallida y cuántas más hay. Sin ninguna fallida (el backend dijo
// que no está listo sin decir por qué), la frase de siempre.
export function motivoDelBloqueo(checks: UploadCheck[] | undefined): string {
  const fallidas = (checks ?? []).filter(c => c.status === 'fail')
  if (fallidas.length === 0) return 'La subida no se puede realizar'
  const { label } = fallidas[0]
  const otras = fallidas.length - 1
  return `No se puede subir: ${label[0].toLowerCase()}${label.slice(1)}${otras > 0 ? ` (y ${otras} más)` : ''}`
}
