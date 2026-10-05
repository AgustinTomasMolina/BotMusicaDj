// Orden "Para mezclar" de la Station (f45). Opt-in: el orden de SoundCloud es el default.
//
// El análisis de audio corre EN PARALELO a la búsqueda de versiones (stationVersions.js), con
// su propia cola chica (`CONCURRENCIA_ANALISIS`): no la traba. El orden "Para mezclar" se pide
// UNA vez, cuando terminó el análisis de todos: así las filas no saltan cada vez que llega un
// dato (decisión: más claro y estable que recalcular a medida que llegan).
import { stationAnalisis } from './api'

export const CONCURRENCIA_ANALISIS = 2
export const TIMEOUT_ANALISIS_MS = 90000

// Analiza `refs` (ids de SoundCloud). `onUno(ref, resultado)` por cada uno, en el orden en que
// terminan; `onFin()` al terminar todos. Devuelve `cancelar()`.
export function cargarAnalisis(refs, { onUno, onFin, concurrencia = CONCURRENCIA_ANALISIS, timeoutMs = TIMEOUT_ANALISIS_MS } = {}) {
  let cancelado = false
  let proximo = 0
  let hechos = 0
  const enVuelo = new Set()
  const pedir = async (ref) => {
    const ctrl = new AbortController()
    enVuelo.add(ctrl)
    const t = setTimeout(() => ctrl.abort(), timeoutMs)
    let r
    try { r = await stationAnalisis(ref, ctrl.signal) }
    catch { r = { ok: false, motivo: 'No se pudo analizar (sin respuesta del servidor).' } }
    finally { clearTimeout(t); enVuelo.delete(ctrl) }
    if (cancelado) return
    onUno?.(ref, r)
    hechos++
    if (hechos === refs.length) { cancelado = true; onFin?.() }
  }
  const trabajador = async () => {
    while (!cancelado && proximo < refs.length) await pedir(refs[proximo++])
  }
  if (!refs.length) { onFin?.(); return () => {} }
  for (let k = 0; k < Math.min(concurrencia, refs.length); k++) trabajador()
  return () => { cancelado = true; for (const c of enVuelo) c.abort() }
}

// Índices de `groups` en el orden en que se muestran. Con 'soundcloud' (o sin orden calculado)
// es la identidad: el orden de la Station tal cual. Con 'mezcla', el que devolvió el backend;
// una fila que el orden no nombra (no debería pasar) va al final, nunca se pierde.
export function ordenVisible(groups, modo, mezcla) {
  const base = groups.map((_, i) => i)
  if (modo !== 'mezcla' || !mezcla?.exito || !Array.isArray(mezcla.orden)) return base
  const porRef = new Map()
  groups.forEach((g, i) => {
    const ref = String(g.base?.video_id ?? '')
    if (!porRef.has(ref)) porRef.set(ref, i)
  })
  const vistos = new Set()
  const out = []
  for (const f of mezcla.orden) {
    const i = porRef.get(String(f.video_id))
    if (i !== undefined && !vistos.has(i)) { vistos.add(i); out.push(i) }
  }
  for (const i of base) if (!vistos.has(i)) out.push(i)
  return out
}

// Lo que dice la fila sobre su lugar en el orden "Para mezclar": el "por qué" del motor o el
// motivo por el que quedó al final. null fuera de ese modo.
export function filaMezcla(g, modo, mezcla) {
  if (modo !== 'mezcla' || !mezcla?.exito) return null
  return mezcla.orden.find((f) => String(f.video_id) === String(g.base?.video_id ?? '')) || null
}

// BPM y key MEDIDOS, como se muestran (§6): BPM con un decimal, key con "?" si es dudosa, y
// "?" en los dos si no se pudo medir. Nunca el BPM/key de metadata.
export function medidoTexto(a) {
  if (!a || !a.ok) return { bpm: '?', key: '?' }
  const bpm = typeof a.bpm === 'number' && Number.isFinite(a.bpm) ? a.bpm.toFixed(1) : '?'
  const key = a.key ? `${a.key}${a.key_dudosa ? '?' : ''}` : '?'
  return { bpm, key }
}
