import { Campo, EstadoSecreto } from '../Campo'
import { CabeceraTarjeta } from '../CabeceraTarjeta'
import { alternar, OTROS_TRACKERS, type ConfigForm } from './useConfigForm'
import { LogoTracker } from '../../../components/LogoTracker'

// Las claves de los otros trackers (fase B del plan multitracker). Opcionales: sin clave no se sube a ese tracker.
// No se prueban al guardar; una clave mala sale en el registro de la primera subida.
function ClaveTracker({ nombre, configurado, enmascarado, valor, setValor, quitar, setQuitar }: {
  nombre: string
  configurado?: boolean
  enmascarado?: string | null
  valor: string
  setValor: (v: string) => void
  quitar: boolean
  setQuitar: (f: (q: boolean) => boolean) => void
}) {
  return (
    <Campo
      etiqueta={`API key de ${nombre}`}
      nota={configurado ? 'dejar vacío para mantener la actual' : undefined}
      ayuda={<EstadoSecreto configurado={configurado} enmascarado={enmascarado} />}
    >
      {c => (
        <>
          <input
            {...c}
            type="password"
            className="form-input"
            value={valor}
            onChange={e => { setValor(e.target.value); setQuitar(() => false) }}
            placeholder={quitar ? 'Se quitará al guardar' : `API key de ${nombre}`}
            autoComplete="new-password"
          />
          {configurado && !valor && (
            <button type="button" className="btn btn-secondary btn-sm aj-self-start" onClick={() => setQuitar(q => !q)}>
              {quitar ? 'No quitarla' : 'Quitar la clave'}
            </button>
          )}
        </>
      )}
    </Campo>
  )
}

// Personal release / Anónimo de cada grupo en este tracker: los de eMuwarez hasta que se marcan aparte.
function MarcasTracker({ id, nombre, form }: { id: string; nombre: string; form: ConfigForm }) {
  const { tags, gruposPR, gruposAnon, marcasTrackers, setMarcasTrackers } = form
  const marcas = marcasTrackers[id] ?? null
  const poner = (m: typeof marcas) => setMarcasTrackers(t => ({ ...t, [id]: m }))
  return (
    <div className="form-group aj-campo">
      <span className="form-label aj-campo__etiqueta">Marcas de los grupos</span>
      <label className="checkbox-label aj-fs-13">
        <input type="checkbox" checked={!marcas}
          onChange={e => poner(e.target.checked ? null : { personal_release: gruposPR, anonimos: gruposAnon })} />
        Las mismas que en eMuwarez
      </label>
      {marcas && (
        <ul className="aj-grupos" aria-label={`Marcas de los grupos en ${nombre}`}>
          {tags.map(t => (
            <li key={t} className="aj-grupos__fila">
              <span className="aj-grupos__nombre">{t}</span>
              <label className="aj-grupos__marca">
                <input type="checkbox" checked={marcas.personal_release.includes(t)}
                  onChange={e => poner({ ...marcas, personal_release: alternar(marcas.personal_release, t, e.target.checked) })} />
                Personal release
              </label>
              <label className="aj-grupos__marca">
                <input type="checkbox" checked={marcas.anonimos.includes(t)}
                  onChange={e => poner({ ...marcas, anonimos: alternar(marcas.anonimos, t, e.target.checked) })} />
                Anónimo
              </label>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function OtrosTrackersCard({ form }: { form: ConfigForm }) {
  const {
    settings, newNobsKey, setNewNobsKey, newMnvKey, setNewMnvKey, quitarNobs, setQuitarNobs, quitarMnv, setQuitarMnv,
  } = form
  const claves = {
    nobs: { configurado: settings?.nobs_api_key_configured, enmascarado: settings?.nobs_api_key_masked, valor: newNobsKey,
      setValor: setNewNobsKey, quitar: quitarNobs, setQuitar: setQuitarNobs },
    mnv: { configurado: settings?.mnv_api_key_configured, enmascarado: settings?.mnv_api_key_masked, valor: newMnvKey,
      setValor: setNewMnvKey, quitar: quitarMnv, setQuitar: setQuitarMnv },
  }
  return (
    <div className="placeholder-card">
      <CabeceraTarjeta
        titulo="Otros trackers"
        descripcion="Para subir también a N.O.B.S y a Milnueve, con el mismo fichero que en eMuwarez. Con su clave puesta, el Resumen del asistente y el de «Subir por lotes» dejan elegirlos (una subida por tracker)."
      />
      {OTROS_TRACKERS.map(({ id, nombre }) => (
        <fieldset key={id} className="aj-grupo-eventos">
          <legend className="aj-titulo-grupo"><LogoTracker tracker={id} nombre={nombre} size={18} conNombre /></legend>
          <ClaveTracker nombre={nombre} {...claves[id]} />
          <MarcasTracker id={id} nombre={nombre} form={form} />
        </fieldset>
      ))}
    </div>
  )
}
