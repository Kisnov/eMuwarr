import { useEffect, useRef, useState } from 'react'
import type { LibraryItemResponse } from '../types/api'
import { getTmdbCandidatos, identificarTmdb, type CandidatosTmdb, type TipoTmdb } from '../api/library'
import { Modal } from './Modal'
import { FichaPoster } from './FichaPoster'
import { IconoTipo } from './Iconos'
import { Campo } from '../pages/ajustes/Campo'
import { idVerifyUrl, leerIdTmdb, posterUrl } from '../utils/libraryItem'

const fallo = (e: unknown, porDefecto: string) => (e instanceof Error && e.message ? e.message : porDefecto)

/**
 * Confirmar la ficha de TMDb de una fila de carpeta manual. Abre con la búsqueda que el servidor saca del nombre del
 * fichero; se puede buscar otra cosa o pegar el id. En una temporada o un capítulo, la ficha es la de su serie.
 */
export function IdentificarTmdbModal({ item, onClose, onDone }: {
  item: LibraryItemResponse
  onClose: () => void
  onDone: (mensaje: string) => void
}) {
  const [tipo, setTipo] = useState<TipoTmdb>(item.item_type === 'movie' ? 'movie' : 'tv')
  const [consulta, setConsulta] = useState(item.title)
  const [anio, setAnio] = useState('')
  const [resultado, setResultado] = useState<CandidatosTmdb | null>(null)
  const [buscando, setBuscando] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [errores, setErrores] = useState<{ consulta?: string; anio?: string; id?: string }>({})
  const [idPegado, setIdPegado] = useState('')
  // El id que se está confirmando: mientras, nada más se puede pulsar.
  const [guardando, setGuardando] = useState<string | null>(null)
  // Solo vale la última búsqueda.
  const ultima = useRef(0)

  async function pedir(busqueda?: { q: string; anio: number | null; tipo: TipoTmdb }) {
    const esta = ++ultima.current
    setBuscando(true)
    setError(null)
    try {
      const r = await getTmdbCandidatos(item.id, busqueda)
      if (esta !== ultima.current) return
      setResultado(r)
      if (!busqueda) {
        setTipo(r.tipo)
        setConsulta(r.consulta)
        setAnio(r.anio ? String(r.anio) : '')
      }
    } catch (e) {
      if (esta === ultima.current) setError(fallo(e, 'No se pudo buscar en TMDb.'))
    } finally {
      if (esta === ultima.current) setBuscando(false)
    }
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps -- una vez al abrir: la búsqueda del nombre del fichero
  useEffect(() => { void pedir() }, [item.id])

  function buscar(e: React.FormEvent) {
    e.preventDefault()
    const q = consulta.trim()
    const a = anio.trim()
    const sig = {
      consulta: q ? undefined : 'Escribe qué buscar.',
      anio: !a || /^\d{4}$/.test(a) ? undefined : 'El año, con cuatro cifras (o vacío).',
    }
    setErrores(sig)
    if (sig.consulta || sig.anio) return
    void pedir({ q, anio: a ? Number(a) : null, tipo })
  }

  async function confirmar(tmdbId: string, tipoFicha: TipoTmdb) {
    setGuardando(tmdbId)
    setError(null)
    try {
      const r = await identificarTmdb(item.id, tmdbId, tipoFicha)
      if (!r.ok) { setError(r.message || 'No se pudo confirmar la ficha.'); setGuardando(null); return }
      onDone(r.message)
    } catch (e) {
      setError(fallo(e, 'No se pudo confirmar la ficha.'))
      setGuardando(null)
    }
  }

  function usarId(e: React.FormEvent) {
    e.preventDefault()
    const leido = leerIdTmdb(idPegado)
    setErrores(p => ({ ...p, id: leido ? undefined : 'Pega el número o la dirección de la ficha (themoviedb.org/movie/… o /tv/…).' }))
    if (leido) void confirmar(leido.id, leido.tipo ?? tipo)
  }

  const ocupado = guardando !== null
  const sugerencia = item.sugerencia_tmdb
  // Los resultados se confirman con el tipo con el que se buscaron, no con el del desplegable si se ha cambiado luego.
  const tipoDeLista = resultado?.tipo ?? tipo
  const deSerie = item.item_type === 'season' || item.item_type === 'episode'

  return (
    <Modal titulo="Identificar en TMDb" onClose={() => { if (!ocupado) onClose() }} className="modal-wide bib-ident-modal">
      <div className="modal-body">
        <p className="bib-ident-de">
          <span translate="no">«{item.title}»</span>
          {deSerie && ' La ficha se aplica a toda la serie.'}
        </p>

        {sugerencia && (
          <div className="bib-ident-sugerencia">
            <FichaPoster url={posterUrl(sugerencia.poster_url, 'w154')} alt="" fallbackIcon={<IconoTipo serie={sugerencia.tipo === 'tv'} />} />
            <div className="bib-ident-texto">
              <p className="bib-ident-pregunta">¿Es {sugerencia.titulo}{sugerencia.anio ? ` (${sugerencia.anio})` : ''}?</p>
              <p className="bib-ident-nota">El escaneo la encontró en TMDb por el nombre del fichero.</p>
            </div>
            <button type="button" className="btn btn-primary btn-sm" onClick={() => confirmar(sugerencia.tmdb_id, sugerencia.tipo)} disabled={ocupado}>
              {guardando === sugerencia.tmdb_id ? 'Guardando…' : 'Sí, es esta'}
            </button>
          </div>
        )}

        <form className="bib-ident-busqueda" onSubmit={buscar} noValidate>
          <Campo etiqueta="Tipo">
            {c => (
              <select {...c} className="form-input form-select" value={tipo} onChange={e => setTipo(e.target.value as TipoTmdb)}>
                <option value="movie">Película</option>
                <option value="tv">Serie</option>
              </select>
            )}
          </Campo>
          <Campo etiqueta="Título" error={errores.consulta}>
            {c => <input {...c} type="search" className="form-input" value={consulta} onChange={e => setConsulta(e.target.value)} autoComplete="off" spellCheck={false} />}
          </Campo>
          <Campo etiqueta="Año" nota="opcional" error={errores.anio}>
            {c => <input {...c} className="form-input bib-ident-anio-campo" inputMode="numeric" maxLength={4} value={anio} onChange={e => setAnio(e.target.value)} autoComplete="off" />}
          </Campo>
          <button type="submit" className="btn btn-secondary" disabled={ocupado}>Buscar</button>
        </form>

        {error && <p className="bib-ident-error" role="alert">{error}</p>}
        {buscando ? (
          <p className="bib-ident-estado" role="status">Buscando en TMDb…</p>
        ) : resultado && resultado.candidatos.length === 0 ? (
          <p className="bib-ident-estado" role="status">
            TMDb no devuelve nada para «{resultado.consulta}»{resultado.anio ? ` de ${resultado.anio}` : ''}. Prueba con otro título, sin el año
            o con el otro tipo.
          </p>
        ) : resultado && (
          <ul className="bib-ident-lista" aria-label="Resultados de TMDb">
            {resultado.candidatos.map(c => {
              const nombre = `${c.titulo}${c.anio ? ` (${c.anio})` : ''}`
              return (
                <li key={c.tmdb_id} className="bib-ident-cand">
                  <FichaPoster url={posterUrl(c.poster_url, 'w154')} alt="" fallbackIcon={<IconoTipo serie={tipoDeLista === 'tv'} />} />
                  <div className="bib-ident-texto">
                    <a className="bib-ident-titulo hover-link" href={idVerifyUrl('tmdb', c.tmdb_id, tipoDeLista === 'tv') ?? undefined} target="_blank" rel="noreferrer">
                      {c.titulo}
                      {c.anio && <span className="bib-ident-anio"> ({c.anio})</span>}
                    </a>
                    {c.titulo_original && c.titulo_original !== c.titulo && (
                      <span className="bib-ident-original" translate="no">{c.titulo_original}</span>
                    )}
                    {c.resumen && <p className="bib-ident-resumen">{c.resumen}</p>}
                  </div>
                  <button type="button" className="btn btn-secondary btn-sm" onClick={() => confirmar(c.tmdb_id, tipoDeLista)} disabled={ocupado} aria-label={`Es esta: ${nombre}`}>
                    {guardando === c.tmdb_id ? 'Guardando…' : 'Es esta'}
                  </button>
                </li>
              )
            })}
          </ul>
        )}

        <form className="bib-ident-id" onSubmit={usarId} noValidate>
          <Campo etiqueta="Id o enlace de TMDb" error={errores.id} ayuda="Si no sale en la lista: el número o la dirección de su ficha en themoviedb.org.">
            {c => (
              <div className="bib-ident-id-fila">
                <input {...c} className="form-input bib-ident-id-campo" placeholder="https://www.themoviedb.org/movie/603" value={idPegado} onChange={e => setIdPegado(e.target.value)} autoComplete="off" spellCheck={false} translate="no" />
                <button type="submit" className="btn btn-secondary" disabled={ocupado}>Usar este id</button>
              </div>
            )}
          </Campo>
        </form>

        <div className="modal-actions bib-ident-pie">
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={ocupado}>Cancelar</button>
        </div>
      </div>
    </Modal>
  )
}
