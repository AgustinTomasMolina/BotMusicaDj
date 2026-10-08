// Tests de src/radioPantalla.js (f50): los textos de la columna «Cues» y de la energía del
// detalle, y la navegación con teclado de las filas del set y de las pestañas. Los valores
// esperados están escritos a mano.
//
// Uso (desde frontend/):  npm run test:unidad
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { textoCues, textoEnergia, siguienteFila, siguienteTab } from '../src/radioPantalla.js'

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
