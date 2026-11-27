// Paso 1 de una película o un capítulo (uno o varios): los campos de la
// nomenclatura con la vista previa del nombre, y MediaInfo y pistas del archivo.
import { useId } from 'react'
import { esTanda } from '../../utils/seleccionSubida'
import { listDisplayTitle } from '../../utils/libraryItem'
import { normalizarModo, renombra, tocaElFichero } from '../../utils/modoPistas'
import { FitText } from '../FitText'
import { IconoAviso } from '../Iconos'
import { SourceComparisonModal } from '../SourceComparisonModal'
import { FIELD_INFO, INTERNAL_TITLE_LABEL } from './constantes'
import { MediaInfoTable } from './MediaInfoTable'
import { AvisoSubidaExistente, BotonComparacionSource, CampoTag, DatosDerivados, FieldLabel, GeneratingHint, MarcoPaso, TableLoadingSpinner } from './piezas'
import { Pestanas, type Pestana } from './Pestanas'
import { AvisoModoPistas, RemuxTable } from './RemuxTable'
import type { Asistente, PestanaArchivo } from './useAsistente'

export function PasoArchivo({ a }: { a: Asistente }) {
  const idCampo = useId()
  const {
    seleccion, targetId, episodeQueue, episodes, needsSelection, existingJob, existingJobBlocking,
    episodePrepping, episodePrepIndex, loading, error, reintentarNomenclatura,
    fields, setField, titleOptions, titleWarning, sources, providers, opcionesTag, detectingProvider,
    sourceRequiresProvider, sourceWarning, avisoSource, audioWarning, preview,
    sourceComparison, showSourceCompare, setShowSourceCompare, showEngineDetail, setShowEngineDetail,
    pestana, elegirPestana, miReport, miLoading, miError, runMediaInfo,
    remuxPreview, remuxLoading, remuxError, remuxEdits, runTrackRemux,
    setRemuxEdit, elegirIdioma, cambiarElemento, setStep, goToUpload, preparing, recalculandoIdioma,
  } = a

  const fieldBox = (
    <>
      <div className="asist-rejilla-campos">
        <label htmlFor={`${idCampo}-titulo`}><FieldLabel label="Título" info={FIELD_INFO.title} />
          {/* Spanish by default (it leads title_options), original as the
              alternative. Falls back to the old read-only input when the
              backend couldn't offer a choice at all, so the field never
              silently turns into an empty select. */}
          {titleOptions.length > 0 ? (
            <select id={`${idCampo}-titulo`}
              className="form-input form-select"
              value={fields.title || ''}
              onChange={e => setField('title', e.target.value)}
              disabled={titleOptions.length < 2}
            >
              {titleOptions.map(o => (
                <option key={o.value} value={o.value}>{o.label}: {o.value}</option>
              ))}
            </select>
          ) : (
            <input id={`${idCampo}-titulo`} className="form-input" value={fields.title || ''} disabled />
          )}
          {titleWarning && (
            <span className="asist-aviso-campo"><IconoAviso /> {titleWarning}</span>
          )}
        </label>
        <label htmlFor={`${idCampo}-source`}><FieldLabel label="Source" info={FIELD_INFO.source} />
          <div className="asist-fila-source">
            <select id={`${idCampo}-source`} className="form-input form-select asist-crece" value={fields.source || ''} onChange={e => setField('source', e.target.value)}>
              <option value="">— Seleccionar —</option>
              {sources.map(s => <option key={s.code} value={s.code}>{s.label}</option>)}
            </select>
            {sourceComparison && (
              <BotonComparacionSource
                comparison={sourceComparison}
                onClick={() => { setShowEngineDetail(false); setShowSourceCompare(v => !v) }}
              />
            )}
          </div>
          {sourceWarning && (
            <span className="asist-aviso-campo"><IconoAviso /> {sourceWarning}</span>
          )}
          {avisoSource && (
            <span className="asist-aviso-campo"><IconoAviso /> {avisoSource}</span>
          )}
        </label>
        {sourceRequiresProvider && (
          <label htmlFor={`${idCampo}-proveedor`}><FieldLabel label="Proveedor" info={FIELD_INFO.provider} />
            {detectingProvider ? (
              <div className="form-input asist-detectando">
                <span className="spinner asist-spinner-12" /> Detectando…
              </div>
            ) : (
              <select id={`${idCampo}-proveedor`} className="form-input form-select" value={fields.provider || ''} onChange={e => setField('provider', e.target.value || null)}>
                <option value="">— Sin proveedor —</option>
                {providers.map(p => <option key={p.code} value={p.code}>{p.name} ({p.code})</option>)}
              </select>
            )}
          </label>
        )}
        <CampoTag value={fields.tag} opciones={opcionesTag} onChange={t => setField('tag', t)} />
      </div>
      {/* Temporada / Episodio tampoco se edita: el nombre del release y el del archivo saldrían de lo que se escribiera. */}
      <DatosDerivados datos={[
        { etiqueta: 'Año', info: FIELD_INFO.year, valor: fields.year },
        ...(fields.season_episode != null
          ? [{ etiqueta: 'Temporada / Episodio', info: FIELD_INFO.season_episode, valor: fields.season_episode }] : []),
        { etiqueta: 'Resolución', info: FIELD_INFO.resolution, valor: fields.resolution },
        { etiqueta: 'Códec de vídeo', info: FIELD_INFO.video_codec, valor: fields.video_codec },
        { etiqueta: 'Audio', info: FIELD_INFO.audio, valor: fields.audio_summary, avisos: [audioWarning] },
        { etiqueta: 'Subtítulos', info: FIELD_INFO.subs, valor: fields.subs },
      ]} />
      <div className="asist-mt-16">
        <div className="asist-etiqueta">{INTERNAL_TITLE_LABEL}</div>
        {preview?.internal_mkv_title ? <div translate="no"><FitText min={11} text={preview.internal_mkv_title} /></div> : <GeneratingHint />}
      </div>
    </>
  )


  // Las pistas de audio sin idioma esperan una elección: la pestaña lo dice sin abrirla.
  const sinIdioma = (remuxPreview?.ordered_tracks ?? []).filter(t => t.type === 'audio' && t.sin_idioma && !remuxEdits[t.id]?.language).length
  const pestanas: Pestana<PestanaArchivo>[] = [
    { id: 'nomenclatura', etiqueta: 'Nomenclatura' },
    { id: 'pistas', etiqueta: 'Pistas', aviso: sinIdioma > 0 ? `${sinIdioma} sin idioma` : undefined },
    { id: 'mediainfo', etiqueta: 'MediaInfo' },
  ]
  const listo = !loading && !error && !episodePrepping
  const enTanda = esTanda(seleccion)

  return (
    <MarcoPaso
      pie={<>
        {needsSelection
          ? <button className="btn btn-secondary" type="button" onClick={() => setStep(0)}>Volver a la selección</button>
          : <span />}
        <button className="btn btn-primary" type="button" onClick={goToUpload} disabled={!listo || preparing || recalculandoIdioma || !preview || existingJobBlocking}>
          {preparing ? 'Preparando…' : 'Continuar'}
        </button>
      </>}
    >
      {episodeQueue.length > 1 && (
        <div className="asist-selector-fila">
          <select
            className="form-input form-select asist-select-auto"
            aria-label="Capítulo"
            value={targetId ?? ''} onChange={e => cambiarElemento(Number(e.target.value))}
            disabled={episodePrepping}
          >
            {/* Canonical "Serie (Año) - SxxExx", same format the rest
                of the app uses for an episode. */}
            {episodeQueue.map(id => {
              const ep = episodes.find(x => x.id === id)
              return <option key={id} value={id}>{ep ? listDisplayTitle(ep) : id}</option>
            })}
          </select>
        </div>
      )}
      <AvisoSubidaExistente job={existingJob} bloquea={existingJobBlocking} />
      {episodePrepping && (
        <div className="asist-espera">
          <span className="spinner asist-spinner-28" />
          <div className="asist-espera-titulo">Generando Nomenclatura para eMuwarez…</div>
          <div className="asist-t125 text-muted">
            Capítulo {Math.min(episodePrepIndex + 1, episodeQueue.length)} de {episodeQueue.length}
            {(() => {
              const ep = episodes.find(x => x.id === episodeQueue[episodePrepIndex])
              return ep ? `: ${listDisplayTitle(ep)}` : ''
            })()}
          </div>
        </div>
      )}
      {loading && !episodePrepping && (
        <div className="asist-espera">
          <span className="spinner asist-spinner-28" />
          <div className="asist-espera-titulo">Generando Nomenclatura para eMuwarez…</div>
        </div>
      )}
      {error && !loading && !episodePrepping && (
        <div className="asist-bloque-error">
          <div className="alert alert-error asist-alerta" role="alert">
            <IconoAviso /> {error}
            {/* La causa de casi siempre: el contenedor no ve los medios en la ruta que da Radarr/Sonarr. */}
            {/no accesible/i.test(error) && (
              <p className="asist-alerta-pista">
                Comprueba que la carpeta de medios está montada en el contenedor en la misma ruta que en Radarr/Sonarr.
              </p>
            )}
          </div>
          <button className="btn btn-secondary btn-sm" type="button" onClick={reintentarNomenclatura}>↻ Reintentar</button>
        </div>
      )}
      {listo && (
        <>
          {/* El nombre que saldrá, a la vista en las tres pestañas: lo que se cambie en cualquiera lo mueve. */}
          <div className="detail-card asist-tarjeta">
            <div className="asist-etiqueta">Nombre final del torrent (eMuwarez)</div>
            {preview?.torrent_name ? <div translate="no"><FitText min={11} text={preview.torrent_name} /></div> : <GeneratingHint />}
          </div>

          <Pestanas nombre="Detalle del archivo" pestanas={pestanas} activa={pestana} onCambiar={elegirPestana}>
            {pestana === 'nomenclatura' && (
              <>
                <p className="text-muted asist-t12 asist-mt-0 asist-mb-12">
                  El título y el año vienen de Radarr/Sonarr, el Source y el Proveedor del nombre del archivo, y el resto
                  se lee con MediaInfo del archivo real. Puedes ajustar los campos editables.
                </p>
                {fieldBox}
              </>
            )}

            {pestana === 'pistas' && (
              <>
                <div className="asist-fila-titulo-pestana">
                  <AvisoModoPistas plan={remuxPreview} />
                </div>
                {remuxLoading && !remuxPreview && <TableLoadingSpinner label="Cargando pistas del archivo…" />}
                {remuxError && !remuxLoading && (
                  <>
                    <p className="text-error asist-t12 asist-mt-0 asist-mb-10" role="alert"><IconoAviso /> {remuxError}</p>
                    <button className="btn btn-secondary btn-sm" type="button" onClick={runTrackRemux}>↻ Reintentar</button>
                  </>
                )}
                {/* Editable solo si el modo renombra: sin eso la
                    columna ofrece escribir un nombre que no se va a
                    aplicar. Lo decide Configuración → Otros, no una
                    casilla aquí: la casilla solo salía subiendo una
                    película o un capítulo suelto, nunca en una
                    temporada entera. */}
                {remuxPreview && (
                  <>
                    {/* En una tanda "Subir todos" no lleva las ediciones: se
                        enseñan las pistas pero no se pueden tocar. */}
                    {enTanda && (
                      <p className="text-muted asist-t12 asist-mt-0">
                        Al subir varios capítulos a la vez se aplican los nombres automáticos. Para cambiarlos, sube el capítulo suelto.
                      </p>
                    )}
                    <RemuxTable
                      plan={remuxPreview}
                      editable={!enTanda && tocaElFichero(normalizarModo(remuxPreview.track_mode))}
                      nameEditable={!enTanda && renombra(normalizarModo(remuxPreview.track_mode))}
                      edits={remuxEdits}
                      onEdit={setRemuxEdit}
                      // Con las pistas en «Ninguno» el idioma no se escribe: no se ofrece.
                      onIdioma={enTanda || !tocaElFichero(normalizarModo(remuxPreview.track_mode)) ? undefined : elegirIdioma}
                    />
                  </>
                )}
              </>
            )}

            {pestana === 'mediainfo' && (
              <>
                {miLoading && !miReport && <TableLoadingSpinner label="Cargando MediaInfo…" />}
                {miError && !miLoading && (
                  <>
                    <p className="text-error asist-t12 asist-mt-0 asist-mb-10" role="alert"><IconoAviso /> {miError}</p>
                    <button className="btn btn-secondary btn-sm" type="button" onClick={runMediaInfo}>↻ Reintentar</button>
                  </>
                )}
                {miReport && (
                  <MediaInfoTable
                    report={miReport}
                    onSourceToolClick={sourceComparison ? () => { setShowEngineDetail(true); setShowSourceCompare(true) } : undefined}
                  />
                )}
              </>
            )}
          </Pestanas>
        </>
      )}
      {/* Fuera de las pestañas: se abre igual desde el botón de Source (Nomenclatura) que desde MediaInfo. */}
      {showSourceCompare && sourceComparison && (
        <SourceComparisonModal
          comparison={sourceComparison}
          initialShowDetail={showEngineDetail}
          onClose={() => setShowSourceCompare(false)}
        />
      )}
    </MarcoPaso>
  )
}
