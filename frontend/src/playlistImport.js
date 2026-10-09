// Lo puro de "las playlists como centro" (f53): cómo se dice el estado de cada tema, el
// resumen del encabezado, los puntos de color de las marcas y los textos del diálogo de
// importar. Sin React ni red: lo prueban los tests de unidad (test/playlistImport.test.mjs).
//
// Reglas (CLAUDE.md §6): un dato que miente es peor que uno ausente. El BPM va con UN decimal;
// la key con Camelot Y clásica, y `?` si la detección es dudosa; si el BPM/key no los midió el
// motor se dice de dónde salen («de Rekordbox»). Los estados van en TEXTO (no solo color).

export const ORIGENES = { rekordbox: 'Rekordbox', musiflix: 'MusiFlix', carpeta: 'Carpeta' }
export const origenTexto = (o) => ORIGENES[o] || ORIGENES.musiflix

// El estado del ARCHIVO, en palabras. `local` = un archivo propio del dueño (importado o de su
// biblioteca): está «En tu PC», MusiFlix no lo «descargó».
export function textoArchivo(it) {
  switch (it?.archivo_estado) {
    case 'ok': return it.local ? 'En tu PC' : 'Descargado'
    case 'no-existe': return 'No encuentro el archivo'
    case 'no-encontrado': return 'No encuentro el archivo'
    case 'ambiguo': return `Hay ${it.homonimos || 2} archivos con ese nombre`
    default: return 'Sin archivo'
  }
}

// El estado del ANÁLISIS del motor: {clave, texto, tono}. `tono` elige el color (ok/info/
// warn/error/neutro), el texto lo dice igual sin color.
export function estadoTema(it) {
  const a = it?.analisis || {}
  if (it?.archivo_estado && it.archivo_estado !== 'ok') {
    return { clave: it.archivo_estado, texto: textoArchivo(it), tono: 'warn' }
  }
  switch (a.estado) {
    case 'analizado': return { clave: 'analizado', texto: 'Analizado', tono: 'ok' }
    case 'analizando': return { clave: 'analizando', texto: 'Analizando…', tono: 'info' }
    case 'en-cola': return { clave: 'en-cola', texto: 'En cola para analizar', tono: 'info' }
    case 'fallo': return { clave: 'fallo', texto: 'No se pudo analizar', tono: 'error' }
    case 'pendiente': return { clave: 'pendiente', texto: 'Falta analizar', tono: 'neutro' }
    default: return { clave: 'sin-archivo', texto: textoArchivo(it), tono: 'neutro' }
  }
}

export const fmtBpm = (v) => (v === null || v === undefined || !Number.isFinite(Number(v)) || Number(v) <= 0
  ? null : Number(v).toFixed(1))

// BPM y key como se muestran, con de dónde salen. `null` si no hay dato (no un 0 ni un guion
// que parezca medido).
export function datosTema(it) {
  const a = it?.analisis || {}
  const bpm = fmtBpm(a.bpm)
  const key = a.camelot ? `${a.camelot}${a.tonalidad ? ` · ${a.tonalidad}` : ''}${a.key_dudosa ? ' ?' : ''}` : null
  const fuente = a.dato === 'motor' ? null : a.dato === 'rekordbox' ? 'de Rekordbox' : a.dato ? 'sin medir' : null
  return { bpm, key, fuente, dudosa: !!a.key_dudosa }
}

// Los puntos de color de las marcas: un punto por hot cue (con el color de su pad), uno por
// memory y uno por loop. Clases de nocturne.css (las mismas del editor, ver cues.js).
export function puntosMarcas(m) {
  if (!m || !m.total) return []
  const out = (m.pads || []).map((n) => ({ clase: `cue-c${n + 1}`, texto: `hot cue ${n + 1}` }))
  for (let i = 0; i < (m.memory || 0); i++) out.push({ clase: 'cue-mem', texto: 'memory cue' })
  for (let i = 0; i < (m.loop || 0); i++) out.push({ clase: 'cue-loop', texto: 'loop' })
  return out
}

export function textoMarcas(m) {
  if (!m || !m.total) return 'sin marcas'
  const partes = []
  if (m.pads?.length) partes.push(`${m.pads.length} hot cue${m.pads.length === 1 ? '' : 's'}`)
  if (m.memory) partes.push(`${m.memory} memory`)
  if (m.loop) partes.push(`${m.loop} loop${m.loop === 1 ? '' : 's'}`)
  return partes.join(', ')
}

const durTotal = (s) => {
  s = Math.round(Number(s) || 0)
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
  return h ? `${h} h ${String(m).padStart(2, '0')} min` : `${m} min`
}

// Rango de BPM por la fuente REAL de cada tema, sin mezclar fuentes: el de la más confiable
// que haya (motor > Rekordbox > búsqueda) y, si quedan temas de otra fuente, cuántos quedaron
// afuera. Un BPM de YouTube/SoundCloud nunca se rotula «de Rekordbox» (CLAUDE.md §6).
const FUENTES_BPM = [
  { dato: 'motor', rotulo: (n, afuera) => (afuera ? `${n} medido${n === 1 ? '' : 's'} por el motor` : null) },
  { dato: 'rekordbox', rotulo: () => 'de Rekordbox' },
  // "otro": la búsqueda o los tags de una carpeta. No se nombra un origen que no se sabe.
  { dato: 'otro', rotulo: () => 'sin medir' },
]
function rangoBpm(items) {
  const con = items.map((it) => ({ bpm: Number(it.analisis?.bpm), dato: it.analisis?.dato }))
    .filter((x) => Number.isFinite(x.bpm) && x.bpm > 0)
  for (const f of FUENTES_BPM) {
    const vs = con.filter((x) => (f.dato === 'otro' ? x.dato !== 'motor' && x.dato !== 'rekordbox' : x.dato === f.dato)).map((x) => x.bpm)
    if (!vs.length) continue
    const afuera = con.length - vs.length
    const lo = Math.min(...vs).toFixed(1), hi = Math.max(...vs).toFixed(1)
    const partes = [f.rotulo(vs.length, afuera), afuera ? `${afuera} sin medir afuera` : null].filter(Boolean)
    return { bpm: lo === hi ? lo : `${lo}–${hi}`, bpmFuente: partes.length ? partes.join(' · ') : null }
  }
  return { bpm: null, bpmFuente: null }
}

// El encabezado de la playlist: temas, duración, rango de BPM (ver `rangoBpm`) y los géneros
// más comunes.
export function resumenPlaylist(items = []) {
  const generos = new Map()
  for (const it of items) {
    const g = (it.genero || '').trim()
    if (g) generos.set(g, (generos.get(g) || 0) + 1)
  }
  const sinGenero = items.filter((it) => !(it.genero || '').trim()).length
  return {
    temas: items.length,
    duracion: durTotal(items.reduce((s, it) => s + (Number(it.duracion) || 0), 0)),
    ...rangoBpm(items),
    generos: [...generos.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 3).map(([g]) => g),
    sinGenero,
    analizados: items.filter((it) => it.analisis?.estado === 'analizado').length,
  }
}

// "2 de 3 encontrados" y el porqué de los que faltan, sin esconderlos.
export function textoEncontrados(p) {
  return `${p.encontrados} de ${p.total} encontrado${p.total === 1 ? '' : 's'}`
}
export function detalleFaltantes(p) {
  const out = []
  if (p.ambiguos) out.push(`${p.ambiguos} con el nombre repetido en tus carpetas (no elijo uno a la suerte)`)
  if (p.faltan) out.push(`${p.faltan} que no están en tus carpetas de música`)
  if (p.inexistentes) out.push(`${p.inexistentes} que el XML lista pero no trae en la colección`)
  return out
}

export function textoProgreso(p) {
  if (!p || !p.total) return null
  if (p.corriendo) return `Analizando ${Math.min(p.hechos + 1, p.total)} de ${p.total}${p.actual ? ` · ${p.actual}` : ''}`
  const f = p.fallidos?.length || 0
  return `Análisis terminado: ${p.hechos - f} de ${p.total} analizados${f ? ` · ${f} no se ${f === 1 ? 'pudo' : 'pudieron'} analizar` : ''}`
}

// Lo que dice el server cuando algo no anduvo: su motivo, o el código si no dio uno.
export function motivoDe(r) {
  const d = (r && r.data) || {}
  if (typeof d.mensaje === 'string' && d.mensaje.trim()) return d.mensaje
  if (typeof d.error === 'string' && d.error.trim()) return d.error
  if (d.error_texto) return `El servidor falló (HTTP ${r.status}).`
  return `El servidor contestó HTTP ${r ? r.status : '?'} sin explicación.`
}

// Las métricas del crate (server: db.metricas_bpm, ya sin mezclar fuentes): el promedio y el
// rango con UN decimal y de dónde salen. Sin ningún BPM: «sin medir», no un número.
const FUENTE_METRICA = { motor: 'medido por el motor', rekordbox: 'de Rekordbox', otro: 'sin medir' }
export function metricaBpm(m) {
  if (!m || m.bpm_prom === null || m.bpm_prom === undefined) return { promedio: null, rango: null, fuente: 'sin medir' }
  const f = (v) => Number(v).toFixed(1)
  const afuera = m.bpm_afuera ? ` · ${m.bpm_afuera} de otra fuente afuera` : ''
  return {
    promedio: f(m.bpm_prom),
    rango: m.bpm_min === m.bpm_max ? f(m.bpm_min) : `${f(m.bpm_min)}–${f(m.bpm_max)}`,
    fuente: `${FUENTE_METRICA[m.bpm_fuente] || 'sin medir'}${afuera}`,
  }
}
