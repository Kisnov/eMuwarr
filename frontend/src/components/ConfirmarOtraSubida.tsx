import { confirmarPendiente, descartarPendiente, irAlAsistente, useSesionAsistente } from '../utils/asistenteSesion'
import { Modal } from './Modal'

// Se pulsa «Subir» en otro contenido con una subida a medias en el asistente: solo hay una a la vez, así que antes de
// dejarla se pregunta. Va una sola vez, en AppShell.
export function ConfirmarOtraSubida() {
  const { sesion, pendiente } = useSesionAsistente()
  if (!sesion || !pendiente) return null
  return (
    <Modal titulo="Ya hay una subida en preparación" onClose={descartarPendiente} className="modal-estrecho">
      <div className="modal-body">
        <p className="text-muted">
          Estás preparando «{sesion.item.title}». Si empiezas con «{pendiente.item.title}» se pierde lo que hayas
          elegido y cambiado en ella.
        </p>
        <div className="modal-actions">
          {/* La acción segura lleva el foco: Intro sin mirar no tira el trabajo. */}
          <button type="button" className="btn btn-secondary" autoFocus onClick={() => { descartarPendiente(); irAlAsistente() }}>
            Volver a la que estaba
          </button>
          <button type="button" className="btn btn-danger" onClick={confirmarPendiente}>Dejarla y empezar esta</button>
        </div>
      </div>
    </Modal>
  )
}
