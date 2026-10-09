// Tests de getOnda3 (src/api.js, f52): la URL que arma para /api/radio/tracks/{id}/onda3 y que
// devuelve {ok, status, data} como el resto de la radio, con el motivo del 400 en data.error.
// `fetch` es uno falso que anota la URL; las URLs esperadas están escritas a mano.
//
// Uso (desde frontend/):  npm run test:unidad
import { test, beforeEach, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { getOnda3 } from '../src/api.js'

let pedidas = []
let respuesta = { status: 200, cuerpo: '{}' }
const fetchOriginal = globalThis.fetch

beforeEach(() => {
  pedidas = []
  respuesta = { status: 200, cuerpo: '{}' }
  globalThis.fetch = async (url, init) => {
    pedidas.push({ url, metodo: init?.method })
    return { ok: respuesta.status < 400, status: respuesta.status, text: async () => respuesta.cuerpo }
  }
})
afterEach(() => { globalThis.fetch = fetchOriginal })

test('sin tramo ni puntos pide el tema entero: la URL va sin query', async () => {
  await getOnda3('0123456789abcdef')
  assert.deepEqual(pedidas, [{ url: '/api/radio/tracks/0123456789abcdef/onda3', metodo: 'GET' }])
})

test('desde, hasta y puntos viajan en ese orden; el 0 viaja (no es «sin dato»)', async () => {
  await getOnda3('abc', 0, 12.5, 2000)
  assert.equal(pedidas[0].url, '/api/radio/tracks/abc/onda3?desde=0&hasta=12.5&puntos=2000')
})

test('null o undefined no viajan: el backend usa su default', async () => {
  await getOnda3('abc', null, 30)
  await getOnda3('abc', 10, undefined, 500)
  assert.deepEqual(pedidas.map((p) => p.url), [
    '/api/radio/tracks/abc/onda3?hasta=30',
    '/api/radio/tracks/abc/onda3?desde=10&puntos=500',
  ])
})

test('un NaN viaja tal cual (el backend contesta 400) en vez de pedir el tema entero callado', async () => {
  await getOnda3('abc', Number.NaN, 5)
  assert.equal(pedidas[0].url, '/api/radio/tracks/abc/onda3?desde=NaN&hasta=5')
})

test('el id va escapado: nunca arma otra ruta', async () => {
  await getOnda3('../../x?y', 1, 2)
  assert.equal(pedidas[0].url, '/api/radio/tracks/..%2F..%2Fx%3Fy/onda3?desde=1&hasta=2')
})

test('un 400 devuelve el motivo del backend en data.error', async () => {
  respuesta = { status: 400, cuerpo: '{"error": "`hasta` (31 s) está después del final del audio (30.000 s)"}' }
  const r = await getOnda3('abc', 0, 31)
  assert.deepEqual(r, { ok: false, status: 400, data: { error: '`hasta` (31 s) está después del final del audio (30.000 s)' } })
})
