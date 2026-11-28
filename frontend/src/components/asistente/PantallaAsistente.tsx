import { useEffect, useId, useRef } from 'react'
import { registrarCambios } from '../../utils/asistenteSesion'
import { Modal } from '../Modal'
import { PasoArchivo } from './PasoArchivo'
import { PasoSeleccion } from './PasoSeleccion'
import { PasoSubida } from './PasoSubida'
import { PasoSubidaTanda } from './PasoSubidaTanda'
import { PasoTemporada } from './PasoTemporada'
import { useAsistente, type PropsAsistente } from './useAsistente'

// Los pasos que de verdad tiene cada camino: «Contenido» solo existe al abrir una serie o una temporada.
function pasosDelAsistente(needsSelection: boolean): { paso: 0 | 1 | 2; nombre: string }[] {
  return [
    ...(needsSelection ? [{ paso: 0 as const, nombre: 'Contenido' }] : []),
    { paso: 1, nombre: 'Nombre y pistas' },
    { paso: 2, nombre: 'Resumen' },
  ]
}

// El asistente de «Subir», a pantalla completa (pages/SubirPage.tsx lo monta en /subir/<id>). Con una serie o una
// temporada pregunta primero qué subir; el paso 1 genera la nomenclatura —título y año de Radarr/Sonarr, Source y
// Proveedor del nombre del fichero, el resto de MediaInfo— en casillas editables con el nombre final a la vista; el
// paso 2 hace las comprobaciones previas y, confirmado, ENCOLA la subida (services/upload_queue.py hace integridad,
// nomenclatura, remux y arranque en el servidor, en el momento o cuando queda libre una plaza de «Subidas en
// paralelo») y pasa a Subidas: aquí no se aplica la nomenclatura ni se arranca Uploadrr.
//
// Era una ventana emergente: con una tanda de 30 temporadas no cabía, y mientras calculaba no dejaba usar el resto.
// Como pantalla se queda montada al cambiar de sección, igual que las demás, y se vuelve a ella desde el menú.
//
// El estado y la lógica están en useAsistente.ts; cada paso se pinta en su propio componente.
export function PantallaAsistente(props: PropsAsistente) {
  const a = useAsistente(props)
  const { item, step, needsSelection, targetIsSeason, batchQueue, seasonQueue, episodeQueue } = a
  const { cerrarModal, confirmandoCierre, setConfirmandoCierre, onClose, hayCambios } = a
  const pasos = pasosDelAsistente(needsSelection)
  const idTitulo = useId()
  // Al cambiar de paso el foco pasa al paso nuevo, para que el lector lo anuncie y Tab siga desde arriba.
  const pasoActual = useRef<HTMLLIElement>(null)
  const primerPintado = useRef(true)
  useEffect(() => {
    if (primerPintado.current) { primerPintado.current = false; return }
    pasoActual.current?.focus()
  }, [step])
  // Recargar o cerrar la pestaña con cambios hechos a mano avisa (AppShell lo pregunta).
  const cambios = useRef(hayCambios)
  cambios.current = hayCambios
  useEffect(() => registrarCambios(() => cambios.current()), [])
  return (
    <section className="asist-pantalla" aria-labelledby={idTitulo}>
      <div className="page-header asist-cabecera">
        <h1 className="page-title asist-titulo" id={idTitulo}>{item.title}</h1>
        <button type="button" className="btn btn-secondary btn-sm" onClick={cerrarModal}>Cerrar el asistente</button>
      </div>

      <nav aria-label="Pasos de la subida" className="asist-pasos">
        <ol>
          {pasos.map(({ paso, nombre }, i) => {
            const estado = paso < step ? 'hecho' : paso === step ? 'actual' : 'pendiente'
            return (
              <li
                key={paso} className={`asist-paso asist-paso-${estado}`}
                aria-current={estado === 'actual' ? 'step' : undefined}
                ref={estado === 'actual' ? pasoActual : undefined} tabIndex={estado === 'actual' ? -1 : undefined}
              >
                {estado === 'hecho' && <span aria-hidden="true">✓ </span>}{i + 1} · {nombre}
                {estado === 'hecho' && <span className="solo-lector"> (hecho)</span>}
              </li>
            )
          })}
        </ol>
      </nav>

      {step === 0 && <PasoSeleccion a={a} />}
      {step === 1 && targetIsSeason && <PasoTemporada a={a} />}
      {step === 1 && !targetIsSeason && <PasoArchivo a={a} />}
      {step === 2 && batchQueue.length > 1 && <PasoSubidaTanda a={a} />}
      {step === 2 && seasonQueue.length <= 1 && episodeQueue.length <= 1 && <PasoSubida a={a} />}

      {confirmandoCierre && (
        <Modal titulo="¿Descartar los cambios?" onClose={() => setConfirmandoCierre(false)} className="modal-estrecho">
          <div className="modal-body">
            <p className="text-muted">
              Has cambiado datos de la subida y todavía no se ha encolado. Si cierras ahora se perderán los cambios sin guardar; la nomenclatura que ya guardaste al pasar de paso se queda como está.
            </p>
            <div className="modal-actions">
              <button className="btn btn-secondary" type="button" onClick={() => setConfirmandoCierre(false)}>Seguir editando</button>
              <button className="btn btn-danger" type="button" onClick={onClose}>Cerrar y descartar</button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  )
}
