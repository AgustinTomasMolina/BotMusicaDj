// Tests de la lógica pura del editor de cues (f48, src/cues.js): formateo y lectura de
// tiempos, el paso de un beat, posiciones en la onda, el pad libre, la validación de un loop
// y qué tecla es qué atajo (y que ninguno actúe mientras se escribe).
//
// Los valores esperados están escritos a mano a partir de la definición (60/BPM, m:ss.mmm),
// no recalculados con el código bajo prueba.
//
// Uso (desde frontend/):  npm run test:unidad

import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  fmtTiempo, parseTiempo, beatSegundos, pasoBeat, pctDe, tiempoDeX, padLibre, hotCue,
  errorLoop, beatsDeLoop, escribiendo, atajo, marcasRegla, fmtRegla,
} from '../src/cues.js'

test('fmtTiempo: m:ss.mmm con el redondeo hecho una sola vez', () => {
  assert.equal(fmtTiempo(0), '0:00.000')
  assert.equal(fmtTiempo(7.25), '0:07.250')
  assert.equal(fmtTiempo(83.456), '1:23.456')
  assert.equal(fmtTiempo(59.9996), '1:00.000')        // no "0:60.000"
  assert.equal(fmtTiempo(600.5, 1), '10:00.5')
  assert.equal(fmtTiempo(61, 0), '1:01')
  assert.equal(fmtTiempo(null), null)
  assert.equal(fmtTiempo(Number.NaN), null)
})

test('parseTiempo: lee lo que se escribe en la tabla', () => {
  assert.equal(parseTiempo('1:23.456'), 83.456)
  assert.equal(parseTiempo('83.456'), 83.456)
  assert.equal(parseTiempo(' 0:05.5 '), 5.5)
  assert.equal(parseTiempo('0:05,25'), 5.25)
  assert.equal(parseTiempo('2:00'), 120)
  assert.equal(parseTiempo('1:75'), null)               // 75 segundos no es un tiempo con minutos
  assert.equal(parseTiempo('1:2:3'), null)
  assert.equal(parseTiempo('-1'), null)
  assert.equal(parseTiempo('abc'), null)
  assert.equal(parseTiempo(''), null)
  assert.equal(parseTiempo('0:01.2345'), null)          // más de milisegundos: no se adivina
  // Ida y vuelta: lo que muestra la tabla se vuelve a leer igual.
  for (const s of [0, 0.001, 9.346, 59.999, 61.5, 3599.999]) assert.equal(parseTiempo(fmtTiempo(s)), s)
})

test('el beat sale del BPM medido; sin BPM no hay beat', () => {
  assert.equal(beatSegundos(120), 0.5)
  assert.equal(beatSegundos(128.4), 60 / 128.4)
  assert.equal(beatSegundos(null), null)
  assert.equal(beatSegundos(0), null)
  assert.equal(beatSegundos(-120), null)
  assert.equal(pasoBeat(10, 120, 1, 240), 10.5)
  assert.equal(pasoBeat(10, 120, -1, 240), 9.5)
  assert.equal(pasoBeat(0.2, 120, -1, 240), 0)          // no pasa del principio
  assert.equal(pasoBeat(239.9, 120, 1, 240), 240)       // ni del final
  assert.equal(pasoBeat(10, null, 1, 240), null)
})

test('posiciones en la onda', () => {
  assert.equal(pctDe(60, 240), 25)
  assert.equal(pctDe(300, 240), 100)
  assert.equal(pctDe(-1, 240), 0)
  assert.equal(pctDe(5, 0), 0)
  assert.equal(tiempoDeX(250, 1000, 240), 60)
  assert.equal(tiempoDeX(-10, 1000, 240), 0)
  assert.equal(tiempoDeX(1200, 1000, 240), 240)
})

test('pad libre y hot cue de un pad', () => {
  const m = (tipo, num, inicio = 1) => ({ tipo, num, inicio })
  assert.equal(padLibre([]), 0)
  assert.equal(padLibre([m('cue', 0), m('cue', 1), m('cue', 3), m('memory', null)]), 2)
  assert.equal(padLibre([0, 1, 2, 3, 4, 5, 6, 7].map((n) => m('cue', n))), null)
  assert.deepEqual(hotCue([m('memory', null, 5), m('cue', 2, 9)], 2), m('cue', 2, 9))
  assert.equal(hotCue([m('cue', 2)], 3), null)
})

test('un loop se cierra solo hacia adelante y dentro del tema', () => {
  assert.match(errorLoop(null, 10, 240), /entrada/)
  assert.match(errorLoop(10, 10, 240), /después de la entrada/)
  assert.match(errorLoop(10, 9, 240), /después de la entrada/)
  assert.match(errorLoop(230, 241, 240), /final del tema/)
  assert.equal(errorLoop(10, 12, 240), null)
  assert.equal(beatsDeLoop(10, 12, 120), 4)
  assert.equal(beatsDeLoop(10, 12, null), null)
})

const tecla = (key, extra = {}) => ({ key, target: { tagName: 'DIV' }, ...extra })

test('cada atajo es su acción', () => {
  assert.deepEqual(atajo(tecla(' ')), { tipo: 'play' })
  assert.deepEqual(atajo(tecla('c')), { tipo: 'cue' })
  assert.deepEqual(atajo(tecla('C')), { tipo: 'cue' })
  assert.deepEqual(atajo(tecla('m')), { tipo: 'memory' })
  assert.deepEqual(atajo(tecla('i')), { tipo: 'loopIn' })
  assert.deepEqual(atajo(tecla('o')), { tipo: 'loopOut' })
  assert.deepEqual(atajo(tecla('ArrowLeft')), { tipo: 'beat', dir: -1 })
  assert.deepEqual(atajo(tecla('ArrowRight')), { tipo: 'beat', dir: 1 })
  assert.deepEqual(atajo(tecla('1')), { tipo: 'ir', num: 0 })
  assert.deepEqual(atajo(tecla('8')), { tipo: 'ir', num: 7 })
  assert.equal(atajo(tecla('9')), null)
  assert.equal(atajo(tecla('x')), null)
  // Con modificadores no es un atajo del editor (Ctrl+C copia, Alt+← vuelve atrás).
  assert.equal(atajo(tecla('c', { ctrlKey: true })), null)
  assert.equal(atajo(tecla('ArrowLeft', { altKey: true })), null)
  assert.equal(atajo(tecla('m', { metaKey: true })), null)
  assert.equal(atajo(tecla('c', { isComposing: true })), null)
})

test('ningún atajo actúa mientras se escribe', () => {
  const campos = [
    { tagName: 'INPUT', type: 'text' }, { tagName: 'INPUT', type: 'search' },
    { tagName: 'INPUT' }, { tagName: 'INPUT', type: 'number' }, { tagName: 'TEXTAREA' },
    { tagName: 'SELECT' }, { tagName: 'DIV', isContentEditable: true },
  ]
  for (const target of campos) {
    assert.equal(escribiendo(target), true, JSON.stringify(target))
    for (const k of [' ', 'c', 'm', 'i', 'o', '1', 'ArrowLeft']) assert.equal(atajo({ key: k, target }), null, `${k} en ${JSON.stringify(target)}`)
  }
  // Un botón o un checkbox no son "escribir": ahí los atajos siguen andando.
  assert.equal(escribiendo({ tagName: 'BUTTON' }), false)
  assert.equal(escribiendo({ tagName: 'INPUT', type: 'button' }), false)
  assert.deepEqual(atajo({ key: 'c', target: { tagName: 'BUTTON' } }), { tipo: 'cue' })
})

test('la regla de tiempo deja como mucho 6 etiquetas redondas', () => {
  assert.deepEqual(marcasRegla(240), [0, 60, 120, 180, 240])
  assert.deepEqual(marcasRegla(60), [0, 15, 30, 45, 60])
  assert.deepEqual(marcasRegla(25), [0, 5, 10, 15, 20, 25])
  assert.deepEqual(marcasRegla(27), [0, 5, 10, 15, 20, 25])
  assert.deepEqual(marcasRegla(0), [])
  assert.ok(marcasRegla(3 * 3600).length <= 6)
  assert.equal(fmtRegla(125), '2:05')
})
