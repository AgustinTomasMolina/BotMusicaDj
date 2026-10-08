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
