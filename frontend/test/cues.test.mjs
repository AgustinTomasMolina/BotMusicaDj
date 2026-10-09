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
  fmtTiempo, parseTiempo, beatSegundos, pasoBeat, pctDe, tiempoDeX, padLibre, marcaDelPad,
  claseMarca, textoMarca, nombreMarca,
  errorLoop, beatsDeLoop, escribiendo, atajo, marcasRegla, fmtRegla, activable, tramoOnda,
  avisoDuracion,
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

test('pad libre y la marca de un pad', () => {
  const m = (tipo, num, inicio = 1) => ({ tipo, num, inicio })
  assert.equal(padLibre([]), 0)
  assert.equal(padLibre([m('cue', 0), m('cue', 1), m('cue', 3), m('memory', null)]), 2)
  assert.equal(padLibre([0, 1, 2, 3, 4, 5, 6, 7].map((n) => m('cue', n))), null)
  assert.deepEqual(marcaDelPad([m('memory', null, 5), m('cue', 2, 9)], 2), m('cue', 2, 9))
  assert.equal(marcaDelPad([m('cue', 2)], 3), null)
})

test('un loop en un pad (hot loop, f51) ocupa ese pad; uno sin pad no ocupa ninguno', () => {
  const m = (tipo, num, inicio = 1, fin = undefined) => ({ tipo, num, inicio, fin })
  // El pad 2 (num 1) lo tiene un loop: el primer libre es el 3 (num 2), no el del loop.
  assert.equal(padLibre([m('cue', 0), m('loop', 1, 4, 8), m('loop', null, 9, 12)]), 2)
  assert.equal(padLibre([0, 1, 2, 3].map((n) => m('cue', n)).concat([4, 5, 6, 7].map((n) => m('loop', n, n, n + 1)))), null)
  assert.deepEqual(marcaDelPad([m('loop', null, 9, 12), m('loop', 1, 4, 8)], 1), m('loop', 1, 4, 8))
  assert.equal(marcaDelPad([m('loop', null, 9, 12)], 0), null, 'un loop sin pad no es la marca del pad 1')
})

test('color, etiqueta y nombre de cada marca: el loop siempre naranja, con su pad escrito', () => {
  const m = (tipo, num, inicio = 64, fin = 71.5) => ({ tipo, num, inicio, fin })
  assert.deepEqual([m('cue', 0), m('cue', 7), m('memory', null), m('loop', null), m('loop', 2)].map(claseMarca),
    ['cue-c1', 'cue-c8', 'cue-mem', 'cue-loop', 'cue-loop'])
  assert.deepEqual([m('cue', 0), m('cue', 7), m('memory', null), m('loop', null), m('loop', 2)].map(textoMarca),
    ['1', '8', 'M', 'L', 'L3'])
  assert.equal(nombreMarca(m('loop', 2)), 'loop del pad 3 1:04.000 a 1:11.500')
  assert.equal(nombreMarca(m('loop', null)), 'loop 1:04.000 a 1:11.500')
  assert.equal(nombreMarca(m('cue', 4)), 'hot cue 5')
  assert.equal(nombreMarca(m('memory', null, 3.5)), 'memory cue en 0:03.500')
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

test('Espacio en un botón es del botón; en la onda o el cuerpo, del reproductor', () => {
  const boton = { tagName: 'BUTTON' }
  for (const target of [boton, { tagName: 'A' }, { tagName: 'INPUT', type: 'checkbox' },
    { tagName: 'DIV', getAttribute: (k) => (k === 'role' ? 'button' : null) }]) {
    assert.equal(activable(target), true, JSON.stringify(target))
    assert.equal(atajo({ key: ' ', target }), null, `Espacio en ${JSON.stringify(target)} no es play`)
  }
  const onda = { tagName: 'DIV', getAttribute: (k) => (k === 'role' ? 'slider' : null) }
  assert.equal(activable(onda), false)
  assert.deepEqual(atajo({ key: ' ', target: onda }), { tipo: 'play' })
  assert.deepEqual(atajo({ key: ' ', target: { tagName: 'BODY' } }), { tipo: 'play' })
  // Las letras sí siguen andando con el foco en un botón.
  assert.deepEqual(atajo({ key: 'c', target: boton }), { tipo: 'cue' })
})

test('con Shift no hay atajo: Shift+C no es C', () => {
  for (const k of ['C', 'c', 'M', 'I', 'O', ' ', '1', 'ArrowLeft']) {
    assert.equal(atajo({ key: k, shiftKey: true, target: { tagName: 'DIV' } }), null, `Shift+${k}`)
  }
})

test('la onda se ubica en su tiempo real cuando el archivo y la base no coinciden', () => {
  // Más corto: ocupa su parte del ancho, con todos los picos.
  assert.deepEqual(tramoOnda(1000, 60, 240), { ancho: 0.25, picos: 1000 })
  // Más largo: todo el ancho, solo los picos de los primeros `dur` segundos (no se comprime).
  assert.deepEqual(tramoOnda(1000, 300, 240), { ancho: 1, picos: 800 })
  assert.deepEqual(tramoOnda(1000, 240, 240), { ancho: 1, picos: 1000 })
  assert.deepEqual(tramoOnda(1000, 0, 240), { ancho: 0, picos: 0 })
})

test('el aviso de duración dice lo que hace el dibujo en cada caso', () => {
  assert.equal(avisoDuracion(240.3, 240), null)
  const corto = avisoDuracion(60, 240)
  assert.match(corto, /dura 1:00\.0 y la biblioteca dice 4:00\.0/)
  assert.match(corto, /ocupa solo los 1:00\.0 del archivo/)
  const largo = avisoDuracion(300, 240)
  assert.match(largo, /dura 5:00\.0, más que los 4:00\.0/)
  assert.match(largo, /muestra solo los primeros 4:00\.0/)
  assert.doesNotMatch(largo, /ocupa solo/)
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
