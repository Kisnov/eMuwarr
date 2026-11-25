import { useEffect, useState } from 'react'
import { getSubirTambien, subirTambien, type SubirTambienCandidatas, type SubirTambienRespuesta } from '../api/uploads'
import type { UploadJob } from '../types/api'
import { Modal } from './Modal'
import { formatDate } from '../utils/format'
import { MAX_ELEMENTOS_LOTE } from '../utils/lote'

// «Subir también a…» (services/subir_tambien.py): lo que ya está en un tracker se sube a otro con el mismo fichero de la
// carpeta de subidas, el mismo nombre y las mismas piezas. Antes de encolar, el servidor mira en qBittorrent que el
// torrent de origen está entero desde ese fichero.
export const ayudaSubirTambien = (origen: string) => `Con el mismo fichero, el mismo nombre y las mismas piezas que en ${origen}: `
  + 'no se prepara ni se hashea de nuevo, y qBittorrent siembra los dos torrents desde el mismo fichero.'

const error = (e: unknown, porDefecto: string) => (e instanceof Error && e.message ? e.message : porDefecto)

function textoResultado(r: SubirTambienRespuesta, nombres: Record<string, string>): string[] {
  return r.resultados.map(x => {
    const t = nombres[x.tracker] ?? x.tracker
    return x.status === 'queued'
      ? `Subida #${x.nuevo_job_id} a ${t} en cola.`
      : `No se sube #${x.job_id} a ${t}: ${x.motivo ?? 'no se pudo encolar'}`
  })
}

// En el detalle de una subida completada: un botón por cada otro tracker que la acepta.
export function SubirTambienBloque({ job, onEncolado }: { job: UploadJob; onEncolado?: () => void }) {
  const destinos = job.subir_tambien_a ?? []
  const avisos = job.subir_tambien_avisos ?? []
  const [enviando, setEnviando] = useState<string | null>(null)
  const [mensajes, setMensajes] = useState<string[]>([])
  useEffect(() => { setMensajes([]); setEnviando(null) }, [job.id])
  if (destinos.length === 0 && avisos.length === 0 && mensajes.length === 0) return null
  const nombres = Object.fromEntries(destinos.map(t => [t.id, t.nombre]))
  const subir = async (tracker: string) => {
    setEnviando(tracker)
    try {
      const r = await subirTambien([job.id!], [tracker])
      setMensajes(textoResultado(r, nombres))
      if (r.encolados) onEncolado?.()
    } catch (e) {
      setMensajes([error(e, 'No se pudo encolar la subida.')])
    } finally {
      setEnviando(null)
    }
  }
  return (
    <div className="dsub-panel" data-testid="subir-tambien">
      <h2 className="dsub-panel-titulo">Subir también a otros trackers</h2>
      <div className="detail-card dsub-caja">
        <p className="sub-muted sub-t12" id={`subir-tambien-${job.id}`}>{ayudaSubirTambien(job.tracker_nombre || 'eMuwarez')}</p>
        <div className="sub-fila">
          {destinos.map(t => (
            <button key={t.id} type="button" className="btn btn-secondary btn-sm" aria-describedby={`subir-tambien-${job.id}`}
              aria-disabled={enviando !== null} onClick={() => { if (enviando === null) subir(t.id) }}>
              {enviando === t.id ? 'Encolando…' : `Subir también a ${t.nombre}`}
            </button>
          ))}
        </div>
        {avisos.map(a => <p key={a} className="sub-muted sub-t12">{a}</p>)}
        <div aria-live="polite" data-testid="subir-tambien-resultado">
          {mensajes.map(m => <p key={m} className="sub-t12">{m}</p>)}
        </div>
      </div>
    </div>
  )
}

// En Subidas: elegir varias subidas completadas y los trackers (a su propio tracker no se sube). Solo lo elegido a
// mano; cada par es una subida de la cola, todas en un lote, y van saliendo según «Subidas en paralelo».
export function SubirTambienModal({ inicial, onClose, onEncolado }: {
  // Abierto desde una fila: esa subida ya marcada, y solo los trackers donde le falta.
  inicial?: number
  onClose: () => void
  // Con el lote creado (o null si solo hubo una), para filtrar la tabla por él.
  onEncolado: (loteId: string | null) => void
}) {
  const [datos, setDatos] = useState<SubirTambienCandidatas | null>(null)
  const [fallo, setFallo] = useState<string | null>(null)
  const [elegidas, setElegidas] = useState<Set<number>>(new Set(inicial ? [inicial] : []))
  const [trackers, setTrackers] = useState<Set<string>>(new Set())
  const [enviando, setEnviando] = useState(false)
  const [mensajes, setMensajes] = useState<string[]>([])
  // Tras encolar, la lista se pide otra vez: lo recién encolado ya no se ofrece.
  const [vez, setVez] = useState(0)

  useEffect(() => {
    let vigente = true
    getSubirTambien().then(
      d => {
        if (!vigente) return
        setDatos(d)
        if (vez > 0) {
          // Un refresco: lo marcado mientras tanto se queda, salvo lo que ya no se ofrece.
          setElegidas(e => new Set([...e].filter(id => d.subidas.some(s => s.job_id === id))))
          return
        }
        const deLaFila = d.subidas.find(s => s.job_id === inicial)
        if (!deLaFila) setElegidas(new Set())  // ya no se ofrece: no queda marcada sin casilla
        setTrackers(new Set((deLaFila?.trackers ?? d.trackers).map(t => t.id)))
      },
      e => { if (vigente && vez === 0) setFallo(error(e, 'No se pudieron cargar las subidas.')) },
    )
    return () => { vigente = false }
  }, [inicial, vez])

  const alternar = <T,>(conjunto: Set<T>, valor: T) => {
    const nuevo = new Set(conjunto)
    if (nuevo.has(valor)) nuevo.delete(valor)
    else nuevo.add(valor)
    return nuevo
  }
  const nombres = Object.fromEntries((datos?.trackers ?? []).map(t => [t.id, t.nombre]))
  // El servidor rechaza entera (422) una petición con más subidas que un lote: al llegar al tope no se marcan más.
  const lleno = elegidas.size >= MAX_ELEMENTOS_LOTE
  const puede = elegidas.size > 0 && trackers.size > 0 && !enviando
  const enviar = async () => {
    if (!puede) return
    setEnviando(true)
    setMensajes([])
    try {
      const r = await subirTambien([...elegidas], [...trackers])
      setMensajes(textoResultado(r, nombres))
      if (r.encolados) {
        setElegidas(new Set())
        setVez(n => n + 1)
        onEncolado(r.lote_id)
      }
    } catch (e) {
      setMensajes([error(e, 'No se pudo encolar.')])
    } finally {
      setEnviando(false)
    }
  }

  return (
    <Modal titulo="Subir también a otros trackers" onClose={onClose} className="sub-modal-560">
      <p className="sub-muted sub-t12">{ayudaSubirTambien('el tracker donde ya está')} Elige las subidas: se suben de pocas en pocas, según «Subidas en paralelo».</p>
      {fallo ? <p role="alert">{fallo}</p> : !datos ? <p className="sub-muted">Cargando…</p>
        : datos.trackers.length === 0 ? (
          <p>Pon la clave de N.O.B.S o de Milnueve en Configuración → Otros trackers para subir también a ellos.</p>
        ) : datos.subidas.length === 0 ? (
          <p>No hay subidas completadas que falten en otro tracker.</p>
        ) : (
          <>
            <fieldset className="subir-tambien-trackers">
              <legend>Trackers</legend>
              {datos.trackers.map(t => (
                <label key={t.id}>
                  <input type="checkbox" checked={trackers.has(t.id)} onChange={() => setTrackers(s => alternar(s, t.id))} />
                  {' '}{t.nombre}
                </label>
              ))}
            </fieldset>
            {datos.avisos?.map(a => <p key={a} className="sub-muted sub-t12">{a}</p>)}
            <ul className="subir-tambien-lista" aria-label="Subidas completadas">
              {datos.subidas.map(s => (
                <li key={s.job_id}>
                  <label>
                    <input type="checkbox" checked={elegidas.has(s.job_id)} disabled={lleno && !elegidas.has(s.job_id)}
                      onChange={() => setElegidas(c => alternar(c, s.job_id))} />
                    {' '}<span>{s.title || s.nombre}</span>
                    <span className="sub-muted sub-t11"> · #{s.job_id}{s.finished_at ? ` · ${formatDate(s.finished_at)}` : ''}
                      {' '}· en {s.origen.nombre} · falta en {s.trackers.map(t => t.nombre).join(', ')}
                      {s.ya_lo_tienen?.length ? ` · ${s.ya_lo_tienen.join(' · ')}` : ''}</span>
                  </label>
                </li>
              ))}
            </ul>
            <p className="sub-muted sub-t12" role="status">
              {elegidas.size} de {datos.subidas.length} · máximo {MAX_ELEMENTOS_LOTE} a la vez
            </p>
          </>
        )}
      <div aria-live="polite" data-testid="subir-tambien-resultado">
        {mensajes.map(m => <p key={m} className="sub-t12">{m}</p>)}
      </div>
      <div className="sub-fila">
        <button type="button" className="btn btn-secondary" onClick={onClose}>Cerrar</button>
        <button type="button" className="btn btn-primary" aria-disabled={!puede} onClick={enviar}>
          {enviando ? 'Encolando…' : elegidas.size ? `Subir ${elegidas.size} ${elegidas.size === 1 ? 'subida' : 'subidas'}` : 'Subir'}
        </button>
      </div>
    </Modal>
  )
}
