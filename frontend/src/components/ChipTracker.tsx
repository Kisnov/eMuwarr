// El tracker de una fila, solo si no es eMuwarez: quien solo usa eMuwarez no ve nada nuevo. `siempre`: también eMuwarez.
export function ChipTracker({ tracker, nombre, siempre }: { tracker?: string; nombre?: string; siempre?: boolean }) {
  if (!tracker || (tracker === 'emu' && !siempre)) return null
  return <span className="sub-tracker-chip">{nombre}</span>
}
