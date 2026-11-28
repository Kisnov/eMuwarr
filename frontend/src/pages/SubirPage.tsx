// La pantalla del asistente de subida: /subir/<id>. No tiene entrada fija en el menú: se llega desde «Subir» en
// Biblioteca, el Panel o Peticiones, y mientras haya una subida en preparación el menú ofrece volver a ella.
import { useEffect, useRef, useState } from 'react'
import { PantallaAsistente } from '../components/asistente/PantallaAsistente'
import { LoadingState } from '../components/LoadingState'
import { useAlEntrar, useSeccionVisible } from '../context/SeccionVisible'
import {
  avisarSubidaEncolada, cerrarAsistente, esLaSesion, recuperarAsistente, salirDelAsistente, SECCION_ASISTENTE,
  sesionGuardada, useSesionAsistente,
} from '../utils/asistenteSesion'
import { esDeLaSeccion, getDetailIdFromPath } from '../utils/detailUrl'

export function SubirPage({ onNavigate }: { onNavigate: (page: string) => void }) {
  const { sesion } = useSesionAsistente()
  const seVe = useSeccionVisible()
  const [recuperando, setRecuperando] = useState(false)

  // Sin subida en preparación solo se llega aquí recargando la pestaña o con Atrás: lo apuntado en la pestaña dice
  // si hay una que rehacer; si no, esta pantalla no tiene nada que enseñar.
  useAlEntrar(() => {
    if (sesion || recuperando) return
    const origen = sesionGuardada()?.origen ?? 'library'
    setRecuperando(true)
    void recuperarAsistente().then(hay => {
      setRecuperando(false)
      // Si mientras tanto se fue a otra sección, ya no hay de dónde sacarle.
      if (!hay && esDeLaSeccion(window.location.pathname, SECCION_ASISTENTE)) salirDelAsistente(origen)
    })
  })

  // Con una subida a la vista: la URL es la suya (se pudo llegar con Atrás a la de otra ya cerrada), y una recién
  // abierta empieza arriba, no donde se dejó la anterior.
  const vista = useRef<number | null>(null)
  useEffect(() => {
    if (!seVe || !sesion) return
    if (getDetailIdFromPath(SECCION_ASISTENTE) !== sesion.item.id) {
      window.history.replaceState({}, '', `/${SECCION_ASISTENTE}/${sesion.item.id}`)
    }
    if (vista.current !== sesion.n) {
      vista.current = sesion.n
      document.getElementById('contenido')?.scrollTo?.(0, 0)
    }
  }, [seVe, sesion])

  if (!sesion) return recuperando ? <LoadingState /> : null
  // Un asistente dejado por otra subida puede seguir respondiendo (una petición que llega tarde): ya no cierra ni
  // lleva a ningún sitio.
  const vigente = () => esLaSesion(sesion.n)
  const cerrar = () => {
    if (!vigente()) return
    cerrarAsistente()
    // Tras encolar ya se ha ido a Subidas; cerrando a mano se vuelve a donde se abrió.
    if (esDeLaSeccion(window.location.pathname, SECCION_ASISTENTE)) onNavigate(sesion.origen)
  }
  return (
    <PantallaAsistente
      key={sesion.n} item={sesion.item} preselect={sesion.preselect} requestId={sesion.requestId}
      onClose={cerrar} onDone={avisarSubidaEncolada} onNavigate={seccion => { if (vigente()) onNavigate(seccion) }}
    />
  )
}
