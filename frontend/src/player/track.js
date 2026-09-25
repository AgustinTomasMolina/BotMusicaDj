// Forma única de un tema para el reproductor, venga de donde venga.
//
// Cada pantalla tiene su propia forma de track (la home, los resultados, las playlists, la
// radio). El reproductor no quiere conocerlas: cada una pasa por su `from*` y el resto del
// reproductor lee solo estos campos. Regla §6: lo que no viene queda en null — nada de 0,
// nada derivado (ni la clásica desde el Camelot, ni el formato desde la fuente).
//
//   key        identidad estable (para saber si "ese" tema es el que suena)
//   origen     'biblioteca' | 'busqueda' | 'playlist' | 'radio'
//   titulo, artista, fuente, duracion
//   audioSrc   URL de audio directo servida por el backend (biblioteca/radio)
//   bpm        número tal cual vino; bpmMedido=true si se midió sobre ESTE archivo (Rekordbox
//              o el motor) — se muestra con un decimal; si viene de una fuente externa,
//              con la precisión que trae (ver fmtBpm)
//   camelot, tonalidad, keyDudosa, formato, grade
//   raw        el objeto original de la pantalla (para descargar y agregar a playlist)
//   paraPlaylist  lo que se manda a AddToPlaylist
//   descargable   si el flujo de descarga de la app sirve para este tema
import { audioUrl, radioAudioUrl } from '../api'
import { songKey, metaKey } from '../utils'
import { youtubeId } from '../cover'

const n = (v) => (v === undefined || v === '' ? null : v)

// Home: track de la biblioteca local (XML de Rekordbox, BPM medido con un decimal).
export function fromLibrary(t) {
  return {
    key: `biblioteca|${t.id}`, origen: 'biblioteca',
    titulo: t.titulo, artista: n(t.artista), fuente: 'biblioteca', duracion: n(t.dur),
    audioSrc: audioUrl(t.id), cover: `/api/cover/${encodeURIComponent(t.id)}`,
    bpm: n(t.bpm), bpmMedido: t.bpm != null, camelot: n(t.camelot), tonalidad: n(t.tonalidad),
    keyDudosa: false, formato: n(t.formato), grade: null,
    raw: t, descargable: false,
    paraPlaylist: { titulo: t.titulo, artista: t.artista, bpm: t.bpm, camelot: t.camelot, genero: t.genero },
  }
}

// Resultados de búsqueda / modo lista / parecidas. `meta` = el metaMap de la app (BPM y
// género que se buscan aparte, los mismos que muestra la fila).
export function fromResult(c, metaMap) {
  const m = (metaMap && metaMap[metaKey(c)]) || {}
  const bpm = c.bpm || m.bpm || null
  const genero = c.genero || m.genero || null
  return {
    key: songKey(c), origen: 'busqueda',
    titulo: c.titulo, artista: n(c.artista), fuente: (c.fuente || '').toLowerCase() || null,
    duracion: n(c.duracion), audioSrc: null, cover: null,
    bpm, bpmMedido: false, camelot: n(c.camelot), tonalidad: null, keyDudosa: false,
    formato: null, grade: null,
    raw: c, descargable: !!(c.fuente && c.url),
    paraPlaylist: { ...c, bpm, genero, camelot: c.camelot },
    video_id: c.video_id, url: c.url, stream_url: c.stream_url, preview_url: c.preview_url,
    thumbnail: c.thumbnail, id: c.id,
  }
}

// Ítem de una playlist propia (crate). La playlist guarda la URL, no el video_id: el id de
// YouTube/SoundCloud se saca de la URL al reproducir (ver engineFor).
export function fromCrateItem(it) {
  return {
    key: songKey(it), origen: 'playlist',
    titulo: it.titulo, artista: n(it.artista), fuente: (it.fuente || '').toLowerCase() || null,
    duracion: n(it.duracion), audioSrc: null, cover: null,
    bpm: n(it.bpm), bpmMedido: false, camelot: n(it.camelot), tonalidad: null, keyDudosa: false,
    formato: it.descargado ? n(it.formato) : null, grade: it.grade && it.grade !== '?' ? it.grade : null,
    raw: it, descargable: !!(it.fuente && it.url) && !it.descargado,
    paraPlaylist: { titulo: it.titulo, artista: it.artista, fuente: it.fuente, url: it.url, thumbnail: it.thumbnail,
      duracion: it.duracion, bpm: it.bpm, camelot: it.camelot, genero: it.genero },
    url: it.url, thumbnail: it.thumbnail,
  }
}

// Radio DJ (motor/). TODAVÍA SIN USAR: la pantalla de radio tiene su propio <audio> y se
// integra en un paso aparte (ver el informe de la rama f28). Queda acá para que ese paso sea
// `player.playQueue(set.map(fromRadio), i)`. `leyenda` es el texto del motor para el `?`.
export function fromRadio(t, leyenda = null) {
  return {
    key: `radio|${t.id}`, origen: 'radio',
    titulo: t.titulo, artista: n(t.artista), fuente: 'radio', duracion: n(t.dur),
    audioSrc: radioAudioUrl(t.id), cover: null,
    bpm: n(t.bpm), bpmMedido: t.bpm != null, camelot: n(t.camelot), tonalidad: n(t.tonalidad),
    keyDudosa: !!t.key_dudosa, keyLeyenda: leyenda,
    formato: null, grade: null,
    raw: t, descargable: false,
    paraPlaylist: { titulo: t.titulo, artista: t.artista, bpm: t.bpm, camelot: t.camelot },
  }
}

const SC_ID = /\/tracks\/(?:soundcloud%3Atracks%3A|soundcloud:tracks:)?(\d+)/

// URL que entiende el widget de SoundCloud para este tema, o null.
export function soundcloudUrl(t) {
  if ((t.fuente || '') !== 'soundcloud') return null
  if (t.video_id) return `https://api.soundcloud.com/tracks/${t.video_id}`
  const m = SC_ID.exec(t.url || '')
  if (m) return `https://api.soundcloud.com/tracks/${m[1]}`
  return /^https:\/\/(www\.)?soundcloud\.com\//.test(t.url || '') ? t.url : null
}

// Fuentes de audio directo, en orden de intento.
export function directSources(t) {
  const out = []
  if (t.audioSrc) out.push(t.audioSrc)
  if (t.stream_url) out.push(t.stream_url)
  if (t.preview_url) out.push(t.preview_url)
  // Ligaudio publica un .m3u8 como stream (Chrome de escritorio no lo abre en <audio>) y el
  // MP3 directo como descarga: el MP3 es el mismo audio y sí se reproduce.
  if (['ligaudio', 'hitplayer'].includes(t.fuente) && /\.mp3(\?|$)/i.test(t.url || '')) out.push(t.url)
  return [...new Set(out)]
}

// Con qué motor suena un tema:
//   'audio'      <audio> propio (biblioteca, radio, MP3 directos, previews de 30 s)
//   'youtube'    IFrame API de YouTube (video visible en el monitor)
//   'soundcloud' Widget API de SoundCloud (widget visible en el monitor)
//   'embed'      solo se puede escuchar en el reproductor de la fuente (Spotify): la barra
//                no lo controla y lo dice
//   null         no hay nada reproducible
export function engineFor(t) {
  if (!t) return null
  if (t.audioSrc) return 'audio'
  if (youtubeId(t)) return 'youtube'
  if (soundcloudUrl(t)) return 'soundcloud'
  if (directSources(t).length) return 'audio'
  if (t.fuente === 'spotify' && t.id) return 'embed'
  return null
}

export const playable = (t) => ['audio', 'youtube', 'soundcloud'].includes(engineFor(t))

// BPM con la precisión que se conoce: medido sobre el archivo → un decimal (128 → "128.0");
// de una fuente externa que lo manda entero → entero (agregarle ".0" sería inventar precisión).
export function fmtBpm(t) {
  if (t?.bpm == null || t.bpm === '') return null
  const v = Number(t.bpm)
  if (!Number.isFinite(v) || v <= 0) return null
  return t.bpmMedido || !Number.isInteger(v) ? v.toFixed(1) : String(v)
}

export const NOMBRE_FUENTE = {
  biblioteca: 'Biblioteca', radio: 'Radio', youtube: 'YouTube', soundcloud: 'SoundCloud',
  spotify: 'Spotify', deezer: 'Deezer', ligaudio: 'MP3 directo', hitplayer: 'MP3 directo',
}
