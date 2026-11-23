// Paso 1 de «Subir por lotes»: qué entra. Cada serie marcada sale como sus temporadas (packs). Lo que no se puede
// subir (no está en disco, sin castellano, una temporada que no se puede subir como pack) sale desmarcado y sin
// casilla que marcar, con su motivo. Con más de un tracker con clave, en vez del castellano, que lo admita alguno. Desde el Panel cada fila dice qué petición cubre y, si cubre varias (dos
// peticiones sobre el mismo elemento), deja elegir cuál se enlaza: el servidor solo enlaza una por subida.
import type { ReglasConNombre } from '../../api/uploads'
import type { LibraryItemResponse } from '../../types/api'
import { admiten, resumenPorTracker } from '../../utils/elegibilidad'
import { formatNumero } from '../../utils/format'
import { listDisplayTitle, tituloEnFicha } from '../../utils/libraryItem'
import { MAX_ELEMENTOS_LOTE, razonNoSubible } from '../../utils/lote'
import { IconoAviso } from '../Iconos'

const calidad = (item: LibraryItemResponse) => [item.resolution, item.source].filter(Boolean).join(' ')

const TIPO: Record<string, string> = { movie: 'Película', season: 'Temporada', episode: 'Capítulo' }

const NINGUNA: readonly number[] = []
const SIN_ELEGIBILIDAD: ReadonlyMap<number, readonly ReglasConNombre[]> = new Map()

/** El título de la fila en dos partes: el nombre de la serie y lo que distingue a la temporada («Temporada 1»), para
 *  que en un móvil se recorte solo el nombre y la parte siga a la vista. Una película no tiene parte. */
function partesDelTitulo(item: LibraryItemResponse): { nombre: string; parte: string | null } {
  const entero = listDisplayTitle(item)
  if (item.item_type !== 'season') return { nombre: entero, parte: null }
  const parte = tituloEnFicha(item)
  return entero.endsWith(` - ${parte}`)
    ? { nombre: entero.slice(0, -(parte.length + 3)), parte }
    : { nombre: entero, parte: null }
}

export function PasoContenidoLote({
  elementos, marcadas, onAlternar, onContinuar, expandiendo = null, avisos = [], peticiones, peticionDe,
  onElegirPeticion, elegibilidad = SIN_ELEGIBILIDAD, comprobandoIdiomas = false,
}: {
  elementos: readonly LibraryItemResponse[]
  marcadas: ReadonlySet<number>
  // Con más de un tracker, qué admite cada uno (/upload/elegibilidad).
  elegibilidad?: ReadonlyMap<number, readonly ReglasConNombre[]>
  // Aún no han respondido todos: no se continúa, que de ellos salen los trackers de entrada.
  comprobandoIdiomas?: boolean
  onAlternar: (id: number) => void
  onContinuar: () => void
  // Mientras se cargan las temporadas de las series marcadas: cuántas series van de cuántas.
  expandiendo?: { hechas: number; series: number } | null
  // Una serie sin temporadas o que no se pudo cargar, o una petición cuya temporada o capítulo no está.
  avisos?: readonly string[]
  // Desde el Panel: las peticiones que cubre cada elemento, la que se enlaza y cómo elegir otra.
  peticiones?: ReadonlyMap<number, readonly number[]>
  peticionDe?: (id: number) => number | undefined
  onElegirPeticion?: (id: number, peticion: number) => void
}) {
  const razon = (e: LibraryItemResponse) => {
    const r = elegibilidad.get(e.id)
    return razonNoSubible(e, r && admiten(r))
  }
  const n = elementos.filter(e => marcadas.has(e.id) && !razon(e)).length
  const lleno = n >= MAX_ELEMENTOS_LOTE
  if (expandiendo) {
    return (
      <div className="modal-body">
        <p className="lote-cargando" role="status">
          <span className="spinner asist-spinner-14" /> Cargando las temporadas de las series marcadas
          ({formatNumero(Math.min(expandiendo.hechas + 1, expandiendo.series))} de {formatNumero(expandiendo.series)})…
        </p>
      </div>
    )
  }
  return (
    <div className="modal-body">
      <p className="lote-intro text-muted">
        Se subirán en este orden, con el nombre que genere la nomenclatura para cada una; una serie, como el pack de
        cada temporada. Para cambiar un nombre, elegir pistas o subir capítulos sueltos, súbela suelta.
      </p>
      {avisos.length > 0 && (
        <ul className="lote-motivos lote-avisos-expansion">{avisos.map(a => <li key={a}>{a}</li>)}</ul>
      )}
      <ul className="lote-lista">
        {elementos.map(item => {
          const motivo = razon(item)
          const marcada = !motivo && marcadas.has(item.id)
          // Qué tracker lo admite, si alguno no.
          const reglas = elegibilidad.get(item.id)
          const trackers = reglas?.some(r => !r.permitido) ? resumenPorTracker(reglas) : null
          const suyas = peticiones?.get(item.id) ?? NINGUNA
          const enlazada = peticionDe?.(item.id) ?? suyas[0]
          return (
            <li key={item.id} className={motivo ? 'lote-fila lote-fila-apagada' : 'lote-fila'}>
              <label className="checkbox-label lote-fila-casilla">
                <input
                  type="checkbox"
                  checked={marcada}
                  disabled={!!motivo || (!marcada && lleno)}
                  onChange={() => onAlternar(item.id)}
                />
                <TituloDeFila item={item} />
              </label>
              <span className="lote-fila-tipo">{TIPO[item.item_type] ?? item.item_type}</span>
              <span className="lote-fila-calidad">{calidad(item)}</span>
              {suyas.length === 1 && <span className="lote-fila-peticion">Petición #{suyas[0]}</span>}
              {suyas.length > 1 && (
                <select
                  className="form-input form-select lote-fila-peticion-elegir"
                  aria-label={`Petición que se enlaza con «${listDisplayTitle(item)}»`}
                  value={enlazada}
                  onChange={e => onElegirPeticion?.(item.id, Number(e.target.value))}
                >
                  {suyas.map(p => <option key={p} value={p}>Petición #{p}</option>)}
                </select>
              )}
              {motivo && <span className="lote-motivo"><IconoAviso /> {motivo}</span>}
              {!motivo && trackers && <span className="lote-fila-trackers asist-dato-nota">{trackers}</span>}
            </li>
          )
        })}
      </ul>
      <p className="lote-total" role="status">
        {formatNumero(n)} de {formatNumero(elementos.length)} {elementos.length === 1 ? 'marcada' : 'marcadas'} · máximo {MAX_ELEMENTOS_LOTE} por lote
      </p>
      <div className="asist-pie asist-pie-fin">
        {comprobandoIdiomas && (
          <span className="text-muted asist-t12" role="status">
            <span className="spinner asist-spinner-14" /> Mirando qué admite cada tracker…
          </span>
        )}
        <button className="btn btn-primary" type="button" onClick={onContinuar} disabled={n === 0 || comprobandoIdiomas}>
          Continuar ({n})
        </button>
      </div>
    </div>
  )
}

function TituloDeFila({ item }: { item: LibraryItemResponse }) {
  const { nombre, parte } = partesDelTitulo(item)
  return (
    <span className="lote-fila-titulo">
      <span className="lote-fila-nombre">{nombre}</span>
      {parte && <><span className="lote-fila-separador"> - </span><span className="lote-fila-parte">{parte}</span></>}
    </span>
  )
}
