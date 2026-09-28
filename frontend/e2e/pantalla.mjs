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

// Qué suena: la ruta de cada medio que está reproduciendo (sin pausa y sin terminar).
const sonando = (page) => page.evaluate(() =>
  [...window.__medios].filter((m) => !m.paused && !m.ended).map((m) => new URL(m.currentSrc || m.src, location.href).pathname))

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

    const real = await hasta(() => leerGuardado(page), (v) => json(v.resumen) === json(t3.resumen), 'el resumen en pantalla no llegó al de la API')
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
]

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
