// Estado de la carpeta vigilada (f65) en una línea, para el cajón del historial (f66).
//
// Solo lo que devuelve GET /api/vigilancia (server.vigilancia_estado → Vigilante.estado()):
// apagada → el `mensaje` del server, que dice cómo prenderla; prendida → la carpeta (solo el
// nombre), el último escaneo y los contadores. Un contador que no vino no se dibuja como 0.

const cuenta = (v) => (Number.isInteger(v) && v >= 0 ? v : null)
const plural = (n, uno, varios) => `${n} ${n === 1 ? uno : varios}`

// `ultimo_ciclo` es la hora LOCAL del server sin zona ("2026-10-21T10:05:33", ver
// pipeline/vigilar.py `_ciclo`): se muestra como "21/10 10:05" leyendo el texto, sin pasarlo por
// Date (que lo reinterpretaría). Algo que no tiene esa forma: null.
export function fechaCiclo(iso) {
  const m = typeof iso === 'string' ? iso.match(/^\d{4}-(\d{2})-(\d{2})T(\d{2}):(\d{2})/) : null
  return m ? `${m[2]}/${m[1]} ${m[3]}:${m[4]}` : null
}

// {tipo: 'error'|'apagada'|'activa', titulo, partes: [texto], aviso, procesando: [nombre], reporte}
export function resumenVigilancia(r) {
  const base = { partes: [], aviso: null, procesando: [], reporte: null }
  if (!r || r.error || r.exito === false) {
    return { ...base, tipo: 'error', titulo: 'No pude leer el estado de la carpeta vigilada.' }
  }
  if (!r.activa) {
    return { ...base, tipo: 'apagada', titulo: r.mensaje || 'La carpeta vigilada está apagada.' }
  }
  const e = r.estado || {}
  const partes = []
  const f = fechaCiclo(e.ultimo_ciclo)
  partes.push(f ? `último escaneo ${f}` : 'todavía sin escanear')
  for (const [k, uno, varios] of [['pendientes', 'pendiente', 'pendientes'], ['procesados', 'procesado', 'procesados'], ['fallidos', 'fallido', 'fallidos']]) {
    const n = cuenta(e[k])
    if (n !== null) partes.push(plural(n, uno, varios))
  }
  const creciendo = cuenta(e.creciendo)
  if (creciendo) partes.push(`${creciendo} todavía copiándose`)
  let aviso = null
  if (e.alcanzable === false) aviso = `No se alcanza la carpeta${e.ultimo_error ? `: ${e.ultimo_error}` : ''}`
  else if (e.ultimo_error) aviso = e.ultimo_error
  else if (e.corriendo === false) aviso = 'El vigilante no está corriendo.'
  return {
    ...base, tipo: 'activa', titulo: e.carpeta ? `Carpeta vigilada «${e.carpeta}»` : 'Carpeta vigilada',
    partes, aviso,
    procesando: Array.isArray(e.procesando) ? e.procesando.filter((x) => typeof x === 'string') : [],
    reporte: typeof e.ultimo_reporte === 'string' && e.ultimo_reporte ? e.ultimo_reporte : null,
  }
}
