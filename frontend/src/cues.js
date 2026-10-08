// Lógica pura del editor de cues (f48): formateo de tiempos, posiciones en la onda, el paso
// de un beat, el pad libre, la validación de un loop y qué tecla es qué atajo. Sin React ni
// DOM real, para poder probarla con `node --test` (test/cues.test.mjs).
//
// REGLA (spec §6): nada de esto inventa datos. El BPM, la duración y las marcas vienen de la
// API; acá solo se formatean, se acotan y se ubican.

export const HOT_CUES = 8

// "m:ss.mmm" (o con `dec` decimales). Siempre con signo de minutos aunque sea 0 ("0:07.250"):
// en la tabla todas las filas quedan alineadas. null/NaN → null (la pantalla dibuja "—").
export function fmtTiempo(s, dec = 3) {
  if (s === null || s === undefined || !Number.isFinite(Number(s))) return null
  const v = Math.max(0, Number(s))
  // Redondeo una sola vez, al final: si no, 59.9996 daba "0:60.000".
  const escala = 10 ** dec
  const total = Math.round(v * escala) / escala
  const min = Math.floor(total / 60)
  const seg = total - min * 60
  const [ent, frac] = seg.toFixed(dec).split('.')
  return `${min}:${ent.padStart(2, '0')}${dec > 0 ? `.${frac}` : ''}`
}

// Lo inverso, para editar un tiempo en la tabla: "1:23.456", "83.456", "1:23", "0:05.5".
// Devuelve segundos (con precisión de milisegundo) o null si no es un tiempo.
export function parseTiempo(texto) {
  if (typeof texto !== 'string') return null
  const t = texto.trim().replace(',', '.')
  const m = /^(?:(\d{1,3}):)?(\d{1,5})(?:\.(\d{1,3}))?$/.exec(t)
  if (!m) return null
  const min = m[1] === undefined ? 0 : Number(m[1])
  const seg = Number(m[2])
  if (m[1] !== undefined && seg >= 60) return null      // "1:75" no es un tiempo
  const ms = m[3] === undefined ? 0 : Number(m[3].padEnd(3, '0'))
  return (min * 60 * 1000 + seg * 1000 + ms) / 1000
}

// La duración de un beat según el BPM MEDIDO, o null si no hay BPM (no se inventa uno).
export function beatSegundos(bpm) {
  const b = Number(bpm)
  return bpm !== null && bpm !== undefined && Number.isFinite(b) && b > 0 ? 60 / b : null
}

export const acotar = (t, dur) => Math.min(Math.max(0, t), Math.max(0, dur))

// Mover el cursor ±1 beat desde `t`. NO es una grilla: no hay offset del primer beat, así que
// no se "ajusta" a nada, solo se suma o resta un período. null si no hay BPM.
export function pasoBeat(t, bpm, dir, dur) {
  const b = beatSegundos(bpm)
  if (b === null) return null
  return acotar(t + dir * b, dur)
}

// Posición en % del ancho para un tiempo.
export function pctDe(t, dur) {
  if (!(dur > 0)) return 0
  return Math.min(100, Math.max(0, (t / dur) * 100))
}

// El tiempo bajo una x (px desde el borde izquierdo de la onda de ancho `ancho`).
export function tiempoDeX(x, ancho, dur) {
  if (!(ancho > 0) || !(dur > 0)) return 0
  return acotar((x / ancho) * dur, dur)
}

// El primer pad libre (0..7) o null si están los 8.
export function padLibre(marcas) {
  const usados = new Set((marcas || []).filter((m) => m.tipo === 'cue').map((m) => m.num))
  for (let n = 0; n < HOT_CUES; n++) if (!usados.has(n)) return n
  return null
}

// El hot cue de un pad (0..7), o null.
export const hotCue = (marcas, num) => (marcas || []).find((m) => m.tipo === 'cue' && m.num === num) || null

// Por qué un loop no se puede cerrar, o null si se puede. Mismas reglas que el servidor
// (motor/cue_marks.validate_times); el servidor igual vuelve a validar.
export function errorLoop(entrada, salida, dur) {
  if (entrada === null || entrada === undefined) return 'Primero marcá la entrada del loop (I).'
  if (!(salida > entrada)) return 'La salida del loop tiene que quedar después de la entrada.'
  if (dur > 0 && salida > dur) return 'La salida del loop queda después del final del tema.'
  return null
}

// Cuántos beats dura un loop según el BPM medido, con un decimal; null sin BPM.
export function beatsDeLoop(entrada, salida, bpm) {
  const b = beatSegundos(bpm)
  if (b === null || !(salida > entrada)) return null
  return Math.round(((salida - entrada) / b) * 10) / 10
}

// ¿El evento viene de un lugar donde se está escribiendo? Ahí ningún atajo actúa.
export function escribiendo(target) {
  if (!target) return false
  if (target.isContentEditable) return true
  const tag = (target.tagName || '').toLowerCase()
  if (tag === 'textarea' || tag === 'select') return true
  if (tag !== 'input') return false
  const tipo = (target.type || 'text').toLowerCase()
  return !['button', 'submit', 'reset', 'image', 'range', 'color', 'file'].includes(tipo)
}

// La acción de un atajo, o null. `e` es un KeyboardEvent (o algo con key, ctrlKey...).
//   play · cue · memory · loopIn · loopOut · beat(±1) · ir(num 0..7)
export function atajo(e) {
  if (!e || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return null
  if (escribiendo(e.target)) return null
  const k = e.key
  if (k === ' ' || k === 'Spacebar') return { tipo: 'play' }
  if (k === 'ArrowLeft') return { tipo: 'beat', dir: -1 }
  if (k === 'ArrowRight') return { tipo: 'beat', dir: 1 }
  if (/^[1-8]$/.test(k)) return { tipo: 'ir', num: Number(k) - 1 }
  switch ((k || '').toLowerCase()) {
    case 'c': return { tipo: 'cue' }
    case 'm': return { tipo: 'memory' }
    case 'i': return { tipo: 'loopIn' }
    case 'o': return { tipo: 'loopOut' }
    default: return null
  }
}

// Marcas de la regla de tiempo: un paso "redondo" que deje como mucho `max` etiquetas.
export function marcasRegla(dur, max = 6) {
  if (!(dur > 0)) return []
  const pasos = [5, 10, 15, 30, 60, 120, 300, 600]
  const cuantas = (p) => Math.floor(dur / p + 1e-9) + 1
  const paso = pasos.find((p) => cuantas(p) <= max) || Math.ceil(dur / (max - 1) / 60) * 60
  const out = []
  for (let t = 0; t <= dur + 1e-9; t += paso) out.push(t)
  return out
}

// "m:ss" para la regla (sin decimales).
export function fmtRegla(s) {
  const v = Math.max(0, Math.round(s))
  return `${Math.floor(v / 60)}:${String(v % 60).padStart(2, '0')}`
}
