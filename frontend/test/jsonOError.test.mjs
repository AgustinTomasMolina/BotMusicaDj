// Tests de `jsonOError` (src/api.js, f66): lo que dice cuando la respuesta de «Escanear ahora»
// no trae el {exito, mensaje} de siempre. Cada caso arma un `Response` real (el de fetch) con
// el cuerpo y el código a mano; los mensajes esperados están escritos a mano.
//
// Uso (desde frontend/):  npm run test:unidad
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { jsonOError } from '../src/api.js'

const html = (status) => new Response('<html><body><h1>Internal Server Error</h1></body></html>',
  { status, headers: { 'Content-Type': 'text/html' } })
const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

test('error que no es JSON: el server contestó con un error, y con qué código', async () => {
  assert.deepEqual(await jsonOError(html(500)), { exito: false, mensaje: 'El servidor contestó con un error (HTTP 500).' })
  assert.deepEqual(await jsonOError(html(502)), { exito: false, mensaje: 'El servidor contestó con un error (HTTP 502).' })
})

test('2xx que no se puede leer (vacío o cortado): no dice que fue un error del server', async () => {
  assert.deepEqual(await jsonOError(new Response('', { status: 200 })),
    { exito: false, mensaje: 'No pude leer la respuesta del servidor.' })
  // La conexión se corta a mitad del cuerpo: el stream tira al leerlo.
  const cortado = new ReadableStream({
    start(c) { c.enqueue(new TextEncoder().encode('{"exito": tr')); c.error(new TypeError('terminated')) },
  })
  assert.deepEqual(await jsonOError(new Response(cortado, { status: 200 })),
    { exito: false, mensaje: 'No pude leer la respuesta del servidor.' })
})

test('error JSON sin `mensaje` (el {"detail"} de FastAPI): el código y el detalle si es texto', async () => {
  assert.deepEqual(await jsonOError(json(404, { detail: 'Not Found' })),
    { exito: false, mensaje: 'El servidor contestó con un error (HTTP 404): Not Found.' })
  assert.deepEqual(await jsonOError(json(405, { detail: 'Method Not Allowed.' })),
    { exito: false, mensaje: 'El servidor contestó con un error (HTTP 405): Method Not Allowed.' })
  // El 422 de validación trae una lista en `detail`: no es texto, va solo el código.
  assert.deepEqual(await jsonOError(json(422, { detail: [{ loc: ['body'], msg: 'field required' }] })),
    { exito: false, mensaje: 'El servidor contestó con un error (HTTP 422).' })
  assert.deepEqual(await jsonOError(json(500, null)), { exito: false, mensaje: 'El servidor contestó con un error (HTTP 500).' })
})

test('las respuestas JSON de siempre pasan tal cual (un 409 con su mensaje, un 200)', async () => {
  const apagada = { exito: false, mensaje: 'La carpeta vigilada está apagada.' }
  assert.deepEqual(await jsonOError(json(409, apagada)), apagada)
  const ok = { exito: true, encolado: true, estado: { carpeta: 'Mis Descargas' } }
  assert.deepEqual(await jsonOError(json(200, ok)), ok)
})
