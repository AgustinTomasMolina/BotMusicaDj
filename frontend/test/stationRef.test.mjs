// Tests de la referencia de SoundCloud que la antena manda a /api/station (f43, src/api.js).
//
// El caso real (auditoría del 2026-10-01): buscando "Kashpitzky For The Vision" la fila trae
// YouTube (elegida), SoundCloud 2041950136 y dos MP3. Antes se mandaba solo la elegida y la
// opción de SoundCloud se perdía. Las opciones de YouTube y SoundCloud son las que devolvió
// /api/buscar; las de MP3 llevan una URL inventada (las reales son URLs firmadas).
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
const { station, stationRef } = await vite.ssrLoadModule('/src/api.js')

const YT = { titulo: 'Kashpitzky — For The Vision [BAO095]', artista: 'HATE', duracion: 334,
  url: 'https://www.youtube.com/watch?v=5HuHGbZno1Y', fuente: 'youtube', video_id: '5HuHGbZno1Y' }
const SC = { titulo: 'GTG Premiere | Kashpitzky - For The Vision  [BAOX095]', artista: 'Grab The Groove', duracion: 333.249,
  url: 'https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A2041950136', fuente: 'soundcloud', video_id: '2041950136' }
const MP3 = { titulo: 'For The Vision', artista: 'Kashpitzky', duracion: 333, url: 'https://example.invalid/a.mp3', fuente: 'ligaudio' }
const busqueda = { opciones: [YT, SC, MP3] }

test('fila de búsqueda: la referencia es la opción de SoundCloud del grupo, como candidata', () => {
  assert.deepEqual(stationRef(busqueda), { sc_ref: '2041950136', sc_ref_origen: 'busqueda' })
})

test('fila de búsqueda: el id sale de la URL de la API si no viene video_id (el extractor de siempre)', () => {
  const sinId = { ...SC, video_id: undefined }
  assert.deepEqual(stationRef({ opciones: [YT, sinId] }), { sc_ref: '2041950136', sc_ref_origen: 'busqueda' })
})

test('fila sin SoundCloud: no hay referencia', () => {
  assert.equal(stationRef({ opciones: [YT, MP3] }), null)
  assert.equal(stationRef(undefined), null)
})

test('un id numérico de otra plataforma (Deezer) no es una referencia de SoundCloud', () => {
  const deezer = { titulo: 'For The Vision', artista: 'Kashpitzky', fuente: 'deezer', video_id: '3135556', url: 'https://www.deezer.com/track/3135556' }
  assert.equal(stationRef({ opciones: [YT, deezer] }), null)
  assert.deepEqual(stationRef({ opciones: [deezer, SC] }), { sc_ref: '2041950136', sc_ref_origen: 'busqueda' })
})

test('un id que el backend rechazaría no se manda (sería un 400 en vez de buscar)', () => {
  assert.equal(stationRef({ opciones: [YT, { ...SC, video_id: '0', url: '' }] }), null)
  assert.equal(stationRef({ opciones: [YT, { ...SC, video_id: '1'.repeat(21), url: '' }] }), null)
})

test('fila de la Station: la referencia es `g.base` (el tema de la Station), con origen "station"', () => {
  const base = { titulo: 'Redefined', artista: 'allure.', fuente: 'soundcloud', video_id: '2356665509',
    url: 'https://soundcloud.com/allurerecs/alr-premiere-illia-redefined', estacion: true }
  // Elegida la versión de YouTube de esa fila: igual manda el tema de la Station.
  const g = { opciones: [{ ...YT, video_id: 'bbbbbbbbbbb' }, base], base, sel: 0 }
  assert.deepEqual(stationRef(g), { sc_ref: '2356665509', sc_ref_origen: 'station' })
})

test('station(c, g): con YouTube elegido el pedido lleva la opción elegida Y sc_ref del grupo', async (t) => {
  const pedidos = []
  t.mock.method(globalThis, 'fetch', async (url) => {
    pedidos.push(url)
    return { status: 200, text: async () => JSON.stringify({ exito: false, motivo: 'x', mensaje: 'y' }) }
  })
  await station(YT, busqueda)
  assert.equal(pedidos.length, 1)
  const u = new URL(pedidos[0], 'http://local')
  assert.equal(u.pathname, '/api/station')
  assert.deepEqual(Object.fromEntries(u.searchParams), {
    fuente: 'youtube', fuente_id: '5HuHGbZno1Y', titulo: YT.titulo, artista: 'HATE', duracion: '334',
    sc_ref: '2041950136', sc_ref_origen: 'busqueda',
  })
})

test('station(c) sin grupo (llamada vieja): no manda sc_ref', async (t) => {
  const pedidos = []
  t.mock.method(globalThis, 'fetch', async (url) => {
    pedidos.push(url)
    return { status: 200, text: async () => JSON.stringify({ exito: false }) }
  })
  await station(YT)
  const u = new URL(pedidos[0], 'http://local')
  assert.equal(u.searchParams.has('sc_ref'), false)
  assert.equal(u.searchParams.has('sc_ref_origen'), false)
})
