// Llamadas al backend FastAPI. En dev van por el proxy de Vite (ver vite.config.js);
// en prod contra el mismo origen que sirve el build.

async function json(res) {
  return res.json()
}

export async function buscar(q, formato, genero) {
  const r = await fetch(`/api/buscar?q=${encodeURIComponent(q || '')}&limite=28` +
    `&formato=${encodeURIComponent(formato || 'wav')}&genero=${encodeURIComponent(genero || '')}`)
  return json(r)
}

export async function buscarLista(lista, formato) {
  const r = await fetch('/api/buscar_lista', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lista, formato }),
  })
  return json(r)
}

export async function parecidasLista(titulo, artista, formato, genero) {
  const q = `titulo=${encodeURIComponent(titulo)}&artista=${encodeURIComponent(artista || '')}` +
            `&formato=${encodeURIComponent(formato)}&genero=${encodeURIComponent(genero || '')}`
  const r = await fetch(`/api/parecidas_lista?${q}`)
  return json(r)
}

export async function fetchMeta(titulo, artista) {
  const r = await fetch(`/api/meta?titulo=${encodeURIComponent(titulo)}&artista=${encodeURIComponent(artista || '')}`)
  return json(r)
}

export async function descargar(payload) {
  const r = await fetch('/api/descargar', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return json(r)
}

// Estado de un trabajo encolado en el worker.
export const estadoJob = (jobId) => fetch(`/api/jobs/${jobId}`).then(json)

// Poolea un job hasta que termina. Devuelve el resultado (mismo shape que la
// descarga local: {exito, archivo, calidad} o {exito:false, mensaje}).
export async function esperarJob(jobId, { intervalo = 1500, timeout = 180000 } = {}) {
  const fin = Date.now() + timeout
  while (Date.now() < fin) {
    let s
    try { s = await estadoJob(jobId) } catch { return { exito: false, mensaje: 'Se perdió la conexión con el trabajo' } }
    if (s.estado === 'finished') return s.resultado || { exito: false, mensaje: 'El trabajo no devolvió resultado' }
    if (s.estado === 'failed') return { exito: false, mensaje: s.error || 'El trabajo falló' }
    if (s.estado === 'unknown') return { exito: false, mensaje: 'No encontré el trabajo' }
    await new Promise((r) => setTimeout(r, intervalo))
  }
  return { exito: false, mensaje: 'La descarga tardó demasiado' }
}

export const spectroUrl = (c) =>
  `/api/spectro?titulo=${encodeURIComponent(c.titulo)}&artista=${encodeURIComponent(c.artista || '')}` +
  `&fuente=${encodeURIComponent(c.fuente || '')}&url=${encodeURIComponent(c.url || '')}`

// Nota de calidad (A/B/C/D/F) del audio real, ANTES de descargar.
export async function calidad(c) {
  const q = `titulo=${encodeURIComponent(c.titulo)}&artista=${encodeURIComponent(c.artista || '')}` +
            `&fuente=${encodeURIComponent(c.fuente || '')}&url=${encodeURIComponent(c.url || '')}`
  const r = await fetch(`/api/calidad?${q}`)
  return json(r)
}

// Biblioteca local (colección analizada) agrupada por género, para la home.
export const getBiblioteca = () => fetch('/api/biblioteca').then(json)
// URL de audio de un track de la biblioteca (para el <audio> del preview).
export const audioUrl = (id) => `/api/audio/${encodeURIComponent(id)}`

/* ---------- Radio DJ (motor/) ----------
   OJO: esta biblioteca NO es la de la home. Aquella sale del XML de Rekordbox; esta, de la
   base SQLite del motor (la que tiene energía, embeddings y confianza de la key). Ids y
   rutas distintos → endpoints distintos (ver el comentario de /api/radio/* en server.py). */

// Biblioteca del motor: tracks para elegir semilla + `opciones` (curvas, defaults de
// RadioConfig y leyenda del `?`). Siempre 200: sin base contesta con `estado`/`motivo`.
// Nunca `.then(json)` a secas, por lo mismo que `getRadioSet`: un 500 de FastAPI viene en
// text/plain y el parseo explotaba, así que la pantalla decía "no pude conectar" cuando el
// servidor sí había contestado. Un fallo del server se devuelve con la misma forma que usa
// el backend para degradar (`estado`/`motivo`), así la pantalla lo muestra sin casos nuevos.
export async function getRadioBiblioteca() {
  const r = await fetch('/api/radio/biblioteca')
  const data = await cuerpoRadio(r)
  if (r.ok && Array.isArray(data?.tracks)) return data
  const suelto = data?.error_texto || data?.error || data?.detail
  return {
    configurada: false, estado: `http-${r.status}`, total: 0, tracks: [], opciones: null,
    motivo: `El servidor no pudo darme la biblioteca del motor (HTTP ${r.status})`
      + (suelto ? `: ${typeof suelto === 'string' ? suelto : JSON.stringify(suelto)}` : '.'),
  }
}

// Cuerpo de una respuesta de la radio, sin asumir que es JSON.
//
// `r.json()` a secas era un error: FastAPI manda los 500 como text/plain ("Internal Server
// Error"), así que el parseo explotaba, el await caía en el catch de la llamada y la
// pantalla decía "no pude conectar con el servidor, revisá que esté corriendo" — cuando el
// servidor SÍ contestó y lo que falló fue adentro. Un mensaje que miente sobre qué pasó
// manda al usuario a arreglar lo que no está roto (§6).
//
// Devuelve el objeto parseado, o `{error_texto}` con el cuerpo crudo si no era JSON: son
// dos claves distintas a propósito, para que quien lo muestre sepa si está leyendo el
// motivo que escribió el motor o el texto suelto de un fallo del servidor.
async function cuerpoRadio(r) {
  const txt = await r.text()
  try { return JSON.parse(txt) } catch { return { error_texto: txt.trim() } }
}

// El set. Solo viajan los parámetros que el usuario tocó: los que falten los pone
// `RadioConfig` en el backend, que además contesta en `config` con los que usó. Escribir
// los defaults acá sería un segundo juego que se desincroniza en silencio.
// Devuelve {ok, status, data} porque un 400 (semilla que no es track, curva inexistente)
// trae el motivo del motor en `data.error` y hay que mostrarlo tal cual.
export async function getRadioSet(params) {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params || {})) {
    if (v === undefined || v === null || v === '') continue
    qs.set(k, String(v))
  }
  const r = await fetch(`/api/radio/set?${qs.toString()}`)
  return { ok: r.ok, status: r.status, data: await cuerpoRadio(r) }
}

// El set como .m3u8 para Rekordbox (/api/radio/set.m3u8). Mismos parámetros que
// `getRadioSet` —el backend lo vuelve a armar con la misma función— más `esperado`: los ids
// que la pantalla muestra, para que el backend se niegue (409) si el set re-armado ya no es
// ese. Se baja con fetch y no con un <a href> directo porque un error (400/409) tiene que
// verse en la pantalla con su motivo, no terminar guardado en Descargas como si fuera el
// archivo. Devuelve {ok, blob, nombre} o {ok: false, status, data}.
export async function exportarRadioM3u8(params, esperado) {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params || {})) {
    if (v === undefined || v === null || v === '') continue
    qs.set(k, String(v))
  }
  if (esperado && esperado.length) qs.set('esperado', esperado.join(','))
  const r = await fetch(`/api/radio/set.m3u8?${qs.toString()}`)
  const disp = r.headers.get('Content-Disposition') || ''
  if (!r.ok || !/^attachment/i.test(disp)) {
    return { ok: false, status: r.status, data: await cuerpoRadio(r) }
  }
  return { ok: true, blob: await r.blob(), nombre: nombreDeDescarga(disp) }
}

// El nombre que eligió el backend (ya saneado para Windows). `filename*` primero porque
// trae el nombre real en UTF-8; `filename` es la versión ASCII de respaldo.
function nombreDeDescarga(disp) {
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disp)
  if (utf8) { try { return decodeURIComponent(utf8[1]) } catch { /* cae al ASCII */ } }
  const ascii = /filename="([^"]+)"/i.exec(disp)
  return ascii ? ascii[1] : 'DJ Radio.m3u8'
}

export const radioAudioUrl = (id) => `/api/radio/audio/${encodeURIComponent(id)}`

// El <audio> avisa que falló pero no deja leer el cuerpo de la respuesta, y el 404 de la
// radio explica si el archivo se movió o si la base se escaneó en otra máquina. Se vuelve
// a pedir el primer byte solo para leer ese motivo. null = no hay motivo del backend.
export async function radioAudioMotivo(id) {
  try {
    const r = await fetch(radioAudioUrl(id), { headers: { Range: 'bytes=0-0' } })
    if (r.ok) return null
    const d = await cuerpoRadio(r)
    // Mismo criterio que el set: el motivo del backend tal cual, y si el cuerpo no era JSON
    // (un 500 en text/plain) se dice el código, que es lo único cierto que hay.
    if (d.error) return d.error
    return d.error_texto ? `El servidor falló al servir este audio (HTTP ${r.status}): ${d.error_texto}` : null
  } catch { return null }
}

// Historial persistido: búsquedas, playlists (modo lista) y descargas.
export async function historial(limite = 20) {
  const r = await fetch(`/api/historial?limite=${limite}`)
  return json(r)
}

// Trae una playlist guardada lista para renderizar en la vista 'lista'.
export async function getPlaylistGuardada(id) {
  const r = await fetch(`/api/historial/playlist/${id}`)
  return json(r)
}

export async function borrarPlaylist(id) {
  const r = await fetch(`/api/historial/playlist/${id}`, { method: 'DELETE' })
  return json(r)
}

// Vacía el historial. que ∈ 'busquedas' | 'playlists' | 'descargas' | 'todo'.
export async function limpiarHistorial(que = 'todo') {
  const r = await fetch(`/api/historial?que=${encodeURIComponent(que)}`, { method: 'DELETE' })
  return json(r)
}

/* ---------- Mis Playlists (crates) ---------- */
// Aviso global de "cambiaron las playlists" (el rail de la home lo escucha para refrescarse).
export const avisarPlaylists = () => { try { window.dispatchEvent(new Event('musiflix:playlists')) } catch { /* sin window */ } }
const jpost = (url, body) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }).then(json)

export const listarPlaylists = () => fetch('/api/playlists').then(json)
export const playlistActiva = () => fetch('/api/playlists/activa').then(json)
export const crearPlaylist = (nombre) => jpost('/api/playlists', { nombre })
export const getPlaylist = (id) => fetch(`/api/playlists/${id}`).then(json)
export const editarPlaylist = (id, body) => fetch(`/api/playlists/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(json)
export const borrarPlaylistMia = (id) => fetch(`/api/playlists/${id}`, { method: 'DELETE' }).then(json)
export const agregarAPlaylist = (id, track) => jpost(`/api/playlists/${id}/items`, { track })
export const quitarItemPlaylist = (id, itemId) => fetch(`/api/playlists/${id}/items/${itemId}`, { method: 'DELETE' }).then(json)
export const reordenarPlaylist = (id, orden) => jpost(`/api/playlists/${id}/orden`, { orden })
export const exportarPlaylist = (id) => jpost(`/api/playlists/${id}/export`, {})
