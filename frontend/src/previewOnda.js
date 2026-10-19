// La vista previa de la onda en cada fila de Mis playlists (f62, la columna «Preview» de
// Rekordbox): lógica pura, sin React ni DOM, para probarla con `node --test`
// (test/previewOnda.test.mjs).
//
// - La onda son las 3 bandas del audio REAL (`/api/radio/tracks/{id}/preview`, las mismas de
//   /onda3 a baja resolución), dibujadas como el editor D: graves afuera, medios y agudos
//   adentro, con `alturaBanda` y los colores --edd-*.
// - Las marcas salen de `/api/radio/tracks/{id}/marcas` y se ubican en el eje de la BIBLIOTECA
//   (`track.dur`, contra el que se validan), igual que en el editor; la onda ocupa su tramo
//   real (`tramoOnda`): si el archivo dura otra cosa, no se estira.
// - Cada marca con SU color (`claseMarca`): el hot cue el de su pad, la memory neutra y el loop
//   naranja como un tramo. Nada se inventa: sin duración no se ubica ninguna marca.

import { claseMarca, pctDe, textoMarca, tramoOnda } from './cues.js'
import { alturaBanda } from './editorD.js'

// Puntos por banda que se piden: alcanzan para ~150 px y pesan poco (3 × 160 valores).
export const PUNTOS_PREVIEW = 160
// Alto del dibujo en unidades del viewBox del SVG (la caja mide 24 px).
export const ALTO_PREVIEW = 24
export const BANDAS_PREVIEW = ['graves', 'medios', 'agudos']

// El eje de las marcas: la duración de la biblioteca o, si no viene, la del audio decodificado
// (la misma regla que el editor D: `dur = durEje || durAudio`). 0 = no hay eje.
export function durEje(track, durAudio) {
  const d = Number(track?.dur)
  if (Number.isFinite(d) && d > 0) return d
  const a = Number(durAudio)
  return Number.isFinite(a) && a > 0 ? a : 0
}

// Un tiempo de verdad: null o '' no son el segundo 0.
const esNumero = (v) => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v))

// El orden de dibujo: los loops atrás (son tramos), después las memory y los hot cues arriba.
const CAPA = { loop: 0, memory: 1, cue: 2 }

// Dónde va cada marca, en % del ancho de la caja, con su clase de color.
//   { id, tipo, clase, texto, left, width (solo loops; si no, null), fuera }
// `fuera`: la marca cae (toda o en parte) después del final del eje; se dibuja pegada al borde,
// como la deja `pctDe`, y la fila lo puede decir. Sin eje (dur ≤ 0) no se ubica nada.
export function marcasEnOnda(marcas, dur) {
  if (!Array.isArray(marcas) || !(dur > 0)) return []
  return marcas
    .filter((m) => m && esNumero(m.inicio) && CAPA[m.tipo] !== undefined)
    .map((m) => {
      const left = pctDe(Number(m.inicio), dur)
      const esLoop = m.tipo === 'loop'
      const fin = esLoop && esNumero(m.fin) ? Number(m.fin) : null
      return {
        id: m.id,
        tipo: m.tipo,
        clase: claseMarca(m),
        texto: textoMarca(m),
        left,
        width: esLoop ? Math.max(0, (fin === null ? left : pctDe(fin, dur)) - left) : null,
        fuera: Boolean(m.fuera_del_track) || Number(m.inicio) > dur || (fin !== null && fin > dur),
      }
    })
    .sort((a, b) => CAPA[a.tipo] - CAPA[b.tipo] || a.left - b.left || (a.id ?? 0) - (b.id ?? 0))
}

// Cuántas de cada una, en el MISMO texto que `textoMarcas` de la playlist (que cuenta desde el
// resumen de la API): «2 hot cues, 1 memory, 1 loop» o «sin marcas». Un hot loop cuenta como
// loop (su pad no lo hace hot cue).
export function resumenMarcas(marcas) {
  const lista = Array.isArray(marcas) ? marcas : []
  const n = (t) => lista.filter((m) => m && m.tipo === t).length
  const cues = n('cue')
  const memory = n('memory')
  const loops = n('loop')
  const partes = []
  if (cues) partes.push(`${cues} hot cue${cues === 1 ? '' : 's'}`)
  if (memory) partes.push(`${memory} memory`)
  if (loops) partes.push(`${loops} loop${loops === 1 ? '' : 's'}`)
  return partes.length ? partes.join(', ') : 'sin marcas'
}

// El `d` de un <path> con una barra por punto (ancho 1 en el viewBox, centrada en el alto).
// Valores fuera de 0..1 se acotan (`alturaBanda`); los no numéricos no dibujan nada.
export function trazoBanda(valores, banda, alto = ALTO_PREVIEW) {
  if (!Array.isArray(valores)) return ''
  let d = ''
  for (let i = 0; i < valores.length; i++) {
    const v = Number(valores[i])
    if (!Number.isFinite(v)) continue
    const h = Math.round(alturaBanda(v, banda) * alto * 10) / 10
    if (h <= 0) continue
    const y = Math.round(((alto - h) / 2) * 100) / 100
    d += `M${i} ${y}h1v${h}h-1z`
  }
  return d
}

// Lo que dibuja el SVG de la onda, o null si la respuesta no trae bandas.
//   { ancho: fracción 0..1 de la caja, puntos: cuántos puntos del principio, trazos: {banda: d} }
// `dur` es el eje de las marcas; `data` la respuesta de /preview.
export function ondaPreview(data, dur) {
  const b = data && data.bandas
  if (!b || !BANDAS_PREVIEW.every((k) => Array.isArray(b[k]))) return null
  const n = Math.min(...BANDAS_PREVIEW.map((k) => b[k].length))
  const durAudio = Number(data.duracion_audio)
  const t = tramoOnda(n, durAudio, dur > 0 ? dur : durAudio)
  if (!(t.picos > 0)) return null
  const trazos = {}
  for (const k of BANDAS_PREVIEW) trazos[k] = trazoBanda(b[k].slice(0, t.picos), k)
  return { ancho: t.ancho, puntos: t.picos, trazos }
}

// Qué respuestas de /preview se guardan en la caché de la fila (f62, auditoría): la onda (200)
// y el audio que no decodifica (422: no cambia mientras el archivo no cambie, y volver a pedirlo
// es volver a decodificar). Un 404 (el archivo se movió), un 409 o un 5xx son pasajeros: no se
// guardan y la fila vuelve a pedir cuando entra otra vez en pantalla.
export const guardarOnda = (r) => Boolean(r) && (r.ok === true || r.status === 422)

// Qué respuestas de /marcas se guardan: solo las que trajeron el track y la lista con la base
// utilizable (`estado: 'ok'`). Un 200 con la base ocupada (un scan) no se guarda.
export const guardarMarcas = (r) => Boolean(r) && r.ok === true && Boolean(r.data) && r.data.estado === 'ok'
  && Boolean(r.data.track) && Array.isArray(r.data.marcas)

// El texto del lugar de la onda cuando no hay nada que dibujar, según el estado del tema
// (`estadoTema(...).clave`) y si tiene archivo en esta PC.
export function textoSinOnda(clave, conArchivo) {
  if (!conArchivo) return 'sin archivo'
  switch (clave) {
    case 'analizando': return 'analizando'
    case 'en-cola': return 'en cola'
    case 'fallo': return 'falló'
    default: return 'sin analizar'
  }
}
