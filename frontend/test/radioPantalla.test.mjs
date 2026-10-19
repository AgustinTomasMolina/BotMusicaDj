// Tests de src/radioPantalla.js (f50): los textos de la columna «Cues» y de la energía del
// detalle, y la navegación con teclado de las filas del set y de las pestañas. Los valores
// esperados están escritos a mano.
//
// Uso (desde frontend/):  npm run test:unidad
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  textoCues, textoEnergia, siguienteFila, siguienteTab, textoPool, etiquetaMotivo, generoDe, textoAnalizando,
  resumenMarcas, temaDeTrack,
} from '../src/radioPantalla.js'

const ceros = { otro_genero: 0, sin_genero: 0, sin_archivo: 0, analizando: 0, sin_analizar: 0, fallo_analisis: 0, no_es_track: 0, duplicado: 0 }

test('textoPool (f55): el renglón del diseño, con los números del server y sin los ceros', () => {
  assert.deepEqual(textoPool({ genero: 'Groove', entran: 38, total: 44, excluidos: { ...ceros, otro_genero: 4, sin_genero: 1, sin_archivo: 1 } }), {
    titular: 'Entran 38 de 44 temas, del mismo género que la semilla (Groove)',
    detalle: '4 de otro género · 1 sin género · 1 sin archivo',
    afuera: 6,
  })
  assert.deepEqual(textoPool({ genero: 'Techno', entran: 1, total: 1, excluidos: ceros }),
    { titular: 'Entran 1 de 1 tema, del mismo género que la semilla (Techno)', detalle: '', afuera: 0 })
  assert.equal(textoPool({ genero: 'X', entran: 2, total: 5, excluidos: { ...ceros, no_es_track: 1, duplicado: 2 } }).detalle,
    '1 no es un track (menos de 90 s) · 2 repetidos')
  assert.equal(textoPool(null), null)
  assert.equal(textoPool({ entran: undefined, total: 3 }), null)
})

test('resumenMarcas (f55): la misma cuenta que server._marcas_por_clase', () => {
  assert.deepEqual(resumenMarcas([
    { tipo: 'cue', num: 3 }, { tipo: 'memory', num: null }, { tipo: 'cue', num: 0 },
    { tipo: 'loop', num: 5 }, { tipo: 'memory', num: null },
  ]), { total: 5, pads: [0, 3], memory: 2, loop: 1 })
  assert.deepEqual(resumenMarcas([]), { total: 0, pads: [], memory: 0, loop: 0 })
  assert.deepEqual(resumenMarcas(undefined), { total: 0, pads: [], memory: 0, loop: 0 })
})

test('etiquetaMotivo, generoDe y textoAnalizando (f55)', () => {
  assert.equal(etiquetaMotivo('otro_genero'), 'otro género')
  assert.equal(etiquetaMotivo('duplicado'), 'repetido')
  const rp = { generos: [{ clave: 'techno', genero: 'Techno' }, { clave: 'house', genero: 'House' }] }
  assert.equal(generoDe(rp, { clave_genero: 'house' }).genero, 'House')
  assert.equal(generoDe(rp, { clave_genero: 'trance' }), null)
  assert.equal(generoDe(rp, null), null)
  assert.equal(textoAnalizando(0), null)
  assert.equal(textoAnalizando(1), '1 tema todavía se está analizando: el set se arma con los que ya están; cuando termine, armalo de nuevo para sumarlo.')
  assert.equal(textoAnalizando(3), '3 temas todavía se están analizando: el set se arma con los que ya están; cuando terminen, armalo de nuevo para sumarlos.')
})

test('textoCues: «sin cues» con 0, singular y plural, y guion sin conteo (no un 0)', () => {
  assert.equal(textoCues(0), 'sin cues')
  assert.equal(textoCues(1), '1 cue')
  assert.equal(textoCues(3), '3 cues')
  assert.equal(textoCues(undefined), '—')
  assert.equal(textoCues(null), '—')
})

test('textoEnergia: los dos percentiles del motor tal cual, sin adjetivos', () => {
  assert.equal(textoEnergia(64, 71), '64 → 71')
  assert.equal(textoEnergia(80, 12), '80 → 12')
  assert.equal(textoEnergia(null, 71), '— → 71', 'sin dato del anterior, guion; no se inventa')
  assert.equal(textoEnergia(null, 71, { hayAnterior: false }), '71', 'la semilla no tiene anterior')
  assert.equal(textoEnergia(null, null, { hayAnterior: false }), '—')
})

test('siguienteFila: ↑ ↓ Inicio Fin, sin dar la vuelta', () => {
  const ns = [1, 2, 3, 4]
  assert.equal(siguienteFila(ns, 2, 'ArrowDown'), 3)
  assert.equal(siguienteFila(ns, 4, 'ArrowDown'), 4, 'en la última se queda')
  assert.equal(siguienteFila(ns, 1, 'ArrowUp'), 1, 'en la primera se queda')
  assert.equal(siguienteFila(ns, 3, 'Home'), 1)
  assert.equal(siguienteFila(ns, 2, 'End'), 4)
  assert.equal(siguienteFila(ns, 2, 'ArrowLeft'), null, '← → no son de la lista')
  assert.equal(siguienteFila([], 1, 'ArrowDown'), null)
})

const TABS = [{ k: 'cues' }, { k: 'info' }, { k: 'onda', off: true }]

test('siguienteTab: ← → dan la vuelta y saltean la deshabilitada', () => {
  assert.equal(siguienteTab(TABS, 'cues', 'ArrowRight'), 'info')
  assert.equal(siguienteTab(TABS, 'info', 'ArrowRight'), 'cues', 'después de Información no va a «Onda avanzada»: vuelve a la primera')
  assert.equal(siguienteTab(TABS, 'cues', 'ArrowLeft'), 'info', 'hacia atrás da la vuelta sin pasar por la deshabilitada')
  assert.equal(siguienteTab(TABS, 'info', 'Home'), 'cues')
  assert.equal(siguienteTab(TABS, 'cues', 'End'), 'info', 'Fin va a la última HABILITADA')
  assert.equal(siguienteTab(TABS, 'cues', 'ArrowDown'), null)
  assert.equal(siguienteTab([{ k: 'x', off: true }], 'x', 'ArrowRight'), null)
})

test('temaDeTrack (f61): el track de un paso del set con la forma que pide el editor D', () => {
  const t = { id: 42, titulo: 'Tema A', artista: 'Artista B', bpm: 128.0, en_biblioteca: true }
  assert.deepEqual(temaDeTrack(t), {
    id: 42, titulo: 'Tema A', artista: 'Artista B', analisis: { radio_id: 42, estado: 'analizado' },
  })
  // Sin artista ni título: cadenas vacías, nunca undefined (el editor arma «artista — título»).
  assert.deepEqual(temaDeTrack({ id: 7 }), { id: 7, titulo: '', artista: '', analisis: { radio_id: 7, estado: 'analizado' } })
  // El id 0 es un id válido.
  assert.equal(temaDeTrack({ id: 0, titulo: 'x' }).analisis.radio_id, 0)
})

test('temaDeTrack (f61): fuera de la biblioteca o sin id no hay tema para editar', () => {
  assert.equal(temaDeTrack({ id: 3, titulo: 'x', en_biblioteca: false }), null)
  assert.equal(temaDeTrack({ titulo: 'x' }), null)
  assert.equal(temaDeTrack({ id: null }), null)
  assert.equal(temaDeTrack(null), null)
})
