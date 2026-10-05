// Tests de src/stationMezcla.js (f45): el orden en pantalla de la Station y cómo se muestra lo
// medido. Uso (desde frontend/):  npm run test:unidad
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const vite = await createServer({
  configFile: false, root: fileURLToPath(new URL('..', import.meta.url)), logLevel: 'error',
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom',
})
after(() => vite.close())
const { ordenVisible, filaMezcla, medidoTexto, cargarAnalisis } = await vite.ssrLoadModule('/src/stationMezcla.js')

const grupos = ['11', '22', '33', '44'].map((id) => ({ base: { video_id: id } }))
const mezcla = {
  exito: true,
  orden: [
    { video_id: '33', grupo: 'set', razon: '+1.8% BPM | 8A → 9A (vecino)' },
    { video_id: '11', grupo: 'set', razon: '-0.4% BPM | 9A → 9A (mismo)' },
    { video_id: '44', grupo: 'fuera', motivo: 'Fuera de rango de BPM: …' },
    { video_id: '22', grupo: 'sin_analisis', motivo: 'No pude analizar.' },
  ],
}

test('el default es el orden de SoundCloud, aunque ya haya un orden para mezclar', () => {
  assert.deepEqual(ordenVisible(grupos, 'soundcloud', mezcla), [0, 1, 2, 3])
  assert.deepEqual(ordenVisible(grupos, 'mezcla', null), [0, 1, 2, 3])
  assert.deepEqual(ordenVisible(grupos, 'mezcla', { exito: false, mensaje: 'x' }), [0, 1, 2, 3])
})

test('"Para mezclar" sigue el orden del backend y no pierde filas', () => {
  assert.deepEqual(ordenVisible(grupos, 'mezcla', mezcla), [2, 0, 3, 1])
  // Una fila que el orden no nombra (o que llegó después) va al final, no desaparece.
  const conOtra = [...grupos, { base: { video_id: '55' } }]
  assert.deepEqual(ordenVisible(conOtra, 'mezcla', mezcla), [2, 0, 3, 1, 4])
  // Filas que todavía no llegaron (versiones cargando): solo se ordenan las que hay.
  assert.deepEqual(ordenVisible(grupos.slice(0, 2), 'mezcla', mezcla), [0, 1])
})

test('cada fila trae su por qué o su motivo, solo en modo mezcla', () => {
  assert.equal(filaMezcla(grupos[2], 'mezcla', mezcla).razon, '+1.8% BPM | 8A → 9A (vecino)')
  assert.equal(filaMezcla(grupos[3], 'mezcla', mezcla).motivo, 'Fuera de rango de BPM: …')
  assert.equal(filaMezcla(grupos[2], 'soundcloud', mezcla), null)
})

test('lo medido: BPM con un decimal, key con "?" si es dudosa, "?" si no se pudo', () => {
  assert.deepEqual(medidoTexto({ ok: true, bpm: 128, key: '8A', key_dudosa: false }), { bpm: '128.0', key: '8A' })
  assert.deepEqual(medidoTexto({ ok: true, bpm: 127.94, key: '8A', key_dudosa: true }), { bpm: '127.9', key: '8A?' })
  assert.deepEqual(medidoTexto({ ok: false, motivo: 'x', bpm: 120 }), { bpm: '?', key: '?' })
  assert.deepEqual(medidoTexto(null), { bpm: '?', key: '?' })
})

test('cargarAnalisis: como mucho `concurrencia` pedidos a la vez, y onFin al terminar todos', async () => {
  let enVuelo = 0, maximo = 0
  const pendientes = []
  globalThis.fetch = (url) => new Promise((resolve) => {
    enVuelo++; maximo = Math.max(maximo, enVuelo)
    pendientes.push(() => { enVuelo--; resolve({ status: 200, text: async () => JSON.stringify({ ok: true, bpm: 120, ref: url }) }) })
  })
  const vistos = []
  const fin = new Promise((resolve) => cargarAnalisis(['1', '2', '3', '4', '5'], { concurrencia: 2, onUno: (r) => vistos.push(r), onFin: resolve }))
  while (vistos.length < 5) {
    await new Promise((r) => setTimeout(r, 0))
    pendientes.shift()?.()
  }
  await fin
  assert.equal(maximo, 2)
  assert.deepEqual([...vistos].sort(), ['1', '2', '3', '4', '5'])
})
