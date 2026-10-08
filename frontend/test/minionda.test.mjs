// Tests de src/minionda.js (f50): cómo se reducen los picos a las barras de la miniondita y
// la cola de ondas (tope de concurrencia, prioridad del editor, un solo pedido por tema,
// caché, cancelar lo que salió de pantalla). Los pedidos son promesas controladas a mano:
// el test decide cuándo contesta cada uno.
//
// Uso (desde frontend/):  npm run test:unidad
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { reducirPicos, crearCargador } from '../src/minionda.js'

test('reducirPicos: el máximo de cada tramo, como dibuja el editor', () => {
  assert.deepEqual(reducirPicos([0.1, 0.9, 0.2, 0.3, 0.5, 0.4], 3), [0.9, 0.3, 0.5])
  assert.deepEqual(reducirPicos([0.1, 0.2], 5), [0.1, 0.2], 'con menos picos que barras no se inventan barras')
  assert.deepEqual(reducirPicos([], 10), [])
  assert.deepEqual(reducirPicos(null, 10), [])
  assert.deepEqual(reducirPicos([0.5, Number.NaN, 2], 3), [0.5, 0, 1], 'un valor roto no es un pico y nada pasa de 1')
})

// Un `pedir` que deja cada pedido pendiente hasta que el test lo contesta.
function pedidosManuales() {
  const pendientes = []
  const pedir = (id) => new Promise((ok, mal) => pendientes.push({ id, ok, mal }))
  const contestar = async (id, r = { ok: true, id }) => {
    const i = pendientes.findIndex((p) => p.id === id)
    assert.ok(i >= 0, `no hay un pedido pendiente de ${id} (pendientes: ${pendientes.map((p) => p.id)})`)
    const [p] = pendientes.splice(i, 1)
    p.ok(r)
    await new Promise((res) => setTimeout(res, 0))
  }
  const fallar = async (id) => {
    const i = pendientes.findIndex((p) => p.id === id)
    const [p] = pendientes.splice(i, 1)
    p.mal(new Error('sin red'))
    await new Promise((res) => setTimeout(res, 0))
  }
  return { pendientes, pedir, contestar, fallar }
}
const tick = () => new Promise((res) => setTimeout(res, 0))

test('nunca más de `concurrencia` pedidos a la vez; el resto espera su turno en orden', async () => {
  const m = pedidosManuales()
  const c = crearCargador({ pedir: m.pedir, concurrencia: 2 })
  const ids = ['a', 'b', 'c', 'd', 'e']
  const promesas = ids.map((id) => c.cargar(id).promesa)
  await tick()
  assert.deepEqual(m.pendientes.map((p) => p.id), ['a', 'b'], 'salen los dos primeros')
  await m.contestar('a')
  assert.deepEqual(m.pendientes.map((p) => p.id), ['b', 'c'], 'al terminar uno sale el siguiente de la cola')
  await m.contestar('c')
  await m.contestar('b')
  await m.contestar('d')
  await m.contestar('e')
  assert.deepEqual((await Promise.all(promesas)).map((r) => r.id), ids)
  assert.equal(c.estado().maxEnVuelo, 2, 'el pico de pedidos simultáneos')
})

test('urgente (el editor) pasa adelante de la cola, sin romper el tope', async () => {
  const m = pedidosManuales()
  const c = crearCargador({ pedir: m.pedir, concurrencia: 2 })
  for (const id of ['a', 'b', 'c', 'd']) c.cargar(id)
  c.cargar('z', { urgente: true })
  c.cargar('d', { urgente: true })       // ya estaba en cola: se adelanta
  await tick()
  assert.deepEqual(m.pendientes.map((p) => p.id), ['a', 'b'])
  await m.contestar('a')
  assert.deepEqual(m.pendientes.map((p) => p.id), ['b', 'd'], 'el último urgente va primero')
  await m.contestar('b')
  assert.deepEqual(m.pendientes.map((p) => p.id), ['d', 'z'])
  assert.equal(c.estado().maxEnVuelo, 2)
})

test('el mismo tema se pide una sola vez y después sale de la caché', async () => {
  const m = pedidosManuales()
  let llamadas = 0
  const c = crearCargador({ pedir: (id) => { llamadas += 1; return m.pedir(id) }, concurrencia: 2 })
  const p1 = c.cargar('a').promesa
  const p2 = c.cargar('a', { urgente: true }).promesa     // la fila y el editor del mismo tema
  await tick()
  await m.contestar('a', { ok: true, picos: [0.5] })
  assert.deepEqual(await p1, { ok: true, picos: [0.5] })
  assert.equal(await p2, await p1, 'los dos reciben la MISMA respuesta')
  assert.deepEqual(await c.cargar('a').promesa, { ok: true, picos: [0.5] })
  assert.equal(llamadas, 1, 'tres pedidos del mismo tema tienen que ser UNA llamada al servidor')
})

test('cancelar lo que todavía no salió lo saca de la cola (una fila que se fue de pantalla)', async () => {
  const m = pedidosManuales()
  const c = crearCargador({ pedir: m.pedir, concurrencia: 1 })
  c.cargar('a')
  const b = c.cargar('b')
  c.cargar('c')
  await tick()
  b.cancelar()
  assert.equal(c.estado().enCola, 1, 'b ya no espera')
  await m.contestar('a')
  assert.deepEqual(m.pendientes.map((p) => p.id), ['c'], 'después de a sale c, no b')
})

test('cancelar no saca un tema que otro todavía espera, ni corta uno que ya salió', async () => {
  const m = pedidosManuales()
  const c = crearCargador({ pedir: m.pedir, concurrencia: 1 })
  c.cargar('a')
  const b1 = c.cargar('b')
  const b2 = c.cargar('b')
  await tick()
  b1.cancelar()
  assert.equal(c.estado().enCola, 1, 'b sigue en cola: lo espera otro')
  await m.contestar('a')
  assert.deepEqual(m.pendientes.map((p) => p.id), ['b'])
  b2.cancelar()                                          // ya salió: termina y se guarda
  await m.contestar('b', { ok: true, id: 'b' })
  assert.deepEqual(c.guardado('b'), { ok: true, id: 'b' })
})

test('una respuesta de error (404/422) se guarda; un pedido que tira (sin red) no', async () => {
  const m = pedidosManuales()
  let llamadas = 0
  const c = crearCargador({ pedir: (id) => { llamadas += 1; return m.pedir(id) }, concurrencia: 2 })
  const p404 = c.cargar('x').promesa
  const pRed = c.cargar('y').promesa
  pRed.catch(() => {})    // el rechazo se mira abajo; esto evita el aviso de "sin manejar"
  await tick()
  await m.contestar('x', { ok: false, status: 404 })
  await m.fallar('y')
  assert.deepEqual(await p404, { ok: false, status: 404 })
  await assert.rejects(pRed, /sin red/)
  c.cargar('x')
  c.cargar('y')
  await tick()
  assert.equal(llamadas, 3, 'x sale de la caché; y se vuelve a pedir')
})

test('la caché guarda como mucho `max` temas y descarta el que se usó hace más tiempo', async () => {
  const c = crearCargador({ pedir: async (id) => ({ id }), concurrencia: 2, max: 2 })
  await c.cargar('a').promesa
  await c.cargar('b').promesa
  await c.cargar('a').promesa           // a se usó recién
  await c.cargar('c').promesa           // entra c: sale b
  assert.deepEqual([c.guardado('a'), c.guardado('b'), c.guardado('c')], [{ id: 'a' }, undefined, { id: 'c' }])
})
