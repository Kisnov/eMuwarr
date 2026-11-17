import { useEffect, useState } from 'react'
import { getObjetivos, guardarObjetivos } from '../api/estadisticas'
import type { PuntoCuenta, RitmosCuenta } from '../types/api'
import { formatNumero } from '../utils/format'
import {
  antiguedadTexto, progreso, textoPlazo, TIPOS, type Objetivo, type ObjetivosCuenta as Datos, type TipoObjetivo,
} from '../utils/objetivos'

const ORDEN_TIPOS = Object.keys(TIPOS) as TipoObjetivo[]
const DECIMALES: Record<TipoObjetivo, number> = { subido: 2, ratio: 2, bonus: 0, seeding: 0, antiguedad: 1 }

const cifra = (tipo: TipoObjetivo, n: number) => {
  const unidad = TIPOS[tipo].unidad
  return `${formatNumero(n, DECIMALES[tipo])}${unidad ? ` ${unidad}` : ''}`
}

const hoyIso = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// Tus objetivos en eMuwarez y tu antigüedad (la fecha de alta la escribes tú: la API no la da). El progreso sale de la
// última instantánea, y cuándo llegas, de los ritmos de la última semana. Se piden cada vez que se entra (`visible`).
export function ObjetivosCuenta({ visible, ultimo, ritmos }: {
  visible: boolean; ultimo: PuntoCuenta | undefined; ritmos: RitmosCuenta | null
}) {
  const [datos, setDatos] = useState<Datos | null>(null)
  const [editando, setEditando] = useState<Datos | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [guardando, setGuardando] = useState(false)

  useEffect(() => {
    if (!visible) return
    let vigente = true
    getObjetivos().then(d => { if (vigente) setDatos(d) }).catch(() => { /* sin objetivos guardados no se enseña nada */ })
    return () => { vigente = false }
  }, [visible])

  if (!datos) return null
  const hoy = new Date()

  const guardar = async () => {
    if (!editando) return
    setGuardando(true)
    setError(null)
    try {
      setDatos(await guardarObjetivos({ ...editando, fecha_alta: editando.fecha_alta || null }))
      setEditando(null)
    } catch {
      setError('No se pudieron guardar. Revisa que cada cifra sea mayor que 0 y la fecha no sea futura.')
    } finally {
      setGuardando(false)
    }
  }

  const cambiar = (i: number, o: Partial<Objetivo>) =>
    setEditando(e => e && { ...e, objetivos: e.objetivos.map((x, j) => (j === i ? { ...x, ...o } : x)) })
  const libres = (e: Datos) => ORDEN_TIPOS.filter(t => !e.objetivos.some(o => o.tipo === t))

  return (
    <section className="est-objetivos" aria-labelledby="est-grupo-objetivos">
      <div className="est-objetivos-cab">
        <h2 className="est-grupo-titulo" id="est-grupo-objetivos">Objetivos</h2>
        {!editando && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditando(datos)}>
            {datos.objetivos.length || datos.fecha_alta ? 'Editar' : 'Añadir objetivos'}
          </button>
        )}
      </div>

      {editando ? (
        <form className="est-objetivos-form" onSubmit={e => { e.preventDefault(); void guardar() }}>
          <label className="est-objetivos-fila">
            <span>Fecha de alta en eMuwarez</span>
            <input type="date" className="form-input" aria-label="Fecha de alta en eMuwarez" max={hoyIso()} value={editando.fecha_alta ?? ''}
              onChange={e => setEditando({ ...editando, fecha_alta: e.target.value || null })} />
          </label>
          {editando.objetivos.map((o, i) => (
            <div className="est-objetivos-fila" key={o.tipo}>
              <select className="form-input form-select" aria-label="Tipo de objetivo" value={o.tipo}
                onChange={e => cambiar(i, { tipo: e.target.value as TipoObjetivo })}>
                {[o.tipo, ...libres(editando)].map(t => <option key={t} value={t}>{TIPOS[t].nombre}</option>)}
              </select>
              <input type="number" className="form-input" min="0" step="any" aria-label={`Objetivo de ${TIPOS[o.tipo].nombre}`}
                value={Number.isNaN(o.valor) ? '' : o.valor} onChange={e => cambiar(i, { valor: e.target.valueAsNumber })} />
              <span className="est-objetivos-unidad">{TIPOS[o.tipo].unidad}</span>
              <button type="button" className="btn btn-ghost btn-sm" aria-label={`Quitar el objetivo de ${TIPOS[o.tipo].nombre}`}
                onClick={() => setEditando({ ...editando, objetivos: editando.objetivos.filter((_, j) => j !== i) })}>Quitar</button>
            </div>
          ))}
          {error && <p className="est-nota est-aviso" role="alert">{error}</p>}
          <div className="est-objetivos-botones">
            {libres(editando).length > 0 && (
              <button type="button" className="btn btn-secondary btn-sm"
                onClick={() => setEditando({ ...editando, objetivos: [...editando.objetivos, { tipo: libres(editando)[0], valor: NaN }] })}>
                Añadir objetivo
              </button>
            )}
            <button type="submit" className="btn btn-primary btn-sm" disabled={guardando}>Guardar</button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setEditando(null); setError(null) }}>Cancelar</button>
          </div>
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
                      {p.actual === null ? '—' : cifra(o.tipo, p.actual)} / {cifra(o.tipo, o.valor)}
                      <span className={p.cumplido ? 'est-objetivo-cumplido' : 'est-objetivo-plazo'}> · {plazo}</span>
                    </span>
                  </div>
                  <div className="progress-bar" role="progressbar" aria-label={`${TIPOS[o.tipo].nombre}: progreso`}
                    aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((p.fraccion ?? 0) * 100)}>
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
