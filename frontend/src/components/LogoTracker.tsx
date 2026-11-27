// Los logos de los trackers van dentro del proyecto, en assets/trackers/<id>.png (el favicon de cada uno, recortado a la
// marca): la CSP solo deja imágenes de la app.
const LOGOS = (import.meta as unknown as {
  glob: (pattern: string, opts: object) => Record<string, string>
}).glob('../assets/trackers/*.png', { eager: true, query: '?no-inline', import: 'default' })

const POR_ID = new Map(Object.entries(LOGOS).map(([ruta, url]) => [ruta.split('/').pop()!.replace(/\.png$/, ''), url]))
// Los que son arte de píxel: se escalan sin suavizar.
const PIXELADOS = new Set(['mnv'])

// El logo del tracker a `size` px de alto (y como mucho 1,4 veces de ancho), con su nombre de texto alternativo; con
// `conNombre`, el nombre va escrito al lado. Sin logo, el nombre.
export function LogoTracker({ tracker, nombre, size = 16, conNombre = false }: {
  tracker?: string; nombre: string; size?: number; conNombre?: boolean
}) {
  const id = tracker || 'emu'
  const url = POR_ID.get(id)
  if (!url) return <>{nombre}</>
  const img = <img className={PIXELADOS.has(id) ? 'logo-tracker es-pixel' : 'logo-tracker'} src={url}
    alt={conNombre ? '' : nombre} title={conNombre ? undefined : nombre} height={size} style={{ maxWidth: size * 1.4 }} />
  return conNombre ? <span className="logo-tracker-con-nombre">{img}{nombre}</span> : img
}
