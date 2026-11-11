import type { ReactNode } from 'react'
import { IconoAviso } from './Iconos'
interface ErrorStateProps {
  title?: string
  message: string
  onRetry?: () => void
  // Otra salida junto a Reintentar (p. ej. quitar el filtro que provoca el error).
  extra?: ReactNode
}

export function ErrorState({ title = 'Error', message, onRetry, extra }: ErrorStateProps) {
  return (
    <div className="empty-state" role="alert">
      <div className="empty-state-icon"><IconoAviso /></div>
      <div className="empty-state-title">{title}</div>
      <div className="empty-state-body">{message}</div>
      {onRetry && !extra && (
        <button className="btn btn-secondary btn-sm" onClick={onRetry}>
          Reintentar
        </button>
      )}
      {onRetry && extra && (
        <div className="sub-vacio-acciones">
          <button className="btn btn-secondary btn-sm" onClick={onRetry}>
            Reintentar
          </button>
          {extra}
        </div>
      )}
    </div>
  )
}
