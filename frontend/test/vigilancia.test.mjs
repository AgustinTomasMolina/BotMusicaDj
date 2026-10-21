// Tests de src/vigilancia.js (f66): la línea de estado de la carpeta vigilada dice SOLO lo que
// devuelve GET /api/vigilancia. Las respuestas tienen la forma de server.vigilancia_estado y
// de pipeline/vigilar.py `Vigilante.estado()`; los textos esperados están escritos a mano.
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
const { resumenVigilancia, fechaCiclo } = await vite.ssrLoadModule('/src/vigilancia.js')

const APAGADA = { exito: true, activa: false, mensaje: 'La carpeta vigilada está apagada: definí MUSIC_WATCH_FOLDER en el .env (una carpeta local, un disco montado o \\\\NAS\\carpeta) y reiniciá el servidor.' }
const estado = (extra) => ({
  carpeta: 'Mis Descargas', alcanzable: true, corriendo: true, procesando: [], pendientes: 2, creciendo: 0,
  ignorados: 1, procesados: 14, fallidos: 1, ultimos_fallidos: ['roto.mp3'], ultimo_ciclo: '2026-10-21T09:05:33',
  ultimo_error: null, ultimo_reporte: 'revision_vigilada_2026-10-21_090533.html', intervalo_s: 60, ...extra,
})

test('apagada: el mensaje del server, que dice cómo prenderla, y nada más', () => {
  const v = resumenVigilancia(APAGADA)
  assert.equal(v.tipo, 'apagada')
  assert.equal(v.titulo, APAGADA.mensaje)
  assert.deepEqual([v.partes, v.aviso, v.procesando, v.reporte], [[], null, [], null])
})

// El "ahora" de los casos: el mismo año que el último escaneo del fixture (no el reloj de la PC).
const AHORA = new Date(2026, 9, 21, 12, 0)

test('prendida: carpeta, último escaneo y los contadores que vinieron', () => {
  const v = resumenVigilancia({ exito: true, activa: true, estado: estado() }, AHORA)
  assert.equal(v.tipo, 'activa')
  assert.equal(v.titulo, 'Carpeta vigilada «Mis Descargas»')
  assert.deepEqual(v.partes, ['último escaneo 21/10 09:05', '2 pendientes', '14 procesados', '1 fallido'])
  assert.equal(v.aviso, null)
  assert.equal(v.reporte, 'revision_vigilada_2026-10-21_090533.html')
})

test('prendida sin escanear todavía, copiando y procesando: lo dice; un contador que no vino no es 0', () => {
  const v = resumenVigilancia({ exito: true, activa: true, estado: estado({ ultimo_ciclo: null, pendientes: 1, procesados: undefined,
    fallidos: 0, creciendo: 3, procesando: ['tema nuevo.wav'] }) })
  assert.deepEqual(v.partes, ['todavía sin escanear', '1 pendiente', '0 fallidos', '3 todavía copiándose'])
  assert.deepEqual(v.procesando, ['tema nuevo.wav'])
})

test('carpeta que no se alcanza o hilo parado: el aviso con el motivo del server', () => {
  const nas = resumenVigilancia({ exito: true, activa: true, estado: estado({ alcanzable: false, ultimo_error: 'la carpeta vigilada «musica» no está (¿el NAS está apagado?)' }) })
  assert.equal(nas.aviso, 'No se alcanza la carpeta: la carpeta vigilada «musica» no está (¿el NAS está apagado?)')
  const parado = resumenVigilancia({ exito: true, activa: true, estado: estado({ corriendo: false }) })
  assert.equal(parado.aviso, 'El vigilante no está corriendo.')
})

test('sin respuesta o con error: no inventa un estado', () => {
  for (const r of [null, { error: true }, { exito: false, mensaje: 'x' }]) {
    const v = resumenVigilancia(r)
    assert.deepEqual([v.tipo, v.titulo, v.partes], ['error', 'No pude leer el estado de la carpeta vigilada.', []])
  }
})

test('fechaCiclo: la hora local del server tal cual, sin reinterpretar la zona', () => {
  assert.equal(fechaCiclo('2026-10-21T23:59:01', AHORA), '21/10 23:59')
  assert.equal(fechaCiclo('ayer', AHORA), null)
  assert.equal(fechaCiclo(null, AHORA), null)
})

test('fechaCiclo: de otro año, con el año; del mismo año, sin él', () => {
  // El borde: 31/12 a las 23:59 visto a las 00:05 del 1/1 ya es de otro año y lo dice.
  const anoNuevo = new Date(2027, 0, 1, 0, 5)
  assert.equal(fechaCiclo('2026-12-31T23:59:01', anoNuevo), '31/12/2026 23:59')
  assert.equal(fechaCiclo('2027-01-01T00:01:00', anoNuevo), '01/01 00:01')
  // Y dentro del resumen, contra el `ahora` que se le pasa (no el reloj de la PC).
  const r = { exito: true, activa: true, estado: estado({ ultimo_ciclo: '2025-03-02T08:00:00' }) }
  assert.equal(resumenVigilancia(r, AHORA).partes[0], 'último escaneo 02/03/2025 08:00')
  assert.equal(resumenVigilancia(r, new Date(2025, 5, 1)).partes[0], 'último escaneo 02/03 08:00')
})

// escanearVigilancia (src/api.js): `fetch` es uno falso que contesta lo que diga cada caso.
const api = await import('../src/api.js')
const conFetch = async (respuesta, fn) => {
  const antes = globalThis.fetch
  const pedidos = []
  globalThis.fetch = async (url, init) => { pedidos.push([url, init?.method ?? 'GET']); return respuesta() }
  try { return { valor: await fn(), pedidos } } finally { globalThis.fetch = antes }
}

test('escanear: un error que no es JSON (un 500 de HTML) dice que el server contestó con un error y el código', async () => {
  const { valor, pedidos } = await conFetch(() => new Response('<html><body>Internal Server Error</body></html>',
    { status: 500, headers: { 'Content-Type': 'text/html' } }), () => api.escanearVigilancia())
  assert.deepEqual(pedidos, [['/api/vigilancia/escanear', 'POST']])
  assert.deepEqual(valor, { exito: false, mensaje: 'El servidor contestó con un error (HTTP 500).' })
})

test('escanear: el JSON del server pasa tal cual (también el 409 de apagada)', async () => {
  const cuerpo = { exito: false, mensaje: 'La carpeta vigilada está apagada.' }
  const { valor } = await conFetch(() => new Response(JSON.stringify(cuerpo), { status: 409 }), () => api.escanearVigilancia())
  assert.deepEqual(valor, cuerpo)
})

test('los demás pedidos no cambian: GET /api/vigilancia con HTML sigue tirando (lo muestra como "no pude leer")', async () => {
  await assert.rejects(conFetch(() => new Response('<html></html>', { status: 502 }), () => api.vigilancia()), SyntaxError)
})
