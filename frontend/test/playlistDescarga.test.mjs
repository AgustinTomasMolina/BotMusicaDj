// Tests de src/playlistDescarga.js (f41): bajar un tema de la playlist y "los N que faltan".
//
// Mismo esquema que los de stationVersions (f37): `node --test` + el Vite de devDependencies,
// que carga el módulo como la app. Nada sale a la red: `pedir` (el POST al server), `esperar`
// (el seguimiento del job) y `bajar` (la descarga de un item) son dobles que el test controla.
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
const { bajarItem, crearDescargas, faltantes, textoLote, TIMEOUT_JOB_MS } = await vite.ssrLoadModule('/src/playlistDescarga.js')

// Tope por caso: un doble que nunca contesta (o una tanda que no se detiene) es un fallo
// con nombre, no una corrida colgada.
const caso = (nombre, fn) => test(nombre, { timeout: 5000 }, fn)
const SIN_LINK = 'Sin link para bajar: el tema se guardó sin una fuente de internet (por ejemplo, desde tu biblioteca).'
const it = (id, extra = {}) => ({ id, titulo: `Tema ${id}`, descargado: false, motivo_no_bajable: null, ...extra })

/* ---------- bajarItem: cómo se lee la respuesta del server ---------- */

caso('bajarItem: sin cola, devuelve el resultado del server tal cual (éxito con item y nota)', async () => {
  const pedidos = []
  const pedir = async (...a) => { pedidos.push(a); return { ok: true, status: 200, data: { exito: true, mensaje: 'Descargado: x.wav', item: { id: 7, formato: 'wav' }, calidad: { grade: 'B' } } } }
  const r = await bajarItem(3, 7, 'wav', { pedir, esperar: () => assert.fail('no hay job que esperar') })
  assert.deepEqual(pedidos, [[3, 7, 'wav']])
  assert.deepEqual(r, { exito: true, mensaje: 'Descargado: x.wav', item: { id: 7, formato: 'wav' }, calidad: { grade: 'B' } })
})

caso('bajarItem: con cola sigue el job (con el tope de 15 min) y devuelve SU resultado', async () => {
  const esperas = []
  const pedir = async () => ({ ok: true, status: 200, data: { encolado: true, job_id: 'j1' } })
  const esperar = async (id, op) => { esperas.push([id, op]); return { exito: false, mensaje: 'ERROR: Video unavailable', item: { id: 7 } } }
  const r = await bajarItem(3, 7, 'flac', { pedir, esperar })
  assert.deepEqual(esperas, [['j1', { timeout: TIMEOUT_JOB_MS }]])
  assert.equal(TIMEOUT_JOB_MS, 900000, 'el tope tiene que ser el job_timeout de jobs.encolar (900 s)')
  assert.deepEqual(r, { exito: false, mensaje: 'ERROR: Video unavailable', item: { id: 7 }, calidad: null })
})

caso('bajarItem: un 400/404 trae el motivo del server; un 500 en texto dice el código; sin red lo dice', async () => {
  const r400 = await bajarItem(1, 2, 'wav', { pedir: async () => ({ ok: false, status: 400, data: { exito: false, mensaje: SIN_LINK } }) })
  assert.deepEqual([r400.exito, r400.mensaje], [false, SIN_LINK])
  const r500 = await bajarItem(1, 2, 'wav', { pedir: async () => ({ ok: false, status: 500, data: { error_texto: 'Internal Server Error' } }) })
  assert.deepEqual(r500, { exito: false, mensaje: 'El servidor falló al bajar (HTTP 500): Internal Server Error' })
  const rRed = await bajarItem(1, 2, 'wav', { pedir: async () => { throw new TypeError('Failed to fetch') } })
  assert.deepEqual(rRed, { exito: false, mensaje: 'No pude conectar con el servidor.' })
})

/* ---------- faltantes ---------- */

caso('faltantes: solo los no bajados que se pueden bajar, en el orden de la playlist', () => {
  const items = [it(1, { descargado: true }), it(2), it(3, { motivo_no_bajable: SIN_LINK }), it(4)]
  assert.deepEqual(faltantes(items).map((x) => x.id), [2, 4])
})

/* ---------- la tanda ---------- */

// Doble de bajarItem: contesta cuando el test lo libera, así se puede mirar el estado MIENTRAS baja.
function bajadorControlado(resultados) {
  const pendientes = []
  const llamadas = []
  const bajar = (pid, id, formato) => new Promise((res) => { llamadas.push([pid, id, formato]); pendientes.push({ id, res }) })
  const liberar = async () => {
    while (!pendientes.length) await new Promise((r) => setImmediate(r))
    const p = pendientes.shift()
    p.res(resultados[p.id] ?? { exito: true, mensaje: '' })
    await new Promise((r) => setImmediate(r))
  }
  return { bajar, llamadas, liberar }
}

caso('bajarTodos: baja solo los que faltan, de a uno, y el progreso cuenta bien', async () => {
  const items = [it(1, { descargado: true }), it(2), it(3, { motivo_no_bajable: SIN_LINK }), it(4), it(5)]
  const b = bajadorControlado({ 4: { exito: false, mensaje: 'ERROR: [youtube] 403 Forbidden' } })
  const d = crearDescargas({ bajar: b.bajar })
  const textos = []
  d.subscribe(() => { const t = textoLote(d.getSnapshot().lote); if (textos.at(-1) !== t) textos.push(t) })
  const fin = d.bajarTodos(9, items, 'mp3')

  await new Promise((r) => setImmediate(r))
  // Mientras baja el primero: uno pedido, los otros en cola.
  assert.deepEqual(b.llamadas, [[9, 2, 'mp3']])
  assert.deepEqual(d.getSnapshot().items, { 2: { estado: 'bajando' }, 4: { estado: 'cola' }, 5: { estado: 'cola' } })
  await b.liberar(); await b.liberar(); await b.liberar()
  const lote = await fin

  assert.deepEqual(b.llamadas, [[9, 2, 'mp3'], [9, 4, 'mp3'], [9, 5, 'mp3']], 'bajó un descargado o un sin link, o en otro orden')
  assert.deepEqual(textos, [
    'Bajando 1 de 3 · Tema 2…',
    'Bajando 2 de 3 · Tema 4…',
    'Bajando 3 de 3 · Tema 5…',
    '2 de 3 bajados · 1 con error (el motivo está en cada tema)',
  ])
  assert.deepEqual([lote.ok, lote.fallidos], [2, [{ id: 4, titulo: 'Tema 4', mensaje: 'ERROR: [youtube] 403 Forbidden' }]])
  // El que falló queda con SU motivo; los bajados salen del mapa (lo dice la playlist recargada).
  assert.deepEqual(d.getSnapshot().items, { 4: { estado: 'error', mensaje: 'ERROR: [youtube] 403 Forbidden' } })
  assert.equal(d.getSnapshot().hechos, 2, 'la pantalla recarga la playlist una vez por tema bajado')
})

caso('bajarTodos: una tanda nueva reintenta el que falló (y no los ya bajados)', async () => {
  const resultados = { 2: { exito: false, mensaje: 'HTTP Error 403' } }
  const b = bajadorControlado(resultados)
  const d = crearDescargas({ bajar: b.bajar })
  const fin = d.bajarTodos(1, [it(1), it(2)], 'wav')
  await b.liberar(); await b.liberar(); await fin
  // Segunda tanda con la playlist recargada (1 ya bajado, 2 sigue sin bajar); ahora 2 baja.
  delete resultados[2]
  const fin2 = d.bajarTodos(1, [it(1, { descargado: true }), it(2)], 'wav')
  await b.liberar(); const lote = await fin2
  assert.deepEqual(b.llamadas, [[1, 1, 'wav'], [1, 2, 'wav'], [1, 2, 'wav']])
  assert.deepEqual([lote.total, lote.ok, d.getSnapshot().items], [1, 1, {}], 'el error viejo tiene que limpiarse al bajar')
})

caso('bajarTodos: "detener" corta después del que está bajando y los demás vuelven a falta bajar', async () => {
  const b = bajadorControlado({})
  const d = crearDescargas({ bajar: b.bajar })
  const items = [it(1), it(2), it(3)]
  const fin = d.bajarTodos(1, items, 'wav')
  await new Promise((r) => setImmediate(r))
  d.detener()
  await b.liberar()
  const lote = await fin
  assert.deepEqual(b.llamadas.map((l) => l[1]), [1], 'después de detener no se pide ninguno más')
  assert.deepEqual([lote.ok, lote.total, lote.detenido], [1, 3, true])
  assert.equal(textoLote(lote), 'Listo: 1 de 3 bajados · detenido')
  // Los que no llegaron vuelven a "falta bajar" (sin estado), no quedan "en cola" para siempre.
  assert.deepEqual(d.getSnapshot().items, {})
})

caso('bajarUno: un error deja el motivo y el reintento lo limpia; dos clicks no piden dos veces', async () => {
  const b = bajadorControlado({ 8: { exito: false, mensaje: 'No se pudo encontrar audio descargable.' } })
  const d = crearDescargas({ bajar: b.bajar })
  const p1 = d.bajarUno(2, it(8), 'wav')
  const p2 = await d.bajarUno(2, it(8), 'wav')
  assert.deepEqual(p2, { exito: false, mensaje: 'Ya se está bajando.' })
  await b.liberar(); await p1
  assert.deepEqual(d.getSnapshot().items, { 8: { estado: 'error', mensaje: 'No se pudo encontrar audio descargable.' } })
  assert.equal(b.llamadas.length, 1)
  const b2 = bajadorControlado({})
  const d2 = crearDescargas({ bajar: b2.bajar })
  const p3 = d2.bajarUno(2, it(8), 'wav'); await b2.liberar(); await p3
  assert.deepEqual([d2.getSnapshot().items, d2.getSnapshot().hechos], [{}, 1])
})

caso('bajarTodos: no arranca una segunda tanda mientras hay una en curso', async () => {
  const b = bajadorControlado({})
  const d = crearDescargas({ bajar: b.bajar })
  const fin = d.bajarTodos(1, [it(1)], 'wav')
  assert.equal(await d.bajarTodos(2, [it(5)], 'wav'), null)
  await b.liberar(); await fin
  assert.deepEqual(b.llamadas, [[1, 1, 'wav']])
})

/* ---------- ronda 2: un tema quitado de la playlist durante la tanda (C5) ---------- */

caso('bajarItem: el 404 de "ya no está en la playlist" y el job que lo dice vuelven como quitado', async () => {
  const r404 = await bajarItem(1, 2, 'wav', { pedir: async () => ({ ok: false, status: 404, data: { exito: false, mensaje: 'Ese tema no está en esta playlist.', quitado: true } }) })
  assert.deepEqual(r404, { exito: false, mensaje: 'Ese tema no está en esta playlist.', item: null, calidad: null, quitado: true })
  const rJob = await bajarItem(1, 2, 'wav', {
    pedir: async () => ({ ok: true, status: 200, data: { encolado: true, job_id: 'j' } }),
    esperar: async () => ({ exito: false, mensaje: 'El tema ya no está en esta playlist.', item: null, quitado: true }),
  })
  assert.equal(rJob.quitado, true)
  // Una playlist que no existe NO es un tema quitado (el server manda quitado: false).
  const rPl = await bajarItem(1, 2, 'wav', { pedir: async () => ({ ok: false, status: 404, data: { exito: false, mensaje: 'Playlist no encontrada.', quitado: false } }) })
  assert.equal(rPl.quitado, undefined)
})

caso('bajarTodos: lo quitado mientras espera no se pide y no cuenta como error "en su fila"', async () => {
  const b = bajadorControlado({ 3: { exito: false, mensaje: 'Ese tema no está en esta playlist.', quitado: true } })
  const d = crearDescargas({ bajar: b.bajar })
  const fin = d.bajarTodos(1, [it(1), it(2), it(3), it(4)], 'wav')
  await new Promise((r) => setImmediate(r))
  d.olvidar(2)                         // la pantalla lo quitó mientras bajaba el 1
  await b.liberar(); await b.liberar(); await b.liberar()
  const lote = await fin
  assert.deepEqual(b.llamadas.map((l) => l[1]), [1, 3, 4], 'pidió al server un tema que ya se había quitado')
  assert.deepEqual([lote.ok, lote.fallidos, lote.quitados], [2, [], [2, 3]])
  assert.deepEqual(d.getSnapshot().items, {}, 'un quitado no puede quedar como error ni en cola')
  assert.equal(textoLote(lote), '2 de 4 bajados · 2 quitados de la playlist mientras bajaba')
  assert.equal(textoLote({ ...lote, fallidos: [{ id: 9, titulo: 'x', mensaje: 'y' }], quitados: [3] }),
    '2 de 4 bajados · 1 con error (el motivo está en cada tema) · 1 quitado de la playlist mientras bajaba')
})
