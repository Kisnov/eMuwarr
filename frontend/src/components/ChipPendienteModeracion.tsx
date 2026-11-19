import { Tooltip } from './Tooltip'
import { PENDIENTE_MODERACION } from '../utils/estadoDeSubida'

// El chip «Pendiente de moderación» con su ayuda, que también se abre con el teclado (tabIndex) y al tocarlo.
export function ChipPendienteModeracion({ className }: { className?: string }) {
  return (
    <Tooltip content={PENDIENTE_MODERACION.ayuda}>
      <span className={className ? `${PENDIENTE_MODERACION.clase} ${className}` : PENDIENTE_MODERACION.clase} tabIndex={0}>
        {PENDIENTE_MODERACION.texto}
      </span>
    </Tooltip>
  )
}
