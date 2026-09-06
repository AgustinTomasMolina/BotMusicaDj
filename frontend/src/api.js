// Llamadas al backend FastAPI. En dev van por el proxy de Vite (ver vite.config.js);
// en prod contra el mismo origen que sirve el build.

async function json(res) {
  return res.json()
}

export async function buscar(q, formato, genero) {
  const r = await fetch(`/api/buscar?q=${encodeURIComponent(q || '')}&limite=28` +
    `&formato=${encodeURIComponent(formato || 'mp3')}&genero=${encodeURIComponent(genero || '')}`)
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
