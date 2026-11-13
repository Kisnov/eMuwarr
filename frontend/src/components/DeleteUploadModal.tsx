import { useState } from 'react'
import type { UploadJob } from '../types/api'
import { deleteUploadJob } from '../api/uploads'
import { Modal } from './Modal'

// Styled confirmation card for deleting an upload job from its own dedicated
// detail page — only ever opened for a 'failed'/'cancelled' job (the backend
// refuses any other status, see delete_upload_job() in services/upload_execution.py).
export function DeleteUploadModal({ job, onClose, onDeleted }: {
  job: UploadJob
  onClose: () => void
  onDeleted: () => void
}) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleDelete = async () => {
    setLoading(true); setError(null)
    try {
      await deleteUploadJob(job.id!)
      onDeleted()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'No se pudo borrar la subida.')
      setLoading(false)
    }
  }

  return (
    <Modal titulo="Borrar subida" onClose={onClose} className="delsub-modal">
      <div className="modal-body">
        <p className="delsub-titulo">{job.title || `Subida #${job.id}`}</p>
        <div className="delsub-aviso">
          <p className="delsub-aviso-texto">
            Se va a borrar lo que esta aplicación guarda de esta subida: el registro de actividad, las capturas y
            los demás datos generados. Esta acción no se puede deshacer.
          </p>
          {(job.retry_count ?? 0) > 0 && (
            <p className="delsub-aviso-texto delsub-aviso-extra">
              Incluye sus <strong>{job.retry_count} reintento(s)</strong>: se borran los que
              fallaron o se cancelaron. Otra subida del mismo título que llegó a eMuwarez no se borra desde aquí.
            </p>
          )}
        </div>
        <p className="delsub-nota">
          No se toca el archivo de vídeo de tu biblioteca, ni el torrent en eMuwarez, ni lo que siembra qBittorrent.
        </p>
        {error && <p className="error-text delsub-error">{error}</p>}
        <div className="delsub-botones">
          <button className="btn btn-secondary" onClick={onClose} disabled={loading}>Cancelar</button>
          <button className="btn btn-danger" onClick={handleDelete} disabled={loading}>{loading ? 'Borrando…' : 'Borrar subida'}</button>
        </div>
      </div>
    </Modal>
  )
}
