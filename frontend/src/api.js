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

// (f36) "Temas parecidos" (Deezer, /api/parecidas_lista) ya no se ofrece desde el front: la
// Station de SoundCloud la reemplaza. El endpoint sigue en el backend, sin uso desde acá.

// Versiones de UN tema de la Station en las otras plataformas (f36, /api/versiones): el tema
// de SoundCloud + la mejor de cada plataforma que sea el mismo tema, cada una con su nota.
// `signal` corta el pedido (timeout de la fila o salir de la pantalla). Un 400/500 o un cuerpo
// que no es JSON vuelve como {exito:false, mensaje} con el código.
export async function versiones(tema, formato, signal) {
  const r = await fetch('/api/versiones', {
    method: 'POST', signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tema, formato }),
  })
  const d = await cuerpoRadio(r)
  if (d && typeof d.exito === 'boolean') return d
  return { exito: false, mensaje: `El servidor no pudo buscar las versiones (HTTP ${r.status}).` }
}

// Station de SoundCloud del tema (f34, /api/station). De SoundCloud va su id numérico (la
// semilla es ese track); de otra fuente, título/artista/duración y el backend lo busca en
// SoundCloud con la regla de identidad. Siempre devuelve un objeto con `exito`: un 400/500 o
// un cuerpo que no es JSON se traduce a {exito:false, mensaje} con el código, no a "no pude
// conectar" (el servidor sí contestó).
//
// f43: además va la referencia de SoundCloud de la FILA (`g`), no solo de la opción elegida:
// en una búsqueda la elegida suele ser YouTube y la opción de SoundCloud del grupo se perdía.
export async function station(c, g) {
  const fuente = (c.fuente || '').toLowerCase()
  const ref = fuente === 'soundcloud' ? sourceAudioRefId(c) : null
  const q = new URLSearchParams({ fuente, fuente_id: ref || c.video_id || '', titulo: c.titulo || '', artista: c.artista || '' })
  if (c.duracion > 0) q.set('duracion', String(c.duracion))
  const sc = stationRef(g)
  if (sc) { q.set('sc_ref', sc.sc_ref); q.set('sc_ref_origen', sc.sc_ref_origen) }
  const r = await fetch(`/api/station?${q.toString()}`)
  const d = await cuerpoRadio(r)
  if (d && typeof d.exito === 'boolean') return d
  return { exito: false, motivo: `http-${r.status}`, mensaje: `El servidor no pudo pedir la Station (HTTP ${r.status}).` }
}

// Id numérico de SoundCloud del resultado (video_id o el de su URL de la API), o null.
// Orden "Para mezclar" de la Station (f45). El análisis es de UN tema (id de SoundCloud); el
// orden se pide cuando están todos y usa solo los análisis ya hechos en el backend.
export async function stationAnalisis(ref, signal) {
  const r = await fetch(`/api/station/analisis?ref=${encodeURIComponent(ref)}`, { signal })
  const d = await cuerpoRadio(r)
  if (d && typeof d.ok === 'boolean') return d
  return { ok: false, motivo: `El servidor no pudo analizar este tema (HTTP ${r.status}).` }
}

export async function stationOrden(semilla, items) {
  const r = await fetch('/api/station/orden', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ semilla: String(semilla), items: items.map(({ video_id, titulo, artista, duracion, url }) => ({ video_id, titulo, artista, duracion, url })) }),
  })
  const d = await cuerpoRadio(r)
  if (d && typeof d.exito === 'boolean') return d
  return { exito: false, mensaje: `El servidor no pudo armar el orden (HTTP ${r.status}).` }
}

function sourceAudioRefId(c) {
  if (/^\d+$/.test(String(c.video_id || ''))) return String(c.video_id)
  const m = /\/tracks\/(?:soundcloud%3Atracks%3A|soundcloud:tracks:)?(\d+)/.exec(c.url || '')
  return m ? m[1] : null
}

// Referencia de SoundCloud de una fila para la Station (f43), o null:
// - fila de la Station: `g.base` ES el tema de SoundCloud de la Station → origen "station";
// - otra fila (búsqueda, lista): la primera opción de SoundCloud del grupo → origen "busqueda".
//   Es CANDIDATA (el agrupador junta por palabras): el backend la valida antes de usarla.
// Un id que el backend rechazaría (su SC_ID: sin cero adelante, hasta 20 dígitos) no se manda:
// convertiría todo el pedido en un 400 en vez de ir a la búsqueda.
export function stationRef(g) {
  if (!g) return null
  const valido = (id) => (id && /^[1-9]\d{0,19}$/.test(id) ? id : null)
  if (g.base) {
    const id = valido(sourceAudioRefId(g.base))
    return id ? { sc_ref: id, sc_ref_origen: 'station' } : null
  }
  for (const o of g.opciones || []) {
    const id = (o?.fuente || '').toLowerCase() === 'soundcloud' ? valido(sourceAudioRefId(o)) : null
    if (id) return { sc_ref: id, sc_ref_origen: 'busqueda' }
  }
  return null
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

/* ---------- Audio de YouTube / SoundCloud para la barra (f32) ----------
   El backend saca el audio del tema con yt-dlp y lo sirve como un archivo más (con Range):
   la barra lo toca en su <audio>, sin el video. Se pide por fuente + id, nunca por URL. */
export const sourceAudioUrl = ({ fuente, ref }) =>
  `/api/fuente/audio?fuente=${encodeURIComponent(fuente)}&ref=${encodeURIComponent(ref)}`

// Qué es lo que suena (misma resolución cacheada en el backend): si es un fragmento, duración…
export async function sourceAudioInfo({ fuente, ref }) {
  const r = await fetch(`/api/fuente/audio/info?fuente=${encodeURIComponent(fuente)}&ref=${encodeURIComponent(ref)}`)
  return r.ok ? r.json() : null
}

// Por qué no se pudo: el <audio> avisa que falló pero no deja leer la respuesta. El backend
// recuerda el fallo un rato, así que volver a preguntar no repite la resolución.
// null = no hay motivo del backend (se muestra el genérico).
export async function sourceAudioReason(src) {
  try {
    const r = await fetch(src, { headers: { Range: 'bytes=0-0' } })
    if (r.ok) return null
    const txt = await r.text()
    let d = null
    try { d = JSON.parse(txt) } catch { /* no era JSON */ }
    if (d && d.error) return d.error
    return `El servidor falló al preparar este audio (HTTP ${r.status}).`
  } catch { return null }
}

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
  return descargaM3u8(await fetch(`/api/radio/set.m3u8?${qs.toString()}`))
}

// El .m3u8 de un set GUARDADO: la foto, en su orden, sin re-armar nada
// (/api/radio/sets/{id}/m3u8). `faltan` = cuántas rutas de la foto ya no están en la
// biblioteca del motor (el archivo las trae igual: son las que había).
export async function exportarSetGuardadoM3u8(id) {
  const r = await fetch(`/api/radio/sets/${encodeURIComponent(id)}/m3u8`)
  const d = await descargaM3u8(r)
  if (d.ok) {
    const f = Number(r.headers.get('X-DJRadio-Faltan'))
    d.faltan = Number.isFinite(f) ? f : null
  }
  return d
}

async function descargaM3u8(r) {
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

/* ---------- Sets guardados de la radio (tarea 16) ----------
   Un set guardado es la FOTO de lo que se vio: la pantalla lo dibuja con lo que devuelve
   GET /api/radio/sets/{id}, nunca re-armándolo. Todas devuelven {ok, status, data} con el
   cuerpo leído por `cuerpoRadio`, por lo mismo que `getRadioSet`: un 400/404/409 trae el
   motivo del backend en `data.error` y la pantalla lo muestra tal cual. */
async function pedirSets(ruta, metodo = 'GET', cuerpo) {
  const init = { method: metodo }
  if (cuerpo !== undefined) {
    init.headers = { 'Content-Type': 'application/json' }
    init.body = JSON.stringify(cuerpo)
  }
  const r = await fetch(ruta, init)
  return { ok: r.ok, status: r.status, data: await cuerpoRadio(r) }
}

// Guarda el set que está EN PANTALLA: los parámetros que el set dice que usó, `esperado`
// (los ids de los pasos, en orden) y la `huella` que devolvió /api/radio/set. Si el backend
// re-arma otra cosa contesta 409 con el motivo, y no se guarda nada.
export const guardarRadioSet = (params, esperado, huella, nombre) => {
  const cuerpo = { esperado, huella }
  for (const [k, v] of Object.entries(params || {})) {
    if (v === undefined || v === null || v === '') continue
    cuerpo[k] = v
  }
  if (nombre && nombre.trim()) cuerpo.nombre = nombre
  return pedirSets('/api/radio/sets', 'POST', cuerpo)
}
export const listarRadioSets = () => pedirSets('/api/radio/sets')
export const getRadioSetGuardado = (id) => pedirSets(`/api/radio/sets/${encodeURIComponent(id)}`)
export const renombrarRadioSet = (id, nombre) => pedirSets(`/api/radio/sets/${encodeURIComponent(id)}`, 'PATCH', { nombre })
export const borrarRadioSet = (id) => pedirSets(`/api/radio/sets/${encodeURIComponent(id)}`, 'DELETE')
export const calificarTransicion = (id, n, calificacion, motivo) =>
  pedirSets(`/api/radio/sets/${encodeURIComponent(id)}/transiciones/${n}`, 'PUT', { calificacion, motivo: motivo ?? null })
export const descalificarTransicion = (id, n) =>
  pedirSets(`/api/radio/sets/${encodeURIComponent(id)}/transiciones/${n}`, 'DELETE')

/* ---------- Editor de cues (f48) ----------
   Las marcas del dueño (hot cues, memory cues, loops) de un track de la biblioteca del motor.
   Misma forma {ok, status, data} que los sets guardados. Las escrituras devuelven la lista
   ENTERA de marcas como quedó en la base: la pantalla dibuja eso y su «Guardado» es lo que
   el servidor confirmó. Tiempos en segundos con tres decimales. */
const rutaMarcas = (id) => `/api/radio/tracks/${encodeURIComponent(id)}/marcas`
export const getMarcas = (id) => pedirSets(rutaMarcas(id))
export const crearMarca = (id, marca) => pedirSets(rutaMarcas(id), 'POST', marca)
export const cambiarMarca = (id, marcaId, cambios) => pedirSets(`${rutaMarcas(id)}/${encodeURIComponent(marcaId)}`, 'PATCH', cambios)
export const borrarMarca = (id, marcaId) => pedirSets(`${rutaMarcas(id)}/${encodeURIComponent(marcaId)}`, 'DELETE')
export const getOnda = (id) => pedirSets(`/api/radio/tracks/${encodeURIComponent(id)}/onda`)
export const contarMarcas = (ids) => pedirSets(`/api/radio/marcas/conteo?ids=${encodeURIComponent(ids.join(','))}`)

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

// Baja un tema guardado en la playlist (f41). Solo viaja el formato: qué se baja (url,
// fuente, título) lo saca el server del item guardado. Devuelve {ok, status, data} con el
// cuerpo leído por `cuerpoRadio`: un 400/404/409 trae el motivo en `data.mensaje` y un 500
// en text/plain no se confunde con "no pude conectar".
export async function descargarItemPlaylist(pid, itemId, formato) {
  const r = await fetch(`/api/playlists/${encodeURIComponent(pid)}/items/${encodeURIComponent(itemId)}/descargar`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ formato }),
  })
  return { ok: r.ok, status: r.status, data: await cuerpoRadio(r) }
}

/* ---------- Playlists como centro (f53): importar, género y análisis con el motor ----------
   Todas devuelven {ok, status, data} con el cuerpo leído por `cuerpoRadio`: un 4xx trae el
   motivo del server en `data.mensaje` y la pantalla lo muestra tal cual. */

// El XML va CRUDO (sin multipart): el server lo lee con tope y rechaza DTD/entidades. El
// nombre del archivo viaja aparte, solo para mostrarlo.
export async function leerXmlRekordbox(file) {
  const r = await fetch('/api/importar/rekordbox/leer', {
    method: 'POST', body: file,
    headers: { 'Content-Type': 'application/xml', 'X-Nombre-Archivo': encodeURIComponent(file.name || 'rekordbox.xml') },
  })
  return { ok: r.ok, status: r.status, data: await cuerpoRadio(r) }
}
export const leerXmlConfigurado = () => pedirSets('/api/importar/rekordbox/leer', 'POST', { usar_configurado: true })
export const importarRekordbox = (token, playlists, actualizar = false) =>
  pedirSets('/api/importar/rekordbox', 'POST', { token, playlists, actualizar })
export const listarCarpetas = (raiz, ruta = '') => pedirSets(raiz === undefined || raiz === null
  ? '/api/importar/carpetas'
  : `/api/importar/carpetas?raiz=${encodeURIComponent(raiz)}&ruta=${encodeURIComponent(ruta)}`)
export const importarCarpeta = (raiz, ruta, recursivo = false, actualizar = false) =>
  pedirSets('/api/importar/carpeta', 'POST', { raiz, ruta, recursivo, actualizar })
export const analizarPlaylist = (pid) => pedirSets(`/api/playlists/${encodeURIComponent(pid)}/analizar`, 'POST', {})
export const progresoAnalisis = (pid) => pedirSets(`/api/playlists/${encodeURIComponent(pid)}/analisis`)
export const editarGeneroItem = (pid, itemId, genero) =>
  pedirSets(`/api/playlists/${encodeURIComponent(pid)}/items/${encodeURIComponent(itemId)}`, 'PATCH', { genero })
export const generoALosSinGenero = (pid, genero) => pedirSets(`/api/playlists/${encodeURIComponent(pid)}/genero`, 'POST', { genero })
