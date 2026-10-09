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
  // f50: una fila de la lista del set (maestro/detalle). BPM, key y duración van en celdas; la
  // energía no está en la fila (va en el detalle: ver `__detalle`).
  window.__datosFila = (row) => {
    const k = row.querySelector('.c-key')
    const duda = k?.querySelector('.rduda')
    const hayKey = !!k?.querySelector('.rkey-clasica')
    return {
      BPM: row.querySelector('.c-bpm b')?.textContent ?? null,
      key: {
        camelot: k?.querySelector('b')?.textContent ?? null,
        clasica: hayKey ? k.querySelector('.rkey-clasica').textContent : null,
        duda: duda && duda.textContent === '?' && visible(duda) ? duda.getAttribute('aria-label') : null,
      },
      Dur: row.querySelector('.c-dur')?.textContent ?? null,
    }
  }
  // f50: lo que muestra el detalle del track elegido.
  window.__detalle = () => {
    const d = document.querySelector('.rdet')
    if (!d) return null
    return {
      titulo: d.querySelector('#r-det-h')?.textContent ?? null,
      artista: d.querySelector('.rdet-artista')?.textContent ?? null,
      chips: [...d.querySelectorAll('.rdet-chips > .mb b, .rdet-chips .mb-key > b')].map((b) => b.textContent),
      motivo: d.querySelector('.rdet-motivo')?.textContent ?? null,
      energia: d.querySelector('.rdet-energia .mono')?.textContent ?? null,
      why: d.querySelector('.rdet-why')?.textContent ?? null,
    }
  }
}

const erroresDe = new WeakMap()

async function nuevaPagina(ctx) {
  const page = await ctx.browser.newPage()
  await page.setViewport({ width: 1440, height: 900 })
  const errores = []
  page.on('pageerror', (e) => errores.push(String(e)))
  // Un error de consola no es del front y se ignora: los 404 de /api/cover (track sin
  // carátula: el front dibuja el placeholder). Cualquier otro error cuenta.
  page.on('console', (m) => {
    const t = m.text()
    if (m.type() === 'error' && !/Failed to load resource/.test(t)) errores.push(t)
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

// f50: la semilla, los controles y los sets guardados están en un panel que se pliega solo
// cuando hay un set. Para tocarlos, se despliega con su botón (como lo haría el DJ).
async function desplegarArmado(page) {
  const abierto = await page.$eval('.rarma-toggle', (b) => b.getAttribute('aria-expanded'))
  if (abierto === 'true') return
  await page.click('.rarma-toggle')
  await hasta(() => page.evaluate(() => window.__visible(document.querySelector('#r-arma-cuerpo .rsem, #r-arma-cuerpo .rpanel'))),
    (v) => v, 'el panel de armado no se desplegó')
}

async function elegirSemilla(page, titulo) {
  await desplegarArmado(page)
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

// f50: lo mismo para una fila de la lista del set (sin la energía, que va en el detalle).
function esperadoFila(t, leyenda) {
  const { BPM, key, Dur } = esperadoDatos(t, leyenda)
  return { BPM, key, Dur }
}

// f50: elige la fila del paso `n` (clic en su título) y espera a que el detalle sea el suyo.
async function elegirFila(page, n) {
  const ya = await page.$eval(`.rpaso[data-n="${n}"]`, (r) => r.classList.contains('is-sel')).catch(() => null)
  afirmar(ya !== null, `no hay una fila ${n} en la lista del set`)
  if (ya) return
  await page.click(`.rpaso-sel[data-n="${n}"]`)
  await hasta(() => page.evaluate((i) => ({
    sel: document.querySelector('.rpaso.is-sel')?.dataset.n ?? null,
    titulo: document.querySelector('#r-det-h')?.textContent ?? null,
    fila: document.querySelector(`.rpaso[data-n="${i}"] .rpaso-titulo`)?.textContent ?? null,
  }), n), (v) => v.sel === String(n) && v.titulo === v.fila, `elegí la fila ${n} y el detalle no la muestra`)
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
  await desplegarArmado(page)
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
        datos: window.__datosFila(p),
      })),
      // Todas las calificaciones quedan montadas en el detalle (se ve la del track elegido):
      // se leen todas, en orden de transición.
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
  semilla: p.es_semilla, datos: esperadoFila(p.track, s.leyenda_key),
}))

// Calificación de la transición `n` según la API, y el resumen.
async function calificacionApi(ctx, id, n) {
  const s = await setGuardadoApi(ctx, id)
  const t = s.transiciones.find((x) => x.n === n)
  return { calificacion: t.calificacion, motivo: t.motivo, resumen: s.resumen }
}

// f50: la calificación de la transición `n` (del paso n al n + 1) está en el detalle del paso
// n + 1: primero se elige esa fila, después se toca el nivel (tiene que estar a la vista).
const clickNivel = async (page, n, nivel) => {
  await elegirFila(page, n + 1)
  await page.click(`.rcal[data-n="${n}"] .rcal-opt.is-${nivel}`)
}

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
    igual(esperadoPasos(s), visto.pasos.map((p) => ({ n: String(p.n), titulo: p.track.titulo, artista: p.track.artista || '—', motivo: p.motivo, semilla: p.es_semilla, datos: esperadoFila(p.track, visto.leyenda_key) })),
      'lo guardado no es lo que se veía')
    // La energía no está en la fila (f50: va en el detalle), pero la foto la guarda igual.
    igual(s.pasos.map((p) => p.track.energia_pct), visto.pasos.map((p) => p.track.energia_pct), 'la energía guardada no es la que se veía')
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
    // Regular con el teclado (Espacio sobre el radio), en el detalle del paso 3.
    await elegirFila(page, 3)
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
      // El detalle del paso 2 también es la foto: su BPM, su porqué y la energía guardada.
      await elegirFila(page, 2)
      const det = await page.evaluate(() => window.__detalle())
      igual({ bpm: det.chips[0], motivo: det.motivo, energia: det.energia },
        { bpm: bpm1(paso.bpm), motivo: s.pasos[1].motivo, energia: `${s.pasos[0].track.energia_pct ?? '—'} → ${paso.energia_pct ?? '—'}` },
        'el detalle del paso 2 no muestra la foto (BPM, porqué del motor y energía guardados)')
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
        play: p.querySelector('.c-play button.rplay')?.getAttribute('aria-label') ?? null,
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
    await desplegarArmado(page)
    await page.click('#r-cfg-largo', { clickCount: 3 })
    await page.type('#r-cfg-largo', largoHoy)
    await hasta(() => page.$eval('#r-cfg-largo', (i) => i.value), (v) => v === largoHoy, 'no pude cambiar el largo de los controles')
    const paso = visto.pasos[1].track
    const nuevo = Math.round((paso.bpm + 0.3) * 10) / 10
    const antesLista = (await api(ctx, '/api/radio/sets')).sets.map((x) => x.id)
    const { antes } = mutarBase(ctx, 'bpm', paso.titulo, String(nuevo))
    try {
      // Al centro antes del clic: con el armado desplegado el botón queda bajo la barra fija de arriba si solo se lo trae «a la vista».
      await page.$eval('.rguardar', (b) => b.scrollIntoView({ block: 'center' }))
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
      // Al centro antes del clic: con el armado desplegado el botón queda bajo la barra fija de arriba si solo se lo trae «a la vista».
      await page.$eval('.rguardar', (b) => b.scrollIntoView({ block: 'center' }))
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

/* ---------- editor de cues (f48) ----------
   Las marcas viven en la base de juguete y el server es el mismo para todos los casos: cada
   caso arranca borrando por la API las marcas de los temas que usa. Lo esperado sale de la
   API (la lista que devolvió el servidor) y de la definición del beat (60 / BPM de la API). */

const rutaMarcas = (id) => `/api/radio/tracks/${id}/marcas`

async function limpiarMarcas(ctx, id) {
  const r = await apiPedir(ctx, rutaMarcas(id))
  afirmar(r.status === 200, `GET ${rutaMarcas(id)} contestó ${r.status}: ${json(r.data)}`)
  for (const m of r.data.marcas) await apiPedir(ctx, `${rutaMarcas(id)}/${m.id}`, 'DELETE')
}

const marcasApi = async (ctx, id) => (await apiPedir(ctx, rutaMarcas(id))).data.marcas

// m:ss.mmm (contrato de la tabla; escrito acá, no importado del front).
const tiempoTabla = (s) => {
  const ms = Math.round(s * 1000)
  return `${Math.floor(ms / 60000)}:${String(Math.floor((ms % 60000) / 1000)).padStart(2, '0')}.${String(ms % 1000).padStart(3, '0')}`
}
const ms3 = (s) => Math.round(s * 1000) / 1000

// La tabla de marcas como se ve, y como tendría que verse según la API.
const leerTabla = (page) => page.$$eval('.cue-tabla tbody tr[data-id]', (rows) => rows.map((r) => ({
  id: Number(r.dataset.id), tipo: r.dataset.tipo, tag: r.querySelector('.cue-tag')?.textContent ?? null,
  tiempos: [...r.querySelectorAll('.cue-in-t')].map((i) => i.value), nombre: r.querySelector('.cue-in-n')?.value ?? null,
})))
const esperadoTabla = (marcas) => marcas.map((m) => ({
  id: m.id, tipo: m.tipo, tag: m.tipo === 'cue' ? String(m.num + 1) : m.tipo === 'memory' ? 'M' : 'L',
  tiempos: [m.inicio, ...(m.tipo === 'loop' ? [m.fin] : [])].map(tiempoTabla), nombre: m.nombre || '',
}))

const estadoEditor = (page) => page.evaluate(() => document.querySelector('.cue-estado')?.textContent ?? '')

// Radio → set de «Uno» → la fila del tema `titulo` (f50: el editor está en la pestaña «Cues y
// loops» del detalle, abierta por defecto), y espera a que el editor lo cargue.
async function abrirEditor(page, ctx, titulo = 'Uno', { navegar = true } = {}) {
  await abrirRadio(page, ctx, { navegar })
  await elegirSemilla(page, 'Uno')
  await armarSet(page)
  const n = await page.evaluate((t) => [...document.querySelectorAll('.rpaso')].find((r) => r.querySelector('.rpaso-titulo')?.textContent === t)?.dataset.n ?? null, titulo)
  afirmar(n, `el set de «Uno» no tiene a «${titulo}»`)
  await elegirFila(page, Number(n))
  await hasta(() => page.evaluate(() => ({
    titulo: document.querySelector('#r-det-h')?.textContent ?? null,
    pestana: document.querySelector('[role=tab][aria-selected=true]')?.id ?? null,
    estado: document.querySelector('#r-panel-cues:not([hidden]) .cue-estado')?.textContent ?? '',
  })), (v) => v.titulo === titulo && v.pestana === 'r-tab-cues' && /guardadas en la biblioteca/.test(v.estado),
  `el editor no terminó de abrir «${titulo}» en la pestaña «Cues y loops»`)
}

// Aprieta una tecla y espera la escritura que tiene que disparar (POST/PATCH/DELETE).
async function teclaQueGuarda(page, tecla, metodo = 'POST') {
  const r = page.waitForResponse((x) => x.request().method() === metodo && /\/api\/radio\/tracks\/[0-9a-f]{16}\/marcas/.test(new URL(x.url()).pathname), { timeout: ESPERA_MS })
  await page.keyboard.press(tecla)
  const res = await r
  return { status: res.status(), data: await res.json() }
}

const pedidosDeMarcas = (page) => {
  const lista = []
  page.on('request', (q) => { if (q.method() !== 'GET' && /\/api\/radio\/tracks\/.+\/marcas/.test(new URL(q.url()).pathname)) lista.push(`${q.method()} ${new URL(q.url()).pathname}`) })
  return lista
}

const CASOS_CUES = [

  ['cues: C, M e I/O con el teclado → la tabla dice los tiempos de la API, y después de recargar siguen', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    await limpiarMarcas(ctx, uno.id)
    await abrirEditor(page, ctx)
    const cab = await page.evaluate(() => window.__detalle().chips)
    igual(cab[0], bpm1(uno.bpm), 'el BPM del detalle no es el de la API, con un decimal')
    const atajos = await page.$eval('.cue-atajos', (p) => p.textContent)
    afirmar(atajos.includes(`El beat sale del BPM medido (${bpm1(uno.bpm)})`), `el editor no dice de qué BPM sale el beat: ${json(atajos)}`)
    const beat = 60 / uno.bpm          // el ±1 beat sale del BPM MEDIDO
    await page.focus('.cue-wave')
    for (let i = 0; i < 3; i++) await page.keyboard.press('ArrowRight')
    const cue = await teclaQueGuarda(page, 'c')
    igual([cue.status, cue.data.marca.tipo, cue.data.marca.num, cue.data.marca.inicio], [201, 'cue', 0, ms3(3 * beat)], 'C después de 3 beats: un hot cue 1 en 3 × 60/BPM')
    for (let i = 0; i < 2; i++) await page.keyboard.press('ArrowRight')
    const mem = await teclaQueGuarda(page, 'm')
    igual([mem.status, mem.data.marca.tipo, mem.data.marca.inicio], [201, 'memory', ms3(5 * beat)], 'M después de 5 beats')
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('i')
    for (let i = 0; i < 4; i++) await page.keyboard.press('ArrowRight')
    const loop = await teclaQueGuarda(page, 'o')
    igual([loop.status, loop.data.marca.tipo, loop.data.marca.inicio, loop.data.marca.fin], [201, 'loop', ms3(6 * beat), ms3(10 * beat)], 'I en el beat 6 y O en el 10')
    await page.keyboard.press('ArrowLeft')
    await page.keyboard.press('1')       // ir al hot cue 1: el cursor vuelve a su tiempo
    const reloj = await page.$eval('.cue-reloj', (e) => e.textContent)
    const s = cue.data.marca.inicio
    igual(reloj, `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, '0')}`, 'la tecla 1 no llevó el cursor al hot cue 1')

    const api_ = await marcasApi(ctx, uno.id)
    igual(api_.length, 3, 'la API no tiene las 3 marcas')
    await hasta(() => leerTabla(page), (v) => json(v) === json(esperadoTabla(api_)), 'la tabla no es la lista de la API')
    const estado = await estadoEditor(page)
    afirmar(/Guardado en la biblioteca/.test(estado) && /1 hot cue, 1 memory, 1 loop/.test(estado), `el indicador no dice que se guardó: ${json(estado)}`)
    const cuenta = await hasta(() => page.$eval('.rpaso.is-sel .rcues', (e) => e.textContent), (v) => v === '3 cues',
      'la columna «Cues» de la lista no cuenta las marcas del tema elegido')
    igual(cuenta, '3 cues', 'la columna «Cues» del tema elegido')

    await page.reload({ waitUntil: 'domcontentloaded' })
    await abrirEditor(page, ctx, 'Uno', { navegar: false })
    igual(await leerTabla(page), esperadoTabla(await marcasApi(ctx, uno.id)), 'después de recargar la tabla no es la lista de la API')
  }],

  ['cues: borrar pide confirmación y la marca sale de la base', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    await limpiarMarcas(ctx, uno.id)
    await apiPedir(ctx, rutaMarcas(uno.id), 'POST', { tipo: 'cue', inicio: 1.5 })
    const mem = (await apiPedir(ctx, rutaMarcas(uno.id), 'POST', { tipo: 'memory', inicio: 2.25, nombre: 'break' })).data.marca
    await abrirEditor(page, ctx)
    const pedidos = pedidosDeMarcas(page)
    await page.click(`.cue-tabla tr[data-id="${mem.id}"] .cue-borrar`)
    await page.waitForSelector(`.cue-tabla tr[data-id="${mem.id}"] .cue-si`, { timeout: ESPERA_MS })
    igual(pedidos, [], 'el primer clic en borrar ya borró (tenía que pedir confirmación)')
    const r = page.waitForResponse((x) => x.request().method() === 'DELETE', { timeout: ESPERA_MS })
    await page.click(`.cue-tabla tr[data-id="${mem.id}"] .cue-si`)
    igual((await r).status(), 200, 'estado del DELETE')
    const api_ = await marcasApi(ctx, uno.id)
    afirmar(!api_.some((m) => m.id === mem.id), 'la memory sigue en la API')
    await hasta(() => leerTabla(page), (v) => json(v) === json(esperadoTabla(api_)), 'la tabla no es la lista de la API después de borrar')
  }],

  ['cues: los atajos no se disparan escribiendo el nombre', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    await limpiarMarcas(ctx, uno.id)
    const cue = (await apiPedir(ctx, rutaMarcas(uno.id), 'POST', { tipo: 'cue', inicio: 1 })).data.marca
    await abrirEditor(page, ctx)
    const pedidos = pedidosDeMarcas(page)
    const campo = `.cue-tabla tr[data-id="${cue.id}"] .cue-in-n`
    await page.click(campo)
    await page.keyboard.type('c m i o 1 8')
    await page.keyboard.press('Space')
    await page.keyboard.press('ArrowLeft')
    // Un pedido posterior propio: si algún atajo hubiera disparado un POST, ya habría salido.
    await page.evaluate(() => fetch('/api/radio/biblioteca').then((r) => r.text()))
    igual(pedidos, [], 'escribir en el nombre disparó atajos que escribieron en la base')
    igual(await sonando(page), [], 'la barra espaciadora en el nombre arrancó el audio')
    igual(await page.$eval(campo, (i) => i.value), 'c m i o 1 8 ', 'las teclas no llegaron al campo')
    const r = await teclaQueGuarda(page, 'Enter', 'PATCH')
    igual([r.status, r.data.marca.nombre], [200, 'c m i o 1 8'], 'Enter no guardó el nombre (recortado)')
    igual((await marcasApi(ctx, uno.id)).map((m) => m.nombre), ['c m i o 1 8'], 'el nombre no quedó en la API')
  }],

  ['cues: mantener C apretada pone UN hot cue (la tecla repetida no siembra marcas)', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    await limpiarMarcas(ctx, uno.id)
    await abrirEditor(page, ctx)
    const pedidos = pedidosDeMarcas(page)
    await page.focus('.cue-wave')
    const r = page.waitForResponse((x) => x.request().method() === 'POST', { timeout: ESPERA_MS })
    // keyboard.down sobre una tecla ya apretada manda keydown con repeat=true, como el SO al
    // mantenerla.
    for (let i = 0; i < 5; i++) await page.keyboard.down('c')
    await page.keyboard.up('c')
    igual((await r).status(), 201, 'el primer keydown no creó el hot cue')
    await page.evaluate(() => fetch('/api/radio/biblioteca').then((x) => x.text()))
    igual(pedidos.length, 1, `mantener C apretada mandó ${pedidos.length} pedidos`)
    igual((await marcasApi(ctx, uno.id)).length, 1, 'la API tiene más de un hot cue')
  }],

  ['cues: Espacio activa el botón con foco (Cue aquí, Borrar de la confirmación), reproduce en la onda; Shift+C no pone un cue', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    await limpiarMarcas(ctx, uno.id)
    const mem = (await apiPedir(ctx, rutaMarcas(uno.id), 'POST', { tipo: 'memory', inicio: 2 })).data.marca
    await abrirEditor(page, ctx)
    const pedidos = pedidosDeMarcas(page)
    const sentinela = () => page.evaluate(() => fetch('/api/radio/biblioteca').then((x) => x.text()))
    // Shift+C: no es un atajo.
    await page.focus('.cue-wave')
    await page.keyboard.down('Shift')
    await page.keyboard.press('KeyC')
    await page.keyboard.up('Shift')
    await sentinela()
    igual(pedidos, [], 'Shift+C escribió en la base')
    // Espacio en «Cue aquí»: el botón se activa (un POST) y no arranca el audio.
    await page.focus('.cue-b-cue')
    let r = page.waitForResponse((x) => x.request().method() === 'POST', { timeout: ESPERA_MS })
    await page.keyboard.press('Space')
    igual((await r).status(), 201, 'Espacio en «Cue aquí» no puso el cue')
    igual(await sonando(page), [], 'Espacio en «Cue aquí» arrancó el audio')
    // Espacio en «Borrar» de la confirmación: borra, no reproduce.
    await page.click(`.cue-tabla tr[data-id="${mem.id}"] .cue-borrar`)
    await page.waitForSelector(`.cue-tabla tr[data-id="${mem.id}"] .cue-si`, { timeout: ESPERA_MS })
    await page.focus(`.cue-tabla tr[data-id="${mem.id}"] .cue-si`)
    r = page.waitForResponse((x) => x.request().method() === 'DELETE', { timeout: ESPERA_MS })
    await page.keyboard.press('Space')
    igual((await r).status(), 200, 'Espacio en «Borrar» no borró')
    igual(await sonando(page), [], 'Espacio en «Borrar» arrancó el audio')
    afirmar(!(await marcasApi(ctx, uno.id)).some((m) => m.id === mem.id), 'la memory sigue en la API')
    // En la onda, Espacio es reproducir.
    await page.focus('.cue-wave')
    await page.keyboard.press('Space')
    await hasta(() => sonando(page), (s) => json(s) === json([`/api/radio/audio/${uno.id}`]), 'Espacio en la onda no reprodujo')
    await page.keyboard.press('Space')
    await hasta(() => sonando(page), (s) => s.length === 0, 'Espacio otra vez en la onda no pausó')
  }],

  ['cues: un solo audio (el editor y la barra se ceden el lugar)', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    const lib = await api(ctx, '/api/biblioteca')
    const unoHome = lib.generos.flatMap((g) => g.tracks).find((t) => t.titulo === 'Uno')
    await abrirHome(page, ctx)
    await playEnHome(page, 'Uno')
    await abrirEditor(page, ctx, 'Uno', { navegar: false })
    const ver = async () => ({
      suenan: await sonando(page), barra: (await leerBarra(page))?.estado,
      editor: await page.$eval('.cue-play', (b) => b.textContent),
    })
    await page.focus('.cue-wave')
    await page.keyboard.press('Space')
    await hasta(ver, (v) => json(v.suenan) === json([`/api/radio/audio/${uno.id}`]) && v.barra === 'paused' && /Pausar/.test(v.editor),
      'Espacio en el editor: tendría que sonar SOLO el editor y la barra quedar en pausa')
    await page.click('.deck-play')
    await hasta(ver, (v) => json(v.suenan) === json([`/api/audio/${unoHome.id}`]) && v.barra === 'playing' && /Reproducir/.test(v.editor),
      'play en la barra: tendría que sonar SOLO la barra y el editor volver a «Reproducir»')
    await page.focus('.cue-wave')
    await page.keyboard.press('Space')
    await hasta(ver, (v) => json(v.suenan) === json([`/api/radio/audio/${uno.id}`]) && v.barra === 'paused',
      'Espacio otra vez: la barra no cedió')
    // Elegir otro track de la lista corta el audio del editor (f50: ya no hay «Volver al set»).
    await elegirFila(page, 2)
    await hasta(() => sonando(page), (s) => s.length === 0, 'al elegir otro track el editor siguió sonando')
    // Y una fila de la radio que arranca pausa al editor (un solo audio también entre los dos
    // audios de la pantalla).
    await hasta(() => estadoEditor(page), (v) => /guardadas en la biblioteca/.test(v), 'el editor no cargó el paso 2')
    await page.focus('.cue-wave')
    await page.keyboard.press('Space')
    const dosId = await page.$eval('.rpaso[data-n="2"]', (r) => r.querySelector('.rpaso-titulo').textContent)
    const dos = (await api(ctx, '/api/radio/biblioteca')).tracks.find((t) => t.titulo === dosId)
    await hasta(() => sonando(page), (s) => json(s) === json([`/api/radio/audio/${dos.id}`]), 'Espacio en la onda del paso 2 no lo hizo sonar')
    await page.click('.rpaso[data-n="1"] .rplay')
    await hasta(async () => ({ suenan: await sonando(page), editor: await page.$eval('.cue-play', (b) => b.textContent) }),
      (v) => json(v.suenan) === json([`/api/radio/audio/${uno.id}`]) && /Reproducir/.test(v.editor),
      'play en una fila con el editor sonando: tendría que sonar SOLO la fila y el editor volver a «Reproducir»')
  }],

  ['cues: «Guardado» recién con la respuesta; si falla, aviso con «Reintentar»', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    await limpiarMarcas(ctx, uno.id)
    let soltar = null
    let veces = 0
    await interceptar(page, (req) => {
      if (req.method() !== 'POST' || !/\/marcas$/.test(new URL(req.url()).pathname)) return false
      veces += 1
      if (veces === 1) {
        // El primero se retiene y después se contesta 503 (la base ocupada, simulada).
        return new Promise((res) => { soltar = () => { req.respond({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'la base está ocupada (simulado)' }) }); res(true) } })
      }
      return false
    })
    await abrirEditor(page, ctx)
    await page.focus('.cue-wave')
    await page.keyboard.press('c')
    await hasta(() => soltar, (s) => !!s, 'el POST nunca salió')
    const mientras = await estadoEditor(page)
    afirmar(/Guardando/.test(mientras) && !/Guardado/.test(mientras), `con el pedido en vuelo el indicador tiene que decir «Guardando…»: ${json(mientras)}`)
    soltar()
    const falla = await hasta(() => page.evaluate(() => document.querySelector('.cue-falla')?.textContent ?? null), (v) => !!v, 'un 503 no mostró el aviso de error')
    afirmar(/No se guardó/.test(falla) && /ocupada \(simulado\)/.test(falla), `el aviso no dice qué pasó: ${json(falla)}`)
    afirmar(!/Guardado/.test(await estadoEditor(page)), 'con el error, el indicador igual dice «Guardado»')
    igual(await leerTabla(page), [], 'la tabla muestra una marca que no se guardó')
    igual(await marcasApi(ctx, uno.id), [], 'la API tiene una marca que tendría que haber fallado')
    const r = page.waitForResponse((x) => x.request().method() === 'POST', { timeout: ESPERA_MS })
    await page.click('.cue-reintentar')
    igual((await r).status(), 201, 'el reintento no guardó')
    const api_ = await marcasApi(ctx, uno.id)
    await hasta(() => leerTabla(page), (v) => json(v) === json(esperadoTabla(api_)) && v.length === 1, 'después del reintento la tabla no es la lista de la API')
    await hasta(() => estadoEditor(page), (v) => /Guardado en la biblioteca/.test(v), 'después del reintento el indicador no dice «Guardado»')
    igual(await page.$('.cue-falla'), null, 'el aviso de error quedó después de reintentar bien')
  }],

  ['cues: clic en la onda va a ese punto y arrastrar una marca la mueve al tiempo bajo el mouse', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    await limpiarMarcas(ctx, uno.id)
    const cue = (await apiPedir(ctx, rutaMarcas(uno.id), 'POST', { tipo: 'cue', inicio: 30 })).data.marca
    await abrirEditor(page, ctx)
    // f50: el editor está en el detalle, que tiene su propio scroll: la onda se trae a la vista.
    await page.$eval('.cue-wave', (e) => e.scrollIntoView({ block: 'center' }))
    const caja = await page.$eval('.cue-wave', (e) => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height } })
    // Clic al 25 % del ancho: el cursor va a 0.25 × la duración de la API.
    await page.mouse.click(caja.x + caja.w * 0.25, caja.y + caja.h / 2)
    const t = 0.25 * uno.dur
    igual(await page.$eval('.cue-reloj', (e) => e.textContent), `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, '0')}`, 'el clic en la onda no llevó el cursor a ese punto')
    // Arrastrar la banderita del hot cue 1 hasta el 75 %.
    const flag = await page.$eval(`.cue-mk .cue-flag`, (e) => { const r = e.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 } })
    const destino = caja.x + caja.w * 0.75
    const r = page.waitForResponse((x) => x.request().method() === 'PATCH', { timeout: ESPERA_MS })
    await page.mouse.move(flag.x, flag.y)
    await page.mouse.down()
    await page.mouse.move(flag.x + 40, flag.y, { steps: 4 })
    await page.mouse.move(destino, flag.y, { steps: 6 })
    await page.mouse.up()
    const res = await r
    const esperado = ms3(((destino - caja.x) / caja.w) * uno.dur)
    const movida = (await res.json()).marca
    igual([res.status(), movida.id], [200, cue.id], 'el arrastre no mandó un PATCH de esa marca')
    afirmar(Math.abs(movida.inicio - esperado) <= 0.001, `la marca quedó en ${movida.inicio} y el mouse soltó en ${esperado}`)
    igual((await marcasApi(ctx, uno.id)).map((m) => m.inicio), [movida.inicio], 'la API no tiene la marca movida')
    await hasta(() => leerTabla(page), (v) => v.length === 1 && v[0].tiempos[0] === tiempoTabla(movida.inicio), 'la tabla no muestra el tiempo nuevo')
  }],

  ['cues: 400 px sin desborde, con la lista del set arriba del detalle y marcas en los bordes', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    await limpiarMarcas(ctx, uno.id)
    await apiPedir(ctx, rutaMarcas(uno.id), 'POST', { tipo: 'cue', inicio: 0, nombre: 'Un nombre largo para ver que no empuja la tabla' })
    await apiPedir(ctx, rutaMarcas(uno.id), 'POST', { tipo: 'loop', inicio: uno.dur - 2, fin: uno.dur })
    await apiPedir(ctx, rutaMarcas(uno.id), 'POST', { tipo: 'cue', inicio: uno.dur - 0.001, num: 7 })
    await page.setViewport({ width: 400, height: 860 })
    await hasta(() => page.evaluate(() => document.documentElement.clientWidth), (w) => w <= 400, 'el viewport no pasó a 400 px')
    await abrirEditor(page, ctx)
    const d = await desbordeDe(page)
    afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `editor a 400 px: hay contenido fuera del ancho: ${json(d)}`)
    const geo = await page.evaluate(() => {
      const l = document.querySelector('.rpanel-set').getBoundingClientRect()
      const m = document.querySelector('.rdet').getBoundingClientRect()
      return { listaAbajo: l.bottom, detalleArriba: m.top }
    })
    afirmar(geo.listaAbajo <= geo.detalleArriba, `a 400 px el detalle (con el editor) tiene que ir debajo de la lista del set: ${json(geo)}`)
    // `desbordeDe` mira el viewport: un campo que se sale de SU celda y pisa la de al lado
    // queda adentro de la pantalla y no lo ve. Cada campo y botón tiene que entrar en su celda.
    const pisados = await page.$$eval('.cue-tabla td', (tds) => tds.flatMap((td) => {
      const c = td.getBoundingClientRect()
      return [...td.querySelectorAll('input,button')].filter((e) => {
        const r = e.getBoundingClientRect()
        return r.left < c.left - 1 || r.right > c.right + 1
      }).map((e) => `${e.className} (${Math.round(e.getBoundingClientRect().width)} px en ${Math.round(c.width)})`)
    }))
    igual(pisados, [], 'a 400 px hay campos de la tabla que se salen de su celda')
  }],
]

/* ---------- Radio DJ en maestro/detalle y volumen (f50) ----------
   Lo esperado sale de la API (/api/radio/set, marcas, conteo, onda) o de la definición que
   pidió el dueño (volumen 45 % por defecto, de a 5 %), escrita acá y no importada del front. */

// m:ss.d (el mismo formato del reloj del editor; contrato escrito acá).
const mmssd = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, '0')}`
// La columna «Cues»: 0 → «sin cues», 1 → «1 cue», n → «n cues» (pedido del dueño).
const cuesTexto = (n) => (n === 0 ? 'sin cues' : `${n} cue${n === 1 ? '' : 's'}`)

const geoMD = (page) => page.evaluate(() => {
  const l = document.querySelector('.rpanel-set').getBoundingClientRect()
  const d = document.querySelector('.rdet').getBoundingClientRect()
  return { lista: { izq: l.left, der: l.right, arriba: l.top, abajo: l.bottom }, det: { izq: d.left, der: d.right, arriba: d.top, abajo: d.bottom } }
})

// Lo que muestra el control de volumen del editor y el de la barra, y a cuánto suena cada audio.
const leerVolumenes = (page) => page.evaluate(() => {
  const r = document.querySelector('.cue-vol .vol-range')
  const barra = document.querySelector('.deck-range-vol')
  const audioBarra = [...window.__medios].find((m) => /\/api\/audio\//.test(m.currentSrc || m.src))
  const audioEditor = document.querySelector('.cue-ed audio')
  return {
    editor: r ? { valor: r.value, role: r.getAttribute('role'), label: r.getAttribute('aria-label'), now: r.getAttribute('aria-valuenow'), texto: r.getAttribute('aria-valuetext'), min: r.getAttribute('aria-valuemin'), max: r.getAttribute('aria-valuemax') } : null,
    barra: barra ? barra.value : null,
    audioEditor: audioEditor ? { volumen: Math.round(audioEditor.volume * 100) / 100, mudo: audioEditor.muted } : null,
    audioBarra: audioBarra ? Math.round(audioBarra.volume * 100) / 100 : null,
    guardado: localStorage.getItem('musiflix.volumen'),
  }
})

const CASOS_F50 = [

  ['radio (f50): maestro y detalle lado a lado a 1440 y 1280; el porqué es el del motor y no hay «% de match»', async (page, ctx) => {
    const { lib } = await semillaUno(ctx)
    await abrirRadio(page, ctx)
    await elegirSemilla(page, 'Uno')
    const set = await armarSet(page)
    for (const ancho of [1440, 1280]) {
      await page.setViewport({ width: ancho, height: 900 })
      await hasta(() => page.evaluate(() => document.documentElement.clientWidth), (w) => w <= ancho, `el viewport no pasó a ${ancho} px`)
      const g = await geoMD(page)
      afirmar(g.lista.der <= g.det.izq && g.det.arriba < g.lista.abajo, `a ${ancho} px la lista y el detalle tienen que ir lado a lado: ${json(g)}`)
      const d = await desbordeDe(page)
      afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `a ${ancho} px hay scroll horizontal o contenido cortado: ${json(d)}`)
    }
    // A la izquierda, al pie del rail, el estado de la biblioteca con el dato real.
    igual(await page.$eval('.pl-rail-lib .pl-rail-lib-n', (e) => e.textContent), `${lib.total} tracks analizados`, 'el estado de la biblioteca en el rail')
    // La tabla: roles, una fila por paso y tantas celdas como columnas.
    const tabla = await page.evaluate(() => {
      const t = document.querySelector('.rtabla')
      const filas = [...t.querySelectorAll('[role=row]')]
      return { role: t.getAttribute('role'), filas: filas.length, cols: filas[0].querySelectorAll('[role=columnheader]').length, celdas: filas.slice(1).map((f) => f.querySelectorAll(':scope > [role=cell]').length) }
    })
    igual(tabla, { role: 'table', filas: set.pasos.length + 1, cols: tabla.cols, celdas: set.pasos.map(() => tabla.cols) }, 'la tabla del set (roles y celdas por columna)')
    // El porqué del detalle es el del motor para cada paso, y no hay puntajes inventados.
    for (const p of set.pasos) {
      await elegirFila(page, p.n)
      const d = await page.evaluate(() => window.__detalle())
      igual(d.motivo, p.motivo, `el porqué del detalle del paso ${p.n}`)
      const resto = d.why.replace(p.motivo, '')
      afirmar(!/%/.test(resto), `el bloque del porqué del paso ${p.n} muestra un porcentaje que no es el del motor: ${json(resto)}`)
    }
    const texto = await page.$eval('.radiodj', (e) => e.textContent)
    afirmar(!/match/i.test(texto) && !/g[eé]nero coincide/i.test(texto) && !/\+\d+\s*(pts|puntos)?\s*$/m.test(texto),
      'la pantalla muestra un «match», un «género coincide» o un puntaje inventado')
    // Teclado: una sola parada de Tab en la lista; ↑ ↓ Inicio Fin cambian de fila con el foco.
    await elegirFila(page, set.pasos[0].n)
    await page.focus(`.rpaso-sel[data-n="${set.pasos[0].n}"]`)
    const ver = () => page.evaluate(() => ({
      foco: document.activeElement?.dataset?.n ?? null,
      sel: document.querySelector('.rpaso.is-sel')?.dataset.n ?? null,
      actual: [...document.querySelectorAll('.rpaso-sel[aria-current=true]')].map((b) => b.dataset.n),
      tab0: [...document.querySelectorAll('.rpaso-sel')].filter((b) => b.tabIndex === 0).map((b) => b.dataset.n),
      titulo: document.querySelector('#r-det-h')?.textContent ?? null,
    }))
    const ns = set.pasos.map((p) => String(p.n))
    const esperar = async (n, que) => hasta(ver, (v) => v.foco === n && v.sel === n && json(v.actual) === json([n]) && json(v.tab0) === json([n]), que)
    await page.keyboard.press('ArrowDown')
    const v1 = await esperar(ns[1], 'Flecha abajo no pasó a la segunda fila')
    igual(v1.titulo, set.pasos[1].track.titulo, 'el detalle después de Flecha abajo')
    await page.keyboard.press('End')
    await esperar(ns[ns.length - 1], 'Fin no fue a la última fila')
    await page.keyboard.press('ArrowDown')
    await esperar(ns[ns.length - 1], 'Flecha abajo en la última fila no se tiene que mover')
    await page.keyboard.press('Home')
    await esperar(ns[0], 'Inicio no volvió a la primera fila')
    await page.keyboard.press('ArrowUp')
    await esperar(ns[0], 'Flecha arriba en la primera fila no se tiene que mover')
  }],

  ['radio (f50): pestañas con flechas; «Onda avanzada» deshabilitada no se activa; «Información» = la API', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    await abrirEditor(page, ctx)
    const tabs = () => page.evaluate(() => ({
      lista: document.querySelector('[role=tablist]')?.getAttribute('aria-label') ?? null,
      tabs: [...document.querySelectorAll('[role=tablist] [role=tab]')].map((t) => ({
        id: t.id, texto: t.textContent, sel: t.getAttribute('aria-selected'), dis: t.getAttribute('aria-disabled'),
        controla: t.getAttribute('aria-controls'), tab: t.tabIndex,
      })),
      foco: document.activeElement?.id ?? null,
      paneles: [...document.querySelectorAll('.rdet [role=tabpanel]')].map((p) => ({ id: p.id, por: p.getAttribute('aria-labelledby'), visible: window.__visible(p) })),
    }))
    const t0 = await tabs()
    igual(t0.tabs.map((t) => [t.id, t.sel, t.dis, t.controla, t.tab]), [
      ['r-tab-cues', 'true', null, 'r-panel-cues', 0], ['r-tab-info', 'false', null, 'r-panel-info', -1], ['r-tab-onda', 'false', 'true', null, -1],
    ], 'las pestañas al abrir: «Cues y loops» elegida, una sola parada de Tab, la tercera deshabilitada')
    afirmar(/próximamente/.test(t0.tabs[2].texto), `la deshabilitada no dice «próximamente»: ${json(t0.tabs[2].texto)}`)
    igual(t0.paneles, [{ id: 'r-panel-cues', por: 'r-tab-cues', visible: true }, { id: 'r-panel-info', por: 'r-tab-info', visible: false }], 'los paneles al abrir')
    const elegida = (t) => t.tabs.find((x) => x.sel === 'true')?.id
    await page.focus('#r-tab-cues')
    await page.keyboard.press('ArrowRight')
    let t = await hasta(tabs, (v) => elegida(v) === 'r-tab-info' && v.foco === 'r-tab-info', 'Flecha derecha no pasó a «Información»')
    igual(t.paneles.map((p) => p.visible), [false, true], 'con «Información» elegida se ve su panel y no el del editor')
    await page.keyboard.press('ArrowRight')
    await hasta(tabs, (v) => elegida(v) === 'r-tab-cues' && v.foco === 'r-tab-cues', 'Flecha derecha desde «Información» tiene que saltear la deshabilitada y volver a la primera')
    await page.keyboard.press('ArrowLeft')
    await hasta(tabs, (v) => elegida(v) === 'r-tab-info' && v.foco === 'r-tab-info', 'Flecha izquierda desde la primera no dio la vuelta a «Información»')
    await page.keyboard.press('Home')
    await hasta(tabs, (v) => elegida(v) === 'r-tab-cues', 'Inicio no fue a la primera')
    await page.keyboard.press('End')
    await hasta(tabs, (v) => elegida(v) === 'r-tab-info', 'Fin no fue a la última habilitada')
    // La deshabilitada no se activa con el mouse.
    await page.click('#r-tab-onda')
    t = await tabs()
    igual([elegida(t), t.paneles.map((p) => p.visible)], ['r-tab-info', [false, true]], 'un clic en «Onda avanzada» cambió de pestaña')
    // «Información»: lo que la API de marcas dice del archivo y la duración del audio de /onda.
    const x = (await apiPedir(ctx, rutaMarcas(uno.id))).data.track
    const onda = (await apiPedir(ctx, `/api/radio/tracks/${uno.id}/onda`)).data
    const info = await hasta(() => page.evaluate(() => {
      const dl = document.querySelector('#r-panel-info .rinfo-dl')
      if (!dl) return null
      const o = {}
      const dts = [...dl.querySelectorAll('dt')]
      dts.forEach((dt) => { o[dt.textContent] = dt.nextElementSibling?.textContent ?? null })
      return o
    }), (v) => v && !/leyendo/.test(v['Duración del audio'] || 'leyendo'), '«Información» no terminó de mostrar los datos')
    igual({ archivo: info.Archivo, formato: info.Formato, bpm: info['BPM medido'], acuerdo: info['Acuerdo de la key'], licencia: info.Licencia, origen: info.Origen, dur: info['Duración (biblioteca)'], audio: info['Duración del audio'] },
      { archivo: x.ruta, formato: x.formato.toUpperCase(), bpm: bpm1(x.bpm), acuerdo: `${x.key_acuerdo} tramos`, licencia: x.licencia, origen: x.origen, dur: mmssd(x.dur), audio: mmssd(onda.duracion_audio) },
      '«Información» no dice lo que tiene la API')
    afirmar(x.ruta && x.ruta.endsWith('uno.wav') && x.licencia && x.origen, `la API no trae ruta, licencia u origen: el caso no probaría nada (${json(x)})`)
  }],

  ['radio (f50): la columna «Cues» = las marcas de cada track en la base, con UN pedido de conteos por set', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    const set = await api(ctx, `/api/radio/set?track=${encodeURIComponent(uno.id)}`)
    afirmar(set.pasos.length >= 3, 'el set de juguete tiene menos de 3 pasos')
    for (const p of set.pasos) await limpiarMarcas(ctx, p.track.id)
    const segundo = set.pasos[1].track
    await apiPedir(ctx, rutaMarcas(uno.id), 'POST', { tipo: 'cue', inicio: 1 })
    await apiPedir(ctx, rutaMarcas(uno.id), 'POST', { tipo: 'loop', inicio: 4, fin: 8 })
    await apiPedir(ctx, rutaMarcas(segundo.id), 'POST', { tipo: 'memory', inicio: 2 })
    const conteos = []
    page.on('request', (q) => { if (new URL(q.url()).pathname === '/api/radio/marcas/conteo') conteos.push(q.url()) })
    await abrirRadio(page, ctx)
    await elegirSemilla(page, 'Uno')
    await armarSet(page)
    const esperado = []
    for (const p of set.pasos) esperado.push(cuesTexto((await marcasApi(ctx, p.track.id)).length))
    afirmar(esperado.includes('sin cues') && esperado.includes('1 cue') && esperado.includes('2 cues'), `la base no tiene los tres casos: ${json(esperado)}`)
    const leer = () => page.$$eval('.rpaso .c-cues .rcues', (es) => es.map((e) => e.textContent))
    await hasta(leer, (v) => json(v) === json(esperado), 'la columna «Cues» no dice cuántas marcas tiene cada track en la base')
    // «sin cues» en gris: el color del token neutral-400 (no el del texto).
    const colores = await page.evaluate(() => {
      const probe = document.createElement('span')
      probe.style.color = 'var(--color-neutral-400)'
      document.body.appendChild(probe)
      const gris = getComputedStyle(probe).color
      probe.remove()
      return [...document.querySelectorAll('.rpaso .rcues')].map((e) => ({ texto: e.textContent, gris: getComputedStyle(e).color === gris }))
    })
    igual(colores.map((c) => c.gris), esperado.map((e) => e === 'sin cues'), '«sin cues» en gris y los conteos no')
    await page.evaluate(() => fetch('/api/radio/biblioteca').then((r) => r.text()))
    igual(conteos.length, 1, `un set pidió ${conteos.length} veces /api/radio/marcas/conteo`)
    const ids = new URL(conteos[0]).searchParams.get('ids').split(',')
    igual(ids, set.pasos.map((p) => p.track.id), 'el pedido de conteos no lleva los ids del set, en orden')
    // Poner un cue en el editor actualiza la columna sin volver a pedir los conteos.
    await hasta(() => estadoEditor(page), (v) => /guardadas en la biblioteca/.test(v), 'el editor no cargó «Uno»')
    await page.focus('.cue-wave')
    await teclaQueGuarda(page, 'c')
    const n = (await marcasApi(ctx, uno.id)).length
    await hasta(() => page.$eval('.rpaso[data-n="1"] .rcues', (e) => e.textContent), (v) => v === cuesTexto(n), 'la columna no se actualizó después de poner un cue')
    await page.evaluate(() => fetch('/api/radio/biblioteca').then((r) => r.text()))
    igual(conteos.length, 1, 'poner un cue volvió a pedir todos los conteos')
    for (const p of set.pasos) await limpiarMarcas(ctx, p.track.id)
  }],

  ['volumen (f50): 45 % por defecto, mueve el <audio> del editor y el de la barra (un solo volumen) y se acuerda al recargar', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    await limpiarMarcas(ctx, uno.id)
    const marca = (await apiPedir(ctx, rutaMarcas(uno.id), 'POST', { tipo: 'cue', inicio: 1 })).data.marca
    await abrirHome(page, ctx)
    // El perfil de Chrome es el mismo para toda la corrida: se arranca sin nada guardado.
    await page.evaluate(() => localStorage.removeItem('musiflix.volumen'))
    await page.reload({ waitUntil: 'domcontentloaded' })
    await page.waitForSelector('.lib-card', { timeout: ESPERA_MS })
    try {
      await playEnHome(page, 'Uno')
      await abrirEditor(page, ctx, 'Uno', { navegar: false })
      let v = await hasta(() => leerVolumenes(page), (x) => x.audioEditor && x.audioBarra !== null, 'no están los dos audios')
      igual(v, {
        editor: { valor: '45', role: 'slider', label: 'Volumen', now: '45', texto: '45 %', min: '0', max: '100' },
        barra: '45', audioEditor: { volumen: 0.45, mudo: false }, audioBarra: 0.45, guardado: null,
      }, 'sin nada guardado el volumen tiene que ser 45 % en el editor, en la barra y en los dos audios')
      // ↑ con el foco en la onda: +5 % cada vez, en todos lados, y se guarda.
      await page.focus('.cue-wave')
      await page.keyboard.press('ArrowUp')
      await page.keyboard.press('ArrowUp')
      v = await hasta(() => leerVolumenes(page), (x) => x.editor.valor === '55', 'dos ↑ en la onda no subieron el volumen a 55 %')
      igual([v.editor.now, v.editor.texto, v.barra, v.audioEditor.volumen, v.audioBarra, v.guardado], ['55', '55 %', '55', 0.55, 0.55, '0.55'],
        'el volumen después de dos ↑: el mismo en el editor, la barra, los dos audios y lo guardado')
      // Con un modificador o escribiendo, ↑ ↓ no son del volumen.
      await page.keyboard.down('Shift')
      await page.keyboard.press('ArrowUp')
      await page.keyboard.up('Shift')
      await page.click(`.cue-tabla tr[data-id="${marca.id}"] .cue-in-n`)
      // Solo ↑ (un ↑ y un ↓ se anularían y el caso no vería nada).
      await page.keyboard.press('ArrowUp')
      await page.keyboard.press('ArrowUp')
      await page.evaluate(() => fetch('/api/radio/biblioteca').then((r) => r.text()))
      igual((await leerVolumenes(page)).editor.valor, '55', 'Shift+↑ o ↑ ↓ escribiendo el nombre cambiaron el volumen')
      await page.focus('.cue-wave')
      await page.keyboard.press('ArrowDown')
      await hasta(() => leerVolumenes(page), (x) => x.editor.valor === '50' && x.audioEditor.volumen === 0.5 && x.audioBarra === 0.5, '↓ en la onda no bajó a 50 %')
      // Silencio: el botón del editor silencia los dos (el de la barra dice lo mismo).
      await page.click('.cue-vol .vol-mute')
      v = await hasta(() => leerVolumenes(page), (x) => x.audioEditor.mudo, 'Silenciar no silenció el audio del editor')
      igual([v.editor.valor, v.editor.texto, v.barra, v.audioBarra], ['0', 'Silenciado', '0', 0], 'silenciado: los dos controles en 0 y la barra muda')
      igual(await page.$eval('.deck .deck-vol button', (b) => b.getAttribute('aria-pressed')), 'true', 'la barra no se enteró del silencio')
      await page.click('.cue-vol .vol-mute')
      await hasta(() => leerVolumenes(page), (x) => !x.audioEditor.mudo && x.editor.valor === '50' && x.audioBarra === 0.5, 'Activar sonido no volvió a 50 %')
      // Recargar: se acuerda.
      await page.reload({ waitUntil: 'domcontentloaded' })
      await abrirEditor(page, ctx, 'Uno', { navegar: false })
      v = await hasta(() => leerVolumenes(page), (x) => x.audioEditor, 'después de recargar no está el editor')
      igual([v.editor.valor, v.audioEditor.volumen, v.guardado], ['50', 0.5, '0.5'], 'después de recargar el volumen no es el que quedó')
    } finally {
      await page.evaluate(() => localStorage.removeItem('musiflix.volumen')).catch(() => {})
      await limpiarMarcas(ctx, uno.id)
    }
  }],

  ['radio (f50): las minionditas se piden solo para las filas a la vista, de a dos, y el mismo tema una vez', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    const set = await api(ctx, `/api/radio/set?track=${encodeURIComponent(uno.id)}`)
    const retenidos = []     // pedidos a /onda que todavía no se contestaron
    const pedidos = []       // ids pedidos, en orden
    let maxEnVuelo = 0
    await interceptar(page, (req) => {
      const m = /^\/api\/radio\/tracks\/([0-9a-f]{16})\/onda$/.exec(new URL(req.url()).pathname)
      if (!m) return false
      pedidos.push(m[1])
      return new Promise((res) => {
        retenidos.push({ id: m[1], soltar: () => { req.continue().catch(() => {}); res(true) } })
        maxEnVuelo = Math.max(maxEnVuelo, retenidos.length)
      })
    })
    await page.setViewport({ width: 1440, height: 640 })
    await abrirRadio(page, ctx)
    await elegirSemilla(page, 'Uno')
    await armarSet(page)
    await page.evaluate(() => window.scrollTo(0, 0))
    const filas = () => page.evaluate(() => [...document.querySelectorAll('.rpaso')].map((r) => {
      const m = r.querySelector('.rmini')
      const b = m.getBoundingClientRect()
      return { id: m.dataset.mini, visible: b.bottom > 0 && b.top < innerHeight, fase: (m.className.match(/is-(\w+)/) || [])[1] }
    }))
    // Una vuelta de red para que lo que se fuera a pedir ya haya salido.
    await hasta(async () => retenidos.length, (n) => n >= 1, 'no se pidió ninguna onda (ni la del editor)')
    await page.evaluate(() => fetch('/api/radio/biblioteca').then((r) => r.text()))
    const f0 = await filas()
    afirmar(f0.some((f) => !f.visible), `todas las filas están a la vista: el caso no probaría la carga perezosa (${json(f0)})`)
    const elegido = set.pasos[0].track.id    // la fila elegida: su onda la pide el editor
    const fuera = f0.filter((f) => !f.visible && f.id !== elegido).map((f) => f.id)
    afirmar(fuera.length > 0, 'no hay filas fuera de pantalla además de la elegida')
    igual(pedidos.filter((id) => fuera.includes(id)), [], 'se pidió la onda de filas que no están a la vista')
    igual(f0.filter((f) => f.id !== elegido).map((f) => f.fase), f0.filter((f) => f.id !== elegido).map(() => 'cargando'), 'mientras no llega, cada fila tiene su casillero gris')
    // A la vista: se piden, pero nunca más de dos a la vez.
    await page.evaluate(() => document.querySelector('.rtabla').scrollIntoView({ block: 'center' }))
    const todas = set.pasos.map((p) => p.track.id)
    for (let vuelta = 0; vuelta < 20 && retenidos.length + pedidos.length > 0; vuelta++) {
      await page.evaluate(() => fetch('/api/radio/biblioteca').then((r) => r.text()))
      afirmar(retenidos.length <= 2, `hay ${retenidos.length} ondas pedidas a la vez (el tope es 2)`)
      if (todas.every((id) => pedidos.includes(id)) && retenidos.length === 0) break
      const r = retenidos.shift()
      if (r) r.soltar()
    }
    igual([...pedidos].sort(), [...todas].sort(), 'cada tema del set se pidió exactamente una vez (la fila y el editor comparten el pedido)')
    afirmar(maxEnVuelo === 2, `el pico de pedidos a la vez fue ${maxEnVuelo}: con el editor y las filas tendría que llegar a 2, no más`)
    const fin = await hasta(filas, (v) => v.every((f) => f.fase === 'ok'), 'las minionditas no se dibujaron al llegar la onda')
    const barras = await page.$$eval('.rpaso .rmini svg', (ss) => ss.map((s) => s.querySelectorAll('rect').length))
    afirmar(barras.length === fin.length && barras.every((n) => n > 0), `alguna miniondita quedó sin barras: ${json(barras)}`)
  }],

  ['radio (f50): elegir una fila con el mouse o Enter lleva el foco a la onda y C pone un cue; con ↑ ↓ el foco se queda en la lista', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    const set = await api(ctx, `/api/radio/set?track=${encodeURIComponent(uno.id)}`)
    afirmar(set.pasos.length >= 3, 'el set de juguete tiene menos de 3 pasos')
    const [, p2, p3] = set.pasos
    await limpiarMarcas(ctx, p2.track.id)
    try {
      await abrirRadio(page, ctx)
      await elegirSemilla(page, 'Uno')
      await armarSet(page)
      const ayuda = await page.$eval('.cue-atajos', (p) => p.textContent)
      afirmar(/atajos actúan con el foco en el editor/.test(ayuda), `la ayuda no dice dónde actúan los atajos: ${json(ayuda)}`)
      const foco = () => page.evaluate(() => ({
        onda: document.activeElement?.classList.contains('cue-wave') ?? false,
        fila: document.activeElement?.classList.contains('rpaso-sel') ? document.activeElement.dataset.n : null,
        titulo: document.querySelector('#r-det-h')?.textContent ?? null,
        estado: document.querySelector('#r-panel-cues .cue-estado')?.textContent ?? '',
      }))
      // Con el mouse: el foco termina en la onda del editor del track elegido.
      await page.click(`.rpaso-sel[data-n="${p2.n}"]`)
      await hasta(foco, (v) => v.onda && v.titulo === p2.track.titulo && /guardadas en la biblioteca/.test(v.estado),
        'elegí una fila con el mouse y el foco no pasó a la onda de su editor')
      // …y C pone el cue en ESE track (sin tocar nada más), y Espacio reproduce (no re-elige).
      const cue = await teclaQueGuarda(page, 'c')
      igual([cue.status, cue.data.marca.tipo], [201, 'cue'], 'C después de elegir con el mouse')
      igual((await marcasApi(ctx, p2.track.id)).length, 1, 'el cue no quedó en el track elegido')
      await page.keyboard.press('Space')
      await hasta(() => sonando(page), (s) => json(s) === json([`/api/radio/audio/${p2.track.id}`]), 'Espacio después de elegir con el mouse no reprodujo el track')
      await page.keyboard.press('Space')
      // Con Enter sobre una fila: lo mismo.
      await page.focus(`.rpaso-sel[data-n="${p3.n}"]`)
      await page.keyboard.press('Enter')
      await hasta(foco, (v) => v.onda && v.titulo === p3.track.titulo, 'Enter sobre una fila no llevó el foco a la onda de su editor')
      // Con ↑ ↓ el foco se queda en la lista (para seguir recorriéndola).
      await page.focus(`.rpaso-sel[data-n="${p3.n}"]`)
      await page.keyboard.press('ArrowUp')
      let v = await hasta(foco, (x) => x.fila === String(p2.n) && x.titulo === p2.track.titulo, 'Flecha arriba no pasó a la fila anterior')
      afirmar(!v.onda, 'con Flecha arriba el foco se fue al editor')
      await page.keyboard.press('ArrowDown')
      v = await hasta(foco, (x) => x.fila === String(p3.n), 'Flecha abajo no volvió a la fila siguiente')
      afirmar(!v.onda, 'con Flecha abajo el foco se fue al editor')
    } finally {
      await limpiarMarcas(ctx, p2.track.id)
    }
  }],

  ['radio (f50): a 1440×900 la lista del set se ve entera sin scroll; el armado se pliega solo y se despliega con teclado', async (page, ctx) => {
    await page.setViewport({ width: 1440, height: 900 })
    await abrirRadio(page, ctx)
    igual(await page.$eval('.rarma-toggle', (b) => b.getAttribute('aria-expanded')), 'true', 'sin set, el panel de armado tiene que estar abierto')
    await elegirSemilla(page, 'Uno')
    const set = await armarSet(page)
    await page.evaluate(() => window.scrollTo(0, 0))
    const medir = () => page.evaluate(() => {
      const t = document.querySelector('.rtabla').getBoundingClientRect()
      return {
        expandido: document.querySelector('.rarma-toggle').getAttribute('aria-expanded'),
        cuerpo: window.__visible(document.getElementById('r-arma-cuerpo')),
        armar: window.__visible(document.querySelector('.rarmar')),
        scrollY: window.scrollY, alto: innerHeight, tablaArriba: Math.round(t.top), tablaAbajo: Math.round(t.bottom),
        filas: document.querySelectorAll('.rpaso').length,
      }
    })
    const m = await hasta(medir, (x) => x.expandido === 'false', 'con un set armado el panel de armado no se plegó')
    igual([m.cuerpo, m.armar, m.filas], [false, true, set.pasos.length], 'plegado: sin semilla/controles a la vista, con «Armar el set» y todas las filas')
    afirmar(m.scrollY === 0 && m.tablaAbajo <= m.alto, `a 1440×900 la lista del set no entra sin scroll: ${json(m)}`)
    // Se despliega y se pliega con el teclado.
    await page.focus('.rarma-toggle')
    await page.keyboard.press('Enter')
    await hasta(medir, (x) => x.expandido === 'true' && x.cuerpo, 'Enter en «Armar el set» no desplegó el panel')
    await page.keyboard.press('Space')
    await hasta(medir, (x) => x.expandido === 'false' && !x.cuerpo, 'Espacio no volvió a plegar el panel')
  }],

  ['volumen (f50): el preview del mouse sigue el volumen de la app (al 10 % suena al 10 %, no al 55 %)', async (page, ctx) => {
    const lib = await api(ctx, '/api/biblioteca')
    const uno = lib.generos.flatMap((g) => g.tracks).find((t) => t.titulo === 'Uno')
    const grupos = [{ opciones: [{ titulo: 'Tema Preview', artista: 'Artista P', duracion: 200, fuente: 'deezer', thumbnail: null,
      url: 'https://www.deezer.com/track/1', preview_url: `${ctx.url}/api/audio/${uno.id}` }] }]
    await page.setRequestInterception(true)
    page.on('request', (req) => {
      const u = new URL(req.url())
      const json_ = (body) => req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
      if (u.pathname === '/api/buscar') return json_({ exito: true, grupos })
      if (u.pathname === '/api/calidad') return json_({ ok: false, grade: '?' })
      if (u.pathname === '/api/meta') return json_({ bpm: null, genero: null })
      if (u.origin !== new URL(ctx.url).origin) return req.abort()
      return req.continue()
    })
    await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
    try {
      for (const nivel of ['0.1', '0.3']) {
        await page.evaluate((n) => localStorage.setItem('musiflix.volumen', n), nivel)
        await page.reload({ waitUntil: 'domcontentloaded' })
        await page.waitForSelector('input[aria-label="Buscar una canción, artista o género"]', { timeout: ESPERA_MS })
        await page.type('input[aria-label="Buscar una canción, artista o género"]', 'tema preview')
        await page.keyboard.press('Enter')
        await page.waitForSelector('.trk', { timeout: ESPERA_MS })
        await page.hover('.trk')
        const vol = await hasta(() => page.evaluate(() => {
          const a = document.querySelector('.thumb-prev audio')
          return a ? Math.round(a.volume * 100) / 100 : null
        }), (x) => x !== null, 'pasar el mouse no arrancó el preview')
        igual(vol, Number(nivel), `con la app al ${Number(nivel) * 100} % el preview no suena a ese volumen`)
        await page.mouse.move(0, 0)
      }
    } finally {
      await page.evaluate(() => localStorage.removeItem('musiflix.volumen')).catch(() => {})
    }
  }],

  ['radio (f50): «Información» dice «no declarado» cuando la licencia o el origen no vienen', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    await interceptar(page, async (req) => {
      if (req.method() !== 'GET' || new URL(req.url()).pathname !== rutaMarcas(uno.id)) return false
      // La respuesta real, sin licencia ni origen. La API de hoy manda el literal «no declarado»
      // (la base lo guarda así); null prueba que la pantalla no dibuja un vacío si faltara.
      const r = await apiPedir(ctx, rutaMarcas(uno.id))
      const cuerpo = { ...r.data, track: { ...r.data.track, licencia: null, origen: null } }
      await req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(cuerpo) })
      return true
    })
    await abrirEditor(page, ctx)
    await page.click('#r-tab-info')
    const info = await hasta(() => page.evaluate(() => {
      const dl = document.querySelector('#r-panel-info .rinfo-dl')
      if (!dl) return null
      const o = {}
      dl.querySelectorAll('dt').forEach((dt) => { o[dt.textContent] = dt.nextElementSibling?.textContent ?? null })
      return o
    }), (v) => v !== null, '«Información» no se dibujó')
    igual([info.Licencia, info.Origen], ['no declarado', 'no declarado'], 'sin licencia ni origen tiene que decir «no declarado»')
  }],

  ['400 px (f50): el detalle va debajo de la lista; pestañas, editor e «Información» sin desborde', async (page, ctx) => {
    await page.setViewport({ width: 400, height: 860 })
    await abrirEditor(page, ctx)
    await hasta(() => page.evaluate(() => document.documentElement.clientWidth), (w) => w <= 400, 'el viewport no pasó a 400 px')
    const g = await geoMD(page)
    afirmar(g.det.arriba >= g.lista.abajo, `a 400 px el detalle tiene que ir debajo de la lista: ${json(g)}`)
    let d = await desbordeDe(page)
    afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `400 px con el editor: ${json(d)}`)
    await page.click('#r-tab-info')
    await page.waitForSelector('#r-panel-info .rinfo-dl', { timeout: ESPERA_MS })
    d = await desbordeDe(page)
    afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `400 px con «Información»: ${json(d)}`)
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

  // La consola en vivo por WebSocket se sacó en f42 (pedido del dueño): el server ya no tiene
  // /ws/console y sin una librería de WebSocket uvicorn avisaba y el front reintentaba cada
  // 1,5 s. El WebSocket se mira desde el navegador (CDP), no desde el código del front: si
  // cualquier parte de la app vuelve a abrir uno, este caso se entera.
  ['sin consola en vivo: la app no abre ningún WebSocket ni ofrece un botón de consola', async (page, ctx) => {
    const sockets = []
    const pedidosWs = []
    const cdp = await page.target().createCDPSession()
    await cdp.send('Network.enable')
    cdp.on('Network.webSocketCreated', (e) => sockets.push(e.url))
    page.on('request', (r) => { if (/\/ws(\/|$)/.test(new URL(r.url()).pathname)) pedidosWs.push(r.url()) })
    await abrirHome(page, ctx)
    // Estado, no espera fija: la red quieta (un reintento cada 1,5 s no la dejaría quieta).
    await page.waitForNetworkIdle({ idleTime: 500, timeout: ESPERA_MS })
    igual({ sockets, pedidosWs }, { sockets: [], pedidosWs: [] }, 'la app abrió un WebSocket o pidió /ws/*')
    await page.click('button[aria-label="Menú"]')
    await page.waitForSelector('.drawer-left.is-open', { timeout: ESPERA_MS })
    const consola = await page.evaluate(() => [...document.querySelectorAll('button, a, [role="dialog"]')]
      .map((e) => `${e.textContent.trim()} | ${e.getAttribute('aria-label') ?? ''} | ${e.getAttribute('title') ?? ''}`)
      .filter((s) => /consola/i.test(s)))
    igual(consola, [], 'todavía hay un botón o un cajón de consola')
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
        datos: window.__datosFila(p),
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
      semilla: p.es_semilla, datos: esperadoFila(p.track, leyenda),
    }))
    igual(real.pasos.length, esperado.length, 'cantidad de pasos del set')
    for (let i = 0; i < esperado.length; i++) igual(real.pasos[i], esperado[i], `paso ${i + 1} del set`)
    // La energía (que ya no está en la fila) y el porqué grande: en el detalle de cada paso.
    for (let i = 0; i < set.pasos.length; i++) {
      const p = set.pasos[i]
      await elegirFila(page, p.n)
      const d = await page.evaluate(() => window.__detalle())
      const e = esperadoDatos(p.track, leyenda)
      igual({ motivo: d.motivo, energia: d.energia, chips: d.chips },
        { motivo: p.motivo, energia: i === 0 ? e['Energía'] : `${esperadoDatos(set.pasos[i - 1].track, leyenda)['Energía']} → ${e['Energía']}`, chips: [e.BPM, e.key.camelot, e.Dur] },
        `el detalle del paso ${i + 1}`)
    }
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
      // aria-describedby puede tener varios ids (f50: «Exportar set» lleva además su aclaración).
      const desc = (b) => (b.getAttribute('aria-describedby')
        ? b.getAttribute('aria-describedby').split(/\s+/).map((id) => document.getElementById(id)?.textContent ?? '(id sin elemento)').join(' | ')
        : null)
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
    afirmar(!/sin elemento/.test(sinNada.exportarPorQue) && /no hay set/.test(sinNada.exportarPorQue),
      `sin set, «Exportar set» tiene que decir que falta el set: ${json(sinNada.exportarPorQue)}`)
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
    // Con set: habilitado y solo con su aclaración (la de "falta el set" ya no).
    afirmar(conSet.exportar === 'false' && conSet.exportarPorQue === 'Lista .m3u8 hoy · XML con cues cuando esté verificado',
      `con set, «Exportar» tendría que estar habilitado y describirse solo con su aclaración: ${json(conSet)}`)
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

  ...CASOS_CUES,

  ...CASOS_F50,

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

  ['station: el botón abre la Station; las filas llegan en orden con "N / 49", la mejor nota elegida y suenan por el proxy', async (page, ctx) => {
    // Sin red. /api/station y /api/versiones contestan con los archivos que los tests de Python
    // comparan contra lo que los endpoints REALMENTE devuelven (tests/test_soundcloud_station.py
    // y tests/test_station_versiones.py): acá hacen de API. Lo esperado sale de esos archivos.
    const s = await montarStation(page, ctx, { retener: true })
    const { respuesta, items } = s
    await abrirStation(page, ctx, s)

    // Arranca vacía, con el avance en 0, y pide de a 3 (los demás esperan en la cola).
    const inicio = await hasta(() => leerStation(page), (v) => v.progreso && s.versiones.length === 3,
      'la Station no arrancó a pedir versiones')
    igual({ progreso: inicio.progreso, filas: inicio.filas.length }, { progreso: `0 / ${items.length}`, filas: 0 },
      'antes de la primera fila: 0 / 49 y ninguna fila')
    igual(s.versiones.map((b) => b.tema.video_id), items.slice(0, 3).map((t) => t.video_id),
      'los primeros pedidos no son los primeros temas de la Station, en su orden')
    igual(s.versiones[0].tema, items[0], 'el pedido no lleva el tema de la Station tal cual')
    igual(s.versiones[0].formato, 'wav', 'el pedido no lleva el formato de descarga')
    // La 2 contesta antes que la 1: aparecen las dos, en el orden de la Station.
    s.soltar(items[1].video_id)
    s.soltar(items[0].video_id)
    const dos = await hasta(() => leerStation(page), (v) => v.filas.length === 2, 'no aparecieron las dos primeras filas')
    igual(dos.progreso, `2 / ${items.length}`, 'el avance no dice cuántas filas hay')
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
    const fin = await hasta(() => leerStation(page), (v) => v.filas.length === items.length && v.listo,
      'no llegaron todas las filas')
    const conOtras = items.filter((t) => (s.vx[t.video_id]?.respuesta.opciones || []).some((o) => !o.estacion)).length
    igual(fin.progreso, `${conOtras} de ${items.length} con versiones`, 'el resumen final')
    // f40: cada fila muestra el título del tema de la Station, también la 1 con la versión de
    // YouTube elegida (antes mostraba el del video: parecía otra edición).
    igual(fin.filas, items.map((t) => t.titulo), 'las filas no muestran los temas de la Station, en su orden, al terminar')
    igual(s.versiones.map((b) => b.tema.video_id), items.map((t) => t.video_id), 'no se pidió cada tema una vez, en orden')

    const v = await page.evaluate(() => ({
      titulo: document.querySelector('h1.station-title')?.textContent ?? null,
      datosSemilla: document.querySelectorAll('.station-head .mb, .st-estado .mb').length,
    }))
    igual(v.titulo, `Station de «${respuesta.semilla.titulo}»`, 'el encabezado no dice de qué tema es la Station')
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
    // `desbordeDe` mira cajas, no texto: un título sin partir se sale de su propia caja sin que
    // ninguna caja se pase. Se mide el texto.
    const h = await page.evaluate(() => { const e = document.querySelector('h1.station-title'); return { texto: e.scrollWidth, caja: e.clientWidth } })
    afirmar(h.texto <= h.caja, `a 400 px el título de la Station queda cortado: ${json(h)}`)
  }],

  ['station (f43): con YouTube elegido, la antena manda la referencia de SoundCloud de la fila y llega la Station de 49 en el orden de SoundCloud', async (page, ctx) => {
    // El caso real (Kashpitzky): /api/buscar devuelve la fila con YouTube primero (elegida) y la
    // opción de SoundCloud 2041950136 (los datos que devolvió el buscador). /api/station contesta
    // station_respuesta_kashpitzky.json, que tests/test_soundcloud_station.py compara contra lo
    // que el endpoint REAL devuelve para este pedido (YouTube + sc_ref, sin /search).
    const yt = { titulo: 'Kashpitzky — For The Vision [BAO095]', artista: 'HATE', duracion: 334, fuente: 'youtube', thumbnail: null,
      url: 'https://www.youtube.com/watch?v=5HuHGbZno1Y', video_id: '5HuHGbZno1Y' }
    const sc = { titulo: 'GTG Premiere | Kashpitzky - For The Vision  [BAOX095]', artista: 'Grab The Groove', duracion: 333.249,
      fuente: 'soundcloud', thumbnail: null, url: 'https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A2041950136', video_id: '2041950136' }
    const s = await montarStation(page, ctx, { archivo: 'station_respuesta_kashpitzky.json', grupos: [{ opciones: [yt, sc] }] })
    const { respuesta, items } = s
    igual([items.length, respuesta.semilla.video_id], [49, '2041950136'], 'el archivo de la Station de Kashpitzky cambió')

    await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
    await page.type('input[aria-label="Buscar una canción, artista o género"]', 'Kashpitzky For The Vision')
    await page.keyboard.press('Enter')
    const boton = `button[aria-label="Station de SoundCloud de ${yt.titulo}"]`
    await page.waitForSelector(boton, { timeout: ESPERA_MS })
    afirmar(/^Opción 1: YouTube/.test(await elegidaDe(page, 0) || ''), `la elegida de la fila no es YouTube: ${await elegidaDe(page, 0)}`)
    await page.click(boton)
    await page.waitForSelector('h1.station-title', { timeout: ESPERA_MS })

    igual(s.pedidosStation.length, 1, 'tenía que salir un solo pedido a /api/station')
    igual(Object.fromEntries(new URL(s.pedidosStation[0]).searchParams), {
      fuente: 'youtube', fuente_id: '5HuHGbZno1Y', titulo: yt.titulo, artista: 'HATE', duracion: '334',
      sc_ref: '2041950136', sc_ref_origen: 'busqueda',
    }, 'el pedido no lleva la opción elegida y la referencia de SoundCloud de la fila')

    const fin = await hasta(() => leerStation(page), (v) => v.filas.length === items.length && v.listo,
      'no llegaron las 49 filas de la Station')
    igual(fin.filas, items.map((t) => t.titulo), 'las filas no son los temas de la Station, en el orden de SoundCloud')
    const titulo = await page.evaluate(() => document.querySelector('h1.station-title')?.textContent ?? null)
    igual(titulo, `Station de «${respuesta.semilla.titulo}»`, 'el encabezado no es el de la semilla de SoundCloud')
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

    // FblManny: nadie más lo tiene; queda el preview marcado y sin descarga. Desde 47f4754 el
    // backend no manda motivo cuando queda solo SoundCloud, y la fila no inventa uno.
    const f1 = await filaTexto(page, 1)
    afirmar(f1.includes('Preview 30 s'), `la fila del Go+ sin versión no dice que es un preview: ${f1}`)
    igual(vx[fbl.video_id].respuesta.motivo, null, 'el archivo de versiones cambió: el Go+ sin otra versión no traía motivo')
    // Las pastillas marcan el preview con "30 s" (y "solo 30 s (Go+)" para el lector), sin nota.
    const daftOps = vx[daft.video_id].respuesta.opciones
    igual(await pastillasDe(page, 0), esperadasStation(daftOps, daftOps.indexOf(completo)), 'las pastillas del Go+ resuelto (el preview con "30 s", elegido el completo)')
    igual(await pastillasDe(page, 1), esperadasStation(vx[fbl.video_id].respuesta.opciones, 0), 'la pastilla del Go+ sin otra versión: "30 s" y elegida')
    igual((await pastillasDe(page, 1))[0].texto, 'SoundCloud30 s', 'el caso no prueba nada: la pastilla del Go+ tiene que decir "30 s"')
    igual(await page.evaluate(() => [...document.querySelectorAll('.trk')][1].querySelectorAll('.trk-note').length), 0,
      'la fila del Go+ sin otra versión dice algo bajo el artista sin que el backend mande motivo')
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

  ['station: el Extended viene elegido y lo dice con su duración; un MP3 sin duración verificada se ofrece atenuado y no se elige', async (page, ctx) => {
    // f40-r2 (decisiones del dueño). «FTB» de Burjoe (fila 4): YouTube trae su Extended (nota
    // C), la Station es B y un MP3 de HitPlayer sin duración verificada tiene A. Antes ganaba
    // la A (otra edición posible) y el Extended ni se ofrecía.
    const s = await montarStation(page, ctx)
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === s.items.length, 'no llegaron todas las filas')
    const ftb = s.items[3]
    const ops = s.vx[ftb.video_id].respuesta.opciones
    igual(ops.map((o) => [o.fuente, o.edicion ?? null, o.duracion_verificada ?? null, o.calidad.grade]),
      [['youtube', 'extended', true, 'C'], ['soundcloud', null, null, 'B'], ['hitplayer', null, false, 'A']],
      'el archivo de versiones cambió: FTB tenía que traer el Extended (C), la Station (B) y un MP3 sin verificar (A)')
    const ps = await pastillasDe(page, 3)
    igual(ps, esperadasStation(ops, 0), 'las pastillas de FTB: el Extended elegido, el MP3 atenuado')
    // Que el caso pruebe algo: el Extended dice su duración a la vista y el MP3 está atenuado.
    igual([ps[0].texto, ps[0].nombre], ['YouTubeExtended 6:52C', 'Opción 1: YouTube, nota C, Extended · 6:52 (elegida)'],
      'la pastilla del Extended no dice qué es')
    igual([ps[2].sinVerificar, ps[2].nombre], [true, 'Opción 3: MP3, nota A, duración sin verificar'],
      'el MP3 sin duración verificada no va atenuado o no lo dice')
    // La fila sigue siendo el tema de la Station (título y duración suyos, no los del Extended).
    igual(await fichaDe(page, 3), { titulo: ftb.titulo, artista: ftb.artista, duracion: mmss(ftb.duracion) },
      'con el Extended elegido la fila dejó de mostrar el tema de la Station')
    await descargarFila(page, 3)
    await hasta(() => s.descargas.length, (n) => n === 1, 'la descarga de FTB no salió')
    igual(s.descargas[0].url, ops[0].url, 'se descargó otra versión y no el Extended elegido')
  }],

  ['station: sin el Extended, el MP3 sin duración verificada (nota A) tampoco viene elegido: queda la de SoundCloud', async (page, ctx) => {
    // La misma respuesta de FTB sin la opción de YouTube: quedan la Station (B) y el MP3 sin
    // verificar (A). Por nota ganaría el MP3; por la decisión del dueño (f40-r2), no se elige.
    const vx = leerVersiones()
    const id = '2172426774'
    const r = vx[id].respuesta
    const sinExt = { ...r, opciones: r.opciones.filter((o) => o.fuente !== 'youtube') }
    igual(sinExt.opciones.map((o) => [o.fuente, o.duracion_verificada ?? null, o.calidad.grade]),
      [['soundcloud', null, 'B'], ['hitplayer', false, 'A']], 'el archivo de versiones cambió: FTB sin YouTube tenía que ser SoundCloud B y MP3 A sin verificar')
    const s = await montarStation(page, ctx, { respuestas: { [id]: sinExt } })
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === s.items.length, 'no llegaron todas las filas')
    igual(s.items[3].video_id, id, 'la fila 4 no es FTB')
    igual(await pastillasDe(page, 3), esperadasStation(sinExt.opciones, 0), 'el MP3 sin verificar quedó elegido o no va atenuado')
    await descargarFila(page, 3)
    await hasta(() => s.descargas.length, (n) => n === 1, 'la descarga de FTB no salió')
    igual(s.descargas[0].url, sinExt.opciones[0].url, 'se descargó el MP3 sin duración verificada')
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

  ['station (f45): el orden por defecto es el de SoundCloud; "Para mezclar" reordena con el por qué del motor y vuelve', async (page, ctx) => {
    // API doblada: el análisis y el orden los contesta el test (el cálculo real lo prueban los
    // tests de Python con audio sintético). Acá se prueba la pantalla: default, cambio y vuelta.
    const s = await montarStation(page, ctx)
    const ids = s.items.map((it) => String(it.video_id))
    s.analisis = Object.fromEntries(ids.map((id, k) => [id, k === 1
      ? { ok: false, motivo: 'SoundCloud no ofrece un audio que se pueda reproducir acá.' }
      : { ok: true, bpm: 150 + (k % 7) * 0.1, key: '8A', key_dudosa: k % 2 === 0, preview: false, tramo_s: 140 }]))
    const set = [ids[3], ids[0], ids[2]]
    const resto = ids.filter((id) => !set.includes(id) && id !== ids[1])
    s.orden = { exito: true, en_set: set.length + resto.length - 1, fuera: 1, sin_analisis: 1, aviso: null, orden: [
      ...set.map((id, k) => ({ video_id: id, grupo: 'set', razon: `+${k}.5% BPM | 8A → 8A (mismo)` })),
      ...resto.slice(0, -1).map((id) => ({ video_id: id, grupo: 'set', razon: '+0.0% BPM | 8A → 8A (mismo)' })),
      { video_id: resto.at(-1), grupo: 'fuera', motivo: 'Fuera de rango de BPM: 99.0 BPM no está a ±8% de ningún tema del set (con medio/doble tiempo).' },
      { video_id: ids[1], grupo: 'sin_analisis', motivo: 'SoundCloud no ofrece un audio que se pueda reproducir acá.' },
    ] }
    const titulos = s.items.map((it) => it.titulo)
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === s.items.length, 'no llegaron todas las filas')
    const mezclar = '.station-orden button:nth-of-type(2)'
    await hasta(() => page.evaluate((q) => !document.querySelector(q)?.disabled, mezclar), (x) => x, '"Para mezclar" no se habilitó al terminar el análisis')
    igual((await leerStation(page)).filas, titulos, 'el orden por defecto no es el de SoundCloud')
    // f46: el resumen del orden pasó a la aclaración bajo el selector (antes, al lado).
    const listo = await leerOrden(page)
    igual([listo.pressed, listo.candado, listo.aclaracion], [['true', 'false'], false,
      `Listo: podés ordenar para mezclar · ${s.orden.en_set} en el set · 1 fuera de rango de BPM · 1 sin analizar`],
    'con el orden listo, "Para mezclar" sin candado y la aclaración con el resumen')
    const medido = () => page.evaluate(() => [...document.querySelectorAll('.trk')].slice(0, 2).map((r) =>
      [...r.querySelectorAll('.mb-medido b')].map((b) => b.textContent)))
    igual(await medido(), [['150.0', '8A?'], ['?', '?']], 'BPM/key medidos de las dos primeras filas (un decimal, "?" si no se pudo o es dudosa)')
    await page.click(mezclar)
    const esperado = s.orden.orden.map((f) => titulos[ids.indexOf(f.video_id)])
    igual((await leerStation(page)).filas, esperado, '"Para mezclar" no siguió el orden del motor')
    const notas = await page.evaluate(() => [...document.querySelectorAll('.trk')].map((r) => r.querySelector('.trk-razon')?.textContent ?? null))
    igual(notas[0], '+0.5% BPM | 8A → 8A (mismo)', 'el por qué de la primera fila')
    afirmar(notas.at(-2).startsWith('Al final: Fuera de rango de BPM'), `la fila fuera de rango no dice por qué: ${notas.at(-2)}`)
    afirmar(notas.at(-1).startsWith('Al final: SoundCloud no ofrece'), `la fila sin análisis no dice por qué: ${notas.at(-1)}`)
    // f46: el encabezado dice que el orden es del motor, y "SoundCloud" sigue una sola vez.
    const mezclado = await leerOrden(page)
    igual([mezclado.pressed, mezclado.sub, mezclado.soundcloud, mezclado.aclaracion], [['false', 'true'],
      `${s.respuesta.semilla.artista} · ${titulos.length} temas recomendados por SoundCloud · ordenados para mezclar por el motor`, 1,
      `Ordenado para mezclar · ${s.orden.en_set} en el set · 1 fuera de rango de BPM · 1 sin analizar`],
    'en "Para mezclar" el encabezado y la aclaración no dicen que el orden es del motor')
    await page.click('.station-orden button:nth-of-type(1)')
    igual((await leerStation(page)).filas, titulos, 'volver a "SoundCloud" no restituyó su orden')
    igual(await page.evaluate(() => document.querySelectorAll('.trk-razon').length), 0, 'en orden SoundCloud quedó el por qué del motor')
    igual(new Set(s.pedidosAnalisis).size, s.pedidosAnalisis.length, 'se pidió dos veces el análisis de un tema')
  }],

  /* ---------- f46: encabezado y panel de estado (opción A del dueño) ---------- */

  ['station (f46): el encabezado dice "Station de «tema»" y "artista · N temas recomendados por SoundCloud", con "SoundCloud" una vez y sin eyebrow ni borde de acento', async (page, ctx) => {
    const s = await montarStation(page, ctx)
    const { respuesta, items } = s
    const leer = () => page.evaluate(() => {
      const h = document.querySelector('.station-head')
      return { titulo: h?.querySelector('h1')?.textContent ?? null, sub: h?.querySelector('.station-sub')?.textContent ?? null,
        soundcloud: (h?.textContent.match(/SoundCloud/g) || []).length,
        eyebrow: document.querySelectorAll('#contenido .seedbar, .station-head .eyebrow').length,
        borde: h ? getComputedStyle(h).borderLeftWidth : null, acento: h ? getComputedStyle(h, '::before').content : null,
        viejo: /recomendado por soundcloud, en su orden|según la Station de SoundCloud|temas con versiones|Analizando \d/i.test(document.querySelector('#contenido')?.innerText || '') }
    })
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === items.length && v.listo, 'no llegaron todas las filas')
    igual(await leer(), { titulo: `Station de «${respuesta.semilla.titulo}»`,
      sub: `${respuesta.semilla.artista} · ${items.length} temas recomendados por SoundCloud`,
      soundcloud: 1, eyebrow: 0, borde: '0px', acento: 'none', viejo: false }, 'el encabezado de la Station')
    // Con un solo tema, en singular.
    s.respuesta.items = items.slice(0, 1)
    s.respuesta.total = 1
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === 1 && v.listo, 'no llegó la única fila')
    igual((await leer()).sub, `${respuesta.semilla.artista} · 1 tema recomendado por SoundCloud`, 'con un tema la línea no va en singular')
  }],

  ['station (f46): dos barras de progreso (versiones y medición) con sus valores mientras carga y su estado final al terminar', async (page, ctx) => {
    const s = await montarStation(page, ctx, { retener: true })
    const { respuesta, items } = s
    const ids = items.map((it) => String(it.video_id))
    const semilla = String(respuesta.semilla.video_id)
    // Se mide la semilla además de los temas: en este archivo no está entre ellos y todos tienen id.
    afirmar(!ids.includes(semilla) && ids.every((id) => /^[1-9]\d*$/.test(id)), 'el archivo de la Station cambió: la semilla está entre los temas o hay temas sin id')
    const N = items.length, M = N + 1
    s.retenerAnalisis = true
    const ok = { ok: true, bpm: 128, key: '8A', key_dudosa: false, preview: false, tramo_s: 140 }
    s.analisis = Object.fromEntries([semilla, ...ids].map((id) => [id, ok]))
    for (const id of [ids[3], ids[7]]) s.analisis[id] = { ok: false, motivo: 'SoundCloud no ofrece un audio que se pueda reproducir acá.' }
    await abrirStation(page, ctx, s)
    const inicio = await hasta(() => leerAvances(page), (a) => a.versiones && a.medicion && s.versiones.length === 3 && s.pendAnalisis.size === 2,
      'no arrancaron a pedir versiones y análisis')
    igual(inicio, {
      versiones: { estado: 'cargando', rotulo: 'Buscando versiones en otras plataformas', valor: `0 / ${N}`, min: 0, now: 0, max: N, texto: `0 de ${N}` },
      medicion: { estado: 'cargando', rotulo: 'Midiendo BPM y key (incluye el tema original)', valor: `0 / ${M}`, min: 0, now: 0, max: M, texto: `0 de ${M}` },
    }, 'las dos barras al arrancar')
    // Cada una avanza por su lado.
    s.soltar(ids[0]); s.soltar(ids[1])
    for (const ref of [...s.pendAnalisis.keys()]) s.soltarAnalisis(ref)
    const medio = await hasta(() => leerAvances(page), (a) => a.versiones.now === 2 && a.medicion.now === 2, 'las barras no avanzaron')
    igual([medio.versiones.valor, medio.versiones.texto, medio.medicion.valor, medio.medicion.texto, medio.versiones.estado, medio.medicion.estado],
      [`2 / ${N}`, `2 de ${N}`, `2 / ${M}`, `2 de ${M}`, 'cargando', 'cargando'], 'las barras a mitad de camino')
    s.soltarTodas()
    s.soltarAnalisisTodos()
    const fin = await hasta(() => leerAvances(page), (a) => a.versiones.estado === 'listo' && a.medicion.estado === 'listo', 'las barras no terminaron')
    const conOtras = items.filter((t) => (s.vx[t.video_id]?.respuesta.opciones || []).some((o) => !o.estacion)).length
    igual(fin, {
      versiones: { estado: 'listo', rotulo: 'Versiones en otras plataformas', valor: `${conOtras} de ${N} con versiones`, min: 0, now: N, max: N, texto: `${conOtras} de ${N} con versiones` },
      medicion: { estado: 'listo', rotulo: 'BPM y key (incluye el tema original)', valor: `${M - 2} medidos, 2 sin poder medir`, min: 0, now: M, max: M, texto: `${M - 2} medidos, 2 sin poder medir` },
    }, 'las dos barras al terminar')
    // El total de la medición es lo que se mandó a medir: cada tema y la semilla, una vez.
    igual([s.pedidosAnalisis.length, new Set(s.pedidosAnalisis).size, s.pedidosAnalisis.includes(semilla)], [M, M, true],
      'la barra de medición no cuenta lo que se mide')
  }],

  ['station (f46): "Para mezclar" deshabilitado con candado y su aclaración mientras mide; habilitado al terminar, con teclado; y sin medición, el motivo', async (page, ctx) => {
    const s = await montarStation(page, ctx)
    const { respuesta, items } = s
    const ids = items.map((it) => String(it.video_id))
    const titulos = items.map((it) => it.titulo)
    const N = items.length, M = N + 1
    s.retenerAnalisis = true
    s.analisis = Object.fromEntries([String(respuesta.semilla.video_id), ...ids].map((id) => [id, { ok: true, bpm: 128, key: '8A', key_dudosa: false, preview: false, tramo_s: 140 }]))
    s.orden = { exito: true, en_set: N, fuera: 0, sin_analisis: 0, aviso: null,
      orden: [...ids].reverse().map((id) => ({ video_id: id, grupo: 'set', razon: '+0.0% BPM | 8A → 8A (mismo)' })) }
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === N, 'no llegaron todas las filas')
    const antes = await leerOrden(page)
    igual(antes, { rotulo: 'Orden', botones: ['SoundCloud', 'Para mezclar'], pressed: ['true', 'false'], disabled: [false, true], candado: true,
      describedby: 'station-orden-hint', aclaracion: '«Para mezclar» se activa cuando termine de medir BPM y key.', mismoAncho: true,
      anuncio: `Midiendo BPM y key de ${M} temas.`, sub: `${respuesta.semilla.artista} · ${N} temas recomendados por SoundCloud`, soundcloud: 1 },
    'mientras mide: "Para mezclar" deshabilitado, con candado y la aclaración enlazada')
    const sc = '.st-estado .station-orden [role="group"] button:nth-of-type(1)'
    await page.focus(sc)
    await page.keyboard.press('Tab')
    afirmar(await page.evaluate(() => document.activeElement?.textContent.trim()) !== 'Para mezclar', 'el foco se detuvo en "Para mezclar" deshabilitado')
    // Medio camino: la región viva no anuncia cada tema.
    for (const ref of [...s.pendAnalisis.keys()]) s.soltarAnalisis(ref)
    await hasta(() => leerAvances(page), (a) => a.medicion?.now === 2, 'la medición no avanzó')
    igual((await leerOrden(page)).anuncio, `Midiendo BPM y key de ${M} temas.`, 'la región viva cambió con un tema medido (tiene que anunciar al empezar y al terminar)')
    s.soltarAnalisisTodos()
    const despues = await hasta(() => leerOrden(page), (o) => !o.disabled[1], '"Para mezclar" no se habilitó al terminar de medir')
    igual([despues.disabled, despues.candado, despues.aclaracion, despues.anuncio],
      [[false, false], false, `Listo: podés ordenar para mezclar · ${N} en el set`, `Medición terminada: ${M} medidos. «Para mezclar» disponible.`],
      'al terminar: sin candado, la aclaración y el anuncio cambian')
    // Teclado: Tab desde "SoundCloud" llega a "Para mezclar" con el foco visible; Espacio ordena.
    await page.focus(sc)
    await page.keyboard.press('Tab')
    const foco = await page.evaluate(() => {
      const a = document.activeElement
      const cs = getComputedStyle(a)
      return { texto: a.textContent.trim(), visible: a.matches(':focus-visible'), anillo: cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 2 }
    })
    igual(foco, { texto: 'Para mezclar', visible: true, anillo: true }, 'el foco con teclado en "Para mezclar"')
    await page.keyboard.press('Space')
    await hasta(() => leerOrden(page), (o) => o.pressed[1] === 'true', 'Espacio no eligió "Para mezclar"')
    igual((await leerStation(page)).filas, [...titulos].reverse(), 'con teclado, "Para mezclar" no siguió el orden del motor')
    await page.keyboard.down('Shift'); await page.keyboard.press('Tab'); await page.keyboard.up('Shift')
    await page.keyboard.press('Enter')
    await hasta(() => leerOrden(page), (o) => o.pressed[0] === 'true', 'Shift+Tab y Enter no volvieron a "SoundCloud"')
    igual((await leerStation(page)).filas, titulos, 'volver a "SoundCloud" con teclado no restituyó su orden')

    // Sin nada medido: el backend dice por qué no hay orden, y la aclaración lo repite.
    const motivo = 'No se pudo analizar ningún tema: no hay orden para mezclar.'
    s.analisis = {}
    s.orden = { exito: false, mensaje: motivo }
    await abrirStation(page, ctx, s)
    const sin = await hasta(() => leerOrden(page), (o) => o.aclaracion === motivo, 'sin medición la aclaración no dice por qué')
    igual([sin.disabled, sin.candado, sin.anuncio], [[false, true], true, `Medición terminada: 0 medidos, ${M} sin poder medir. ${motivo}`],
      'sin medición: "Para mezclar" deshabilitado, con candado, y el anuncio con el motivo')
    igual((await leerAvances(page)).medicion.valor, `0 medidos, ${M} sin poder medir`, 'la barra de medición sin nada medido')
  }],

  ['station (f46): las filas que cargan tienen la forma de la final: mismas columnas alineadas, "Buscando versiones…" o "En cola", acciones deshabilitadas y contraste AA', async (page, ctx) => {
    const s = await montarStation(page, ctx, { retener: true })
    const { items } = s
    s.retenerAnalisis = true
    await abrirStation(page, ctx, s)
    await hasta(() => s.versiones.length, (n) => n === 3, 'no arrancó a pedir versiones')
    s.soltar(items[0].video_id)
    await hasta(() => leerStation(page), (v) => v.filas.length === 1, 'no apareció la primera fila')
    await hasta(() => s.versiones.length, (n) => n === 4, 'no salió el cuarto pedido')
    const leer = () => page.evaluate(() => {
      const celdas = (r) => [...r.children].map((c) => {
        const b = c.getBoundingClientRect()
        return { clase: c.className.split(' ')[0], x: Math.round(b.left), w: Math.round(b.width) }
      })
      const pend = [...document.querySelectorAll('.results .trk-pending')]
      return { head: celdas(document.querySelector('.results-head')).map((c) => c.x), final: celdas(document.querySelector('.trk')),
        pend: pend.map((r) => ({ celdas: celdas(r), idx: r.querySelector('.trk-idx')?.textContent, titulo: r.querySelector('.trk-title')?.textContent,
          estado: r.dataset.estado, vers: r.querySelector('.trk-vers')?.textContent.trim(), spinner: !!r.querySelector('.trk-vers .spinner'),
          grises: [r.querySelectorAll('.trk-meta .sk').length, r.querySelectorAll('.trk-grade .sk').length],
          acciones: [...r.querySelectorAll('.trk-acts button')].map((b) => b.disabled), oculta: r.getAttribute('aria-hidden') })),
        viejo: /buscando versiones en YouTube/i.test(document.querySelector('#contenido').innerText) }
    })
    const d = await leer()
    igual(d.pend.length, items.length - 1, 'no hay una fila pendiente por cada tema que falta')
    igual(d.final.length, 7, 'la fila final no tiene 7 celdas')
    igual(d.pend.map((p) => p.celdas.map((c) => c.clase)), d.pend.map(() => d.final.map((c) => c.clase)),
      'las filas pendientes no tienen las mismas celdas que la final')
    // Alineadas: cada celda empieza y mide lo mismo que la de la fila final, y que el encabezado.
    igual(d.final.map((c) => c.x), d.head, 'la fila final no está alineada con el encabezado (el caso no prueba nada)')
    for (const p of d.pend) igual([p.celdas.map((c) => c.x), p.celdas.map((c) => c.w)], [d.final.map((c) => c.x), d.final.map((c) => c.w)], `la fila pendiente ${p.idx} no está alineada con las columnas`)
    igual(d.pend.map((p) => [p.idx, p.titulo, p.estado, p.vers, p.spinner, p.grises, p.acciones, p.oculta]),
      items.slice(1).map((t, k) => [String(k + 2).padStart(2, '0'), t.titulo, k < 3 ? 'buscando' : 'cola', k < 3 ? 'Buscando versiones…' : 'En cola', k < 3, [2, 1], [true, true], 'true']),
      'lo que dice cada fila pendiente (las 3 pedidas buscan, el resto en cola)')
    afirmar(!d.viejo, 'quedó la fila pendiente vieja ("buscando versiones en YouTube, MP3 y Spotify…")')
    // Contraste AA (4.5:1) del texto chico del panel y de las filas pendientes, sobre su fondo.
    const malos = (await contrasteDe(page, ['.st-estado .st-rotulo', '.st-estado .st-hint', '.st-avance-head > span:first-child', '.st-avance-det', '.st-avance-val',
      '.st-seg button[aria-pressed="true"]', '.station-sub', '.trk-pending .trk-idx', '.trk-pending .trk-artist',
      '.trk-pending[data-estado="buscando"] .st-pend-vers', '.trk-pending[data-estado="cola"] .st-pend-vers'])).filter((c) => !(c.ratio >= 4.5))
    igual(malos, [], 'texto con contraste menor a 4.5:1')
  }],

  ['station (f46): a 1280 px el panel va en dos columnas y a 400 px se apila (orden arriba, avances abajo) sin que nada se salga, cargando y al terminar', async (page, ctx) => {
    const s = await montarStation(page, ctx, { retener: true })
    const { items } = s
    const geo = () => page.evaluate(() => {
      const r = (q) => document.querySelector(q).getBoundingClientRect()
      const p = r('.st-estado'), o = r('.st-estado .station-orden'), a = r('.st-estado .st-avances')
      return { apilado: a.top >= o.bottom - 1, lado: a.left >= o.right - 1 && Math.abs(a.top - o.top) < 2,
        dentro: [o, a].every((x) => x.left >= p.left - 1 && x.right <= p.right + 1), avancesAncho: Math.round(a.width), panelAncho: Math.round(p.width),
        cortado: [...document.querySelectorAll('.st-estado *:not(.sr-only), .station-head *')].filter((e) => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== 'visible').map((e) => e.className) }
    })
    await page.setViewport({ width: 1280, height: 860 })
    await abrirStation(page, ctx, s)
    await hasta(() => s.versiones.length, (n) => n === 3, 'no arrancó a pedir versiones')
    const ancho = await geo()
    afirmar(ancho.lado && !ancho.apilado && ancho.dentro, `a 1280 px el panel no tiene el orden a la izquierda y los avances a la derecha: ${json(ancho)}`)
    await page.setViewport({ width: 400, height: 860 })
    await hasta(() => page.evaluate(() => document.documentElement.clientWidth), (w) => w <= 400, 'el viewport no pasó a 400 px')
    s.soltar(items[0].video_id)
    await hasta(() => leerStation(page), (v) => v.filas.length === 1, 'no apareció la primera fila')
    for (const momento of ['cargando', 'al terminar']) {
      if (momento === 'al terminar') {
        s.soltarTodas()
        await hasta(() => leerStation(page), (v) => v.listo && v.filas.length === items.length, 'no terminó')
      }
      const g = await geo()
      afirmar(g.apilado && g.dentro && g.avancesAncho >= g.panelAncho - 2 * 16 - 4 && g.cortado.length === 0,
        `a 400 px (${momento}) el panel no está apilado, se sale o corta texto: ${json(g)}`)
      const d = await desbordeDe(page)
      afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `Station a 400 px (${momento}): hay contenido fuera del ancho: ${json(d)}`)
    }
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

  /* ---------- f40: la Station con las mismas pastillas en línea que la búsqueda ----------
     El plegado de f38 ("+N versiones" con una sub-lista) se sacó a pedido del dueño. Estos casos
     cubren lo que aquellos protegían y sigue valiendo: qué versión viene elegida y qué se baja,
     que cada versión suene por donde corresponde y la pastilla lo diga, el teclado, el motivo
     una sola vez, "MP3" sin el sitio y 400 px. */

  ['station (f40): las versiones son pastillas como en la búsqueda: la de mejor nota elegida con ✓, cada una con su nota, y elegir otra cambia lo que se baja', async (page, ctx) => {
    const s = await montarStation(page, ctx, {})
    const { items } = s
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === items.length, 'no llegaron todas las filas')

    // La columna se llama "Versiones" como en la búsqueda, y del plegado no queda nada.
    igual(await page.evaluate((plegado) => ({ station: !!document.querySelector('.results.results-station'),
      columna: [...document.querySelectorAll('.results-head > div')].map((d) => d.textContent)[5] ?? null,
      plegado: document.querySelectorAll(plegado).length }), PLEGADO),
    { station: true, columna: 'Versiones', plegado: 0 }, 'el encabezado de la Station o restos del plegado')

    const r0 = s.vx[items[0].video_id].respuesta
    igual(r0.opciones.filter((o) => o.calidad?.grade === 'A').length, 1, 'el archivo de versiones cambió: la fila 1 tenía una sola A')
    const mejorK = r0.opciones.findIndex((o) => o.calidad?.grade === 'A')
    const ytK = r0.opciones.findIndex((o) => o.fuente === 'youtube')
    afirmar(mejorK !== 0, 'el caso no prueba nada: la de mejor nota tiene que no ser la primera')

    // Una pastilla por versión, en el orden de la API. ✓ (y aria-pressed) solo en la elegida; las
    // demás llevan su número. La nota de cada una, al lado; Spotify no tiene (se baja de YouTube).
    igual(await pastillasDe(page, 0), esperadasStation(r0.opciones, mejorK), 'la fila 1 con la de mejor nota elegida')

    // Elegir YouTube (nota B): cambia la elegida, la descarga baja esa, y la fila sigue siendo
    // el tema de la Station (f40: no el título ni la duración del video).
    const yt = r0.opciones[ytK]
    await elegir(page, 0, 'YouTube')
    await hasta(() => pastillasDe(page, 0), (ps) => ps[ytK].elegida === 'true', 'tocar la pastilla de YouTube no la eligió')
    igual(await pastillasDe(page, 0), esperadasStation(r0.opciones, ytK), 'elegida YouTube: el ✓ pasa a YouTube y la de mejor nota muestra su número')
    afirmar(mmss(yt.duracion) !== mmss(items[0].duracion), `el caso no prueba nada: YouTube y la Station duran lo mismo (${mmss(yt.duracion)})`)
    igual(await fichaDe(page, 0), { titulo: items[0].titulo, artista: items[0].artista, duracion: mmss(items[0].duracion) },
      'con YouTube elegida, la fila dejó de mostrar el tema de la Station')
    await descargarFila(page, 0)
    await hasta(() => s.descargas.length, (x) => x === 1, 'la descarga no salió')
    igual(s.descargas[0].url, yt.url, 'después de elegir YouTube se bajó otra versión')

    // De vuelta en la de mejor nota: se baja esa.
    await elegir(page, 0, null, mejorK)
    await hasta(() => pastillasDe(page, 0), (ps) => ps[mejorK].elegida === 'true', 'no volvió a quedar elegida la de mejor nota')
    igual(await pastillasDe(page, 0), esperadasStation(r0.opciones, mejorK), 'de vuelta en la de mejor nota')
    await descargarFila(page, 0)
    await hasta(() => s.descargas.length, (x) => x === 2, 'la segunda descarga no salió')
    igual(s.descargas[1].url, r0.opciones[mejorK].url, 'de vuelta en la de mejor nota se bajó otra versión')
    igual(await sonando(page), [], 'elegir una pastilla puso algo a sonar')
  }],

  ['station (f40): YouTube y SoundCloud suenan por el proxy de audio, sin video; la pastilla que suena lo dice (aria-current, barritas) y la fila sigue siendo el tema de la Station', async (page, ctx) => {
    const s = await montarStation(page, ctx, {})
    const { items } = s
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === items.length, 'no llegaron todas las filas')
    const r0 = s.vx[items[0].video_id].respuesta
    const total = r0.opciones.length
    const k = (f, extra = () => true) => r0.opciones.findIndex((o) => o.fuente === f && extra(o))
    const [ytK, scK, hitK] = [k('youtube'), k('soundcloud', (o) => o.estacion), k('hitplayer')]
    const medio = () => page.evaluate(() => [...window.__medios].find((m) => !m.paused && !m.ended && !m.error)?.src ?? null)
    const fila = () => page.evaluate(() => ({
      sonando: document.querySelector('.trk')?.getAttribute('aria-current') ?? null,
      ahora: document.querySelector('.trk .trk-now')?.textContent ?? null,
      fuente: document.querySelector('.deck-src')?.textContent ?? null,
      cola: document.querySelector('.deck-count')?.textContent ?? null,
      monitor: window.__visible(document.querySelector('.deck-monitor')),
      iframes: document.querySelectorAll('iframe').length,
    }))
    // Qué pastilla suena: [elegida, aria-current, barritas, barritas animadas] de cada una.
    const estados = async () => (await pastillasDe(page, 0)).map((p) => [p.elegida, p.suena, p.barras, p.animadas])
    const esperados = (elegidaK, suenaK, animadas) => r0.opciones.map((_, j) =>
      [String(j === elegidaK), j === suenaK ? 'true' : null, j === suenaK, j === suenaK && animadas])
    const ficha = { titulo: items[0].titulo, artista: items[0].artista, duracion: mmss(items[0].duracion) }

    // YouTube: se elige y se le da play a la fila. Suena como audio del backend, con su id.
    await elegir(page, 0, 'YouTube')
    await hasta(() => pastillasDe(page, 0), (ps) => ps[ytK].elegida === 'true', 'no quedó elegida la de YouTube')
    await page.click('.trk:nth-child(2) .thumb-play')     // nth-child(1) es el encabezado
    const barra = await hasta(() => leerBarra(page), (d) => d && d.estado === 'playing', 'la fila 1 no quedó sonando con YouTube')
    igual(barra.titulo, r0.opciones[ytK].titulo, 'la barra no muestra la versión de YouTube')
    const src = new URL(await medio())
    igual(src.pathname + '?' + src.searchParams.toString(), `/api/fuente/audio?fuente=youtube&ref=${r0.opciones[ytK].video_id}`,
      'la versión de YouTube no suena por el proxy de audio con su id')
    igual(await sonando(page), ['/api/fuente/audio'], 'tiene que sonar un solo audio')
    await hasta(estados, (e) => e[ytK][3], 'la pastilla de YouTube no quedó "sonando"')
    igual(await estados(), esperados(ytK, ytK, true), 'solo la pastilla de YouTube suena (aria-current y barritas animadas)')
    igual((await pastillasDe(page, 0))[ytK].nombre, `Opción ${ytK + 1}: YouTube, nota B (elegida, sonando ahora)`, 'el nombre para lector de la que suena')
    igual(await fila(), { sonando: 'true', ahora: `Sonando: opción ${ytK + 1} · YouTube`, fuente: `YouTube · ${ytK + 1}/${total}, opción ${ytK + 1} de ${total}, solo audio`,
      cola: `tema 1/${items.length}`, monitor: false, iframes: 0 }, 'la fila y la barra dicen qué versión suena, sin video; la cola sigue siendo la de la Station')
    igual(await fichaDe(page, 0), ficha, 'con YouTube sonando, la fila dejó de mostrar el tema de la Station')

    // Elegir la de la Station (SoundCloud) mientras suena YouTube: elegida y sonando se distinguen.
    await elegir(page, 0, null, scK)
    await hasta(estados, (e) => e[scK][0] === 'true', 'no quedó elegida la de SoundCloud')
    igual(await estados(), esperados(scK, ytK, true), 'elegida SoundCloud y sonando YouTube tienen que distinguirse')
    // Play: SoundCloud por el proxy, con el id del tema.
    await page.click('.trk:nth-child(2) .thumb-play')
    await hasta(medio, (u) => (u || '').includes(`fuente=soundcloud&ref=${items[0].video_id}`), 'la versión de SoundCloud no sonó por el proxy con su id')
    await hasta(estados, (e) => e[scK][3], 'la pastilla de SoundCloud no quedó "sonando"')
    igual(await estados(), esperados(scK, scK, true), 'la pastilla que suena pasó a SoundCloud')
    igual(await sonando(page), ['/api/fuente/audio'], 'con SoundCloud sonando tiene que haber un solo audio')

    // Pausa: la pastilla sigue marcada como la de la barra, con las barritas quietas.
    await page.click('.deck-play')
    await hasta(() => leerBarra(page), (d) => d.estado === 'paused', 'la barra no pausó')
    await hasta(estados, (e) => !e[scK][3], 'en pausa las barritas siguen animadas')
    igual(await estados(), esperados(scK, scK, false), 'en pausa: aria-current sigue, barritas quietas')
    igual((await fila()).ahora, `En pausa: opción ${scK + 1} · SoundCloud`, 'la fila no dice que quedó en pausa')

    // Un MP3: su archivo, sin pasar por el proxy.
    await elegir(page, 0, null, hitK)
    await page.click('.trk:nth-child(2) .thumb-play')
    await hasta(medio, (u) => u === r0.opciones[hitK].stream_url, 'el MP3 no sonó desde su archivo')
    await hasta(estados, (e) => e[hitK][3], 'la pastilla del MP3 no quedó "sonando"')
    igual(await sonando(page), [new URL(r0.opciones[hitK].stream_url).pathname], 'con el MP3 sonando tiene que haber un solo audio')
    igual(await fichaDe(page, 0), ficha, 'con el MP3 sonando, la fila dejó de mostrar el tema de la Station')
    igual(s.descargas.length, 0, 'escuchar descargó algo')
  }],

  ['station (f40): con teclado, Tab llega a las pastillas y Enter o Espacio eligen; el foco se queda en la pastilla y nada suena', async (page, ctx) => {
    const s = await montarStation(page, ctx, {})
    const { items } = s
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === items.length, 'no llegaron todas las filas')
    const r0 = s.vx[items[0].video_id].respuesta
    const foco = () => page.evaluate(() => {
      const e = document.activeElement
      return { pastilla: !!e?.classList.contains('vchip'), nombre: e?.getAttribute('aria-label') ?? null, elegida: e?.getAttribute('aria-pressed') ?? null }
    })
    // Desde el play de la fila, Tab recorre lo que sigue en orden: la primera parada con foco
    // en VERSIONES es la pastilla 1.
    await page.focus('.trk:nth-child(2) .thumb-play')
    for (let t = 0; t < 6 && !(await foco()).pastilla; t++) await page.keyboard.press('Tab')
    const p1 = await foco()
    afirmar(p1.pastilla, `con Tab desde el play de la fila no se llega a las pastillas: ${json(p1)}`)
    igual(p1.nombre.replace(/ \(.*\)$/, ''), esperadasStation(r0.opciones, -1)[0].nombre, 'Tab no llega primero a la pastilla 1')

    // Enter sobre YouTube (la 1): la elige y el foco sigue ahí.
    const ytK = r0.opciones.findIndex((o) => o.fuente === 'youtube')
    igual(ytK, 0, 'el archivo de versiones cambió: YouTube era la opción 1')
    await page.keyboard.press('Enter')
    await hasta(() => pastillasDe(page, 0), (ps) => ps[ytK].elegida === 'true', 'Enter sobre la pastilla no la eligió')
    igual(await foco(), { pastilla: true, nombre: `${esperadasStation(r0.opciones, ytK)[ytK].nombre}`, elegida: 'true' }, 'después de Enter el foco se perdió o no dice "elegida"')
    igual(await elegidaDe(page, 0), esperadasStation(r0.opciones, ytK)[ytK].nombre.replace(/ \(.*\)$/, ''), 'Enter no dejó elegida a YouTube')

    // Tab a la 2 (SoundCloud) y Espacio: la elige.
    await page.keyboard.press('Tab')
    const p2 = await foco()
    igual(p2.nombre, esperadasStation(r0.opciones, ytK)[1].nombre, 'Tab no pasó a la pastilla 2')
    await page.keyboard.press('Space')
    await hasta(() => pastillasDe(page, 0), (ps) => ps[1].elegida === 'true', 'Espacio sobre la pastilla no la eligió')
    igual(await pastillasDe(page, 0), esperadasStation(r0.opciones, 1), 'Espacio no dejó elegida a SoundCloud (y solo a ella)')
    igual((await foco()).nombre, esperadasStation(r0.opciones, 1)[1].nombre, 'después de Espacio el foco no quedó en la pastilla')

    // Lo elegido con teclado es lo que se baja; elegir no puso nada a sonar.
    await descargarFila(page, 0)
    await hasta(() => s.descargas.length, (x) => x === 1, 'la descarga no salió')
    igual(s.descargas[0].url, r0.opciones[1].url, 'se bajó otra versión y no la elegida con teclado')
    igual(await sonando(page), [], 'elegir con teclado puso algo a sonar')
  }],

  ['station (f40): sin otras versiones queda solo la pastilla de SoundCloud y ningún texto; si una plataforma no contestó, "No contestó a tiempo" una sola vez, bajo el artista', async (page, ctx) => {
    // Fila 1: la respuesta real con los dos MP3 caídos (así la arma el backend: ver
    // test_motivo_cuando_una_plataforma_no_contesta_a_tiempo). Filas 2 y 3: una sola versión y
    // sin motivo (la 2 con su respuesta grabada; la 3 no está en el archivo y contesta la forma
    // del "no hay otras" de ese mismo archivo). Desde 47f4754 el backend no manda motivo cuando
    // queda solo SoundCloud: la fila no tiene nada que decir.
    const st = leerJson('station_respuesta.json')
    const vx = leerVersiones()
    const t0 = st.items[0]
    const r0 = vx[t0.video_id].respuesta
    const sinMp3 = { ...r0, motivo: 'No contestó a tiempo: MP3', fallidas: ['ligaudio', 'hitplayer'],
      opciones: r0.opciones.filter((o) => !['ligaudio', 'hitplayer'].includes(o.fuente)) }
    const s = await montarStation(page, ctx, { respuestas: { [t0.video_id]: sinMp3 } })
    const { items } = s
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === items.length, 'no llegaron todas las filas')
    const leer = (i) => page.evaluate((k) => {
      const r = [...document.querySelectorAll('.trk')][k]
      return { notas: [...r.querySelectorAll('.trk-note')].map((e) => e.textContent),
        bajoArtista: [...r.querySelectorAll('.trk-id > .trk-artist ~ .trk-note.is-warn')].map((e) => e.textContent),
        versiones: r.querySelector('.trk-vers').textContent.trim(), texto: r.innerText }
    }, i)
    const veces = (texto, que) => texto.split(que).length - 1

    for (const i of [1, 2]) {
      const r = s.vx[items[i].video_id]?.respuesta
      igual(r ? r.motivo : null, null, `el archivo de versiones cambió: la fila ${i + 1} no tenía motivo`)
      const opciones = r ? r.opciones : [{ ...items[i], estacion: true }]
      igual(opciones.map((o) => o.fuente), ['soundcloud'], `el caso no prueba nada: la fila ${i + 1} tiene que traer solo SoundCloud`)
      igual(await pastillasDe(page, i), esperadasStation(opciones, 0), `la fila ${i + 1}: una sola pastilla, la de SoundCloud, elegida`)
      const f = await leer(i)
      igual({ notas: f.notas, versiones: f.versiones }, { notas: [], versiones: (await pastillasDe(page, i))[0].texto },
        `la fila ${i + 1} sin otras versiones no tiene que decir nada más que su pastilla`)
    }
    const pagina = await page.evaluate(() => document.querySelector('.results').innerText)
    afirmar(!/No lo encontr|solo en SoundCloud/i.test(pagina), `la Station dice "No lo encontré"/"solo en SoundCloud": ${pagina.match(/.{0,40}(No lo encontr|solo en SoundCloud).{0,40}/i)?.[0]}`)

    const f0 = await leer(0)
    igual(f0.bajoArtista, [sinMp3.motivo], 'el motivo de la caída no está bajo el artista')
    igual(veces(f0.texto, sinMp3.motivo), 1, 'el motivo de la fila 1 aparece más de una vez en la fila')
    igual(veces(pagina, 'No contestó a tiempo'), 1, '"No contestó a tiempo" aparece más de una vez en la Station')
    igual(await pastillasDe(page, 0), esperadasStation(sinMp3.opciones, sinMp3.opciones.findIndex((o) => o.calidad?.grade === 'B')),
      'la fila 1 sin los MP3: sus pastillas, con la de mejor nota elegida')
  }],

  ['solo MP3 (f38/f40): ningún texto de la Station, la barra, el comparador ni el historial nombra el sitio de un MP3; los nombres para lector numeran', async (page, ctx) => {
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

    // Station: las dos pastillas MP3 dicen "MP3" y el lector las distingue por su número.
    const ps = await pastillasDe(page, 0)
    igual(mp3K.map((j) => ps[j].texto.replace(/^\d+/, '').replace(/[A-F?][-+]?$/, '')), ['MP3', 'MP3'], 'las pastillas de los MP3 no dicen "MP3"')
    igual(mp3K.map((j) => ps[j].nombre.replace(/,.*$|\s\(.*$/, '')), mp3K.map((j) => `Opción ${j + 1}: MP3`), 'los nombres de los MP3 no llevan su número de opción')
    igual(new Set(ps.map((p) => p.nombre)).size, ps.length, 'hay dos pastillas con el mismo nombre para lector')
    igual(await rastrosSitio(page), [], 'la Station nombra el sitio de un MP3')

    // Barra: el otro MP3 sonando dice "MP3" y su opción.
    const otro = mp3K.find((j) => ps[j].elegida !== 'true')
    await elegir(page, 0, null, otro)
    await page.click('.trk:nth-child(2) .thumb-play')     // nth-child(1) es el encabezado
    await hasta(() => leerBarra(page), (d) => d && d.estado === 'playing', 'el MP3 no quedó sonando')
    igual(await page.evaluate(() => ({ fuente: document.querySelector('.deck-src')?.textContent, fila: document.querySelector('.trk .trk-now')?.textContent })),
      { fuente: `MP3 · ${otro + 1}/${r0.opciones.length}, opción ${otro + 1} de ${r0.opciones.length}`, fila: `Sonando: opción ${otro + 1} · MP3` },
      'la barra y la fila no dicen "MP3" y su opción')
    igual(await rastrosSitio(page), [], 'con un MP3 sonando, la barra o la fila nombran el sitio')

    // Comparador: una columna por versión, los MP3 como "MP3".
    await page.evaluate(() => document.querySelector('.trk button[aria-label^="Comparar versiones de"]').click())
    const cols = await hasta(() => page.$$eval('[role=dialog] .cmp-src', (e) => e.map((x) => x.textContent)), (v) => v.length === r0.opciones.length,
      'el comparador no mostró una columna por versión')
    // f40: la plataforma, y si se repite (dos MP3), con su número de opción, el mismo de la fila.
    const repetidas = (n) => r0.opciones.filter((o) => NOMBRE[o.fuente] === n).length > 1
    afirmar(repetidas('MP3'), 'el caso no prueba nada: la fila 1 tiene que traer dos MP3')
    igual(cols, r0.opciones.map((o, k) => repetidas(NOMBRE[o.fuente]) ? `${NOMBRE[o.fuente]} · opción ${k + 1}` : NOMBRE[o.fuente]),
      'el comparador no nombra cada versión como la fila (dos MP3 tienen que distinguirse)')
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

  ['búsqueda, modo lista y playlist guardada (f38/f40): pastillas y "Versiones", sin nada del plegado; los MP3 dicen "MP3" y numeran', async (page, ctx) => {
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
    const pastillas = () => page.evaluate((plegado) => ({
      station: !!document.querySelector('.results-station'),
      columna: [...document.querySelectorAll('.results-head > div')].map((d) => d.textContent)[5] ?? null,
      plegado: document.querySelectorAll(plegado).length,
      pastillas: [...document.querySelectorAll('.trk .vchip')].map((b) => ({ texto: b.textContent.trim(), nombre: b.getAttribute('aria-label') })),
    }), PLEGADO)
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

  ['station (f40): a 400 px las pastillas entran enteras en su tarjeta, nada se sale y se pueden elegir', async (page, ctx) => {
    const s = await montarStation(page, ctx, {})
    const { items } = s
    await abrirStation(page, ctx, s)
    await hasta(() => leerStation(page), (v) => v.filas.length === items.length, 'no llegaron todas las filas')
    const r0 = s.vx[items[0].video_id].respuesta
    afirmar(r0.opciones.length >= 5, 'el caso no prueba nada: la fila 1 tiene que traer 5 versiones (las que más ocupan)')
    await page.setViewport({ width: 400, height: 860 })
    await hasta(() => page.evaluate(() => document.documentElement.clientWidth), (w) => w <= 400, 'el viewport no pasó a 400 px')
    const d = await desbordeDe(page)
    afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `Station a 400 px: hay contenido fuera del ancho: ${json(d)}`)
    // Cada pastilla de las primeras filas: dentro de su fila, a la vista, con el texto entero.
    const geo = await page.evaluate(() => [...document.querySelectorAll('.trk')].slice(0, 3).flatMap((r, i) => {
      const rf = r.getBoundingClientRect()
      return [...r.querySelectorAll('.vchip')].map((b) => {
        const rb = b.getBoundingClientRect()
        return { fila: i, nombre: b.getAttribute('aria-label'), dentro: rb.left >= rf.left - 1 && rb.right <= rf.right + 1,
          visible: window.__visible(b), cortada: b.scrollWidth > b.clientWidth + 1 }
      })
    }))
    igual(geo.filter((p) => p.fila === 0).length, r0.opciones.length, 'a 400 px la fila 1 no muestra una pastilla por versión')
    igual(geo.filter((p) => !p.dentro || !p.visible || p.cortada), [], 'a 400 px hay pastillas fuera de su fila, ocultas o cortadas')
    // Tocar una pastilla a 400 px elige (la tarjeta no la tapa con otra cosa).
    const ytK = r0.opciones.findIndex((o) => o.fuente === 'youtube')
    const centro = await page.evaluate((j) => {
      const b = document.querySelectorAll('.trk')[0].querySelectorAll('.vchip')[j]
      b.scrollIntoView({ block: 'center' })
      const rc = b.getBoundingClientRect()
      return { x: rc.left + rc.width / 2, y: rc.top + rc.height / 2 }
    }, ytK)
    await page.mouse.click(centro.x, centro.y)
    await hasta(() => pastillasDe(page, 0), (ps) => ps[ytK].elegida === 'true', 'a 400 px tocar la pastilla de YouTube no la eligió')
  }],

  // f41: bajar desde la playlist. La descarga está doblada en el server (e2e/server_doblado.py:
  // ~0.8 s, archivo de mentira, Opus 160k; una url con "e2e-falla" tira el error de yt-dlp).
  // Lo esperado sale de la API (el item, su nota, su motivo), no del front.
  ['playlist: "Bajar" en una fila y "Bajar los N que faltan" con progreso (descarga doblada)', async (page, ctx) => {
    const nombre = 'E2E bajar desde la playlist'
    const crear = await apiPedir(ctx, '/api/playlists', 'POST', { nombre })
    afirmar(crear.status === 200 && crear.data.playlist, `no pude crear la playlist: ${json(crear)}`)
    const pid = crear.data.playlist.id
    const temas = [
      { titulo: 'Bajar Uno', artista: 'E2E', fuente: 'youtube', url: 'https://www.youtube.com/watch?v=e2e-uno', bpm: 128.4, camelot: '8A', duracion: 300 },
      { titulo: 'Bajar Falla', artista: 'E2E', fuente: 'youtube', url: 'https://www.youtube.com/watch?v=e2e-falla', duracion: 200 },
      { titulo: 'Bajar Tres', artista: 'E2E', fuente: 'soundcloud', url: 'https://soundcloud.com/e2e/tres', duracion: 250 },
      // Como lo agrega la home (un archivo de la biblioteca): sin fuente ni url.
      { titulo: 'Mi Edit Local', artista: 'E2E', bpm: 140.2, camelot: '5A' },
    ]
    try {
      for (const t of temas) {
        const r = await apiPedir(ctx, `/api/playlists/${pid}/items`, 'POST', { track: t })
        afirmar(r.status === 200 && r.data.id, `no pude agregar ${t.titulo}: ${json(r)}`)
      }
      const apiItems = async () => Object.fromEntries((await api(ctx, `/api/playlists/${pid}`)).data.items.map((i) => [i.titulo, i]))
      // El formato que el usuario eligió en el buscador (guardado en el navegador): MP3.
      await page.evaluateOnNewDocument(() => { try { window.localStorage.setItem('musiflix.formato', 'mp3') } catch { /* sin storage */ } })
      // Las carátulas de YouTube/SoundCloud no se piden a internet.
      await interceptar(page, (req) => {
        if (new URL(req.url()).origin === new URL(ctx.url).origin) return false
        req.abort().catch(() => {})
        return true
      })
      await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
      await hasta(() => page.evaluate((n) => [...document.querySelectorAll('.pl-item')].some((b) => b.querySelector('.pl-item-name')?.textContent === n), nombre),
        (v) => v, 'la playlist no aparece en el rail')
      await page.evaluate((n) => [...document.querySelectorAll('.pl-item')].find((b) => b.querySelector('.pl-item-name')?.textContent === n).click(), nombre)

      const leerFilas = () => page.evaluate(() => Object.fromEntries([...document.querySelectorAll('.results-crate .trk')].map((r) => [
        r.querySelector('.trk-title')?.textContent, {
          estado: r.querySelector('.trk-status')?.textContent.trim() ?? null,
          boton: r.querySelector('.trk-status button')?.getAttribute('aria-label') ?? null,
          motivo: r.querySelector('.trk-motivo')?.textContent ?? null,
          grade: r.querySelector('.trk-grade .grade')?.textContent ?? null,
        }])))
      const leerProgreso = () => page.evaluate(() => {
        const s = document.querySelector('.crate-dl [role="status"]')
        const b = document.querySelector('.crate-dl [role="progressbar"]')
        return { texto: s?.textContent ?? null, now: b?.getAttribute('aria-valuenow') ?? null, max: b?.getAttribute('aria-valuemax') ?? null }
      })
      const botonArriba = () => page.evaluate(() => [...document.querySelectorAll('.crate-actions button')].map((b) => b.textContent.trim()).find((t) => t.startsWith('Bajar')) ?? null)

      let filas = await hasta(leerFilas, (f) => f['Bajar Uno']?.boton, 'no apareció el botón Bajar de la fila')
      let items = await apiItems()
      igual(filas['Mi Edit Local'], { estado: 'Sin link', boton: null, motivo: items['Mi Edit Local'].motivo_no_bajable, grade: null },
        'el tema sin link no dice por qué no se puede bajar (o tiene botón)')
      afirmar(items['Mi Edit Local'].motivo_no_bajable, 'la API no dio motivo para el tema sin link')
      igual(filas['Bajar Uno'], { estado: 'Bajar', boton: 'Bajar «Bajar Uno»', motivo: null, grade: null }, 'la fila no ofrece "Bajar" con nombre')
      igual(await botonArriba(), 'Bajar los 3 que faltan', 'el botón de arriba no cuenta los que faltan y se pueden bajar')

      // Una fila, con teclado: el mismo botón queda "Bajando" con el foco y termina en "Descargado".
      await page.focus('button[aria-label="Bajar «Bajar Uno»"]')
      await page.keyboard.press('Enter')
      const foco = await hasta(() => page.evaluate(() => ({ nombre: document.activeElement?.getAttribute('aria-label') ?? null, ocupado: document.activeElement?.getAttribute('aria-disabled') ?? null })),
        (v) => v.nombre === 'Bajando «Bajar Uno»', 'el botón no pasó a "Bajando" o perdió el foco')
      igual(foco.ocupado, 'true', 'mientras baja, el botón tiene que estar aria-disabled')
      filas = await hasta(leerFilas, (f) => f['Bajar Uno'].estado === 'Descargado', 'la fila no pasó a Descargado')
      items = await apiItems()
      igual([items['Bajar Uno'].descargado, items['Bajar Uno'].formato], [true, 'mp3'], 'la API no tiene el tema bajado en el formato elegido en el buscador')
      igual(filas['Bajar Uno'].grade, items['Bajar Uno'].grade, 'la nota de la fila no es la de la API')
      igual(await hasta(botonArriba, (t) => t === 'Bajar los 2 que faltan'), 'Bajar los 2 que faltan', 'el contador no bajó después de bajar uno')

      // La tanda: de a uno, con progreso; el que falla queda con el motivo del server.
      // Con teclado (B5): antes el botón se desmontaba al arrancar y el foco caía a <body>.
      await page.evaluate(() => [...document.querySelectorAll('.crate-actions button')].find((b) => b.textContent.trim() === 'Bajar los 2 que faltan').focus())
      await page.keyboard.press('Enter')
      const focoTanda = () => page.evaluate(() => ({ texto: document.activeElement?.textContent.trim() ?? null, ocupado: document.activeElement?.getAttribute('aria-disabled') ?? null }))
      igual(await hasta(focoTanda, (v) => v.texto === 'Bajando los que faltan…'), { texto: 'Bajando los que faltan…', ocupado: 'true' },
        'al arrancar la tanda el botón perdió el foco o no quedó aria-disabled')
      const p1 = await hasta(leerProgreso, (v) => v.texto === 'Bajando 1 de 2 · Bajar Falla…', 'no apareció el progreso del primero')
      igual([p1.now, p1.max], ['0', '2'], 'la barra no arranca en 0 de 2')
      filas = await leerFilas()
      igual([filas['Bajar Falla'].boton, filas['Bajar Tres'].boton], ['Bajando «Bajar Falla»', 'En cola para bajar «Bajar Tres»'], 'las filas no dicen cuál baja y cuál espera')
      const p2 = await hasta(leerProgreso, (v) => v.texto === 'Bajando 2 de 2 · Bajar Tres…', 'el progreso no pasó al segundo')
      igual(p2.now, '1', 'la barra no cuenta el que ya terminó')
      const pf = await hasta(leerProgreso, (v) => v.texto && !v.texto.startsWith('Bajando'), 'la tanda no terminó')
      igual(pf, { texto: '1 de 2 bajados · 1 con error (el motivo está en cada tema)', now: '2', max: '2' }, 'el resumen no dice cuántos bajaron y cuántos fallaron')
      filas = await hasta(leerFilas, (f) => f['Bajar Tres'].estado === 'Descargado', 'Bajar Tres no pasó a Descargado')
      items = await apiItems()
      igual([items['Bajar Falla'].descargado, items['Bajar Tres'].descargado], [false, true], 'la API no coincide con la tanda')
      igual(filas['Bajar Falla'], { estado: 'Reintentar', boton: 'Reintentar bajar «Bajar Falla»', motivo: 'No se bajó: ERROR: [youtube] e2e-falla: Video unavailable', grade: null },
        'el que falló no muestra el motivo real o no se puede reintentar')
      igual(await botonArriba(), 'Bajar el que falta', 'el que falló tiene que seguir contando como "falta"')
      igual(await focoTanda(), { texto: 'Bajar el que falta', ocupado: null }, 'al terminar la tanda el foco no quedó en el botón')

      // Lo bajado entra en el .m3u8 (el que no se bajó y el sin link quedan afuera).
      const exp = await apiPedir(ctx, `/api/playlists/${pid}/export`, 'POST', {})
      igual([exp.data.incluidos, exp.data.excluidos], [2, 2], 'el .m3u8 no incluye lo bajado desde la playlist')

      // 400 px: botones y motivos entran sin cortarse.
      await page.setViewport({ width: 400, height: 860 })
      await hasta(() => page.evaluate(() => document.documentElement.clientWidth), (w) => w <= 400, 'el viewport no pasó a 400 px')
      const d = await desbordeDe(page)
      afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `playlist a 400 px: hay contenido fuera del ancho: ${json(d)}`)
    } finally {
      await apiPedir(ctx, `/api/playlists/${pid}`, 'DELETE')
    }
  }],

  // f41 ronda 2: la descarga de la barra sobre un tema de la playlist va AL ITEM (C7: antes iba
  // por /api/descargar y lo sumaba a la playlist activa, otra), y cuando la tanda termina y el
  // botón "Bajar el que falta" desaparece, el foco pasa al resumen y no cae a <body> (B5).
  ['playlist: la barra baja al item (no a la activa) y el foco sobrevive al final de la tanda', async (page, ctx) => {
    const previa = (await api(ctx, '/api/playlists/activa')).activa
    const crear = async (nombre) => {
      const r = await apiPedir(ctx, '/api/playlists', 'POST', { nombre })
      afirmar(r.status === 200 && r.data.playlist, `no pude crear ${nombre}: ${json(r)}`)
      return r.data.playlist.id
    }
    const nombre = 'E2E barra y foco'
    const pid = await crear(nombre)
    const activa = await crear('E2E otra activa')
    try {
      for (const t of [
        { titulo: 'Barra Uno', artista: 'E2E', fuente: 'youtube', url: 'https://www.youtube.com/watch?v=e2e-barra', duracion: 210 },
        { titulo: 'Foco Dos', artista: 'E2E', fuente: 'youtube', url: 'https://www.youtube.com/watch?v=e2e-foco', duracion: 220 },
      ]) {
        const r = await apiPedir(ctx, `/api/playlists/${pid}/items`, 'POST', { track: t })
        afirmar(r.status === 200 && r.data.id, `no pude agregar ${t.titulo}: ${json(r)}`)
      }
      const act = await apiPedir(ctx, `/api/playlists/${activa}`, 'PATCH', { activar: true })
      afirmar(act.status === 200, `no pude activar la otra playlist: ${json(act)}`)
      // Nada sale a internet: carátulas afuera, y el audio de la fuente (yt-dlp) contesta 503.
      await interceptar(page, (req) => {
        const u = new URL(req.url())
        if (u.origin !== new URL(ctx.url).origin) { req.abort().catch(() => {}); return true }
        if (u.pathname.startsWith('/api/fuente/')) { req.respond({ status: 503, contentType: 'application/json', body: '{"exito":false,"mensaje":"E2E: sin red"}' }).catch(() => {}); return true }
        return false
      })
      await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
      await hasta(() => page.evaluate((n) => [...document.querySelectorAll('.pl-item')].some((b) => b.querySelector('.pl-item-name')?.textContent === n), nombre),
        (v) => v, 'la playlist no aparece en el rail')
      await page.evaluate((n) => [...document.querySelectorAll('.pl-item')].find((b) => b.querySelector('.pl-item-name')?.textContent === n).click(), nombre)
      await hasta(() => page.$('button[aria-label="Reproducir Barra Uno"]'), (v) => v, 'no apareció el play de Barra Uno')
      // Click por DOM: el play de la carátula aparece con hover y puppeteer no siempre lo ve clickeable.
      await page.evaluate(() => document.querySelector('button[aria-label="Reproducir Barra Uno"]').click())

      // La barra: su botón de descarga baja ESE item.
      await hasta(() => page.$('.deck-acts button[aria-label="Descargar Barra Uno"]'), (v) => v, 'la barra no ofrece bajar el tema de la playlist')
      await page.evaluate(() => document.querySelector('.deck-acts button[aria-label="Descargar Barra Uno"]').click())
      const items = async (id) => Object.fromEntries((await api(ctx, `/api/playlists/${id}`)).data.items.map((i) => [i.titulo, i]))
      const it = await hasta(() => items(pid), (v) => v['Barra Uno'].descargado, 'la descarga de la barra no dejó bajado el item de la playlist')
      igual([it['Barra Uno'].descargado, it['Foco Dos'].descargado], [true, false], 'la barra bajó otro item')
      igual(Object.keys(await items(activa)), [], 'la descarga de la barra ensució la playlist activa (camino viejo /api/descargar)')
      await hasta(() => page.evaluate(() => [...document.querySelectorAll('.results-crate .trk')].find((r) => r.querySelector('.trk-title')?.textContent === 'Barra Uno')?.querySelector('.trk-status')?.textContent.trim()),
        (v) => v === 'Descargado', 'la fila de la playlist no pasó a Descargado')

      // La última tanda: el botón desaparece al terminar (no falta ninguno) y el foco va al resumen.
      const boton = await hasta(() => page.evaluate(() => [...document.querySelectorAll('.crate-actions button')].map((b) => b.textContent.trim()).find((t) => t.startsWith('Bajar')) ?? null),
        (v) => v === 'Bajar el que falta', 'el botón no cuenta el que falta')
      igual(boton, 'Bajar el que falta', 'el botón no cuenta el que falta')
      await page.evaluate(() => [...document.querySelectorAll('.crate-actions button')].find((b) => b.textContent.trim() === 'Bajar el que falta').focus())
      await page.keyboard.press('Enter')
      const foco = () => page.evaluate(() => ({ tag: document.activeElement?.tagName ?? null, rol: document.activeElement?.getAttribute('role') ?? null, texto: document.activeElement?.textContent.trim() ?? null }))
      await hasta(foco, (v) => v.texto === 'Bajando los que faltan…', 'al arrancar, el botón perdió el foco')
      const fin = await hasta(foco, (v) => v.rol === 'status' || v.tag === 'BODY', 'la tanda no terminó')
      igual(fin, { tag: 'DIV', rol: 'status', texto: 'Listo: 1 de 1 bajado' }, 'al terminar la tanda el foco no pasó al resumen')
    } finally {
      await apiPedir(ctx, `/api/playlists/${pid}`, 'DELETE')
      await apiPedir(ctx, `/api/playlists/${activa}`, 'DELETE')
      if (previa) await apiPedir(ctx, `/api/playlists/${previa.id}`, 'PATCH', { activar: true })
    }
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
// f43: `archivo` es la respuesta de /api/station a usar y `grupos`, lo que contesta /api/buscar
// (por defecto, una fila con el tema de SoundCloud); cada pedido a /api/station queda en
// `s.pedidosStation` (la URL entera).
async function montarStation(page, ctx, { retener = false, primeros = [], stationFalla = false, respuestas = {}, extra = null,
  archivo = 'station_respuesta.json', grupos = null } = {}) {
  const respuesta = leerJson(archivo)
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
    versiones: [], descargas: [], calidades: [], mp3: [], parecidas: 0, pendientes: new Map(), pedidosStation: [],
    analisis: {}, orden: null, pedidosAnalisis: [], retenerAnalisis: false, pendAnalisis: new Map(),
    // f46: con `s.retenerAnalisis = true` cada /api/station/analisis espera a que el test lo suelte.
    soltarAnalisis(ref) { const r = s.pendAnalisis.get(ref); afirmar(r, `no hay un análisis de ${ref} para soltar`); s.pendAnalisis.delete(ref); r() },
    soltarAnalisisTodos() { s.retenerAnalisis = false; for (const ref of [...s.pendAnalisis.keys()]) s.soltarAnalisis(ref) },
    soltar(id) { const r = s.pendientes.get(id); afirmar(r, `no hay un pedido de versiones de ${id} para soltar`); s.pendientes.delete(id); r() },
    soltarTodas() { s.retener = false; for (const id of [...s.pendientes.keys()]) s.soltar(id) },
  }
  await page.setRequestInterception(true)
  page.on('request', (req) => {
    const u = new URL(req.url())
    // Un pedido retenido que la página ya cortó (salir de la pantalla) no se puede contestar.
    const responder = (body, status = 200) => req.respond({ status, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {})
    if (u.pathname === '/api/buscar') return responder({ exito: true, grupos: grupos || [{ opciones: [tema] }] })
    if (u.pathname === '/api/calidad') { s.calidades.push(u.searchParams.get('url')); return responder({ ok: false, grade: '?' }) }
    if (u.pathname === '/api/meta') return responder({ bpm: null, genero: null })
    if (u.pathname === '/api/station') { s.pedidosStation.push(u.href); return responder(s.stationFalla ? s.falla : station) }
    if (u.pathname === '/api/parecidas_lista') { s.parecidas++; return responder({ exito: false }) }
    // f45: análisis de audio y orden "Para mezclar". Sin red: lo que el test ponga en
    // `s.analisis` / `s.orden`; si no, "no analizado" (nunca un BPM inventado).
    if (u.pathname === '/api/station/analisis') {
      const ref = u.searchParams.get('ref')
      s.pedidosAnalisis.push(ref)
      const contestar = () => responder(s.analisis[ref] || { ok: false, motivo: 'Sin análisis en el E2E.' })
      if (s.retenerAnalisis) { s.pendAnalisis.set(ref, contestar); return }
      return contestar()
    }
    if (u.pathname === '/api/station/orden') return responder(s.orden || { exito: false, mensaje: 'Sin orden en el E2E.' })
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

// f46: una de las dos barras del panel de estado ('versiones' o 'medicion'): lo que se ve
// (rótulo y valor) y lo que dice la progressbar (aria-value*). null si no está.
function leerAvance(id) {
  const a = document.querySelector(`.st-estado .st-avance[data-avance="${id}"]`)
  if (!a) return null
  const b = a.querySelector('[role="progressbar"]')
  const n = (k) => (b?.hasAttribute(k) ? Number(b.getAttribute(k)) : null)
  const rot = b && document.getElementById(b.getAttribute('aria-labelledby') || '')
  return { estado: a.dataset.estado, rotulo: rot?.textContent.replace(/\s+/g, ' ').trim() ?? null,
    valor: a.querySelector('.st-avance-val')?.textContent.trim() ?? null,
    min: n('aria-valuemin'), now: n('aria-valuenow'), max: n('aria-valuemax'), texto: b?.getAttribute('aria-valuetext') ?? null }
}

// Avance de las versiones ("N / 49" mientras carga, "X de 49 con versiones" al terminar; f46:
// la barra del panel) y el título de cada fila. `listo` = la barra de versiones terminó.
const leerStation = (page) => page.evaluate(`(() => {
  const v = (${leerAvance})('versiones')
  return { progreso: v?.valor ?? null, listo: v?.estado === 'listo',
    filas: [...document.querySelectorAll('.trk')].map((r) => r.querySelector('.trk-title')?.textContent ?? null) }
})()`)
const leerAvances = (page) => page.evaluate(`(() => { const f = ${leerAvance}; return { versiones: f('versiones'), medicion: f('medicion') } })()`)

// f46: el selector de orden del panel, su aclaración (por aria-describedby), la región viva y
// el encabezado (título, línea de abajo y cuántas veces dice "SoundCloud").
const leerOrden = (page) => page.evaluate(() => {
  const g = document.querySelector('.st-estado .station-orden [role="group"]')
  const bs = g ? [...g.querySelectorAll('button')] : []
  const desc = bs[1]?.getAttribute('aria-describedby') || null
  const anchos = bs.map((b) => b.getBoundingClientRect().width)
  const h = document.querySelector('.station-head')
  return { rotulo: g ? document.getElementById(g.getAttribute('aria-labelledby') || '')?.textContent ?? null : null,
    botones: bs.map((b) => b.textContent.trim()), pressed: bs.map((b) => b.getAttribute('aria-pressed')), disabled: bs.map((b) => b.disabled),
    candado: !!bs[1]?.querySelector('svg'), describedby: desc, aclaracion: desc ? document.getElementById(desc)?.textContent ?? null : null,
    mismoAncho: anchos.length === 2 && Math.abs(anchos[0] - anchos[1]) < 0.5,
    anuncio: document.querySelector('.st-estado [role="status"]')?.textContent ?? null,
    sub: h?.querySelector('.station-sub')?.textContent ?? null, soundcloud: (h?.textContent.match(/SoundCloud/g) || []).length }
})

// Contraste (WCAG) del color de texto de cada selector contra el primer fondo opaco hacia arriba.
const contrasteDe = (page, sels) => page.evaluate((qs) => {
  const rgb = (s) => (s.match(/[\d.]+/g) || []).map(Number)
  const lum = ([r, g, b]) => {
    const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4 }
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
  }
  const fondo = (e) => {
    for (let a = e; a; a = a.parentElement) { const v = rgb(getComputedStyle(a).backgroundColor); if (v.length === 3 || (v.length === 4 && v[3] > 0)) return v }
    return [0, 0, 0]
  }
  return qs.map((q) => {
    const e = document.querySelector(q)
    if (!e) return { q, ratio: null }
    const [a, b] = [lum(rgb(getComputedStyle(e).color)), lum(fondo(e))].sort((x, y) => y - x)
    return { q, ratio: Math.round(((a + 0.05) / (b + 0.05)) * 100) / 100 }
  })
}, sels)

// Título, artista y duración que muestra una fila (f40: los del tema de la Station).
const fichaDe = (page, i) => page.evaluate((k) => {
  const r = [...document.querySelectorAll('.trk')][k]
  return r && { titulo: r.querySelector('.trk-title')?.textContent ?? null, artista: r.querySelector('.trk-artist .truncate')?.textContent ?? null,
    duracion: r.querySelector('.trk-artist .mono')?.textContent ?? null }
}, i)

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

// La versión elegida de una fila y cómo elegir otra. Son lo ÚNICO de los casos de la Station que
// depende del markup de la columna VERSIONES; los casos verifican comportamiento (qué se elige,
// qué se descarga), no la disposición.
//
// f40: la Station usa las mismas pastillas en línea que la búsqueda (el plegado de f38 se sacó).
// Clases del plegado: no tiene que quedar ninguna en ninguna vista.
const PLEGADO = '.vpick, .vbest, .vmore, .vsolo, .trk-versions, .vline, .vlist'

// Las pastillas de la fila `i`, como se ven y como las lee un lector de pantalla.
const pastillasDe = (page, i) => page.evaluate((k) => {
  const r = [...document.querySelectorAll('.trk')][k]
  return r ? [...r.querySelectorAll('.trk-vers .vchip')].map((b) => ({
    texto: b.textContent.trim(), nombre: b.getAttribute('aria-label'),
    elegida: b.getAttribute('aria-pressed'), suena: b.getAttribute('aria-current'),
    tilde: !!b.querySelector('.vchip-ok'),                      // forma, no color
    barras: !!b.querySelector('.eq'), animadas: !!b.querySelector('.eq:not(.is-quieto)'),
    sinVerificar: b.classList.contains('is-sin-verificar'),
  })) : null
}, i)

// Lo esperado de las pastillas de una fila de la Station sin nada sonando, a partir de la
// respuesta de /api/versiones: la elegida con ✓ (sin número), las demás con su número; la
// plataforma ("MP3" para los dos sitios de MP3); la nota que trajo la respuesta ("?" si no se
// pudo medir, nada si no hay nota, "30 s" si es un preview Go+).
function esperadasStation(opciones, elegidaK) {
  return opciones.map((o, k) => {
    const nota = o.calidad ? (o.calidad.ok ? o.calidad.grade : '?') : null
    const notaTexto = o.solo_preview ? 'solo 30 s (Go+)' : nota ? `nota ${nota}` : null
    // f40-r2: el Extended dice su duración (m:ss con `mmss` de acá, no con el fmtDur del front)
    // y una versión con la duración sin verificar lo dice y va atenuada.
    const sinVerificar = o.duracion_verificada === false
    const ext = o.edicion === 'extended' ? (sinVerificar ? 'Extended' : `Extended · ${mmss(o.duracion)}`) : null
    const extras = [ext, sinVerificar ? 'duración sin verificar' : null].filter(Boolean).join(', ')
    return {
      texto: `${k === elegidaK ? '' : k + 1}${NOMBRE[o.fuente]}${ext ? ext.replace(' · ', ' ') : ''}${o.solo_preview ? '30 s' : nota || ''}`,
      nombre: `Opción ${k + 1}: ${NOMBRE[o.fuente]}${notaTexto ? `, ${notaTexto}` : ''}${extras ? `, ${extras}` : ''}${k === elegidaK ? ' (elegida)' : ''}`,
      elegida: String(k === elegidaK), suena: null, tilde: k === elegidaK, barras: false, animadas: false, sinVerificar,
    }
  })
}

// "Opción N: plataforma, nota X" de la elegida (su nombre para lector sin el estado). Si no hay
// exactamente una pastilla con aria-pressed y ✓, y es la misma, lo dice en vez de elegir una.
async function elegidaDe(page, i) {
  const ps = await pastillasDe(page, i)
  if (!ps) return null
  const pulsadas = ps.filter((p) => p.elegida === 'true')
  const tildes = ps.filter((p) => p.tilde)
  if (pulsadas.length !== 1 || tildes.length !== 1 || pulsadas[0] !== tildes[0]) return `la fila ${i + 1} no tiene una sola elegida con su ✓: ${json(ps)}`
  return pulsadas[0].nombre.replace(/ \(.*\)$/, '')
}

// Toca la pastilla de `plataforma` (tiene que haber una sola) o, con `k`, la opción k (0-based).
async function elegir(page, i, plataforma, k = null) {
  const ok = await page.evaluate((f, p, j) => {
    const bs = [...([...document.querySelectorAll('.trk')][f]?.querySelectorAll('.trk-vers .vchip') || [])]
    const cual = j != null ? [bs[j]].filter(Boolean)
      : bs.filter((b) => new RegExp(`^Opción \\d+: ${p}(,| \\(|$)`).test(b.getAttribute('aria-label')))
    if (cual.length === 1) cual[0].click()
    return cual.length
  }, i, plataforma, k)
  igual(ok, 1, `la fila ${i + 1} no tiene una (y solo una) pastilla de ${plataforma ?? `la opción ${k + 1}`}`)
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
