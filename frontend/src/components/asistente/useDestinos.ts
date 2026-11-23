// A qué trackers se sube: los que se puede (eMuwarez y los que tienen clave) y los elegidos, eMuwarez de entrada. Lo
// usan el Resumen del asistente (una subida o una tanda) y el de «Subir por lotes».
import { useEffect, useState } from 'react'
import { fetchTrackersParaSubir, type TrackerParaSubir } from '../../api/uploads'

export function useDestinos() {
  const [trackersParaSubir, setTrackersParaSubir] = useState<TrackerParaSubir[]>([])
  const [trackersElegidos, setTrackersElegidos] = useState<string[]>(['emu'])
  useEffect(() => {
    fetchTrackersParaSubir().then(setTrackersParaSubir).catch(() => { /* solo eMuwarez, como siempre */ })
  }, [])
  const elegirTracker = (id: string, si: boolean) =>
    setTrackersElegidos(t => si ? [...new Set([...t, id])] : t.filter(x => x !== id))
  // Los elegidos, en el orden de la lista (eMuwarez primero). Sin lista (no se pudo leer), eMuwarez, como siempre.
  const destinos = trackersParaSubir.length
    ? trackersParaSubir.filter(t => trackersElegidos.includes(t.id))
    : [{ id: 'emu', nombre: 'eMuwarez' }]
  // «eMuwarez», «eMuwarez y N.O.B.S»: lo que dicen el botón y la confirmación.
  const destinoSubida = destinos.length > 1
    ? `${destinos.slice(0, -1).map(t => t.nombre).join(', ')} y ${destinos[destinos.length - 1].nombre}`
    : destinos[0]?.nombre ?? ''
  return { trackersParaSubir, trackersElegidos, setTrackersElegidos, elegirTracker, destinos, destinoSubida }
}
