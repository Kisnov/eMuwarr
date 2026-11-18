import { useEffect, useState } from 'react'
import { getDashboardSummary } from '../api/dashboard'
import { getEstadisticasCuenta } from '../api/estadisticas'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import { GraficoLinea } from '../components/GraficoLinea'
import { LoadingState } from '../components/LoadingState'
import { MisSubidasEmu } from '../components/MisSubidasEmu'
import { StatCard } from '../components/StatCard'
import { ICON_UPLOADS, svgIcon, TarjetasDelPanel } from '../components/TarjetasDelPanel'
import { useSeccionVisible } from '../context/SeccionVisible'
import type { DashboardSummaryResponse, PuntoCuenta, RitmosCuenta } from '../types/api'
import { formatBytes, formatNumero, SIN_DATO } from '../utils/format'
import { formatDia, type PuntoSerie } from '../utils/graficos'

const RANGOS = [30, 90, 365]
// La proyección del ratio: a cuántos días vista.
const DIAS_PROYECCION = 30

// formatBytes enseña una raya para el cero; en un eje o en la tabla, 0 es una cifra. El buffer puede ser negativo.
const bytes = (n: number) => (n === 0 ? '0\u00A0B' : n < 0 ? `−${formatBytes(-n)}` : formatBytes(n))
const ratio = (n: number) => formatNumero(n, 2)
const entero = (n: number) => formatNumero(n)

type CampoNumerico = Exclude<keyof PuntoCuenta, 'dia' | 'grupo'>
const serie = (puntos: PuntoCuenta[], campo: CampoNumerico): PuntoSerie[] =>
  puntos.map(p => ({ dia: p.dia, valor: p[campo] }))

const celda = (n: number | null, formato: (n: number) => string) => (n === null ? SIN_DATO : formato(n))

const ICON_DESCARGA = svgIcon(<path d="M12 5v14M5 12l7 7 7-7" />)
const ICON_BONUS = svgIcon(<><circle cx="12" cy="12" r="9" /><path d="M12 7v10M9.5 9.5h3.75a1.75 1.75 0 0 1 0 3.5h-2.5a1.75 1.75 0 0 0 0 3.5H15" /></>)
const ICON_RATIO = svgIcon(<><path d="M4 19 10 13l4 4 6-7" /><path d="M15 10h5v5" /></>)
const ICON_GRUPO = svgIcon(<><circle cx="12" cy="8" r="4" /><path d="M5 21a7 7 0 0 1 14 0" /></>)

// El ratio dentro de DIAS_PROYECCION días si sigues al ritmo de la última semana; null sin ritmo o sin datos.
function ratioProyectado(ultimo: PuntoCuenta | undefined, r: RitmosCuenta): number | null {
  if (!ultimo || ultimo.subido_bytes === null || ultimo.bajado_bytes === null) return null
  if (r.subido_dia === null || r.bajado_dia === null) return null
  const bajado = ultimo.bajado_bytes + r.bajado_dia * DIAS_PROYECCION
  return bajado > 0 ? (ultimo.subido_bytes + r.subido_dia * DIAS_PROYECCION) / bajado : null
}

// Tu cuenta ahora y lo que crece: grupo, ritmos de la última semana y adónde va el ratio.
function TarjetasDeRitmo({ puntos, ritmos }: { puntos: PuntoCuenta[]; ritmos: RitmosCuenta }) {
  const ultimo = puntos[puntos.length - 1]
  const proyectado = ratioProyectado(ultimo, ritmos)
  const semana = `De las lecturas de los últimos ${ritmos.dias} días: la mediana de lo que cambia entre una y la siguiente.`
  return (
    <>
      <h2 className="est-grupo-titulo" id="est-grupo-ritmo">Tu cuenta, a este ritmo</h2>
      <div className="stat-cards-row" role="group" aria-labelledby="est-grupo-ritmo">
        <StatCard icon={ICON_GRUPO} label="Grupo" value={ultimo?.grupo ?? SIN_DATO}
          info={`Tu grupo en eMuwarez en la última lectura${ultimo?.grupo ? `: ${ultimo.grupo}` : ''}. Si cambia, se ve en la tabla de abajo.`} />
        <StatCard icon={ICON_UPLOADS} label="Subido al día" value={celda(ritmos.subido_dia, bytes)} info={semana} />
        <StatCard icon={ICON_DESCARGA} label="Bajado al día" value={celda(ritmos.bajado_dia, bytes)} info={semana} />
        <StatCard icon={ICON_BONUS} label="Bonus por hora" value={celda(ritmos.bonus_hora, n => formatNumero(n, 1))}
          info={`Estimado con lo que sube tu bonus entre lectura y lectura; los tramos en los que gastas no cuentan. eMuwarez no da la cifra exacta por la API. ${semana}`} />
        <StatCard icon={ICON_RATIO} label={`Ratio en ${DIAS_PROYECCION} días`} value={celda(proyectado, ratio)}
          info={`El ratio que tendrías dentro de ${DIAS_PROYECCION} días si subes y bajas al ritmo de la última semana.`} />
      </div>
    </>
  )
}

type Resultado = { dias: number; puntos: PuntoCuenta[] | null; ritmos: RitmosCuenta | null; error: string | null }

// Arriba, las cifras de tu biblioteca y de tus peticiones (las tarjetas que tenía el Panel); debajo, la evolución de
// tu cuenta de eMuwarez: una instantánea al día, que el servidor guarda al leer tu perfil (cada hora, con la tarea
// «Leer tu cuenta de eMuwarez»).
// Todo se pide al entrar en la sección y la evolución también al cambiar de rango; no hay sondeo.
export function EstadisticasPage() {
  const seVe = useSeccionVisible()
  const [resumen, setResumen] = useState<DashboardSummaryResponse | null>(null)
  const [dias, setDias] = useState(90)
  const [intento, setIntento] = useState(0)
  const [intentoResumen, setIntentoResumen] = useState(0)
  const [resumenFallo, setResumenFallo] = useState(false)
  const [resultado, setResultado] = useState<Resultado | null>(null)

  // Si falla, se quedan las cifras de la vez anterior (o ninguna) y una nota lo dice: la evolución de la cuenta es lo que
  // da la página, así que no la tapa.
  useEffect(() => {
    if (!seVe) return
    let vigente = true
    getDashboardSummary().then(
      r => { if (vigente) { setResumen(r); setResumenFallo(false) } },
      () => { if (vigente) setResumenFallo(true) },
    )
    return () => { vigente = false }
  }, [seVe, intentoResumen])

  useEffect(() => {
    if (!seVe) return
    let vigente = true
    getEstadisticasCuenta(dias).then(
      r => { if (vigente) setResultado({ dias, puntos: r.puntos, ritmos: r.ritmos, error: null }) },
      () => { if (vigente) setResultado({ dias, puntos: null, ritmos: null, error: 'No se pudo cargar la evolución de tu cuenta.' }) },
    )
    return () => { vigente = false }
  }, [seVe, dias, intento])

  // Al cambiar de rango se queda lo que había (atenuado) hasta que llegan los datos nuevos: si no, los gráficos
  // desaparecen, la página se acorta y el contenido salta. Tras un error o la primera carga no hay nada que conservar.
  const enCurso = resultado !== null && resultado.dias !== dias
  const sinMostrar = resultado === null || (enCurso && resultado.error !== null)
  const puntos = resultado?.puntos ?? []
  const diasMostrados = resultado?.dias ?? dias
  // Sin hover (una pantalla táctil) el gráfico se lee con el dedo, no con el ratón.
  const [tactil] = useState(() => window.matchMedia?.('(hover: none)').matches ?? false)

  return (
    <div className="est-pagina" data-tour="stats-content">
      <div className="page-header">
        <div>
          <h1 className="page-title">Estadísticas</h1>
          <div className="page-subtitle">Tu biblioteca y tus peticiones, y cómo evoluciona tu cuenta de eMuwarez: una instantánea al día, que se guarda sola cada hora.</div>
        </div>
        <div className="est-rangos" role="group" aria-label="Rango de días">
          {RANGOS.map(r => (
            <button key={r} type="button" className={`btn btn-sm ${r === dias ? 'btn-primary' : 'btn-secondary'}`}
              aria-pressed={r === dias} onClick={() => setDias(r)}>
              {r} días
            </button>
          ))}
        </div>
      </div>

      {/* Las tarjetas están desde el principio (con «—» hasta que llegan las cifras): si entraran después, empujarían
          hacia abajo lo que ya se ve. */}
      <TarjetasDelPanel resumen={resumen} />
      {resumenFallo && (
        <p className="est-nota est-aviso" role="status">
          No se pudieron cargar las cifras de la biblioteca y las peticiones{resumen ? '; se enseñan las de la última vez' : ''}.{' '}
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setIntentoResumen(n => n + 1)}>Reintentar</button>
        </p>
      )}

      {sinMostrar ? (
        <LoadingState label="Cargando estadísticas…" />
      ) : resultado.error ? (
        <ErrorState message={resultado.error} onRetry={() => { setResultado(null); setIntento(n => n + 1) }} />
      ) : puntos.length === 0 ? (
        <EmptyState
          title="Todavía no hay datos"
          body="Se empieza a guardar hoy: la primera instantánea sale en cuanto se lea tu perfil de eMuwarez, como mucho en una hora (hace falta su API key en Configuración)."
        />
      ) : (
        <div className={enCurso ? 'est-cargando' : undefined} aria-busy={enCurso}>
          {resultado.ritmos && <TarjetasDeRitmo puntos={puntos} ritmos={resultado.ritmos} />}
          {puntos.length === 1 ? (
            <p className="est-nota" role="status">Solo hay un día guardado: la línea aparecerá a partir de mañana.</p>
          ) : (
            <p className="est-nota">
              {puntos.length} de los últimos {diasMostrados} días tienen instantánea. {tactil
                ? 'Toca o desliza el dedo por un gráfico para ver cada día.'
                : 'Pasa el ratón por un gráfico, o enfócalo y usa las flechas, para ver cada día.'}
            </p>
          )}
          <div className="est-rejilla-graficos">
            <GraficoLinea titulo="Subido y bajado" formato={bytes} ancho
              series={[
                { nombre: 'Subido', clase: 'est-serie-subido', puntos: serie(puntos, 'subido_bytes') },
                { nombre: 'Bajado', clase: 'est-serie-bajado', puntos: serie(puntos, 'bajado_bytes') },
              ]} />
            <GraficoLinea titulo="Ratio" formato={ratio}
              series={[{ nombre: 'Ratio', clase: 'est-serie-ratio', puntos: serie(puntos, 'ratio') }]} />
            <GraficoLinea titulo="Bonus" formato={entero}
              series={[{ nombre: 'Bonus', clase: 'est-serie-bonus', puntos: serie(puntos, 'bonus') }]} />
            <GraficoLinea titulo="Buffer" formato={bytes}
              series={[{ nombre: 'Buffer', clase: 'est-serie-buffer', puntos: serie(puntos, 'buffer_bytes') }]} />
            <GraficoLinea titulo="Seeding y leeching" formato={entero}
              series={[
                { nombre: 'Seeding', clase: 'est-serie-seeding', puntos: serie(puntos, 'seeding') },
                { nombre: 'Leeching', clase: 'est-serie-leeching', puntos: serie(puntos, 'leeching') },
              ]} />
            <GraficoLinea titulo="H&R" formato={entero} desdeCero
              series={[{ nombre: 'H&R', clase: 'est-serie-hr', puntos: serie(puntos, 'hit_and_runs') }]} />
          </div>

          <details className="est-tabla">
            <summary>Ver los datos en una tabla ({puntos.length} {puntos.length === 1 ? 'día' : 'días'})</summary>
            {/* Con 365 filas la tabla se desplaza por dentro; con el foco en ella, se recorre con las flechas. */}
            <div className="est-tabla-marco" tabIndex={0} role="region" aria-label="Tabla de instantáneas diarias">
            <table className="data-table">
              <caption className="solo-lector">Instantáneas diarias de tu cuenta, de la más reciente a la más antigua</caption>
              <thead>
                <tr>
                  <th scope="col">Día</th><th scope="col">Ratio</th><th scope="col">Subido</th>
                  <th scope="col">Bajado</th><th scope="col">Bonus</th><th scope="col">Buffer</th>
                  <th scope="col">Seeding</th><th scope="col">Leeching</th><th scope="col">H&amp;R</th>
                  <th scope="col">Grupo</th>
                </tr>
              </thead>
              <tbody>
                {[...puntos].reverse().map(p => (
                  <tr key={p.dia}>
                    <th scope="row">{formatDia(p.dia)}</th>
                    <td>{celda(p.ratio, ratio)}</td>
                    <td>{celda(p.subido_bytes, bytes)}</td>
                    <td>{celda(p.bajado_bytes, bytes)}</td>
                    <td>{celda(p.bonus, entero)}</td>
                    <td>{celda(p.buffer_bytes, bytes)}</td>
                    <td>{celda(p.seeding, entero)}</td>
                    <td>{celda(p.leeching, entero)}</td>
                    <td>{celda(p.hit_and_runs, entero)}</td>
                    <td>{p.grupo ?? SIN_DATO}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </details>
        </div>
      )}

      <MisSubidasEmu visible={seVe} />
    </div>
  )
}
