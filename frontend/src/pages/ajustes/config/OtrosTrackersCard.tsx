import { Campo, EstadoSecreto } from '../Campo'
import { CabeceraTarjeta } from '../CabeceraTarjeta'
import type { ConfigForm } from './useConfigForm'

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

export function OtrosTrackersCard({ form }: { form: ConfigForm }) {
  const {
    settings, newNobsKey, setNewNobsKey, newMnvKey, setNewMnvKey, quitarNobs, setQuitarNobs, quitarMnv, setQuitarMnv,
  } = form
  return (
    <div className="placeholder-card">
      <CabeceraTarjeta
        titulo="Otros trackers"
        descripcion="Para subir también a N.O.B.S y a Milnueve, con el mismo fichero que en eMuwarez. Con su clave puesta, el Resumen del asistente y el de «Subir por lotes» dejan elegirlos (una subida por tracker)."
      />
      <ClaveTracker nombre="N.O.B.S" configurado={settings?.nobs_api_key_configured}
        enmascarado={settings?.nobs_api_key_masked} valor={newNobsKey} setValor={setNewNobsKey}
        quitar={quitarNobs} setQuitar={setQuitarNobs} />
      <ClaveTracker nombre="Milnueve" configurado={settings?.mnv_api_key_configured}
        enmascarado={settings?.mnv_api_key_masked} valor={newMnvKey} setValor={setNewMnvKey}
        quitar={quitarMnv} setQuitar={setQuitarMnv} />
    </div>
  )
}
