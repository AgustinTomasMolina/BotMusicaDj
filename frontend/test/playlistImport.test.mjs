// Tests de src/playlistImport.js (f53): cómo se dice el estado de cada tema, de dónde sale el
// BPM/key, el resumen del encabezado, los puntos de color de las marcas y los textos del
// diálogo de importar. Mismo esquema que los otros: `node --test` + el Vite del proyecto.
//
// Uso (desde frontend/):  npm run test:unidad

import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const vite = await createServer({
  configFile: false, root: fileURLToPath(new URL('..', import.meta.url)), logLevel: 'error',
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom',
})
after(() => vite.close())
const m = await vite.ssrLoadModule('/src/playlistImport.js')

const tema = (extra = {}) => ({ id: 1, titulo: 'T', archivo_estado: 'ok', local: true, analisis: { estado: 'pendiente' }, ...extra })

test('textoArchivo: en la PC no es «Descargado»', () => {
  assert.equal(m.textoArchivo(tema()), 'En tu PC')
  assert.equal(m.textoArchivo(tema({ local: false })), 'Descargado')
  assert.equal(m.textoArchivo(tema({ archivo_estado: 'no-existe' })), 'No encuentro el archivo')
  assert.equal(m.textoArchivo(tema({ archivo_estado: 'no-encontrado' })), 'No encuentro el archivo')
  assert.equal(m.textoArchivo(tema({ archivo_estado: 'ambiguo', homonimos: 3 })), 'Hay 3 archivos con ese nombre')
  assert.equal(m.textoArchivo(tema({ archivo_estado: 'sin-archivo' })), 'Sin archivo')
})

test('estadoTema: el archivo manda; con archivo, el estado del análisis en texto', () => {
  assert.deepEqual(m.estadoTema(tema({ analisis: { estado: 'analizado' } })), { clave: 'analizado', texto: 'Analizado', tono: 'ok' })
  assert.deepEqual(m.estadoTema(tema({ analisis: { estado: 'analizando' } })), { clave: 'analizando', texto: 'Analizando…', tono: 'info' })
  assert.equal(m.estadoTema(tema({ analisis: { estado: 'fallo' } })).texto, 'No se pudo analizar')
  assert.equal(m.estadoTema(tema({ analisis: { estado: 'pendiente' } })).texto, 'Falta analizar')
  assert.equal(m.estadoTema(tema({ archivo_estado: 'ambiguo', homonimos: 2, analisis: { estado: 'sin-archivo' } })).texto,
    'Hay 2 archivos con ese nombre')
})

test('datosTema: BPM con un decimal, key con las dos notaciones y el ? ; de dónde salen', () => {
  assert.deepEqual(m.datosTema(tema({ analisis: { bpm: 128, camelot: '8A', tonalidad: 'Am', key_dudosa: true, dato: 'motor' } })),
    { bpm: '128.0', key: '8A · Am ?', fuente: null, dudosa: true })
  assert.deepEqual(m.datosTema(tema({ analisis: { bpm: 128.4, camelot: '8A', tonalidad: 'Am', key_dudosa: false, dato: 'rekordbox' } })),
    { bpm: '128.4', key: '8A · Am', fuente: 'de Rekordbox', dudosa: false })
  // Sin dato: null (la pantalla dibuja «?»), nunca un 0.
  assert.deepEqual(m.datosTema(tema({ analisis: { bpm: 0, camelot: null, dato: null } })),
    { bpm: null, key: null, fuente: null, dudosa: false })
})

test('puntosMarcas: un punto por hot cue con el color de su pad, memory y loop', () => {
  assert.deepEqual(m.puntosMarcas({ total: 4, pads: [0, 2], memory: 1, loop: 1 }).map((p) => p.clase),
    ['cue-c1', 'cue-c3', 'cue-mem', 'cue-loop'])
  assert.deepEqual(m.puntosMarcas({ total: 0, pads: [], memory: 0, loop: 0 }), [])
  assert.deepEqual(m.puntosMarcas(null), [])
  assert.equal(m.textoMarcas({ total: 4, pads: [0, 2], memory: 1, loop: 1 }), '2 hot cues, 1 memory, 1 loop')
})

test('resumenPlaylist: duración, rango de BPM y de dónde sale, géneros, sin género', () => {
  const items = [
    tema({ duracion: 3600, genero: 'Techno', analisis: { estado: 'analizado', bpm: 128.4, dato: 'motor' } }),
    tema({ duracion: 300, genero: 'Techno', analisis: { estado: 'pendiente', bpm: 140, dato: 'rekordbox' } }),
    tema({ duracion: 60, genero: '', analisis: { estado: 'pendiente', bpm: null } }),
    tema({ duracion: 0, genero: 'Acid', analisis: { estado: 'pendiente' } }),
  ]
  assert.deepEqual(m.resumenPlaylist(items), {
    temas: 4, duracion: '1 h 06 min', bpm: '128.4–140.0', bpmFuente: null,
    generos: ['Techno', 'Acid'], sinGenero: 1, analizados: 1,
  })
  const soloXml = m.resumenPlaylist([tema({ analisis: { bpm: 130, dato: 'rekordbox' } })])
  assert.equal(soloXml.bpmFuente, 'de Rekordbox', 'un rango solo de Rekordbox se dice')
})

test('textoEncontrados y detalleFaltantes: no se esconde por qué faltan', () => {
  const p = { total: 3, encontrados: 1, ambiguos: 1, faltan: 1, inexistentes: 2 }
  assert.equal(m.textoEncontrados(p), '1 de 3 encontrados')
  assert.deepEqual(m.detalleFaltantes(p), [
    '1 con el nombre repetido en tus carpetas (no elijo uno a la suerte)',
    '1 que no están en tus carpetas de música',
    '2 que el XML lista pero no trae en la colección',
  ])
  assert.deepEqual(m.detalleFaltantes({ total: 1, encontrados: 1, ambiguos: 0, faltan: 0, inexistentes: 0 }), [])
})

test('textoProgreso', () => {
  assert.equal(m.textoProgreso({ corriendo: true, hechos: 1, total: 3, actual: 'A — Uno', fallidos: [] }), 'Analizando 2 de 3 · A — Uno')
  assert.equal(m.textoProgreso({ corriendo: false, hechos: 3, total: 3, fallidos: [{}] }),
    'Análisis terminado: 2 de 3 analizados · 1 no se pudo analizar')
  assert.equal(m.textoProgreso({ corriendo: false, hechos: 0, total: 0, fallidos: [] }), null)
})

test('origenTexto y motivoDe', () => {
  assert.deepEqual(['rekordbox', 'carpeta', 'musiflix', undefined].map(m.origenTexto), ['Rekordbox', 'Carpeta', 'MusiFlix', 'MusiFlix'])
  assert.equal(m.motivoDe({ status: 400, data: { mensaje: 'XML roto' } }), 'XML roto')
  assert.equal(m.motivoDe({ status: 500, data: { error_texto: 'Internal' } }), 'El servidor falló (HTTP 500).')
})
