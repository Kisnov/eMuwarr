import { useEffect, useRef, useState } from 'react'
import { getObjetivos, guardarObjetivos } from '../api/estadisticas'
import type { PuntoCuenta, RitmosCuenta } from '../types/api'
import { formatNumero } from '../utils/format'
import {
  antiguedadTexto, progreso, textoPlazo, TIPOS, type Objetivo, type ObjetivosCuenta as Datos, type TipoObjetivo,
} from '../utils/objetivos'

const ORDEN_TIPOS = Object.keys(TIPOS) as TipoObjetivo[]
const DECIMALES: Record<TipoObjetivo, number> = { subido: 2, ratio: 2, bonus: 0, seeding: 0, antiguedad: 1 }

const cifra = (tipo: TipoObjetivo, n: number, extra = 0) => {
  const unidad = TIPOS[tipo].unidad
  return `${formatNumero(n, DECIMALES[tipo] + extra)}${unidad ? ` ${unidad}` : ''}`
}

/** «actual / meta»; si el redondeo las iguala sin estar cumplida (1,231 y 1,234 → «1,23 / 1,23»), con los decimales
 * que hagan falta para distinguirlas (hasta 12). */
function cifras(tipo: TipoObjetivo, actual: number | null, meta: number, cumplido: boolean): string {
  if (actual === null) return `— / ${cifra(tipo, meta)}`
  let extra = 0
  while (!cumplido && extra < 12 && cifra(tipo, actual, extra) === cifra(tipo, meta, extra)) extra++
  return `${cifra(tipo, actual, extra)} / ${cifra(tipo, meta, extra)}`
}

const hoyIso = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// En el editor cada fila lleva un id propio: con el tipo como clave, cambiar el tipo rehacía el selector y perdía el foco.
type Fila = Objetivo & { id: number }
type Borrador = { fecha_alta: string | null; objetivos: Fila[] }
let siguienteId = 0
const borradorDe = (d: Datos): Borrador => ({ ...d, objetivos: d.objetivos.map(o => ({ ...o, id: ++siguienteId })) })

// Tus objetivos en eMuwarez y tu antigüedad (la fecha de alta la escribes tú: la API no la da). El progreso sale de la
// última instantánea, y cuándo llegas, de los ritmos de la última semana. Se piden cada vez que se entra (`visible`).
export function ObjetivosCuenta({ visible, ultimo, ritmos }: {
  visible: boolean; ultimo: PuntoCuenta | undefined; ritmos: RitmosCuenta | null
}) {
  const [datos, setDatos] = useState<Datos | null>(null)
  const [falloCarga, setFalloCarga] = useState(false)
  const [intento, setIntento] = useState(0)
  const [editando, setEditando] = useState<Borrador | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [guardando, setGuardando] = useState(false)
  // Cada guardado cambia la generación: una lectura que salió antes y llega después no pisa lo recién guardado.
  const generacion = useRef(0)

  useEffect(() => {
    if (!visible) return
    let vigente = true
    const gen = generacion.current
    const vale = () => vigente && gen === generacion.current
    getObjetivos().then(
      d => { if (vale()) { setDatos(d); setFalloCarga(false) } },
      () => { if (vale()) setFalloCarga(true) },
    )
    return () => { vigente = false }
  }, [visible, intento])

  if (!datos && !falloCarga) return null
  const hoy = new Date()

  const guardar = async () => {
    if (!editando) return
    setGuardando(true)
    setError(null)
    generacion.current++
    try {
      const guardados = await guardarObjetivos({
        fecha_alta: editando.fecha_alta || null,
        objetivos: editando.objetivos.map(({ tipo, valor }) => ({ tipo, valor })),
      })
      generacion.current++   // también lo que se pidió mientras se guardaba (con «Reintentar») llega tarde
      setDatos(guardados)
      setEditando(null)
    } catch {
      setError('No se pudieron guardar. Revisa que cada cifra sea mayor que 0 y la fecha no sea futura.')
    } finally {
      setGuardando(false)
    }
  }

  const cambiar = (id: number, o: Partial<Objetivo>) =>
    setEditando(e => e && { ...e, objetivos: e.objetivos.map(x => (x.id === id ? { ...x, ...o } : x)) })
  const libres = (e: Borrador) => ORDEN_TIPOS.filter(t => !e.objetivos.some(o => o.tipo === t))

  return (
    <section className="est-objetivos" aria-labelledby="est-grupo-objetivos">
      <div className="est-objetivos-cab">
        <h2 className="est-grupo-titulo" id="est-grupo-objetivos">Objetivos</h2>
        {datos && !editando && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditando(borradorDe(datos))}>
            {datos.objetivos.length || datos.fecha_alta ? 'Editar' : 'Añadir objetivos'}
          </button>
        )}
      </div>

      {falloCarga && (
        <p className="est-nota est-aviso" role="status">
          No se pudieron cargar tus objetivos{datos ? '; se enseñan los de la última vez' : ''}.{' '}
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setIntento(n => n + 1)}>Reintentar</button>
        </p>
      )}

      {!datos ? null : editando ? (
        <form className="est-objetivos-form" onSubmit={e => { e.preventDefault(); void guardar() }}>
          {/* Mientras se guarda no se toca nada: lo que se cambiara después de enviar se perdería al cerrar. */}
          <fieldset className="est-objetivos-campos" disabled={guardando}>
            <label className="est-objetivos-fila">
              <span>Fecha de alta en eMuwarez</span>
              <input type="date" className="form-input" aria-label="Fecha de alta en eMuwarez" max={hoyIso()}
                value={editando.fecha_alta ?? ''} onChange={e => setEditando({ ...editando, fecha_alta: e.target.value || null })} />
            </label>
            {editando.objetivos.map(o => (
              <div className="est-objetivos-fila" key={o.id}>
                <select className="form-input form-select" aria-label="Tipo de objetivo" value={o.tipo}
                  onChange={e => cambiar(o.id, { tipo: e.target.value as TipoObjetivo })}>
                  {[o.tipo, ...libres(editando)].map(t => <option key={t} value={t}>{TIPOS[t].nombre}</option>)}
                </select>
                <input type="number" className="form-input" min="0" step="any" aria-label={`Objetivo de ${TIPOS[o.tipo].nombre}`}
                  value={Number.isNaN(o.valor) ? '' : o.valor} onChange={e => cambiar(o.id, { valor: e.target.valueAsNumber })} />
                <span className="est-objetivos-unidad">{TIPOS[o.tipo].unidad}</span>
                <button type="button" className="btn btn-ghost btn-sm" aria-label={`Quitar el objetivo de ${TIPOS[o.tipo].nombre}`}
                  onClick={() => setEditando({ ...editando, objetivos: editando.objetivos.filter(x => x.id !== o.id) })}>Quitar</button>
              </div>
            ))}
            {error && <p className="est-nota est-aviso" role="alert">{error}</p>}
            <div className="est-objetivos-botones">
              {libres(editando).length > 0 && (
                <button type="button" className="btn btn-secondary btn-sm"
                  onClick={() => setEditando({ ...editando, objetivos: [...editando.objetivos, { tipo: libres(editando)[0], valor: NaN, id: ++siguienteId }] })}>
                  Añadir objetivo
                </button>
              )}
              <button type="submit" className="btn btn-primary btn-sm">Guardar</button>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setEditando(null); setError(null) }}>Cancelar</button>
            </div>
          </fieldset>
        </form>
      ) : (
        <>
          {datos.fecha_alta && (
            <p className="est-nota">Antigüedad: <strong>{antiguedadTexto(datos.fecha_alta, hoy)}</strong>.</p>
          )}
          {datos.objetivos.length === 0 && !datos.fecha_alta && (
            <p className="est-nota">Ponte una meta (subido, ratio, bonus, seeding o antigüedad) y aquí verás cuánto te falta y cuándo llegas a este ritmo.</p>
          )}
          <ul className="est-objetivos-lista">
            {datos.objetivos.map(o => {
              const p = progreso(o, ultimo, ritmos, datos.fecha_alta, hoy)
              const plazo = p.cumplido ? 'Cumplido' : p.dias !== null ? textoPlazo(p.dias)
                : o.tipo === 'antiguedad' && !datos.fecha_alta ? 'Falta tu fecha de alta' : 'Sin fecha a este ritmo'
              return (
                <li key={o.tipo}>
                  <div className="est-objetivo-texto">
                    <span className="est-objetivo-nombre">{TIPOS[o.tipo].nombre}</span>
                    <span className="est-objetivo-cifras">
                      {cifras(o.tipo, p.actual, o.valor, p.cumplido)}
                      <span className={p.cumplido ? 'est-objetivo-cumplido' : 'est-objetivo-plazo'}> · {plazo}</span>
                    </span>
                  </div>
                  {/* Sin cifra actual no se anuncia un 0 %: la barra queda vacía y sin valor. El avance va en scaleX,
                      como en el resto de .progress-bar (dashboard.css). */}
                  <div className="progress-bar" role="progressbar" aria-label={`${TIPOS[o.tipo].nombre}: progreso`}
                    aria-valuemin={0} aria-valuemax={100}
                    aria-valuenow={p.fraccion === null ? undefined : Math.round(p.fraccion * 100)}>
                    <div className="progress-fill" style={{ transform: `scaleX(${p.fraccion ?? 0})` }} />
                  </div>
                </li>
              )
            })}
          </ul>
        </>
      )}
    </section>
  )
}
