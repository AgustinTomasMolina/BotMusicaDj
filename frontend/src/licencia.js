// Licencia, origen y permiso de descarga de un tema (f66).
//
// Regla del dueño (CLAUDE.md, 2026-10-09) y de §6: la licencia y el origen se muestran como los
// DECLARA la fuente; si no vienen, «no declarado», nunca uno inventado ni uno "probable". Hoy la
// única fuente que los declara es Jamendo (`license_ccurl` y la página del tema, ver
// fuentes/jamendo.py); las demás no dan licencia.
//
// Jamendo también dice si el artista permite bajar el tema (`descargable`). Con `false` el botón
// de descarga queda deshabilitado y dice por qué; el server igual vuelve a preguntarle a Jamendo
// antes de bajar (no confía en lo que mande la pantalla).

export const NO_DECLARADO = 'no declarado'

// Las fuentes que declaran licencia en sus resultados: en sus filas se muestra la pastilla.
export const FUENTES_CON_LICENCIA = ['jamendo']

const CC = /^https?:\/\/(?:www\.)?creativecommons\.org\/licenses\/([a-z]+(?:-[a-z]+)*)\/(\d+(?:\.\d+)*)\/?$/i
const CC0 = /^https?:\/\/(?:www\.)?creativecommons\.org\/publicdomain\/zero\/(\d+(?:\.\d+)*)\/?$/i

// El texto declarado, sin espacios de más; null si no hay (vacío, no es texto o el literal).
export function declarado(v) {
  if (typeof v !== 'string') return null
  const t = v.trim()
  return t && t.toLowerCase() !== NO_DECLARADO ? t : null
}

// Cómo se nombra la licencia en pantalla. Una URL de Creative Commons se nombra por su código y
// versión, que es lo que dice la propia URL (http://creativecommons.org/licenses/by-sa/3.0/ →
// "CC BY-SA 3.0"); la URL entera va en el tooltip. Cualquier otro texto, tal cual. Sin dato,
// «no declarado».
export function licenciaTexto(lic) {
  const v = declarado(lic)
  if (!v) return NO_DECLARADO
  let m = v.match(CC)
  if (m) return `CC ${m[1].toUpperCase()} ${m[2]}`
  m = v.match(CC0)
  if (m) return `CC0 ${m[1]}`
  return v
}

// El origen (la página del tema en la fuente): el sitio como texto y la URL para el link. Un
// texto que no es una URL http(s) va tal cual y sin link.
export function origenTexto(o) {
  const v = declarado(o)
  if (!v) return { texto: NO_DECLARADO, href: null }
  try {
    const u = new URL(v)
    if (u.protocol === 'https:' || u.protocol === 'http:') return { texto: u.hostname.replace(/^www\./, ''), href: v }
  } catch { /* no es una URL: va como texto */ }
  return { texto: v, href: null }
}

export const muestraLicencia = (c) => FUENTES_CON_LICENCIA.includes((c?.fuente || '').toLowerCase())

export const SIN_DESCARGA_JAMENDO = 'El artista no habilitó la descarga en Jamendo'

// Por qué no se puede bajar este tema, o null si se puede. Solo cuenta lo que la fuente DICE
// (`descargable === false`): un resultado sin el campo (todas las otras fuentes) se puede bajar.
export function motivoSinDescarga(c) {
  if (c?.descargable !== false) return null
  return (c.fuente || '').toLowerCase() === 'jamendo' ? SIN_DESCARGA_JAMENDO : 'Esta fuente no permite descargar este tema'
}

// "Descargar todas": qué filas se bajan y cuántas quedan afuera, por qué. Un Go+ de SoundCloud
// (30 s) no es el tema; uno que el artista no dejó bajar, tampoco se intenta.
export function separarBajables(filas) {
  const bajables = [], soloPreview = [], sinPermiso = []
  for (const c of filas) {
    if (c?.solo_preview) soloPreview.push(c)
    else if (motivoSinDescarga(c)) sinPermiso.push(c)
    else bajables.push(c)
  }
  return { bajables, soloPreview, sinPermiso }
}
