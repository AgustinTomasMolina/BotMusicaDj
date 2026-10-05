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
const { ordenVisible, filaMezcla, medidoTexto, cargarAnalisis, refsAnalisis, estadoMedicion, aclaracionOrden } = await vite.ssrLoadModule('/src/stationMezcla.js')

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

// f46: el panel de estado (totales de la medición y la aclaración del orden).

test('refsAnalisis: semilla + temas, sin repetir y solo ids de SoundCloud', () => {
  const items = [{ video_id: '22' }, { video_id: 33 }, { video_id: '22' }, { video_id: 'abc' }, { video_id: null }, {}]
  assert.deepEqual(refsAnalisis('11', items), ['11', '22', '33'])
  // La semilla que también es un tema recomendado se mide una vez.
  assert.deepEqual(refsAnalisis('22', items), ['22', '33'])
  // Sin semilla con id: solo los temas.
  assert.deepEqual(refsAnalisis(undefined, items), ['22', '33'])
  assert.deepEqual(refsAnalisis('0', []), [])
})

test('estadoMedicion: el total es lo que se mide (incluye la semilla) y cuenta medidos y sin medir', () => {
  const items = [{ video_id: '22' }, { video_id: '33' }]
  assert.deepEqual(estadoMedicion({}, '11', items), { total: 3, hechos: 0, medidos: 0, sinMedir: 0, incluyeSemilla: true })
  const analisis = { 11: { ok: true, bpm: 120 }, 33: { ok: false, motivo: 'x' } }
  assert.deepEqual(estadoMedicion(analisis, '11', items), { total: 3, hechos: 2, medidos: 1, sinMedir: 1, incluyeSemilla: true })
  // La semilla entre los temas: no se suma aparte ni se dice "incluye el tema original".
  assert.deepEqual(estadoMedicion({}, '22', items), { total: 2, hechos: 0, medidos: 0, sinMedir: 0, incluyeSemilla: false })
  // Sin id de semilla: tampoco.
  assert.equal(estadoMedicion({}, '', items).incluyeSemilla, false)
  // Un resultado de algo que no se mandó a medir no cuenta.
  assert.equal(estadoMedicion({ 99: { ok: true } }, '11', items).hechos, 0)
})

test('aclaracionOrden: midiendo, armando el orden, listo con su resumen, o el motivo del backend', () => {
  assert.deepEqual(aclaracionOrden({ analizando: true, mezcla: null, modo: 'soundcloud', hechos: 1, total: 3 }),
    { bloqueado: true, texto: '«Para mezclar» se activa cuando termine de medir BPM y key.' })
  assert.deepEqual(aclaracionOrden({ analizando: true, mezcla: null, modo: 'soundcloud', hechos: 3, total: 3 }),
    { bloqueado: true, texto: 'Medición terminada: armando el orden «Para mezclar»…' })
  const mezcla = { exito: true, en_set: 40, fuera: 2, sin_analisis: 1 }
  assert.deepEqual(aclaracionOrden({ analizando: false, mezcla, modo: 'soundcloud', hechos: 3, total: 3 }),
    { bloqueado: false, texto: 'Listo: podés ordenar para mezclar · 40 en el set · 2 fuera de rango de BPM · 1 sin analizar' })
  assert.deepEqual(aclaracionOrden({ analizando: false, mezcla: { exito: true, en_set: 3, fuera: 0, sin_analisis: 0 }, modo: 'mezcla', hechos: 3, total: 3 }),
    { bloqueado: false, texto: 'Ordenado para mezclar · 3 en el set' })
  const sin = { exito: false, mensaje: 'No se pudo analizar ningún tema: no hay orden para mezclar.' }
  assert.deepEqual(aclaracionOrden({ analizando: false, mezcla: sin, modo: 'soundcloud', hechos: 3, total: 3 }),
    { bloqueado: true, texto: sin.mensaje })
})
