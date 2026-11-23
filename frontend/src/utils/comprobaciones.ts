import type { UploadCheck, UploadChecksResult } from '../api/uploads'

// Por qué no se puede subir: la primera comprobación fallida y cuántas más hay. Sin ninguna fallida (el backend dijo
// que no está listo sin decir por qué), la frase de siempre.
export function motivoDelBloqueo(checks: UploadCheck[] | undefined): string {
  const fallidas = (checks ?? []).filter(c => c.status === 'fail')
  if (fallidas.length === 0) return 'La subida no se puede realizar'
  const { label } = fallidas[0]
  const otras = fallidas.length - 1
  return `No se puede subir: ${label[0].toLowerCase()}${label.slice(1)}${otras > 0 ? ` (y ${otras} más)` : ''}`
}

// «eMuwarez disponible» va en todas las pasadas (alcance `all`, la última), pero solo puede bloquear si eMuwarez es
// destino: subiendo solo a N.O.B.S o Milnueve, su fallo cuenta como omitida y `ready` sale de las demás. Las demás
// llegan todas, porque la de eMuwarez solo corre si ninguna anterior falló.
export function segunDestinos(r: UploadChecksResult | null, conEmu: boolean): UploadChecksResult | null {
  if (!r || conEmu || !r.checks.some(c => c.id === 'emuwarez' && c.status === 'fail')) return r
  const checks = r.checks.map(c => c.id === 'emuwarez'
    ? { ...c, status: 'skip' as const, message: 'eMuwarez no es destino de esta subida (se omite)' }
    : c)
  return { ...r, checks, ready: checks.every(c => c.status !== 'fail') }
}
