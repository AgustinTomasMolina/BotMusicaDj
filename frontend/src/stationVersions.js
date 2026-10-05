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
// El peor caso del server por fila (`presupuesto_fila_s` en server.py) tiene que quedar debajo
// de esto con margen: un test de Python lo lee de acá y lo compara (f40-r2).
export const TIMEOUT_FILA_MS = 45000

// La versión que viene elegida en una fila: la de mayor nota con el mismo ranking que el
// comparador (GRADE_RANK); a igual nota gana la primera (el backend las ordena por fuente).
// Nunca un preview de 30 s (no es el tema) ni Spotify (se baja buscando en YouTube, sin
// verificar que sea este tema). Si no hay otra, queda la de SoundCloud: marcada y sin descarga.
// Sin nota (no se pudo medir, "?") va entre la F y la D (f40): una F es un audio MEDIDO como
// malo y no le puede ganar a uno que no se sabe; uno que no se sabe tampoco le gana a una D medida.
//
// f40-r2 (decisiones del dueño):
// - Una versión cuya duración NO se pudo verificar (`duracion_verificada: false`, p. ej. un MP3
//   de HitPlayer sin cabecera) se ofrece pero NUNCA viene elegida: «Argy - Aria» de HitPlayer
//   sin duración resultó ser otra edición (252 s de un tema de 315 s) y era la elegida. Si todas
//   las demás son así, queda la de SoundCloud.
// - El Extended del tema (`edicion: "extended"`, con la duración verificada) le gana a las del
//   mismo largo, sea cual sea la nota: "el extended dura más, y para los DJ eso es ORO". Entre
//   varios Extended, la nota.
//
// f40-r3 (decisión del dueño): un Extended con nota D o F NO viene elegido ("un tema largo que
// suena mal no sirve para pasar"): gana la mejor del mismo largo y el Extended queda como
// pastilla para elegirlo a mano. Con A/B/C o sin nota (y la duración verificada) sigue ganando.
export const SIN_NOTA_RANK = (GRADE_RANK.F + GRADE_RANK.D) / 2
const EXTENDED_RANK = 100     // por encima de cualquier nota (GRADE_RANK va de 1 a 6)
const EXTENDED_NO_ELEGIBLE = new Set(['D', 'F'])
export function bestOption(opciones) {
  let best = -1, bestRank = -1
  opciones.forEach((o, i) => {
    const f = (o.fuente || '').toLowerCase()
    if (o.solo_preview || f === 'spotify' || f === 'deezer') return
    if (o.duracion_verificada === false) return
    const g = o.calidad?.ok ? o.calidad.grade : null
    if (o.edicion === 'extended' && EXTENDED_NO_ELEGIBLE.has(g)) return
    const r = (g && g !== '?' && g in GRADE_RANK ? GRADE_RANK[g] : SIN_NOTA_RANK)
      + (o.edicion === 'extended' ? EXTENDED_RANK : 0)
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
// `onPedido(i)` (f46, solo aviso) cuando sale el pedido del tema i: la fila pendiente dice
// "Buscando versiones…" si ya se pidió y "En cola" si no. No cambia la cola.
// Devuelve `cancelar()`: corta los pedidos en vuelo y no entrega nada más.
export function cargarVersiones(items, formato, { onFila, onFin, onPedido, concurrencia = CONCURRENCIA, timeoutMs = TIMEOUT_FILA_MS } = {}) {
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
    onPedido?.(i)
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
