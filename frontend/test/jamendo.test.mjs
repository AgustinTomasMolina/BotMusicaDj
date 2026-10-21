// Tests de Jamendo en la pantalla (f66): la fuente tiene nombre y color propios en todos los
// mapas, la licencia se muestra como la declara Jamendo (y «no declarado» si no viene, nunca
// una inventada) y un tema que el artista no dejó bajar no ofrece "Descargar".
//
// Los resultados de Jamendo tienen la forma de `server._buscar_jamendo` (la misma que fija
// tests/test_fuentes.py::test_server_jamendo_aparece_como_una_plataforma_mas). Los valores
// esperados están escritos a mano.
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
const utils = await vite.ssrLoadModule('/src/utils.js')
const { NOMBRE_FUENTE, fromResult } = await vite.ssrLoadModule('/src/player/track.js')
const lic = await vite.ssrLoadModule('/src/licencia.js')

const jam = (extra) => ({
  titulo: 'Campo Abierto', artista: 'Coro de Niebla', duracion: 254, fuente: 'jamendo', id: '1500001',
  url: 'https://www.jamendo.com/track/1500001', stream_url: 'https://prod-1.storage.jamendo.com/?trackid=1500001&format=mp31',
  thumbnail: null, licencia: 'http://creativecommons.org/licenses/by-sa/3.0/', origen: 'https://www.jamendo.com/track/1500001',
  descargable: true, ...extra,
})

test('Jamendo tiene nombre, orden, clase de plataforma y color propios', () => {
  assert.equal(utils.FUENTE_CORTO.jamendo, 'Jamendo')
  assert.equal(utils.FUENTE_NOMBRES.jamendo, '♪ Jamendo')
  assert.equal(NOMBRE_FUENTE.jamendo, 'Jamendo', 'la barra diría "jamendo" en minúscula')
  assert.equal(utils.FUENTE_ORDEN.at(-1), 'jamendo')
  assert.equal(utils.FUENTE_PF.jamendo, 'pf-jm')
  assert.equal(utils.SRC_COLOR.jamendo, '#e46fbf')
  const otros = Object.entries(utils.SRC_COLOR).filter(([f]) => f !== 'jamendo').map(([, c]) => c)
  assert.ok(!otros.includes(utils.SRC_COLOR.jamendo), 'el color de Jamendo repite el de otra fuente')
  const pfOtros = Object.entries(utils.FUENTE_PF).filter(([f]) => f !== 'jamendo').map(([, c]) => c)
  assert.ok(!pfOtros.includes('pf-jm'), 'la clase de Jamendo repite la de otra fuente')
  // El mapa compartido (f66) conserva lo que tenían las dos copias de views.jsx y PlayerBar.jsx.
  assert.deepEqual(utils.FUENTE_PF, { youtube: 'pf-yt', soundcloud: 'pf-sc', spotify: 'pf-sp', deezer: 'pf-sp',
    ligaudio: 'pf-m1', hitplayer: 'pf-m2', jamendo: 'pf-jm' })
  assert.deepEqual(utils.nombresDeVersiones([jam(), { fuente: 'youtube' }]), ['Jamendo', 'YouTube'])
})

test('licencia: la URL de Creative Commons se nombra por su código; sin dato, «no declarado»', () => {
  assert.equal(lic.licenciaTexto('http://creativecommons.org/licenses/by-sa/3.0/'), 'CC BY-SA 3.0')
  assert.equal(lic.licenciaTexto('https://creativecommons.org/licenses/by-nc-nd/4.0/'), 'CC BY-NC-ND 4.0')
  assert.equal(lic.licenciaTexto('http://creativecommons.org/publicdomain/zero/1.0/'), 'CC0 1.0')
  // Un texto que no es una URL de CC va tal cual: no se adivina qué licencia es.
  assert.equal(lic.licenciaTexto('Licencia Arte Libre'), 'Licencia Arte Libre')
  assert.equal(lic.licenciaTexto('http://creativecommons.org/licenses/by/'), 'http://creativecommons.org/licenses/by/',
    'una URL de CC sin versión no se completa con una versión inventada')
  for (const vacio of [undefined, null, '', '   ', 'no declarado', 'No declarado', 42, {}]) {
    assert.equal(lic.licenciaTexto(vacio), 'no declarado', `sin licencia (${JSON.stringify(vacio)}) tiene que decir «no declarado»`)
  }
})

test('origen: el sitio con su link; sin dato, «no declarado» sin link', () => {
  assert.deepEqual(lic.origenTexto('https://www.jamendo.com/track/1500001'), { texto: 'jamendo.com', href: 'https://www.jamendo.com/track/1500001' })
  assert.deepEqual(lic.origenTexto('no declarado'), { texto: 'no declarado', href: null })
  assert.deepEqual(lic.origenTexto(null), { texto: 'no declarado', href: null })
  assert.deepEqual(lic.origenTexto('javascript:alert(1)'), { texto: 'javascript:alert(1)', href: null }, 'un link que no es http(s) no se vuelve clickeable')
})

test('la pastilla de licencia va en las filas de Jamendo, no en las de fuentes que no la declaran', () => {
  assert.equal(lic.muestraLicencia(jam()), true)
  assert.equal(lic.muestraLicencia(jam({ fuente: 'Jamendo' })), true)
  assert.equal(lic.muestraLicencia({ fuente: 'youtube', titulo: 'x' }), false)
  assert.equal(lic.muestraLicencia({ fuente: 'deezer' }), false)
})

test('Descargar: un tema de Jamendo que el artista no habilitó no se ofrece, y dice por qué', () => {
  assert.equal(lic.motivoSinDescarga(jam({ descargable: false })), 'El artista no habilitó la descarga en Jamendo')
  assert.equal(lic.motivoSinDescarga(jam()), null)
  // Las demás fuentes no mandan el campo: se pueden bajar (no se inventa un "no").
  assert.equal(lic.motivoSinDescarga({ fuente: 'youtube', url: 'https://www.youtube.com/watch?v=aaaaaaaaaaa' }), null)
  // La barra de abajo usa `descargable` de fromResult: sin botón para ese tema.
  assert.equal(fromResult(jam({ descargable: false }), {}).descargable, false, 'la barra ofrecería bajar un tema que el artista no dejó bajar')
  assert.equal(fromResult(jam(), {}).descargable, true)
  assert.equal(fromResult({ fuente: 'youtube', url: 'https://www.youtube.com/watch?v=aaaaaaaaaaa', titulo: 'Y' }, {}).descargable, true)
})

test('"Descargar todas" no intenta los que el artista no dejó bajar ni los Go+ de 30 s', () => {
  const a = jam({ id: '1', titulo: 'A' })
  const b = jam({ id: '2', titulo: 'B', descargable: false })
  const c = { fuente: 'soundcloud', titulo: 'C', url: 'https://soundcloud.com/x/c', solo_preview: true }
  const d = { fuente: 'youtube', titulo: 'D', url: 'https://www.youtube.com/watch?v=aaaaaaaaaaa' }
  const s = lic.separarBajables([a, b, c, d])
  assert.deepEqual([s.bajables.map((x) => x.titulo), s.soloPreview.map((x) => x.titulo), s.sinPermiso.map((x) => x.titulo)],
    [['A', 'D'], ['C'], ['B']])
})
