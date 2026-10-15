// Tests de src/exportRekordbox.js (f56): los textos del diálogo «Exportar a Rekordbox» y lo que
// se dice de los cues que vienen de Rekordbox. Mismo esquema que los otros: `node --test` + el
// Vite del proyecto. Las respuestas de ejemplo tienen la forma de la API (ver
// tests/test_server_rekordbox.py).
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
const m = await vite.ssrLoadModule('/src/exportRekordbox.js')

const resumenApi = (extra = {}) => ({
  exito: true, nombre: 'Set', archivo: 'MusiFlix - Set.xml', total: 3, incluidos: 2,
  marcas: { hot_cues: 2, memory: 1, loops: 2 }, marcas_fuera: 0, keys_omitidas: 1,
  omitidos: [{ id: 9, titulo: 'tres', motivo: 'Todavía no lo analizó el motor: analizalo y exportá de nuevo.' }],
  ...extra,
})

test('textoMarcasExport: cada tipo con su número y singular/plural; sin marcas lo dice', () => {
  assert.equal(m.textoMarcasExport({ hot_cues: 2, memory: 1, loops: 2 }), '2 hot cues, 1 memory cue, 2 loops')
  assert.equal(m.textoMarcasExport({ hot_cues: 1, memory: 0, loops: 1 }), '1 hot cue, 1 loop')
  assert.equal(m.textoMarcasExport({ hot_cues: 0, memory: 3, loops: 0 }), '3 memory cues')
  assert.equal(m.textoMarcasExport({ hot_cues: 0, memory: 0, loops: 0 }), 'sin marcas')
  assert.equal(m.textoMarcasExport(null), 'sin marcas')
})

test('resumenExport: temas, marcas, avisos de key y de marcas fuera, y cada omitido con su motivo', () => {
  const v = m.resumenExport(resumenApi({ marcas_fuera: 2 }))
  assert.deepEqual(v, {
    temas: '2 de 3 temas', marcas: '2 hot cues, 1 memory cue, 2 loops',
    boton: 'Descargar el XML (2 temas)', puede: true,
    avisos: ['1 tema va sin key: el motor no está seguro (?) y Rekordbox la va a analizar.',
      '2 marcas quedan afuera: caen después del final del audio (el archivo cambió).'],
    omitidos: [{ id: 9, texto: 'tres: Todavía no lo analizó el motor: analizalo y exportá de nuevo.' }],
  })
  const nada = m.resumenExport(resumenApi({ incluidos: 0, keys_omitidas: 0, marcas: { hot_cues: 0, memory: 0, loops: 0 } }))
  assert.deepEqual([nada.puede, nada.boton, nada.avisos], [false, 'No hay temas para exportar', []])
  assert.equal(m.resumenExport(null), null)
})

test('textoExportado: lo que dicen las cabeceras; la grilla solo si se pidió', () => {
  const h = { temas: '2', marcas: '5', omitidos: '1', grillas: '0' }
  assert.equal(m.textoExportado('MusiFlix - Set.xml', h), 'MusiFlix - Set.xml · 2 temas · 5 marcas · 1 afuera')
  assert.equal(m.textoExportado('MusiFlix - Set.xml', { ...h, grillas: '1', omitidos: '0' }, true),
    'MusiFlix - Set.xml · 2 temas · 5 marcas · grilla estimada en 1')
})

test('textoCuesImportados: lo que entró, lo que la página conservó y lo que no entra, con números', () => {
  assert.equal(m.textoCuesImportados(null), null)
  assert.equal(m.textoCuesImportados({ error: 'La base del motor está ocupada. Los cues no se guardaron.' }),
    'Cues: La base del motor está ocupada. Los cues no se guardaron.')
  const c = { temas: 2, agregadas: 3, ya_estaban: 1, conservadas: 1, reemplazadas: 0, fuera_del_tema: 1,
    sin_lugar: 0, sin_archivo: 2, ambiguas: 1, color_distinto: 2, nombre_descartado: 1, ignoradas: { tipo: 1 }, error: null }
  assert.equal(m.textoCuesImportados(c), 'Cues: 3 cues nuevos · 1 ya estaba · 1 pad quedó como en la página (no se pisa) · '
    + '2 en temas sin archivo no entran · 1 en temas con el nombre repetido en tus carpetas no entra (no elijo un archivo a la suerte) · 1 fuera del audio · 1 fade-in, fade-out o load (la página no los tiene) no entra · '
    + '2 con otro color en Rekordbox: acá toman el de su pad · 1 sin nombre (el de Rekordbox no se puede guardar)')
  assert.equal(m.textoCuesImportados({ agregadas: 0, ya_estaban: 5, reemplazadas: 2, ignoradas: {} }),
    'Cues: 0 cues nuevos · 5 ya estaban · 2 pads se pisaron con Rekordbox')
})

test('textoCuesXml: cues y loops de cada playlist del XML, o nada', () => {
  assert.equal(m.textoCuesXml({ cues: 3, loops: 2 }), '3 cues · 2 loops')
  assert.equal(m.textoCuesXml({ cues: 1, loops: 0 }), '1 cue')
  assert.equal(m.textoCuesXml({ cues: 0, loops: 0 }), null)
})
