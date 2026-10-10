// Lógica pura del editor grande de cues (f54, opción D del canvas): la ventana de la onda y su
// zoom, el mapeo tiempo ↔ pixel, la grilla de beats ESTIMADA (líneas, ajuste del dueño, snap),
// el deshacer/rehacer de las marcas y los atajos. Sin React ni DOM, para probarla con
// `node --test` (test/editorD.test.mjs).
//
// REGLAS (spec §6)
// - La grilla es una ESTIMACIÓN del motor (`/onda3` → `grilla`), nunca la verdad: sin
//   `primer_beat_s` no hay líneas (y se dice por qué), con confianza baja se dibuja atenuada, y
//   el ajuste que haga el dueño (medio beat, fino) vive solo en este navegador.
// - Nada se inventa: sin grilla el zoom va en segundos (no en compases de un BPM que no se
//   midió), sin BPM no hay paso de beat, y el compás (cuál beat es el 1) se marca solo si el
//   motor lo estimó (`compas_ref`).

import { fmtTiempo } from './cues.js'

export const ZOOM_COMPASES = [2, 4, 8, 16, 32]
// El equivalente sin grilla: segundos a la vista. Mismo largo de lista que ZOOM_COMPASES.
export const ZOOM_SEGUNDOS = [4, 8, 15, 30, 60]
export const ZOOM_DEFECTO = 2                 // 8 compases (o 15 s), como el tablero D
export const CONFIANZA_BAJA = 0.35            // debajo, la grilla va atenuada y sin snap por defecto
// La «nitidez» es la `concentracion` de f52: qué parte de los ataques cae sobre una línea. La
// confianza sola engaña (auditoría f54): con un acorde sostenido bajo los kicks la fase quedó
// 56 ms antes con confianza 1,00 y concentración 0,37. Debajo de esto también va atenuada.
// Es un umbral de PANTALLA: los de f52 (cuándo hay grilla) no se tocan.
export const NITIDEZ_BAJA = 0.45
export const AJUSTE_FINO_S = 0.005            // «fino»: 5 ms
export const AJUSTE_MAX_BEATS = 4             // el ajuste no se va más de 4 beats para cada lado
export const PADS = 'ABCDEFGH'
export const LOOP_BEATS = [1, 2, 4, 8, 16]

const finito = (x) => typeof x === 'number' && Number.isFinite(x)

// ---------------------------------------------------------------------- grilla

// ¿Hay grilla que dibujar? `g` es `grilla` de /onda3 (o null: el track no tiene BPM medido).
// Devuelve { hay, atenuada, baja, motivo }: `motivo` es por qué NO hay (para decirlo en
// pantalla) y `baja` qué la atenúa: 'confianza', 'nitidez', las dos (array) o [].
export function estadoGrilla(g) {
  if (!g) return { hay: false, atenuada: false, baja: [], motivo: 'el tema no tiene BPM medido' }
  if (!finito(g.primer_beat_s) || !finito(g.periodo_s) || !(g.periodo_s > 0)) {
    return { hay: false, atenuada: false, baja: [], motivo: g.motivo || 'el motor no pudo estimar dónde cae el beat' }
  }
  const c = finito(g.confianza) ? g.confianza : 0
  // Sin concentración (una respuesta vieja) no se puede afirmar que es nítida: cuenta como baja.
  const n = finito(g.concentracion) ? g.concentracion : 0
  const baja = [...(c < CONFIANZA_BAJA ? ['confianza'] : []), ...(n < NITIDEZ_BAJA ? ['nitidez'] : [])]
  return { hay: true, atenuada: baja.length > 0, baja, motivo: null }
}

// El período de un beat para moverse de a un beat: el de la grilla si la hay (afinado contra
// los kicks), si no 60/BPM medido, si no null (sin BPM no hay beat).
export function periodoBeat(g, bpm) {
  if (estadoGrilla(g).hay) return g.periodo_s
  const b = Number(bpm)
  return bpm !== null && bpm !== undefined && Number.isFinite(b) && b > 0 ? 60 / b : null
}

// Las líneas de la grilla en [desde, hasta] con el ajuste del dueño (`offset` en segundos).
// Cada línea: { t, beat } con `beat` = 1..4 dentro del compás si el motor estimó el compás
// (`compas_ref`), o null si no se sabe cuál es el 1 (todas iguales, ninguna «gruesa»).
export function lineasGrilla(g, offset, desde, hasta) {
  if (!estadoGrilla(g).hay || !(hasta > desde)) return []
  const p = g.periodo_s
  const off = finito(offset) ? offset : 0
  const fase = g.primer_beat_s + off
  const bpc = Number.isInteger(g.beats_por_compas) && g.beats_por_compas > 0 ? g.beats_por_compas : 4
  const ref = finito(g.compas_ref) ? g.compas_ref + off : null
  const j0 = Math.ceil((desde - fase) / p - 1e-9)
  const j1 = Math.floor((hasta - fase) / p + 1e-9)
  const out = []
  for (let j = j0; j <= j1; j++) {
    const t = fase + j * p
    if (t < 0) continue
    let beat = null
    if (ref !== null) {
      const k = Math.round((t - ref) / p)
      beat = (((k % bpc) + bpc) % bpc) + 1
    }
    out.push({ t: Math.round(t * 1e6) / 1e6, beat })
  }
  return out
}

// El tiempo `t` llevado a la línea de grilla más cercana, o `t` tal cual si no hay grilla.
export function snapGrilla(t, g, offset) {
  if (!estadoGrilla(g).hay || !finito(t)) return t
  const p = g.periodo_s
  const fase = g.primer_beat_s + (finito(offset) ? offset : 0)
  const s = fase + Math.round((t - fase) / p) * p
  // Antes del 0 no hay línea: la primera real es la siguiente. Siempre a ms (lo que guarda la base).
  return Math.round((s < 0 ? s + p : s) * 1000) / 1000
}

// Correr la grilla: `delta` en segundos sobre el ajuste actual, acotado a ±AJUSTE_MAX_BEATS.
export function moverAjuste(offset, delta, periodo) {
  const v = (finito(offset) ? offset : 0) + (finito(delta) ? delta : 0)
  const max = finito(periodo) && periodo > 0 ? AJUSTE_MAX_BEATS * periodo : Infinity
  return Math.round(Math.min(max, Math.max(-max, v)) * 1e6) / 1e6
}

// El ajuste dicho para el dueño: «+½ beat −5 ms», «sin ajuste».
export function textoAjuste(offset, periodo) {
  if (!finito(offset) || Math.abs(offset) < 0.0005) return 'sin ajuste'
  const signo = (x) => (x < 0 ? '−' : '+')
  if (finito(periodo) && periodo > 0) {
    const medios = Math.round(offset / (periodo / 2))
    const resto = Math.round((offset - medios * (periodo / 2)) * 1000)
    const partes = []
    if (medios !== 0) {
      const n = Math.abs(medios)
      partes.push(`${signo(medios)}${n % 2 === 0 ? `${n / 2} beat${n / 2 === 1 ? '' : 's'}` : `${n === 1 ? '' : `${(n - 1) / 2} y `}½ beat`}`)
    }
    if (resto !== 0) partes.push(`${signo(resto)}${Math.abs(resto)} ms`)
    return partes.join(' ') || 'sin ajuste'
  }
  return `${signo(offset)}${Math.abs(Math.round(offset * 1000))} ms`
}

// --------------------------------------------------- ajuste guardado en el navegador

const CLAVE = 'musiflix.editorD.grilla.'

// El ajuste de la grilla de un tema, tal como quedó en ESTE navegador. `storage` es
// localStorage (o uno falso en los tests); si no se puede leer, el de fábrica.
export function leerAjuste(storage, id) {
  const fabrica = { offset: 0, oculta: false }
  try {
    const crudo = storage && storage.getItem(CLAVE + id)
    if (!crudo) return fabrica
    const d = JSON.parse(crudo)
    return { offset: finito(d.offset) ? d.offset : 0, oculta: d.oculta === true }
  } catch { return fabrica }
}

export function guardarAjuste(storage, id, ajuste) {
  try {
    if (!storage) return false
    if (!(ajuste.offset) && !ajuste.oculta) storage.removeItem(CLAVE + id)
    else storage.setItem(CLAVE + id, JSON.stringify({ offset: ajuste.offset || 0, oculta: !!ajuste.oculta }))
    return true
  } catch { return false }
}

// ---------------------------------------------------------------------- zoom y ventana

// Segundos a la vista con el zoom `i`: compases × beats × período si hay grilla; si no, segundos.
export function anchoZoom(i, g) {
  const k = Math.min(ZOOM_COMPASES.length - 1, Math.max(0, i | 0))
  const e = estadoGrilla(g)
  if (e.hay) {
    const bpc = Number.isInteger(g.beats_por_compas) && g.beats_por_compas > 0 ? g.beats_por_compas : 4
    return ZOOM_COMPASES[k] * bpc * g.periodo_s
  }
  return ZOOM_SEGUNDOS[k]
}

// La ventana [desde, hasta] de `ancho` segundos centrada en `centro`, sin salirse del tema.
export function ventana(centro, ancho, dur) {
  if (!(dur > 0)) return { desde: 0, hasta: Math.max(0, ancho) }
  if (!(ancho > 0) || ancho >= dur) return { desde: 0, hasta: dur }
  const desde = Math.min(dur - ancho, Math.max(0, (finito(centro) ? centro : 0) - ancho / 2))
  return { desde, hasta: desde + ancho }
}

// Tiempo → x (px desde el borde izquierdo de la onda de ancho `px`) y al revés.
export function xDeTiempo(t, v, px) {
  const w = v.hasta - v.desde
  if (!(w > 0) || !(px > 0)) return 0
  return ((t - v.desde) / w) * px
}
export function tiempoDeXV(x, v, px) {
  const w = v.hasta - v.desde
  if (!(w > 0) || !(px > 0)) return v.desde
  return v.desde + (x / px) * w
}

// Qué pedirle a /onda3 para una ventana: el tramo de la ventana con una ventana más de cada
// lado (arrastrar no pide en cada movimiento) y la resolución de la pantalla. `durAudio` es la
// duración del audio decodificado (el backend no acepta `hasta` más allá).
export function tramoPedido(v, durAudio, px) {
  const ancho = v.hasta - v.desde
  const desde = Math.max(0, v.desde - ancho)
  const hasta = Math.min(durAudio, v.hasta + ancho)
  const puntos = Math.max(1, Math.min(4000, Math.ceil(px * ((hasta - desde) / ancho))))
  return { desde: Math.round(desde * 1000) / 1000, hasta: Math.round(hasta * 1000) / 1000, puntos }
}

// ¿Lo que ya se trajo (`d`: {desde, hasta, puntos}) alcanza para dibujar la ventana `v` con
// `px` columnas? Tiene que cubrirla y tener al menos la mitad de la resolución de la pantalla
// (o todos los cuadros de 10 ms que hay: el backend no inventa más).
export function alcanza(d, v, px, durAudio) {
  if (!d || !(d.hasta > d.desde)) return false
  const fin = Math.min(v.hasta, durAudio)
  if (d.desde > v.desde + 0.011 || d.hasta < fin - 0.011) return false
  const porSeg = d.puntos / (d.hasta - d.desde)
  const pide = px / (v.hasta - v.desde)
  return porSeg >= Math.min(pide / 2, 100 - 1e-9)
}

// Columnas a dibujar: para cada una de las `px` columnas de la ventana, el máximo de cada
// banda en los puntos que caen en ella (o null si no hay datos ahí: fuera del audio). Si hay
// menos puntos que columnas, cada punto ocupa sus columnas (no se interpola).
export function columnas(d, v, px) {
  const n = d && d.bandas && Array.isArray(d.bandas.graves) ? d.bandas.graves.length : 0
  const out = []
  if (!n || !(px > 0)) return out
  const span = d.hasta - d.desde
  const w = v.hasta - v.desde
  for (let c = 0; c < px; c++) {
    const t0 = v.desde + (c / px) * w
    const t1 = v.desde + ((c + 1) / px) * w
    let i0 = Math.floor(((t0 - d.desde) / span) * n)
    let i1 = Math.ceil(((t1 - d.desde) / span) * n)
    if (i1 <= 0 || i0 >= n) { out.push(null); continue }
    i0 = Math.max(0, i0)
    i1 = Math.min(n, Math.max(i0 + 1, i1))
    let g = 0, m = 0, a = 0
    for (let i = i0; i < i1; i++) {
      if (d.bandas.graves[i] > g) g = d.bandas.graves[i]
      if (d.bandas.medios[i] > m) m = d.bandas.medios[i]
      if (d.bandas.agudos[i] > a) a = d.bandas.agudos[i]
    }
    out.push({ graves: g, medios: m, agudos: a })
  }
  return out
}

// ---------------------------------------------------------------------- deshacer / rehacer
// Cada edición es una operación que el SERVIDOR confirmó:
//   { tipo: 'crear', marca }           (marca tal como la devolvió el servidor)
//   { tipo: 'borrar', marca }
//   { tipo: 'cambiar', id, antes, despues }   (solo los campos que cambiaron)
//   { tipo: 'grupo', ops: [...] }      (cambiar el tipo = crear la nueva + borrar la vieja)
// Deshacer aplica la inversa; rehacer, la original. Recrear una marca borrada le da OTRO id
// en la base: `reemplazarId` lo propaga a todo el historial.

export const historialVacio = () => ({ pasado: [], futuro: [] })
const TOPE = 200

export function inversa(op) {
  switch (op.tipo) {
    case 'crear': return { tipo: 'borrar', marca: op.marca }
    case 'borrar': return { tipo: 'crear', marca: op.marca }
    case 'cambiar': return { tipo: 'cambiar', id: op.id, antes: op.despues, despues: op.antes }
    case 'grupo': return { tipo: 'grupo', ops: [...op.ops].reverse().map(inversa) }
    default: throw new Error(`operación desconocida: ${op && op.tipo}`)
  }
}

export function registrar(h, op) {
  const pasado = [...h.pasado, op]
  return { pasado: pasado.length > TOPE ? pasado.slice(pasado.length - TOPE) : pasado, futuro: [] }
}

// Lo que hay que aplicar para deshacer, y el historial como queda SI se aplica bien.
export function deshacer(h) {
  if (!h.pasado.length) return null
  const op = h.pasado[h.pasado.length - 1]
  return { aplicar: inversa(op), h: { pasado: h.pasado.slice(0, -1), futuro: [op, ...h.futuro] } }
}

export function rehacer(h) {
  if (!h.futuro.length) return null
  const op = h.futuro[0]
  return { aplicar: op, h: { pasado: [...h.pasado, op], futuro: h.futuro.slice(1) } }
}

function conId(op, viejo, nuevo) {
  switch (op.tipo) {
    case 'crear':
    case 'borrar': return op.marca.id === viejo ? { ...op, marca: { ...op.marca, id: nuevo } } : op
    case 'cambiar': return op.id === viejo ? { ...op, id: nuevo } : op
    case 'grupo': return { ...op, ops: op.ops.map((o) => conId(o, viejo, nuevo)) }
    default: return op
  }
}

export function reemplazarId(h, viejo, nuevo) {
  if (viejo === nuevo) return h
  return { pasado: h.pasado.map((o) => conId(o, viejo, nuevo)), futuro: h.futuro.map((o) => conId(o, viejo, nuevo)) }
}

// El cuerpo del POST que vuelve a crear una marca (sin id ni fechas).
export function cuerpoCrear(m) {
  const c = { tipo: m.tipo, inicio: m.inicio }
  if (m.tipo === 'loop') c.fin = m.fin
  if (m.tipo !== 'memory' && Number.isInteger(m.num)) c.num = m.num
  if (m.nombre) c.nombre = m.nombre
  return c
}

// Lo que cambió entre dos versiones de una marca (para un PATCH y su inversa).
export function diferencia(antes, despues) {
  const a = {}, d = {}
  for (const k of ['inicio', 'fin', 'nombre', 'num']) {
    if ((antes[k] ?? null) !== (despues[k] ?? null)) { a[k] = antes[k] ?? null; d[k] = despues[k] ?? null }
  }
  return { antes: a, despues: d }
}

// ---------------------------------------------------------------------- atajos

function escribiendo(target) {
  if (!target) return false
  if (target.isContentEditable) return true
  const tag = (target.tagName || '').toLowerCase()
  if (tag === 'textarea' || tag === 'select') return true
  if (tag !== 'input') return false
  return !['button', 'submit', 'reset', 'checkbox', 'radio', 'range'].includes((target.type || 'text').toLowerCase())
}

function activable(target) {
  if (!target) return false
  const tag = (target.tagName || '').toLowerCase()
  if (tag === 'button' || tag === 'a' || tag === 'summary') return true
  if (tag === 'input' && ['button', 'submit', 'reset', 'checkbox', 'radio'].includes((target.type || '').toLowerCase())) return true
  const role = typeof target.getAttribute === 'function' ? target.getAttribute('role') : target.role
  return ['button', 'link', 'checkbox', 'radio', 'switch', 'menuitem', 'tab', 'option'].includes((role || '').toLowerCase())
}

// La acción de una tecla en el editor D, o null. `e`: KeyboardEvent (o algo con key, code...).
//   play · beat(dir) · fino(dir) · pad(num) · borrarPad(num) · cue · memory · loopIn · loopOut ·
//   loopBeats · zoom(dir) · grilla · medioBeat · snap · deshacer · rehacer · inicio · fin ·
//   tema(dir) · cerrar
export function atajoD(e) {
  if (!e || e.isComposing) return null
  const k = e.key
  const mod = e.ctrlKey || e.metaKey
  if (escribiendo(e.target)) return null
  if (mod && !e.altKey) {
    const l = (k || '').toLowerCase()
    if (l === 'z') return e.shiftKey ? { tipo: 'rehacer' } : { tipo: 'deshacer' }
    if (l === 'y') return { tipo: 'rehacer' }
    return null
  }
  if (e.altKey || mod) return null
  // Los números por `code`: con Mayús, la tecla «1» da «!» (o «"» en un teclado español).
  const dig = /^Digit([1-8])$/.exec(e.code || '') || /^Numpad([1-8])$/.exec(e.code || '')
  if (dig) return { tipo: e.shiftKey ? 'borrarPad' : 'pad', num: Number(dig[1]) - 1 }
  if (/^[1-8]$/.test(k || '')) return { tipo: e.shiftKey ? 'borrarPad' : 'pad', num: Number(k) - 1 }
  if (k === 'ArrowLeft') return e.shiftKey ? { tipo: 'fino', dir: -1 } : { tipo: 'beat', dir: -1 }
  if (k === 'ArrowRight') return e.shiftKey ? { tipo: 'fino', dir: 1 } : { tipo: 'beat', dir: 1 }
  if (k === 'Escape') return { tipo: 'cerrar' }
  if (e.shiftKey) return null
  if (k === ' ' || k === 'Spacebar') return activable(e.target) ? null : { tipo: 'play' }
  if (k === '+' || k === 'Add') return { tipo: 'zoom', dir: 1 }
  if (k === '-' || k === 'Subtract') return { tipo: 'zoom', dir: -1 }
  if (k === 'Home') return { tipo: 'inicio' }
  if (k === 'End') return { tipo: 'fin' }
  if (k === 'PageUp') return { tipo: 'tema', dir: -1 }
  if (k === 'PageDown') return { tipo: 'tema', dir: 1 }
  switch ((k || '').toLowerCase()) {
    case 'c': return { tipo: 'cue' }
    case 'm': return { tipo: 'memory' }
    case 'i': return { tipo: 'loopIn' }
    case 'o': return { tipo: 'loopOut' }
    case 'l': return { tipo: 'loopBeats' }
    case 'g': return { tipo: 'grilla' }
    case 'h': return { tipo: 'medioBeat' }
    case 'q': return { tipo: 'snap' }
    default: return null
  }
}

// ---------------------------------------------------------------------- textos

// BPM con un decimal SOLO si se midió; si no, null (la pantalla dice «?»).
export const fmtBpmMedido = (bpm) => (bpm !== null && bpm !== undefined && Number.isFinite(Number(bpm)) && Number(bpm) > 0
  ? Number(bpm).toFixed(1) : null)

// La letra del pad (A..H) de una marca que vive en un pad, o null.
export const letraPad = (num) => (Number.isInteger(num) && num >= 0 && num < PADS.length ? PADS[num] : null)

// El rótulo de una marca en la onda y en la lista: la letra del pad (hot cue), «M» (memory),
// «Loop» o «Loop C» (hot loop en el pad C). No depende del color.
export function rotuloMarca(m) {
  if (m.tipo === 'cue') return letraPad(m.num)
  if (m.tipo === 'memory') return 'M'
  return letraPad(m.num) ? `Loop ${letraPad(m.num)}` : 'Loop'
}

// Cómo se llama una marca para un lector de pantalla y para los avisos, con la letra del pad
// (el editor D nombra los pads A..H, como Rekordbox).
export function nombreD(m) {
  if (m.tipo === 'cue') return `hot cue ${letraPad(m.num)}`
  if (m.tipo === 'memory') return `memory cue en ${fmtTiempo(m.inicio)}`
  return `loop${letraPad(m.num) ? ` del pad ${letraPad(m.num)}` : ''} ${fmtTiempo(m.inicio)} a ${fmtTiempo(m.fin)}`
}

// El compás y beat de un instante según la grilla («compás 12 · beat 3»), o null si el motor
// no estimó cuál es el 1 (no se inventa). Los compases se cuentan desde el primero del tema.
// Antes del primer 1 (la anacrusa) hay beats pero no compás: { compas: null, beat }. Antes de
// la primera línea de la grilla, null.
export function compasDe(t, g, offset) {
  if (!estadoGrilla(g).hay || !finito(g.compas_ref) || !finito(t)) return null
  const p = g.periodo_s
  const bpc = Number.isInteger(g.beats_por_compas) && g.beats_por_compas > 0 ? g.beats_por_compas : 4
  let ref = g.compas_ref + (finito(offset) ? offset : 0)
  // El primer 1 del tema: el ref corrido hacia atrás de a compases mientras siga ≥ 0.
  ref -= Math.floor(ref / (bpc * p)) * bpc * p
  const k = Math.floor((t - ref) / p + 1e-6)
  if (k < 0) {
    if (ref + k * p < -1e-6) return null            // antes de la primera línea: no hay beat
    return { compas: null, beat: (((k % bpc) + bpc) % bpc) + 1 }
  }
  return { compas: Math.floor(k / bpc) + 1, beat: (k % bpc) + 1 }
}

// Sacar del historial un paso que el servidor rechazó (la marca ya no existe, el pad está
// ocupado): `h` es el historial como quedó DESPUÉS de deshacer / rehacer (`paso.h`), con la
// operación ya movida; se la tira para que el próximo Ctrl+Z no repita el mismo pedido.
export function descartarPaso(h, dir) {
  return dir < 0 ? { pasado: h.pasado, futuro: h.futuro.slice(1) } : { pasado: h.pasado.slice(0, -1), futuro: h.futuro }
}
