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
  clasificarFalla, yaHecha, opConId, contarIguales, cerrarPaso,
  marcaCercana, filasBanderas, detectarKicks, alturasBandas,
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

test('cada paso conserva el tema en el que nació (tid), también en la inversa de un grupo', () => {
  assert.deepEqual(inversa({ tipo: 'crear', marca: M1, tid: 'a1' }), { tipo: 'borrar', marca: M1, tid: 'a1' })
  assert.equal(inversa({ tipo: 'cambiar', id: 7, antes: {}, despues: {}, tid: 'a1' }).tid, 'a1')
  const g = inversa({ tipo: 'grupo', ops: [{ tipo: 'crear', marca: M1 }], tid: 'b2' })
  assert.equal(g.tid, 'b2')
  const h = registrar(historialVacio(), { tipo: 'crear', marca: M1, tid: 'a1' })
  assert.equal(deshacer(h).aplicar.tid, 'a1', 'deshacer manda el tema del paso')
  assert.equal(rehacer(deshacer(h).h).aplicar.tid, 'a1')
})

test('clasificarFalla: 400/404 se descartan; 409 (base ocupada), 5xx y sin respuesta se reintentan', () => {
  assert.deepEqual([400, 404, 409, 500, 503, null].map(clasificarFalla), ['rechazo', 'rechazo', 'reintentable', 'reintentable', 'reintentable', 'reintentable'])
})

test('yaHecha: lo que la base ya tiene no se vuelve a mandar (reintentar no duplica)', () => {
  const lista = [{ id: 9, tipo: 'memory', num: null, inicio: 0.204, fin: null }, { id: 3, tipo: 'cue', num: 1, inicio: 2, fin: null, nombre: 'Drop' }]
  // Con ESE id en la lista: hecha.
  assert.deepEqual(yaHecha({ tipo: 'crear', marca: { id: 9, tipo: 'memory', num: null, inicio: 0.204, fin: null } }, lista), { hecha: true, id: 9 })
  // Con otro id: hecha solo si ya están TODAS las idénticas que tenía que haber (la respuesta
  // del POST se perdió pero la marca está).
  assert.deepEqual(yaHecha({ tipo: 'crear', marca: { id: 5, tipo: 'memory', num: null, inicio: 0.204, fin: null }, iguales: 1 }, lista), { hecha: true, id: 9 })
  assert.deepEqual(yaHecha({ tipo: 'crear', marca: { id: 5, tipo: 'memory', num: null, inicio: 0.204, fin: null }, iguales: 2 }, lista), { hecha: false },
    'había dos memory iguales y queda una: falta la otra (no es «ya hecha»)')
  assert.deepEqual(yaHecha({ tipo: 'crear', marca: { id: 5, tipo: 'memory', num: null, inicio: 0.204, fin: null } }, lista), { hecha: false },
    'sin saber cuántas había, una igual con otro id no alcanza')
  assert.deepEqual(yaHecha({ tipo: 'crear', marca: { id: 5, tipo: 'memory', num: null, inicio: 0.205, fin: null }, iguales: 1 }, lista), { hecha: false })
  assert.deepEqual(yaHecha({ tipo: 'crear', marca: { id: 5, tipo: 'cue', num: 2, inicio: 2, fin: null }, iguales: 1 }, lista), { hecha: false }, 'otro pad no es la misma marca')
  assert.deepEqual(yaHecha({ tipo: 'borrar', marca: { id: 3 } }, lista), { hecha: false })
  assert.deepEqual(yaHecha({ tipo: 'borrar', marca: { id: 4 } }, lista), { hecha: true })
  assert.deepEqual(yaHecha({ tipo: 'cambiar', id: 3, antes: { nombre: 'X' }, despues: { nombre: 'Drop' } }, lista), { hecha: true })
  assert.deepEqual(yaHecha({ tipo: 'cambiar', id: 3, antes: { inicio: 2 }, despues: { inicio: 3 } }, lista), { hecha: false })
  assert.deepEqual(yaHecha({ tipo: 'cambiar', id: 77, antes: {}, despues: { inicio: 3 } }, lista), { hecha: false }, 'si no está, que lo diga el servidor (404)')
})

test('dos memory iguales (M M): borrar una y deshacer, o deshacer las dos y rehacer, vuelve a dos', () => {
  const m1 = { id: 1, tipo: 'memory', num: null, inicio: 0.204, fin: null }
  const m2 = { ...m1, id: 2 }
  assert.equal(contarIguales(m1, [m1, m2]), 2)
  assert.equal(contarIguales(m1, [{ ...m1, id: 7, inicio: 0.3 }]), 0)
  // D1: borrar m2 (había 2 iguales) y deshacer: la lista tiene solo m1 → falta crearla.
  assert.deepEqual(yaHecha(inversa({ tipo: 'borrar', marca: m2, iguales: 2 }), [m1]), { hecha: false })
  // D2: crear m1 (1 igual), crear m2 (2 iguales); deshacer las dos; rehacer m1 (id 3), rehacer m2.
  assert.deepEqual(yaHecha({ tipo: 'crear', marca: m1, iguales: 1 }, []), { hecha: false })
  assert.deepEqual(yaHecha({ tipo: 'crear', marca: m2, iguales: 2 }, [{ ...m1, id: 3 }]), { hecha: false },
    'el segundo Ctrl+Y no puede decir «Rehecho.» sin mandar nada')
  // Dos loops sin pad iguales: lo mismo.
  const l = { id: 4, tipo: 'loop', num: null, inicio: 1, fin: 2.875 }
  assert.deepEqual(yaHecha({ tipo: 'crear', marca: { ...l, id: 5 }, iguales: 2 }, [l]), { hecha: false })
  assert.equal(inversa({ tipo: 'crear', marca: m1, iguales: 3 }).iguales, 3, 'la inversa conserva cuántas iguales había')
})

test('cerrarPaso trabaja sobre el historial ACTUAL: lo registrado mientras viajaba el pedido no se pierde', () => {
  const cue = { tipo: 'crear', marca: { id: 1 } }
  const mem = { tipo: 'crear', marca: { id: 2 } }
  const alEmpezar = registrar(historialVacio(), cue)
  const ahora = registrar(alEmpezar, mem)          // M mientras el Ctrl+Z del cue viajaba
  // Falla reintentable: nada cambia (la memory sigue arriba para deshacer).
  assert.deepEqual(cerrarPaso(ahora, cue, -1, 'reintentable'), ahora)
  // Sale bien: el cue sale del pasado; como hubo algo nuevo, no va al futuro; la memory queda.
  assert.deepEqual(cerrarPaso(ahora, cue, -1, 'ok'), { pasado: [mem], futuro: [] })
  // Sin nada nuevo: como siempre (pasa al futuro).
  assert.deepEqual(cerrarPaso(alEmpezar, cue, -1, 'ok'), { pasado: [], futuro: [cue] })
  // Rechazo: sale y nada más.
  assert.deepEqual(cerrarPaso(ahora, cue, -1, 'rechazo'), { pasado: [mem], futuro: [] })
  // Rehacer: del futuro al final del pasado; rechazado, sale del futuro.
  const conFuturo = { pasado: [mem], futuro: [cue] }
  assert.deepEqual(cerrarPaso(conFuturo, cue, 1, 'ok'), { pasado: [mem, cue], futuro: [] })
  assert.deepEqual(cerrarPaso(conFuturo, cue, 1, 'rechazo'), { pasado: [mem], futuro: [] })
})

test('opConId cambia el id de marca en cualquier operación, también adentro de un grupo', () => {
  const g = { tipo: 'grupo', ops: [{ tipo: 'crear', marca: { id: 7 } }, { tipo: 'cambiar', id: 7 }, { tipo: 'borrar', marca: { id: 8 } }] }
  assert.deepEqual(opConId(g, 7, 12).ops.map((o) => (o.marca ? o.marca.id : o.id)), [12, 12, 8])
})

test('compasDe con compas_ref varios compases después del primero cuenta desde el PRIMER 1 del tema', () => {
  // El motor puede dar como referencia un 1 del tercer compás: el compás 1 es el del primer beat.
  const g = { ...G, compas_ref: 0.25 + 8 * 0.46875 }
  assert.deepEqual(compasDe(0.26, g, 0), { compas: 1, beat: 1 })
  assert.deepEqual(compasDe(0.25 + 9 * 0.46875 + 0.01, g, 0), { compas: 3, beat: 2 })
})

test('cuerpoCrear: lo que el POST acepta, sin id ni fechas', () => {
  assert.deepEqual(cuerpoCrear({ ...M1, creada: 'x' }), { tipo: 'cue', inicio: 1.5, num: 0, nombre: 'Drop' })
  assert.deepEqual(cuerpoCrear({ id: 3, tipo: 'loop', num: null, inicio: 4, fin: 6, nombre: null }), { tipo: 'loop', inicio: 4, fin: 6 })
  assert.deepEqual(cuerpoCrear({ id: 3, tipo: 'memory', num: null, inicio: 2, fin: null, nombre: '' }), { tipo: 'memory', inicio: 2 })
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

/* ---------- pedidos del dueño (f54): duplicados, banderas, kicks, colores ---------- */

test('marcaCercana: ±50 ms del mismo tipo; un loop tiene que coincidir en entrada y salida', () => {
  const ms = [{ id: 1, tipo: 'cue', num: 0, inicio: 75.439 }, { id: 2, tipo: 'memory', inicio: 10 }, { id: 3, tipo: 'loop', inicio: 5, fin: 6.875 }]
  assert.equal(marcaCercana(ms, 'cue', 75.48)?.id, 1, 'a 41 ms ya hay un cue')
  assert.equal(marcaCercana(ms, 'cue', 75.49), null, 'a 51 ms no')
  assert.equal(marcaCercana(ms, 'cue', 10), null, 'una memory no es un cue')
  assert.equal(marcaCercana(ms, 'memory', 9.96)?.id, 2)
  assert.equal(marcaCercana(ms, 'loop', 5.02, 6.9)?.id, 3)
  assert.equal(marcaCercana(ms, 'loop', 5, 8.75), null, 'mismo in, otro out: es otro loop')
})

test('filasBanderas: no se pisan; por prioridad; si no entran, versión corta u oculta', () => {
  // El caso de la captura del dueño: un hot cue «Primer cue» y un loop en el MISMO punto.
  const r = filasBanderas([
    { id: 'loop', x: 100, ancho: 50, anchoMin: 40, prioridad: 1 },
    { id: 'cue', x: 100, ancho: 90, anchoMin: 20, prioridad: 0 },
  ])
  assert.deepEqual(r, { cue: { fila: 0, corta: false }, loop: { fila: 1, corta: false } }, 'el hot cue arriba, el loop en la fila de abajo')
  // Tres en el mismo punto con 2 filas: el tercero (memory) no entra ni corto.
  const r2 = filasBanderas([
    { id: 'a', x: 10, ancho: 30, anchoMin: 20, prioridad: 0 },
    { id: 'b', x: 10, ancho: 30, anchoMin: 20, prioridad: 0 },
    { id: 'm', x: 12, ancho: 30, anchoMin: 20, prioridad: 2 },
  ])
  assert.deepEqual(r2, { a: { fila: 0, corta: false }, b: { fila: 1, corta: false }, m: null })
  // Entra solo corto: al lado de otra, sin tocarla.
  const r3 = filasBanderas([
    { id: 'x', x: 0, ancho: 60, anchoMin: 20, prioridad: 0 },
    { id: 'y', x: 70, ancho: 60, anchoMin: 20, prioridad: 0 },
    { id: 'z', x: 30, ancho: 60, anchoMin: 30, prioridad: 2 },
  ], 1)
  assert.deepEqual(r3, { x: { fila: 0, corta: false }, y: { fila: 0, corta: false }, z: null })
  // Colgada a la izquierda (cerca del borde derecho): ocupa [x − ancho, x].
  const r4 = filasBanderas([
    { id: 'p', x: 500, ancho: 60, anchoMin: 20, prioridad: 0, der: true },
    { id: 'q', x: 445, ancho: 30, anchoMin: 20, prioridad: 1 },
  ], 1)
  assert.deepEqual(r4, { p: { fila: 0, corta: false }, q: null }, 'q (445..475) pisa a p (440..500)')
})

test('detectarKicks: las subidas bruscas de los graves, y nada con un bajo sostenido o poca resolución', () => {
  // 100 puntos por segundo; kicks a 0,25 s y cada 0,5 s (caída exponencial), como el generador.
  const n = 300
  const graves = Array.from({ length: n }, (_, i) => {
    const t = i / 100
    const k = (t - 0.25) % 0.5
    return t >= 0.25 && k < 0.15 ? Math.exp(-k * 20) : 0.02
  })
  const d = { desde: 0, hasta: 3, bandas: { graves } }
  assert.deepEqual(detectarKicks(d, 0, 3), [0.25, 0.75, 1.25, 1.75, 2.25, 2.75])
  assert.deepEqual(detectarKicks(d, 1, 2), [1.25, 1.75], 'solo los de la ventana')
  // Un bajo sostenido (alto pero sin subidas): ningún kick.
  assert.deepEqual(detectarKicks({ desde: 0, hasta: 3, bandas: { graves: Array(n).fill(0.85) } }, 0, 3), [])
  // Con 20 puntos por segundo no se distingue un golpe: nada (no se inventa).
  assert.deepEqual(detectarKicks({ desde: 0, hasta: 3, bandas: { graves: graves.filter((_, i) => i % 5 === 0) } }, 0, 3), [])
})

test('alturasBandas: el kick sobresale del bajo sostenido (curva en los graves), anidadas', () => {
  const kick = alturasBandas({ graves: 1, medios: 0.3, agudos: 0.2 })
  const bajo = alturasBandas({ graves: 0.8, medios: 0.3, agudos: 0.2 })
  assert.equal(kick.graves, 1)
  assert.ok(Math.abs(bajo.graves - 0.64) < 1e-9)
  assert.ok(kick.graves / bajo.graves >= 1.5, 'la diferencia kick/bajo tiene que crecer (1,25 → ≥1,5)')
  const x = alturasBandas({ graves: 0.5, medios: 0.9, agudos: 0.4 })
  assert.ok(x.agudos >= x.medios && x.medios >= x.graves, 'afuera la más alta, adentro los graves')
})