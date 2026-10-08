// Las minionditas de la lista del set y la onda del editor (f50): lógica pura, sin React ni
// DOM, para probarla con `node --test` (test/minionda.test.mjs).
//
// POR QUÉ HAY UNA COLA: la primera vez que se pide la onda de un tema el servidor decodifica
// el archivo entero para sacar los picos (`motor.peaks`, después queda en caché). En la PC del
// dueño, con poca memoria, un set de 20 temas eran 20 decodificaciones a la vez. La cola deja
// pasar como mucho `concurrencia` pedidos juntos, comparte el mismo pedido entre quien lo pida
// dos veces (la fila y el editor del mismo tema) y guarda las respuestas para no volver a
// pedirlas al cambiar de tema.

// Reduce los picos (0..1, uno por tramo del audio) a `n` barras: el MÁXIMO de cada tramo,
// igual que el editor al dibujar su onda (un promedio aplanaría los golpes). Nada se inventa:
// sin picos no hay barras.
export function reducirPicos(picos, n) {
  if (!Array.isArray(picos) || picos.length === 0 || !(n > 0)) return []
  const total = picos.length
  const barras = Math.min(Math.floor(n), total)
  const out = new Array(barras)
  for (let b = 0; b < barras; b++) {
    const i0 = Math.floor((b * total) / barras)
    const i1 = Math.max(i0 + 1, Math.floor(((b + 1) * total) / barras))
    let m = 0
    for (let i = i0; i < i1 && i < total; i++) {
      const v = Number(picos[i])
      if (Number.isFinite(v) && v > m) m = v
    }
    out[b] = Math.min(1, m)
  }
  return out
}

// Una cola de pedidos por id con tope de concurrencia, prioridad, pedidos compartidos y caché.
//
//   const c = crearCargador({ pedir: (id) => fetch…, concurrencia: 2, max: 48 })
//   const { promesa, cancelar } = c.cargar(id, { urgente })
//
// - `urgente` pasa adelante de la cola (el tema que se abre en el editor), pero respeta el tope.
// - Dos `cargar` del mismo id comparten UN pedido; una respuesta guardada vuelve sin pedir nada.
// - `cancelar()` suelta a quien esperaba. Si nadie más espera y el pedido todavía no salió, se
//   saca de la cola (una fila que se fue de la pantalla antes de su turno no se pide). Si ya
//   salió, termina y su respuesta queda guardada.
// - Lo que `pedir` RESUELVE (también un 404 o un 422 con su motivo) se guarda; si `pedir`
//   TIRA (sin red), no: el próximo intento vuelve a pedir.
// - `max`: cuántas respuestas se guardan; se descarta la que se usó hace más tiempo.
export function crearCargador({ pedir, concurrencia = 2, max = 48 }) {
  const cache = new Map()      // id → respuesta (el orden de inserción es el de uso)
  const cola = []              // [{id, esperan: Set}] en espera de turno
  const enVuelo = new Map()    // id → {esperan: Set}
  let maxVisto = 0

  const guardar = (id, r) => {
    cache.delete(id)
    cache.set(id, r)
    while (cache.size > max) cache.delete(cache.keys().next().value)
  }

  const bombear = () => {
    while (enVuelo.size < concurrencia && cola.length) {
      const item = cola.shift()
      enVuelo.set(item.id, item)
      maxVisto = Math.max(maxVisto, enVuelo.size)
      Promise.resolve()
        .then(() => pedir(item.id))
        .then(
          (r) => { guardar(item.id, r); for (const e of item.esperan) e.ok(r) },
          (err) => { for (const e of item.esperan) e.mal(err) },
        )
        .finally(() => { enVuelo.delete(item.id); bombear() })
    }
  }

  const cargar = (id, { urgente = false } = {}) => {
    if (cache.has(id)) {
      const r = cache.get(id)
      guardar(id, r)        // usado ahora: pasa al final del orden de descarte
      return { promesa: Promise.resolve(r), cancelar: () => {} }
    }
    let espera = null
    const promesa = new Promise((ok, mal) => { espera = { ok, mal } })
    const vuelo = enVuelo.get(id)
    let item = vuelo || cola.find((x) => x.id === id)
    if (!item) {
      item = { id, esperan: new Set() }
      if (urgente) cola.unshift(item)
      else cola.push(item)
    } else if (!vuelo && urgente) {
      // Ya estaba esperando turno: un pedido urgente del mismo tema lo pasa adelante.
      cola.splice(cola.indexOf(item), 1)
      cola.unshift(item)
    }
    item.esperan.add(espera)
    bombear()
    const cancelar = () => {
      item.esperan.delete(espera)
      if (item.esperan.size === 0 && !enVuelo.has(id)) {
        const i = cola.indexOf(item)
        if (i >= 0) cola.splice(i, 1)
      }
    }
    return { promesa, cancelar }
  }

  return {
    cargar,
    // Lo que ya está guardado (o undefined), sin pedir nada.
    guardado: (id) => cache.get(id),
    // Para los tests y para diagnosticar: cuántos salieron, cuántos esperan y el pico visto.
    estado: () => ({ enVuelo: enVuelo.size, enCola: cola.length, maxEnVuelo: maxVisto, guardados: cache.size }),
  }
}
