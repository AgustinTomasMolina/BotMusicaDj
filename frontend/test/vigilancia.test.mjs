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

test('prendida: carpeta, último escaneo y los contadores que vinieron', () => {
  const v = resumenVigilancia({ exito: true, activa: true, estado: estado() })
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
  assert.equal(fechaCiclo('2026-10-21T23:59:01'), '21/10 23:59')
  assert.equal(fechaCiclo('ayer'), null)
  assert.equal(fechaCiclo(null), null)
})
