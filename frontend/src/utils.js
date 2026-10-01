// Helpers y constantes compartidas (portadas 1:1 del index.html vanilla)

export const fmtDur = (s) => {
  s = parseInt(s) || 0
  const m = Math.floor(s / 60), x = s % 60
  return m + ':' + String(x).padStart(2, '0')
}

// Nombre de la fila Netflix por fuente
export const FUENTE_NOMBRES = {
  youtube: '▶ YouTube', soundcloud: '☁ SoundCloud', ligaudio: '⬇ MP3',
  hitplayer: '⬇ MP3', spotify: '♫ Spotify', deezer: '♫ Deezer',
}
export const FUENTE_ORDEN = ['youtube', 'soundcloud', 'ligaudio', 'hitplayer', 'spotify', 'deezer']
// Nombre corto y color de cada plataforma (chips de opciones en modo lista).
// Los MP3 directos dicen solo "MP3" (pedido del dueño, f38): el nombre del sitio no se muestra
// en ningún texto visible; dos versiones MP3 se distinguen por su nota y su posición.
export const FUENTE_CORTO = {
  youtube: 'YouTube', soundcloud: 'SoundCloud', ligaudio: 'MP3',
  hitplayer: 'MP3', spotify: 'Spotify', deezer: 'Deezer',
}
// Nombre de cada versión de una lista (el comparador): la plataforma, y si dos dicen lo mismo
// (dos MP3), con su número de opción — "MP3 · opción 4" —, el mismo número que usan la fila,
// la sub-lista y la barra ("opción 4 · MP3"). Sin eso dos columnas decían "MP3" y "MP3" (f40).
export function nombresDeVersiones(opciones) {
  const base = opciones.map((o) => FUENTE_CORTO[(o?.fuente || '').toLowerCase()] || o?.fuente || '?')
  const veces = base.reduce((m, n) => m.set(n, (m.get(n) || 0) + 1), new Map())
  return base.map((n, k) => (veces.get(n) > 1 ? `${n} · opción ${k + 1}` : n))
}
export const SRC_COLOR = {
  youtube: '#ff5c5c', soundcloud: '#ff8a3d', ligaudio: '#6fa8ff',
  hitplayer: '#27d3c4', spotify: '#1ed760', deezer: '#c98bff',
}

// Géneros preset (para el filtro de búsqueda). El usuario también puede escribir uno.
export const GENEROS = [
  'Techno', 'Hard Techno', 'Hard Bounce', 'Hardstyle', 'Hardcore',
  'House', 'Tech House', 'Deep House', 'Afro House', 'Melodic Techno',
  'Trance', 'Progressive', 'Drum & Bass', 'Dubstep', 'EDM',
  'Trap', 'Rap / Hip Hop', 'Reggaeton', 'Pop', 'Rock',
]

// Normaliza texto para comparar sin distinguir mayúsculas ni acentos ("Électro" ≈ "electro").
export const normalizeText = (s) => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim()

// Formatos de descarga (para el FormatPicker). WAV primero: es el default.
// AIFF no está a propósito: yt-dlp no lo acepta como formato de salida (acepta aac, alac,
// flac, m4a, mp3, opus, vorbis, wav) y la descarga de YouTube/SoundCloud fallaba.
export const SUB = { mp3: 'lossy', wav: 'lossless', flac: 'lossless' }
export const FORMATOS = [
  { val: 'wav', desc: 'Sin pérdida · sin comprimir', tag: 'lossless' },
  { val: 'flac', desc: 'Sin pérdida · comprimido', tag: 'lossless' },
  { val: 'mp3', desc: 'Comprimido · liviano', tag: 'lossy' },
]
export const DEFAULT_FORMAT = 'wav'

// Preferencia de formato: solo se guarda cuando el usuario elige uno a mano, así quien
// nunca eligió sigue al default (WAV) y quien eligió MP3 lo conserva. Un valor guardado
// que ya no es elegible (p. ej. 'aiff' de antes) cae al default.
const FORMAT_KEY = 'musiflix.formato'
export const loadFormat = () => {
  try {
    const v = window.localStorage.getItem(FORMAT_KEY)
    if (FORMATOS.some((f) => f.val === v)) return v
  } catch { /* storage bloqueado: default */ }
  return DEFAULT_FORMAT
}
export const saveFormat = (v) => {
  try { window.localStorage.setItem(FORMAT_KEY, v) } catch { /* sin storage: solo esta sesión */ }
}

// Identidad estable de una canción (para el estado de descarga, que sobrevive a
// cambios de opción en modo lista).
export const songKey = (c) => `${c?.fuente}|${c?.url}|${c?.titulo}|${c?.artista}`
// Clave de metadatos (BPM/género) — igual que el vanilla: titulo + artista, separados por
// un NUL escrito como escape (\0): mismo valor que antes, pero el archivo queda como texto
// para git (el byte NUL literal hacía que git lo tratara como binario y no mostrara diffs).
export const metaKey = (c) => `${c?.titulo || ''}\0${c?.artista || ''}`

// Punto de arranque del preview: saltar la intro y caer cerca del drop.
// Heurística por duración (~30% del tema, acotado a 20–80s). En música de DJ el
// primer drop suele estar por ahí. Devuelve segundos (0 si no aplica).
export const cuePoint = (c) => {
  const d = parseInt(c?.duracion) || 0
  if (!d) return 45
  return Math.max(20, Math.min(Math.round(d * 0.30), 80))
}

// ¿Se puede reproducir un preview de audio/iframe? (Spotify sin audio → solo zoom)
export const previewable = (c) => !!(
  (c?.fuente === 'youtube' && c?.video_id) ||
  (c?.fuente === 'soundcloud' && c?.video_id) ||
  c?.stream_url || c?.preview_url
)
