// Lo puro de «Exportar a Rekordbox (con cues)» y de los cues que vienen de Rekordbox (f56):
// los textos del diálogo de exportar y lo que se dice de los cues al importar. Sin React ni
// red: lo prueban los tests de unidad (test/exportRekordbox.test.mjs).
//
// Regla (CLAUDE.md §6): lo que no se puede exportar o importar se DICE, con su número; nada se
// esconde ni se redondea a «todo bien».

const s = (n, uno, varios) => `${n} ${n === 1 ? uno : varios}`

// «2 hot cues, 1 memory cue, 2 loops» o «sin marcas».
export function textoMarcasExport(m) {
  if (!m) return 'sin marcas'
  const partes = []
  if (m.hot_cues) partes.push(s(m.hot_cues, 'hot cue', 'hot cues'))
  if (m.memory) partes.push(s(m.memory, 'memory cue', 'memory cues'))
  if (m.loops) partes.push(s(m.loops, 'loop', 'loops'))
  return partes.length ? partes.join(', ') : 'sin marcas'
}

// El resumen del diálogo (respuesta de GET /api/playlists/{id}/rekordbox).
export function resumenExport(d) {
  if (!d) return null
  const avisos = []
  if (d.keys_omitidas) {
    avisos.push(`${s(d.keys_omitidas, 'tema va', 'temas van')} sin key: el motor no está seguro (?) y Rekordbox la va a analizar.`)
  }
  if (d.marcas_fuera) {
    avisos.push(`${s(d.marcas_fuera, 'marca queda', 'marcas quedan')} afuera: cae${d.marcas_fuera === 1 ? '' : 'n'} después del final del audio (el archivo cambió).`)
  }
  return {
    temas: `${d.incluidos} de ${s(d.total, 'tema', 'temas')}`,
    marcas: textoMarcasExport(d.marcas),
    boton: d.incluidos ? `Descargar el XML (${s(d.incluidos, 'tema', 'temas')})` : 'No hay temas para exportar',
    puede: d.incluidos > 0,
    avisos,
    omitidos: (d.omitidos || []).map((o) => ({ id: o.id, texto: `${o.titulo}: ${o.motivo}` })),
  }
}

// Lo que se dice después de bajar el XML (cabeceras X-MusiFlix-*).
export function textoExportado(nombre, h, grillaPedida = false) {
  const n = (k) => Number(h?.[k]) || 0
  const partes = [s(n('temas'), 'tema', 'temas'), s(n('marcas'), 'marca', 'marcas')]
  if (grillaPedida) partes.push(`grilla estimada en ${n('grillas')}`)
  if (n('omitidos')) partes.push(`${n('omitidos')} afuera`)
  return `${nombre} · ${partes.join(' · ')}`
}

const IGNORADAS = {
  tipo: 'fade-in, fade-out o load (la página no los tiene)',
  pad: 'en un pad que no es A-H',
  tiempo: 'con un tiempo que no se puede leer',
  loop: 'loops sin salida',
}

// Lo que pasó con los cues de UNA playlist importada (el `cues` de cada resultado). null si no
// traía marcas.
export function textoCuesImportados(c) {
  if (!c) return null
  // El server ya dice que no se guardaron y qué hacer («volvé a importar tildando Actualizar»).
  if (c.error) return `Cues: ${c.error}`
  const partes = []
  partes.push(`${s(c.agregadas, 'cue nuevo', 'cues nuevos')}`)
  if (c.ya_estaban) partes.push(`${c.ya_estaban} ya estaba${c.ya_estaban === 1 ? '' : 'n'}`)
  if (c.conservadas) partes.push(`${s(c.conservadas, 'pad quedó', 'pads quedaron')} como en la página (no se pisa${c.conservadas === 1 ? '' : 'n'})`)
  if (c.reemplazadas) partes.push(`${s(c.reemplazadas, 'pad se pisó', 'pads se pisaron')} con Rekordbox`)
  if (c.sin_archivo) partes.push(`${c.sin_archivo} en temas sin archivo no entra${c.sin_archivo === 1 ? '' : 'n'}`)
  if (c.ambiguas) partes.push(`${c.ambiguas} en temas con el nombre repetido en tus carpetas no entra${c.ambiguas === 1 ? '' : 'n'} (no elijo un archivo a la suerte)`)
  if (c.fuera_del_tema) partes.push(`${c.fuera_del_tema} fuera del audio`)
  if (c.sin_lugar) partes.push(`${c.sin_lugar} por encima del tope por tema`)
  for (const [k, n] of Object.entries(c.ignoradas || {})) {
    if (n) partes.push(`${n} ${IGNORADAS[k] || k} no entra${n === 1 ? '' : 'n'}`)
  }
  if (c.color_distinto) partes.push(`${c.color_distinto} con otro color en Rekordbox: acá toma${c.color_distinto === 1 ? '' : 'n'} el de su pad`)
  if (c.nombre_descartado) partes.push(`${c.nombre_descartado} sin nombre (el de Rekordbox no se puede guardar)`)
  return `Cues: ${partes.join(' · ')}`
}

// «3 cues · 2 loops» de cada playlist del XML, o null.
export function textoCuesXml(p) {
  if (!p || (!p.cues && !p.loops)) return null
  const partes = []
  if (p.cues) partes.push(s(p.cues, 'cue', 'cues'))
  if (p.loops) partes.push(s(p.loops, 'loop', 'loops'))
  return partes.join(' · ')
}
