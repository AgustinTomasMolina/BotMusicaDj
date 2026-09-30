// Versiones de los temas de la Station, fila por fila (f36).
//
// Un pedido a /api/versiones por tema, con una cola de `concurrencia` pedidos a la vez: fluye
// sin saturar al backend (cada fila abre 4 búsquedas y hasta 5 notas) ni a SoundCloud. Las
// filas se entregan EN EL ORDEN DE LA STATION: una fila lista espera a que estén las de arriba,
// así la lista crece hacia abajo y nada salta de lugar. Cada pedido tiene su timeout: si una
// fila tarda demasiado se entrega igual, con la versión de SoundCloud y el motivo.
import { versiones } from './api'
import { GRADE_RANK, seedCalidad } from './components/common'

export const CONCURRENCIA = 3
export const TIMEOUT_FILA_MS = 45000

// La versión que viene elegida en una fila: la de mayor nota con el mismo ranking que el
// comparador (GRADE_RANK); a igual nota gana la primera (el backend las ordena por fuente).
// Nunca un preview de 30 s (no es el tema) ni Spotify (se baja buscando en YouTube, sin
// verificar que sea este tema). Si no hay otra, queda la de SoundCloud: marcada y sin descarga.
export function bestOption(opciones) {
  let best = -1, bestRank = -1
  opciones.forEach((o, i) => {
    const f = (o.fuente || '').toLowerCase()
    if (o.solo_preview || f === 'spotify' || f === 'deezer') return
    const r = o.calidad?.ok ? (GRADE_RANK[o.calidad.grade] ?? 0) : 0
    if (r > bestRank) { bestRank = r; best = i }
  })
  if (best >= 0) return best
  const base = opciones.findIndex((o) => o.estacion)
  return base >= 0 ? base : 0
}

// Grupo de una fila: {opciones, sel, motivo, base}. `base` es el tema de la Station tal cual
// (para el título de la fila y sus metadatos aunque se elija otra versión).
function grupo(item, d) {
  const opciones = d && d.exito && Array.isArray(d.opciones) && d.opciones.length
    ? d.opciones
    : [{ ...item, estacion: true }]
  for (const o of opciones) if (o.calidad) seedCalidad(o, o.calidad)
  const motivo = d && d.exito ? (d.motivo || null) : ((d && d.mensaje) || 'No pude buscar versiones de este tema.')
  return { opciones, sel: bestOption(opciones), motivo, base: item }
}

// Arranca la carga. `onFila(i, grupo)` se llama en orden (0, 1, 2…); `onFin()` al terminar.
// Devuelve `cancelar()`: corta los pedidos en vuelo y no entrega nada más.
export function cargarVersiones(items, formato, { onFila, onFin, concurrencia = CONCURRENCIA, timeoutMs = TIMEOUT_FILA_MS } = {}) {
  let cancelado = false
  let proximo = 0          // siguiente índice a pedir
  let entregar = 0         // siguiente índice a entregar (orden de la Station)
  const listos = new Map()
  const enVuelo = new Set()

  const vaciar = () => {
    while (!cancelado && listos.has(entregar)) {
      const g = listos.get(entregar)
      listos.delete(entregar)
      onFila?.(entregar, g)
      entregar++
    }
    if (!cancelado && entregar === items.length) { cancelado = true; onFin?.() }
  }

  const pedir = async (i) => {
    const item = items[i]
    const ctrl = new AbortController()
    enVuelo.add(ctrl)
    let porTiempo = false
    const t = setTimeout(() => { porTiempo = true; ctrl.abort() }, timeoutMs)
    let g
    try {
      g = grupo(item, await versiones(item, formato, ctrl.signal))
    } catch {
      g = grupo(item, { exito: false, mensaje: porTiempo
        ? `Tardó más de ${Math.round(timeoutMs / 1000)} s en buscar versiones: queda la de SoundCloud.`
        : 'No pude conectar con el servidor para buscar versiones: queda la de SoundCloud.' })
    } finally {
      clearTimeout(t)
      enVuelo.delete(ctrl)
    }
    if (cancelado) return
    listos.set(i, g)
    vaciar()
  }

  const trabajador = async () => {
    while (!cancelado && proximo < items.length) await pedir(proximo++)
  }
  if (!items.length) { onFin?.(); return () => {} }
  for (let k = 0; k < Math.min(concurrencia, items.length); k++) trabajador()

  return () => {
    cancelado = true
    for (const c of enVuelo) c.abort()
  }
}
