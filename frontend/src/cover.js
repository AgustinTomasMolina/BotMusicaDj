// Carátulas: de dónde sale la imagen real de un tema y cómo se dibuja cuando no hay.
//
// Por qué existe (medido 2026-09-25, rama f28): el recuadro "del color del fondo" que se veía
// en lugar de la carátula no era CSS ni referrer (las imágenes que llegaban cargaban: 200,
// sin CORS de por medio). Era que NO llegaba ninguna imagen:
//  - Biblioteca local (home): el XML de Rekordbox no trae imágenes y el front ni lo intentaba
//    (un degradado oscuro por posición) → /api/cover/{id} devuelve la carátula embebida en el
//    archivo, si la tiene.
//  - SoundCloud: con extract_flat yt-dlp manda `thumbnail: null` y la imagen solo en
//    `thumbnails` → el backend ahora la elige (search_agent._thumbnail_soundcloud).
//  - Ligaudio y HitPlayer no publican carátula en su HTML: ahí no hay imagen que traer.
//  - Sin imagen, la fila mostraba --color-well con rayas al 7%: a simple vista, el fondo.
// YouTube sí mandaba la suya (32 de 32 maxresdefault con 200 en la medición). `mqdefault.jpg`
// queda como segundo intento: existe para todo video y cubre las playlists guardadas sin
// `thumbnail` (se saca el id de la URL).

const YT_ID = /(?:[?&]v=|youtu\.be\/|\/embed\/|\/shorts\/)([\w-]{11})/

// Id de YouTube de un tema: el `video_id` de la búsqueda o, en las playlists (que guardan
// solo la URL), el `v=` de la URL. null si no es de YouTube.
export function youtubeId(c) {
  if (!c) return null
  if ((c.fuente || '').toLowerCase() !== 'youtube') return null
  if (c.video_id) return c.video_id
  const m = YT_ID.exec(c.url || '')
  return m ? m[1] : null
}

// Intentos de imagen, en orden. El <img> pasa al siguiente en onError; sin ninguno que cargue
// se dibuja el placeholder. Nunca se "adivina" una URL que no salga de la fuente.
export function coverCandidates(c) {
  const out = []
  if (c?.cover) out.push(c.cover)            // tracks ya normalizados por el reproductor
  if (c?.thumbnail) out.push(c.thumbnail)
  const yt = youtubeId(c)
  if (yt) out.push(`https://i.ytimg.com/vi/${yt}/mqdefault.jpg`)
  return [...new Set(out.filter(Boolean))]
}

/* ---------- Placeholder con identidad ----------
   Cuando de verdad no hay carátula, un recuadro vacío parece un error. El placeholder es una
   "etiqueta blanca" (white label, el disco promo sin arte): colores y ángulos sacados del
   título + artista con un hash, así el mismo tema se ve siempre igual. Es DECORATIVO: no
   codifica ningún dato del tema (ni BPM, ni key, ni género) y lleva una nota musical chica
   que dice "acá no hay carátula" sin disfrazarse de una. Colores del sistema Nocturne, sin
   los de las notas de calidad ni los de plataforma (esos significan algo). */
const PH_COLORS = [
  'var(--color-accent)', 'var(--color-brand)', 'var(--color-info)',
  'var(--color-section-ghost)', 'var(--color-accent-2)', 'var(--color-brand-300)',
]

function hash32(s) {
  // FNV-1a: determinista y sin dependencias.
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) }
  return h >>> 0
}

export function placeholderStyle(c) {
  const h = hash32(`${c?.titulo || ''}\u0000${c?.artista || ''}`)
  const c1 = PH_COLORS[h % PH_COLORS.length]
  const c2 = PH_COLORS[(h >>> 3) % PH_COLORS.length === h % PH_COLORS.length
    ? ((h >>> 3) + 1) % PH_COLORS.length : (h >>> 3) % PH_COLORS.length]
  return {
    '--ph-c1': c1,
    '--ph-c2': c2,
    '--ph-ang': `${(h >>> 7) % 180}deg`,
    '--ph-ang2': `${(h >>> 13) % 360}deg`,
    // El círculo va a una esquina (nunca al centro, donde está la nota).
    '--ph-x': `${(h >>> 17) & 1 ? 16 + ((h >>> 18) % 12) : 72 + ((h >>> 18) % 12)}%`,
    '--ph-y': `${(h >>> 23) & 1 ? 16 + ((h >>> 24) % 12) : 72 + ((h >>> 24) % 12)}%`,
  }
}
