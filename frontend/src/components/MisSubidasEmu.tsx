import { useEffect, useMemo, useState } from 'react'
import { getMisSubidas } from '../api/estadisticas'
import { EMU_URL } from '../constants/emu'
import type { MiTorrent, MisTorrents } from '../types/api'
import { formatDate, formatNumero, SIN_DATO, timeAgo } from '../utils/format'
import { StatCard } from './StatCard'

const ORDENES = {
  completados: { texto: 'Más completadas', comparar: (a: MiTorrent, b: MiTorrent) => (b.completados ?? 0) - (a.completados ?? 0) },
  semana: { texto: 'Más completadas esta semana', comparar: (a: MiTorrent, b: MiTorrent) => (b.completados_semana ?? -1) - (a.completados_semana ?? -1) },
  seeders: { texto: 'Menos seeders', comparar: (a: MiTorrent, b: MiTorrent) => (a.seeders ?? 0) - (b.seeders ?? 0) },
  recientes: { texto: 'Más recientes', comparar: (a: MiTorrent, b: MiTorrent) => (b.subido_at ?? '').localeCompare(a.subido_at ?? '') },
} as const
type Orden = keyof typeof ORDENES

const cifra = (n: number | null) => (n === null ? SIN_DATO : formatNumero(n))

// Tus subidas en eMuwarez: lo que guardó la última lectura (la tarea «Leer tus subidas en eMuwarez», cada 6 horas). Se
// pide cada vez que se entra en Estadísticas (`visible`); no llama al tracker.
export function MisSubidasEmu({ visible }: { visible: boolean }) {
  const [datos, setDatos] = useState<MisTorrents | null>(null)
  const [fallo, setFallo] = useState(false)
  const [orden, setOrden] = useState<Orden>('completados')
  const [intento, setIntento] = useState(0)

  useEffect(() => {
    if (!visible) return
    let vigente = true
    getMisSubidas().then(d => { if (vigente) { setDatos(d); setFallo(false) } }, () => { if (vigente) setFallo(true) })
    return () => { vigente = false }
  }, [visible, intento])

  const filas = useMemo(() => (datos ? [...datos.torrents].sort(ORDENES[orden].comparar) : []), [datos, orden])

  const aviso = fallo && (
    <p className="est-nota est-aviso" role="status">
      No se pudieron cargar tus subidas en eMuwarez{datos ? '; se enseñan las de la última vez' : ''}.{' '}
      <button type="button" className="btn btn-ghost btn-sm" onClick={() => setIntento(n => n + 1)}>Reintentar</button>
    </p>
  )
  if (!datos) return aviso || null
  const t = datos.totales
  return (
    <section aria-labelledby="est-grupo-mis-subidas">
      <h2 className="est-grupo-titulo" id="est-grupo-mis-subidas">Tus subidas en eMuwarez</h2>
      {aviso}
      {datos.leido_at === null ? (
        <p className="est-nota">Aún no se han leído: la tarea «Leer tus subidas en eMuwarez» lo hace cada 6 horas, y en Tareas se puede ejecutar ahora.</p>
      ) : (
        <>
          <div className="stat-cards-row" role="group" aria-label="Totales de tus subidas en eMuwarez">
            <StatCard label="Subidas" value={formatNumero(t.subidas)} />
            <StatCard label="Completadas" value={formatNumero(t.completados)} info="Veces que alguien ha terminado de bajar alguna de tus subidas." />
            <StatCard label="Completadas esta semana" value={cifra(t.completados_semana)}
              info="En los 7 días anteriores a la última lectura. Sale a partir de una semana de lecturas: antes, solo cuentan las subidas de esa semana." />
            <StatCard label="Seeders" value={formatNumero(t.seeders)} info="La suma de los seeders de todas tus subidas." />
            <StatCard label="Sin seeders" value={formatNumero(t.sin_seeders)} accentColor={t.sin_seeders ? 'var(--color-warning)' : undefined}
              info="Subidas que nadie siembra ahora mismo: si las tienes, vuelve a sembrarlas." />
          </div>
          <div className="est-subidas-cab">
            <p className="est-nota">
              Leídas {timeAgo(datos.leido_at)}, cada 6 horas. Las subidas anónimas no salen: eMuwarez no las da al buscar por usuario.
            </p>
            <select className="form-input form-select est-subidas-orden" aria-label="Orden de tus subidas" value={orden}
              onChange={e => setOrden(e.target.value as Orden)}>
              {Object.entries(ORDENES).map(([valor, o]) => <option key={valor} value={valor}>{o.texto}</option>)}
            </select>
          </div>
          {filas.length === 0 ? (
            <p className="est-nota">No hay ninguna subida tuya en eMuwarez que no sea anónima.</p>
          ) : (
            <div className="est-tabla est-tabla-marco" tabIndex={0} role="region" aria-label="Tus subidas en eMuwarez">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Torrent</th><th scope="col">Seeders</th><th scope="col">Leechers</th>
                    <th scope="col">Completadas</th><th scope="col">Esta semana</th><th scope="col">Subido</th>
                  </tr>
                </thead>
                <tbody>
                  {filas.map(f => (
                    <tr key={f.torrent_id}>
                      <th scope="row">
                        <a href={`${EMU_URL}/torrents/${f.torrent_id}`} target="_blank" rel="noreferrer">{f.nombre}</a>
                      </th>
                      <td className={f.seeders === 0 ? 'est-sin-seeders' : undefined}>{cifra(f.seeders)}</td>
                      <td>{cifra(f.leechers)}</td>
                      <td>{cifra(f.completados)}</td>
                      <td>{cifra(f.completados_semana)}</td>
                      <td>{formatDate(f.subido_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  )
}
