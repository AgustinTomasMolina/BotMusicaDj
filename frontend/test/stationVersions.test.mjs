// Tests de src/stationVersions.js (f36): la versión elegida por defecto y la cola que pide
// /api/versiones fila por fila.
//
// Sin dependencias nuevas: `node --test` (Node 22) + el Vite que ya está en devDependencies,
// que carga el módulo como lo carga la app (resuelve './api' sin extensión y el .jsx de
// common). `fetch` es un doble que el test controla: contesta cuando el test dice, en el orden
// que el test dice, y respeta el `signal` (un abort lo corta como en el navegador).
//
// Uso (desde frontend/):  npm run test:unidad

import { test, after, mock } from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const vite = await createServer({
  configFile: false, root: fileURLToPath(new URL('..', import.meta.url)), logLevel: 'error',
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom',
})
after(() => vite.close())
const { bestOption, cargarVersiones, CONCURRENCIA, TIMEOUT_FILA_MS } = await vite.ssrLoadModule('/src/stationVersions.js')
const { GRADE_RANK } = await vite.ssrLoadModule('/src/components/common.jsx')

/* ---------- bestOption ---------- */

const op = (fuente, grade, extra = {}) => ({ fuente, titulo: 'T', artista: 'A', url: `u-${fuente}-${grade}`,
  ...(grade ? { calidad: { ok: grade !== '?', grade } } : {}), ...extra })

test('bestOption: gana la de mayor nota aunque no sea la primera', () => {
  const o = [op('youtube', 'B'), op('soundcloud', 'C', { estacion: true }), op('ligaudio', 'A'), op('hitplayer', 'B')]
  assert.equal(bestOption(o), 2)
})

test('bestOption: el orden de las notas es el de GRADE_RANK (A > A- > B > C > D > F)', () => {
  // Contra la tabla del comparador, no contra una copia: si cambia GRADE_RANK, esto la sigue.
  const notas = Object.keys(GRADE_RANK).filter((g) => g !== '?').sort((a, b) => GRADE_RANK[a] - GRADE_RANK[b])
  assert.deepEqual(notas, ['F', 'D', 'C', 'B', 'A-', 'A'])
  for (let i = 1; i < notas.length; i++) {
    const o = [op('youtube', notas[i - 1]), op('ligaudio', notas[i])]
    assert.equal(bestOption(o), 1, `${notas[i]} tiene que ganarle a ${notas[i - 1]}`)
  }
})

test('bestOption: a igual nota gana la primera (el backend las ordena por fuente)', () => {
  assert.equal(bestOption([op('youtube', 'A'), op('soundcloud', 'A', { estacion: true }), op('ligaudio', 'A')]), 0)
  assert.equal(bestOption([op('youtube', 'C'), op('soundcloud', 'A', { estacion: true }), op('ligaudio', 'A')]), 1)
})

test('bestOption: una nota "?" (no analizable) o sin nota cuenta como la más baja, no como una A', () => {
  assert.equal(bestOption([op('youtube', '?'), op('ligaudio', 'F')]), 1)
  assert.equal(bestOption([op('youtube'), op('ligaudio', 'D')]), 1)
  // `ok:false` con una letra: no vale la letra.
  assert.equal(bestOption([op('youtube', 'D'), { ...op('ligaudio'), calidad: { ok: false, grade: 'A' } }]), 0)
})

test('bestOption: nunca un preview de 30 s ni Spotify (ni Deezer), aunque tengan la mejor nota', () => {
  const o = [op('spotify', 'A'), op('soundcloud', 'A', { estacion: true, solo_preview: true }), op('deezer', 'A'),
    op('Spotify', 'A'), op('youtube', 'F')]
  assert.equal(bestOption(o), 4)
})

test('bestOption: si no hay otra, la de la Station aunque sea un preview', () => {
  const o = [op('spotify', 'A'), op('soundcloud', null, { estacion: true, solo_preview: true })]
  assert.equal(bestOption(o), 1)
  assert.equal(bestOption([op('soundcloud', null, { estacion: true, solo_preview: true })]), 0)
})

/* ---------- cargarVersiones ---------- */

// fetch controlado: cada pedido queda pendiente hasta que el test lo contesta.
function fetchFalso() {
  const pedidos = []
  let enVuelo = 0, maxEnVuelo = 0
  const f = (url, opts) => new Promise((resolve, reject) => {
    const cuerpo = JSON.parse(opts.body)
    enVuelo++
    maxEnVuelo = Math.max(maxEnVuelo, enVuelo)
    const p = { url, cuerpo, abortado: false, hecho: false }
    const fin = () => { if (!p.hecho) { p.hecho = true; enVuelo-- } }
    p.contestar = (body, status = 200) => { fin(); resolve({ status, text: async () => (typeof body === 'string' ? body : JSON.stringify(body)) }) }
    opts.signal?.addEventListener('abort', () => {
      p.abortado = true
      fin()
      reject(Object.assign(new Error('The operation was aborted.'), { name: 'AbortError' }))
    })
    pedidos.push(p)
  })
  return { f, pedidos, enVuelo: () => enVuelo, maxEnVuelo: () => maxEnVuelo }
}

const tema = (i) => ({ titulo: `Tema ${i}`, artista: `Artista ${i}`, fuente: 'soundcloud', video_id: String(1000 + i),
  url: `https://soundcloud.com/a/t${i}` })
const respuesta = (t, extra = []) => ({ exito: true, motivo: null, fallidas: [],
  opciones: [...extra, { ...t, estacion: true, calidad: { ok: true, grade: 'C' } }] })
const tick = () => new Promise((r) => setImmediate(r))
async function hasta(cond, que) {
  for (let i = 0; i < 200; i++) { if (cond()) return; await tick() }
  assert.fail(que)
}

function conFetch(fake, fn) {
  const real = globalThis.fetch
  globalThis.fetch = fake.f
  return Promise.resolve().then(fn).finally(() => { globalThis.fetch = real })
}

test('cargarVersiones: concurrencia 3, cada pedido lleva su tema y el formato', async () => {
  const fake = fetchFalso()
  const items = Array.from({ length: 10 }, (_, i) => tema(i))
  await conFetch(fake, async () => {
    const filas = []
    let fin = 0
    cargarVersiones(items, 'wav', { onFila: (i) => filas.push(i), onFin: () => fin++ })
    await hasta(() => fake.pedidos.length === 3, 'no arrancaron 3 pedidos')
    await tick(); await tick()
    assert.equal(CONCURRENCIA, 3)
    assert.equal(fake.pedidos.length, 3, 'arrancaron más de 3 pedidos a la vez')
    assert.deepEqual(fake.pedidos.map((p) => [p.url, p.cuerpo.tema.video_id, p.cuerpo.formato]),
      [['/api/versiones', '1000', 'wav'], ['/api/versiones', '1001', 'wav'], ['/api/versiones', '1002', 'wav']])
    // Contestar de a uno: siempre hay 3 en vuelo hasta que se acaban.
    for (let k = 0; k < items.length; k++) {
      fake.pedidos[k].contestar(respuesta(items[k]))
      await hasta(() => filas.length === k + 1, `la fila ${k} no se entregó`)
      await hasta(() => fake.pedidos.length === Math.min(items.length, k + 4), 'no se pidió la siguiente')
    }
    await hasta(() => fin === 1, 'no terminó')
    assert.equal(fake.maxEnVuelo(), 3)
    assert.deepEqual(fake.pedidos.map((p) => p.cuerpo.tema.titulo), items.map((t) => t.titulo))
  })
})

test('cargarVersiones: entrega en el orden de la Station aunque las respuestas lleguen al revés', async () => {
  const fake = fetchFalso()
  const items = Array.from({ length: 5 }, (_, i) => tema(i))
  await conFetch(fake, async () => {
    const entregas = []
    let fin = 0
    cargarVersiones(items, 'mp3', { onFila: (i, g) => entregas.push([i, g.base.titulo, g.opciones.map((o) => o.url)]),
      onFin: () => fin++, concurrencia: 5 })
    await hasta(() => fake.pedidos.length === 5, 'no se pidieron las 5')
    for (const k of [4, 3, 1]) fake.pedidos[k].contestar(respuesta(items[k], [{ ...items[k], fuente: 'youtube', url: `yt${k}` }]))
    await tick(); await tick()
    assert.deepEqual(entregas, [], 'se entregó una fila antes que la de arriba')
    fake.pedidos[0].contestar(respuesta(items[0]))
    await hasta(() => entregas.length === 2, 'con la 0 lista tenían que salir la 0 y la 1')
    assert.equal(fin, 0)
    fake.pedidos[2].contestar(respuesta(items[2]))
    await hasta(() => fin === 1, 'no terminó')
    assert.deepEqual(entregas.map((e) => e[0]), [0, 1, 2, 3, 4])
    assert.deepEqual(entregas.map((e) => e[1]), items.map((t) => t.titulo), 'cada fila tiene que llevar SU tema')
    assert.deepEqual(entregas[1][2], ['yt1', items[1].url])
  })
})

test('cargarVersiones: la fila trae la elegida por nota, la nota y el motivo del backend', async () => {
  const fake = fetchFalso()
  const t = tema(0)
  await conFetch(fake, async () => {
    let g = null
    cargarVersiones([t], 'wav', { onFila: (_, x) => { g = x } })
    await hasta(() => fake.pedidos.length === 1, 'no pidió')
    const d = respuesta(t, [{ ...t, fuente: 'youtube', url: 'yt', calidad: { ok: true, grade: 'A' } }])
    d.motivo = 'No contestó a tiempo: Spotify'
    fake.pedidos[0].contestar(d)
    await hasta(() => g, 'no entregó')
    assert.deepEqual({ sel: g.sel, motivo: g.motivo, base: g.base, urls: g.opciones.map((o) => o.url) },
      { sel: 0, motivo: 'No contestó a tiempo: Spotify', base: t, urls: ['yt', t.url] })
  })
})

test('cargarVersiones: un 400 del backend deja la de SoundCloud con el mensaje del backend', async () => {
  const fake = fetchFalso()
  const t = tema(0)
  await conFetch(fake, async () => {
    let g = null
    cargarVersiones([t], 'wav', { onFila: (_, x) => { g = x } })
    await hasta(() => fake.pedidos.length === 1, 'no pidió')
    fake.pedidos[0].contestar({ exito: false, motivo: 'pedido_invalido', mensaje: 'URL de SoundCloud inválida.' }, 400)
    await hasta(() => g, 'no entregó')
    assert.deepEqual(g.opciones, [{ ...t, estacion: true }])
    assert.equal(g.sel, 0)
    assert.equal(g.motivo, 'URL de SoundCloud inválida.')
  })
})

test('cargarVersiones: el timeout de la fila deja la de SoundCloud con su motivo y sigue con las demás', async () => {
  mock.timers.enable({ apis: ['setTimeout'] })
  const fake = fetchFalso()
  const items = [tema(0), tema(1)]
  try {
    await conFetch(fake, async () => {
      const entregas = []
      let fin = 0
      cargarVersiones(items, 'wav', { onFila: (i, g) => entregas.push([i, g]), onFin: () => fin++ })
      await hasta(() => fake.pedidos.length === 2, 'no se pidieron las 2')
      fake.pedidos[1].contestar(respuesta(items[1], [{ ...items[1], fuente: 'youtube', url: 'yt1' }]))
      await tick(); await tick()
      assert.equal(entregas.length, 0)
      mock.timers.tick(TIMEOUT_FILA_MS - 1)
      await tick(); await tick()
      assert.equal(entregas.length, 0, 'cortó antes de los 45 s')
      mock.timers.tick(1)
      await hasta(() => fin === 1, 'el timeout no liberó la fila')
      assert.equal(TIMEOUT_FILA_MS, 45000)
      assert.equal(fake.pedidos[0].abortado, true, 'el pedido que tardó no se cortó')
      const [[i0, g0], [i1, g1]] = entregas
      assert.deepEqual([i0, i1], [0, 1])
      assert.deepEqual(g0.opciones, [{ ...items[0], estacion: true }])
      assert.equal(g0.motivo, 'Tardó más de 45 s en buscar versiones: queda la de SoundCloud.')
      assert.deepEqual(g1.opciones.map((o) => o.url), ['yt1', items[1].url])
    })
  } finally {
    mock.timers.reset()
  }
})

test('cargarVersiones: sin conexión la fila lo dice (no dice que tardó)', async () => {
  const t = tema(0)
  const real = globalThis.fetch
  globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'))
  try {
    let g = null
    cargarVersiones([t], 'wav', { onFila: (_, x) => { g = x } })
    await hasta(() => g, 'no entregó')
    assert.equal(g.motivo, 'No pude conectar con el servidor para buscar versiones: queda la de SoundCloud.')
  } finally {
    globalThis.fetch = real
  }
})

test('cargarVersiones: cancelar corta los pedidos en vuelo y no entrega nada más', async () => {
  const fake = fetchFalso()
  const items = Array.from({ length: 6 }, (_, i) => tema(i))
  await conFetch(fake, async () => {
    const entregas = []
    let fin = 0
    const cancelar = cargarVersiones(items, 'wav', { onFila: (i) => entregas.push(i), onFin: () => fin++ })
    await hasta(() => fake.pedidos.length === 3, 'no arrancó')
    fake.pedidos[0].contestar(respuesta(items[0]))
    await hasta(() => entregas.length === 1, 'la 0 no salió')
    await hasta(() => fake.pedidos.length === 4, 'no se pidió la 3')
    cancelar()
    await tick(); await tick()
    assert.deepEqual(fake.pedidos.map((p) => p.abortado), [false, true, true, true], 'no se cortaron los pedidos en vuelo')
    // Una respuesta que llega igual (el server ya la había mandado) no se entrega.
    fake.pedidos[1].contestar(respuesta(items[1]))
    for (let k = 0; k < 10; k++) await tick()
    assert.deepEqual(entregas, [0])
    assert.equal(fake.pedidos.length, 4, 'después de cancelar no se pide nada más')
    assert.equal(fin, 0)
  })
})

test('cargarVersiones: sin temas termina enseguida sin pedir nada', async () => {
  const fake = fetchFalso()
  await conFetch(fake, async () => {
    let fin = 0
    cargarVersiones([], 'wav', { onFila: () => assert.fail('no hay filas'), onFin: () => fin++ })
    assert.equal(fin, 1)
    assert.equal(fake.pedidos.length, 0)
  })
})
