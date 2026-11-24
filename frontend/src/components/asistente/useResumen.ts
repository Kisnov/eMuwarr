// Lo que el Resumen pide fuera del asistente: si el catálogo local ya tiene lo que se sube y qué petición cubre.
import { useEffect, useState } from 'react'
import { getEmuComparison, getLibraryItem } from '../../api/library'
import { getRequestDetail } from '../../api/requests'
import type { TrackerParaSubir } from '../../api/uploads'
import { resumirComparaciones, textoPeticion, type PeticionResumen, type YaHay } from '../../utils/resumenSubida'

export function useResumen({ ids, trackers, tuya, requestId, cubiertos, alcance }: {
  ids: readonly number[]
  // Los trackers elegidos: de cada uno, lo que ya hay en su catálogo.
  trackers: readonly TrackerParaSubir[]
  // Lo que irá en el nombre; solo con un elemento (en una tanda cada uno lleva el suyo).
  tuya?: { resolution?: string | null; source?: string | null }
  requestId?: number
  // Cuántos de `ids` cubre la petición de la que se abrió el asistente.
  cubiertos: number
  // Una frase del tipo «esta temporada»; null en una tanda, donde no se mira si hay otra abierta.
  alcance: string | null
}) {
  const [yaHay, setYaHay] = useState<ReadonlyMap<string, YaHay>>(() => new Map())
  const [peticion, setPeticion] = useState<PeticionResumen | null>(null)
  const clave = ids.join(',')
  const claveTrackers = trackers.map(t => t.id).join(',')

  useEffect(() => {
    if (ids.length === 0) return
    let vivo = true
    setYaHay(new Map())
    Promise.all(trackers.map(t => Promise.all(ids.map(id => getEmuComparison(id, tuya, t.id).catch(() => null)))))
      .then(porTracker => {
        if (vivo) setYaHay(new Map(trackers.map((t, i) => [t.id, resumirComparaciones(porTracker[i])])))
      })
    return () => { vivo = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- se pide al entrar en el paso o cambiar lo que se sube, no con cada pintado
  }, [clave, claveTrackers, tuya?.resolution, tuya?.source])

  useEffect(() => {
    if (ids.length === 0) return
    let vivo = true
    setPeticion(null)
    const pedir = async (): Promise<PeticionResumen> => {
      const sinAlcance = alcance ?? 'estos elementos'
      if (requestId != null && cubiertos > 0) {
        const r = await getRequestDetail(requestId).catch(() => null)
        return textoPeticion({
          enlazada: { titulo: r?.title ?? `#${requestId}`, puntos: r?.points ?? null },
          sinEnlazar: null, cubre: { cuantos: cubiertos, de: ids.length }, alcance: sinAlcance,
        })
      }
      // Una tanda no mira la ficha de cada elemento: dice solo que no enlaza ninguna.
      if (alcance == null) return { texto: 'Ninguna enlazada', aviso: false }
      const ficha = await getLibraryItem(ids[0]).catch(() => null)
      if (!ficha) return { texto: 'No se pudo comprobar si hay una abierta', aviso: false }
      return textoPeticion({ enlazada: null, sinEnlazar: ficha.peticion?.title ?? null, alcance })
    }
    pedir().then(p => { if (vivo) setPeticion(p) })
    return () => { vivo = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- igual que arriba
  }, [clave, requestId, cubiertos, alcance])

  // De cada tracker elegido, lo que ya hay; mientras se lee, «cargando».
  const yaHayEn = (id: string): YaHay => yaHay.get(id) ?? { estado: 'cargando' }
  return { yaHayEn, peticion }
}
