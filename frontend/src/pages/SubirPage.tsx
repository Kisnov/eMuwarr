// La pantalla del asistente de subida: /subir/<id>. No tiene entrada fija en el menú: se llega desde «Subir» en
// Biblioteca, el Panel o Peticiones, y mientras haya una subida en preparación el menú ofrece volver a ella.
import { useState } from 'react'
import { getLibraryItem } from '../api/library'
import { PantallaAsistente } from '../components/asistente/PantallaAsistente'
import { LoadingState } from '../components/LoadingState'
import { useAlEntrar } from '../context/SeccionVisible'
import {
  avisarSubidaEncolada, cerrarAsistente, restaurarAsistente, salirDelAsistente, SECCION_ASISTENTE, sesionGuardada,
  useSesionAsistente,
} from '../utils/asistenteSesion'
import { esDeLaSeccion, getDetailIdFromPath } from '../utils/detailUrl'

export function SubirPage({ onNavigate }: { onNavigate: (page: string) => void }) {
  const { sesion } = useSesionAsistente()
  const [recuperando, setRecuperando] = useState(false)

  // Sin subida en preparación solo se llega aquí recargando la pestaña o con Atrás: lo apuntado en la pestaña dice
  // si hay una que rehacer; si no, esta pantalla no tiene nada que enseñar.
  useAlEntrar(() => {
    if (sesion || recuperando) return
    const guardada = sesionGuardada()
    if (!guardada || guardada.id !== getDetailIdFromPath(SECCION_ASISTENTE)) {
      salirDelAsistente(guardada?.origen ?? 'library')
      return
    }
    setRecuperando(true)
    getLibraryItem(guardada.id)
      .then(item => restaurarAsistente(item, guardada))
      .catch(() => { cerrarAsistente(); salirDelAsistente(guardada.origen) })
      .finally(() => setRecuperando(false))
  })

  if (!sesion) return recuperando ? <LoadingState /> : null
  const cerrar = () => {
    cerrarAsistente()
    // Tras encolar ya se ha ido a Subidas; cerrando a mano se vuelve a donde se abrió.
    if (esDeLaSeccion(window.location.pathname, SECCION_ASISTENTE)) onNavigate(sesion.origen)
  }
  return (
    <PantallaAsistente
      key={sesion.n} item={sesion.item} preselect={sesion.preselect} requestId={sesion.requestId}
      onClose={cerrar} onDone={avisarSubidaEncolada} onNavigate={onNavigate}
    />
  )
}
