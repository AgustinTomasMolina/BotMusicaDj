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
export const getRadioBiblioteca = () => fetch('/api/radio/biblioteca').then(json)

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
  return { ok: r.ok, status: r.status, data: await json(r) }
}

export const radioAudioUrl = (id) => `/api/radio/audio/${encodeURIComponent(id)}`

// El <audio> avisa que falló pero no deja leer el cuerpo de la respuesta, y el 404 de la
// radio explica si el archivo se movió o si la base se escaneó en otra máquina. Se vuelve
// a pedir el primer byte solo para leer ese motivo. null = no hay motivo del backend.
export async function radioAudioMotivo(id) {
  try {
    const r = await fetch(radioAudioUrl(id), { headers: { Range: 'bytes=0-0' } })
    if (r.ok) return null
    const d = await r.json()
    return (d && d.error) || null
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
