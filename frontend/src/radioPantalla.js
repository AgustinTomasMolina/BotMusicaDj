// Lógica pura de la pantalla de Radio DJ (f50): textos de la lista y del detalle y la
// navegación con teclado de las filas y las pestañas. Sin React ni DOM, para probarla con
// `node --test` (test/radioPantalla.test.mjs).
//
// REGLA (§6): acá no se calcula ningún dato del motor. Los números (marcas, percentiles de
// energía) llegan de la API y solo se ponen en palabras.

// La columna «Cues»: cuántas marcas tiene el tema (hot cues, memory y loops, como las cuenta
// /api/radio/marcas/conteo). Sin conteo (no llegó o el tema ya no está) → «—», no un 0.
export function textoCues(n) {
  if (n === null || n === undefined || !Number.isFinite(Number(n))) return '—'
  const v = Number(n)
  if (v === 0) return 'sin cues'
  return `${v} cue${v === 1 ? '' : 's'}`
}

// La energía en el «¿Por qué este track?»: el percentil del anterior y el de este, los dos
// tal cual los manda el motor. Sin anterior (la semilla) va solo el de este. Sin dato, guion.
// Sin adjetivos («sube un poco»): eso sería un juicio que el motor no hizo.
export function textoEnergia(anterior, actual, { hayAnterior = true } = {}) {
  const f = (v) => (v === null || v === undefined ? '—' : String(v))
  return hayAnterior ? `${f(anterior)} → ${f(actual)}` : f(actual)
}

// Navegación entre filas con ↑ ↓ Inicio Fin (sin dar la vuelta: es una lista, no un carrusel).
// `ns` son las claves de las filas en orden; devuelve la clave a la que ir o null.
export function siguienteFila(ns, actual, tecla) {
  if (!Array.isArray(ns) || ns.length === 0) return null
  const i = ns.indexOf(actual)
  if (tecla === 'Home') return ns[0]
  if (tecla === 'End') return ns[ns.length - 1]
  if (tecla === 'ArrowDown') return i < 0 ? ns[0] : ns[Math.min(ns.length - 1, i + 1)]
  if (tecla === 'ArrowUp') return i < 0 ? ns[0] : ns[Math.max(0, i - 1)]
  return null
}

// Pestañas (patrón tablist): ← → dan la vuelta, Inicio y Fin van a la primera y a la última, y
// las deshabilitadas (`off`) se saltean siempre. Devuelve la clave o null si la tecla no es de
// navegación o no hay ninguna habilitada.
export function siguienteTab(tabs, actual, tecla) {
  const ok = (tabs || []).filter((t) => !t.off).map((t) => t.k)
  if (ok.length === 0) return null
  const i = ok.indexOf(actual)
  if (tecla === 'Home') return ok[0]
  if (tecla === 'End') return ok[ok.length - 1]
  if (tecla === 'ArrowRight') return ok[(i + 1 + ok.length) % ok.length]
  if (tecla === 'ArrowLeft') return ok[(i < 0 ? 0 : i - 1 + ok.length) % ok.length]
  return null
}

// ---------- f55: la radio desde una playlist ----------

// Los motivos por los que un tema queda afuera del set (códigos de `radio_playlist.MOTIVOS`),
// en el orden en que los decide el server, con cómo se dicen contados.
const MOTIVOS = [
  ['otro_genero', (n) => `${n} de otro género`],
  ['sin_genero', (n) => `${n} sin género`],
  ['sin_archivo', (n) => `${n} sin archivo`],
  ['analizando', (n) => `${n} todavía analizando`],
  ['sin_analizar', (n) => `${n} sin analizar`],
  ['fallo_analisis', (n) => `${n} con el análisis fallado`],
  ['no_es_track', (n) => `${n} ${n === 1 ? 'no es un track' : 'no son tracks'} (menos de 90 s)`],
  ['duplicado', (n) => `${n} ${n === 1 ? 'repetido' : 'repetidos'}`],
]

// El renglón de arriba del set: «Entran 38 de 44 temas, del mismo género que la semilla
// (Groove)» y «4 de otro género · 1 sin género · 1 sin archivo». Los números son los del server
// (`pool` de /api/radio/set o un género de /api/radio/playlists/{id}); acá solo se ponen en
// palabras, y lo que está en cero no se nombra. `afuera` = cuántos quedan afuera en total.
export function textoPool(pool) {
  if (!pool || !Number.isFinite(pool.entran) || !Number.isFinite(pool.total)) return null
  const ex = pool.excluidos || {}
  const partes = MOTIVOS.filter(([k]) => ex[k] > 0).map(([k, f]) => f(ex[k]))
  const afuera = MOTIVOS.reduce((s, [k]) => s + (ex[k] > 0 ? ex[k] : 0), 0)
  return {
    titular: `Entran ${pool.entran} de ${pool.total} tema${pool.total === 1 ? '' : 's'}, del mismo género que la semilla${pool.genero ? ` (${pool.genero})` : ''}`,
    detalle: partes.join(' · '),
    afuera,
  }
}

// La etiqueta corta de un motivo, para la lista de los que quedan afuera (el texto largo, con
// el porqué, lo escribe el server).
export function etiquetaMotivo(codigo) {
  return {
    otro_genero: 'otro género', sin_genero: 'sin género', sin_archivo: 'sin archivo',
    analizando: 'analizando', sin_analizar: 'sin analizar', fallo_analisis: 'falló el análisis',
    no_es_track: 'no es un track', duplicado: 'repetido',
  }[codigo] || codigo || ''
}

// El género de la semilla elegida dentro de lo que contestó /api/radio/playlists/{id}: con él
// la pantalla dice, ANTES de armar, cuántos entran y qué queda afuera. null si no hay.
export function generoDe(radioPlaylist, semilla) {
  if (!radioPlaylist || !semilla || !Array.isArray(radioPlaylist.generos)) return null
  return radioPlaylist.generos.find((g) => g.clave === semilla.clave_genero) || null
}

// Los puntos de color de un tema a partir de la lista de marcas que el server devolvió al
// escribir (el editor): la MISMA cuenta que `server._marcas_por_clase` (pads = los `num` de los
// hot cues, en orden; memory y loops contados). Así, después de poner un cue, los puntos se
// actualizan sin volver a pedir los conteos de todo el set.
export function resumenMarcas(lista) {
  const ms = Array.isArray(lista) ? lista : []
  if (!ms.length) return { total: 0, pads: [], memory: 0, loop: 0 }
  return {
    total: ms.length,
    pads: ms.filter((m) => m.tipo === 'cue' && m.num !== null && m.num !== undefined).map((m) => m.num).sort((a, b) => a - b),
    memory: ms.filter((m) => m.tipo === 'memory').length,
    loop: ms.filter((m) => m.tipo === 'loop').length,
  }
}

// El aviso de lo que todavía se está analizando: el set se arma con lo que ya está.
export function textoAnalizando(n) {
  if (!Number.isFinite(n) || n <= 0) return null
  return `${n} ${n === 1 ? 'tema' : 'temas'} todavía ${n === 1 ? 'se está analizando' : 'se están analizando'}: el set se arma con los que ya están; cuando ${n === 1 ? 'termine' : 'terminen'}, armalo de nuevo para sumarl${n === 1 ? 'o' : 'os'}.`
}
