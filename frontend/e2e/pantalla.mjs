// Los chequeos del E2E (los corre run.mjs, que arma la base de juguete y el server).
//
// Regla de todos los casos: lo que se ESPERA sale de la API (o de la base de juguete), nunca
// del código del front. El front no se importa acá: si cambia cómo dibuja un dato, el E2E se
// tiene que enterar, no copiar el cambio. Los dos formatos que sí están escritos acá (BPM con
// un decimal, duración m:ss) son el contrato de §6, no una copia del front.
//
// Sin esperas fijas: cada paso espera un ESTADO (un selector, un atributo, qué audio suena)
// con un tope, y si el tope se vence el error dice qué quedó en pantalla.

import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const AQUI = path.dirname(fileURLToPath(import.meta.url))

const ESPERA_MS = 8000

const bpm1 = (v) => (v == null ? null : Number(v).toFixed(1))
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
const json = (v) => JSON.stringify(v)

function igual(real, esperado, que) {
  if (json(real) !== json(esperado)) throw new Error(`${que}\n  pantalla: ${json(real)}\n  esperado: ${json(esperado)}`)
}

function afirmar(cond, que) {
  if (!cond) throw new Error(que)
}

// Espera de estado: lee hasta que `ok(valor)`; si se vence el tope, el error dice qué quedó.
async function hasta(leer, ok, que, ms = ESPERA_MS) {
  const t0 = Date.now()
  for (;;) {
    const v = await leer()
    if (ok(v)) return v
    if (Date.now() - t0 > ms) throw new Error(`${que} (esperé ${ms / 1000} s)\n  quedó: ${json(v)}`)
    await new Promise((r) => setTimeout(r, 50))
  }
}

const api = async (ctx, ruta) => {
  const r = await fetch(ctx.url + ruta)
  if (!r.ok) throw new Error(`${ruta} contestó ${r.status}`)
  return r.json()
}

/* ---------- página instrumentada ---------- */

// Se inyecta antes de que cargue la app:
//  - __medios: todo <audio>/<video> al que se le dio play, esté o no en el DOM (el de la barra
//    es un `new Audio()` suelto: un querySelectorAll('audio') no lo ve). Así "un solo audio
//    sonando" cuenta TODOS los que suenan, no solo los visibles.
//  - __datosTrack: lee los chips BPM / Key / Energía / Dur de una fila de la radio.
function instrumentar() {
  window.__medios = new Set()
  const play = HTMLMediaElement.prototype.play
  HTMLMediaElement.prototype.play = function (...args) {
    window.__medios.add(this)
    return play.apply(this, args)
  }
  const visible = (el) => {
    if (!el) return false
    const cs = getComputedStyle(el)
    const r = el.getBoundingClientRect()
    return cs.display !== 'none' && cs.visibility !== 'hidden' && Number(cs.opacity) > 0 && r.width > 0 && r.height > 0
  }
  window.__visible = visible
  window.__datosTrack = (el) => {
    const o = {}
    el.querySelectorAll('.rdatos > .mb').forEach((mb) => {
      const label = mb.querySelector('.mb-label')?.textContent
      if (label === 'Key') {
        const duda = mb.querySelector('.rduda')
        o.key = {
          camelot: mb.querySelector('b')?.textContent ?? null,
          clasica: mb.querySelector('.rkey-clasica')?.textContent ?? null,
          // El `?` cuenta si se VE y dice por qué: un span oculto por CSS no avisa nada.
          duda: duda && duda.textContent === '?' && visible(duda) ? duda.getAttribute('aria-label') : null,
        }
      } else if (label) {
        o[label] = mb.querySelector('b')?.textContent ?? null
      }
    })
    return { BPM: o.BPM ?? null, key: o.key ?? null, 'Energía': o['Energía'] ?? null, Dur: o.Dur ?? null }
  }
}

const erroresDe = new WeakMap()

async function nuevaPagina(ctx) {
  const page = await ctx.browser.newPage()
  await page.setViewport({ width: 1440, height: 900 })
  const errores = []
  page.on('pageerror', (e) => errores.push(String(e)))
  // Dos errores de consola no son del front y se ignoran: los 404 de /api/cover (track sin
  // carátula: el front dibuja el placeholder) y la consola en vivo /ws/console, que depende
  // de que el Python del server tenga una librería de WebSocket (uvicorn[standard]).
  // Cualquier otro error cuenta.
  page.on('console', (m) => {
    const t = m.text()
    if (m.type() === 'error' && !/Failed to load resource/.test(t) && !/\/ws\/console/.test(t)) errores.push(t)
  })
  await page.evaluateOnNewDocument(instrumentar)
  erroresDe.set(page, errores)
  return page
}

// Qué suena: la ruta de cada medio que está reproduciendo (sin pausa, sin terminar, sin error).
const sonando = (page) => page.evaluate(() =>
  // Un medio con `error` no suena aunque `paused` siga en false (Chrome no lo pausa al fallar).
  [...window.__medios].filter((m) => !m.paused && !m.ended && !m.error).map((m) => new URL(m.currentSrc || m.src, location.href).pathname))

/* ---------- navegación ---------- */

async function abrirHome(page, ctx) {
  await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.lib-card', { timeout: ESPERA_MS })
}

async function abrirRadio(page, ctx, { navegar = true } = {}) {
  if (navegar) await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('button[aria-label="Radio DJ"]', { timeout: ESPERA_MS })
  await page.click('button[aria-label="Radio DJ"]')
  await page.waitForSelector('.rsem', { timeout: ESPERA_MS })
}

async function elegirSemilla(page, titulo) {
  const hay = await page.evaluate((t) => {
    const b = [...document.querySelectorAll('.rsem')].find((x) => x.querySelector('.rsem-titulo')?.textContent === t)
    b?.click()
    return !!b
  }, titulo)
  afirmar(hay, `no hay una semilla «${titulo}» en la lista`)
  await hasta(() => page.$eval('.rarmar', (b) => b.getAttribute('aria-disabled')), (v) => v === 'false',
    `elegí «${titulo}» y «Armar el set» sigue con aria-disabled`)
}

async function armarSet(page, { teclado = false } = {}) {
  const respuesta = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/radio/set', { timeout: ESPERA_MS })
  if (teclado) {
    await page.focus('.rarmar')
    await page.keyboard.press('Enter')
  } else {
    await page.click('.rarmar')
  }
  const r = await respuesta
  await hasta(() => page.evaluate(() => ({
    armando: /Armando/.test(document.querySelector('.rarmar')?.textContent || ''),
    pasos: document.querySelectorAll('.rpaso').length,
  })), (v) => !v.armando && v.pasos > 0, 'el set no se dibujó después de armarlo')
  // Lo que contestó /api/radio/set a ESTE pedido: el set que la pantalla está mostrando.
  return r.json()
}

async function playEnHome(page, titulo) {
  const sel = `.lib-play[aria-label="Reproducir ${titulo}"]`
  await page.waitForSelector(sel, { timeout: ESPERA_MS })
  // Al centro de la pantalla antes del click: con la barra ya abierta, una tarjeta del borde
  // de abajo queda "a la vista" para el navegador pero tapada por la barra fija, y el click
  // real (el mouse en esas coordenadas) caería sobre la barra.
  await page.$eval(sel, (b) => b.scrollIntoView({ block: 'center' }))
  await page.click(sel)
  return hasta(() => leerBarra(page), (d) => d && d.titulo === titulo && d.estado === 'playing',
    `le di play a «${titulo}» en la home y la barra no lo muestra sonando`)
}

function leerBarra(page) {
  return page.evaluate(() => {
    const d = document.querySelector('.deck')
    if (!d) return null
    const chips = {}
    d.querySelectorAll('.deck-data .mb').forEach((mb) => {
      const label = mb.querySelector('.mb-label')?.textContent
      if (label === 'Key') {
        chips.Key = {
          camelot: mb.querySelector('b')?.textContent ?? null,
          clasica: mb.querySelector('.rkey-clasica')?.textContent ?? null,
        }
      } else if (label) {
        chips[label] = mb.querySelector('b')?.textContent ?? null
      }
    })
    return {
      titulo: d.querySelector('.deck-title')?.textContent ?? null,
      estado: (d.className.match(/\bis-(\w+)/) || [])[1] || null,
      chips,
      boton: d.querySelector('.deck-play')?.getAttribute('aria-label') ?? null,
      anuncio: d.querySelector('[role=status][aria-live]')?.textContent ?? null,
    }
  })
}

// Botones de los pasos de la radio que dicen "Pausar".
const pasosEnPausar = (page) => page.$$eval('.rpaso .rplay', (bs) =>
  bs.map((b) => b.getAttribute('aria-label')).filter((l) => /^Pausar/.test(l || '')))

/* ---------- lo que se espera, sacado de la API ---------- */

function esperadoDatos(t, leyenda) {
  const hayKey = !!(t.camelot || t.tonalidad)
  return {
    BPM: t.bpm == null ? '—' : bpm1(t.bpm),
    // Sin Camelot ni clásica no hay key y tampoco `?` (no se duda de algo que no está).
    key: hayKey
      ? { camelot: t.camelot || '—', clasica: t.tonalidad || '—', duda: t.key_dudosa ? leyenda : null }
      : { camelot: '—', clasica: null, duda: null },
    'Energía': t.energia_pct == null ? '—' : String(t.energia_pct),
    Dur: t.dur == null ? '—' : mmss(t.dur),
  }
}

// "Título — Artista" (sin artista, solo el título) para los anuncios de la barra.
const quien = (t) => `${t.titulo}${t.artista ? ` — ${t.artista}` : ''}`

async function semillaUno(ctx) {
  const lib = await api(ctx, '/api/radio/biblioteca')
  const uno = lib.tracks.find((t) => t.titulo === 'Uno')
  afirmar(uno, '/api/radio/biblioteca no trae el track «Uno» de la base de juguete')
  return { lib, uno }
}

/* ---------- sets guardados (tarea 16) ---------- */

// Pedido a la API con cualquier método; devuelve {status, data} sin tirar por un 4xx: los
// casos comparan el motivo que manda el backend.
async function apiPedir(ctx, ruta, metodo = 'GET', cuerpo) {
  const init = { method: metodo }
  if (cuerpo !== undefined) { init.headers = { 'Content-Type': 'application/json' }; init.body = JSON.stringify(cuerpo) }
  const r = await fetch(ctx.url + ruta, init)
  const txt = await r.text()
  let data = null
  try { data = JSON.parse(txt) } catch { data = { texto: txt } }
  return { status: r.status, data }
}

const setGuardadoApi = async (ctx, id) => {
  const r = await apiPedir(ctx, `/api/radio/sets/${id}`)
  afirmar(r.status === 200 && r.data.set, `GET /api/radio/sets/${id} contestó ${r.status}: ${json(r.data)}`)
  return r.data.set
}

// Un re-escaneo simulado sobre la base de juguete (base_mutar.py), con el server corriendo.
function mutarBase(ctx, ...args) {
  const r = spawnSync(ctx.python, [path.join(AQUI, 'base_mutar.py'), args[0], ctx.base.djradio_db, ...args.slice(1)], { encoding: 'utf8' })
  if (r.error || r.status !== 0) throw new Error(`base_mutar.py ${args[0]} falló: ${r.error ? r.error.message : (r.stderr || r.stdout)}`)
  return JSON.parse(r.stdout)
}

// Guarda POR LA API el set de «Uno», para los casos que no prueban el botón de guardar: con
// la huella y los ids del set que devuelve /api/radio/set, igual que la pantalla.
async function guardarUnoPorApi(ctx, nombre) {
  const { uno } = await semillaUno(ctx)
  const set = await api(ctx, `/api/radio/set?track=${encodeURIComponent(uno.id)}`)
  const r = await apiPedir(ctx, '/api/radio/sets', 'POST', {
    track: uno.id, ...set.config, esperado: set.pasos.map((p) => p.track.id), huella: set.huella, ...(nombre ? { nombre } : {}),
  })
  afirmar(r.status === 201, `POST /api/radio/sets contestó ${r.status}: ${json(r.data)}`)
  return r.data.set
}

// Abre desde la lista de «Sets guardados» el set `id` y espera a ver su cabecera.
async function abrirGuardado(page, id) {
  const respuesta = page.waitForResponse((r) => new URL(r.url()).pathname === `/api/radio/sets/${id}` && r.request().method() === 'GET', { timeout: ESPERA_MS })
  const hay = await hasta(() => page.evaluate((i) => {
    const b = [...document.querySelectorAll('.rsg')].find((x) => x.querySelector('.rsg-id')?.textContent === `#${i}`)
    if (!b) return false
    b.click()
    return true
  }, id), (v) => v, `la lista de sets guardados no tiene el #${id}`)
  afirmar(hay, `no encontré el set #${id} en la lista`)
  await respuesta
  await hasta(() => page.evaluate(() => document.querySelector('.rguardado-h .mono')?.textContent ?? null),
    (v) => v === `#${id}`, `abrí el set #${id} y la cabecera no dice cuál es`)
}

// Lo que muestra el panel del set con un set guardado abierto.
function leerGuardado(page) {
  return page.evaluate(() => {
    const resumenDe = (el) => {
      if (!el) return null
      const o = {}
      el.querySelectorAll('[data-k]').forEach((x) => { if (x.dataset.k !== 'calificadas') o[x.dataset.k] = Number(x.querySelector('b')?.textContent) })
      return o
    }
    return {
      id: document.querySelector('.rguardado-h .mono')?.textContent ?? null,
      nombre: document.querySelector('.rguardado-nombre')?.textContent ?? null,
      fecha: document.querySelector('.rguardado-fecha time')?.getAttribute('datetime') ?? null,
      calificadas: document.querySelector('.rguardado .rresumen-cuenta')?.textContent ?? null,
      resumen: resumenDe(document.querySelector('.rguardado .rresumen')),
      pasos: [...document.querySelectorAll('.rpaso')].map((p) => ({
        n: p.querySelector('.rpaso-n')?.textContent ?? null,
        titulo: p.querySelector('.rpaso-titulo')?.textContent ?? null,
        artista: p.querySelector('.rpaso-artista')?.textContent ?? null,
        motivo: p.querySelector('.rpaso-why .mono')?.textContent ?? null,
        semilla: !!p.querySelector('.rtag-seed'),
        datos: window.__datosTrack(p),
      })),
      transiciones: [...document.querySelectorAll('.rcal')].map((c) => ({
        n: Number(c.dataset.n),
        sel: c.querySelector('input[type=radio]:checked')?.value ?? null,
        motivo: c.querySelector('.rcal-motivo input')?.value ?? null,
      })),
      aviso: document.querySelector('.rsets-aviso')?.textContent ?? null,
    }
  })
}

// Lo que el set guardado de la API dice de cada paso, con la forma de `leerGuardado`.
const esperadoPasos = (s) => s.pasos.map((p) => ({
  n: String(p.n), titulo: p.track.titulo, artista: p.track.artista || '—', motivo: p.motivo,
  semilla: p.es_semilla, datos: esperadoDatos(p.track, s.leyenda_key),
}))

// Calificación de la transición `n` según la API, y el resumen.
async function calificacionApi(ctx, id, n) {
  const s = await setGuardadoApi(ctx, id)
  const t = s.transiciones.find((x) => x.n === n)
  return { calificacion: t.calificacion, motivo: t.motivo, resumen: s.resumen }
}

const clickNivel = (page, n, nivel) => page.click(`.rcal[data-n="${n}"] .rcal-opt.is-${nivel}`)

// Intercepta los pedidos de la página: `manejar(req)` devuelve true si se ocupó de ese pedido
// (abortarlo, retenerlo, contestarlo); el resto sigue de largo.
async function interceptar(page, manejar) {
  await page.setRequestInterception(true)
  page.on('request', (req) => {
    if (req.isInterceptResolutionHandled()) return
    Promise.resolve(manejar(req)).then((hecho) => { if (!hecho) req.continue().catch(() => {}) })
  })
}
const esPut = (req, id, n) => req.method() === 'PUT' && new URL(req.url()).pathname === `/api/radio/sets/${id}/transiciones/${n}`

// Lo que muestra el control de la transición `n`: el nivel marcado, su clase y el estado.
const controlDe = (page, n) => page.evaluate((i) => {
  const c = document.querySelector(`.rcal[data-n="${i}"]`)
  return c ? {
    sel: c.querySelector('input[type=radio]:checked')?.value ?? null,
    clase: (c.className.match(/\bis-(ok|regular|mala)\b/) || [])[1] ?? null,
    estado: c.querySelector('.rcal-estado')?.textContent ?? null,
    error: c.querySelector('.rcal-error')?.textContent ?? null,
    motivo: !!c.querySelector('.rcal-motivo input'),
    falta: !!c.querySelector('.rcal-falta'),
  } : null
}, n)

// Toca «Exportar a Rekordbox» y devuelve lo que bajó el navegador (nombre y bytes).
async function exportarConBoton(page, ctx) {
  const dir = fs.mkdtempSync(path.join(ctx.tmp, 'descargas-'))
  const cdp = await page.createCDPSession()
  await cdp.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: dir, eventsEnabled: true })
  const descargas = []
  cdp.on('Browser.downloadWillBegin', (e) => descargas.push({ guid: e.guid, nombre: e.suggestedFilename, estado: 'empezó' }))
  cdp.on('Browser.downloadProgress', (e) => { const d = descargas.find((x) => x.guid === e.guid); if (d) d.estado = e.state })
  await page.click('.rexportar')
  const [d] = await hasta(async () => descargas, (ds) => ds.length > 0 && ds.every((x) => x.estado === 'completed' || x.estado === 'canceled'),
    'tocar «Exportar a Rekordbox» no terminó ninguna descarga')
  igual(d.estado, 'completed', 'estado de la descarga')
  return { nombre: d.nombre, bytes: fs.readFileSync(path.join(dir, d.nombre)) }
}

const CASOS_SETS = [

  ['sets: guardar el set que se ve = GET /api/radio/sets/{id} y la cabecera dice cuál es', async (page, ctx) => {
    await abrirRadio(page, ctx)
    await elegirSemilla(page, 'Uno')
    const visto = await armarSet(page)
    const antes = await leerGuardado(page)
    afirmar(antes.transiciones.length === 0, `sin guardar no tiene que haber controles de calificación: ${json(antes.transiciones)}`)
    await page.click('.rguardar')
    await page.waitForSelector('#r-guardar-nombre', { timeout: ESPERA_MS })
    const nombre = `Prueba E2E ñ ${Date.now()}`
    await page.type('#r-guardar-nombre', nombre)
    const pedido = page.waitForRequest((q) => new URL(q.url()).pathname === '/api/radio/sets' && q.method() === 'POST', { timeout: ESPERA_MS })
    const respuesta = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/radio/sets' && r.request().method() === 'POST', { timeout: ESPERA_MS })
    await page.keyboard.press('Enter')
    const cuerpo = JSON.parse((await pedido).postData() || '{}')
    // Lo que viaja es lo que se VE: los ids de los pasos del set armado y su huella.
    igual({ esperado: cuerpo.esperado, huella: cuerpo.huella, nombre: cuerpo.nombre },
      { esperado: visto.pasos.map((p) => p.track.id), huella: visto.huella, nombre }, 'el POST no manda los ids y la huella del set en pantalla')
    const r = await respuesta
    igual(r.status(), 201, 'estado del POST /api/radio/sets')
    const id = (await r.json()).set.id
    const s = await setGuardadoApi(ctx, id)
    const real = await hasta(() => leerGuardado(page), (v) => v.id === `#${id}`, 'después de guardar, la cabecera no dice qué set guardado se mira')
    igual(real.nombre, `«${s.nombre}»`, 'el nombre en la cabecera')
    igual(real.fecha, s.guardado, 'la fecha de la cabecera (datetime) no es la de la API')
    igual(real.pasos, esperadoPasos(s), 'los pasos en pantalla no son la foto de GET /api/radio/sets/{id}')
    // Y la foto es el set que se había armado: mismos tracks, mismos porqués, mismos datos.
    igual(esperadoPasos(s), visto.pasos.map((p) => ({ n: String(p.n), titulo: p.track.titulo, artista: p.track.artista || '—', motivo: p.motivo, semilla: p.es_semilla, datos: esperadoDatos(p.track, visto.leyenda_key) })),
      'lo guardado no es lo que se veía')
    igual(real.transiciones, s.transiciones.map((t) => ({ n: t.n, sel: null, motivo: null })), 'un control de calificación por transición, todos sin calificar')
    igual(real.resumen, s.resumen, 'el resumen de la cabecera no es el de la API')
    afirmar(/#\d+/.test(real.aviso || '') && real.aviso.includes(`#${id}`), `la región viva no avisó que se guardó: ${json(real.aviso)}`)
    const foco = await page.evaluate(() => document.activeElement?.id ?? null)
    igual(foco, 'r-guardado-h', 'después de guardar el foco tiene que ir a la cabecera del set guardado')
    const enLista = await hasta(() => page.$$eval('.rsg', (bs) => bs.map((b) => ({ id: b.querySelector('.rsg-id')?.textContent, abierto: b.getAttribute('aria-pressed') }))),
      (v) => v.some((x) => x.id === `#${id}`), `el set #${id} no aparece en la lista`)
    igual(enLista.filter((x) => x.abierto === 'true').map((x) => x.id), [`#${id}`], 'la lista no marca como abierto al set recién guardado')
  }],

  ['sets: la lista = GET /api/radio/sets (nombre, fecha, semilla, largo, resumen)', async (page, ctx) => {
    const s = await guardarUnoPorApi(ctx, `Lista ${Date.now()}`)
    await apiPedir(ctx, `/api/radio/sets/${s.id}/transiciones/1`, 'PUT', { calificacion: 'regular', motivo: null })
    const lista = (await api(ctx, '/api/radio/sets')).sets
    await abrirRadio(page, ctx)
    const real = await hasta(() => page.$$eval('.rsg', (bs) => bs.map((b) => {
      const o = {}
      b.querySelectorAll('.rresumen [data-k]').forEach((x) => { o[x.dataset.k] = Number(x.querySelector('b')?.textContent) })
      return {
        id: b.querySelector('.rsg-id')?.textContent ?? null,
        nombre: b.querySelector('.rsg-nombre')?.textContent ?? null,
        fecha: b.querySelector('time')?.getAttribute('datetime') ?? null,
        semilla: b.querySelector('[data-k=semilla]')?.textContent ?? null,
        largo: b.querySelector('[data-k=largo]')?.textContent ?? null,
        resumen: o,
      }
    })), (v) => v.length === lista.length, `la lista no tiene los ${lista.length} sets de la API`)
    igual(real, lista.map((x) => ({
      id: `#${x.id}`, nombre: x.nombre ?? 'Sin nombre', fecha: x.guardado, semilla: `desde ${x.semilla}`,
      largo: `${x.total} de ${x.pedidos} tracks`, resumen: x.resumen,
    })), 'la lista de sets guardados no dice lo mismo que GET /api/radio/sets')
    afirmar(lista.find((x) => x.id === s.id)?.resumen.regular === 1, 'el set calificado por la API no quedó con 1 regular: el caso no probaría el resumen')
  }],

  ['sets: calificar OK / regular / mala (mala sin motivo no se guarda y lo dice)', async (page, ctx) => {
    const s = await guardarUnoPorApi(ctx, `Calificar ${Date.now()}`)
    afirmar(s.transiciones.length >= 3, `la base de juguete da ${s.transiciones.length} transiciones: hacen falta 3 para las tres escalas`)
    await abrirRadio(page, ctx)
    await abrirGuardado(page, s.id)
    // El control de 3 niveles es un grupo de radios con nombre: dice qué transición califica.
    const grupos = await page.$$eval('.rcal [role=radiogroup]', (gs) => gs.map((g) => g.getAttribute('aria-label')))
    igual(grupos, s.transiciones.map((t) => `Calificación de la transición ${t.desde} → ${t.hasta} (${s.pasos[t.desde - 1].track.titulo} → ${s.pasos[t.hasta - 1].track.titulo})`),
      'el nombre accesible de cada grupo de calificación')

    // OK con el mouse: se guarda al elegir.
    await clickNivel(page, 1, 'ok')
    await hasta(() => calificacionApi(ctx, s.id, 1), (v) => v.calificacion === 'ok', 'elegí OK en la transición 1 y la API no la tiene como ok')
    // Regular con el teclado (Espacio sobre el radio).
    await page.focus('.rcal[data-n="2"] input[value=regular]')
    await page.keyboard.press('Space')
    await hasta(() => calificacionApi(ctx, s.id, 2), (v) => v.calificacion === 'regular', 'elegí Regular con el teclado en la transición 2 y la API no la tiene')

    // Mala sin motivo: no se manda nada, el foco va al motivo y la pantalla dice qué falta.
    const puts = []
    page.on('request', (q) => { if (q.method() === 'PUT' && /\/transiciones\/3$/.test(new URL(q.url()).pathname)) puts.push(q.url()) })
    await clickNivel(page, 3, 'mala')
    const pendiente = await hasta(() => page.evaluate(() => ({
      foco: document.activeElement?.id ?? null,
      falta: document.querySelector('.rcal[data-n="3"] .rcal-falta')?.textContent ?? null,
    })), (v) => v.foco && v.falta, 'elegí Mala sin motivo y no se abrió el campo con el foco ni el aviso de qué falta')
    afirmar(/motivo/.test(pendiente.falta), `el aviso de «mala» no habla del motivo: ${json(pendiente.falta)}`)
    igual(pendiente.foco, `rcal-${s.id}-3-motivo`, 'el foco al elegir Mala')
    igual((await controlDe(page, 3)).estado, 'sin guardar', 'una Mala que espera motivo no puede decir «guardada» ni «sin calificar»')
    await page.evaluate(() => fetch('/api/radio/biblioteca').then((r) => r.text()))
    igual(puts, [], 'elegir Mala sin motivo mandó la calificación igual')
    // Enter con el campo vacío: la API la rechaza y la pantalla muestra SU motivo.
    const esperadoError = (await apiPedir(ctx, `/api/radio/sets/${s.id}/transiciones/3`, 'PUT', { calificacion: 'mala', motivo: '' })).data.error
    afirmar(esperadoError, 'la API aceptó una mala sin motivo: el caso no tiene qué probar')
    await page.keyboard.press('Enter')
    const err = await hasta(() => page.evaluate(() => document.querySelector('.rcal[data-n="3"] .rcal-error[role=alert]')?.textContent ?? null),
      (v) => v !== null, 'Enter con el motivo vacío no mostró por qué no se guardó')
    igual(err, `No se guardó: ${esperadoError}`, 'el error de mala sin motivo no es el de la API')
    igual((await calificacionApi(ctx, s.id, 3)).calificacion, null, 'mala sin motivo quedó guardada')
    // Con motivo sí. Los espacios de las puntas no viajan (el front recorta; la API también).
    await page.type(`#rcal-${s.id}-3-motivo`, '  choque de bajos  ')
    const put3 = page.waitForRequest((q) => esPut(q, s.id, 3), { timeout: ESPERA_MS })
    await page.keyboard.press('Enter')
    igual(JSON.parse((await put3).postData() || '{}').motivo, 'choque de bajos', 'el motivo viajó sin recortar')
    const t3 = await hasta(() => calificacionApi(ctx, s.id, 3), (v) => v.calificacion === 'mala', 'mala con motivo no quedó guardada')
    igual(t3.motivo, 'choque de bajos', 'el motivo guardado')

    const real = await hasta(() => leerGuardado(page), (v) => json(v.resumen) === json(t3.resumen) && v.transiciones[2]?.sel === 'mala' && v.transiciones[2]?.motivo === 'choque de bajos',
      'el resumen o la transición 3 en pantalla no llegaron a lo que tiene la API')
    igual(t3.resumen, { ok: 1, regular: 1, mala: 1, sin_calificar: s.transiciones.length - 3 }, 'el resumen de la API después de las tres')
    igual(real.transiciones.slice(0, 3), [{ n: 1, sel: 'ok', motivo: null }, { n: 2, sel: 'regular', motivo: null }, { n: 3, sel: 'mala', motivo: 'choque de bajos' }],
      'lo que muestran los tres controles')
    igual(real.calificadas, `3 de ${s.transiciones.length} transiciones calificadas`, 'la cuenta de calificadas')
    afirmar((real.aviso || '').includes('3 → 4: mala'), `la región viva no avisó la última calificación: ${json(real.aviso)}`)
    const errores = await page.$$eval('.rcal-error', (es) => es.length)
    igual(errores, 0, 'quedó un error pegado después de guardar bien')
  }],

  ['sets: cambiar y quitar una calificación', async (page, ctx) => {
    const s = await guardarUnoPorApi(ctx, `Cambiar ${Date.now()}`)
    await abrirRadio(page, ctx)
    await abrirGuardado(page, s.id)
    await clickNivel(page, 1, 'ok')
    await hasta(() => calificacionApi(ctx, s.id, 1), (v) => v.calificacion === 'ok', 'OK en la transición 1 no quedó guardada')
    await clickNivel(page, 1, 'regular')
    await hasta(() => calificacionApi(ctx, s.id, 1), (v) => v.calificacion === 'regular', 'cambiar de OK a Regular no quedó en la API')
    await hasta(() => page.$eval('.rcal[data-n="1"] .rcal-quitar', () => true).catch(() => false), (v) => v, 'una transición calificada no ofrece «Quitar»')
    await page.click('.rcal[data-n="1"] .rcal-quitar')
    const t = await hasta(() => calificacionApi(ctx, s.id, 1), (v) => v.calificacion === null, '«Quitar» no la dejó sin calificar en la API')
    igual(t.resumen.sin_calificar, s.transiciones.length, 'el resumen de la API después de quitar')
    const real = await hasta(() => leerGuardado(page), (v) => json(v.resumen) === json(t.resumen) && v.transiciones[0].sel === null,
      'después de quitar, la pantalla sigue mostrando la calificación o el resumen viejo')
    igual(real.transiciones[0], { n: 1, sel: null, motivo: null }, 'el control de la transición 1 después de quitar')
    igual(await page.$('.rcal[data-n="1"] .rcal-quitar'), null, '«Quitar» sigue en una transición sin calificar')
    afirmar((real.aviso || '').includes('1 → 2: sin calificar'), `la región viva no avisó que se quitó: ${json(real.aviso)}`)
  }],

  ['sets: un set guardado se ve con su FOTO aunque después cambie el BPM en la base', async (page, ctx) => {
    const s = await guardarUnoPorApi(ctx, `Foto ${Date.now()}`)
    const paso = s.pasos[1].track
    const nuevo = Math.round((paso.bpm + 0.7) * 10) / 10
    const { antes } = mutarBase(ctx, 'bpm', paso.titulo, String(nuevo))
    try {
      // El cambio es real: la biblioteca de hoy ya dice el BPM nuevo.
      const hoy = (await api(ctx, '/api/radio/biblioteca')).tracks.find((t) => t.titulo === paso.titulo)
      igual(bpm1(hoy.bpm), bpm1(nuevo), 'la biblioteca no tomó el BPM cambiado (el caso no probaría nada)')
      afirmar(bpm1(nuevo) !== bpm1(paso.bpm), 'el BPM nuevo se dibuja igual que el viejo')
      await abrirRadio(page, ctx)
      await abrirGuardado(page, s.id)
      const real = await leerGuardado(page)
      igual(real.pasos, esperadoPasos(s), 'el set guardado no se ve como la foto que se guardó')
      igual(real.pasos[1].datos.BPM, bpm1(paso.bpm), `el paso 2 («${paso.titulo}») muestra el BPM de hoy y no el de la foto`)
    } finally {
      mutarBase(ctx, 'bpm', paso.titulo, String(antes))
    }
  }],

  ['sets: un paso que ya no está en la biblioteca se ve marcado y no se puede reproducir', async (page, ctx) => {
    const s = await guardarUnoPorApi(ctx, `Falta ${Date.now()}`)
    const paso = s.pasos[1].track
    const respaldo = path.join(ctx.tmp, `respaldo-${Date.now()}.json`)
    mutarBase(ctx, 'ocultar', paso.titulo, respaldo)
    try {
      const hoy = await setGuardadoApi(ctx, s.id)
      igual(hoy.pasos.map((p) => p.track.en_biblioteca), s.pasos.map((_, i) => i !== 1), 'la API no marca el paso 2 como fuera de la biblioteca (el caso no probaría nada)')
      await abrirRadio(page, ctx)
      await abrirGuardado(page, s.id)
      const filas = await page.$$eval('.rpaso', (ps) => ps.map((p) => ({
        falta: p.querySelector('.rtag-falta')?.textContent ?? null,
        play: p.querySelector('.rpaso-row button.rplay')?.getAttribute('aria-label') ?? null,
      })))
      igual(filas, hoy.pasos.map((p) => (p.track.en_biblioteca
        ? { falta: null, play: `Reproducir ${p.track.titulo}` }
        : { falta: 'ya no está en la biblioteca', play: null })), 'los pasos: el que falta, marcado y sin botón; el resto, reproducibles')
      igual((await leerGuardado(page)).pasos, esperadoPasos(hoy), 'el paso que falta tiene que verse entero, con los datos de la foto')
      // El hueco del botón no reproduce nada.
      await page.click('.rpaso:nth-child(2) .rplay')
      await page.evaluate(() => fetch('/api/radio/biblioteca').then((r) => r.text()))
      igual(await sonando(page), [], 'tocar el paso que ya no está hizo sonar algo')
      const nota = await page.$eval('.rguardado', (g) => g.textContent)
      afirmar(nota.includes('ya no está en la biblioteca del'), `la cabecera no avisa que falta un track: ${json(nota)}`)
      // Los otros sí suenan.
      await page.click('.rpaso:nth-child(1) .rplay')
      await hasta(() => sonando(page), (v) => json(v) === json([`/api/radio/audio/${s.pasos[0].track.id}`]), 'el paso 1 del set guardado no suena')
    } finally {
      mutarBase(ctx, 'restaurar', respaldo)
    }
  }],

  ['sets: renombrar y borrar (con confirmación)', async (page, ctx) => {
    const s = await guardarUnoPorApi(ctx, null)
    await abrirRadio(page, ctx)
    await abrirGuardado(page, s.id)
    igual((await leerGuardado(page)).nombre, 'sin nombre', 'un set sin nombre')
    await page.click('.rguardado-acciones .btn:first-child')
    await page.waitForSelector('#r-renombrar', { timeout: ESPERA_MS })
    const nombre = `Renombrado ${Date.now()}`
    await page.type('#r-renombrar', nombre)
    await page.keyboard.press('Enter')
    await hasta(() => setGuardadoApi(ctx, s.id), (v) => v.nombre === nombre, 'renombrar no llegó a la API')
    await hasta(() => leerGuardado(page), (v) => v.nombre === `«${nombre}»`, 'la cabecera no muestra el nombre nuevo')
    await hasta(() => page.$$eval('.rsg', (bs) => bs.map((b) => [b.querySelector('.rsg-id')?.textContent, b.querySelector('.rsg-nombre')?.textContent])),
      (v) => v.some(([i, n]) => i === `#${s.id}` && n === nombre), 'la lista no muestra el nombre nuevo')

    // Borrar pide confirmación: Cancelar no borra nada.
    await page.click('.rborrar')
    await page.waitForSelector('[role=alertdialog]', { timeout: ESPERA_MS })
    const dialogo = await page.$eval('[role=alertdialog]', (d) => ({ titulo: d.querySelector('.dialog-title')?.textContent, foco: document.activeElement?.textContent }))
    igual(dialogo, { titulo: `¿Borrar el set guardado #${s.id} («${nombre}»)?`, foco: 'Cancelar' }, 'el diálogo de borrar (y el foco empieza en Cancelar)')
    await page.keyboard.press('Enter')   // Enter sobre «Cancelar»
    await hasta(() => page.$('[role=alertdialog]'), (v) => v === null, 'Cancelar no cerró el diálogo')
    igual((await apiPedir(ctx, `/api/radio/sets/${s.id}`)).status, 200, 'Cancelar borró el set igual')
    await page.click('.rborrar')
    await page.waitForSelector('.rborrar-ok', { timeout: ESPERA_MS })
    await page.click('.rborrar-ok')
    await hasta(async () => (await apiPedir(ctx, `/api/radio/sets/${s.id}`)).status, (v) => v === 404, 'confirmar no borró el set en la API')
    await hasta(() => page.evaluate((i) => ({
      cabecera: !!document.querySelector('.rguardado'),
      enLista: [...document.querySelectorAll('.rsg-id')].some((x) => x.textContent === `#${i}`),
    }), s.id), (v) => !v.cabecera && !v.enLista, 'el set borrado sigue en pantalla o en la lista')
    afirmar(((await leerGuardado(page)).aviso || '').includes(`#${s.id} borrado`), 'la región viva no avisó el borrado')
  }],

  ['sets: si el set cambió entre armar y guardar, 409 con el motivo y «Re-armar el set»', async (page, ctx) => {
    await abrirRadio(page, ctx)
    await elegirSemilla(page, 'Uno')
    const visto = await armarSet(page)
    // Después de armar se toca un control: «Re-armar» tiene que usar lo que el set USÓ, no
    // lo que hoy dicen los controles.
    const largoHoy = String(Math.max(1, Number(visto.config.largo) - 17))
    afirmar(largoHoy !== String(visto.config.largo), 'el largo de los controles no difiere del del set: el caso no probaría nada')
    await page.click('#r-cfg-largo', { clickCount: 3 })
    await page.type('#r-cfg-largo', largoHoy)
    await hasta(() => page.$eval('#r-cfg-largo', (i) => i.value), (v) => v === largoHoy, 'no pude cambiar el largo de los controles')
    const paso = visto.pasos[1].track
    const nuevo = Math.round((paso.bpm + 0.3) * 10) / 10
    const antesLista = (await api(ctx, '/api/radio/sets')).sets.map((x) => x.id)
    const { antes } = mutarBase(ctx, 'bpm', paso.titulo, String(nuevo))
    try {
      await page.click('.rguardar')
      await page.waitForSelector('#r-guardar-nombre', { timeout: ESPERA_MS })
      const respuesta = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/radio/sets' && r.request().method() === 'POST', { timeout: ESPERA_MS })
      await page.keyboard.press('Enter')
      const r = await respuesta
      igual(r.status(), 409, 'guardar un set que cambió en la base')
      const motivo = (await r.json()).error
      const alerta = await hasta(() => page.evaluate(() => {
        const a = document.querySelector('.rguardar-error[role=alert]')
        return a ? { titulo: a.querySelector('.alert-title')?.textContent, texto: a.querySelector('p')?.textContent, rearmar: !!a.querySelector('.rrearmar') } : null
      }), (v) => v !== null, 'el 409 no se mostró')
      igual(alerta, { titulo: 'No se guardó el set', texto: motivo, rearmar: true }, 'el aviso del 409: el motivo de la API y el botón para re-armar')
      igual((await api(ctx, '/api/radio/sets')).sets.map((x) => x.id), antesLista, 'un 409 guardó algo igual')
      igual((await leerGuardado(page)).pasos[1].datos.BPM, bpm1(paso.bpm), 'el set en pantalla cambió solo (tiene que seguir el que se armó)')
      // Re-armar muestra el set de hoy, y ese sí se guarda.
      const hoy = page.waitForResponse((x) => new URL(x.url()).pathname === '/api/radio/set', { timeout: ESPERA_MS })
      await page.click('.rrearmar')
      const respHoy = await hoy
      const qs = new URL(respHoy.url()).searchParams
      igual({ track: qs.get('track'), largo: qs.get('largo'), curva: qs.get('curva') },
        { track: visto.semilla.id, largo: String(visto.config.largo), curva: visto.config.curva },
        '«Re-armar el set» no pidió el set con la config que el set usó (usó la de los controles)')
      const rearmado = await respHoy.json()
      const fila = rearmado.pasos.find((p) => p.track.titulo === paso.titulo)
      afirmar(fila, `el set re-armado ya no tiene «${paso.titulo}»`)
      await hasta(() => leerGuardado(page), (v) => v.pasos.some((p) => p.titulo === paso.titulo && p.datos.BPM === bpm1(nuevo)),
        'después de «Re-armar el set» la pantalla no muestra el BPM de hoy')
      igual(await page.$('.rguardar-error'), null, 'el aviso del 409 quedó pegado al set re-armado')
      await page.click('.rguardar')
      await page.waitForSelector('#r-guardar-nombre', { timeout: ESPERA_MS })
      const otra = page.waitForResponse((x) => new URL(x.url()).pathname === '/api/radio/sets' && x.request().method() === 'POST', { timeout: ESPERA_MS })
      await page.keyboard.press('Enter')
      const ok = await otra
      igual(ok.status(), 201, 'guardar el set re-armado')
      const s = await setGuardadoApi(ctx, (await ok.json()).set.id)
      igual(bpm1(s.pasos.find((p) => p.track.titulo === paso.titulo).track.bpm), bpm1(nuevo), 'se guardó un BPM que no era el que estaba en pantalla')
    } finally {
      mutarBase(ctx, 'bpm', paso.titulo, String(antes))
    }
  }],

  ['sets: exportar un set guardado baja su FOTO aunque la base haya cambiado, y avisa lo que falta', async (page, ctx) => {
    const s = await guardarUnoPorApi(ctx, `Exportar ${Date.now()}`)
    afirmar(s.pasos.length >= 3, 'el set de juguete tiene menos de 3 pasos')
    const cambia = s.pasos[1].track
    const falta = s.pasos[2].track
    const respaldo = path.join(ctx.tmp, `respaldo-exp-${Date.now()}.json`)
    const { antes } = mutarBase(ctx, 'bpm', cambia.titulo, String(Math.round((cambia.bpm + 0.7) * 10) / 10))
    try {
      mutarBase(ctx, 'ocultar', falta.titulo, respaldo)
      try {
        const r = await fetch(`${ctx.url}/api/radio/sets/${s.id}/m3u8`)
        afirmar(r.ok, `/api/radio/sets/${s.id}/m3u8 contestó ${r.status}`)
        const esperado = Buffer.from(await r.arrayBuffer())
        const nombre = decodeURIComponent((/filename\*=UTF-8''([^;]+)/.exec(r.headers.get('content-disposition') || '') || [])[1] || '')
        igual(r.headers.get('x-djradio-faltan'), '1', 'la API no cuenta el track que falta')
        // Que el caso tenga dientes: el set de HOY ya no es la foto.
        const hoy = await fetch(`${ctx.url}/api/radio/set.m3u8?track=${encodeURIComponent(s.pasos[0].track.id)}&largo=${s.config.largo}`)
        const hoyBytes = hoy.ok ? Buffer.from(await hoy.arrayBuffer()) : Buffer.alloc(0)
        afirmar(Buffer.compare(hoyBytes, esperado) !== 0, 'el .m3u8 del set de hoy es igual al de la foto: el caso no probaría nada')
        afirmar(esperado.toString('utf8').includes(`bpm=${bpm1(cambia.bpm)} `), 'el .m3u8 de la foto no trae el BPM que se guardó')
        await abrirRadio(page, ctx)
        await abrirGuardado(page, s.id)
        const bajado = await exportarConBoton(page, ctx)
        igual(bajado.nombre, nombre, 'nombre del archivo bajado')
        afirmar(Buffer.compare(bajado.bytes, esperado) === 0,
          `con un set guardado abierto, el .m3u8 bajado no es su foto\n  bajado:   ${json(bajado.bytes.toString('utf8').slice(0, 300))}\n  esperado: ${json(esperado.toString('utf8').slice(0, 300))}`)
        const aviso = await page.$eval('.rexportar-ok', (p) => p.textContent).catch(() => null)
        afirmar(aviso && aviso.includes(nombre) && aviso.includes('1 ya no está en la biblioteca'), `el aviso no dice que falta un track: ${json(aviso)}`)
      } finally {
        mutarBase(ctx, 'restaurar', respaldo)
      }
    } finally {
      mutarBase(ctx, 'bpm', cambia.titulo, String(antes))
    }
  }],

  ['sets: abrir un set guardado para el audio del set armado', async (page, ctx) => {
    const s = await guardarUnoPorApi(ctx, `Audio ${Date.now()}`)
    const { uno } = await semillaUno(ctx)
    await abrirRadio(page, ctx)
    await elegirSemilla(page, 'Uno')
    await armarSet(page)
    await page.click('.rpaso .rplay')
    await hasta(async () => ({ suenan: await sonando(page), pausar: await pasosEnPausar(page) }),
      (v) => json(v.suenan) === json([`/api/radio/audio/${uno.id}`]) && v.pausar.length === 1, 'el paso 1 del set armado no quedó sonando')
    await abrirGuardado(page, s.id)
    await hasta(async () => ({ suenan: await sonando(page), pausar: await pasosEnPausar(page) }),
      (v) => v.suenan.length === 0 && v.pausar.length === 0,
      'abrí un set guardado y el audio del set armado sigue sonando (o un botón sigue en «Pausar»)')
  }],

  ['sets: al cambiar de set guardado, una calificación a medio escribir no pasa al otro', async (page, ctx) => {
    const a = await guardarUnoPorApi(ctx, `A ${Date.now()}`)
    const b = await guardarUnoPorApi(ctx, `B ${Date.now()}`)
    await abrirRadio(page, ctx)
    await abrirGuardado(page, a.id)
    await clickNivel(page, 1, 'mala')
    await page.waitForSelector('.rcal[data-n="1"] .rcal-motivo input', { timeout: ESPERA_MS })
    await page.type('.rcal[data-n="1"] .rcal-motivo input', 'a medio escribir')
    await abrirGuardado(page, b.id)
    const c = await controlDe(page, 1)
    igual({ sel: c.sel, motivo: c.motivo, falta: c.falta, estado: c.estado }, { sel: null, motivo: false, falta: false, estado: 'sin calificar' },
      `el set #${b.id} muestra la «mala» a medio escribir del set #${a.id}`)
    igual((await calificacionApi(ctx, b.id, 1)).calificacion, null, 'la API del set B')
  }],

  ['sets: si no se puede guardar una calificación, la pantalla vuelve a lo que tiene la API', async (page, ctx) => {
    const s = await guardarUnoPorApi(ctx, `Rollback ${Date.now()}`)
    await apiPedir(ctx, `/api/radio/sets/${s.id}/transiciones/1`, 'PUT', { calificacion: 'ok' })
    let fallar = true
    await interceptar(page, (req) => {
      if (fallar && esPut(req, s.id, 1)) { req.abort('failed'); return true }
      return false
    })
    await abrirRadio(page, ctx)
    await abrirGuardado(page, s.id)
    await clickNivel(page, 1, 'regular')
    const c = await hasta(() => controlDe(page, 1), (v) => v && v.error, 'el PUT falló y la pantalla no lo dijo')
    igual({ sel: c.sel, clase: c.clase, estado: c.estado }, { sel: 'ok', clase: 'ok', estado: 'guardada' },
      'con el PUT caído la pantalla tiene que volver a mostrar lo que tiene la API (ok), no «Regular guardada»')
    igual((await calificacionApi(ctx, s.id, 1)).calificacion, 'ok', 'la API')
    // Volver a elegir Regular manda el pedido otra vez (el radio no quedó marcado).
    fallar = false
    await clickNivel(page, 1, 'regular')
    await hasta(() => calificacionApi(ctx, s.id, 1), (v) => v.calificacion === 'regular', 'el segundo clic en Regular no se mandó')
    const d = await hasta(() => controlDe(page, 1), (v) => v.estado === 'guardada' && v.sel === 'regular', 'la pantalla no terminó en Regular guardada')
    igual(d.error, null, 'quedó el error del intento anterior')
  }],

  ['sets: dos clics seguidos en la misma transición: se guarda el último', async (page, ctx) => {
    const s = await guardarUnoPorApi(ctx, `Dos clics ${Date.now()}`)
    let retenido = null
    await interceptar(page, (req) => {
      if (!retenido && esPut(req, s.id, 1)) { retenido = req; return true }
      return false
    })
    await abrirRadio(page, ctx)
    await abrirGuardado(page, s.id)
    await clickNivel(page, 1, 'ok')
    await hasta(async () => !!retenido, (v) => v, 'el primer clic no mandó el PUT')
    await clickNivel(page, 1, 'regular')          // mientras el primero sigue viajando
    await hasta(() => controlDe(page, 1), (v) => v.sel === 'regular' && v.estado === 'Guardando…',
      'mientras viaja el primero, la pantalla tiene que mostrar el último pedido y que se está guardando')
    retenido.continue()
    await hasta(() => calificacionApi(ctx, s.id, 1), (v) => v.calificacion === 'regular', 'el segundo clic se perdió: la API quedó con el primero')
    await hasta(() => controlDe(page, 1), (v) => v.sel === 'regular' && v.estado === 'guardada', 'la pantalla no terminó en Regular guardada')
  }],

  ['sets: respuestas fuera de orden no hacen retroceder el resumen', async (page, ctx) => {
    const s = await guardarUnoPorApi(ctx, `Orden ${Date.now()}`)
    // El PUT de la transición 1 llega al server PRIMERO, pero su respuesta llega a la página
    // DESPUÉS que la de la transición 2: trae el resumen viejo (1 calificada).
    let soltar
    const suelta = new Promise((r) => { soltar = r })
    let tomado = false
    await interceptar(page, async (req) => {
      if (tomado || !esPut(req, s.id, 1)) return false
      tomado = true
      const r = await fetch(req.url(), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: req.postData() })
      const cuerpo = await r.text()
      await suelta
      await req.respond({ status: r.status, contentType: 'application/json', body: cuerpo })
      return true
    })
    await abrirRadio(page, ctx)
    await abrirGuardado(page, s.id)
    await clickNivel(page, 1, 'ok')
    await hasta(() => calificacionApi(ctx, s.id, 1), (v) => v.calificacion === 'ok', 'el PUT de la transición 1 no llegó al server')
    const r2 = page.waitForResponse((x) => esPut(x.request(), s.id, 2), { timeout: ESPERA_MS })
    await clickNivel(page, 2, 'ok')
    await r2
    const r1 = page.waitForResponse((x) => esPut(x.request(), s.id, 1), { timeout: ESPERA_MS })
    soltar()
    await r1
    // Una vuelta más por la red de la página: la respuesta vieja ya se procesó.
    await page.evaluate(() => fetch('/api/radio/biblioteca').then((r) => r.text()))
    const api2 = await setGuardadoApi(ctx, s.id)
    igual(api2.resumen.ok, 2, 'la API después de las dos')
    await hasta(() => leerGuardado(page), (v) => json(v.resumen) === json(api2.resumen) && v.calificadas === `2 de ${s.transiciones.length} transiciones calificadas`,
      'el resumen en pantalla retrocedió con la respuesta vieja')
  }],

  ['sets: 400 px con un set guardado, calificaciones y el campo de motivo abierto', async (page, ctx) => {
    const s = await guardarUnoPorApi(ctx, `Un nombre bastante largo para ver que no se corta a 400 px ${Date.now()}`)
    await apiPedir(ctx, `/api/radio/sets/${s.id}/transiciones/2`, 'PUT', { calificacion: 'regular', motivo: 'entra medio tarde el bombo, habría que mezclar antes' })
    await abrirRadio(page, ctx)
    await page.setViewport({ width: 400, height: 860 })
    await hasta(() => page.evaluate(() => document.documentElement.clientWidth), (w) => w <= 400, 'el viewport no pasó a 400 px')
    await abrirGuardado(page, s.id)
    await clickNivel(page, 1, 'mala')     // queda pendiente, con el campo de motivo abierto
    await page.waitForSelector('.rcal[data-n="1"] .rcal-motivo input', { timeout: ESPERA_MS })
    await page.click('.rguardado-acciones .btn:first-child')   // Renombrar: el formulario abierto
    await page.waitForSelector('#r-renombrar', { timeout: ESPERA_MS })
    const d = await desbordeDe(page)
    afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `set guardado a 400 px: hay contenido fuera del ancho: ${json(d)}`)
  }],
]

/* ---------- casos ---------- */

const CASOS = [

  ['home: cada tarjeta muestra el BPM (un decimal) y la key de /api/biblioteca', async (page, ctx) => {
    const lib = await api(ctx, '/api/biblioteca')
    await abrirHome(page, ctx)
    // Sin copiar el tope de tarjetas por estante del front: la base de juguete tiene pocos
    // tracks por género, así que la home tiene que mostrarlos TODOS.
    const esperado = lib.generos.flatMap((g) => g.tracks).map((t) => ({
      titulo: t.titulo,
      artista: t.artista || '—',
      // Sin BPM no hay BPM (ni un 0 ni un guion inventado en la tarjeta).
      meta: [bpm1(t.bpm), t.camelot || t.tonalidad || null].filter(Boolean).join(' · ') || null,
    })).sort((a, b) => a.titulo.localeCompare(b.titulo))
    const real = await hasta(() => page.$$eval('.lib-card', (cs) => cs.map((c) => ({
      titulo: c.querySelector('.lib-title')?.textContent ?? null,
      artista: c.querySelector('.lib-artist')?.textContent ?? null,
      meta: c.querySelector('.lib-meta')?.textContent ?? null,
    }))), (v) => v.length === esperado.length, `la home no dibujó las ${esperado.length} tarjetas`)
    igual(real.sort((a, b) => a.titulo.localeCompare(b.titulo)), esperado, 'las tarjetas de la home no dicen lo mismo que /api/biblioteca')
  }],

  ['carátulas: la embebida se ve, sin carátula queda el placeholder, nunca un marco vacío', async (page, ctx) => {
    const lib = await api(ctx, '/api/biblioteca')
    const tracks = lib.generos.flatMap((g) => g.tracks)
    await abrirHome(page, ctx)
    const leer = (titulo) => page.evaluate((t) => {
      const card = [...document.querySelectorAll('.lib-card')].find((c) => c.querySelector('.lib-title')?.textContent === t)
      if (!card) return { card: false }
      card.scrollIntoView({ block: 'center' })   // las <img> son lazy: fuera de vista no cargan
      const img = card.querySelector('.lib-cover img.cover-img')
      const ph = card.querySelector('.lib-cover .cover-ph')
      const phCs = ph && getComputedStyle(ph)
      return {
        card: true,
        img: img
          ? {
              cargada: img.complete && img.naturalWidth > 0,
              opacidad: getComputedStyle(img).opacity,
              // De dónde salió y qué tamaño tiene: una tarjeta con la carátula de OTRO tema
              // también "carga" y se ve.
              ruta: new URL(img.currentSrc || img.src, location.href).pathname,
              tamano: [img.naturalWidth, img.naturalHeight],
            }
          : null,
        // El placeholder se "ve" si está visible y tiene su dibujo (el degradado), no el
        // fondo liso del recuadro.
        placeholder: !!ph && window.__visible(ph) && phCs.opacity === '1' && /gradient/.test(phCs.backgroundImage),
      }
    }, titulo)
    const conImagen = ctx.base.caratula_dibujable   // id → [ancho, alto] de su PNG
    afirmar(new Set(Object.values(conImagen).map(json)).size === Object.keys(conImagen).length,
      'las carátulas de la base de juguete tienen el mismo tamaño: no se distinguiría una de otra')
    for (const t of tracks) {
      if (t.id in conImagen) {
        const v = await hasta(() => leer(t.titulo), (x) => x.img && x.img.cargada && x.img.opacidad === '1',
          `«${t.titulo}» trae carátula (/api/cover/${t.id}) y la tarjeta no la termina mostrando`)
        igual({ ruta: v.img.ruta, tamano: v.img.tamano }, { ruta: `/api/cover/${encodeURIComponent(t.id)}`, tamano: conImagen[t.id] },
          `la tarjeta de «${t.titulo}» muestra una carátula que no es la suya`)
      } else {
        // Sin carátula (404) o con una que el navegador no puede dibujar (el id 3): la <img>
        // tiene que irse y quedar el placeholder visible.
        await hasta(() => leer(t.titulo), (v) => v.card && v.img === null && v.placeholder,
          `«${t.titulo}» no tiene carátula dibujable y la tarjeta no quedó con el placeholder (¿marco vacío?)`)
      }
    }
  }],

  ['barra: aparece al dar play, BPM con un decimal solo si vino, un solo audio', async (page, ctx) => {
    const lib = await api(ctx, '/api/biblioteca')
    const porTitulo = Object.fromEntries(lib.generos.flatMap((g) => g.tracks).map((t) => [t.titulo, t]))
    await abrirHome(page, ctx)
    afirmar(await page.$('.deck') === null, 'la barra está en pantalla sin que nada suene')
    for (const titulo of ['Dos', 'Cuatro', 'Uno']) {
      const t = porTitulo[titulo]
      afirmar(t, `/api/biblioteca no trae «${titulo}»`)
      const barra = await playEnHome(page, titulo)
      const chips = {}
      if (t.bpm != null) chips.BPM = bpm1(t.bpm)
      if (t.camelot || t.tonalidad) chips.Key = { camelot: t.camelot ?? null, clasica: t.tonalidad ?? null }
      igual(barra.chips, chips, `la barra con «${titulo}» no muestra lo que dice /api/biblioteca (sin BPM → sin chip)`)
      const suenan = await hasta(() => sonando(page), (s) => s.length === 1 && s[0] === `/api/audio/${t.id}`,
        `con «${titulo}» en la barra tendría que sonar un solo audio, el suyo`)
      igual(suenan, [`/api/audio/${t.id}`], 'audios sonando')
      // La región viva (lector de pantalla) dice qué suena, con los datos de la API.
      igual(barra.anuncio, `Sonando: ${quien(t)}`, `el anuncio aria-live de la barra con «${titulo}»`)
    }
    const uno = porTitulo.Uno
    await page.click('.deck-play')
    const pausada = await hasta(() => leerBarra(page), (d) => d && d.estado === 'paused', 'pausa desde la barra: no quedó en pausa')
    igual(pausada.anuncio, `En pausa: ${quien(uno)}`, 'el anuncio aria-live de la barra en pausa')
  }],

  ['radio: cada semilla de la lista = /api/radio/biblioteca (BPM, Camelot y clásica, ?, energía)', async (page, ctx) => {
    const lib = await api(ctx, '/api/radio/biblioteca')
    const leyenda = lib.opciones.leyenda_key
    await abrirRadio(page, ctx)
    const real = await hasta(() => page.$$eval('.rsem', (bs) => bs.map((b) => ({
      titulo: b.querySelector('.rsem-titulo')?.textContent ?? null,
      artista: b.querySelector('.rsem-artista')?.textContent ?? null,
      datos: window.__datosTrack(b),
      noTrack: !!b.querySelector('.rtag-notrack'),
    }))), (v) => v.length === lib.tracks.length, `la lista no tiene las ${lib.tracks.length} semillas de la API`)
    const esperado = lib.tracks.map((t) => ({
      titulo: t.titulo, artista: t.artista || '—', datos: esperadoDatos(t, leyenda), noTrack: !t.es_track,
    }))
    for (let i = 0; i < esperado.length; i++) igual(real[i], esperado[i], `semilla ${i + 1} («${esperado[i].titulo}»)`)
    afirmar(esperado.some((t) => t.datos.key.duda), 'la base de juguete no tiene ninguna key dudosa: el chequeo del ? no probaría nada')
  }],

  ['radio: el set = /api/radio/set (orden, motivos, datos, titular del corte, fragmentos)', async (page, ctx) => {
    const { lib, uno } = await semillaUno(ctx)
    const leyenda = lib.opciones.leyenda_key
    const set = await api(ctx, `/api/radio/set?track=${encodeURIComponent(uno.id)}`)
    await abrirRadio(page, ctx)
    await elegirSemilla(page, 'Uno')
    await armarSet(page)
    const real = await page.evaluate(() => ({
      pasos: [...document.querySelectorAll('.rpaso')].map((p) => ({
        n: p.querySelector('.rpaso-n')?.textContent ?? null,
        titulo: p.querySelector('.rpaso-titulo')?.textContent ?? null,
        artista: p.querySelector('.rpaso-artista')?.textContent ?? null,
        motivo: p.querySelector('.rpaso-why .mono')?.textContent ?? null,
        semilla: !!p.querySelector('.rtag-seed'),
        datos: window.__datosTrack(p),
      })),
      cuenta: document.querySelector('.rset-cuenta')?.textContent ?? null,
      corte: (() => {
        const a = document.querySelector('.rpanel-set .alert[role=status]')
        if (!a) return null
        const ps = a.querySelectorAll('p')
        return {
          titular: a.querySelector('.alert-title')?.textContent ?? null,
          detalle: ps[0]?.textContent ?? null,
          codigo: a.querySelector('p.mono')?.textContent ?? null,
        }
      })(),
      notas: [...document.querySelectorAll('.rpanel-set > .rnota')].map((p) => p.textContent),
      curva: [...document.querySelectorAll('.rcurva i')].map((i) => i.style.height),
      anuncio: document.querySelector('.radiodj [role=status][aria-live]')?.textContent ?? null,
    }))
    igual(real.anuncio, `Set de ${set.total} track${set.total === 1 ? '' : 's'} desde ${set.semilla.label}.`,
      'el anuncio aria-live de la radio después de armar el set')
    const esperado = set.pasos.map((p) => ({
      n: String(p.n), titulo: p.track.titulo, artista: p.track.artista || '—', motivo: p.motivo,
      semilla: p.es_semilla, datos: esperadoDatos(p.track, leyenda),
    }))
    igual(real.pasos.length, esperado.length, 'cantidad de pasos del set')
    for (let i = 0; i < esperado.length; i++) igual(real.pasos[i], esperado[i], `paso ${i + 1} del set`)
    igual(real.cuenta, `${set.total} track${set.total === 1 ? '' : 's'}${set.pedidos != null ? ` · ${set.total} de ${set.pedidos} pedidos` : ''}`, 'la cuenta del set')
    igual(real.corte, set.corte ? { titular: set.corte.titular, detalle: set.corte.detalle, codigo: set.corte.codigo ?? null } : null,
      'el aviso de por qué se cortó el set')
    igual(real.notas, [set.aviso_fragmentos, set.leyenda_key].filter(Boolean), 'las notas debajo del set (fragmentos y leyenda del ?)')
    igual(real.curva, set.pasos.map((p) => (p.track.energia_pct == null ? '2px' : `${Math.max(2, p.track.energia_pct)}%`)),
      'la curva de energía no sale del mismo percentil que las filas')
    // Que el caso pruebe algo: la base de juguete corta el set y tiene fragmentos.
    afirmar(set.corte && set.aviso_fragmentos, 'la base de juguete ya no produce corte ni fragmentos: este caso quedó sin dientes')
  }],

  ['radio: exportar baja el mismo .m3u8 (bytes y nombre) que /api/radio/set.m3u8', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    const r = await fetch(`${ctx.url}/api/radio/set.m3u8?track=${encodeURIComponent(uno.id)}`)
    afirmar(r.ok, `/api/radio/set.m3u8 contestó ${r.status}`)
    const esperadoBytes = Buffer.from(await r.arrayBuffer())
    const disp = r.headers.get('content-disposition') || ''
    const esperadoNombre = decodeURIComponent((/filename\*=UTF-8''([^;]+)/.exec(disp) || [])[1] || '')
    afirmar(esperadoNombre.endsWith('.m3u8'), `Content-Disposition sin nombre .m3u8: ${disp}`)

    const dir = fs.mkdtempSync(path.join(ctx.tmp, 'descargas-'))
    const cdp = await page.createCDPSession()
    await cdp.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: dir, eventsEnabled: true })
    const descargas = []
    cdp.on('Browser.downloadWillBegin', (e) => descargas.push({ guid: e.guid, nombre: e.suggestedFilename, estado: 'empezó' }))
    cdp.on('Browser.downloadProgress', (e) => { const d = descargas.find((x) => x.guid === e.guid); if (d) d.estado = e.state })
    const pedidos = []
    page.on('request', (q) => { if (new URL(q.url()).pathname === '/api/radio/set.m3u8') pedidos.push(q.url()) })

    await abrirRadio(page, ctx)
    await elegirSemilla(page, 'Uno')
    await armarSet(page)
    await page.click('.rexportar')
    const [d] = await hasta(async () => descargas, (ds) => ds.length > 0 && ds.every((x) => x.estado === 'completed' || x.estado === 'canceled'),
      'tocar «Exportar a Rekordbox» no terminó ninguna descarga')
    igual(descargas.length, 1, 'cantidad de descargas')
    igual(d.estado, 'completed', 'estado de la descarga')
    igual(d.nombre, esperadoNombre, 'nombre del archivo bajado')
    const bajado = fs.readFileSync(path.join(dir, d.nombre))
    afirmar(Buffer.compare(bajado, esperadoBytes) === 0,
      `el .m3u8 bajado no es el de la API (${bajado.length} vs ${esperadoBytes.length} bytes)\n  bajado:   ${json(bajado.toString('utf8').slice(0, 300))}\n  esperado: ${json(esperadoBytes.toString('utf8').slice(0, 300))}`)
    const aviso = await page.$eval('.rexportar-ok', (p) => p.textContent).catch(() => null)
    afirmar(aviso && aviso.includes(esperadoNombre), `el aviso de éxito no nombra el archivo: ${json(aviso)}`)
    igual(pedidos.length, 1, 'pedidos a /api/radio/set.m3u8 por un click')
  }],

  ['radio: armar otro set con un paso sonando lo pausa (ningún botón queda en «Pausar»)', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    await abrirRadio(page, ctx)
    await elegirSemilla(page, 'Uno')
    await armarSet(page)
    const primero = await page.$eval('.rpaso .rplay', (b) => b.getAttribute('aria-label'))
    await page.click('.rpaso .rplay')
    await hasta(async () => ({ suenan: await sonando(page), pausar: await pasosEnPausar(page) }),
      (v) => v.suenan.length === 1 && v.suenan[0] === `/api/radio/audio/${uno.id}` && v.pausar.length === 1,
      `play en «${primero}» no dejó sonando su audio con el botón en «Pausar»`)
    await armarSet(page)
    await hasta(async () => ({ suenan: await sonando(page), pausar: await pasosEnPausar(page) }),
      (v) => v.suenan.length === 0 && v.pausar.length === 0,
      'armé otro set y el audio del set anterior sigue sonando o un botón sigue en «Pausar»')
  }],

  ['radio y barra: un solo audio; la barra pausa la radio y el botón vuelve a «Reproducir»', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    const lib = await api(ctx, '/api/biblioteca')
    const unoHome = lib.generos.flatMap((g) => g.tracks).find((t) => t.titulo === 'Uno')
    await abrirHome(page, ctx)
    await playEnHome(page, 'Uno')
    await abrirRadio(page, ctx, { navegar: false })
    igual((await leerBarra(page))?.estado, 'playing', 'la barra dejó de sonar al entrar a la radio')
    await elegirSemilla(page, 'Uno')
    await armarSet(page)
    await page.click('.rpaso .rplay')
    // La radio arranca → la barra cede.
    await hasta(async () => ({ suenan: await sonando(page), barra: (await leerBarra(page))?.estado, pausar: await pasosEnPausar(page) }),
      (v) => json(v.suenan) === json([`/api/radio/audio/${uno.id}`]) && v.barra === 'paused' && v.pausar.length === 1,
      'play en un paso de la radio: tendría que sonar SOLO la radio y la barra quedar en pausa')
    // La barra arranca → la radio se pausa y su botón lo dice.
    await page.click('.deck-play')
    await hasta(async () => ({ suenan: await sonando(page), barra: (await leerBarra(page))?.estado, pausar: await pasosEnPausar(page) }),
      (v) => json(v.suenan) === json([`/api/audio/${unoHome.id}`]) && v.barra === 'playing' && v.pausar.length === 0,
      'play en la barra: tendría que sonar SOLO la barra y ningún paso de la radio decir «Pausar»')
  }],

  ['accesibilidad: aria-disabled sin semilla ni set, el foco no se pierde al armar con teclado', async (page, ctx) => {
    await abrirRadio(page, ctx)
    const estado = () => page.evaluate(() => {
      const armar = document.querySelector('.rarmar')
      const exp = document.querySelector('.rexportar')
      const desc = (b) => (b.getAttribute('aria-describedby') ? document.getElementById(b.getAttribute('aria-describedby'))?.textContent ?? '(id sin elemento)' : null)
      return {
        armar: armar.getAttribute('aria-disabled'), armarPorQue: desc(armar),
        exportar: exp.getAttribute('aria-disabled'), exportarPorQue: desc(exp),
        // aria-disabled y NO disabled: un botón deshabilitado pierde el foco.
        disabled: armar.disabled || exp.disabled,
      }
    })
    const sinNada = await estado()
    afirmar(sinNada.armar === 'true' && sinNada.exportar === 'true' && !sinNada.disabled && sinNada.armarPorQue && sinNada.exportarPorQue,
      `sin semilla ni set los dos botones tienen que decir aria-disabled="true" y por qué: ${json(sinNada)}`)
    const pedidos = []
    page.on('request', (q) => { if (new URL(q.url()).pathname.startsWith('/api/radio/set')) pedidos.push(q.url()) })
    await page.click('.rexportar')
    await page.click('.rarmar')
    // Los dos clicks muertos no piden nada. Para ver que ya habrían salido, se espera a que
    // el navegador procese un pedido posterior (un fetch propio) antes de mirar la lista.
    await page.evaluate(() => fetch('/api/radio/biblioteca').then((r) => r.text()))
    igual(pedidos, [], 'con aria-disabled, un click igual pidió el set o el .m3u8')
    await elegirSemilla(page, 'Uno')
    igual((await estado()).exportar, 'true', '«Exportar» sin set armado')
    await armarSet(page, { teclado: true })
    const foco = await page.evaluate(() => ({ clase: document.activeElement?.className ?? null, tag: document.activeElement?.tagName ?? null }))
    afirmar(/\brarmar\b/.test(foco.clase || ''), `armar el set con Enter mandó el foco a otro lado: ${json(foco)}`)
    const conSet = await estado()
    afirmar(conSet.exportar === 'false' && conSet.exportarPorQue === null, `con set, «Exportar» tendría que estar habilitado: ${json(conSet)}`)
  }],

  ['400 px: sin scroll horizontal ni contenido cortado (home con la barra y radio con un set)', async (page, ctx) => {
    const desborde = () => desbordeDe(page)
    const cabe = (d) => d.scroll <= d.ancho && d.fuera.length === 0
    await abrirRadio(page, ctx)
    await elegirSemilla(page, 'Uno')
    await armarSet(page)
    await page.setViewport({ width: 400, height: 860 })
    await hasta(() => page.evaluate(() => document.documentElement.clientWidth), (w) => w <= 400, 'el viewport no pasó a 400 px')
    const radio = await desborde()
    afirmar(cabe(radio), `radio con set a 400 px: hay contenido fuera del ancho: ${json(radio)}`)
    await abrirHome(page, ctx)
    await playEnHome(page, 'Uno')
    const home = await desborde()
    afirmar(cabe(home), `home con la barra a 400 px: hay contenido fuera del ancho: ${json(home)}`)
  }],

  ...CASOS_SETS,

  ['resultados: se ve qué versión está elegida y cuál suena; YouTube/SoundCloud suenan como audio, sin monitor', async (page, ctx) => {
    // Sin red: la búsqueda, la calidad, los metadatos y el audio de las fuentes se simulan
    // interceptando los pedidos del navegador. Lo ESPERADO sale de estos resultados simulados
    // (que hacen de API), no del front. El "audio de YouTube" es el WAV de un track de la base
    // de juguete, servido por el mismo camino que usaría el backend (/api/fuente/audio).
    const lib = await api(ctx, '/api/biblioteca')
    const uno = lib.generos.flatMap((g) => g.tracks).find((t) => t.titulo === 'Uno')
    afirmar(uno, '/api/biblioteca no trae «Uno»')
    const wav = Buffer.from(await (await fetch(`${ctx.url}/api/audio/${uno.id}`)).arrayBuffer())
    const opcion = (fuente, extra) => ({ titulo: 'Tema Simulado', artista: 'Artista S', duracion: 200, fuente, thumbnail: null, ...extra })
    const grupos = [
      { opciones: [
        opcion('youtube', { url: 'https://www.youtube.com/watch?v=aaaaaaaaaaa', video_id: 'aaaaaaaaaaa' }),
        opcion('soundcloud', { url: 'https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A123456789', video_id: '123456789' }),
        opcion('ligaudio', { url: 'https://web.ligaudio.ru/x.mp3', stream_url: 'https://web.ligaudio.ru/x.mp3' }),
      ] },
      { opciones: [opcion('youtube', { titulo: 'Tema Roto', url: 'https://www.youtube.com/watch?v=bbbbbbbbbbb', video_id: 'bbbbbbbbbbb' })] },
    ]
    const MOTIVO = 'El tema ya no está disponible en YouTube.'
    const afuera = []            // pedidos a YouTube/SoundCloud directos (el monitor): no tiene que haber
    await page.setRequestInterception(true)
    page.on('request', (req) => {
      const u = new URL(req.url())
      const json = (status, body) => req.respond({ status, contentType: 'application/json', body: JSON.stringify(body) })
      if (u.pathname === '/api/buscar') return json(200, { exito: true, grupos })
      if (u.pathname === '/api/calidad') return json(200, { ok: false, grade: '?' })
      if (u.pathname === '/api/meta') return json(200, { bpm: null, genero: null })
      if (u.pathname === '/api/fuente/audio/info') return json(200, { preview: false, duracion: 200 })
      if (u.pathname === '/api/fuente/audio') {
        if (u.searchParams.get('ref') === 'bbbbbbbbbbb') return json(404, { error: MOTIVO })
        return req.respond({ status: 200, contentType: 'audio/wav', body: wav })
      }
      if (u.origin !== new URL(ctx.url).origin) {
        // Las carátulas (ytimg/sndcdn) no cuentan: el reproductor embebido vive en estos dos.
        if (/(^|\.)(youtube\.com|soundcloud\.com)$/.test(u.hostname)) afuera.push(u.href)
        return req.abort()
      }
      return req.continue()
    })
    await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
    await page.type('input[aria-label="Buscar una canción, artista o género"]', 'tema simulado')
    await page.keyboard.press('Enter')
    await page.waitForSelector('.trk .vchip', { timeout: ESPERA_MS })

    // Estado de las pastillas y de la fila, leído como lo lee un lector de pantalla + la forma.
    const leer = () => page.evaluate(() => [...document.querySelectorAll('.trk')].map((row) => ({
      sonando: row.getAttribute('aria-current'),
      texto: row.querySelector('.trk-now')?.textContent ?? null,
      chips: [...row.querySelectorAll('.vchip')].map((b) => ({
        nombre: b.getAttribute('aria-label'),
        elegida: b.getAttribute('aria-pressed'),
        suena: b.getAttribute('aria-current'),
        tilde: !!b.querySelector('.vchip-ok'),       // forma, no color
        barras: !!b.querySelector('.eq'),
      })),
    })))
    const c = (nombre, elegida, suena) => ({ nombre, elegida: String(elegida), suena: suena ? 'true' : null, tilde: elegida, barras: !!suena })

    const inicio = await leer()
    igual(inicio[0], { sonando: null, texto: null, chips: [
      c('Opción 1: YouTube (elegida)', true, false), c('Opción 2: SoundCloud', false, false), c('Opción 3: MP3', false, false),
    ] }, 'antes de dar play: la opción 1 elegida (con tilde) y ninguna sonando')

    // Play en la fila 1: suena la versión elegida (YouTube) como AUDIO desde el backend.
    await page.click('.trk:nth-child(2) .thumb-play')     // nth-child(1) es el encabezado
    const barra = await hasta(() => leerBarra(page), (d) => d && d.estado === 'playing', 'la barra no quedó sonando la fila 1')
    igual(barra.titulo, 'Tema Simulado', 'la barra no muestra el tema de la fila 1')
    const suenaYT = await hasta(() => sonando(page), (s) => s.length === 1, 'tendría que sonar un solo audio')
    igual(suenaYT, ['/api/fuente/audio'], 'YouTube tiene que sonar por el audio del backend, no por el reproductor de YouTube')
    const src1 = await page.evaluate(() => [...window.__medios].find((m) => !m.paused)?.src)
    igual(new URL(src1).searchParams.toString(), 'fuente=youtube&ref=aaaaaaaaaaa', 'el audio pedido no es el de la opción elegida')
    const conYT = await leer()
    igual(conYT[0], { sonando: 'true', texto: 'Sonando: opción 1 · YouTube', chips: [
      c('Opción 1: YouTube (elegida, sonando ahora)', true, true), c('Opción 2: SoundCloud', false, false), c('Opción 3: MP3', false, false),
    ] }, 'sonando la opción 1: la fila y la pastilla tienen que decirlo')
    igual(conYT[1].sonando, null, 'la fila 2 no suena y no puede marcarse como la que suena')
    const deck = await page.evaluate(() => ({
      fuente: document.querySelector('.deck-src')?.textContent,
      monitor: window.__visible(document.querySelector('.deck-monitor')),
      iframes: document.querySelectorAll('.deck-monitor iframe').length,
    }))
    igual(deck, { fuente: 'YouTube · 1/3, opción 1 de 3, solo audio', monitor: false, iframes: 0 },
      'la barra tiene que decir fuente y versión, y no mostrar el recuadro de video')

    // Elegir la opción 2 mientras suena la 1: se ve la diferencia entre elegida y sonando.
    await page.click('.trk:nth-child(2) .vchip:nth-child(2)')
    const cambio = await hasta(leer, (v) => v[0].chips[1].elegida === 'true', 'la opción 2 no quedó elegida')
    igual(cambio[0].chips, [
      c('Opción 1: YouTube (sonando ahora)', false, true), c('Opción 2: SoundCloud (elegida)', true, false), c('Opción 3: MP3', false, false),
    ], 'elegida (2) y sonando (1) tienen que distinguirse')

    // Play de nuevo: suena la elegida (SoundCloud), también como audio.
    await page.click('.trk:nth-child(2) .thumb-play')
    await hasta(() => page.evaluate(() => [...window.__medios].find((m) => !m.paused)?.src || ''),
      (s) => s.includes('fuente=soundcloud&ref=123456789'), 'SoundCloud no sonó por el audio del backend')
    const conSC = await hasta(leer, (v) => v[0].chips[1].suena === 'true' && !/^Cargando/.test(v[0].texto || ''), 'la opción 2 no quedó sonando')
    igual(conSC[0].texto, 'Sonando: opción 2 · SoundCloud', 'la fila no dice qué versión suena')

    // Pausa: la fila sigue diciendo cuál está en la barra, pero "En pausa" y sin barras animadas.
    await page.click('.deck-play')
    const pausa = await hasta(leer, (v) => /^En pausa/.test(v[0].texto || ''), 'en pausa la fila no lo dice')
    igual(pausa[0].texto, 'En pausa: opción 2 · SoundCloud', 'el texto de la fila en pausa')

    // Un tema que el backend no puede resolver: la barra dice el motivo del backend y ofrece
    // el reproductor de YouTube como plan B (no lo abre sola, no finge que suena).
    await page.click('.trk:nth-child(3) .thumb-play')
    const roto = await hasta(() => page.evaluate(() => ({
      estado: (document.querySelector('.deck')?.className.match(/\bis-(\w+)/) || [])[1],
      msg: document.querySelector('.deck-msg span')?.textContent,
      planB: [...document.querySelectorAll('.deck-msg button')].map((b) => b.textContent),
    })), (v) => v.estado === 'error', 'con un tema que no se puede resolver la barra no quedó en error')
    igual(roto, { estado: 'error', msg: MOTIVO, planB: ['Escuchar en el reproductor de YouTube'] },
      'la barra tiene que decir el motivo del backend y ofrecer el plan B')
    igual(await sonando(page), [], 'con el tema roto no puede quedar sonando otro audio')
    igual(afuera, [], 'se pidió algo a YouTube/SoundCloud directo (¿se cargó el reproductor embebido?)')
  }],

  ['resultados: mientras la versión carga, la pastilla dice "cargando" y no "sonando" (§6)', async (page, ctx) => {
    // En frío YouTube/SoundCloud tardan 2-5 s en empezar y la barra está en "Cargando". En ese
    // lapso la pastilla no puede decir "sonando ahora" ni animar las barritas: sería mostrar
    // que suena algo que todavía no suena. El audio del backend se RETIENE hasta que el test lo
    // suelta, así el estado de carga no depende de la velocidad de la máquina.
    const lib = await api(ctx, '/api/biblioteca')
    const uno = lib.generos.flatMap((g) => g.tracks).find((t) => t.titulo === 'Uno')
    afirmar(uno, '/api/biblioteca no trae «Uno»')
    const wav = Buffer.from(await (await fetch(`${ctx.url}/api/audio/${uno.id}`)).arrayBuffer())
    const grupos = [{ opciones: [{ titulo: 'Tema Lento', artista: 'Artista S', duracion: 200, fuente: 'youtube',
      thumbnail: null, url: 'https://www.youtube.com/watch?v=ccccccccccc', video_id: 'ccccccccccc' }] }]
    const retenidos = []
    let libre = false
    await page.setRequestInterception(true)
    page.on('request', (req) => {
      const u = new URL(req.url())
      const json = (body) => req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
      if (u.pathname === '/api/buscar') return json({ exito: true, grupos })
      if (u.pathname === '/api/calidad') return json({ ok: false, grade: '?' })
      if (u.pathname === '/api/meta') return json({ bpm: null, genero: null })
      if (u.pathname === '/api/fuente/audio/info') return json({ preview: false, duracion: 200 })
      if (u.pathname === '/api/fuente/audio') {
        const responder = () => req.respond({ status: 200, contentType: 'audio/wav', body: wav })
        return libre ? responder() : retenidos.push(responder)
      }
      if (u.origin !== new URL(ctx.url).origin) return req.abort()
      return req.continue()
    })
    await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
    await page.type('input[aria-label="Buscar una canción, artista o género"]', 'tema lento')
    await page.keyboard.press('Enter')
    await page.waitForSelector('.trk .vchip', { timeout: ESPERA_MS })

    const pastilla = () => page.evaluate(() => {
      const b = document.querySelector('.trk .vchip')
      const row = b.closest('.trk')
      const eq = b.querySelector('.eq')
      return { texto: row.querySelector('.trk-now')?.textContent ?? null, nombre: b.getAttribute('aria-label'),
        animada: !!eq && !eq.classList.contains('is-quieto') }
    })
    await page.click('.trk:nth-child(2) .thumb-play')     // nth-child(1) es el encabezado
    const cargando = await hasta(pastilla, (v) => /^Cargando/.test(v.texto || ''), 'la fila no llegó a decir "Cargando"')
    igual(cargando, { texto: 'Cargando: opción 1 · YouTube', nombre: 'Opción 1: YouTube (elegida, cargando)', animada: false },
      'mientras carga, la pastilla no puede decir que suena ni animar las barritas')

    libre = true
    retenidos.splice(0).forEach((responder) => responder())
    const suena = await hasta(pastilla, (v) => /^Sonando/.test(v.texto || ''), 'soltado el audio, la fila no quedó sonando')
    igual(suena, { texto: 'Sonando: opción 1 · YouTube', nombre: 'Opción 1: YouTube (elegida, sonando ahora)', animada: true },
      'ya sonando, la pastilla tiene que decirlo')
  }],

  ['station: el botón abre la Station; las filas llegan en orden con "N de 49", la mejor nota elegida y suenan por el proxy', async (page, ctx) => {
    // Sin red. /api/station y /api/versiones contestan con los archivos que los tests de Python
    // comparan contra lo que los endpoints REALMENTE devuelven (tests/test_soundcloud_station.py
    // y tests/test_station_versiones.py): acá hacen de API. Lo esperado sale de esos archivos.
    const s = await montarStation(page, ctx, { retener: true })
    const { respuesta, items } = s
    await abrirStation(page, ctx, s)

    // Arranca vacía, con el avance en 0, y pide de a 3 (los demás esperan en la cola).
    const inicio = await hasta(() => leerStation(page), (v) => v.progreso && s.versiones.length === 3,
      'la Station no arrancó a pedir versiones')
    igual({ progreso: inicio.progreso, filas: inicio.filas.length }, { progreso: `0 de ${items.length}`, filas: 0 },
      'antes de la primera fila: 0 de 49 y ninguna fila')
    igual(s.versiones.map((b) => b.tema.video_id), items.slice(0, 3).map((t) => t.video_id),
      'los primeros pedidos no son los primeros temas de la Station, en su orden')
    igual(s.versiones[0].tema, items[0], 'el pedido no lleva el tema de la Station tal cual')
    igual(s.versiones[0].formato, 'wav', 'el pedido no lleva el formato de descarga')
    // La 2 contesta antes que la 1: aparecen las dos, en el orden de la Station.
    s.soltar(items[1].video_id)
    s.soltar(items[0].video_id)
    const dos = await hasta(() => leerStation(page), (v) => v.filas.length === 2, 'no aparecieron las dos primeras filas')
    igual(dos.progreso, `2 de ${items.length}`, 'el avance no dice cuántas filas hay')
    igual(dos.filas, [items[0].titulo, items[1].titulo], 'las filas no están en el orden de la Station')
    afirmar(s.versiones.length <= 5, `con 2 filas listas no puede haber más de 5 pedidos (cola de 3): ${s.versiones.length}`)

    // Fila 1: viene elegida la de mejor nota (un MP3, A), no la primera (YouTube, B).
    const r0 = s.vx[items[0].video_id].respuesta
    const mejor = r0.opciones.find((o) => o.calidad?.grade === 'A')
    igual(mejor.fuente, 'ligaudio', 'el archivo de versiones cambió: la A tenía que ser la de Ligaudio')
    afirmar(/^Opción \d+: MP3, nota A\b/.test(await elegidaDe(page, 0)), `la elegida por defecto no es la de nota A: ${await elegidaDe(page, 0)}`)
    await descargarFila(page, 0)
    await hasta(() => s.descargas.length, (n) => n === 1, 'la descarga de la fila 1 no salió')
    igual(s.descargas[0].url, mejor.url, 'se descargó otra versión y no la elegida por nota')
    // La nota vino con la fila: el badge no la vuelve a pedir.
    const conNota = new Set([...r0.opciones, ...s.vx[items[1].video_id].respuesta.opciones].filter((o) => o.calidad).map((o) => o.url))
    igual(s.calidades.filter((u) => conNota.has(u)), [], 'se volvió a pedir a /api/calidad una nota que ya vino en /api/versiones')

    // Cambiar la elegida: la descarga usa la nueva.
    const yt = r0.opciones.find((o) => o.fuente === 'youtube')
    await elegir(page, 0, 'YouTube')
    await hasta(() => elegidaDe(page, 0), (t) => /^Opción \d+: YouTube, nota B\b/.test(t || ''), 'no quedó elegida la de YouTube')
    await descargarFila(page, 0)
    await hasta(() => s.descargas.length, (n) => n === 2, 'la segunda descarga de la fila 1 no salió')
    igual(s.descargas[1].url, yt.url, 'después de elegir YouTube se descargó otra versión')

    // El resto: al terminar, el avance dice cuántas tienen versiones de otras plataformas.
    s.soltarTodas()
    const fin = await hasta(() => leerStation(page), (v) => v.filas.length === items.length && !/ de /.test(v.progreso || ''),
      'no llegaron todas las filas')
    const conOtras = items.filter((t) => (s.vx[t.video_id]?.respuesta.opciones || []).some((o) => !o.estacion)).length
    igual(fin.progreso, `${items.length} temas · ${conOtras} con versiones en otras plataformas`, 'el resumen final')
    // La fila muestra el título de la versión elegida (la 1, ahora la de YouTube); el resto, el
    // de la Station, en su orden.
    igual(fin.filas, [yt.titulo, ...items.slice(1).map((t) => t.titulo)], 'las filas no están en el orden de la Station al terminar')
    igual(s.versiones.map((b) => b.tema.video_id), items.map((t) => t.video_id), 'no se pidió cada tema una vez, en orden')

    const v = await page.evaluate(() => ({
      titulo: document.querySelector('h1.station-title')?.textContent ?? null,
      datosSemilla: document.querySelectorAll('.seedbar .mb').length,
    }))
    igual(v.titulo, `Radio de «${respuesta.semilla.titulo}» — según la Station de SoundCloud`, 'el encabezado no dice de quién es la recomendación')
    igual(v.datosSemilla, 0, 'la Station es de SoundCloud: no puede mostrar BPM/tonalidad de la semilla medidos por nosotros')

    // Play en la fila 2 (solo la de SoundCloud): suena por el proxy de audio con su id.
    await page.click(`.trk:nth-child(3) .thumb-play`)     // nth-child(1) es el encabezado
    const barra = await hasta(() => leerBarra(page), (d) => d && d.estado === 'playing', 'la fila 2 de la Station no quedó sonando')
    igual(barra.titulo, items[1].titulo, 'la barra no muestra el tema de la fila 2')
    const src = await page.evaluate(() => [...window.__medios].find((m) => !m.paused)?.src)
    igual(new URL(src).pathname + '?' + new URL(src).searchParams.toString(),
      `/api/fuente/audio?fuente=soundcloud&ref=${items[1].video_id}`, 'el tema no suena por el proxy de audio con su id')

    // A 400 px el encabezado (título largo) y las filas con sus versiones entran sin cortarse.
    await page.setViewport({ width: 400, height: 860 })
    await hasta(() => page.evaluate(() => document.documentElement.clientWidth), (w) => w <= 400, 'el viewport no pasó a 400 px')
    const d = await desbordeDe(page)
    afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `Station a 400 px: hay contenido fuera del ancho: ${json(d)}`)
    // `desbordeDe` mira cajas, no texto: un título sin partir se sale de su propia caja y la
    // .seedbar (overflow hidden) lo recorta sin que ninguna caja se pase. Se mide el texto.
    const h = await page.evaluate(() => { const e = document.querySelector('h1.station-title'); return { texto: e.scrollWidth, caja: e.clientWidth } })
    afirmar(h.texto <= h.caja, `a 400 px el título de la Station queda cortado: ${json(h)}`)
  }],

  ['station: un Go+ se baja completo de otra versión y otro Go+ sin otra versión queda sin descarga', async (page, ctx) => {
    // Los dos Go+ son temas reales de la API v2 grabada (Daft Punk «One More Time» y FblManny
    // «From The Top»); sus versiones, las que devuelve el endpoint (tests/test_station_versiones.py).
    const vx = leerVersiones()
    const [daft, fbl] = ['2366118086', '2374863902'].map((id) => ({ ...vx[id].tema, fuente: 'soundcloud' }))
    const s = await montarStation(page, ctx, { primeros: [daft, fbl] })
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === s.items.length, 'no llegaron todas las filas')

    // Daft Punk: la Station solo da 30 s; viene elegido el upload completo (nota A) y lo dice.
    const completo = vx[daft.video_id].respuesta.opciones.find((o) => o.fuente === 'soundcloud' && !o.solo_preview)
    const f0 = await filaTexto(page, 0)
    afirmar(f0.includes('SoundCloud solo da 30 s (Go+): se baja completo de SoundCloud.'), `la fila del Go+ no dice de dónde se baja: ${f0}`)
    afirmar(!f0.includes('Preview 30 s'), 'con una versión completa elegida la fila no puede decir "Preview 30 s"')
    await descargarFila(page, 0)
    await hasta(() => s.descargas.length, (n) => n === 1, 'la descarga del Go+ resuelto no salió')
    igual(s.descargas[0].url, completo.url, 'del Go+ se bajó otra cosa y no el upload completo')

    // FblManny: nadie más lo tiene; queda el preview marcado, sin descarga, y el motivo.
    const f1 = await filaTexto(page, 1)
    afirmar(f1.includes('Preview 30 s'), `la fila del Go+ sin versión no dice que es un preview: ${f1}`)
    afirmar(f1.includes(vx[fbl.video_id].respuesta.motivo), `la fila no dice el motivo del backend: ${f1}`)
    const dl1 = await page.evaluate(() => {
      const b = [...document.querySelectorAll('.trk')][1].querySelector('button[aria-label^="Descargar"]')
      return b ? { disabled: b.disabled, nombre: b.getAttribute('aria-label') } : null
    })
    igual(dl1, { disabled: true, nombre: `Descargar ${fbl.titulo} (no disponible: SoundCloud solo da un fragmento de 30 s)` },
      'el Go+ sin otra versión no puede descargarse como si fuera el tema')

    // "Descargar todas" salta el preview y lo cuenta.
    await clickTexto(page, 'button', /^Descargar todas/)
    const fin = await hasta(() => botonTodas(page), (t) => /^✓/.test(t || ''), '"Descargar todas" no terminó')
    igual(fin, `✓ ${s.items.length - 1}/${s.items.length - 1} descargadas · 1 sin versión completa`, 'el resumen de "Descargar todas"')
    afirmar(!s.descargas.slice(1).some((b) => b.url === fbl.url), 'se descargó el preview de 30 s')
  }],

  ['station: "Descargar todas" con filas pendientes pregunta (bajar las listas / esperar a todas / cancelar)', async (page, ctx) => {
    const s = await montarStation(page, ctx, { retener: true })
    const { items } = s
    await abrirStation(page, ctx, s)
    await hasta(() => s.versiones.length, (n) => n >= 2, 'no arrancó a pedir versiones')
    s.soltar(items[0].video_id)
    s.soltar(items[1].video_id)
    await hasta(() => leerStation(page), (v) => v.filas.length === 2, 'no aparecieron las dos primeras filas')

    const pregunta = () => page.evaluate(() => {
      const g = document.querySelector('[role="group"][aria-label="Descargar todas"]')
      return g ? { texto: g.querySelector('.alert-title')?.textContent, botones: [...g.querySelectorAll('button')].map((b) => b.textContent) } : null
    })
    await clickTexto(page, 'button', /^Descargar todas/)
    const p = await hasta(pregunta, (x) => x, 'con filas pendientes "Descargar todas" no preguntó')
    igual(p, { texto: `Todavía faltan ${items.length - 2} de ${items.length} temas`,
      botones: ['Bajar los 2 listos', `Esperar y bajar los ${items.length}`, 'Cancelar'] }, 'la pregunta y sus tres opciones')
    igual(await page.evaluate(() => [...document.querySelectorAll('button')].find((b) => /^Descargar todas/.test(b.textContent))?.getAttribute('aria-expanded')),
      'true', 'el botón no dice que abrió la pregunta')

    await clickTexto(page, 'button', /^Cancelar$/)
    await hasta(pregunta, (x) => x === null, 'Cancelar no cerró la pregunta')
    igual(s.descargas.length, 0, 'Cancelar bajó algo')

    // Bajar las listas: una foto de las 2 filas de ahora, cada una con su elegida.
    await clickTexto(page, 'button', /^Descargar todas/)
    await hasta(pregunta, (x) => x, 'no volvió a preguntar')
    await clickTexto(page, 'button', /^Bajar los 2 listos$/)
    await hasta(() => s.descargas.length, (n) => n === 2, 'no se bajaron las 2 listas')
    const elegidas = [0, 1].map((i) => s.vx[items[i].video_id].respuesta.opciones)
    igual(s.descargas.map((b) => b.url), [elegidas[0].find((o) => o.calidad?.grade === 'A').url, items[1].url],
      'no se bajó la elegida de cada fila lista')

    // Esperar y bajar todas: no baja nada hasta que llega la última fila; después, las 49.
    await hasta(() => botonTodas(page), (t) => /^Descargar todas|^✓/.test(t || ''), 'el botón no volvió')
    await clickTexto(page, 'button', /^(Descargar todas|✓)/)
    await hasta(pregunta, (x) => x, 'no volvió a preguntar')
    await clickTexto(page, 'button', new RegExp(`^Esperar y bajar los ${items.length}$`))
    const esperando = await hasta(() => botonTodas(page), (t) => /^Esperando/.test(t || ''), 'no dice que espera')
    igual(esperando, `Esperando ${items.length - 2} temas…`, 'el botón mientras espera')
    igual(s.descargas.length, 2, 'mientras espera no puede bajar nada')
    s.soltarTodas()
    await hasta(() => s.descargas.length, (n) => n === 2 + items.length, `al llegar la última fila no se bajaron las ${items.length}`, 20000)
    const fin = await hasta(() => botonTodas(page), (t) => /^✓/.test(t || ''), 'no terminó')
    igual(fin, `✓ ${items.length}/${items.length} descargadas`, 'el resumen de "Descargar todas"')
  }],

  ['station: no queda ningún botón ni texto de "parecidas" (Deezer)', async (page, ctx) => {
    // "Temas parecidos" salió del front en f36: ni en los resultados, ni en la Station, ni como
    // plan B cuando no hay Station. El backend de parecidas sigue (sus tests son de Python).
    const s = await montarStation(page, ctx, { stationFalla: true })
    const rastros = () => page.evaluate(() => {
      const re = /parecid|similitud/i
      const botones = [...document.querySelectorAll('button, a, [role="button"]')]
        .map((b) => [b.textContent, b.getAttribute('aria-label'), b.getAttribute('title')].join(' | ')).filter((t) => re.test(t))
      return { botones, texto: re.test(document.body.innerText) ? document.body.innerText.match(/.{0,40}(parecid|similitud).{0,40}/i)[0] : null }
    })
    await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
    await page.type('input[aria-label="Buscar una canción, artista o género"]', 'elvito gib mir bounce')
    await page.keyboard.press('Enter')
    await page.waitForSelector(s.boton, { timeout: ESPERA_MS })
    igual(await rastros(page), { botones: [], texto: null }, 'en los resultados quedó algo de parecidas')
    // Sin Station: el error lo dice y no ofrece parecidas de Deezer como plan B.
    await page.click(s.boton)
    const err = await hasta(() => page.evaluate(() => [...document.querySelectorAll('.empty p')].map((p) => p.textContent)),
      (x) => x.some((t) => t.includes(s.falla.mensaje)), 'no apareció el error de la Station')
    afirmar(err.some((t) => t.includes(s.tema.titulo)), `el error no dice de qué tema: ${json(err)}`)
    igual(await rastros(page), { botones: [], texto: null }, 'el error de la Station ofrece parecidas')
    // Con Station: tampoco.
    s.stationFalla = false
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === s.items.length, 'no llegaron todas las filas')
    igual(await rastros(page), { botones: [], texto: null }, 'en la Station quedó algo de parecidas')
    igual(s.parecidas, 0, 'se pidió /api/parecidas_lista')
  }],

  ['station: salir de la pantalla corta la carga de versiones (no sigue pidiendo)', async (page, ctx) => {
    const s = await montarStation(page, ctx, { retener: true })
    const cortados = []
    page.on('requestfailed', (req) => { if (new URL(req.url()).pathname === '/api/versiones') cortados.push(req.failure()?.errorText) })
    await abrirStation(page, ctx, s)
    await hasta(() => s.versiones.length, (n) => n === 3, 'no arrancó a pedir versiones')
    await page.click('button[aria-label="Radio DJ"]')
    await page.waitForSelector('.rsem', { timeout: ESPERA_MS })
    await hasta(() => cortados.length, (n) => n === 3, 'al salir no se cortaron los 3 pedidos en vuelo')
    // Un pedido cortado que igual contestara no puede disparar el siguiente de la cola.
    for (const id of [...s.pendientes.keys()]) { const r = s.pendientes.get(id); s.pendientes.delete(id); try { r() } catch { /* ya cortado */ } }
    await api(ctx, '/api/radio/biblioteca')            // una vuelta al server: si la cola siguiera, ya habría pedido
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0))))
    igual(s.versiones.length, 3, 'después de salir de la Station se siguieron pidiendo versiones')
  }],

  ['station: las acciones de cada fila miden 32 px o más y tienen nombre y tooltip (1440 y 400 px)', async (page, ctx) => {
    const s = await montarStation(page, ctx, {})
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === s.items.length, 'no llegaron todas las filas')
    const medir = () => page.evaluate(() => [...document.querySelectorAll('.trk')].slice(0, 3).flatMap((r, i) =>
      [...r.querySelectorAll('.trk-acts button')].map((b) => {
        const rc = b.getBoundingClientRect()
        return { fila: i, nombre: b.getAttribute('aria-label') || '', tooltip: b.getAttribute('title') || '',
          ancho: Math.round(rc.width * 10) / 10, alto: Math.round(rc.height * 10) / 10 }
      })))
    for (const ancho of [1440, 400]) {
      await page.setViewport({ width: ancho, height: 900 })
      await hasta(() => page.evaluate(() => document.documentElement.clientWidth), (w) => w <= ancho, `el viewport no pasó a ${ancho} px`)
      const botones = await medir()
      // Espectrograma, Comparar (si hay más de una versión), Station, Agregar, Descargar.
      afirmar(botones.filter((b) => b.fila === 0).length >= 4, `a ${ancho} px la fila 1 tiene menos acciones de las esperadas: ${json(botones)}`)
      const malos = botones.filter((b) => b.ancho < 32 || b.alto < 32 || !b.nombre.trim() || !b.tooltip.trim())
      igual(malos, [], `a ${ancho} px hay acciones de menos de 32 px o sin nombre/tooltip`)
    }
  }],

  /* ---------- f38: la versión elegida y el resto plegado (diseño A) ---------- */

  ['station (f38): la fila plegada muestra solo la elegida y "+N versiones"; desplegada, una línea por versión con ✓, notas y "Elegida · mejor nota"', async (page, ctx) => {
    // «+1 versión» (singular): la fila 3 contesta con dos versiones, la de la Station y la de
    // YouTube de la respuesta real de la fila 1 (con el título de la fila 3). El resto es la
    // respuesta real del endpoint.
    const st = leerJson('station_respuesta.json')
    const t2 = st.items[2]
    const ytB = leerVersiones()[st.items[0].video_id].respuesta.opciones.find((o) => o.fuente === 'youtube')
    const dos = { exito: true, motivo: null, fallidas: [], opciones: [{ ...ytB, titulo: t2.titulo, artista: t2.artista }, { ...t2, estacion: true }] }
    const s = await montarStation(page, ctx, { respuestas: { [t2.video_id]: dos } })
    const { items } = s
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === items.length, 'no llegaron todas las filas')

    // Solo en la Station la columna se llama "Se baja de".
    igual(await page.evaluate(() => ({ station: !!document.querySelector('.results.results-station'),
      columna: [...document.querySelectorAll('.results-head > div')].map((d) => d.textContent)[5] ?? null })),
    { station: true, columna: 'Se baja de' }, 'el encabezado de la Station')

    const r0 = s.vx[items[0].video_id].respuesta
    const n = r0.opciones.length - 1
    igual(r0.opciones.filter((o) => o.calidad?.grade === 'A').length, 1, 'el archivo de versiones cambió: la fila 1 tenía una sola A')
    const mejorK = r0.opciones.findIndex((o) => o.calidad?.grade === 'A')
    const ytK = r0.opciones.findIndex((o) => o.fuente === 'youtube')
    const plegada = (i) => page.evaluate((k) => {
      const r = [...document.querySelectorAll('.trk')][k]
      const b = r.querySelector('.vmore')
      return {
        elegida: r.querySelector('.vbest')?.textContent.trim() ?? null,
        mas: b ? { texto: b.textContent.trim(), nombre: b.getAttribute('aria-label'), abierta: b.getAttribute('aria-expanded') } : null,
        pastillas: r.querySelectorAll('.vchip').length,
      }
    }, i)
    igual(await plegada(0), { elegida: NOMBRE[r0.opciones[mejorK].fuente],
      mas: { texto: `+${n} versiones`, nombre: `Ver las otras ${n} versiones de ${items[0].titulo}`, abierta: 'false' }, pastillas: 0 },
    'plegada, la fila 1 muestra solo la elegida (la de mejor nota) y cuántas versiones más hay')
    igual(await plegada(2), { elegida: 'YouTube', mas: { texto: '+1 versión', nombre: `Ver la otra versión de ${t2.titulo}`, abierta: 'false' }, pastillas: 0 },
      'con una sola versión más, "+1 versión" en singular')
    igual(await page.evaluate(() => document.querySelectorAll('.trk-versions').length), 0, 'hay sub-listas abiertas sin que nadie las abriera')

    // Desplegar: la sub-lista va justo debajo de su fila, con nombre, y el botón lo dice.
    await page.evaluate(() => document.querySelector('.trk .vmore').click())
    const g = await hasta(() => page.evaluate(() => {
      const b = document.querySelector('.trk .vmore')
      const el = document.getElementById(b.getAttribute('aria-controls'))
      return el && { id: el.id, expandido: b.getAttribute('aria-expanded'), nombre: b.getAttribute('aria-label'), texto: b.textContent.trim(),
        rol: el.getAttribute('role'), grupo: el.getAttribute('aria-label'), debajo: el.previousElementSibling === b.closest('.trk') }
    }), (v) => v, 'la sub-lista de la fila 1 no se desplegó')
    const { id, ...vista } = g
    igual(vista, { expandido: 'true', nombre: `Ocultar las otras ${n} versiones de ${items[0].titulo}`, texto: `+${n} versiones`,
      rol: 'group', grupo: `Versiones de ${items[0].titulo}`, debajo: true }, 'desplegada: aria-expanded, el nombre del botón y el grupo')

    // Una línea por versión, en el orden de la API. ✓ y "Elegida" solo en la elegida;
    // "· mejor nota" solo si la elegida ES la de mejor nota.
    const esperadas = (elegidaK) => r0.opciones.map((o, k) => {
      const nombre = `${NOMBRE[o.fuente]}, opción ${k + 1}`
      const nota = o.solo_preview ? '30 s' : o.calidad?.ok ? o.calidad.grade : '?'
      return {
        plataforma: NOMBRE[o.fuente], elegida: k === elegidaK, tilde: k === elegidaK,
        texto: k === elegidaK ? (k === mejorK ? 'Elegida · mejor nota' : 'Elegida')
          : o.solo_preview ? 'solo 30 s (Go+)' : o.fuente === 'spotify' ? 'se baja buscándolo en YouTube' : nota === '?' ? 'nota sin medir' : '',
        nota, notaLector: o.solo_preview ? null : nota === '?' ? 'nota sin medir' : `nota ${nota}`,
        escuchar: { nombre: `Escuchar la versión ${nombre}`, pulsado: 'false' },
        elegir: k === elegidaK ? null : `Elegir la versión ${nombre} para descargar`,
      }
    })
    igual(await lineasDe(page, id), esperadas(mejorK), 'la sub-lista con la de mejor nota elegida')

    // "Elegir" YouTube: cambia la elegida en la sub-lista, en la fila plegada y en lo que se baja.
    const yt = r0.opciones[ytK]
    const clickEn = (sel) => page.evaluate((q) => { const b = document.querySelector(q); b?.click(); return !!b }, sel)
    afirmar(await clickEn(`#${id} button[aria-label="Elegir la versión YouTube, opción ${ytK + 1} para descargar"]`), 'no hay "Elegir" para YouTube')
    await hasta(() => lineasDe(page, id), (ls) => ls[ytK].elegida, 'Elegir no cambió la elegida')
    igual(await lineasDe(page, id), esperadas(ytK), 'elegida YouTube (nota B): "Elegida" sin "mejor nota", y la A pasa a tener "Elegir"')
    igual((await plegada(0)).elegida, 'YouTube', 'la fila plegada no muestra la nueva elegida')
    igual((await leerStation(page)).filas[0], yt.titulo, 'la fila no muestra el título de la versión elegida')
    igual(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), `Escuchar la versión YouTube, opción ${ytK + 1}`,
      'después de Elegir el foco tiene que quedar en la misma línea (su "Escuchar"), no perderse')
    await descargarFila(page, 0)
    await hasta(() => s.descargas.length, (x) => x === 1, 'la descarga no salió')
    igual(s.descargas[0].url, yt.url, 'después de elegir YouTube en la sub-lista se bajó otra versión')

    // Volver a la de mejor nota: "Elegida · mejor nota" otra vez, y se baja esa.
    afirmar(await clickEn(`#${id} button[aria-label="Elegir la versión ${NOMBRE[r0.opciones[mejorK].fuente]}, opción ${mejorK + 1} para descargar"]`), 'no hay "Elegir" para la de mejor nota')
    await hasta(() => lineasDe(page, id), (ls) => ls[mejorK].elegida, 'no volvió a quedar elegida la de mejor nota')
    igual(await lineasDe(page, id), esperadas(mejorK), 'de vuelta en la de mejor nota')
    await descargarFila(page, 0)
    await hasta(() => s.descargas.length, (x) => x === 2, 'la segunda descarga no salió')
    igual(s.descargas[1].url, r0.opciones[mejorK].url, 'de vuelta en la de mejor nota se bajó otra versión')

    // Plegar: el grupo se va y el botón vuelve a "Ver".
    afirmar(await clickEn('.trk .vmore'), 'no está el botón para plegar')
    await page.waitForSelector(`#${id}`, { hidden: true, timeout: ESPERA_MS })
    igual((await plegada(0)).mas, { texto: `+${n} versiones`, nombre: `Ver las otras ${n} versiones de ${items[0].titulo}`, abierta: 'false' },
      'plegada otra vez, el botón no volvió a su estado')
  }],

  ['station (f38): "Escuchar" suena esa versión en la barra por el proxy sin cambiar la elegida; aria-pressed, y otra vez pausa', async (page, ctx) => {
    const s = await montarStation(page, ctx, {})
    const { items } = s
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === items.length, 'no llegaron todas las filas')
    const r0 = s.vx[items[0].video_id].respuesta
    const total = r0.opciones.length
    const mejorK = r0.opciones.findIndex((o) => o.calidad?.grade === 'A')
    const k = (f, extra = () => true) => r0.opciones.findIndex((o) => o.fuente === f && extra(o))
    const [ytK, scK, ligK] = [k('youtube'), k('soundcloud', (o) => o.estacion), k('ligaudio')]
    const est = await abrirVersiones(page, 0)
    const escuchar = (x) => page.evaluate((sel, j) => document.querySelectorAll(`#${sel} li.vline`)[j].querySelector('button:not(.is-pick)').click(), est.id, x)
    const medio = () => page.evaluate(() => [...window.__medios].find((m) => !m.paused && !m.ended && !m.error)?.src ?? null)
    const fila = () => page.evaluate(() => ({
      ahora: document.querySelector('.trk .trk-now')?.textContent ?? null,
      fuente: document.querySelector('.deck-src')?.textContent ?? null,
      cola: document.querySelector('.deck-count')?.textContent ?? null,
    }))
    const botones = async () => (await lineasDe(page, est.id)).map((l) => l.escuchar)
    const esperados = (suena) => r0.opciones.map((o, j) => ({
      nombre: `${j === suena ? 'Pausar' : 'Escuchar'} la versión ${NOMBRE[o.fuente]}, opción ${j + 1}`, pulsado: String(j === suena) }))

    // YouTube (no es la elegida): suena como audio del backend, con su id.
    await escuchar(ytK)
    const barra = await hasta(() => leerBarra(page), (d) => d && d.estado === 'playing', '"Escuchar" no dejó la barra sonando')
    igual(barra.titulo, r0.opciones[ytK].titulo, 'la barra no muestra la versión de YouTube')
    const src = new URL(await medio())
    igual(src.pathname + '?' + src.searchParams.toString(), `/api/fuente/audio?fuente=youtube&ref=${r0.opciones[ytK].video_id}`,
      'la versión de YouTube no suena por el proxy de audio con su id')
    igual(await sonando(page), ['/api/fuente/audio'], 'tiene que sonar un solo audio')
    await hasta(botones, (b) => b[ytK].pulsado === 'true', 'el "Escuchar" de la versión que suena no quedó pulsado')
    igual(await botones(), esperados(ytK), 'solo el "Escuchar" de YouTube está pulsado y dice "Pausar"')
    igual(await fila(), { ahora: `Sonando: opción ${ytK + 1} · YouTube`, fuente: `YouTube · ${ytK + 1}/${total}, opción ${ytK + 1} de ${total}, solo audio`,
      cola: `tema 1/${items.length}` }, 'la fila y la barra dicen qué versión suena; la cola sigue siendo la de la Station')
    // Escuchar no es elegir: la elegida y lo que se baja siguen siendo la de mejor nota.
    igual((await lineasDe(page, est.id)).map((l) => l.elegida), r0.opciones.map((_, j) => j === mejorK), 'Escuchar cambió la elegida')
    igual(await plegadaDe(page, 0), NOMBRE[r0.opciones[mejorK].fuente], 'Escuchar cambió lo que muestra la fila plegada')

    // Otra vez el mismo: pausa.
    await escuchar(ytK)
    await hasta(() => leerBarra(page), (d) => d.estado === 'paused', 'tocar "Escuchar" de nuevo no pausó')
    await hasta(botones, (b) => b[ytK].pulsado === 'false', 'en pausa el botón sigue pulsado')
    igual(await botones(), esperados(-1), 'en pausa ningún "Escuchar" queda pulsado')
    igual(await sonando(page), [], 'en pausa no puede sonar nada')
    igual((await fila()).ahora, `En pausa: opción ${ytK + 1} · YouTube`, 'la fila no dice que quedó en pausa')

    // La de la Station (SoundCloud): por el proxy, con el id del tema.
    await escuchar(scK)
    await hasta(medio, (u) => (u || '').includes(`fuente=soundcloud&ref=${items[0].video_id}`), 'la versión de SoundCloud no sonó por el proxy con su id')
    await hasta(botones, (b) => b[scK].pulsado === 'true', 'el "Escuchar" de SoundCloud no quedó pulsado')
    igual(await sonando(page), ['/api/fuente/audio'], 'con SoundCloud sonando tiene que haber un solo audio')

    // Un MP3: su archivo, sin pasar por el proxy.
    await escuchar(ligK)
    await hasta(medio, (u) => u === r0.opciones[ligK].stream_url, 'el MP3 no sonó desde su archivo')
    await hasta(botones, (b) => b[ligK].pulsado === 'true', 'el "Escuchar" del MP3 no quedó pulsado')
    igual(await sonando(page), [new URL(r0.opciones[ligK].stream_url).pathname], 'con el MP3 sonando tiene que haber un solo audio')
    igual(s.descargas.length, 0, '"Escuchar" descargó algo')
  }],

  ['station (f38): con teclado, Enter y Espacio despliegan; Escape cierra y devuelve el foco a "+N versiones"', async (page, ctx) => {
    const s = await montarStation(page, ctx, {})
    const { items } = s
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === items.length, 'no llegaron todas las filas')
    const r0 = s.vx[items[0].video_id].respuesta
    const n = r0.opciones.length - 1
    const ytK = r0.opciones.findIndex((o) => o.fuente === 'youtube')
    const estado = () => page.evaluate(() => {
      const b = document.querySelector('.trk .vmore')
      return { abierta: b.getAttribute('aria-expanded'), grupo: !!document.getElementById(b.getAttribute('aria-controls')),
        foco: document.activeElement?.getAttribute('aria-label') ?? null }
    })
    const ver = `Ver las otras ${n} versiones de ${items[0].titulo}`
    const ocultar = `Ocultar las otras ${n} versiones de ${items[0].titulo}`

    await page.focus('.trk .vmore')
    await page.keyboard.press('Enter')
    igual(await hasta(estado, (v) => v.grupo, 'Enter no desplegó'), { abierta: 'true', grupo: true, foco: ocultar }, 'Enter despliega')
    await page.keyboard.press('Enter')
    igual(await hasta(estado, (v) => !v.grupo, 'Enter no volvió a plegar'), { abierta: 'false', grupo: false, foco: ver }, 'Enter otra vez pliega')
    await page.keyboard.press('Space')
    igual(await hasta(estado, (v) => v.grupo, 'Espacio no desplegó'), { abierta: 'true', grupo: true, foco: ocultar }, 'Espacio despliega')
    // Escape sobre el mismo botón: pliega y el foco se queda ahí.
    await page.keyboard.press('Escape')
    igual(await hasta(estado, (v) => !v.grupo, 'Escape sobre el botón no plegó'), { abierta: 'false', grupo: false, foco: ver }, 'Escape sobre "+N versiones"')

    // Escape desde adentro de la sub-lista: pliega y el foco vuelve al botón (no a <body>).
    await page.keyboard.press('Enter')
    await hasta(estado, (v) => v.grupo, 'no volvió a desplegar')
    await page.focus(`.trk-versions button[aria-label="Escuchar la versión YouTube, opción ${ytK + 1}"]`)
    await page.keyboard.press('Escape')
    igual(await hasta(estado, (v) => !v.grupo, 'Escape desde la sub-lista no plegó'), { abierta: 'false', grupo: false, foco: ver },
      'Escape desde la sub-lista devuelve el foco a "+N versiones"')

    // Elegir con teclado: el foco queda en la misma línea; después Escape vuelve al botón.
    await page.keyboard.press('Enter')
    await hasta(estado, (v) => v.grupo, 'no volvió a desplegar')
    await page.focus(`.trk-versions button[aria-label="Elegir la versión YouTube, opción ${ytK + 1} para descargar"]`)
    await page.keyboard.press('Enter')
    await hasta(() => plegadaDe(page, 0), (t) => t === 'YouTube', 'Enter sobre "Elegir" no eligió YouTube')
    igual((await estado()).foco, `Escuchar la versión YouTube, opción ${ytK + 1}`, 'después de elegir con teclado el foco se perdió')
    await page.keyboard.press('Escape')
    igual(await hasta(estado, (v) => !v.grupo, 'Escape no plegó'), { abierta: 'false', grupo: false, foco: ver }, 'Escape después de elegir')
    igual(await sonando(page), [], 'desplegar o elegir con teclado puso algo a sonar')
  }],

  ['station (f38): sin otras versiones la fila dice "solo en SoundCloud" o el motivo, una sola vez; con versiones, el motivo queda bajo el artista', async (page, ctx) => {
    // Fila 1: la respuesta real con los dos MP3 caídos (así la arma el backend: ver
    // test_motivo_cuando_una_plataforma_no_contesta_a_tiempo). Fila 3: una sola versión y SIN
    // motivo. El backend hoy siempre pone uno cuando queda una sola, pero el front tiene la
    // rama "solo en SoundCloud" y es la que se prueba.
    const st = leerJson('station_respuesta.json')
    const vx = leerVersiones()
    const [t0, t2] = [st.items[0], st.items[2]]
    const r0 = vx[t0.video_id].respuesta
    const sinMp3 = { ...r0, motivo: 'No contestó a tiempo: MP3', fallidas: ['ligaudio', 'hitplayer'],
      opciones: r0.opciones.filter((o) => !['ligaudio', 'hitplayer'].includes(o.fuente)) }
    const sola = { exito: true, motivo: null, fallidas: [], opciones: [{ ...t2, estacion: true }] }
    const s = await montarStation(page, ctx, { respuestas: { [t0.video_id]: sinMp3, [t2.video_id]: sola } })
    const { items } = s
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === items.length, 'no llegaron todas las filas')
    const leer = (i) => page.evaluate((k) => {
      const r = [...document.querySelectorAll('.trk')][k]
      return { solo: r.querySelector('.vsolo')?.textContent ?? null, mas: r.querySelector('.vmore')?.textContent.trim() ?? null,
        bajoArtista: [...r.querySelectorAll('.trk-id .trk-note.is-warn')].map((e) => e.textContent), texto: r.innerText }
    }, i)
    const veces = (texto, que) => texto.split(que).length - 1

    const f1 = await leer(1)
    const motivo1 = s.vx[items[1].video_id].respuesta.motivo
    igual({ solo: f1.solo, mas: f1.mas, bajoArtista: f1.bajoArtista }, { solo: motivo1, mas: null, bajoArtista: [] },
      'sin otras versiones el motivo va en la columna de versiones y no bajo el artista')
    igual(veces(f1.texto, motivo1), 1, 'el motivo de la fila 2 aparece más de una vez')

    const f2 = await leer(2)
    igual({ solo: f2.solo, mas: f2.mas, bajoArtista: f2.bajoArtista }, { solo: 'solo en SoundCloud', mas: null, bajoArtista: [] },
      'sin otras versiones ni motivo la fila dice "solo en SoundCloud"')

    const f0 = await leer(0)
    igual({ solo: f0.solo, mas: f0.mas, bajoArtista: f0.bajoArtista }, { solo: null, mas: `+${sinMp3.opciones.length - 1} versiones`, bajoArtista: [sinMp3.motivo] },
      'con otras versiones el motivo sigue bajo el artista')
    igual(veces(f0.texto, sinMp3.motivo), 1, 'el motivo de la fila 1 aparece más de una vez')
  }],

  ['solo MP3 (f38): ningún texto de la Station, la barra, el comparador ni el historial nombra el sitio de un MP3; los nombres para lector numeran', async (page, ctx) => {
    // El historial, con la forma de db.listar_historial: dos descargas de MP3 recién hechas.
    const ahora = new Date().toISOString()
    const descargas = ['hitplayer', 'ligaudio'].map((fuente, j) => ({ id: 2 - j, titulo: 'B WITH U', artista: 'SPÆCE', fuente,
      formato: 'wav', archivo: `B WITH U ${j}.wav`, grade: 'B', color: null, creado_en: ahora }))
    const s = await montarStation(page, ctx, {
      extra: (u, responder) => (u.pathname === '/api/historial' ? (responder({ busquedas: [], playlists: [], descargas }), true) : false),
    })
    const { items } = s
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === items.length, 'no llegaron todas las filas')
    const r0 = s.vx[items[0].video_id].respuesta
    const mp3K = r0.opciones.map((o, j) => (['ligaudio', 'hitplayer'].includes(o.fuente) ? j : -1)).filter((j) => j >= 0)
    igual(mp3K.length, 2, 'el archivo de versiones cambió: la fila 1 tenía dos MP3')

    // Station, con la sub-lista abierta: los dos MP3 se ven "MP3" y el lector los distingue.
    const est = await abrirVersiones(page, 0)
    const ls = await lineasDe(page, est.id)
    igual(mp3K.map((j) => ls[j].plataforma), ['MP3', 'MP3'], 'los MP3 no dicen "MP3" en la sub-lista')
    igual(mp3K.map((j) => ls[j].escuchar.nombre), mp3K.map((j) => `Escuchar la versión MP3, opción ${j + 1}`), 'los nombres de los MP3 no llevan su número de opción')
    igual(new Set(ls.flatMap((l) => [l.escuchar.nombre, l.elegir]).filter(Boolean)).size, ls.length * 2 - 1, 'hay dos botones de la sub-lista con el mismo nombre')
    igual(await rastrosSitio(page), [], 'la Station nombra el sitio de un MP3')

    // Barra: el otro MP3 sonando dice "MP3" y su opción.
    const otro = mp3K.find((j) => !ls[j].elegida)
    await page.evaluate((sel, j) => document.querySelectorAll(`#${sel} li.vline`)[j].querySelector('button:not(.is-pick)').click(), est.id, otro)
    await hasta(() => leerBarra(page), (d) => d && d.estado === 'playing', 'el MP3 no quedó sonando')
    igual(await page.evaluate(() => ({ fuente: document.querySelector('.deck-src')?.textContent, fila: document.querySelector('.trk .trk-now')?.textContent })),
      { fuente: `MP3 · ${otro + 1}/${r0.opciones.length}, opción ${otro + 1} de ${r0.opciones.length}`, fila: `Sonando: opción ${otro + 1} · MP3` },
      'la barra y la fila no dicen "MP3" y su opción')
    igual(await rastrosSitio(page), [], 'con un MP3 sonando, la barra o la fila nombran el sitio')

    // Comparador: una columna por versión, los MP3 como "MP3".
    await page.evaluate(() => document.querySelector('.trk button[aria-label^="Comparar versiones de"]').click())
    const cols = await hasta(() => page.$$eval('[role=dialog] .cmp-src', (e) => e.map((x) => x.textContent)), (v) => v.length === r0.opciones.length,
      'el comparador no mostró una columna por versión')
    igual(cols, r0.opciones.map((o) => NOMBRE[o.fuente]), 'el comparador no nombra cada versión como la fila')
    igual(await rastrosSitio(page), [], 'el comparador nombra el sitio de un MP3')
    await page.keyboard.press('Escape')
    await hasta(() => page.$$eval('[role=dialog] .cmp-src', (e) => e.length), (x) => x === 0, 'Escape no cerró el comparador')

    // Historial: cada descarga dice "formato · MP3 · cuándo".
    await page.click('button[aria-label="Menú"]')
    await page.waitForSelector('.drawer-left.is-open', { timeout: ESPERA_MS })
    afirmar(await page.evaluate(() => { const b = [...document.querySelectorAll('.navitem')].find((x) => x.textContent === 'Historial'); b?.click(); return !!b }),
      'el menú no tiene "Historial"')
    const hist = await hasta(() => page.evaluate(() => [...document.querySelectorAll('.drawer-right.is-open .hist-item')]
      .map((it) => ({ tema: it.querySelector('.truncate')?.textContent ?? null, sub: it.querySelector('.mono')?.textContent ?? null }))),
    (v) => v.length === descargas.length, 'el historial no mostró las descargas')
    igual(hist, descargas.map(() => ({ tema: 'B WITH U — SPÆCE', sub: 'WAV · MP3 · recién' })), 'las descargas del historial')
    igual(await rastrosSitio(page), [], 'el historial nombra el sitio de un MP3')
  }],

  ['búsqueda, modo lista y playlist guardada (f38): siguen con pastillas y "Versiones"; los MP3 dicen "MP3" y numeran', async (page, ctx) => {
    const lib = await api(ctx, '/api/biblioteca')
    const uno = lib.generos.flatMap((g) => g.tracks).find((t) => t.titulo === 'Uno')
    afirmar(uno, '/api/biblioteca no trae «Uno»')
    const opcion = (fuente, extra) => ({ titulo: 'Tema Simulado', artista: 'Artista S', duracion: 200, fuente, thumbnail: null, ...extra })
    const grupos = [{ opciones: [
      opcion('youtube', { url: 'https://www.youtube.com/watch?v=aaaaaaaaaaa', video_id: 'aaaaaaaaaaa' }),
      opcion('ligaudio', { url: 'https://example.invalid/ligaudio/1.mp3', stream_url: 'https://example.invalid/ligaudio/1.mp3' }),
      opcion('hitplayer', { url: 'https://example.invalid/hitplayer/2.mp3', stream_url: 'https://example.invalid/hitplayer/2.mp3' }),
    ] }]
    await page.setRequestInterception(true)
    page.on('request', (req) => {
      const u = new URL(req.url())
      const responder = (body, status = 200) => req.respond({ status, contentType: 'application/json', body: JSON.stringify(body) })
      if (u.pathname === '/api/buscar') return responder({ exito: true, grupos })
      // Formas de /api/buscar_lista, db.listar_historial y db.get_playlist.
      if (u.pathname === '/api/buscar_lista') return responder({ exito: true, grupos, encontradas: 1, total: 1, no_encontradas: [], seed: null })
      if (u.pathname === '/api/historial') return responder({ busquedas: [], descargas: [], playlists: [{ id: 7, nombre: 'Guardada', total: 1, creado_en: new Date().toISOString() }] })
      if (u.pathname === '/api/historial/playlist/7') return responder({ exito: true, data: { groups: grupos, sel: [0], origen: 'busqueda', query: 'tema simulado', nombre: 'Guardada' } })
      if (u.pathname === '/api/calidad') return responder({ ok: false, grade: '?' })
      if (u.pathname === '/api/meta') return responder({ bpm: null, genero: null })
      if (u.origin !== new URL(ctx.url).origin) return req.abort()
      return req.continue()
    })
    const pastillas = () => page.evaluate(() => ({
      station: !!document.querySelector('.results-station'),
      columna: [...document.querySelectorAll('.results-head > div')].map((d) => d.textContent)[5] ?? null,
      plegado: document.querySelectorAll('.vpick, .vbest, .vmore, .vsolo, .trk-versions').length,
      pastillas: [...document.querySelectorAll('.trk .vchip')].map((b) => ({ texto: b.textContent.trim(), nombre: b.getAttribute('aria-label') })),
    }))
    const esperado = { station: false, columna: 'Versiones', plegado: 0, pastillas: [
      { texto: 'YouTube', nombre: 'Opción 1: YouTube (elegida)' }, { texto: '2MP3', nombre: 'Opción 2: MP3' }, { texto: '3MP3', nombre: 'Opción 3: MP3' }] }

    // Búsqueda.
    await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
    await page.type('input[aria-label="Buscar una canción, artista o género"]', 'tema simulado')
    await page.keyboard.press('Enter')
    await page.waitForSelector('.trk .vchip', { timeout: ESPERA_MS })
    igual(await pastillas(), esperado, 'la búsqueda tiene que seguir con pastillas y "Versiones"')
    igual(await rastrosSitio(page), [], 'la búsqueda nombra el sitio de un MP3')

    // Modo lista.
    const menu = async (item) => {
      await page.click('button[aria-label="Menú"]')
      await page.waitForSelector('.drawer-left.is-open', { timeout: ESPERA_MS })
      afirmar(await page.evaluate((t) => { const b = [...document.querySelectorAll('.navitem')].find((x) => x.textContent === t); b?.click(); return !!b }, item),
        `el menú no tiene "${item}"`)
    }
    await menu('Lista')
    await page.waitForSelector('textarea[aria-label="Pegá tu lista de temas"]', { timeout: ESPERA_MS })
    await page.type('textarea[aria-label="Pegá tu lista de temas"]', 'Artista S - Tema Simulado')
    await clickTexto(page, 'button', /^Buscar lista/)
    // Cada vista se reconoce por su rótulo: la anterior también tenía pastillas.
    const rotulo = () => page.evaluate(() => (document.querySelector('.trk .vchip') ? document.querySelector('.cluster > .eyebrow')?.textContent ?? null : null))
    await hasta(rotulo, (t) => t === '1/1 encontradas', 'el modo lista no mostró sus resultados')
    igual(await pastillas(), esperado, 'el modo lista tiene que seguir con pastillas y "Versiones"')

    // Playlist guardada (desde el historial): se abre como la búsqueda que la armó.
    await menu('Historial')
    await page.waitForSelector('.drawer-right.is-open .hist-open', { timeout: ESPERA_MS })
    await page.click('.drawer-right.is-open .hist-open')
    await hasta(rotulo, (t) => t === 'Resultados · tema simulado', 'la playlist guardada no se abrió')
    igual(await pastillas(), esperado, 'la playlist guardada tiene que seguir con pastillas y "Versiones"')
  }],

  ['station (f38): a 400 px la sub-lista abierta va a todo el ancho de la tarjeta y nada se sale', async (page, ctx) => {
    const s = await montarStation(page, ctx, {})
    const { items } = s
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === items.length, 'no llegaron todas las filas')
    await page.setViewport({ width: 400, height: 860 })
    await hasta(() => page.evaluate(() => document.documentElement.clientWidth), (w) => w <= 400, 'el viewport no pasó a 400 px')
    const est = await abrirVersiones(page, 0)
    const d = await desbordeDe(page)
    afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `Station a 400 px con la sub-lista abierta: hay contenido fuera del ancho: ${json(d)}`)
    const geo = await page.evaluate((id) => {
      const g = document.getElementById(id)
      const fila = g.previousElementSibling.getBoundingClientRect()
      const rg = g.getBoundingClientRect()
      const lista = g.querySelector('.vlist').getBoundingClientRect()
      const elegida = g.querySelector('.vline.is-chosen .vline-lbl')
      return {
        // Cuánto le falta a la sub-lista para ocupar el ancho de su fila, de cada lado.
        izq: Math.round(rg.left - fila.left), der: Math.round(fila.right - rg.right),
        // Sangría de las líneas dentro de la sub-lista (en escritorio van alineadas con el tema).
        sangria: Math.round(lista.left - rg.left),
        // "Elegida · mejor nota" en su renglón, entero y a la vista.
        etiqueta: { texto: elegida.textContent, visible: window.__visible(elegida), cortada: elegida.scrollWidth > elegida.clientWidth },
        botones: [...g.querySelectorAll('button')].filter((b) => { const r = b.getBoundingClientRect(); return r.left < rg.left || r.right > rg.right }).length,
      }
    }, est.id)
    afirmar(geo.izq <= 16 && geo.der <= 16, `a 400 px la sub-lista no va a todo el ancho de su fila: ${json(geo)}`)
    afirmar(geo.sangria <= 16, `a 400 px las líneas de la sub-lista siguen con la sangría de escritorio: ${json(geo)}`)
    igual(geo.etiqueta, { texto: 'Elegida · mejor nota', visible: true, cortada: false }, 'a 400 px "Elegida · mejor nota" no se ve entera')
    igual(geo.botones, 0, 'a 400 px hay botones de la sub-lista fuera de ella')
  }],
]

// Nombre visible de cada plataforma. Contrato de f38 (pedido del dueño): los MP3 dicen solo
// "MP3"; el nombre del sitio no aparece en ningún texto de la app.
const NOMBRE = { youtube: 'YouTube', soundcloud: 'SoundCloud', spotify: 'Spotify', ligaudio: 'MP3', hitplayer: 'MP3' }
const SITIO_MP3 = 'ligaudio|hitplayer|mp3 directo'

// Dónde se nombra el sitio de un MP3 (o "MP3 directo"): texto de la página y los atributos que
// una persona ve o escucha (tooltip, nombre para lector, alt, placeholder). Vacío = en ningún lado.
const rastrosSitio = (page) => page.evaluate((fuente) => {
  const re = new RegExp(fuente, 'i')
  const out = []
  const m = document.body.innerText.match(new RegExp(`.{0,30}(${fuente}).{0,30}`, 'i'))
  if (m) out.push(`texto: ${m[0]}`)
  for (const e of document.querySelectorAll('[title], [aria-label], [alt], [placeholder]')) {
    for (const a of ['title', 'aria-label', 'alt', 'placeholder']) {
      const v = e.getAttribute(a)
      if (v && re.test(v)) out.push(`${a}: ${v}`)
    }
  }
  return out
}, SITIO_MP3)

/* ---------- Station (f36) ---------- */

const leerJson = (...p) => JSON.parse(fs.readFileSync(path.join(AQUI, '..', '..', 'tests', 'fixtures', ...p), 'utf8'))
const leerVersiones = () => leerJson('versiones_respuestas.json')

// Intercepta todo lo que la Station necesita, sin red. /api/station contesta la grabada (con
// `primeros` adelante, en lugar de los primeros temas); /api/versiones, lo que el endpoint real
// devuelve (versiones_respuestas.json) y, para los temas que no están ahí, la forma del "no lo
// encontré" de ese mismo archivo con el tema pedido. Con `retener`, cada /api/versiones espera
// a que el test lo suelte (s.soltar(id) / s.soltarTodas()). `respuestas` (f38) reemplaza la
// respuesta de /api/versiones de algún tema ({video_id: respuesta}); `extra(u, responder)`
// contesta otras rutas (devuelve true si contestó). Los MP3 de las versiones (example.invalid)
// suenan con el WAV de la base de juguete y el espectrograma contesta 404 (el modal lo dice).
async function montarStation(page, ctx, { retener = false, primeros = [], stationFalla = false, respuestas = {}, extra = null } = {}) {
  const respuesta = leerJson('station_respuesta.json')
  const vx = leerVersiones()
  const items = [...primeros, ...respuesta.items.slice(primeros.length)]
  const station = { ...respuesta, items, total: items.length }
  const noEncontrado = Object.values(vx).find((x) => x.respuesta.opciones.length === 1 && !x.tema.solo_preview).respuesta
  const lib = await api(ctx, '/api/biblioteca')
  const uno = lib.generos.flatMap((g) => g.tracks).find((t) => t.titulo === 'Uno')
  afirmar(uno, '/api/biblioteca no trae «Uno»')
  const wav = Buffer.from(await (await fetch(`${ctx.url}/api/audio/${uno.id}`)).arrayBuffer())
  const tema = { titulo: respuesta.semilla.titulo, artista: 'ELVITO', duracion: 291, fuente: 'soundcloud', thumbnail: null,
    url: 'https://soundcloud.com/elvitoelvito/elvito-x-w-choppa-gib-mir', video_id: respuesta.semilla.video_id }
  const s = {
    respuesta: station, items, vx, tema, retener, stationFalla,
    falla: { exito: false, motivo: 'station_vacia', mensaje: 'SoundCloud no tiene una Station para este tema.' },
    boton: `button[aria-label="Station de SoundCloud de ${tema.titulo}"]`,
    versiones: [], descargas: [], calidades: [], mp3: [], parecidas: 0, pendientes: new Map(),
    soltar(id) { const r = s.pendientes.get(id); afirmar(r, `no hay un pedido de versiones de ${id} para soltar`); s.pendientes.delete(id); r() },
    soltarTodas() { s.retener = false; for (const id of [...s.pendientes.keys()]) s.soltar(id) },
  }
  await page.setRequestInterception(true)
  page.on('request', (req) => {
    const u = new URL(req.url())
    // Un pedido retenido que la página ya cortó (salir de la pantalla) no se puede contestar.
    const responder = (body, status = 200) => req.respond({ status, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {})
    if (u.pathname === '/api/buscar') return responder({ exito: true, grupos: [{ opciones: [tema] }] })
    if (u.pathname === '/api/calidad') { s.calidades.push(u.searchParams.get('url')); return responder({ ok: false, grade: '?' }) }
    if (u.pathname === '/api/meta') return responder({ bpm: null, genero: null })
    if (u.pathname === '/api/station') return responder(s.stationFalla ? s.falla : station)
    if (u.pathname === '/api/parecidas_lista') { s.parecidas++; return responder({ exito: false }) }
    if (u.pathname === '/api/versiones') {
      const b = JSON.parse(req.postData())
      s.versiones.push(b)
      const id = b.tema.video_id
      const r = respuestas[id] || vx[id]?.respuesta || { ...noEncontrado, opciones: [{ ...b.tema, estacion: true }] }
      const contestar = () => responder(r)
      if (s.retener) { s.pendientes.set(id, contestar); return }
      return contestar()
    }
    if (u.pathname === '/api/descargar') { s.descargas.push(JSON.parse(req.postData())); return responder({ exito: true, calidad: { grade: 'A' } }) }
    if (u.pathname === '/api/fuente/audio/info') return responder({ preview: false, duracion: 245 })
    if (u.pathname === '/api/fuente/audio') return req.respond({ status: 200, contentType: 'audio/wav', body: wav })
    if (u.hostname === 'example.invalid' && u.pathname.endsWith('.mp3')) {
      s.mp3.push(u.href)
      return req.respond({ status: 200, contentType: 'audio/wav', body: wav })
    }
    if (u.pathname === '/api/spectro') return responder({ error: 'sin espectrograma en el E2E' }, 404)
    if (extra && extra(u, responder)) return
    if (u.origin !== new URL(ctx.url).origin) return req.abort()
    return req.continue()
  })
  return s
}

async function abrirStation(page, ctx, s) {
  await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
  await page.type('input[aria-label="Buscar una canción, artista o género"]', 'elvito gib mir bounce')
  await page.keyboard.press('Enter')
  await page.waitForSelector(s.boton, { timeout: ESPERA_MS })
  // Con teclado: el botón se alcanza con foco y se activa con Enter.
  await page.focus(s.boton)
  await page.keyboard.press('Enter')
  await page.waitForSelector('h1.station-title', { timeout: ESPERA_MS })
}

// Avance ("N de 49" mientras carga, el resumen al terminar) y el título de cada fila.
const leerStation = (page) => page.evaluate(() => {
  const t = document.querySelector('.station-progress')?.textContent.replace(/\s+/g, ' ').trim() || null
  const m = t && t.match(/^(\d+ de \d+) temas con versiones/)
  return { progreso: m ? m[1] : t, filas: [...document.querySelectorAll('.trk')].map((r) => r.querySelector('.trk-title')?.textContent ?? null) }
})

// Todo el texto de una fila, visible y accesible (notas, motivos, nombres de botones).
const filaTexto = (page, i) => page.evaluate((k) => {
  const r = [...document.querySelectorAll('.trk')][k]
  return r ? [r.innerText, ...[...r.querySelectorAll('[aria-label]')].map((e) => e.getAttribute('aria-label'))].join(' | ') : ''
}, i)

// El botón de "Descargar todas" (cambia de texto: Bajando…, Esperando…, ✓ N/M descargadas).
const botonTodas = (page) => page.evaluate(() => [...document.querySelectorAll('button')]
  .map((b) => b.textContent.replace(/\s+/g, ' ').trim()).find((t) => /^(Descargar todas|Bajando|Esperando|✓)/.test(t)) ?? null)

async function clickTexto(page, sel, re) {
  const ok = await page.evaluate((q, fuente) => {
    const b = [...document.querySelectorAll(q)].find((e) => new RegExp(fuente).test(e.textContent.trim()) && !e.disabled)
    if (b) b.click()
    return !!b
  }, sel, re.source)
  afirmar(ok, `no hay un ${sel} habilitado que diga ${re}`)
}

async function descargarFila(page, i) {
  const ok = await page.evaluate((k) => {
    const b = [...document.querySelectorAll('.trk')][k]?.querySelector('button[aria-label^="Descargar"]:not([disabled])')
    if (b) b.click()
    return !!b
  }, i)
  afirmar(ok, `la fila ${i + 1} no tiene un botón de descarga habilitado`)
}

// La versión elegida de una fila y cómo elegir otra. Son lo ÚNICO de los casos de f36/f37 que
// depende del markup de la columna VERSIONES; los casos verifican comportamiento (qué se elige,
// qué se descarga), no la disposición.
//
// Diseño A (f38): la fila muestra solo la elegida (.vbest) y "+N versiones" (.vmore) despliega
// debajo la sub-lista, donde la elegida tiene el ✓ y las demás un botón "Elegir". Estas
// funciones abren la sub-lista si hace falta y la dejan como estaba: abierta, corre los
// nth-child de las filas de abajo. `elegidaDe` devuelve el nombre de siempre, "Opción N:
// plataforma, nota X", armado con lo que SE VE en la sub-lista; si la fila plegada y la
// sub-lista no coinciden en cuál es la elegida, lo dice en vez de elegir una de las dos.
async function abrirVersiones(page, i) {
  const est = await page.evaluate((k) => {
    const b = [...document.querySelectorAll('.trk')][k]?.querySelector('.vmore')
    if (!b) return null
    const abierta = b.getAttribute('aria-expanded') === 'true'
    if (!abierta) b.click()
    return { abierta, id: b.getAttribute('aria-controls') }
  }, i)
  if (est) await page.waitForSelector(`#${est.id}`, { timeout: ESPERA_MS })
  return est
}

async function cerrarVersiones(page, i, est) {
  if (!est || est.abierta) return
  await page.evaluate((k) => [...document.querySelectorAll('.trk')][k]?.querySelector('.vmore[aria-expanded="true"]')?.click(), i)
  await page.waitForSelector(`#${est.id}`, { hidden: true, timeout: ESPERA_MS })
}

// Cada línea de la sub-lista `id`, como se ve y como la lee un lector de pantalla.
const lineasDe = (page, id) => page.evaluate((sel) => [...document.querySelectorAll(`#${sel} li.vline`)].map((li) => {
  const escuchar = li.querySelector('button:not(.is-pick)')
  return {
    plataforma: li.querySelector('.vline-src .truncate')?.textContent ?? null,
    elegida: li.classList.contains('is-chosen'),
    tilde: !!li.querySelector('.vline-ok svg'),
    texto: li.querySelector('.vline-lbl')?.textContent ?? null,
    nota: li.querySelector('.vline-grade')?.textContent ?? null,
    notaLector: li.querySelector('.vline-grade [aria-label]')?.getAttribute('aria-label') ?? null,
    escuchar: escuchar ? { nombre: escuchar.getAttribute('aria-label'), pulsado: escuchar.getAttribute('aria-pressed') } : null,
    elegir: li.querySelector('button.is-pick')?.getAttribute('aria-label') ?? null,
  }
}), id)

const plegadaDe = (page, i) => page.evaluate((k) =>
  [...document.querySelectorAll('.trk')][k]?.querySelector('.vbest .truncate')?.textContent ?? null, i)

async function elegidaDe(page, i) {
  const est = await abrirVersiones(page, i)
  try {
    const plegada = await plegadaDe(page, i)
    if (!est) return plegada == null ? null : `Opción 1: ${plegada}`
    const ls = await lineasDe(page, est.id)
    const marcadas = ls.map((l, k) => (l.elegida || l.tilde ? k : -1)).filter((k) => k >= 0)
    if (marcadas.length !== 1) return `la sub-lista tiene ${marcadas.length} elegidas: ${json(ls)}`
    const l = ls[marcadas[0]]
    if (!l.elegida || !l.tilde) return `la elegida de la sub-lista no tiene su marca y su ✓: ${json(l)}`
    if (l.plataforma !== plegada) return `la fila plegada dice ${plegada} y la sub-lista elige ${l.plataforma}`
    return `Opción ${marcadas[0] + 1}: ${l.plataforma}, nota ${l.nota}`
  } finally {
    await cerrarVersiones(page, i, est)
  }
}

async function elegir(page, i, plataforma) {
  const est = await abrirVersiones(page, i)
  afirmar(est, `la fila ${i + 1} no tiene otras versiones para elegir`)
  try {
    const ok = await page.evaluate((id, p) => {
      const li = [...document.querySelectorAll(`#${id} li.vline`)].find((x) => x.querySelector('.vline-src .truncate')?.textContent === p)
      const b = li?.querySelector('button.is-pick')
      if (b) b.click()
      return !!b
    }, est.id, plataforma)
    afirmar(ok, `la fila ${i + 1} no tiene una versión de ${plataforma} para elegir`)
  } finally {
    await cerrarVersiones(page, i, est)
  }
}

// `.app` tiene overflow-x:clip: la página NUNCA scrollea de costado, lo que se pase del
// ancho queda cortado e invisible (peor que un scroll). Por eso no alcanza con mirar
// scrollWidth: se busca cualquier elemento visible que se salga de la caja que lo
// recorta. Dos clases de contenedor:
//  - overflow-x auto|scroll: se desliza de costado A PROPÓSITO (los estantes de la home):
//    lo de adentro se alcanza scrolleando, se excusa.
//  - overflow-x hidden|clip: RECORTA. Lo que se sale de ese contenedor queda cortado e
//    invisible, igual que lo que se sale del viewport: es un fallo.
// Cada elemento se juzga contra su ancestro más cercano de alguno de los dos tipos (o el
// viewport); el contenedor, a su vez, contra el suyo.
function desbordeDe(page) {
  return page.evaluate(() => {
      const ancho = document.documentElement.clientWidth
      const fuera = [...document.querySelectorAll('body *')].filter((e) => {
        const r = e.getBoundingClientRect()
        if (r.width <= 1 || r.height <= 1 || !window.__visible(e)) return false
        // Un panel fijo que está entero fuera de la pantalla es un cajón cerrado (off-canvas),
        // no contenido cortado.
        for (let a = e; a && a !== document.body; a = a.parentElement) {
          if (getComputedStyle(a).position === 'fixed') {
            const ra = a.getBoundingClientRect()
            if (ra.right <= 0 || ra.left >= ancho) return false
            break
          }
        }
        let caja = { left: 0, right: ancho }
        if (getComputedStyle(e).position !== 'fixed') {
          for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) {
            const cs = getComputedStyle(a)
            if (/auto|scroll/.test(cs.overflowX)) return false
            if (/hidden|clip/.test(cs.overflowX)) {
              const ra = a.getBoundingClientRect()
              // La caja del contenedor, sin recortarla al viewport: si el contenedor está en
              // un estante que se desliza, lo juzga su propio ancestro.
              caja = { left: ra.left, right: ra.right }
              break
            }
            if (cs.position === 'fixed') break    // lo fijo solo lo recorta el viewport
          }
        }
        return r.right > caja.right + 1 || r.left < caja.left - 1
      }).slice(0, 4).map((e) => `${e.tagName.toLowerCase()}.${String(e.className).split(' ')[0]} (${Math.round(e.getBoundingClientRect().left)}..${Math.round(e.getBoundingClientRect().right)})`)
      return { scroll: document.documentElement.scrollWidth, ancho, fuera }
  })
}

export async function correr(ctx) {
  const resultados = []
  for (const [nombre, caso] of CASOS) {
    if (ctx.solo && !nombre.includes(ctx.solo)) continue
    const t0 = Date.now()
    let page = null
    try {
      page = await nuevaPagina(ctx)
      await caso(page, ctx)
      const errores = erroresDe.get(page)
      if (errores.length) throw new Error(`errores de JS en la página: ${errores.slice(0, 3).join(' | ')}`)
      resultados.push({ nombre, ok: true, ms: Date.now() - t0 })
    } catch (e) {
      resultados.push({ nombre, ok: false, ms: Date.now() - t0, error: String(e && e.message ? e.message : e).split('\n').slice(0, 14).join('\n') })
    } finally {
      if (page) await page.close().catch(() => {})
    }
  }
  return resultados
}
