import { LogoTracker } from './LogoTracker'

// El tracker de una fila, solo si no es eMuwarez: quien solo usa eMuwarez no ve nada nuevo.
export function ChipTracker({ tracker, nombre }: { tracker?: string; nombre?: string }) {
  if (!tracker || tracker === 'emu') return null
  return <span className="sub-tracker-chip"><LogoTracker tracker={tracker} nombre={nombre ?? tracker} size={14} conNombre /></span>
}
