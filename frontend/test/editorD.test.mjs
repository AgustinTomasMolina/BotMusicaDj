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
  marcaCercana, filasBanderas, detectarGolpesGraves, alturaBanda, agruparIguales, repetidas, grillaAnclada, rescatarHechas, marcaPropia, etiquetaQuitar, avisoQuitado, avisoNoQuitado,
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

test('a medias sin duplicar: la marca recreada que ya crea un paso del pasado no se agrega otra vez', () => {
  // M (memory 9) creada en el editor; después pasada a hot cue (cue 11 + borrar la 9).
  const mem = { id: 9, tipo: 'memory', num: null, inicio: 2, fin: null, nombre: null }
  const cue = { id: 11, tipo: 'cue', num: 0, inicio: 2, fin: null, nombre: null }
  let h = registrar(historialVacio(), { tipo: 'crear', marca: mem })
  h = registrar(h, { tipo: 'grupo', ops: [{ tipo: 'crear', marca: cue }, { tipo: 'borrar', marca: mem }] })
  // Ctrl+Z del cambio de tipo: la memory vuelve (id 15) y borrar el cue da 404 (otra pestaña).
  const d = deshacer(h)
  const h2 = descartarPaso(d.h, -1, [{ tipo: 'crear', marca: mem }], [[9, 15]])
  assert.deepEqual(h2, { pasado: [{ tipo: 'crear', marca: { ...mem, id: 15 } }], futuro: [] },
    'la creación de la memory queda UNA vez (la original, con el id nuevo), no crear(15) | grupo[crear(15)]')
  // Dos Ctrl+Z seguidos: el primero borra la 15 (existe), el segundo no tiene nada más.
  const z1 = deshacer(h2)
  assert.deepEqual(z1.aplicar, { tipo: 'borrar', marca: { ...mem, id: 15 } })
  assert.equal(deshacer(z1.h), null, 'un segundo Ctrl+Z no repite el borrado de la 15 (daría 404)')
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

/* ---------- pedidos del dueño y crítica de diseño (f54) ---------- */

test('marcaCercana: ±50 ms del mismo tipo; un loop tiene que coincidir en entrada y salida', () => {
  const ms = [{ id: 1, tipo: 'cue', num: 0, inicio: 75.439 }, { id: 2, tipo: 'memory', inicio: 10 }, { id: 3, tipo: 'loop', inicio: 5, fin: 6.875 }]
  assert.equal(marcaCercana(ms, 'cue', 75.48)?.id, 1, 'a 41 ms ya hay un cue')
  assert.equal(marcaCercana(ms, 'cue', 75.49), null, 'a 51 ms no')
  assert.equal(marcaCercana(ms, 'cue', 10), null, 'una memory no es un cue')
  assert.equal(marcaCercana(ms, 'memory', 9.96)?.id, 2)
  assert.equal(marcaCercana(ms, 'loop', 5.02, 6.9)?.id, 3)
  assert.equal(marcaCercana(ms, 'loop', 5, 8.75), null, 'mismo in, otro out: es otro loop')
})

test('filasBanderas: 2 carriles por prioridad; corta; si no entra, «+n» en la que la tapa', () => {
  // El caso de la captura del dueño: un hot cue «Primer cue» y un loop en el MISMO punto.
  const r = filasBanderas([
    { id: 'loop', x: 100, ancho: 50, anchoMin: 40, prioridad: 1, carril: 1 },
    { id: 'cue', x: 100, ancho: 90, anchoMin: 20, prioridad: 0, carril: 0 },
  ])
  assert.deepEqual(r, { cue: { fila: 0, corta: false, adentro: [] }, loop: { fila: 1, corta: false, adentro: [] } }, 'el hot cue arriba, el loop en el carril de abajo')
  // Tres en el mismo punto con 2 carriles: la memory no entra y se suma a la que la tapa.
  const r2 = filasBanderas([
    { id: 'a', x: 10, ancho: 30, anchoMin: 20, prioridad: 0 },
    { id: 'b', x: 10, ancho: 30, anchoMin: 20, prioridad: 0 },
    { id: 'm', x: 12, ancho: 30, anchoMin: 20, prioridad: 2, carril: 1 },
  ])
  assert.deepEqual(r2, { a: { fila: 0, corta: false, adentro: ['m'] }, b: { fila: 1, corta: false, adentro: [] }, m: { tapadaPor: 'a' } })
  // Entra solo corta, al lado de otra, sin tocarla.
  const r3 = filasBanderas([
    { id: 'x', x: 0, ancho: 60, anchoMin: 20, prioridad: 0 },
    { id: 'z', x: 64, ancho: 60, anchoMin: 20, prioridad: 2 },
    { id: 'y', x: 90, ancho: 30, anchoMin: 20, prioridad: 0 },
  ], 1)
  assert.deepEqual(r3.z, { fila: 0, corta: true, adentro: [] }, 'z entera (64..124) pisa a y (90..120); corta (64..84) entra entre x y y')
  // Colgada a la izquierda (cerca del borde derecho): ocupa [x − ancho, x].
  const r4 = filasBanderas([
    { id: 'p', x: 500, ancho: 60, anchoMin: 20, prioridad: 0, der: true },
    { id: 'q', x: 445, ancho: 30, anchoMin: 20, prioridad: 1 },
  ], 1)
  assert.deepEqual(r4, { p: { fila: 0, corta: false, adentro: ['q'] }, q: { tapadaPor: 'p' } }, 'q (445..465) pisa a p (440..500)')
})

test('agruparIguales: los 32 loops idénticos del dueño son UN grupo con n = 32 (un relleno, una bandera)', () => {
  const loops = Array.from({ length: 32 }, (_, i) => ({ id: 6 + i, tipo: 'loop', num: null, inicio: 59.576, fin: 61.231 }))
  const otro = { id: 50, tipo: 'loop', num: null, inicio: 59.576, fin: 63.0 }
  const g = agruparIguales([...loops, otro, { id: 2, tipo: 'cue', num: 0, inicio: 1 }])
  assert.deepEqual(g.map((x) => [x.marca.id, x.n]), [[2, 1], [6, 32], [50, 1]], 'en orden de tiempo, el de id más bajo representa al grupo')
  // Hot cues en el mismo tiempo pero en pads distintos son marcas distintas.
  assert.equal(agruparIguales([{ id: 1, tipo: 'cue', num: 2, inicio: 75.439 }, { id: 2, tipo: 'cue', num: 3, inicio: 75.439 }]).length, 2)
})

test('repetidas: lo que ofrece «Borrar repetidos» (queda la más vieja de cada grupo)', () => {
  const ms = [
    ...Array.from({ length: 32 }, (_, i) => ({ id: 6 + i, tipo: 'loop', num: null, inicio: 59.576, fin: 61.231 })),
    ...[41, 42, 43, 44, 45, 46].map((id, k) => ({ id, tipo: 'cue', num: 2 + k, inicio: 75.439 })),
    { id: 1, tipo: 'cue', num: 0, inicio: 59.576, nombre: 'Primer cue' },
    { id: 60, tipo: 'memory', inicio: 3 },
  ]
  const r = repetidas(ms)
  assert.deepEqual(r.grupos.map((x) => [x.tipo, x.queda.id, x.sobran.length]), [['loop', 6, 31], ['cue', 41, 5]])
  assert.equal(r.aBorrar.length, 36)
  assert.ok(!r.aBorrar.some((m) => m.id === 1 || m.id === 6 || m.id === 41 || m.id === 60), 'nunca la que queda, ni las que no se repiten')
  assert.deepEqual(repetidas([{ id: 1, tipo: 'memory', inicio: 3 }, { id: 2, tipo: 'memory', inicio: 3.06 }]).aBorrar, [], 'a 60 ms ya no es repetida')
})

test('detectarGolpesGraves: subidas bruscas de los graves; nada con un bajo sostenido o poca resolución', () => {
  // 100 puntos por segundo; golpes a 0,25 s y cada 0,5 s (caída exponencial), como el generador.
  const n = 300
  const graves = Array.from({ length: n }, (_, i) => {
    const t = i / 100
    const k = (t - 0.25) % 0.5
    return t >= 0.25 && k < 0.15 ? Math.exp(-k * 20) : 0.02
  })
  const d = { desde: 0, hasta: 3, bandas: { graves } }
  assert.deepEqual(detectarGolpesGraves(d, 0, 3), [0.25, 0.75, 1.25, 1.75, 2.25, 2.75])
  assert.deepEqual(detectarGolpesGraves(d, 1, 2), [1.25, 1.75], 'solo los de la ventana')
  assert.deepEqual(detectarGolpesGraves({ desde: 0, hasta: 3, bandas: { graves: Array(n).fill(0.85) } }, 0, 3), [], 'un bajo sostenido no da golpes')
  assert.deepEqual(detectarGolpesGraves({ desde: 0, hasta: 3, bandas: { graves: graves.filter((_, i) => i % 5 === 0) } }, 0, 3), [], 'con 20 puntos por segundo no se distingue un golpe')
})

test('alturaBanda: graves² (el bajo de fondo baja, el kick queda) y medios y agudos adentro', () => {
  assert.ok(Math.abs(alturaBanda(0.45, 'graves') - 0.2025) < 1e-9, 'el bajo de 0,45 baja a 0,20')
  assert.ok(Math.abs(alturaBanda(0.9, 'graves') - 0.81) < 1e-9, 'el kick de 0,9 queda en 0,81')
  assert.ok(alturaBanda(0.9, 'graves') / alturaBanda(0.45, 'graves') >= 4, 'la diferencia kick/bajo pasa de 2× a 4×')
  assert.ok(Math.abs(alturaBanda(1, 'medios') - 0.8) < 1e-9 && Math.abs(alturaBanda(1, 'agudos') - 0.5) < 1e-9, 'medios y agudos adentro de la silueta de graves')
  assert.equal(alturaBanda(1.4, 'graves'), 1, 'acotada')
})

test('grillaAnclada (K): el kick del dueño es el 1 y el período es el medido; nada sin ancla o sin período', () => {
  const sin = { periodo_s: 0.413671, primer_beat_s: null, confianza: 0.002 }
  const g = grillaAnclada(sin, 145.0, 60.0)
  assert.equal(g.anclada, true)
  assert.equal(g.periodo_s, 0.413671, 'el período del motor, no 60/BPM')
  assert.equal(g.compas_ref, 60.0, 'el ancla es el 1')
  assert.ok(Math.abs(g.primer_beat_s - (60 % 0.413671)) < 1e-6)
  assert.deepEqual(estadoGrilla(g), { hay: true, atenuada: false, baja: [], motivo: null })
  assert.ok(Math.abs(grillaAnclada(null, 120, 1.25).periodo_s - 0.5) < 1e-12, 'sin período del motor, 60/BPM medido')
  assert.equal(grillaAnclada(sin, 145, null), null)
  assert.equal(grillaAnclada(null, null, 1), null, 'sin BPM no hay período: no se inventa')
})

test('el ancla se guarda en el navegador con el ajuste, y sin ella el ajuste es el de siempre', () => {
  const m = new Map()
  const s = { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) }
  guardarAjuste(s, 't1', { offset: 0, oculta: false, ancla: 60 })
  assert.deepEqual(leerAjuste(s, 't1'), { offset: 0, oculta: false, ancla: 60 })
  guardarAjuste(s, 't1', { offset: 0, oculta: false })
  assert.deepEqual(leerAjuste(s, 't1'), { offset: 0, oculta: false })
  assert.equal(m.size, 0, 'sin ancla ni ajuste no queda nada guardado')
})

test('atajos K (marcar un kick) y B (solo graves)', () => {
  const t = (key) => ({ key, code: '', target: { tagName: 'DIV', getAttribute: () => 'slider' } })
  assert.deepEqual(atajoD(t('k')), { tipo: 'ancla' })
  assert.deepEqual(atajoD(t('B')), { tipo: 'soloGraves' })
})

/* ---------- auditoría del editor unificado (f54) ---------- */

test('repetidas (M-1): queda la que tiene nombre o pad aunque una suelta sin nombre sea más vieja', () => {
  const suelto = { id: 3, tipo: 'loop', num: null, inicio: 10, fin: 12, nombre: null }
  const hot = { id: 9, tipo: 'loop', num: 0, inicio: 10, fin: 12, nombre: 'Intro' }
  const r = repetidas([hot, suelto])
  assert.deepEqual(r.grupos.map((g) => [g.queda.id, g.sobran.map((m) => m.id)]), [[9, [3]]], 'se borra el suelto, no el hot loop con nombre del pad A')
  // Solo con pad (sin nombre) también cuenta; una memory con nombre, igual.
  assert.equal(repetidas([{ ...suelto }, { ...hot, nombre: null }]).grupos[0].queda.id, 9)
  assert.equal(repetidas([{ id: 1, tipo: 'memory', inicio: 3 }, { id: 2, tipo: 'memory', inicio: 3, nombre: 'Drop' }]).grupos[0].queda.id, 2)
  // Entre iguales en eso, la más vieja (los hot cues siempre tienen pad: decide el nombre).
  assert.equal(repetidas([{ id: 7, tipo: 'cue', num: 3, inicio: 5 }, { id: 4, tipo: 'cue', num: 5, inicio: 5 }]).grupos[0].queda.id, 4)
  assert.equal(repetidas([{ id: 7, tipo: 'cue', num: 3, inicio: 5, nombre: 'Drop' }, { id: 4, tipo: 'cue', num: 5, inicio: 5 }]).grupos[0].queda.id, 7)
  assert.equal(marcaPropia({ tipo: 'cue', num: 0, nombre: '  ' }), false, 'un nombre en blanco no es nombre')
})

test('rescatarHechas (I-1): un Ctrl+Z rechazado a medias deja lo guardado en el historial como paso propio', () => {
  // El caso de la auditoría: una memory de OTRA sesión (no está en el historial) pasada a hot cue
  // en el editor = grupo [crear cue 11, borrar memory 9]. Ctrl+Z = [crear memory, borrar cue 11]:
  // la memory vuelve (id 15) y el DELETE del cue da 400.
  const mem = { id: 9, tipo: 'memory', num: null, inicio: 2, fin: null, nombre: null }
  const cue = { id: 11, tipo: 'cue', num: 0, inicio: 2, fin: null, nombre: null }
  const paso = { tipo: 'grupo', ops: [{ tipo: 'crear', marca: cue }, { tipo: 'borrar', marca: mem }], tid: 'a1' }
  const h = registrar(historialVacio(), paso)
  const sin = cerrarPaso(h, paso, -1, 'rechazo')
  assert.deepEqual(sin, historialVacio(), 'cerrarPaso solo saca el paso entero')
  const r = rescatarHechas(sin, [{ tipo: 'crear', marca: mem }], [[9, 15]], 'a1')
  assert.deepEqual(r, { pasado: [{ tipo: 'grupo', ops: [{ tipo: 'crear', marca: { ...mem, id: 15 } }], tid: 'a1' }], futuro: [] })
  assert.deepEqual(deshacer(r).aplicar, { tipo: 'grupo', ops: [{ tipo: 'borrar', marca: { ...mem, id: 15 } }], tid: 'a1' },
    'el próximo Ctrl+Z saca la memory que quedó guardada')
  assert.deepEqual(rescatarHechas(sin, [], []), sin, 'sin nada hecho, nada que rescatar')
})

test('la X para quitar: nombre accesible y aviso, con pad, nombre y tiempos (pedido del dueño)', () => {
  const cue = { id: 1, tipo: 'cue', num: 0, inicio: 59.576, fin: null, nombre: null }
  assert.equal(etiquetaQuitar(cue), 'Quitar hot cue A en 0:59.576')
  assert.equal(avisoQuitado(cue), 'Quité el hot cue A (0:59.576). Ctrl+Z lo vuelve a poner.')
  assert.equal(avisoNoQuitado(cue), '⚠ No pude quitar el hot cue A (0:59.576): no se guardó (el motivo está en el aviso).')
  const mem = { id: 2, tipo: 'memory', num: null, inicio: 5, fin: null, nombre: 'Drop' }
  assert.equal(etiquetaQuitar(mem), 'Quitar memory cue «Drop» en 0:05.000')
  assert.equal(avisoQuitado(mem), 'Quité la memory cue «Drop» (0:05.000). Ctrl+Z lo vuelve a poner.')
  const loop = { id: 3, tipo: 'loop', num: null, inicio: 59.576, fin: 61.231, nombre: '  ' }
  assert.equal(etiquetaQuitar(loop), 'Quitar loop 0:59.576 a 1:01.231', 'un nombre en blanco no se dice')
  assert.equal(etiquetaQuitar({ ...loop, num: 1, nombre: 'Intro' }), 'Quitar loop del pad B «Intro» 0:59.576 a 1:01.231')
  assert.equal(avisoQuitado({ ...loop, num: 1 }), 'Quité el loop del pad B (0:59.576 a 1:01.231). Ctrl+Z lo vuelve a poner.')
})