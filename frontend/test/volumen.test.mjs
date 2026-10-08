// Tests de src/volumen.js (f50): el volumen de toda la app. Por defecto 45 % (el dueño se
// quejó de que sonaba muy fuerte), lo guardado se acuerda, y ↑ ↓ lo mueven de a 5 % sin
// disparar escribiendo ni con modificadores. Los valores esperados están escritos a mano.
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
const { leerVolumen, guardarVolumen, pasoVolumen, teclaVolumen, VOL_KEY, VOL_DEFECTO } = await vite.ssrLoadModule('/src/volumen.js')

const almacen = (inicial = {}) => {
  const datos = { ...inicial }
  return { datos, getItem: (k) => (k in datos ? datos[k] : null), setItem: (k, v) => { datos[k] = String(v) } }
}

test('por defecto el volumen es medio (45 %), no el máximo', () => {
  assert.equal(VOL_DEFECTO, 0.45)
  assert.equal(leerVolumen(almacen()), 0.45, 'sin nada guardado')
  assert.equal(leerVolumen(null), 0.45, 'sin almacenamiento')
})

test('lo guardado se acuerda; lo que no es un volumen vuelve al de fábrica', () => {
  assert.equal(VOL_KEY, 'musiflix.volumen', 'la misma clave que ya usaba la barra: lo elegido antes se respeta')
  assert.equal(leerVolumen(almacen({ [VOL_KEY]: '0.3' })), 0.3)
  assert.equal(leerVolumen(almacen({ [VOL_KEY]: '0' })), 0, 'el 0 es un volumen (silencio elegido), no "nada guardado"')
  assert.equal(leerVolumen(almacen({ [VOL_KEY]: '1' })), 1)
  for (const malo of ['', '   ', 'abc', '1.5', '-0.1', 'NaN', 'Infinity']) {
    assert.equal(leerVolumen(almacen({ [VOL_KEY]: malo })), 0.45, `«${malo}» no es un volumen`)
  }
})

test('un almacenamiento que tira (modo privado) no rompe ni al leer ni al guardar', () => {
  const roto = { getItem: () => { throw new Error('bloqueado') }, setItem: () => { throw new Error('bloqueado') } }
  assert.equal(leerVolumen(roto), 0.45)
  assert.doesNotThrow(() => guardarVolumen(roto, 0.5))
  const a = almacen()
  guardarVolumen(a, 0.5)
  assert.equal(a.datos[VOL_KEY], '0.5')
})

test('pasoVolumen: de a 5 %, sin errores de coma flotante y dentro de 0..1', () => {
  assert.equal(pasoVolumen(0.45, 1), 0.5)
  assert.equal(pasoVolumen(0.45, -1), 0.4)
  assert.equal(pasoVolumen(0.1, 1), 0.15)
  assert.equal(pasoVolumen(0.7, 1), 0.75)
  assert.equal(pasoVolumen(1, 1), 1, 'no pasa del máximo')
  assert.equal(pasoVolumen(0, -1), 0, 'no baja de cero')
  assert.equal(pasoVolumen(0.43, 1), 0.5, 'un valor fuera de la grilla se acomoda a ella')
})

const ev = (key, extra = {}) => ({ key, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, target: { tagName: 'DIV', getAttribute: () => 'slider' }, ...extra })

test('teclaVolumen: ↑ sube, ↓ baja; el resto de las teclas no es del volumen', () => {
  assert.equal(teclaVolumen(ev('ArrowUp')), 1)
  assert.equal(teclaVolumen(ev('ArrowDown')), -1)
  for (const k of ['ArrowLeft', 'ArrowRight', ' ', 'c', 'Home', 'PageUp']) assert.equal(teclaVolumen(ev(k)), null, k)
})

test('teclaVolumen: nunca con un modificador ni escribiendo ni sobre el propio deslizador', () => {
  for (const mod of ['shiftKey', 'ctrlKey', 'altKey', 'metaKey']) assert.equal(teclaVolumen(ev('ArrowUp', { [mod]: true })), null, mod)
  assert.equal(teclaVolumen(ev('ArrowUp', { target: { tagName: 'INPUT', type: 'text' } })), null, 'escribiendo el nombre de una marca')
  assert.equal(teclaVolumen(ev('ArrowDown', { target: { tagName: 'TEXTAREA' } })), null)
  assert.equal(teclaVolumen(ev('ArrowUp', { target: { tagName: 'INPUT', type: 'range' } })), null, 'el range ya mueve el volumen solo')
  assert.equal(teclaVolumen(ev('ArrowUp', { target: { tagName: 'BUTTON' } })), 1, 'con el foco en un botón del editor sí')
})
