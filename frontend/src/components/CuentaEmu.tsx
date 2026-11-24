import { useState } from 'react'
import { tapar, usePerfilEmu } from '../context/PerfilEmuContext'
import { useCooldown } from '../hooks/useCooldown'
import type { EmuPerfil } from '../types/api'
import { decimalEs, enlacesCuentaEmu, ratioBajo } from '../utils/cuentaEmu'
import { formatNumero, timeAgo } from '../utils/format'
import { ChipTracker } from './ChipTracker'
import { IconoAviso, IconoSubida } from './Iconos'
import { ServiceIcon } from './ServiceIcon'
import { Tooltip } from './Tooltip'

const AVISO_RATIO = 'Con ratio por debajo de 1, eMuwarez desactiva la API key: la app deja de poder sincronizar y subir.'
const OTRA_PESTANA = <span className="solo-lector"> (se abre en otra pestaña)</span>

// Sin usuario aún (la primera lectura ha fallado) se enseña al menos el error.
const seEnsena = (perfil: EmuPerfil) => !!(perfil.username || perfil.error)

// Tu cuenta y el enlace a «Mis subidas» (la API no lo da); mensajes y notificaciones están en la barra superior y el cajón móvil.
// Con otros trackers con clave, una franja más por cada uno, debajo de la de eMuwarez.
export function CuentaEmu() {
  const { perfil } = usePerfilEmu()
  // La de cada tracker que tiene algo que enseñar, aunque la de eMuwarez no lo tenga.
  const bandas = perfil ? [perfil, ...(perfil.otros ?? [])].filter(seEnsena) : []
  if (!bandas.length) return null
  if (bandas.length === 1) return <Cuenta perfil={bandas[0]} />
  return (
    <div className="cuenta-emu-lista">
      {bandas.map(b => <Cuenta key={b.tracker ?? 'emu'} perfil={b} />)}
    </div>
  )
}

function Cuenta({ perfil }: { perfil: EmuPerfil }) {
  const { actualizar, ocultarNombre } = usePerfilEmu()
  const espera = useCooldown()
  const [actualizando, setActualizando] = useState(false)
  const emu = !perfil.tracker || perfil.tracker === 'emu'
  const nombre = perfil.nombre ?? 'eMuwarez'

  const enlaces = perfil.username ? { usuario: perfil.username, ...enlacesCuentaEmu(perfil.username, perfil.url) } : null
  // La regla del ratio por debajo de 1 es la de eMuwarez.
  const bajo = emu && ratioBajo(perfil.ratio)
  const hr = perfil.hit_and_runs ?? 0

  const pulsar = async () => {
    setActualizando(true)
    try { await actualizar() } catch { /* se queda lo que había */ } finally {
      setActualizando(false)
      espera.start(60)  // el servidor tampoco vuelve a eMuwarez antes de un minuto
    }
  }

  const cifra = (etiqueta: string, valor: React.ReactNode, clase = '') => (
    <div className={`cuenta-emu-cifra ${clase}`}><dt>{etiqueta}</dt><dd>{valor}</dd></div>
  )

  return (
    <section className="cuenta-emu" aria-label={`Tu cuenta en ${nombre}`}>
      {enlaces && (
        <a className="cuenta-emu-usuario hover-link" href={enlaces.perfil} target="_blank" rel="noreferrer">
          {emu ? <ServiceIcon type="emu" size={16} /> : <ChipTracker tracker={perfil.tracker} nombre={nombre} />}
          {ocultarNombre ? tapar(enlaces.usuario) : enlaces.usuario}
          <span className="solo-lector"> (tu perfil en {nombre}, se abre en otra pestaña)</span>
        </a>
      )}
      {perfil.group && <span className="badge cuenta-emu-rol">{perfil.group}</span>}

      {perfil.ratio != null && (
        <dl className="cuenta-emu-cifras">
          {cifra('Ratio', bajo
            ? <Tooltip content={AVISO_RATIO}><span tabIndex={0}><IconoAviso /> {decimalEs(perfil.ratio)}</span></Tooltip>
            : decimalEs(perfil.ratio), bajo ? 'es-malo' : '')}
          {perfil.uploaded && cifra('Subido', decimalEs(perfil.uploaded))}
          {perfil.downloaded && cifra('Bajado', decimalEs(perfil.downloaded))}
          {perfil.buffer && cifra('Buffer', decimalEs(perfil.buffer))}
          {cifra('Sembrando', formatNumero(perfil.seeding))}
          {cifra('Descargando', formatNumero(perfil.leeching))}
          {cifra('BON', formatNumero(perfil.seedbonus))}
          {cifra('H&R', hr > 0 ? <><IconoAviso /> {formatNumero(hr)}</> : formatNumero(hr), hr > 0 ? 'es-aviso' : '')}
        </dl>
      )}

      {perfil.error && (
        <span className="cuenta-emu-error" role="status"><IconoAviso /> {perfil.error}</span>
      )}

      {enlaces && (
        <nav className="cuenta-emu-enlaces" aria-label={`Tus subidas en ${nombre}`}>
          <Tooltip content={`Mis subidas en ${nombre}`}>
            <a className="hover-link" href={enlaces.subidas} target="_blank" rel="noreferrer">
              <IconoSubida /> <span className="cuenta-emu-enlace-texto">Mis subidas</span>{OTRA_PESTANA}
            </a>
          </Tooltip>
        </nav>
      )}

      {perfil.configured && (
        <Tooltip content={`${perfil.updated_at ? `Leído de ${nombre} ${timeAgo(perfil.updated_at)}` : `Aún sin leer de ${nombre}`}. Se vuelve a leer solo cada hora.`}>
          <button type="button" className="btn btn-ghost btn-sm" onClick={pulsar}
            disabled={actualizando || espera.active} aria-label={`Actualizar tu cuenta de ${nombre}`}>
            {actualizando ? <span className="spinner emusync-spinner" /> : '↻'}
          </button>
        </Tooltip>
      )}
    </section>
  )
}
