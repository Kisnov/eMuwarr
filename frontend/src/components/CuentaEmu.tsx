import { useState } from 'react'
import { tapar, usePerfilEmu } from '../context/PerfilEmuContext'
import { useCooldown } from '../hooks/useCooldown'
import type { EmuPerfil } from '../types/api'
import { decimalEs, enlacesCuentaEmu, ratioBajo } from '../utils/cuentaEmu'
import { formatNumero, timeAgo } from '../utils/format'
import { IconoAviso, IconoSubida } from './Iconos'
import { LogoTracker } from './LogoTracker'
import { Tooltip } from './Tooltip'

const AVISO_RATIO = 'Con ratio por debajo de 1, eMuwarez desactiva la API key: la app deja de poder sincronizar y subir.'
const OTRA_PESTANA = <span className="solo-lector"> (se abre en otra pestaña)</span>
const ETIQUETAS = ['Ratio', 'Subido', 'Bajado', 'Buffer', 'Sembrando', 'Descargando', 'BON', 'H&R'] as const

// Sin usuario aún (la primera lectura ha fallado) se enseña al menos el error.
const seEnsena = (perfil: EmuPerfil) => !!(perfil.username || perfil.error)

// Tu cuenta en cada tracker con clave y el enlace a «Mis subidas» (la API no lo da); mensajes y notificaciones están en
// la barra superior y el cajón móvil. Dos disposiciones para elegir: una tarjeta por tracker o una tabla.
export function CuentaEmu() {
  const { perfil } = usePerfilEmu()
  // La de cada tracker que tiene algo que enseñar, aunque la de eMuwarez no lo tenga.
  const bandas = perfil ? [perfil, ...(perfil.otros ?? [])].filter(seEnsena) : []
  if (!bandas.length) return null
  const Vista = DISPOSICIONES[DISPOSICION]
  return <Vista bandas={bandas} />
}

// Lo que comparten las dos disposiciones: enlaces, avisos, cifras y el botón de volver a leer.
function useCuenta(perfil: EmuPerfil) {
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

  // Una por etiqueta, en el orden de ETIQUETAS; null si el tracker no la da.
  const cifras: { valor: React.ReactNode; clase: string }[] | null = perfil.ratio == null ? null : [
    { valor: bajo
      ? <Tooltip content={AVISO_RATIO}><span tabIndex={0}><IconoAviso /> {decimalEs(perfil.ratio)}</span></Tooltip>
      : decimalEs(perfil.ratio), clase: bajo ? 'es-malo' : '' },
    { valor: perfil.uploaded && decimalEs(perfil.uploaded), clase: '' },
    { valor: perfil.downloaded && decimalEs(perfil.downloaded), clase: '' },
    { valor: perfil.buffer && decimalEs(perfil.buffer), clase: '' },
    { valor: formatNumero(perfil.seeding), clase: '' },
    { valor: formatNumero(perfil.leeching), clase: '' },
    { valor: formatNumero(perfil.seedbonus), clase: '' },
    { valor: hr > 0 ? <><IconoAviso /> {formatNumero(hr)}</> : formatNumero(hr), clase: hr > 0 ? 'es-aviso' : '' },
  ]

  const usuario = enlaces ? (
    <a className="cuenta-emu-usuario hover-link" href={enlaces.perfil} target="_blank" rel="noreferrer">
      <LogoTracker tracker={perfil.tracker} nombre={nombre} size={20} />
      <span className="cuenta-emu-nombre">{ocultarNombre ? tapar(enlaces.usuario) : enlaces.usuario}</span>
      <span className="solo-lector"> (tu perfil en {nombre}, se abre en otra pestaña)</span>
    </a>
  ) : <span className="cuenta-emu-usuario"><LogoTracker tracker={perfil.tracker} nombre={nombre} size={20} /></span>

  const acciones = (
    <div className="cuenta-emu-acciones">
      {enlaces && (
        <Tooltip content={`Mis subidas en ${nombre}`}>
          <a className="cuenta-emu-enlace hover-link" href={enlaces.subidas} target="_blank" rel="noreferrer">
            <IconoSubida /> <span className="cuenta-emu-enlace-texto">Mis subidas</span>{OTRA_PESTANA}
          </a>
        </Tooltip>
      )}
      {perfil.configured && (
        <Tooltip content={`${perfil.updated_at ? `Leído de ${nombre} ${timeAgo(perfil.updated_at)}` : `Aún sin leer de ${nombre}`}. Se vuelve a leer solo cada hora.`}>
          <button type="button" className="btn btn-ghost btn-sm" onClick={pulsar}
            disabled={actualizando || espera.active} aria-label={`Actualizar tu cuenta de ${nombre}`}>
            {actualizando ? <span className="spinner emusync-spinner" /> : '↻'}
          </button>
        </Tooltip>
      )}
    </div>
  )

  const rol = perfil.group && <span className="badge cuenta-emu-rol">{perfil.group}</span>
  const error = perfil.error && <span className="cuenta-emu-error" role="status"><IconoAviso /> {perfil.error}</span>
  return { nombre, usuario, rol, cifras, error, acciones }
}

// Opción A: una tarjeta por tracker, una al lado de otra; con una sola (y sitio), todo en una fila.
export function CuentasEnTarjetas({ bandas }: { bandas: EmuPerfil[] }) {
  return (
    <div className={`cuentas cuentas-tarjetas${bandas.length === 1 ? ' es-una' : ''}`}>
      {bandas.map(b => <Tarjeta key={b.tracker ?? 'emu'} perfil={b} />)}
    </div>
  )
}

function Tarjeta({ perfil }: { perfil: EmuPerfil }) {
  const c = useCuenta(perfil)
  return (
    <section className="cuenta-emu cuenta-tarjeta" aria-label={`Tu cuenta en ${c.nombre}`}>
      <div className="cuenta-tarjeta-cuerpo">
        <div className="cuenta-tarjeta-cabecera">{c.usuario}{c.rol}</div>
        {c.acciones}
        {c.cifras && (
          <dl className="cuenta-emu-cifras">
            {c.cifras.map((x, i) => x.valor != null && x.valor !== '' && (
              <div key={ETIQUETAS[i]} className={`cuenta-emu-cifra ${x.clase}`}><dt>{ETIQUETAS[i]}</dt><dd>{x.valor}</dd></div>
            ))}
          </dl>
        )}
        {c.error}
      </div>
    </section>
  )
}

// Opción B: una tabla, una fila por tracker y las cifras en columnas alineadas, con las etiquetas una sola vez.
export function CuentasEnTabla({ bandas }: { bandas: EmuPerfil[] }) {
  return (
    <div className="cuentas cuentas-tabla-caja">
      <table className="cuentas-tabla">
        <caption className="solo-lector">Tus cuentas en los trackers</caption>
        <thead>
          <tr>
            <th scope="col" className="cuentas-tabla-cuenta">Cuenta</th>
            {ETIQUETAS.map(e => <th key={e} scope="col">{e}</th>)}
            <th scope="col"><span className="solo-lector">Acciones</span></th>
          </tr>
        </thead>
        <tbody>
          {bandas.map(b => <Fila key={b.tracker ?? 'emu'} perfil={b} />)}
        </tbody>
      </table>
    </div>
  )
}

function Fila({ perfil }: { perfil: EmuPerfil }) {
  const c = useCuenta(perfil)
  return (
    <tr className="cuenta-emu" aria-label={`Tu cuenta en ${c.nombre}`}>
      <th scope="row" className="cuentas-tabla-cuenta">
        <div className="cuentas-tabla-quien">{c.usuario}{c.rol}</div>
        {c.cifras && c.error}
      </th>
      {c.cifras
        ? c.cifras.map((x, i) => <td key={ETIQUETAS[i]} className={`cuenta-emu-cifra ${x.clase}`}>{x.valor || '—'}</td>)
        : <td colSpan={ETIQUETAS.length} className="cuentas-tabla-sin-cifras">{c.error}</td>}
      <td className="cuentas-tabla-acciones">{c.acciones}</td>
    </tr>
  )
}

// Cuál de las dos se enseña (temporal, mientras se elige).
const DISPOSICIONES = { tarjetas: CuentasEnTarjetas, tabla: CuentasEnTabla }
const DISPOSICION: keyof typeof DISPOSICIONES = 'tarjetas'
