// Tests de la lógica pura del editor grande de cues (f54, src/editorD.js): zoom → rango de la
// ventana, mapeo tiempo ↔ pixel, la grilla estimada (cuándo se dibuja, atenuada, líneas,
// compás, ajuste del dueño), el snap, el pedido a /onda3, las columnas de la onda, el
// deshacer / rehacer con ids que cambian, y los atajos.
//
// Los valores esperados están escritos a mano desde la definición (128 BPM → 0,46875 s por
// beat; 8 compases de 4 beats = 15 s), no recalculados con el código bajo prueba.
//
// Uso (desde frontend/):  npm run test:unidad

import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  estadoGrilla, periodoBeat, lineasGrilla, snapGrilla, moverAjuste, textoAjuste, leerAjuste, guardarAjuste,
  anchoZoom, ventana, xDeTiempo, tiempoDeXV, tramoPedido, alcanza, columnas,
  historialVacio, registrar, deshacer, rehacer, reemplazarId, inversa, cuerpoCrear, diferencia,
  atajoD, fmtBpmMedido, letraPad, rotuloMarca, compasDe, nombreD, descartarPaso, CONFIANZA_BAJA, NITIDEZ_BAJA,
  temaVigente, beatsLoop,
} from '../src/editorD.js'

// Una grilla de /onda3 a 128 BPM con el primer beat en 0,25 s.
const G = { estimada: true, bpm: 128, periodo_s: 0.46875, primer_beat_s: 0.25, confianza: 0.8, concentracion: 0.8,
  beats_por_compas: 4, compas_ref: null, motivo: null }

/* ---------- la grilla: cuándo, cómo ---------- */

test('sin grilla (sin BPM) o sin primer beat no se dibuja nada y se dice por qué', () => {
  assert.deepEqual(estadoGrilla(null), { hay: false, atenuada: false, baja: [], motivo: 'el tema no tiene BPM medido' })
  const sin = { ...G, primer_beat_s: null, motivo: 'no se distingue el beat del contratiempo' }
  assert.deepEqual(estadoGrilla(sin), { hay: false, atenuada: false, baja: [], motivo: 'no se distingue el beat del contratiempo' })
  assert.deepEqual(lineasGrilla(sin, 0, 0, 10), [])
  assert.equal(snapGrilla(1.234, sin, 0), 1.234, 'sin grilla el snap no mueve nada')
})

test('con confianza baja la grilla va atenuada; desde el umbral, no', () => {
  assert.equal(CONFIANZA_BAJA, 0.35)
  assert.deepEqual(estadoGrilla({ ...G, confianza: 0.34 }), { hay: true, atenuada: true, baja: ['confianza'], motivo: null })
  assert.deepEqual(estadoGrilla({ ...G, confianza: 0.35 }), { hay: true, atenuada: false, baja: [], motivo: null })
})

test('confianza 1,00 no alcanza: con nitidez (concentración) baja también va atenuada', () => {
  // El caso de la auditoría f54: acorde sostenido bajo los kicks, fase 56 ms corrida.
  assert.equal(NITIDEZ_BAJA, 0.45)
  assert.deepEqual(estadoGrilla({ ...G, confianza: 1, concentracion: 0.37 }), { hay: true, atenuada: true, baja: ['nitidez'], motivo: null })
  assert.deepEqual(estadoGrilla({ ...G, confianza: 1, concentracion: 0.45 }), { hay: true, atenuada: false, baja: [], motivo: null })
  assert.deepEqual(estadoGrilla({ ...G, confianza: 0.2, concentracion: 0.4 }).baja, ['confianza', 'nitidez'])
  const { concentracion, ...sinNitidez } = G   // eslint-disable-line no-unused-vars
  assert.deepEqual(estadoGrilla(sinNitidez).baja, ['nitidez'], 'sin el dato no se afirma que es nítida')
})

test('con un ajuste negativo no hay líneas ni snap en tiempo negativo', () => {
  // fase = 0,25 − 0,30 = −0,05: la línea j = 0 caería antes del 0.
  assert.deepEqual(lineasGrilla(G, -0.3, -1, 1).map((x) => x.t), [0.41875, 0.8875])
  assert.equal(snapGrilla(0.01, G, -0.3), 0.419, 'el snap no puede dar un tiempo negativo: va a la primera línea real')
})

test('las líneas caen en primer_beat + j·período, dentro de la ventana', () => {
  const l = lineasGrilla(G, 0, 0, 2)
  assert.deepEqual(l.map((x) => x.t), [0.25, 0.71875, 1.1875, 1.65625])
  assert.ok(l.every((x) => x.beat === null), 'sin compas_ref no se inventa cuál es el 1')
})

test('con compas_ref se numera el beat del compás (el 1 es el de compas_ref)', () => {
  const g = { ...G, compas_ref: 0.71875 }       // el 1 es el segundo beat del tema
  assert.deepEqual(lineasGrilla(g, 0, 0, 2.2).map((x) => x.beat), [4, 1, 2, 3, 4])
  // compás y beat del cursor: los compases se cuentan desde el primer 1 del tema
  assert.deepEqual(compasDe(0.72, g, 0), { compas: 1, beat: 1 })
  assert.deepEqual(compasDe(0.71875 + 5 * 0.46875 + 0.01, g, 0), { compas: 2, beat: 2 })
  assert.equal(compasDe(0.72, G, 0), null, 'sin compas_ref no hay compás')
  // Antes del primer 1 (la anacrusa) hay beat pero no compás; antes de la primera línea, nada.
  assert.deepEqual(compasDe(0.26, g, 0), { compas: null, beat: 4 })
  assert.equal(compasDe(0.1, g, 0), null)
})

test('correr medio beat corre todas las líneas medio período (el contratiempo)', () => {
  const off = moverAjuste(0, 0.46875 / 2, 0.46875)
  assert.equal(off, 0.234375)
  assert.deepEqual(lineasGrilla(G, off, 0, 1.5).map((x) => x.t), [0.015625, 0.484375, 0.953125, 1.421875])
  assert.equal(textoAjuste(off, 0.46875), '+½ beat')
  assert.equal(textoAjuste(moverAjuste(off, -0.005, 0.46875), 0.46875), '+½ beat −5 ms')
  assert.equal(textoAjuste(0, 0.46875), 'sin ajuste')
  assert.equal(textoAjuste(-0.46875, 0.46875), '−1 beat')
})

test('el ajuste no se va más de 4 beats', () => {
  assert.equal(moverAjuste(1.8, 0.2, 0.46875), 1.875)
  assert.equal(moverAjuste(-1.8, -0.2, 0.46875), -1.875)
})

test('snap: a la línea más cercana, con el ajuste', () => {
  assert.equal(snapGrilla(0.9, G, 0), 0.719)        // 0.71875 redondeado a ms
  assert.equal(snapGrilla(1.0, G, 0), 1.188)
  assert.equal(snapGrilla(0.9, G, 0.234375), 0.953)
  assert.equal(snapGrilla(0.01, G, 0), 0.25, 'antes del primer beat no queda en un tiempo negativo')
})

test('el período de un beat: el de la grilla, si no 60/BPM, si no nada', () => {
  assert.equal(periodoBeat(G, 127), 0.46875)
  assert.equal(periodoBeat({ ...G, primer_beat_s: null }, 120), 0.5)
  assert.equal(periodoBeat(null, null), null)
  assert.equal(periodoBeat(null, 0), null, 'BPM 0 = no se midió')
})

/* ---------- ajuste guardado en el navegador ---------- */

function storageFalso() {
  const m = new Map()
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), m }
}

test('el ajuste se guarda por tema y vuelve igual; el de fábrica no ocupa lugar', () => {
  const s = storageFalso()
  assert.deepEqual(leerAjuste(s, 'abc'), { offset: 0, oculta: false })
  assert.equal(guardarAjuste(s, 'abc', { offset: 0.234375, oculta: true }), true)
  assert.deepEqual(leerAjuste(s, 'abc'), { offset: 0.234375, oculta: true })
  assert.deepEqual(leerAjuste(s, 'otro'), { offset: 0, oculta: false })
  guardarAjuste(s, 'abc', { offset: 0, oculta: false })
  assert.equal(s.m.size, 0)
})

test('un storage roto o con basura no rompe: vuelve el de fábrica', () => {
  const roto = { getItem() { throw new Error('bloqueado') }, setItem() { throw new Error('bloqueado') } }
  assert.deepEqual(leerAjuste(roto, 'x'), { offset: 0, oculta: false })
  assert.equal(guardarAjuste(roto, 'x', { offset: 1, oculta: false }), false)
  const s = storageFalso()
  s.setItem('musiflix.editorD.grilla.x', '{no es json')
  assert.deepEqual(leerAjuste(s, 'x'), { offset: 0, oculta: false })
})

/* ---------- zoom, ventana y pixeles ---------- */

test('zoom → segundos: compases × 4 × período con grilla; segundos sin ella', () => {
  assert.deepEqual([0, 1, 2, 3, 4].map((i) => anchoZoom(i, G)), [3.75, 7.5, 15, 30, 60])
  assert.deepEqual([0, 1, 2, 3, 4].map((i) => anchoZoom(i, null)), [4, 8, 15, 30, 60])
  assert.equal(anchoZoom(9, G), 60, 'un índice fuera de rango se acota')
})

test('la ventana se centra y no se sale del tema', () => {
  assert.deepEqual(ventana(30, 15, 240), { desde: 22.5, hasta: 37.5 })
  assert.deepEqual(ventana(2, 15, 240), { desde: 0, hasta: 15 })
  assert.deepEqual(ventana(239, 15, 240), { desde: 225, hasta: 240 })
  assert.deepEqual(ventana(5, 60, 48), { desde: 0, hasta: 48 }, 'más ancho que el tema: el tema entero')
})

test('tiempo ↔ pixel', () => {
  const v = { desde: 10, hasta: 25 }
  assert.equal(xDeTiempo(10, v, 1500), 0)
  assert.equal(xDeTiempo(17.5, v, 1500), 750)
  assert.equal(xDeTiempo(25, v, 1500), 1500)
  assert.equal(tiempoDeXV(300, v, 1500), 13)
  assert.ok(Math.abs(tiempoDeXV(xDeTiempo(21.2, v, 1500), v, 1500) - 21.2) < 1e-9, 'ida y vuelta')
})

test('el pedido a /onda3: una ventana más de cada lado, acotado al audio, puntos según la pantalla', () => {
  assert.deepEqual(tramoPedido({ desde: 30, hasta: 45 }, 240, 1000), { desde: 15, hasta: 60, puntos: 3000 })
  assert.deepEqual(tramoPedido({ desde: 0, hasta: 15 }, 48, 1000), { desde: 0, hasta: 30, puntos: 2000 })
  assert.deepEqual(tramoPedido({ desde: 0, hasta: 60 }, 600, 2000), { desde: 0, hasta: 120, puntos: 4000 }, 'el backend acepta hasta 4000')
})

test('alcanza: lo traído tiene que cubrir la ventana con resolución suficiente', () => {
  const d = { desde: 15, hasta: 60, puntos: 3000 }
  assert.equal(alcanza(d, { desde: 30, hasta: 45 }, 1000, 240), true)
  assert.equal(alcanza(d, { desde: 50, hasta: 65 }, 1000, 240), false, 'se sale por la derecha')
  assert.equal(alcanza({ desde: 0, hasta: 240, puntos: 1000 }, { desde: 30, hasta: 33.75 }, 1400, 240), false, 'zoom de 2 compases con la vista general: poca resolución')
  assert.equal(alcanza({ desde: 0, hasta: 4, puntos: 400 }, { desde: 0, hasta: 3.75 }, 1400, 4), true, 'todos los cuadros de 10 ms que hay: el backend no da más')
})

test('alcanza: el borde se tolera un cuadro de 10 ms, no más', () => {
  // El backend devuelve los bordes de sus cuadros de 10 ms: 15,005 en vez de 15 está bien;
  // 15,02 (dos cuadros) ya deja sin datos el borde izquierdo de la ventana.
  const v = { desde: 15, hasta: 30 }
  assert.equal(alcanza({ desde: 15.005, hasta: 45, puntos: 3000 }, v, 1000, 240), true)
  assert.equal(alcanza({ desde: 15.02, hasta: 45, puntos: 3000 }, v, 1000, 240), false)
  assert.equal(alcanza({ desde: 0, hasta: 29.995, puntos: 3000 }, v, 1000, 240), true)
  assert.equal(alcanza({ desde: 0, hasta: 29.98, puntos: 3000 }, v, 1000, 240), false)
})

test('columnas: el máximo de cada banda por columna; fuera del audio, nada', () => {
  const d = { desde: 0, hasta: 4, bandas: { graves: [1, 0, 0, 0.2], medios: [0, 0.5, 0, 0], agudos: [0, 0, 0.9, 0] } }
  assert.deepEqual(columnas(d, { desde: 0, hasta: 4 }, 2), [
    { graves: 1, medios: 0.5, agudos: 0 }, { graves: 0.2, medios: 0, agudos: 0.9 }])
  // Menos puntos que columnas: cada punto ocupa sus columnas (no se interpola).
  assert.deepEqual(columnas(d, { desde: 0, hasta: 2 }, 4).map((c) => c.graves), [1, 1, 0, 0])
  // Ventana más allá del audio: esas columnas no tienen datos.
  assert.deepEqual(columnas(d, { desde: 2, hasta: 6 }, 4).map((c) => c && c.agudos), [0.9, 0, null, null])
})

/* ---------- deshacer / rehacer ---------- */

const M1 = { id: 7, tipo: 'cue', num: 0, inicio: 1.5, fin: null, nombre: 'Drop' }

test('inversas: crear ↔ borrar, cambiar da vuelta antes/después, un grupo se invierte al revés', () => {
  assert.deepEqual(inversa({ tipo: 'crear', marca: M1 }), { tipo: 'borrar', marca: M1 })
  assert.deepEqual(inversa({ tipo: 'cambiar', id: 7, antes: { inicio: 1.5 }, despues: { inicio: 2 } }),
    { tipo: 'cambiar', id: 7, antes: { inicio: 2 }, despues: { inicio: 1.5 } })
  const nueva = { ...M1, id: 9, tipo: 'memory', num: null }
  assert.deepEqual(inversa({ tipo: 'grupo', ops: [{ tipo: 'crear', marca: nueva }, { tipo: 'borrar', marca: M1 }] }),
    { tipo: 'grupo', ops: [{ tipo: 'crear', marca: M1 }, { tipo: 'borrar', marca: nueva }] })
})

test('deshacer y rehacer recorren el historial; registrar algo nuevo borra el futuro', () => {
  let h = historialVacio()
  assert.equal(deshacer(h), null)
  h = registrar(h, { tipo: 'crear', marca: M1 })
  h = registrar(h, { tipo: 'cambiar', id: 7, antes: { nombre: 'Drop' }, despues: { nombre: 'Break' } })
  const d1 = deshacer(h)
  assert.deepEqual(d1.aplicar, { tipo: 'cambiar', id: 7, antes: { nombre: 'Break' }, despues: { nombre: 'Drop' } })
  const d2 = deshacer(d1.h)
  assert.deepEqual(d2.aplicar, { tipo: 'borrar', marca: M1 })
  assert.equal(d2.h.pasado.length, 0)
  const r1 = rehacer(d2.h)
  assert.deepEqual(r1.aplicar, { tipo: 'crear', marca: M1 })
  const nuevo = registrar(r1.h, { tipo: 'borrar', marca: M1 })
  assert.equal(rehacer(nuevo), null, 'después de una edición nueva no hay nada para rehacer')
})

test('recrear una marca le da otro id: el historial entero pasa a usar el nuevo', () => {
  let h = registrar(historialVacio(), { tipo: 'crear', marca: M1 })
  h = registrar(h, { tipo: 'cambiar', id: 7, antes: { inicio: 1.5 }, despues: { inicio: 2 } })
  h = registrar(h, { tipo: 'borrar', marca: { ...M1, inicio: 2 } })
  const d = deshacer(h)                          // deshacer el borrado = recrearla (id nuevo 12)
  const h2 = reemplazarId(d.h, 7, 12)
  assert.deepEqual(h2.pasado.map((o) => (o.marca ? o.marca.id : o.id)), [12, 12])
  assert.equal(h2.futuro[0].marca.id, 12)
  assert.deepEqual(deshacer(h2).aplicar, { tipo: 'cambiar', id: 12, antes: { inicio: 2 }, despues: { inicio: 1.5 } })
})

test('el historial guarda los últimos 200 pasos: el más viejo se cae', () => {
  let h = historialVacio()
  for (let i = 0; i < 205; i++) h = registrar(h, { tipo: 'cambiar', id: 1, antes: { inicio: i }, despues: { inicio: i + 1 } })
  assert.equal(h.pasado.length, 200)
  assert.deepEqual(h.pasado[0].antes, { inicio: 5 }, 'se cayeron los 5 primeros, no los últimos')
  assert.deepEqual(h.pasado[199].antes, { inicio: 204 })
})

test('un paso que el servidor rechazó sale del historial (no se repite con el próximo Ctrl+Z)', () => {
  let h = registrar(historialVacio(), { tipo: 'crear', marca: M1 })
  h = registrar(h, { tipo: 'cambiar', id: 7, antes: { inicio: 1.5 }, despues: { inicio: 2 } })
  const d = deshacer(h)                        // el cambio: la marca ya no existe (404)
  const h2 = descartarPaso(d.h, -1)
  assert.deepEqual(h2, { pasado: [{ tipo: 'crear', marca: M1 }], futuro: [] })
  assert.deepEqual(deshacer(h2).aplicar, { tipo: 'borrar', marca: M1 }, 'el próximo Ctrl+Z es el paso anterior, no el rechazado')
  const r = rehacer(d.h)                       // rehacer rechazado: sale del pasado
  assert.deepEqual(descartarPaso(r.h, 1), { pasado: [{ tipo: 'crear', marca: M1 }], futuro: [] })
})

test('un grupo rechazado a medias: lo que se guardó queda en el historial como un paso propio, con el id nuevo', () => {
  // Cambiar el tipo de M1 (cue id 7) a memory: se creó la memory 9 y se borró M1.
  const nueva = { ...M1, id: 9, tipo: 'memory', num: null }
  const h = registrar(historialVacio(), { tipo: 'grupo', ops: [{ tipo: 'crear', marca: nueva }, { tipo: 'borrar', marca: M1 }] })
  // Deshacer = grupo [crear M1, borrar 9]. Crear M1 sale (vuelve con id 12) y borrar la 9 se
  // rechaza (otra pestaña la borró): la M1 recreada YA está en el servidor.
  const d = deshacer(h)
  const h2 = descartarPaso(d.h, -1, [{ tipo: 'crear', marca: M1 }], [[7, 12]])
  assert.deepEqual(h2, { pasado: [{ tipo: 'grupo', ops: [{ tipo: 'crear', marca: { ...M1, id: 12 } }] }], futuro: [] })
  assert.deepEqual(deshacer(h2).aplicar, { tipo: 'grupo', ops: [{ tipo: 'borrar', marca: { ...M1, id: 12 } }] },
    'el próximo Ctrl+Z saca la marca que quedó guardada (por su id nuevo)')
  // Rehacer a medias: se creó la memory (id 15) y el borrado de M1 se rechazó.
  const r = rehacer(d.h)
  const h3 = descartarPaso(r.h, 1, [{ tipo: 'crear', marca: nueva }], [[9, 15]])
  assert.deepEqual(h3, { pasado: [{ tipo: 'grupo', ops: [{ tipo: 'crear', marca: { ...nueva, id: 15 } }] }], futuro: [] })
  // Sin nada guardado, como antes: el paso sale entero.
  assert.deepEqual(descartarPaso(d.h, -1, [], []), { pasado: [], futuro: [] })
})

test('reemplazarId llega a las marcas de un paso «grupo»', () => {
  const nueva = { ...M1, id: 9, tipo: 'memory', num: null }
  const h = { pasado: [{ tipo: 'grupo', ops: [{ tipo: 'crear', marca: nueva }, { tipo: 'borrar', marca: M1 }] }],
    futuro: [{ tipo: 'grupo', ops: [{ tipo: 'cambiar', id: 7, antes: { inicio: 1 }, despues: { inicio: 2 } }] }] }
  const h2 = reemplazarId(h, 7, 12)
  assert.deepEqual(h2.pasado[0].ops.map((o) => o.marca.id), [9, 12])
  assert.equal(h2.futuro[0].ops[0].id, 12)
})

test('temaVigente: una acción o una respuesta solo vale para el tema abierto y con sus datos cargados', () => {
  assert.equal(temaVigente('a1', 'a1', true), true)
  assert.equal(temaVigente('a1', 'a1'), true, 'sin decir si está cargado (una respuesta): alcanza con que sea el mismo tema')
  assert.equal(temaVigente('a1', 'b2', true), false, 'la respuesta de otro tema no toca el actual')
  assert.equal(temaVigente('b2', 'b2', null), false, 'recién pasado de tema, sin los datos del nuevo: lo que hay es del anterior')
  assert.equal(temaVigente('b2', 'b2', false), false)
  assert.equal(temaVigente(null, null, true), false)
})

test('beatsLoop: con el MISMO período con que se crea el loop (el de la grilla)', () => {
  // Grilla afinada a 0,5 s (120 BPM) con el BPM de la base en 128: un loop de 4 beats de la
  // grilla dura 2 s y es «4», no 4,3 (2 / 0,46875).
  assert.equal(beatsLoop(10, 12, 0.5), 4)
  // Con el fin redondeado al ms como lo guarda el editor: 16 beats de 0,468912 s.
  assert.equal(beatsLoop(3, Math.round((3 + 16 * 0.468912) * 1000) / 1000, 0.468912), 16)
  // Sin grilla el período es 60/BPM (`periodoBeat`): 2 s a 120 BPM = 4 beats.
  assert.equal(beatsLoop(0, 2, periodoBeat(null, 120)), 4)
  assert.equal(beatsLoop(0, 2, null), null)
  assert.equal(beatsLoop(2, 2, 0.5), null)
})

test('cuerpoCrear: lo que el POST acepta, sin id ni fechas', () => {
  assert.deepEqual(cuerpoCrear({ ...M1, creada: 'x' }), { tipo: 'cue', inicio: 1.5, num: 0, nombre: 'Drop' })
  assert.deepEqual(cuerpoCrear({ id: 3, tipo: 'loop', num: null, inicio: 4, fin: 6, nombre: null }), { tipo: 'loop', inicio: 4, fin: 6 })
  assert.deepEqual(cuerpoCrear({ id: 3, tipo: 'memory', num: null, inicio: 2, fin: null, nombre: '' }), { tipo: 'memory', inicio: 2 })
  // Un hot loop (loop en un pad) se recrea EN SU PAD.
  assert.deepEqual(cuerpoCrear({ id: 4, tipo: 'loop', num: 2, inicio: 4, fin: 6, nombre: null }), { tipo: 'loop', inicio: 4, fin: 6, num: 2 })
})

test('diferencia: solo lo que cambió, para el PATCH y su inversa', () => {
  assert.deepEqual(diferencia(M1, { ...M1, inicio: 2, nombre: 'Drop' }), { antes: { inicio: 1.5 }, despues: { inicio: 2 } })
  assert.deepEqual(diferencia({ ...M1, tipo: 'loop', fin: 3, num: 2 }, { ...M1, tipo: 'loop', fin: 3, num: null }),
    { antes: { num: 2 }, despues: { num: null } })
})

/* ---------- atajos ---------- */

const tecla = (key, extra = {}) => ({ key, code: '', target: { tagName: 'DIV', getAttribute: () => 'slider' }, ...extra })

test('atajos: pads por código (Mayús + número borra), flechas y Mayús, marcas, zoom, grilla', () => {
  assert.deepEqual(atajoD(tecla('3', { code: 'Digit3' })), { tipo: 'pad', num: 2 })
  assert.deepEqual(atajoD(tecla('#', { code: 'Digit3', shiftKey: true })), { tipo: 'borrarPad', num: 2 })
  assert.equal(atajoD(tecla('9', { code: 'Digit9' })), null)
  assert.deepEqual(atajoD(tecla('ArrowLeft')), { tipo: 'beat', dir: -1 })
  assert.deepEqual(atajoD(tecla('ArrowRight', { shiftKey: true })), { tipo: 'fino', dir: 1 })
  assert.deepEqual(['c', 'M', 'i', 'o', 'l', 'g', 'h', 'q'].map((k) => atajoD(tecla(k)).tipo),
    ['cue', 'memory', 'loopIn', 'loopOut', 'loopBeats', 'grilla', 'medioBeat', 'snap'])
  assert.deepEqual(atajoD(tecla('+')), { tipo: 'zoom', dir: 1 })
  assert.deepEqual(atajoD(tecla('-')), { tipo: 'zoom', dir: -1 })
  assert.deepEqual(atajoD(tecla('PageDown')), { tipo: 'tema', dir: 1 })
  assert.deepEqual(atajoD(tecla('Escape')), { tipo: 'cerrar' })
})

test('atajos: el teclado numérico también fija y borra pads (por code Numpad1..8)', () => {
  assert.deepEqual(atajoD(tecla('3', { code: 'Numpad3' })), { tipo: 'pad', num: 2 })
  assert.deepEqual(atajoD(tecla('8', { code: 'Numpad8' })), { tipo: 'pad', num: 7 })
  // Mayús + teclado numérico: Windows manda la tecla de navegación (Numpad3 → «PageDown»);
  // el pad sale del `code`, no de `key`.
  assert.deepEqual(atajoD(tecla('PageDown', { code: 'Numpad3', shiftKey: true })), { tipo: 'borrarPad', num: 2 })
  assert.equal(atajoD(tecla('9', { code: 'Numpad9' })), null)
})

test('Ctrl+Z deshace, Ctrl+Mayús+Z y Ctrl+Y rehacen; otras combinaciones con Ctrl no son atajos', () => {
  assert.deepEqual(atajoD(tecla('z', { ctrlKey: true })), { tipo: 'deshacer' })
  assert.deepEqual(atajoD(tecla('Z', { ctrlKey: true, shiftKey: true })), { tipo: 'rehacer' })
  assert.deepEqual(atajoD(tecla('y', { metaKey: true })), { tipo: 'rehacer' })
  assert.equal(atajoD(tecla('c', { ctrlKey: true })), null, 'Ctrl+C es copiar, no un cue')
  assert.equal(atajoD(tecla('c', { shiftKey: true })), null)
})

test('escribiendo un nombre o un tiempo ningún atajo actúa (ni Escape, ni Ctrl+Z)', () => {
  const input = { tagName: 'INPUT', type: 'text' }
  for (const e of [tecla('c'), tecla('1', { code: 'Digit1' }), tecla('Escape'), tecla('z', { ctrlKey: true }), tecla(' ')]) {
    assert.equal(atajoD({ ...e, target: input }), null, `${e.key} con el foco en un campo`)
  }
  assert.equal(atajoD({ ...tecla('c'), target: { tagName: 'SELECT' } }), null)
})

test('Espacio reproduce salvo en un botón (ahí activa el botón)', () => {
  assert.deepEqual(atajoD(tecla(' ')), { tipo: 'play' })
  assert.equal(atajoD({ ...tecla(' '), target: { tagName: 'BUTTON', getAttribute: () => null } }), null)
})

/* ---------- textos ---------- */

test('BPM con un decimal solo si se midió; letra del pad; rótulo que no depende del color', () => {
  assert.equal(fmtBpmMedido(128.4), '128.4')
  assert.equal(fmtBpmMedido(128), '128.0')
  assert.equal(fmtBpmMedido(0), null)
  assert.equal(fmtBpmMedido(null), null)
  assert.deepEqual([0, 7, 8, null].map(letraPad), ['A', 'H', null, null])
  assert.equal(rotuloMarca({ tipo: 'cue', num: 7 }), 'H')
  assert.equal(rotuloMarca({ tipo: 'memory', num: null }), 'M')
  assert.equal(rotuloMarca({ tipo: 'loop', num: 2 }), 'Loop C')
  assert.equal(rotuloMarca({ tipo: 'loop', num: null }), 'Loop')
  assert.equal(nombreD({ tipo: 'cue', num: 1, inicio: 3 }), 'hot cue B')
  assert.equal(nombreD({ tipo: 'memory', num: null, inicio: 83.5 }), 'memory cue en 1:23.500')
  assert.equal(nombreD({ tipo: 'loop', num: 4, inicio: 4, fin: 6 }), 'loop del pad E 0:04.000 a 0:06.000')
})
