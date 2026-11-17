import { esClicNormal, useTooltip } from './Tooltip'
import { formatNumero } from '../utils/format'

interface StatCardProps {
  label: string
  value: number | string
  children?: React.ReactNode
  // Color de estado del icono (solo con `icon`).
  accentColor?: string
  // Custom hover explanation (styled popover, not the native title box).
  info?: React.ReactNode
  // Optional leading icon — only rendered by callers that opt in (e.g. the
  // HUD-styled Panel), so plain stat-card usages elsewhere are unaffected.
  icon?: React.ReactNode
  // La sección de la cifra: la tarjeta es un enlace (<a href>) y el clic normal llama a `onIr` (la navegación de la SPA).
  href?: string
  onIr?: () => void
}

export function StatCard({ label, value, children, accentColor, info, icon, href, onIr }: StatCardProps) {
  const { triggerProps, tooltipNode } = useTooltip(info, 460)
  const cifra = typeof value === 'number' ? formatNumero(value) : value
  const contenido = (
    <>
      {/* En línea: el color de acento es un valor que llega por props. */}
      {icon && <div className="stat-card-icon" aria-hidden="true" style={accentColor ? { color: accentColor } : undefined}>{icon}</div>}
      <div className="stat-card-text">
        <div className="stat-card-label">{label}</div>
        {/* La cifra va en el color del texto: el estado lo dice el icono, y una cifra de color con el número grande
            gritaba más que la etiqueta que la explica. */}
        <div className="stat-card-value">{cifra}</div>
        {children && <div className="stat-card-sub">{children}</div>}
      </div>
      {tooltipNode}
    </>
  )
  if (href && onIr) {
    return (
      <a href={href} className="stat-card stat-card-enlace" aria-label={`${label}: ${cifra}`} {...triggerProps}
        onClick={e => { if (!esClicNormal(e)) return; e.preventDefault(); onIr() }}>
        {contenido}
      </a>
    )
  }
  return (
    <div
      className={`stat-card${info != null ? ' stat-card-ayuda' : ''}`}
      // Con explicación, la tarjeta se alcanza con Tab para poder leerla, y es un grupo con nombre «etiqueta: cifra».
      {...(info != null ? { ...triggerProps, tabIndex: 0, role: 'group', 'aria-label': `${label}: ${cifra}` } : {})}
    >
      {contenido}
    </div>
  )
}
