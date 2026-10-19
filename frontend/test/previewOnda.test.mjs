// Tests de src/previewOnda.js (f62): la vista previa de la onda de las filas de Mis playlists.
// Dónde va cada marca (tiempo → % del ancho), el tramo de un loop, la clase de color de cada
// marca, el resumen para el lector de pantalla y el dibujo de las bandas en el eje de la
// biblioteca. Los valores esperados están escritos a mano (no recalculados con el código).
//
// Uso (desde frontend/):  npm run test:unidad
import { test, beforeEach, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { durEje, marcasEnOnda, resumenMarcas, trazoBanda, ondaPreview, PUNTOS_PREVIEW } from '../src/previewOnda.js'
import { getPreview } from '../src/api.js'

const cue = (id, num, inicio) => ({ id, tipo: 'cue', num, inicio, fin: null })
const mem = (id, inicio) => ({ id, tipo: 'memory', num: null, inicio, fin: null })
const loop = (id, inicio, fin, num = null) => ({ id, tipo: 'loop', num, inicio, fin })

test('durEje: la duración de la biblioteca manda; sin ella, la del audio; sin ninguna, 0', () => {
  assert.equal(durEje({ dur: 200 }, 199.5), 200)
  assert.equal(durEje({ dur: null }, 199.5), 199.5)
  assert.equal(durEje({ dur: 0 }, 0), 0)
  assert.equal(durEje(null, undefined), 0)
  assert.equal(durEje({ dur: 'x' }, 'y'), 0)
})

test('marcasEnOnda: tiempo → % del ancho en el eje de la biblioteca', () => {
  const r = marcasEnOnda([cue(1, 0, 50), cue(2, 1, 0), cue(3, 7, 200)], 200)
  assert.deepEqual(r.map((m) => [m.id, m.left]), [[2, 0], [1, 25], [3, 100]])
  assert.ok(r.every((m) => m.width === null), 'un hot cue no es un tramo')
})

test('marcasEnOnda: el loop es un tramo de entrada a salida', () => {
  const [l] = marcasEnOnda([loop(9, 20, 30)], 80)
  assert.equal(l.left, 25)
  assert.equal(l.width, 12.5)
  // Un loop que se pasa del final del eje se corta en el borde y se marca `fuera`.
  const [f] = marcasEnOnda([loop(10, 70, 90)], 80)
  assert.equal(f.left, 87.5)
  assert.equal(f.width, 12.5)
  assert.equal(f.fuera, true)
})

test('marcasEnOnda: el color de cada marca — el pad del hot cue, la memory neutra, el loop naranja aunque tenga pad', () => {
  const r = marcasEnOnda([cue(1, 0, 10), cue(2, 2, 20), cue(3, 7, 30), mem(4, 40), loop(5, 50, 60, 2), loop(6, 70, 80)], 100)
  const clase = Object.fromEntries(r.map((m) => [m.id, m.clase]))
  assert.deepEqual(clase, { 1: 'cue-c1', 2: 'cue-c3', 3: 'cue-c8', 4: 'cue-mem', 5: 'cue-loop', 6: 'cue-loop' })
  // Orden de dibujo: loops atrás, memory, hot cues adelante.
  assert.deepEqual(r.map((m) => m.id), [5, 6, 4, 1, 2, 3])
})

test('marcasEnOnda: sin eje no se ubica nada; lo que no es una marca se ignora', () => {
  assert.deepEqual(marcasEnOnda([cue(1, 0, 10)], 0), [])
  assert.deepEqual(marcasEnOnda(null, 100), [])
  assert.deepEqual(marcasEnOnda([{ id: 1, tipo: 'raro', inicio: 3 }, { id: 2, tipo: 'cue', num: 0, inicio: null }], 100), [])
  // La marca que la API dice fuera del track (el archivo cambió) también va `fuera`.
  const [m] = marcasEnOnda([{ ...cue(3, 0, 10), fuera_del_track: true }], 100)
  assert.equal(m.fuera, true)
})

test('resumenMarcas: el mismo texto que la playlist («2 hot cues, 1 loop»); un hot loop cuenta como loop', () => {
  assert.equal(resumenMarcas([cue(1, 0, 1), cue(2, 1, 2), loop(3, 4, 6, 2)]), '2 hot cues, 1 loop')
  assert.equal(resumenMarcas([cue(1, 0, 1), mem(2, 3), mem(3, 4), loop(4, 5, 6), loop(5, 7, 8)]), '1 hot cue, 2 memory, 2 loops')
  assert.equal(resumenMarcas([]), 'sin marcas')
  assert.equal(resumenMarcas(undefined), 'sin marcas')
})

test('trazoBanda: una barra por punto, centrada, con la altura de alturaBanda (graves: v², medios: v^1.5·0,8, agudos: v·0,5)', () => {
  // graves 1 → alto entero (24); 0,5 → 0,25 × 24 = 6, centrada en y = 9.
  assert.equal(trazoBanda([1, 0.5, 0], 'graves', 24), 'M0 0h1v24h-1zM1 9h1v6h-1z')
  // agudos 1 → 0,5 × 24 = 12, en y = 6. Lo roto no dibuja; lo de más se acota a 1.
  assert.equal(trazoBanda([Number.NaN, 2], 'agudos', 24), 'M1 6h1v12h-1z')
  assert.equal(trazoBanda(null, 'medios'), '')
})

test('ondaPreview: la onda ocupa SU tramo del eje; un archivo más corto no se estira y uno más largo se corta', () => {
  const data = (n, durAudio) => ({ duracion_audio: durAudio, bandas: { graves: Array(n).fill(1), medios: Array(n).fill(0), agudos: Array(n).fill(0) } })
  const igual = ondaPreview(data(160, 200), 200)
  assert.equal(igual.ancho, 1)
  assert.equal(igual.puntos, 160)
  const corto = ondaPreview(data(160, 150), 200)
  assert.equal(corto.ancho, 0.75)
  assert.equal(corto.puntos, 160)
  const largo = ondaPreview(data(160, 400), 200)
  assert.equal(largo.ancho, 1)
  assert.equal(largo.puntos, 80)
  assert.equal(largo.trazos.graves.split('M').length - 1, 80, 'solo los puntos de los primeros 200 s')
  assert.equal(largo.trazos.medios, '')
})

test('ondaPreview: sin bandas no hay dibujo (no se inventa una onda)', () => {
  assert.equal(ondaPreview(null, 100), null)
  assert.equal(ondaPreview({ error: 'no está' }, 100), null)
  assert.equal(ondaPreview({ duracion_audio: 10, bandas: { graves: [1] } }, 100), null)
  assert.equal(ondaPreview({ duracion_audio: 0, bandas: { graves: [1], medios: [1], agudos: [1] } }, 0), null)
})

// getPreview (src/api.js): la URL que arma. `fetch` es uno falso que anota la URL.
let pedidas = []
const fetchOriginal = globalThis.fetch
beforeEach(() => {
  pedidas = []
  globalThis.fetch = async (url) => { pedidas.push(url); return { ok: true, status: 200, text: async () => '{}' } }
})
afterEach(() => { globalThis.fetch = fetchOriginal })

test('getPreview pide /preview con los puntos de la fila', async () => {
  await getPreview('0123456789abcdef', PUNTOS_PREVIEW)
  await getPreview('abc')
  assert.deepEqual(pedidas, ['/api/radio/tracks/0123456789abcdef/preview?puntos=160', '/api/radio/tracks/abc/preview'])
})
