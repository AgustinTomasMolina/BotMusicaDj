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

// Un cajón (.drawer) entra deslizándose (transition de transform, nocturne.css): `.is-open`
// aparece ANTES de que termine, con sus botones todavía fuera de pantalla, y un clic de
// puppeteer ahí falla con "Node is either not clickable". Espera de estado: sin transform y
// sin animaciones en curso.
async function esperarCajon(page, sel) {
  await page.waitForSelector(sel, { timeout: ESPERA_MS })
  await hasta(() => page.evaluate((q) => {
    const d = document.querySelector(q)?.closest('.drawer')
    if (!d) return 'sin cajón'
    const t = getComputedStyle(d).transform
    const anim = d.getAnimations().filter((a) => a.playState === 'running').length
    return (t === 'none' || t === 'matrix(1, 0, 0, 1, 0, 0)') && anim === 0 ? 'quieto' : `moviéndose (${t}, ${anim} animaciones)`
  }, sel), (v) => v === 'quieto', `el cajón de ${sel} no terminó de abrirse`)
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
  // f55: sin playlist elegida, la radio pide una: la de juguete.
  await elegirPlaylistRadio(page, ctx.base.radio_playlist_nombre)
  await page.waitForSelector('.rsem', { timeout: ESPERA_MS })
}

async function elegirPlaylistRadio(page, nombre) {
  await page.waitForSelector('.rpl-item', { timeout: ESPERA_MS })
  const hay = await page.evaluate((n) => {
    const b = [...document.querySelectorAll('.rpl-item')].find((x) => x.querySelector('.rpl-nombre')?.textContent === n)
    b?.click()
    return !!b
  }, nombre)
  afirmar(hay, `la radio no ofrece la playlist «${nombre}»`)
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

// f55: la radio arma desde la playlist de juguete (base_juguete.py). `lib.tracks` son las
// semillas que da /api/radio/playlists/{id} y `lib.total` los tracks de la base del motor.
async function semillaUno(ctx) {
  const rp = await api(ctx, `/api/radio/playlists/${ctx.base.radio_playlist}`)
  const lib = { ...rp, tracks: rp.semillas, total: rp.motor_total }
  const uno = lib.tracks.find((t) => t.titulo === 'Uno')
  afirmar(uno, '/api/radio/playlists/{id} no trae la semilla «Uno» de la playlist de juguete')
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
  const set = await api(ctx, `/api/radio/set?playlist=${ctx.base.radio_playlist}&track=${encodeURIComponent(uno.id)}`)
  const r = await apiPedir(ctx, '/api/radio/sets', 'POST', {
    playlist: ctx.base.radio_playlist, track: uno.id, ...set.config, esperado: set.pasos.map((p) => p.track.id), huella: set.huella, ...(nombre ? { nombre } : {}),
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
        const hoy = await fetch(`${ctx.url}/api/radio/set.m3u8?playlist=${ctx.base.radio_playlist}&track=${encodeURIComponent(s.pasos[0].track.id)}&largo=${s.config.largo}`)
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

// Radio → set de «Uno» → la fila del tema `titulo`, y espera a que la pestaña «Cues y loops»
// (abierta por defecto) haya leído sus marcas. f61: ahí hay una lista de solo lectura y el
// botón que abre el editor D; el editor chico de f48 ya no está en esta pantalla.
async function abrirDetalle(page, ctx, titulo = 'Uno', { navegar = true } = {}) {
  await abrirRadio(page, ctx, { navegar })
  await elegirSemilla(page, 'Uno')
  await armarSet(page)
  const n = await page.evaluate((t) => [...document.querySelectorAll('.rpaso')].find((r) => r.querySelector('.rpaso-titulo')?.textContent === t)?.dataset.n ?? null, titulo)
  afirmar(n, `el set de «Uno» no tiene a «${titulo}»`)
  await elegirFila(page, Number(n))
  await hasta(() => page.evaluate(() => ({
    titulo: document.querySelector('#r-det-h')?.textContent ?? null,
    pestana: document.querySelector('[role=tab][aria-selected=true]')?.id ?? null,
    leida: Boolean(document.querySelector('#r-panel-cues:not([hidden]) .rdet-abrir'))
      && !/Leyendo las marcas/.test(document.querySelector('#r-panel-cues')?.textContent ?? ''),
  })), (v) => v.titulo === titulo && v.pestana === 'r-tab-cues' && v.leida,
  `el detalle no terminó de abrir «${titulo}» en la pestaña «Cues y loops»`)
}

// Abre el editor D desde un botón del detalle y espera la onda (el mismo editor de Mis
// playlists: diálogo .edd con el título del track).
async function abrirEditorDDesde(page, boton, titulo) {
  await page.click(boton)
  await page.waitForSelector('.edd[role="dialog"]', { timeout: ESPERA_MS })
  await hasta(() => page.evaluate(() => ({
    titulo: document.querySelector('#edd-titulo')?.textContent ?? '',
    msg: document.querySelector('.edd-msg')?.textContent ?? null,
    estado: document.querySelector('.edd-grilla-estado')?.textContent ?? null,
  })), (v) => v.titulo.startsWith(titulo) && !v.msg && v.estado && !v.estado.includes('Leyendo'),
  `el editor D no abrió «${titulo}» con su onda`, 20000)
}
async function cerrarEditorD(page) {
  await page.focus('.edd-onda')
  await page.keyboard.press('Escape')
  await hasta(() => page.$('.edd').then((e) => e === null), (v) => v, 'Escape no cerró el editor D')
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

// Lo que muestra el control de volumen de la barra y a cuánto suena cada audio (barra y editor D).
const leerVolumenes = (page) => page.evaluate(() => {
  const barra = document.querySelector('.deck-range-vol')
  const audioBarra = [...window.__medios].find((m) => /\/api\/audio\//.test(m.currentSrc || m.src))
  const audioEditor = document.querySelector('.edd audio')
  return {
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

  ['radio (f50/f61): pestañas con flechas; «Onda avanzada» abre el editor D del track; «Información» = la API', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    await abrirDetalle(page, ctx)
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
      ['r-tab-cues', 'true', null, 'r-panel-cues', 0], ['r-tab-info', 'false', null, 'r-panel-info', -1], ['r-tab-onda', 'false', null, 'r-panel-onda', -1],
    ], 'las pestañas al abrir: «Cues y loops» elegida, una sola parada de Tab, las tres habilitadas')
    igual(t0.tabs.map((t) => t.texto), ['Cues y loops', 'Información', 'Onda avanzada'], 'los nombres de las pestañas (sin «próximamente»)')
    igual(t0.paneles, [{ id: 'r-panel-cues', por: 'r-tab-cues', visible: true }, { id: 'r-panel-info', por: 'r-tab-info', visible: false }, { id: 'r-panel-onda', por: 'r-tab-onda', visible: false }], 'los paneles al abrir')
    const elegida = (t) => t.tabs.find((x) => x.sel === 'true')?.id
    await page.focus('#r-tab-cues')
    await page.keyboard.press('ArrowRight')
    let t = await hasta(tabs, (v) => elegida(v) === 'r-tab-info' && v.foco === 'r-tab-info', 'Flecha derecha no pasó a «Información»')
    igual(t.paneles.map((p) => p.visible), [false, true, false], 'con «Información» elegida se ve su panel y no los otros')
    await page.keyboard.press('ArrowRight')
    await hasta(tabs, (v) => elegida(v) === 'r-tab-onda' && v.foco === 'r-tab-onda', 'Flecha derecha desde «Información» no pasó a «Onda avanzada»')
    await page.keyboard.press('ArrowRight')
    await hasta(tabs, (v) => elegida(v) === 'r-tab-cues' && v.foco === 'r-tab-cues', 'Flecha derecha desde la última no dio la vuelta a la primera')
    await page.keyboard.press('ArrowLeft')
    await hasta(tabs, (v) => elegida(v) === 'r-tab-onda' && v.foco === 'r-tab-onda', 'Flecha izquierda desde la primera no dio la vuelta a «Onda avanzada»')
    await page.keyboard.press('Home')
    await hasta(tabs, (v) => elegida(v) === 'r-tab-cues', 'Inicio no fue a la primera')
    await page.keyboard.press('End')
    await hasta(tabs, (v) => elegida(v) === 'r-tab-onda', 'Fin no fue a la última')
    // «Onda avanzada» abre el editor D de ESTE track (el mismo de Mis playlists).
    t = await tabs()
    igual(t.paneles.map((p) => p.visible), [false, false, true], 'con «Onda avanzada» elegida se ve su panel')
    await abrirEditorDDesde(page, '#r-panel-onda .rdet-abrir-onda', 'Uno')
    igual(await page.$eval('.edd', (d) => [d.getAttribute('aria-modal'), d.querySelectorAll('.edd-sel option').length]), ['true', 1],
      'el editor D desde el set: modal y con UN tema (el elegido)')
    await cerrarEditorD(page)
    await page.click('#r-tab-info')
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
    const set = await api(ctx, `/api/radio/set?playlist=${ctx.base.radio_playlist}&track=${encodeURIComponent(uno.id)}`)
    afirmar(set.pasos.length >= 3, 'el set de juguete tiene menos de 3 pasos')
    for (const p of set.pasos) await limpiarMarcas(ctx, p.track.id)
    const segundo = set.pasos[1].track
    await apiPedir(ctx, rutaMarcas(uno.id), 'POST', { tipo: 'cue', inicio: 1, nombre: 'Drop' })
    await apiPedir(ctx, rutaMarcas(uno.id), 'POST', { tipo: 'loop', inicio: 4, fin: 8, num: 2 })
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
      return [...document.querySelectorAll('.rpaso .c-cues .rcues')].map((e) => ({ texto: e.textContent, gris: getComputedStyle(e).color === gris }))
    })
    igual(colores.map((c) => c.gris), esperado.map((e) => e === 'sin cues'), '«sin cues» en gris y los conteos no')
    await page.evaluate(() => fetch('/api/radio/biblioteca').then((r) => r.text()))
    igual(conteos.length, 1, `un set pidió ${conteos.length} veces /api/radio/marcas/conteo`)
    const ids = new URL(conteos[0]).searchParams.get('ids').split(',')
    igual(ids, set.pasos.map((p) => p.track.id), 'el pedido de conteos no lleva los ids del set, en orden')
    // «Cues y loops» lista lo que tiene la base (solo lectura), en orden de tiempo.
    // f51: el hot loop se lee con su pad (L3) y en naranja; el hot cue en el rojo del pad 1;
    // el tiempo en m:ss.d (el loop con «→ fin»), el nombre si tiene, y el tipo para el lector.
    const listaRO = () => page.$$eval('#r-panel-cues .rdet-marca', (li) => li.map((x) => {
      const pad = x.querySelector('.rdet-marca-pad')
      return { pad: pad.textContent, color: getComputedStyle(pad).backgroundColor, t: x.querySelector('.mono').textContent,
        nombre: x.querySelector('.rdet-marca-n')?.textContent ?? null, lector: x.querySelector('.sr-only')?.textContent ?? null }
    }))
    const ro = await hasta(listaRO, (v) => v.length === 2, 'la lista de «Cues y loops» no tiene las dos marcas de la API')
    igual(ro.map(({ lector, ...x }) => x), [  // eslint-disable-line no-unused-vars
      { pad: '1', color: 'rgb(255, 77, 90)', t: mmssd(1), nombre: 'Drop' },
      { pad: 'L3', color: 'rgb(255, 154, 46)', t: `${mmssd(4)} → ${mmssd(8)}`, nombre: null },
    ], 'la lista de «Cues y loops» no dice lo de la API (pad, color de la tabla del motor, tiempos y nombre)')
    afirmar(/hot cue 1/.test(ro[0].lector) && /loop del pad 3/.test(ro[1].lector), `el lector no anuncia el tipo de cada marca: ${json(ro.map((x) => x.lector))}`)
    // Poner un cue en el editor D actualiza la columna y la lista al cerrar, sin volver a pedir los conteos.
    await abrirEditorDDesde(page, '#r-panel-cues .rdet-abrir', 'Uno')
    await page.focus('.edd-onda')
    await teclaQueGuarda(page, '2')
    await cerrarEditorD(page)
    const n = (await marcasApi(ctx, uno.id)).length
    igual(n, 3, 'el pad 2 del editor D no quedó en la base')
    await hasta(() => page.$eval('.rpaso[data-n="1"] .c-cues .rcues', (e) => e.textContent), (v) => v === cuesTexto(n), 'la columna no se actualizó después de poner un cue')
    await hasta(() => page.$$eval('#r-panel-cues .rdet-marca', (li) => li.length), (v) => v === n, 'la lista de «Cues y loops» no se releyó al cerrar el editor')
    await page.evaluate(() => fetch('/api/radio/biblioteca').then((r) => r.text()))
    igual(conteos.length, 1, 'poner un cue volvió a pedir todos los conteos')
    for (const p of set.pasos) await limpiarMarcas(ctx, p.track.id)
  }],

  ['radio (f61): un cue que se guarda DESPUÉS de cerrar el editor D aparece en la columna «Cues» y en la lista sin volver a elegir la fila', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    await limpiarMarcas(ctx, uno.id)
    let demorar = false
    let demorarGet = false
    await interceptar(page, async (req) => {
      const ruta = new URL(req.url()).pathname === rutaMarcas(uno.id)
      // La relectura del cierre sale antes que el POST vuelva y llega DESPUÉS (con la lista
      // vieja): la lectura que dispara el aviso tardío tiene que reemplazarla.
      if (demorarGet && ruta && req.method() === 'GET') {
        demorarGet = false
        // La foto de la base de ESTE momento (antes del POST), entregada 3 s después.
        const foto = await apiPedir(ctx, rutaMarcas(uno.id))
        await new Promise((res) => setTimeout(res, 3000))
        await req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(foto.data) })
        return true
      }
      if (!demorar || req.method() !== 'POST' || !ruta) return false
      demorar = false
      // Más tarde que la relectura que hace el detalle al cerrar.
      await new Promise((res) => setTimeout(res, 1500))
      await req.continue()
      return true
    })
    try {
      await abrirDetalle(page, ctx)
      await abrirEditorDDesde(page, '#r-panel-cues .rdet-abrir', 'Uno')
      await page.focus('.edd-onda')
      demorar = true
      const post = page.waitForResponse((x) => x.request().method() === 'POST' && new URL(x.url()).pathname === rutaMarcas(uno.id), { timeout: ESPERA_MS })
      await page.keyboard.press('1')
      demorarGet = true
      await page.keyboard.press('Escape')
      await hasta(() => page.$('.edd'), (v) => !v, 'Esc tenía que cerrar (la escritura todavía no volvió)')
      igual((await post).status(), 201, 'el POST demorado tenía que guardarse')
      // Que la relectura vieja (demorada) ya haya llegado antes de mirar el final.
      await new Promise((res) => setTimeout(res, 3500))
      const n = (await marcasApi(ctx, uno.id)).length
      igual(n, 1, 'el cue no quedó en la base')
      const fila = await page.$eval('.rpaso.is-sel', (e) => e.dataset.n)
      await hasta(() => page.evaluate((k) => ({
        col: document.querySelector(`.rpaso[data-n="${k}"] .c-cues .rcues`)?.textContent ?? null,
        lista: document.querySelectorAll('#r-panel-cues .rdet-marca').length,
      }), fila), (v) => v.col === cuesTexto(n) && v.lista === n,
      'lo que se guardó después de cerrar no llegó a la columna «Cues» ni a la lista')
    } finally {
      await limpiarMarcas(ctx, uno.id)
    }
  }],

  ['radio (f61): un cambio de tipo y su Ctrl+Z que terminan DESPUÉS de cerrar el editor D dejan en el set las marcas de la base', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    await limpiarMarcas(ctx, uno.id)
    let demorar = false
    let demorarDelete = false
    await interceptar(page, async (req) => {
      // El DELETE directo de después de cerrar vuelve más tarde que la relectura que dispara el POST.
      if (demorarDelete && req.method() === 'DELETE') { demorarDelete = false; await new Promise((res) => setTimeout(res, 1200)); await req.continue(); return true }
      if (!demorar || req.method() !== 'POST' || new URL(req.url()).pathname !== rutaMarcas(uno.id)) return false
      demorar = false
      await new Promise((res) => setTimeout(res, 1500))   // más tarde que la relectura del cierre
      await req.continue()
      return true
    })
    // Lo que muestra el set de ESE track: la columna «Cues» y los rótulos de la lista.
    const delSet = () => page.evaluate(() => ({
      col: document.querySelector('.rpaso.is-sel .c-cues .rcues')?.textContent ?? null,
      lista: [...document.querySelectorAll('#r-panel-cues .rdet-marca-pad')].map((e) => e.textContent),
    }))
    const comoLaBase = async (que) => {
      const api_ = await marcasApi(ctx, uno.id)
      const esperado = { col: cuesTexto(api_.length), lista: api_.sort((a, b) => a.inicio - b.inicio).map((m) => (m.tipo === 'cue' ? String(m.num + 1) : m.tipo === 'memory' ? 'M' : 'L')) }
      await hasta(delSet, (v) => json(v) === json(esperado), `${que}: el set no muestra lo que tiene la base (${json(esperado)})`)
      return api_
    }
    const cerrar = async () => {
      await page.keyboard.press('Escape')
      await hasta(() => page.$('.edd'), (v) => !v, 'Esc tenía que cerrar (la escritura todavía no volvió)')
    }
    try {
      await abrirDetalle(page, ctx)
      // 1) memory → hot cue, con el POST demorado y Esc enseguida: el DELETE de la vieja sale
      //    después de cerrar (directo, fuera del historial).
      await abrirEditorDDesde(page, '#r-panel-cues .rdet-abrir', 'Uno')
      await page.focus('.edd-onda')
      const mem = (await teclaQueGuarda(page, 'm')).data.marca
      await page.waitForSelector(`.edd-fila[data-id="${mem.id}"] .edd-editar`, { timeout: ESPERA_MS })
      await page.click(`.edd-fila[data-id="${mem.id}"] .edd-editar`)
      await page.waitForSelector(`.edd-fila[data-id="${mem.id}"] select.edd-tipo`, { timeout: ESPERA_MS })
      demorar = true
      demorarDelete = true
      const borra = page.waitForResponse((x) => x.request().method() === 'DELETE' && new URL(x.url()).pathname.startsWith(rutaMarcas(uno.id)), { timeout: ESPERA_MS })
      await page.select(`.edd-fila[data-id="${mem.id}"] select.edd-tipo`, 'cue')
      await page.focus('.edd-onda')
      await cerrar()
      igual((await borra).status(), 200, 'el borrado de la memory vieja tenía que salir y guardarse')
      const tras1 = await comoLaBase('cambio de tipo terminado después de cerrar')
      igual(tras1.map((m) => m.tipo), ['cue'], 'en la base tiene que quedar solo el hot cue')
      // 2) Ctrl+Z de un cambio de tipo con el POST demorado y Esc apenas sale: lo que falta del
      //    grupo (borrar el cue) se termina en el tema de origen, ya cerrado.
      await abrirEditorDDesde(page, '#r-panel-cues .rdet-abrir', 'Uno')
      await page.focus('.edd-onda')
      const m2 = (await teclaQueGuarda(page, 'm')).data.marca
      await page.click(`.edd-fila[data-id="${m2.id}"] .edd-editar`)
      await page.waitForSelector(`.edd-fila[data-id="${m2.id}"] select.edd-tipo`, { timeout: ESPERA_MS })
      const delete2 = page.waitForResponse((x) => x.request().method() === 'DELETE', { timeout: ESPERA_MS })
      await page.select(`.edd-fila[data-id="${m2.id}"] select.edd-tipo`, 'cue')
      await delete2
      await hasta(async () => (await marcasApi(ctx, uno.id)).map((m) => m.tipo).sort(), (v) => json(v) === json(['cue', 'cue']), 'el cambio de tipo no terminó')
      await page.focus('.edd-onda')
      demorar = true
      const sale = page.waitForRequest((q) => q.method() === 'POST' && new URL(q.url()).pathname === rutaMarcas(uno.id), { timeout: ESPERA_MS })
      const borra2 = page.waitForResponse((x) => x.request().method() === 'DELETE', { timeout: ESPERA_MS })
      await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control')
      await sale
      await cerrar()
      igual((await borra2).status(), 200, 'el resto del Ctrl+Z (borrar el cue) tenía que guardarse después de cerrar')
      const tras2 = await comoLaBase('Ctrl+Z terminado después de cerrar')
      igual(tras2.map((m) => m.tipo).sort(), ['cue', 'memory'], 'el Ctrl+Z tenía que dejar la memory en lugar del segundo cue')
    } finally {
      await limpiarMarcas(ctx, uno.id)
    }
  }],

  ['volumen (f50/f61): 45 % por defecto en la barra y en el <audio> del editor D abierto desde el set; un solo volumen que se acuerda al recargar', async (page, ctx) => {
    await abrirHome(page, ctx)
    // El perfil de Chrome es el mismo para toda la corrida: se arranca sin nada guardado.
    await page.evaluate(() => localStorage.removeItem('musiflix.volumen'))
    await page.reload({ waitUntil: 'domcontentloaded' })
    await page.waitForSelector('.lib-card', { timeout: ESPERA_MS })
    try {
      await playEnHome(page, 'Uno')
      await abrirDetalle(page, ctx, 'Uno', { navegar: false })
      await abrirEditorDDesde(page, '#r-panel-cues .rdet-abrir', 'Uno')
      let v = await hasta(() => leerVolumenes(page), (x) => x.audioEditor && x.audioBarra !== null, 'no están los dos audios')
      igual([v.barra, v.audioEditor, v.audioBarra, v.guardado], ['45', { volumen: 0.45, mudo: false }, 0.45, null],
        'sin nada guardado el volumen tiene que ser 45 % en la barra y en los dos audios')
      await cerrarEditorD(page)
      // Lo guardado manda: al recargar, el editor D suena a ese volumen.
      await page.evaluate(() => localStorage.setItem('musiflix.volumen', '0.3'))
      await page.reload({ waitUntil: 'domcontentloaded' })
      await abrirDetalle(page, ctx, 'Uno', { navegar: false })
      await abrirEditorDDesde(page, '#r-panel-cues .rdet-abrir', 'Uno')
      v = await hasta(() => leerVolumenes(page), (x) => x.audioEditor, 'después de recargar no está el editor D')
      igual([v.audioEditor.volumen, v.guardado], [0.3, '0.3'], 'después de recargar el editor D no suena al volumen guardado')
    } finally {
      await page.evaluate(() => localStorage.removeItem('musiflix.volumen')).catch(() => {})
    }
  }],

  ['radio (f50): las minionditas se piden solo para las filas a la vista, de a dos, y el mismo tema una vez', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    const set = await api(ctx, `/api/radio/set?playlist=${ctx.base.radio_playlist}&track=${encodeURIComponent(uno.id)}`)
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

  ['radio (f50/f61): elegir una fila con el mouse o Enter lleva el foco a «Editar cues y loops» y Enter abre el editor D de ESE track; con ↑ ↓ el foco se queda en la lista', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    const set = await api(ctx, `/api/radio/set?playlist=${ctx.base.radio_playlist}&track=${encodeURIComponent(uno.id)}`)
    afirmar(set.pasos.length >= 3, 'el set de juguete tiene menos de 3 pasos')
    const [, p2, p3] = set.pasos
    await limpiarMarcas(ctx, p2.track.id)
    try {
      await abrirRadio(page, ctx)
      await elegirSemilla(page, 'Uno')
      await armarSet(page)
      const foco = () => page.evaluate(() => ({
        boton: document.activeElement?.classList.contains('rdet-abrir') ?? false,
        fila: document.activeElement?.classList.contains('rpaso-sel') ? document.activeElement.dataset.n : null,
        titulo: document.querySelector('#r-det-h')?.textContent ?? null,
      }))
      // Con el mouse: el foco termina en el botón del editor del track elegido.
      await page.click(`.rpaso-sel[data-n="${p2.n}"]`)
      await hasta(foco, (v) => v.boton && v.titulo === p2.track.titulo, 'elegí una fila con el mouse y el foco no pasó a «Editar cues y loops»')
      // …Enter abre el editor D de ESE track y un pad queda en ESE track.
      await page.keyboard.press('Enter')
      await hasta(() => page.$eval('#edd-titulo', (h) => h.textContent).catch(() => ''), (v) => v.startsWith(p2.track.titulo), 'Enter no abrió el editor D del track elegido')
      await hasta(() => page.evaluate(() => ({ msg: document.querySelector('.edd-msg')?.textContent ?? null, e: document.querySelector('.edd-grilla-estado')?.textContent ?? '' })),
        (v) => !v.msg && v.e && !v.e.includes('Leyendo'), 'la onda del editor D no cargó', 20000)
      await page.focus('.edd-onda')
      const cue = await teclaQueGuarda(page, '1')
      igual([cue.status, cue.data.marca.tipo], [201, 'cue'], 'el pad 1 en el editor D abierto desde el set')
      igual((await marcasApi(ctx, p2.track.id)).length, 1, 'el cue no quedó en el track elegido')
      await cerrarEditorD(page)
      // Con Enter sobre una fila: lo mismo.
      await page.focus(`.rpaso-sel[data-n="${p3.n}"]`)
      await page.keyboard.press('Enter')
      await hasta(foco, (v) => v.boton && v.titulo === p3.track.titulo, 'Enter sobre una fila no llevó el foco a «Editar cues y loops»')
      // Con ↑ ↓ el foco se queda en la lista (para seguir recorriéndola).
      await page.focus(`.rpaso-sel[data-n="${p3.n}"]`)
      await page.keyboard.press('ArrowUp')
      let v = await hasta(foco, (x) => x.fila === String(p2.n) && x.titulo === p2.track.titulo, 'Flecha arriba no pasó a la fila anterior')
      afirmar(!v.boton, 'con Flecha arriba el foco se fue al editor')
      await page.keyboard.press('ArrowDown')
      v = await hasta(foco, (x) => x.fila === String(p3.n), 'Flecha abajo no volvió a la fila siguiente')
      afirmar(!v.boton, 'con Flecha abajo el foco se fue al editor')
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
    await abrirDetalle(page, ctx)
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

  ['400 px (f50/f61): el detalle va debajo de la lista; las tres pestañas sin desborde', async (page, ctx) => {
    await page.setViewport({ width: 400, height: 860 })
    await abrirDetalle(page, ctx)
    await hasta(() => page.evaluate(() => document.documentElement.clientWidth), (w) => w <= 400, 'el viewport no pasó a 400 px')
    const g = await geoMD(page)
    afirmar(g.det.arriba >= g.lista.abajo, `a 400 px el detalle tiene que ir debajo de la lista: ${json(g)}`)
    let d = await desbordeDe(page)
    afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `400 px con «Cues y loops»: ${json(d)}`)
    await page.click('#r-tab-onda')
    d = await desbordeDe(page)
    afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `400 px con «Onda avanzada»: ${json(d)}`)
    await page.click('#r-tab-info')
    await page.waitForSelector('#r-panel-info .rinfo-dl', { timeout: ESPERA_MS })
    d = await desbordeDe(page)
    afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `400 px con «Información»: ${json(d)}`)
  }],
]

/* ---------- casos ---------- */

/* ---------- f55: la radio desde una playlist ---------- */

// Cómo se dice cada motivo contado y su etiqueta en la lista de afuera: escrito acá (es el
// contrato de la pantalla), no importado del front.
const MOTIVO_CONTADO = {
  otro_genero: (n) => `${n} de otro género`, sin_genero: (n) => `${n} sin género`,
  sin_archivo: (n) => `${n} sin archivo`, analizando: (n) => `${n} todavía analizando`,
  sin_analizar: (n) => `${n} sin analizar`, fallo_analisis: (n) => `${n} con el análisis fallado`,
  no_es_track: (n) => `${n} ${n === 1 ? 'no es un track' : 'no son tracks'} (menos de 90 s)`,
  duplicado: (n) => `${n} ${n === 1 ? 'repetido' : 'repetidos'}`,
}
const MOTIVO_ETIQUETA = {
  otro_genero: 'otro género', sin_genero: 'sin género', sin_archivo: 'sin archivo', analizando: 'analizando',
  sin_analizar: 'sin analizar', fallo_analisis: 'falló el análisis', no_es_track: 'no es un track', duplicado: 'repetido',
}
const poolEsperado = (pool) => ({
  titular: `Entran ${pool.entran} de ${pool.total} temas, del mismo género que la semilla (${pool.genero})`,
  detalle: Object.keys(MOTIVO_CONTADO).filter((k) => pool.excluidos[k] > 0).map((k) => MOTIVO_CONTADO[k](pool.excluidos[k])).join(' · '),
})
const leerPoolBarra = (page) => page.evaluate(() => ({
  titular: document.querySelector('.rarma-pool .rpool-tit')?.textContent ?? null,
  detalle: document.querySelector('.rarma-pool .rpool-det')?.textContent ?? null,
}))
const leerAccionSet = (page) => page.evaluate(() => {
  const b = [...document.querySelectorAll('.crate-accion')].find((x) => x.querySelector('.crate-accion-t')?.textContent === 'Armar un set')
  if (!b) return null
  const d = document.getElementById(b.getAttribute('aria-describedby') || '')
  return { deshabilitada: b.getAttribute('aria-disabled') === 'true', motivo: d && window.__visible(d) ? d.textContent : null, alto: Math.round(b.getBoundingClientRect().height) }
})
async function abrirPlaylistEnRail(page, ctx, nombre) {
  await abrirHome(page, ctx)
  await hasta(() => page.evaluate((n) => [...document.querySelectorAll('.pl-item')].some((b) => b.querySelector('.pl-item-name')?.textContent === n), nombre),
    (v) => v, `«${nombre}» no aparece en el rail`)
  await page.evaluate((n) => [...document.querySelectorAll('.pl-item')].find((b) => b.querySelector('.pl-item-name')?.textContent === n).click(), nombre)
  await hasta(() => page.evaluate(() => document.querySelector('.name-edit')?.value ?? null), (v) => v === nombre, `no se abrió «${nombre}» en Mis playlists`)
}

const CASOS_F55 = [

  ['radio (f55): sin playlist pide una (= /api/playlists); «Armar un set» de Mis playlists abre la radio con ESA playlist, con el teclado', async (page, ctx) => {
    await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('button[aria-label="Radio DJ"]', { timeout: ESPERA_MS })
    await page.click('button[aria-label="Radio DJ"]')
    const nombres = (await api(ctx, '/api/playlists')).playlists.map((p) => p.nombre)
    const ofrecidas = await hasta(() => page.$$eval('.rpl-item', (bs) => bs.map((b) => ({ nombre: b.querySelector('.rpl-nombre')?.textContent, alto: Math.round(b.getBoundingClientRect().height), actual: b.getAttribute('aria-current') }))),
      (v) => v.length === nombres.length, 'la radio no ofrece todas las playlists de /api/playlists')
    igual(ofrecidas.map((o) => o.nombre), nombres, 'las playlists que ofrece la radio')
    afirmar(ofrecidas.every((o) => o.alto >= 44 && o.actual === null), `elegir playlist: botones de menos de 44 px o uno marcado sin elegir: ${json(ofrecidas)}`)
    igual(await page.$$eval('.rsem', (s) => s.length), 0, 'sin playlist elegida no tiene que haber semillas')

    // Desde Mis playlists, con el teclado: Tab llega a «Armar un set» y Enter abre la radio.
    const rp = await api(ctx, `/api/radio/playlists/${ctx.base.radio_playlist}`)
    igual([rp.puede_armar, rp.generos.map((g) => [g.genero, g.entran])], [true, [['Techno', 5]]], 'la playlist de juguete según la API')
    await abrirPlaylistEnRail(page, ctx, ctx.base.radio_playlist_nombre)
    const acc = await hasta(() => leerAccionSet(page), (a) => a && a.motivo && !a.motivo.startsWith('Revisando'), '«Armar un set» no terminó de preguntarle a la radio')
    igual(acc.deshabilitada, false, '«Armar un set» tendría que estar habilitado con 5 temas de Techno')
    afirmar(acc.motivo.startsWith('Techno: 5 temas.') && acc.alto >= 44, `«Armar un set» no dice cuántos entran (o mide menos de 44 px): ${json(acc)}`)
    await page.evaluate(() => [...document.querySelectorAll('.crate-accion')].find((x) => x.querySelector('.crate-accion-t')?.textContent === 'Armar un set').focus())
    await page.keyboard.press('Enter')
    await page.waitForSelector('.rsem', { timeout: ESPERA_MS })
    igual(await page.$eval('.rpl-actual-nombre', (e) => e.textContent), ctx.base.radio_playlist_nombre, 'la radio no abrió con la playlist de la que se pidió')
    igual(await page.$$eval('.rsem .rsem-titulo', (s) => s.map((x) => x.textContent)), rp.semillas.map((s) => s.titulo), 'las semillas no son las de esa playlist')

    // Una playlist vacía: deshabilitado con el motivo del server, y el clic no hace nada.
    const vacia = await apiPedir(ctx, '/api/playlists', 'POST', { nombre: `E2E vacía ${Date.now()}` })
    await abrirPlaylistEnRail(page, ctx, vacia.data.playlist.nombre)
    const acc2 = await hasta(() => leerAccionSet(page), (a) => a && a.motivo && !a.motivo.startsWith('Revisando'), '«Armar un set» no terminó de preguntarle a la radio')
    igual([acc2.deshabilitada, acc2.motivo], [true, 'La playlist está vacía.'], '«Armar un set» con una playlist vacía')
    await page.evaluate(() => [...document.querySelectorAll('.crate-accion')].find((x) => x.querySelector('.crate-accion-t')?.textContent === 'Armar un set').click())
    await page.evaluate(() => new Promise((r) => setTimeout(r, 300)))
    igual(await page.$$eval('.rsem, .rpl-item', (s) => s.length), 0, 'el clic en «Armar un set» deshabilitado abrió la radio')
    await apiPedir(ctx, `/api/playlists/${vacia.data.playlist.id}`, 'DELETE')
  }],

  ['radio (f55): «Entran N de M» y los que quedan afuera = /api/radio/playlists (antes de armar) y el pool de /api/radio/set (armado)', async (page, ctx) => {
    const rp = await api(ctx, `/api/radio/playlists/${ctx.base.radio_playlist}`)
    await abrirRadio(page, ctx)
    igual(await leerPoolBarra(page), { titular: null, detalle: null }, 'sin semilla no hay género: no se dice cuántos entran')
    await elegirSemilla(page, 'Uno')
    const techno = rp.generos.find((g) => g.genero === 'Techno')
    igual(await leerPoolBarra(page), poolEsperado(techno), 'antes de armar: el renglón no es el del género de la semilla')
    const set = await armarSet(page)
    igual([set.pool.entran, set.pool.total, set.pool.genero], [5, 9, 'Techno'], 'la API: la playlist de juguete tiene 9 temas y entran los 5 de Techno que son tracks')
    igual(await leerPoolBarra(page), poolEsperado(set.pool), 'armado: el renglón no es el pool de /api/radio/set')
    // Ver los de afuera, con el teclado.
    igual(await page.$eval('.rpool-ver', (b) => b.textContent), `Ver los ${set.pool.afuera.length} que quedan afuera`, 'el botón no dice cuántos quedan afuera')
    // Cerrada, aria-controls no apunta a un id que no existe (auditoría f55).
    igual(await page.$eval('.rpool-ver', (b) => [b.getAttribute('aria-expanded'), b.getAttribute('aria-controls')]), ['false', null],
      'con la lista cerrada, aria-controls apunta a algo que no está')
    await page.focus('.rpool-ver')
    await page.keyboard.press('Enter')
    const lista = await hasta(() => page.$$eval('#r-afuera li', (ls) => ls.map((l) => ({
      n: l.querySelector('.rpool-n')?.textContent, tema: l.querySelector('.rpool-tema')?.textContent,
      genero: l.querySelector('.rpool-genero')?.textContent ?? null, motivo: l.querySelector('.rpool-motivo')?.textContent,
    }))), (v) => v.length > 0, 'Enter en «Ver los que quedan afuera» no abrió la lista')
    igual(await page.$eval('.rpool-ver', (b) => [b.getAttribute('aria-expanded'), b.getAttribute('aria-controls'), !!document.getElementById(b.getAttribute('aria-controls'))]),
      ['true', 'r-afuera', true], 'abierta: aria-expanded y aria-controls a la lista que existe')
    igual(lista, set.pool.afuera.map((a) => ({
      n: `#${a.posicion}`, tema: `${a.artista ? `${a.artista} — ` : ''}${a.titulo}`, genero: a.genero, motivo: `${MOTIVO_ETIQUETA[a.motivo]}: ${a.texto}`,
    })), 'la lista de los que quedan afuera no es la de la API')
    igual(set.pool.afuera.map((a) => a.motivo).sort(), ['no_es_track', 'otro_genero', 'sin_archivo', 'sin_genero'], 'la base de juguete deja afuera uno por motivo')
    const enSet = set.pasos.map((p) => p.item_id)
    afirmar(enSet.every((i) => Number.isInteger(i)) && !set.pool.afuera.some((a) => enSet.includes(a.item_id)),
      `un tema de afuera está en el set (o un paso sin item): ${json(enSet)}`)
    await page.keyboard.press('Enter')
    await hasta(() => page.$$eval('#r-afuera', (x) => x.length), (n) => n === 0, 'Enter otra vez no cerró la lista')
  }],

  ['radio (f55): «Guardar como playlist» crea la playlist con los temas del set en su orden y se abre en Mis playlists', async (page, ctx) => {
    await abrirRadio(page, ctx)
    await elegirSemilla(page, 'Uno')
    const set = await armarSet(page)
    const resp = page.waitForResponse((r) => new URL(r.url()).pathname === `/api/radio/playlists/${ctx.base.radio_playlist}/guardar`, { timeout: ESPERA_MS })
    await page.click('.rguardar-pl')
    const r = await resp
    igual(r.status(), 201, 'estado del POST de guardar como playlist')
    const nueva = (await r.json()).playlist
    const aviso = await hasta(() => page.$eval('.rguardar-pl-ok', (e) => e.textContent).catch(() => null), (v) => !!v, 'no avisó que se guardó')
    afirmar(aviso.includes(`«${nueva.nombre}»`) && aviso.includes(`${set.total} temas`), `el aviso no dice qué playlist ni cuántos temas: ${aviso}`)
    const items = (await api(ctx, `/api/playlists/${nueva.id}`)).data.items
    igual(items.map((i) => i.titulo), set.pasos.map((p) => p.track.titulo), 'la playlist nueva no tiene los temas del set en su orden')
    igual([...new Set(items.map((i) => i.genero))], ['Techno'], 'la playlist nueva perdió el género de los temas')
    await page.evaluate(() => [...document.querySelectorAll('.rguardar-pl-ok button')][0].click())
    await hasta(() => page.evaluate(() => document.querySelector('.name-edit')?.value ?? null), (v) => v === nueva.nombre, '«Abrir» no llevó a la playlist nueva')
    await apiPedir(ctx, `/api/playlists/${nueva.id}`, 'DELETE')
  }],

  ['radio (f55): con randomness 0, armar dos veces da el mismo set', async (page, ctx) => {
    await abrirRadio(page, ctx)
    await elegirSemilla(page, 'Uno')
    const a = await armarSet(page)
    igual(a.config.randomness, 0, 'el default del motor ya no es randomness 0: este caso no prueba el determinismo')
    await desplegarArmado(page)
    const b = await armarSet(page)
    igual(b.pasos.map((p) => [p.track.id, p.motivo]), a.pasos.map((p) => [p.track.id, p.motivo]), 'el mismo pedido dio otro set')
    igual(await page.$$eval('.rpaso .rpaso-titulo', (s) => s.map((x) => x.textContent)), a.pasos.map((p) => p.track.titulo), 'la pantalla no muestra ese set')
  }],

  ['radio (f55): los puntos de color de cada tema = las marcas de la base (pad, memory, loop), no solo color', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    await limpiarMarcas(ctx, uno.id)
    try {
      for (const m of [{ tipo: 'cue', inicio: 1, num: 2 }, { tipo: 'memory', inicio: 3 }, { tipo: 'loop', inicio: 5, fin: 9 }]) {
        const r = await apiPedir(ctx, rutaMarcas(uno.id), 'POST', m)
        afirmar(r.status === 201 || r.status === 200, `no pude crear la marca ${json(m)}: ${r.status} ${json(r.data)}`)
      }
      await abrirRadio(page, ctx)
      await elegirSemilla(page, 'Uno')
      const set = await armarSet(page)
      const filas = await hasta(() => page.$$eval('.rpaso', (rs) => rs.map((r) => ({
        titulo: r.querySelector('.rpaso-titulo')?.textContent,
        puntos: [...r.querySelectorAll('.rcues-puntos i')].map((i) => i.className),
        texto: r.querySelector('.rcues-puntos')?.getAttribute('title') ?? null,
        cuenta: r.querySelector('.rcues')?.textContent,
      }))), (v) => v.some((f) => f.puntos.length), 'no aparecieron los puntos de las marcas')
      igual(filas[0], { titulo: 'Uno', puntos: ['cue-c3', 'cue-mem', 'cue-loop'], texto: '1 hot cue, 1 memory, 1 loop', cuenta: '3 cues' },
        'los puntos de «Uno» no son sus marcas de la base')
      afirmar(filas.slice(1).every((f) => f.puntos.length === 0 && f.texto === null), `un tema sin marcas tiene puntos: ${json(filas)}`)
      igual(filas.length, set.pasos.length, 'filas del set')
    } finally {
      await limpiarMarcas(ctx, uno.id)
    }
  }],

  ['radio (f55): 400 px con la lista de afuera abierta: sin desborde y los botones nuevos de 44 px', async (page, ctx) => {
    await abrirRadio(page, ctx)
    await elegirSemilla(page, 'Uno')
    await armarSet(page)
    await page.setViewport({ width: 400, height: 860 })
    await hasta(() => page.evaluate(() => document.documentElement.clientWidth), (w) => w <= 400, 'el viewport no pasó a 400 px')
    await page.click('.rpool-ver')
    await page.waitForSelector('#r-afuera li', { timeout: ESPERA_MS })
    const d = await desbordeDe(page)
    afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `a 400 px hay contenido fuera del ancho: ${json(d)}`)
    const altos = await page.$$eval('.rpool-ver, .rguardar-pl, .rpl-cambiar', (bs) => bs.map((b) => Math.round(b.getBoundingClientRect().height)))
    afirmar(altos.length === 3 && altos.every((h) => h >= 44), `botones de menos de 44 px: ${json(altos)}`)
  }],
]

const CASOS = [

  ...CASOS_F55,

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
    await esperarCajon(page, '.drawer-left.is-open')
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

  ['radio: cada semilla de la lista = /api/radio/playlists/{id} (BPM, Camelot y clásica, ?, energía, género)', async (page, ctx) => {
    const { lib } = await semillaUno(ctx)
    const leyenda = lib.opciones.leyenda_key
    await abrirRadio(page, ctx)
    const real = await hasta(() => page.$$eval('.rsem', (bs) => bs.map((b) => ({
      titulo: b.querySelector('.rsem-titulo')?.textContent ?? null,
      artista: b.querySelector('.rsem-artista')?.textContent ?? null,
      datos: window.__datosTrack(b),
      noTrack: !!b.querySelector('.rtag-notrack'),
      genero: b.querySelector('.rsem-genero')?.textContent ?? null,
    }))), (v) => v.length === lib.tracks.length, `la lista no tiene las ${lib.tracks.length} semillas de la API`)
    const esperado = lib.tracks.map((t) => ({
      titulo: t.titulo, artista: t.artista || '—', datos: esperadoDatos(t, leyenda), noTrack: !t.es_track, genero: t.genero,
    }))
    // f55: el loop (no es un track) no puede arrancar un set: no está en la lista.
    afirmar(!real.some((r) => r.titulo === 'Loop'), 'el loop aparece como semilla')
    for (let i = 0; i < esperado.length; i++) igual(real[i], esperado[i], `semilla ${i + 1} («${esperado[i].titulo}»)`)
    afirmar(esperado.some((t) => t.datos.key.duda), 'la base de juguete no tiene ninguna key dudosa: el chequeo del ? no probaría nada')
  }],

  ['radio: el set = /api/radio/set (orden, motivos, datos, titular del corte, fragmentos)', async (page, ctx) => {
    const { lib, uno } = await semillaUno(ctx)
    const leyenda = lib.opciones.leyenda_key
    const set = await api(ctx, `/api/radio/set?playlist=${ctx.base.radio_playlist}&track=${encodeURIComponent(uno.id)}`)
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
    // f55: el pool ya no trae fragmentos (lo que no es un track queda afuera antes, con motivo).
    igual([set.fragmentos, set.aviso_fragmentos], [0, null], 'la API: un set desde playlist no tiene fragmentos')
    igual(real.notas, [set.leyenda_key].filter(Boolean), 'las notas debajo del set (leyenda del ?)')
    igual(real.curva, set.pasos.map((p) => (p.track.energia_pct == null ? '2px' : `${Math.max(2, p.track.energia_pct)}%`)),
      'la curva de energía no sale del mismo percentil que las filas')
    // Que el caso pruebe algo: la base de juguete corta el set, y el loop (que no es un track)
    // queda afuera ANTES del motor (f55), con su motivo, en vez de ser un fragmento ignorado.
    afirmar(set.corte && set.pool.afuera.some((a) => a.motivo === 'no_es_track'),
      'la base de juguete ya no produce corte ni deja afuera al loop: este caso quedó sin dientes')
  }],

  ['radio: exportar baja el mismo .m3u8 (bytes y nombre) que /api/radio/set.m3u8', async (page, ctx) => {
    const { uno } = await semillaUno(ctx)
    const r = await fetch(`${ctx.url}/api/radio/set.m3u8?playlist=${ctx.base.radio_playlist}&track=${encodeURIComponent(uno.id)}`)
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
    await page.waitForSelector('.rpl-item', { timeout: ESPERA_MS })   // f55: la radio pide la playlist
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
    await esperarCajon(page, '.drawer-left.is-open')
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
      await esperarCajon(page, '.drawer-left.is-open')
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
    await esperarCajon(page, '.drawer-right.is-open .hist-open')
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
      // Primero que termine la tanda (aparece el resumen) y DESPUÉS dónde quedó el foco: entre que
      // el botón se va y el resumen lo recibe, el foco pasa un instante por el <body>, y aceptar
      // BODY como «terminó» hacía el caso intermitente (auditoría f54).
      await hasta(() => page.evaluate(() => [...document.querySelectorAll('[role="status"]')].some((e) => e.textContent.trim().startsWith('Listo:'))),
        (v) => v, 'la tanda no terminó')
      const fin = await hasta(foco, (v) => v.rol === 'status', 'al terminar la tanda el foco no pasó al resumen (quedó donde dice «quedó»)', 3000)
      igual(fin, { tag: 'DIV', rol: 'status', texto: 'Listo: 1 de 1 bajado' }, 'al terminar la tanda el foco no pasó al resumen')
    } finally {
      await apiPedir(ctx, `/api/playlists/${pid}`, 'DELETE')
      await apiPedir(ctx, `/api/playlists/${activa}`, 'DELETE')
      if (previa) await apiPedir(ctx, `/api/playlists/${previa.id}`, 'PATCH', { activar: true })
    }
  }],

  // ---------------------------------------------------------------------- f66: Jamendo y licencias
  // Las filas tienen la forma de `server._buscar_jamendo`, el historial la de
  // `db.listar_historial` y la carpeta vigilada la de `server.vigilancia_estado` (apagada: la
  // respuesta REAL del server del E2E, que arranca sin MUSIC_WATCH_FOLDER).
  ['jamendo (f66): la fila dice la licencia (o «no declarado») y sin permiso del artista «Descargar» queda deshabilitado con el motivo; el historial muestra licencia y origen; la carpeta vigilada dice lo que da /api/vigilancia', async (page, ctx) => {
    const opcion = (extra) => ({ titulo: 'Campo Abierto', artista: 'Coro de Niebla', duracion: 254, fuente: 'jamendo', thumbnail: null,
      url: 'https://www.jamendo.com/track/1500001', id: '1500001', licencia: 'http://creativecommons.org/licenses/by-sa/3.0/',
      origen: 'https://www.jamendo.com/track/1500001', descargable: true, ...extra })
    const grupos = [
      { opciones: [opcion()] },
      { opciones: [opcion({ titulo: 'Campo Cerrado', url: 'https://www.jamendo.com/track/1500002', id: '1500002', licencia: 'no declarado', descargable: false })] },
    ]
    const ahora = new Date().toISOString()
    const descargas = [
      { id: 2, titulo: 'Campo Abierto', artista: 'Coro de Niebla', fuente: 'jamendo', formato: 'mp3', archivo: 'Campo Abierto - Coro de Niebla.mp3',
        grade: null, color: null, licencia: 'http://creativecommons.org/licenses/by-sa/3.0/', origen: 'https://www.jamendo.com/track/1500001', creado_en: ahora },
      // Como la mandaba la API antes de f66 (sin los campos): la pantalla no dibuja un vacío.
      { id: 1, titulo: 'Tema YT', artista: 'Artista Y', fuente: 'youtube', formato: 'wav', archivo: 'Tema YT.wav', grade: 'B', color: null, creado_en: ahora },
    ]
    const vigReal = await api(ctx, '/api/vigilancia')
    afirmar(vigReal.activa === false && vigReal.mensaje, `el server del E2E tenía que arrancar con la carpeta vigilada apagada: ${json(vigReal)}`)
    let vig = null            // null: contesta el server; si no, esta respuesta
    let frenoVig = null       // una promesa: hasta que se cumpla, GET /api/vigilancia no contesta
    let lecturasVig = 0
    let escanearHtml = false  // true: el POST contesta un 500 de HTML (un server roto, un proxy)
    let escanear409 = null    // si no es null: el POST contesta 409 con este cuerpo (apagada)
    let frenoEsc = null       // como frenoVig, para el POST
    const escaneos = []
    const bajadas = []        // lo que pidió "Descargar todas" a /api/descargar
    await interceptar(page, async (req) => {
      const u = new URL(req.url())
      const responder = async (body) => { await req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }); return true }
      if (u.pathname === '/api/buscar') return responder({ exito: true, grupos })
      if (u.pathname === '/api/calidad') return responder({ ok: false, grade: '?' })
      if (u.pathname === '/api/meta') return responder({ bpm: null, genero: null })
      if (u.pathname === '/api/historial') return responder({ busquedas: [], playlists: [], descargas })
      if (u.pathname === '/api/descargar') { bajadas.push(JSON.parse(req.postData()).url); return responder({ exito: true, calidad: { grade: 'A' } }) }
      if (u.pathname === '/api/vigilancia') {
        lecturasVig++
        if (frenoVig) await frenoVig
        if (vig) return responder(vig)
      }
      if (u.pathname === '/api/vigilancia/escanear') {
        escaneos.push(req.method())
        if (frenoEsc) await frenoEsc
        if (escanear409) { await req.respond({ status: 409, contentType: 'application/json', body: JSON.stringify(escanear409) }); return true }
        if (escanearHtml) { await req.respond({ status: 500, contentType: 'text/html', body: '<html><body><h1>Internal Server Error</h1></body></html>' }); return true }
        return responder({ exito: true, encolado: true, estado: vig.estado })
      }
      if (u.origin !== new URL(ctx.url).origin) { await req.abort(); return true }
      return false
    })
    await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
    const caja = 'input[aria-label="Buscar una canción, artista o género"]'
    await page.waitForSelector(caja, { timeout: ESPERA_MS })
    await page.type(caja, 'campo')
    await page.keyboard.press('Enter')
    const filas = await hasta(() => page.evaluate(() => [...document.querySelectorAll('.trk')].map((r) => {
      const lic = r.querySelector('.trk-meta .mb-lic')
      const dl = r.querySelector('.trk-acts .btn-dl')
      return {
        titulo: r.querySelector('.trk-title')?.textContent ?? null,
        licencia: lic?.querySelector('b')?.textContent ?? null, licTitulo: lic?.getAttribute('title') ?? null,
        atenuada: lic ? lic.classList.contains('is-no-declarado') : null,
        dlDeshabilitado: dl ? dl.disabled : null, dlTitulo: dl?.getAttribute('title') ?? null,
        nota: r.querySelector('.trk-sin-descarga')?.textContent ?? null,
        pastillas: [...r.querySelectorAll('.vchip')].map((b) => ({ texto: b.textContent.trim(), color: getComputedStyle(b).getPropertyValue('--pf').trim() })),
      }
    })), (v) => v.length === 2, 'no aparecieron las dos filas de Jamendo')
    igual(filas, [
      { titulo: 'Campo Abierto', licencia: 'CC BY-SA 3.0', licTitulo: 'Licencia declarada por Jamendo: http://creativecommons.org/licenses/by-sa/3.0/',
        atenuada: false, dlDeshabilitado: false, dlTitulo: 'Descargar', nota: null, pastillas: [{ texto: 'Jamendo', color: '#e46fbf' }] },
      { titulo: 'Campo Cerrado', licencia: 'no declarado', licTitulo: 'Jamendo no declara la licencia de este tema',
        atenuada: true, dlDeshabilitado: true, dlTitulo: 'El artista no habilitó la descarga en Jamendo',
        nota: 'El artista no habilitó la descarga en Jamendo.', pastillas: [{ texto: 'Jamendo', color: '#e46fbf' }] },
    ], 'las filas de Jamendo: licencia, color de la plataforma y descarga')

    // "Descargar todas" no intenta la que el artista no dejó bajar, y lo cuenta.
    await clickTexto(page, 'button', /^Descargar todas/)
    const finTodas = await hasta(() => botonTodas(page), (t) => /^✓/.test(t || ''), '"Descargar todas" no terminó')
    igual([bajadas, finTodas], [[grupos[0].opciones[0].url], '✓ 1/1 descargadas · 1 sin descarga habilitada'],
      '"Descargar todas" con un tema de Jamendo sin permiso de descarga')

    // Historial: licencia y origen de cada descarga.
    const abrirHistorial = async () => {
      await page.click('button[aria-label="Menú"]')
      await esperarCajon(page, '.drawer-left.is-open')
      afirmar(await page.evaluate(() => { const b = [...document.querySelectorAll('.navitem')].find((x) => x.textContent === 'Historial'); b?.click(); return !!b }),
        'el menú no tiene "Historial"')
      await esperarCajon(page, '.drawer-right.is-open')
    }
    await abrirHistorial()
    const hist = await hasta(() => page.evaluate(() => [...document.querySelectorAll('.drawer-right.is-open .hist-item')].map((it) => ({
      tema: it.querySelector('.truncate')?.textContent ?? null, lic: it.querySelector('.hist-lic')?.textContent ?? null,
      link: it.querySelector('.hist-lic a')?.getAttribute('href') ?? null,
    }))), (v) => v.length === descargas.length, 'el historial no mostró las dos descargas')
    igual(hist, [
      { tema: 'Campo Abierto — Coro de Niebla', lic: 'Licencia: CC BY-SA 3.0 · Origen: jamendo.com', link: 'https://www.jamendo.com/track/1500001' },
      { tema: 'Tema YT — Artista Y', lic: 'Licencia: no declarado · Origen: no declarado', link: null },
    ], 'licencia y origen de cada descarga del historial')

    // Carpeta vigilada apagada: el mensaje de la API (dice cómo prenderla) y nada para escanear.
    const leerVig = () => page.evaluate(() => {
      const s = document.querySelector('.drawer-right.is-open .vig')
      return s ? {
        estado: s.dataset.estado ?? null, titulo: s.querySelector('.vig-titulo')?.textContent ?? null,
        partes: [...s.querySelectorAll('.vig-partes')].map((p) => p.textContent), aviso: s.querySelector('.vig-aviso')?.textContent ?? null,
        boton: s.querySelector('button')?.textContent ?? null, msg: s.querySelector('[role=status]')?.textContent ?? null,
      } : null
    })
    igual(await hasta(leerVig, (v) => v && v.estado, 'no apareció el estado de la carpeta vigilada'),
      { estado: 'apagada', titulo: vigReal.mensaje, partes: [], aviso: null, boton: null, msg: '' }, 'la carpeta vigilada apagada')

    // Prendida: la carpeta, el último escaneo y los contadores; «Escanear ahora» = un POST.
    // El escaneo es de este año (del año del reloj): uno de otro año se muestra con el año.
    vig = { exito: true, activa: true, estado: { carpeta: 'Mis Descargas', alcanzable: true, corriendo: true, procesando: [], pendientes: 2,
      creciendo: 0, ignorados: 0, procesados: 14, fallidos: 1, ultimos_fallidos: ['roto.mp3'], ultimo_ciclo: `${new Date().getFullYear()}-10-21T09:05:33`,
      ultimo_error: null, ultimo_reporte: 'revision_vigilada_2026-10-21_090533.html', intervalo_s: 60 } }
    const cerrarHistorial = async () => {
      await page.click('.drawer-right.is-open button[aria-label="Cerrar historial"]')
      await hasta(() => page.evaluate(() => !!document.querySelector('.drawer-right.is-open')), (v) => !v, 'el historial no se cerró')
    }
    const leerBusy = () => page.evaluate(() => {
      const s = document.querySelector('.drawer-right.is-open .vig')
      return s ? { busy: s.getAttribute('aria-busy'), texto: s.textContent } : null
    })
    const LEYENDO = { busy: 'true', texto: 'Leyendo la carpeta vigilada…' }
    await cerrarHistorial()
    // Al volver a abrir, mientras el server no contesta: "Leyendo…", no el estado de antes (apagada).
    let soltarVig
    let soltarViejo = () => {}
    let leyendo, trasViejo
    frenoVig = new Promise((r) => { soltarVig = r })
    try {
      const lecturasAlAbrir = lecturasVig
      await abrirHistorial()
      leyendo = await leerBusy()
      // Se cierra con ese GET todavía en vuelo (ya llegó, frenado) y se vuelve a abrir (el GET
      // nuevo, frenado también): cuando contesta el de antes, esta apertura sigue en "Leyendo…".
      const lecturasEnVuelo = await hasta(() => lecturasVig, (n) => n > lecturasAlAbrir, 'al abrir no se pidió el estado')
      await cerrarHistorial()
      soltarViejo = soltarVig
      frenoVig = new Promise((r) => { soltarVig = r })
      await abrirHistorial()
      await hasta(() => lecturasVig, (n) => n > lecturasEnVuelo, 'al volver a abrir no se pidió el estado otra vez')
      soltarViejo()
      await page.evaluate(() => new Promise((r) => setTimeout(r, 400)))   // gracia: la página procesa la respuesta vieja
      trasViejo = await leerBusy()
    } finally {
      frenoVig = null
      soltarViejo()
      soltarVig()
    }
    igual(leyendo, LEYENDO, 'al volver a abrir se vio el estado de la vez anterior')
    igual(trasViejo, LEYENDO, 'la respuesta de una apertura anterior (cerrada con el GET en vuelo) pisó a la de ahora')
    igual(await hasta(leerVig, (v) => v && v.estado === 'activa', 'al volver a abrir, la carpeta vigilada no se leyó otra vez'),
      { estado: 'activa', titulo: 'Carpeta vigilada «Mis Descargas»',
        partes: ['último escaneo 21/10 09:05 · 2 pendientes · 14 procesados · 1 fallido', 'Último reporte: revision_vigilada_2026-10-21_090533.html'],
        aviso: null, boton: 'Escanear ahora', msg: '' }, 'la carpeta vigilada prendida')
    const lecturasAntes = lecturasVig
    await page.click('.drawer-right.is-open .vig button')
    const tras = await hasta(leerVig, (v) => v && v.msg, 'tocar «Escanear ahora» no dijo nada')
    igual([escaneos, tras.msg], [['POST'], 'Escaneo pedido: arranca en unos segundos.'], '«Escanear ahora» = un POST a /api/vigilancia/escanear')
    // Unos segundos después se relee el estado y el aviso ya cumplió: se va.
    const releido = await hasta(leerVig, (v) => v && lecturasVig > lecturasAntes && v.msg === '',
      'después de releer el estado, «Escaneo pedido» seguía en pantalla', 4000 + ESPERA_MS)
    igual([lecturasVig - lecturasAntes, releido.estado], [1, 'activa'], 'la relectura después de «Escanear ahora»')

    // Un POST que vuelve con una página de error (no JSON): el server contestó, no "no pude conectar".
    escanearHtml = true
    await page.click('.drawer-right.is-open .vig button')
    const roto = await hasta(leerVig, (v) => v && v.msg, 'un 500 de HTML al escanear no dijo nada')
    igual([escaneos, roto.msg], [['POST', 'POST'], 'El servidor contestó con un error (HTTP 500).'], '«Escanear ahora» con un 500 de HTML')

    // Lo que sigue prueba que algo NO pasa después de la relectura (4 s en VigilanciaEstado):
    // la única forma es dejar pasar ese tiempo. `pasadaRelectura` espera 4 s + un margen.
    const pasadaRelectura = () => page.evaluate(() => new Promise((r) => setTimeout(r, 4000 + 700)))
    const PEDIDO = 'Escaneo pedido: arranca en unos segundos.'
    const APAGADA_409 = { exito: false, mensaje: 'La carpeta vigilada está apagada: definí MUSIC_WATCH_FOLDER en el .env y reiniciá el servidor.' }
    const boton = '.drawer-right.is-open .vig button'
    escanearHtml = false

    // Un segundo «Escanear ahora» que vuelve con exito:false mientras la relectura del primero
    // está pendiente: esa relectura ya no corre y el aviso del segundo se queda.
    await page.click(boton)
    await hasta(leerVig, (v) => v && v.msg === PEDIDO, 'el escaneo que salió bien no dijo «Escaneo pedido»')
    escanear409 = APAGADA_409
    const lecturasPendiente = lecturasVig
    await page.click(boton)
    await hasta(leerVig, (v) => v && v.msg === APAGADA_409.mensaje, 'un 409 al escanear no mostró su mensaje')
    await pasadaRelectura()
    igual([(await leerVig())?.msg, lecturasVig - lecturasPendiente], [APAGADA_409.mensaje, 0],
      'la relectura pendiente del escaneo anterior corrió y borró el aviso del 409')

    // Lo mismo con la relectura YA en vuelo (el GET frenado) cuando se pide el segundo escaneo:
    // cuando contesta, no borra el aviso del segundo.
    escanear409 = null
    await page.click(boton)
    await hasta(leerVig, (v) => v && v.msg === PEDIDO, 'el escaneo que salió bien no dijo «Escaneo pedido»')
    const lecturasEnVuelo2 = lecturasVig
    let soltarRelectura = () => {}
    let trasRelectura
    frenoVig = new Promise((r) => { soltarRelectura = r })
    try {
      await hasta(() => lecturasVig, (n) => n > lecturasEnVuelo2, 'no se releyó el estado después de «Escanear ahora»', 4000 + ESPERA_MS)
      escanear409 = APAGADA_409
      await page.click(boton)
      await hasta(leerVig, (v) => v && v.msg === APAGADA_409.mensaje, 'un 409 al escanear no mostró su mensaje')
      frenoVig = null
      soltarRelectura()
      await page.evaluate(() => new Promise((r) => setTimeout(r, 400)))   // gracia: la página procesa la relectura
      trasRelectura = await leerVig()
    } finally {
      frenoVig = null
      soltarRelectura()
    }
    igual(trasRelectura?.msg, APAGADA_409.mensaje, 'la relectura del escaneo anterior (en vuelo) borró el aviso del 409')

    // Cerrar el cajón corta todo: ni la relectura que estaba armada ni la de un POST que vuelve
    // después de cerrar piden el estado con el cajón cerrado.
    escanear409 = null
    await page.click(boton)
    await hasta(leerVig, (v) => v && v.msg === PEDIDO, 'el escaneo que salió bien no dijo «Escaneo pedido»')
    await cerrarHistorial()                 // con la relectura de ese escaneo armada
    const lecturasReabrir = lecturasVig
    await abrirHistorial()
    await hasta(leerVig, (v) => v && v.estado === 'activa' && lecturasVig > lecturasReabrir, 'al volver a abrir no se leyó el estado')
    // Desde acá no hay ningún GET legítimo: la relectura armada antes de cerrar (si no se
    // cancelara) caería dentro de la espera de abajo, y la del POST que vuelve cerrado también.
    const lecturasCerrado = lecturasVig
    let soltarEsc = () => {}
    frenoEsc = new Promise((r) => { soltarEsc = r })
    try {
      const escaneosAntes = escaneos.length
      await page.click(boton)
      await hasta(() => escaneos.length, (n) => n > escaneosAntes, '«Escanear ahora» no hizo el POST')
      await cerrarHistorial()               // con el POST en vuelo
      frenoEsc = null
      soltarEsc()
      await pasadaRelectura()
    } finally {
      frenoEsc = null
      soltarEsc()
    }
    igual(lecturasVig - lecturasCerrado, 0, 'con el cajón cerrado se volvió a pedir el estado de la carpeta vigilada')
  }],

  // ---------------------------------------------------------------------- f53: importar
  // Al final a propósito: el análisis agrega temas a la base del motor de juguete, y los casos
  // de la radio de arriba comparan contra esa base.
  // (una función y no una constante: está declarada al final del archivo y se iza).
  ...casosF53(),
  // ---------------------------------------------------------------------- f54: editor D
  // También al final: da de alta (y de baja al terminar) los temas generados del editor.
  ...casosF54(),
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

/* ============================================================================
   f53 — importar playlists (Rekordbox y carpeta), estado de cada tema, género y análisis.

   Lo esperado sale de la API (`/api/importar/...`, `/api/playlists/{id}`) o de la base de
   juguete (base_juguete.py: qué archivos existen y con qué BPM se GENERÓ cada clic). Los textos
   que se comparan literales son el contrato de la pantalla (los del canvas F/G y el pedido del
   dueño): «N de M encontrados», «No encuentro el archivo», «En tu PC», «de Rekordbox».
   ========================================================================== */
function casosF53() {
  const ANALISIS_MS = 180000   // el primer análisis carga librosa (MUSIFLIX_SIN_CALENTAR=1)
  // Contrato del BPM (auditoría f53): lo medido por el motor con un decimal; lo que no se
  // midió, con la precisión que trae (un entero de la metadata no gana un «.0» inventado).
  const bpmTxt = (v, medido) => (medido || !Number.isInteger(Number(v)) ? Number(v).toFixed(1) : String(Number(v)))
  const ESTADO_ANALISIS = { analizado: 'Analizado', analizando: 'Analizando…', 'en-cola': 'En cola para analizar',
    fallo: 'No se pudo analizar', pendiente: 'Falta analizar' }

  const abrirImportar = async (page, ctx) => {
    await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('.pl-rail-importar', { timeout: ESPERA_MS })
    await page.click('.pl-rail-importar')
    await page.waitForSelector('.imp-dialog[role="dialog"]', { timeout: ESPERA_MS })
  }
  // El mismo XML, leído por la API como lo manda la pantalla (cuerpo crudo).
  const leerXmlApi = async (ctx, ruta) => {
    const r = await fetch(`${ctx.url}/api/importar/rekordbox/leer`, {
      method: 'POST', body: fs.readFileSync(ruta),
      headers: { 'Content-Type': 'application/xml', 'X-Nombre-Archivo': encodeURIComponent(path.basename(ruta)) },
    })
    return { status: r.status, data: await r.json() }
  }
  const subirXml = async (page, ruta) => {
    const input = await page.$('.imp-file input[type="file"]')
    afirmar(input, 'no está el campo para elegir el XML')
    await input.uploadFile(ruta)
  }
  const leerChecklist = (page) => page.$$eval('.imp-pl', (lis) => lis.map((li) => {
    const i = li.querySelector('input[type="checkbox"]')
    const det = i?.getAttribute('aria-describedby')
    return {
      nombre: li.querySelector('.imp-pl-nombre')?.textContent ?? null,
      carpeta: li.querySelector('.imp-pl-carpeta')?.textContent ?? null,
      n: li.querySelector('.imp-pl-n')?.textContent ?? null,
      tildada: i ? i.checked : null,
      detalle: [...li.querySelectorAll('.imp-pl-det > span')].map((s) => s.textContent),
      describe: det ? document.getElementById(det)?.textContent ?? null : null,
    }
  }))
  const encontradosTxt = (p) => `${p.encontrados} de ${p.total} encontrado${p.total === 1 ? '' : 's'}`

  const leerFilasPl = (page) => page.$$eval('.results-crate .trk', (rs) => rs.map((r) => {
    const chip = (l) => [...r.querySelectorAll('.trk-meta .mb')].find((m) => m.querySelector('.mb-label')?.textContent === l)?.querySelector('b')?.textContent ?? null
    const g = r.querySelector('.mb-genero')
    return {
      titulo: r.querySelector('.trk-title')?.textContent ?? null,
      estado: r.querySelector('.trk-estado')?.textContent ?? null,
      bpm: chip('BPM'),
      bpmTitulo: [...r.querySelectorAll('.trk-meta .mb')].find((m) => m.querySelector('.mb-label')?.textContent === 'BPM')?.getAttribute('title') ?? null,
      key: chip('KEY'),
      fuente: r.querySelector('.mb-fuente')?.textContent ?? null,
      genero: g ? g.querySelector('b')?.textContent ?? null : null,
      generoVisible: g ? window.__visible(g) : false,
      status: r.querySelector('.trk-status')?.textContent.trim() ?? null,
    }
  }))
  // Lo que la fila tiene que decir de un item de la API.
  const filaEsperada = (it) => {
    const a = it.analisis || {}
    let estado
    if (it.archivo_estado === 'ok') estado = `En tu PC · ${ESTADO_ANALISIS[a.estado]}`
    else if (it.archivo_estado === 'no-encontrado' || it.archivo_estado === 'no-existe') estado = 'No encuentro el archivo'
    else if (it.archivo_estado === 'ambiguo') estado = `Hay ${it.homonimos || 2} archivos con ese nombre`
    else estado = 'Sin archivo'
    // El nombre del archivo que no se encuentra va al lado (sin la ruta).
    if (it.archivo_estado !== 'ok' && it.nombre_archivo) estado += ` · ${it.nombre_archivo}`
    const key = a.camelot ? `${a.camelot}${a.tonalidad ? ` · ${a.tonalidad}` : ''}${a.key_dudosa ? ' ?' : ''}` : '?'
    return {
      titulo: it.titulo, estado,
      bpm: a.bpm ? bpmTxt(a.bpm, a.dato === 'motor') : '?',
      bpmTitulo: a.bpm ? (a.dato === 'motor' ? 'BPM medido por el motor' : a.dato === 'rekordbox' ? 'BPM de Rekordbox' : 'BPM sin medir') : 'sin BPM',
      key,
      fuente: a.dato === 'rekordbox' && (a.bpm || a.camelot) ? 'de Rekordbox' : null,
      genero: it.genero || '+ género', generoVisible: true,
    }
  }
  const sinStatus = (filas) => filas.map(({ status, ...f }) => f)  // eslint-disable-line no-unused-vars
  const itemsApi = async (ctx, pid) => (await api(ctx, `/api/playlists/${pid}`)).data.items
  const filasCoinciden = async (page, ctx, pid) => {
    const esperado = (await itemsApi(ctx, pid)).map(filaEsperada)
    const real = sinStatus(await leerFilasPl(page))
    return { real, esperado, ok: json(real) === json(esperado) }
  }
  const esperarFilas = async (page, ctx, pid, que, ms = ESPERA_MS) => {
    await hasta(() => filasCoinciden(page, ctx, pid), (x) => x.ok, que, ms)
    return leerFilasPl(page)
  }
  const esperarAnalisisApi = (ctx, pid) => hasta(async () => (await api(ctx, `/api/playlists/${pid}/analisis`)).progreso,
    (p) => !p.corriendo, 'el análisis no terminó', ANALISIS_MS)
  // Todo lo que se toca mide 44 px o más (pedido del dueño). Devuelve los que no.
  const chicos = (page, sel) => page.$$eval(sel, (els) => els.filter((e) => window.__visible(e)).map((e) => {
    const r = e.getBoundingClientRect()
    return { el: `${e.tagName.toLowerCase()}.${String(e.className).split(' ')[0]} «${(e.getAttribute('aria-label') || e.textContent).trim().slice(0, 30)}»`, w: Math.round(r.width), h: Math.round(r.height) }
  }).filter((x) => x.w < 44 || x.h < 44))
  // La ruta de la PC nunca se muestra (el server resuelve; la pantalla ve nombres).
  const sinRutas = async (page, ctx, que) => {
    const t = await page.evaluate(() => document.body.innerText)
    for (const r of [...ctx.base.library_roots, ctx.tmp].flatMap((x) => [x, x.replace(/\\/g, '/')])) {
      afirmar(!t.includes(r), `${que}: la pantalla muestra una ruta de la PC (${r})`)
    }
  }
  const borrarImportadas = async (ctx) => {
    for (const p of (await api(ctx, '/api/playlists')).playlists) {
      if (p.origen === 'rekordbox' || p.origen === 'carpeta') await apiPedir(ctx, `/api/playlists/${p.id}`, 'DELETE')
    }
  }
  const nombreAbierta = (page) => page.$eval('.crate-head .name-edit', (i) => i.value).catch(() => null)
  const abrirEnRail = async (page, nombre) => {
    await hasta(() => page.evaluate((n) => [...document.querySelectorAll('.pl-item')].some((b) => b.querySelector('.pl-item-name')?.textContent === n), nombre),
      (v) => v, `«${nombre}» no aparece en el rail`)
    await page.evaluate((n) => [...document.querySelectorAll('.pl-item')].find((b) => b.querySelector('.pl-item-name')?.textContent === n).click(), nombre)
    await hasta(() => nombreAbierta(page), (v) => v === nombre, `no se abrió «${nombre}»`)
  }
  const leerAcciones = (page) => page.$$eval('.crate-accion', (bs) => bs.map((b) => {
    const d = document.getElementById(b.getAttribute('aria-describedby') || '')
    return {
      titulo: b.querySelector('.crate-accion-t')?.textContent ?? null,
      deshabilitada: b.disabled || b.getAttribute('aria-disabled') === 'true',
      motivo: d && window.__visible(d) ? d.textContent : null,
      enfocable: b.tabIndex >= 0 && !b.disabled,
    }
  }))
  const dialogos = (page) => page.evaluate(() => document.querySelectorAll('[role="dialog"]').length)
  // Las métricas de BPM del crate contra las de la API: un decimal, la fuente dicha y nunca
  // un número sin medición. Los rótulos son el contrato (auditoría f53).
  const FUENTE_BPM = { motor: 'medido por el motor', rekordbox: 'de Rekordbox', otro: 'sin medir' }
  const metricasBpm = (page) => page.$$eval('.metric-bpm', (ms) => ms.map((x) => [x.querySelector('b')?.textContent ?? null, x.querySelector('span')?.textContent ?? null]))
  const esperadasBpm = (m) => {
    if (m.bpm_prom === null) return [['sin medir', 'BPM']]
    const f = `${FUENTE_BPM[m.bpm_fuente]}${m.bpm_afuera ? ` · ${m.bpm_afuera} de otra fuente afuera` : ''}`
    const t = (v) => bpmTxt(v, m.bpm_fuente === 'motor')
    const r = m.bpm_min === m.bpm_max ? t(m.bpm_min) : `${t(m.bpm_min)}–${t(m.bpm_max)}`
    return [[t(m.bpm_prom), `BPM promedio · ${f}`], [r, `Rango BPM · ${f}`]]
  }
  const esperarMetricas = async (page, ctx, pid, que) => {
    const m = (await api(ctx, `/api/playlists/${pid}`)).data.metrics
    await hasta(() => metricasBpm(page), (v) => json(v) === json(esperadasBpm(m)), `${que}: ${json(esperadasBpm(m))}`)
    return m
  }

  return [
    ['importar (f53): Rekordbox — «N de M encontrados», el porqué de los que faltan, «No encuentro el archivo» y el XML con DOCTYPE', async (page, ctx) => {
      await borrarImportadas(ctx)
      try {
        await abrirImportar(page, ctx)
        // El foco arranca en la primera pestaña (lo primero es elegir de dónde), no en «Cerrar».
        await hasta(() => page.evaluate(() => document.activeElement?.textContent ?? null), (v) => v === 'Desde Rekordbox',
          'el foco inicial del diálogo no está en la primera pestaña «Desde Rekordbox»')
        // Pestañas con rol: la de Rekordbox arranca elegida.
        const tabs = await page.$$eval('.imp-dialog [role="tab"]', (ts) => ts.map((t) => [t.textContent, t.getAttribute('aria-selected')]))
        igual(tabs, [['Desde Rekordbox', 'true'], ['Desde una carpeta de la PC', 'false']], 'las pestañas del diálogo no son las del tablero G')

        // Un XML con DOCTYPE: el motivo del server, tal cual, y nada de lista.
        const malo = path.join(ctx.tmp, 'con doctype.xml')
        fs.writeFileSync(malo, '<?xml version="1.0"?>\n<!DOCTYPE DJ_PLAYLISTS [<!ENTITY x "y">]>\n<DJ_PLAYLISTS Version="1.0.0"><COLLECTION Entries="0"/></DJ_PLAYLISTS>')
        const rMalo = await leerXmlApi(ctx, malo)
        afirmar(rMalo.status >= 400 && rMalo.status < 500 && rMalo.data.mensaje, `la API no rechazó el DOCTYPE con un 4xx y motivo: ${json(rMalo)}`)
        await subirXml(page, malo)
        const alerta = await hasta(() => page.$eval('.imp-dialog [role="alert"]', (e) => e.textContent).catch(() => null), (v) => v, 'no apareció el motivo del rechazo')
        igual(alerta, rMalo.data.mensaje, 'la pantalla no muestra el motivo del server para el XML con DOCTYPE')
        igual(await page.$$eval('.imp-pl', (x) => x.length), 0, 'con el XML rechazado no tiene que haber lista de playlists')

        // El XML bueno: cada playlist con «N de M encontrados» de la API y el porqué de los que faltan.
        const lectura = (await leerXmlApi(ctx, ctx.base.importar_xml)).data
        afirmar(lectura.exito && lectura.playlists.length >= 2, `la API no leyó el XML de prueba: ${json(lectura)}`)
        await subirXml(page, ctx.base.importar_xml)
        const filas = await hasta(() => leerChecklist(page), (f) => f.length === lectura.playlists.length, 'no aparecieron las playlists del XML')
        const resumen = await page.$eval('.imp-resumen', (e) => e.textContent)
        igual(resumen, `${lectura.archivo}: ${lectura.temas} tema${lectura.temas === 1 ? '' : 's'} · ${lectura.playlists.length} playlists`, 'el resumen no dice cuántos temas y playlists trae el XML')
        lectura.playlists.forEach((p, k) => {
          const f = filas[k]
          igual([f.nombre, f.carpeta, f.n, f.tildada], [p.nombre, p.carpeta || null, encontradosTxt(p), p.encontrados > 0 && !p.ya_importada],
            `la playlist ${p.ruta} no dice lo de la API (nombre, carpeta, encontrados, tildada por defecto)`)
          // Cada faltante tiene su porqué con su número: la suma de los números dichos es la de la API.
          const dichos = f.detalle.map((t) => Number((t.match(/^(\d+) /) || [])[1] || 0)).reduce((a, b) => a + b, 0)
          igual(dichos, p.ambiguos + p.faltan + p.inexistentes, `${p.ruta}: los que faltan no se explican todos (${json(f.detalle)})`)
          if (p.encontrados < p.total) afirmar(f.describe, `${p.ruta}: el checkbox no tiene descripción (aria-describedby) de los que faltan`)
        })
        // La base de juguete: «Peak» tiene un homónimo ambiguo y un TrackID que no está; «Cierre» un fantasma.
        const peak = lectura.playlists.find((p) => p.nombre === 'Peak')
        const cierre = lectura.playlists.find((p) => p.nombre === 'Cierre')
        igual([peak.encontrados, peak.ambiguos, peak.inexistentes, cierre.encontrados, cierre.faltan], [2, 1, 1, 1, 1],
          'el resolver no encontró lo que la base de juguete tiene en disco (ver tests/sinteticos.py:biblioteca_importable)')

        igual(await chicos(page, '.imp-dialog button, .imp-dialog .imp-check, .imp-dialog .imp-file'), [], 'hay controles del diálogo de menos de 44 px')
        await sinRutas(page, ctx, 'diálogo de Rekordbox')

        // Importar las tildadas: se abre la primera y arranca el análisis.
        const tildadas = filas.filter((f) => f.tildada).map((f) => f.nombre)
        const boton = await page.$eval('.imp-dialog .dialog-actions .btn-primary', (b) => b.textContent.trim())
        igual(boton, `Importar ${tildadas.length} playlist${tildadas.length === 1 ? '' : 's'}`, 'el botón no cuenta las playlists tildadas')
        await page.click('.imp-dialog .dialog-actions .btn-primary')
        await hasta(() => page.$('.imp-dialog'), (v) => !v, 'el diálogo no se cerró al importar')
        const lista = (await api(ctx, '/api/playlists')).playlists.filter((p) => p.origen === 'rekordbox')
        igual(lista.map((p) => p.nombre).sort(), [...tildadas].sort(), 'la API no tiene importadas justo las tildadas')
        await hasta(() => nombreAbierta(page), (v) => v === tildadas[0], 'no se abrió la primera playlist importada')
        const eyebrow = await page.$eval('.crate-head .eyebrow', (e) => e.textContent)
        afirmar(eyebrow.includes('Rekordbox'), `el encabezado no dice que vino de Rekordbox: ${eyebrow}`)

        // Peak: el ambiguo y los encontrados; las filas dicen lo de la API (estado, BPM/key y de dónde salen).
        const pidPeak = lista.find((p) => p.nombre === 'Peak').id
        await abrirEnRail(page, 'Peak')
        await esperarAnalisisApi(ctx, pidPeak)
        const filasPeak = await esperarFilas(page, ctx, pidPeak, 'las filas de Peak no dicen lo de la API', 15000)
        const itemsPeak = await itemsApi(ctx, pidPeak)
        const mPeak = await esperarMetricas(page, ctx, pidPeak, 'las métricas de BPM de Peak no son las de la API')
        // Ácido lo midió el motor (o no): si hay medición, la fuente es el motor y el 140 de
        // Rekordbox de «Repetido» queda afuera, no promediado con lo medido.
        const medidosPeak = itemsPeak.filter((i) => i.analisis.dato === 'motor' && i.analisis.bpm)
        igual(mPeak.bpm_fuente, medidosPeak.length ? 'motor' : 'rekordbox', 'la API no eligió la fuente más confiable')
        igual(itemsPeak.map((i) => [i.titulo, i.archivo_estado]), [['Reubicado', 'ok'], ['Ácido #1', 'ok'], ['Repetido', 'ambiguo']],
          'la API no resolvió Peak como está en disco (el 999 no está en la colección y no entra)')
        afirmar(filasPeak.find((f) => f.titulo === 'Repetido').estado.startsWith('Hay 2 archivos con ese nombre'), 'el homónimo no dice que hay dos archivos')

        // La acción 2: deshabilitada, con el motivo A LA VISTA y alcanzable con el teclado. La 3
        // (f56) ya está disponible.
        const acc = await leerAcciones(page)
        igual(acc.slice(1).map((a) => [a.titulo, a.deshabilitada, a.enfocable]), [['Armar un set', true, true], ['Exportar a Rekordbox (con cues)', false, true]],
          'Armar un set tiene que estar deshabilitado y Exportar a Rekordbox disponible (los dos alcanzables con el teclado)')
        // «Armar un set» (f55) dice el motivo de la radio: con Peak no alcanza para un set.
        const rpPeak = await api(ctx, `/api/radio/playlists/${pidPeak}`)
        igual(rpPeak.puede_armar, false, 'con Peak la radio no tendría que poder armar un set')
        await hasta(async () => (await leerAcciones(page))[1].motivo, (m) => !!m && m.startsWith(rpPeak.motivo_no_puede),
          '«Armar un set» no muestra el motivo de /api/radio/playlists/{id}')
        const antes = await dialogos(page)
        await page.evaluate(() => [...document.querySelectorAll('.crate-accion')].find((b) => b.textContent.includes('Armar un set')).click())
        igual(await dialogos(page), antes, '«Armar un set» deshabilitado abrió algo')

        // El género: el tema sin género lo dice; se cambia con el teclado y queda en la API.
        const sinG = itemsPeak.filter((i) => !(i.genero || '').trim()).length
        igual(sinG, 1, 'en la base de juguete «Repetido» es el único de Peak sin género')
        igual(await page.$eval('.crate-genero label', (l) => l.textContent).catch(() => null), '1 tema sin género:', 'no avisa cuántos temas no tienen género')
        afirmar((await leerAcciones(page))[1].motivo.includes('sin género no va'), 'Armar un set no avisa que el tema sin género no va a entrar')
        await page.focus('button[aria-label="Ponerle género a «Repetido»"]')
        await page.keyboard.press('Enter')
        await page.waitForSelector('input[aria-label="Género de «Repetido»"]', { timeout: ESPERA_MS })
        // f61: editando, el campo no es una caja de formulario (sin borde ni fondo de campo) y el
        // foco de teclado se ve (anillo); el objetivo táctil sigue de 44 px.
        const campo = await page.$eval('input[aria-label="Género de «Repetido»"]', (i) => {
          const c = getComputedStyle(i)
          return { borde: c.borderTopWidth, fondo: c.backgroundColor, anillo: c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) >= 2, alto: Math.round(i.getBoundingClientRect().height) }
        })
        igual(campo, { borde: '0px', fondo: 'rgba(0, 0, 0, 0)', anillo: true, alto: 44 }, 'el campo del género se ve como una caja o sin anillo de foco')
        await page.keyboard.type('Acid')
        await page.keyboard.press('Enter')
        await hasta(async () => (await itemsApi(ctx, pidPeak)).find((i) => i.titulo === 'Repetido').genero, (g) => g === 'Acid', 'el género no llegó a la API')
        await esperarFilas(page, ctx, pidPeak, 'la fila no muestra el género nuevo')
        // En reposo, el género es texto como BPM y KEY pero sin caja: ni fondo ni borde.
        const reposo = await page.$$eval('.results-crate .mb-genero', (bs) => bs.filter((b) => b !== document.activeElement).map((b) => {
          const c = getComputedStyle(b.querySelector('b'))
          return [c.backgroundColor, c.boxShadow]
        }))
        afirmar(reposo.length > 0 && reposo.every(([f, sh]) => f === 'rgba(0, 0, 0, 0)' && sh === 'none'), `el género en reposo tiene caja: ${json(reposo)}`)
        await hasta(() => page.evaluate(() => document.activeElement?.getAttribute('aria-label') ?? null), (v) => v && v.startsWith('Género de «Repetido»: Acid'), 'el foco no volvió al botón del género')
        igual(await page.$('.crate-genero'), null, 'con todos los temas con género, el aviso «sin género» tiene que irse')
        igual(await chicos(page, '.crate-accion, .crate-head .crate-actions .btn, .mb-genero, .results-crate .trk-status .btn, .results-crate .trk-acts .btn, .crate-analisis .btn'), [],
          'hay controles de la playlist de menos de 44 px')

        // Cierre: el que no está en disco dice «No encuentro el archivo» (no se esconde).
        const pidCierre = lista.find((p) => p.nombre === 'Cierre').id
        await abrirEnRail(page, 'Cierre')
        await esperarAnalisisApi(ctx, pidCierre)
        const filasCierre = await esperarFilas(page, ctx, pidCierre, 'las filas de Cierre no dicen lo de la API', 15000)
        const fantasma = filasCierre.find((f) => f.titulo === 'Fantasma')
        afirmar(fantasma && fantasma.estado.startsWith('No encuentro el archivo') && fantasma.status === 'No está',
          `el tema que no está en disco no dice «No encuentro el archivo»: ${json(fantasma)}`)
        await sinRutas(page, ctx, 'playlist importada')

        // Volver a leer el mismo XML: las importadas lo dicen y no vienen tildadas.
        await page.click('.pl-rail-importar')
        await page.waitForSelector('.imp-dialog[role="dialog"]', { timeout: ESPERA_MS })
        await subirXml(page, ctx.base.importar_xml)
        const relectura = (await leerXmlApi(ctx, ctx.base.importar_xml)).data
        const filas2 = await hasta(() => leerChecklist(page), (f) => f.length === relectura.playlists.length, 'no se releyó el XML')
        relectura.playlists.forEach((p, k) => {
          igual(filas2[k].tildada, p.encontrados > 0 && !p.ya_importada, `${p.ruta}: la ya importada viene tildada (o al revés)`)
          if (p.ya_importada) afirmar(filas2[k].detalle.some((t) => t.includes(`«${p.ya_importada.nombre}»`)), `${p.ruta}: no dice que ya está importada`)
        })
        igual(relectura.playlists.filter((p) => p.ya_importada).length, tildadas.length, 'la API no marca como importadas las que se importaron')
        // f56: cada playlist dice cuántos cues y loops trae de Rekordbox (los de la API).
        relectura.playlists.forEach((p, k) => {
          const partes = [p.cues ? `${p.cues} cue${p.cues === 1 ? '' : 's'}` : null, p.loops ? `${p.loops} loop${p.loops === 1 ? '' : 's'}` : null].filter(Boolean)
          const dice = filas2[k].detalle.filter((t) => t.startsWith('Trae '))
          igual(dice, partes.length ? [`Trae ${partes.join(' · ')} de Rekordbox`] : [], `${p.ruta}: no dice los cues que trae del XML`)
        })
        // «Actualizar» ofrece pisar los cues, destildado por defecto (ganan los de la página).
        igual(await page.$('.imp-pisar'), null, 'la opción de pisar cues aparece sin «Actualizar»')
        const kYa = relectura.playlists.findIndex((p) => p.ya_importada)
        await page.evaluate((k) => document.querySelectorAll('.imp-pl input[type="checkbox"]')[k].click(), kYa)
        await page.waitForSelector('.imp-actualizar input', { timeout: ESPERA_MS })
        igual(await page.$('.imp-pisar'), null, 'la opción de pisar cues aparece sin tildar «Actualizar»')
        await page.click('.imp-actualizar input')
        const pisar = await hasta(() => page.$eval('.imp-pisar input', (i) => i.checked).catch(() => null), (v) => v !== null, 'no aparece la opción de pisar los cues')
        igual(pisar, false, 'pisar los cues tiene que venir destildado')
        afirmar((await page.$eval('#imp-pisar-d', (e) => e.textContent)).includes('ganan'), 'no explica que por defecto ganan los cues de la página')

        // 400 px: el diálogo y la playlist entran sin cortarse, y el género se sigue pudiendo tocar.
        await page.setViewport({ width: 400, height: 860 })
        await hasta(() => page.evaluate(() => document.documentElement.clientWidth), (w) => w <= 400, 'el viewport no pasó a 400 px')
        let d = await desbordeDe(page)
        afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `diálogo a 400 px: hay contenido fuera del ancho: ${json(d)}`)
        await page.keyboard.press('Escape')
        await hasta(() => page.$('.imp-dialog'), (v) => !v, 'Escape no cerró el diálogo')
        const filas400 = await leerFilasPl(page)
        afirmar(filas400.length > 0 && filas400.every((f) => f.generoVisible), `a 400 px el género de cada tema no se ve: ${json(filas400)}`)
        d = await desbordeDe(page)
        afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `playlist importada a 400 px: hay contenido fuera del ancho: ${json(d)}`)
      } finally {
        await borrarImportadas(ctx)
      }
    }],

    ['métricas (f53): una playlist sin ningún BPM dice «sin medir», no un número; con BPM de la búsqueda lo rotula', async (page, ctx) => {
      const crear = await apiPedir(ctx, '/api/playlists', 'POST', { nombre: 'E2E sin BPM' })
      afirmar(crear.status === 200 && crear.data.playlist, `no pude crear la playlist: ${json(crear)}`)
      const pid = crear.data.playlist.id
      try {
        const r = await apiPedir(ctx, `/api/playlists/${pid}/items`, 'POST', { track: { titulo: 'Sin BPM', artista: 'E2E', fuente: 'youtube', url: 'https://www.youtube.com/watch?v=e2e-sinbpm' } })
        afirmar(r.status === 200, `no pude agregar el tema: ${json(r)}`)
        await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
        await abrirEnRail(page, 'E2E sin BPM')
        const m = await esperarMetricas(page, ctx, pid, 'las métricas de una playlist sin BPM no dicen «sin medir»')
        igual(m.bpm_prom, null, 'la API inventó un BPM para una playlist sin BPM')
        // Con un BPM de la búsqueda: el número con un decimal y «sin medir» como fuente.
        const r2 = await apiPedir(ctx, `/api/playlists/${pid}/items`, 'POST', { track: { titulo: 'Con BPM', artista: 'E2E', fuente: 'youtube', url: 'https://www.youtube.com/watch?v=e2e-conbpm', bpm: 131 } })
        afirmar(r2.status === 200, `no pude agregar el tema: ${json(r2)}`)
        await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
        await abrirEnRail(page, 'E2E sin BPM')
        const m2 = await esperarMetricas(page, ctx, pid, 'las métricas con un BPM de la búsqueda no son las de la API')
        igual([m2.bpm_prom, m2.bpm_fuente], [131, 'otro'], 'la API no rotula el BPM de la búsqueda como «otro»')
        igual((await metricasBpm(page))[0][0], '131', 'un BPM entero de la búsqueda ganó un decimal inventado')
      } finally {
        await apiPedir(ctx, `/api/playlists/${pid}`, 'DELETE')
      }
    }],

    ['importar (f53): carpeta — navegar sin rutas, importar, barra de progreso del análisis y BPM medido contra el generado', async (page, ctx) => {
      await borrarImportadas(ctx)
      try {
        await abrirImportar(page, ctx)
        // A la otra pestaña con el teclado (flechas, como un tablist).
        await page.focus('.imp-dialog [role="tab"][aria-selected="true"]')
        await page.keyboard.press('ArrowRight')
        await hasta(() => page.evaluate(() => [document.activeElement?.textContent, document.activeElement?.getAttribute('aria-selected')]),
          (v) => v[0] === 'Desde una carpeta de la PC' && v[1] === 'true', 'la flecha no pasó (con el foco) a la pestaña de carpeta')

        const raices = (await api(ctx, '/api/importar/carpetas')).raices
        const nombresCarpetas = () => page.$$eval('.imp-dialog .imp-carpeta .imp-pl-nombre', (s) => s.map((x) => x.textContent))
        const botonesRaiz = await hasta(nombresCarpetas, (v) => v.length > 0, 'no aparecen las carpetas de música')
        igual(botonesRaiz, raices.map((r) => r.nombre), 'las carpetas de música no son las de la API (por su nombre, sin ruta)')
        await sinRutas(page, ctx, 'lista de carpetas')

        const entrar = (nombre) => page.evaluate((n) => [...document.querySelectorAll('.imp-dialog .imp-carpeta')].find((b) => b.querySelector('.imp-pl-nombre')?.textContent === n).click(), nombre)
        // La raíz de lo importable (base_juguete.py: RAIZ_IMPORTAR) → sus subcarpetas → «Set».
        const r = raices.find((x) => x.nombre === ctx.base.importar_raiz)
        afirmar(r, `la API no lista la raíz «${ctx.base.importar_raiz}»: ${json(raices)}`)
        await entrar(r.nombre)
        const nivel1 = await api(ctx, `/api/importar/carpetas?raiz=${r.id}&ruta=`)
        await hasta(nombresCarpetas, (v) => json(v) === json(nivel1.subcarpetas.map((s) => s.nombre)), 'las subcarpetas no son las de la API')
        afirmar(nivel1.subcarpetas.some((s) => s.nombre === 'Set'), 'la API no lista la carpeta Set')
        await entrar('Set')
        const set = await api(ctx, `/api/importar/carpetas?raiz=${r.id}&ruta=Set`)
        const resumen = await hasta(() => page.$eval('.imp-dialog .imp-resumen', (e) => e.textContent).catch(() => null), (v) => v && v.startsWith(`${set.audios} audio`), 'no se abrió la carpeta Set')
        igual(resumen, `${set.audios} audios en esta carpeta · 0 subcarpetas`, 'la carpeta no dice cuántos audios tiene (según la API)')
        igual(set.audios, Object.keys(ctx.base.importar_set).length, 'la API no cuenta los audios que la base de juguete puso en Set')
        igual(await chicos(page, '.imp-dialog button, .imp-dialog .imp-check'), [], 'hay controles de la pestaña de carpeta de menos de 44 px')
        await sinRutas(page, ctx, 'carpeta abierta')

        // Importar: se abre «Set» y el análisis muestra su barra mientras corre.
        await page.evaluate(() => [...document.querySelectorAll('.imp-dialog .dialog-actions .btn-primary')].find((b) => b.textContent.includes('Importar')).click())
        await hasta(() => nombreAbierta(page), (v) => v === 'Set', 'no se abrió la playlist «Set»')
        const pid = (await api(ctx, '/api/playlists')).playlists.find((p) => p.origen === 'carpeta' && p.nombre === 'Set').id
        const vistos = []
        const fin = await hasta(async () => {
          const s = await page.evaluate(() => {
            const b = document.querySelector('.crate-analisis [role="progressbar"]')
            return { texto: document.querySelector('.crate-analisis [role="status"]')?.textContent ?? null,
              now: b?.getAttribute('aria-valuenow') ?? null, max: b?.getAttribute('aria-valuemax') ?? null }
          })
          if (s.max) vistos.push(s)
          return s
        }, (s) => s.texto && s.texto.startsWith('Análisis terminado'), 'el análisis no terminó en pantalla', ANALISIS_MS)
        afirmar(vistos.length > 0, 'mientras analizaba no se vio la barra de progreso')
        for (const v of vistos) {
          afirmar(v.max === '2' && Number(v.now) >= 0 && Number(v.now) <= 2 && /^Analizando [12] de 2/.test(v.texto), `la barra no cuenta los 2 temas de la carpeta: ${json(v)}`)
        }
        const prog = (await api(ctx, `/api/playlists/${pid}/analisis`)).progreso
        const f = prog.fallidos.length
        igual(fin.texto, `Análisis terminado: ${prog.hechos - f} de ${prog.total} analizados${f ? ` · ${f} no se ${f === 1 ? 'pudo' : 'pudieron'} analizar` : ''}`, 'el final del análisis no dice lo de la API')
        igual([prog.hechos, prog.total, f], [2, 2, 0], 'el motor no analizó los dos clics')

        // BPM con un decimal, el del motor, y cerca del BPM con que se generó cada clic.
        const filas = await esperarFilas(page, ctx, pid, 'las filas analizadas no dicen lo de la API', 15000)
        const items = await itemsApi(ctx, pid)
        for (const it of items) {
          const generado = ctx.base.importar_set[it.titulo]
          afirmar(generado, `«${it.titulo}» no es uno de los clics generados`)
          igual(it.analisis.dato, 'motor', `«${it.titulo}»: el BPM no lo midió el motor`)
          afirmar(Math.abs(it.analisis.bpm - generado) <= 1.0, `«${it.titulo}»: el motor midió ${it.analisis.bpm} BPM y se generó a ${generado}`)
        }
        afirmar(filas.every((r) => /^\d+\.\d$/.test(r.bpm) && r.fuente === null && r.estado === 'En tu PC · Analizado'), `las filas no muestran el BPM medido con un decimal: ${json(filas)}`)
        const acc = await leerAcciones(page)
        igual([acc[0].titulo, acc[0].deshabilitada], ['Marcar cues', false], 'con temas analizados, «Marcar cues» tiene que estar disponible')
        const mSet = await esperarMetricas(page, ctx, pid, 'las métricas de BPM de Set no son las de la API')
        igual([mSet.bpm_fuente, mSet.bpm_n], ['motor', 2], 'las métricas de Set no salen de lo medido por el motor')

        // Las marcas: además de los puntos de color (decorativos), el texto dice cuántas de cada tipo.
        const uno = items.find((i) => i.titulo === 'Kick Uno')
        await limpiarMarcas(ctx, uno.analisis.radio_id)
        for (const m of [{ tipo: 'cue', inicio: 1, num: 0 }, { tipo: 'cue', inicio: 2, num: 1 }, { tipo: 'loop', inicio: 4, fin: 6, num: 2 }]) {
          const r = await apiPedir(ctx, rutaMarcas(uno.analisis.radio_id), 'POST', m)
          afirmar(r.status === 201, `no pude crear la marca ${json(m)}: ${json(r)}`)
        }
        await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
        await abrirEnRail(page, 'Set')
        const mApi = (await itemsApi(ctx, pid)).find((i) => i.titulo === 'Kick Uno').analisis.marcas
        igual([mApi.pads.length, mApi.memory, mApi.loop], [2, 0, 1], 'la API no cuenta las marcas creadas')
        // f62: la vista previa de la onda dice en su nombre cuántas de cada tipo y dibuja las 3
        // (decorativas, aria-hidden); los puntos viejos ya no están.
        const marcasFila = await hasta(() => page.evaluate(() => {
          const r = [...document.querySelectorAll('.results-crate .trk')].find((x) => x.querySelector('.trk-title')?.textContent === 'Kick Uno')
          const p = r?.querySelector('.pv')
          return p ? { txt: p.getAttribute('aria-label'), puntos: p.querySelectorAll('.pv-mk').length,
            puntosOcultos: [...p.querySelectorAll('.pv-mk')].every((i) => i.closest('[aria-hidden="true"]')), viejos: r.querySelectorAll('.cue-puntos').length } : null
        }), (v) => v && v.puntos === 3, 'la fila de Kick Uno no muestra sus marcas')
        igual(marcasFila, { txt: 'Marcar cues de «Kick Uno» (2 hot cues, 1 loop)', puntos: 3, puntosOcultos: true, viejos: 0 }, 'las marcas no dicen en texto cuántas de cada tipo')
        await limpiarMarcas(ctx, uno.analisis.radio_id)

        // Sin género en los tags: no se inventa; se pone a todos con el formulario.
        igual(items.map((i) => i.genero || null), [null, null], 'la carpeta inventó un género que los tags no traen')
        await page.type('#genero-todos', 'Techno')
        await page.click('.crate-genero button[type="submit"]')
        await hasta(async () => (await itemsApi(ctx, pid)).map((i) => i.genero), (g) => json(g) === json(['Techno', 'Techno']), 'el género no llegó a los dos temas en la API')
        await esperarFilas(page, ctx, pid, 'las filas no muestran el género puesto a todos')
        igual(await page.$('.crate-genero'), null, 'el aviso «sin género» sigue con todos los temas con género')
      } finally {
        await borrarImportadas(ctx)
      }
    }],

    /* f56: «Exportar a Rekordbox (con cues)». Lo esperado: el resumen de la API (GET
       /api/playlists/{id}/rekordbox), los bytes del POST y las marcas creadas en el caso. Los
       atributos de cada POSITION_MARK son los de la prueba verificada en Rekordbox 7.2.16
       (pipeline/PRUEBA_CUES.md) con los colores del dueño en números: escritos acá, no copiados
       del front. */
    ['exportar (f56): el diálogo dice lo de la API y baja el mismo XML, con los cues y sus colores', async (page, ctx) => {
      await borrarImportadas(ctx)
      try {
        const raiz = (await api(ctx, '/api/importar/carpetas')).raices.find((x) => x.nombre === ctx.base.importar_raiz)
        const imp = await apiPedir(ctx, '/api/importar/carpeta', 'POST', { raiz: raiz.id, ruta: 'Set' })
        afirmar(imp.status === 200, `no pude importar «Set»: ${json(imp)}`)
        const pid = imp.data.id
        afirmar((await apiPedir(ctx, `/api/playlists/${pid}/analizar`, 'POST', {})).status === 200, 'no arrancó el análisis')
        await esperarAnalisisApi(ctx, pid)
        const uno = (await itemsApi(ctx, pid)).find((i) => i.titulo === 'Kick Uno')
        afirmar(uno?.analisis?.radio_id, `«Kick Uno» no quedó analizado: ${json(uno)}`)
        await limpiarMarcas(ctx, uno.analisis.radio_id)
        for (const m of [{ tipo: 'cue', inicio: 1, num: 0, nombre: 'Drop' }, { tipo: 'loop', inicio: 4, fin: 6, num: 2 },
          { tipo: 'memory', inicio: 0.5 }]) {
          const r = await apiPedir(ctx, rutaMarcas(uno.analisis.radio_id), 'POST', m)
          afirmar(r.status === 201, `no pude crear la marca ${json(m)}: ${json(r)}`)
        }
        // Un tema sin archivo en la PC: queda afuera, y el diálogo lo dice con su motivo.
        const sinArch = await apiPedir(ctx, `/api/playlists/${pid}/items`, 'POST', { track: { titulo: 'Sin archivo', artista: 'E2E', fuente: 'youtube', url: 'https://www.youtube.com/watch?v=e2e-f56' } })
        afirmar(sinArch.status === 200, `no pude agregar el tema sin archivo: ${json(sinArch)}`)
        const res = (await apiPedir(ctx, `/api/playlists/${pid}/rekordbox`)).data
        igual([res.incluidos, res.total, res.marcas, res.omitidos.map((o) => o.titulo)], [2, 3, { hot_cues: 1, memory: 1, loops: 1 }, ['Sin archivo']],
          'la API no cuenta los temas, las marcas y el omitido de «Set»')

        await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
        await abrirEnRail(page, 'Set')
        const acc = await leerAcciones(page)
        igual([acc[2].titulo, acc[2].deshabilitada, acc[2].enfocable], ['Exportar a Rekordbox (con cues)', false, true], 'la acción 3 no está disponible')
        // Con el teclado: foco en la acción y Enter.
        await page.evaluate(() => [...document.querySelectorAll('.crate-accion')].find((b) => b.textContent.includes('Exportar a Rekordbox')).focus())
        await page.keyboard.press('Enter')
        await page.waitForSelector('.rbx-dialog[role="dialog"]', { timeout: ESPERA_MS })
        const leer = () => page.evaluate(() => ({
          temas: document.querySelector('.rbx-temas')?.textContent ?? null,
          marcas: document.querySelector('.rbx-marcas')?.textContent ?? null,
          archivo: document.querySelector('.rbx-archivo')?.textContent ?? null,
          rutas: document.querySelector('.rbx-rutas')?.textContent ?? null,
          grilla: document.querySelector('.rbx-grilla input')?.checked ?? null,
          omitidos: [...document.querySelectorAll('.rbx-omitidos li')].map((l) => l.textContent),
        }))
        const pl = (n, uno1, varios) => `${n} ${n === 1 ? uno1 : varios}`
        const esperado = { temas: `${res.incluidos} de ${pl(res.total, 'tema', 'temas')}`, marcas: '1 hot cue, 1 memory cue, 1 loop',
          archivo: res.archivo, rutas: res.aviso_rutas, grilla: false, omitidos: res.omitidos.map((o) => `${o.titulo}: ${o.motivo}`) }
        await hasta(leer, (v) => json(v) === json(esperado), `el diálogo no dice lo de la API: ${json(esperado)}`)
        igual(await chicos(page, '.rbx-dialog button, .rbx-dialog .imp-check'), [], 'hay controles del diálogo de exportar de menos de 44 px')
        await sinRutas(page, ctx, 'diálogo de exportar')

        // La descarga: los mismos bytes y el mismo nombre que el POST de la API.
        const r = await fetch(`${ctx.url}/api/playlists/${pid}/rekordbox`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        afirmar(r.ok, `POST /rekordbox contestó ${r.status}`)
        const esperadoBytes = Buffer.from(await r.arrayBuffer())
        const nombre = decodeURIComponent((/filename\*=UTF-8''([^;]+)/.exec(r.headers.get('content-disposition') || '') || [])[1] || '')
        igual(nombre, res.archivo, 'el nombre del archivo de la API')
        const dir = fs.mkdtempSync(path.join(ctx.tmp, 'descargas-rbx-'))
        const cdp = await page.createCDPSession()
        await cdp.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: dir, eventsEnabled: true })
        const bajadas = []
        cdp.on('Browser.downloadWillBegin', (e) => bajadas.push({ guid: e.guid, nombre: e.suggestedFilename, estado: 'empezó' }))
        cdp.on('Browser.downloadProgress', (e) => { const d = bajadas.find((x) => x.guid === e.guid); if (d) d.estado = e.state })
        await page.click('.rbx-descargar')
        const [d] = await hasta(async () => bajadas, (ds) => ds.length > 0 && ds.every((x) => x.estado === 'completed' || x.estado === 'canceled'),
          'tocar «Descargar el XML» no terminó ninguna descarga')
        igual([bajadas.length, d.estado, d.nombre], [1, 'completed', nombre], 'la descarga del XML')
        const bajado = fs.readFileSync(path.join(dir, d.nombre))
        afirmar(Buffer.compare(bajado, esperadoBytes) === 0, `el XML bajado no es el de la API (${bajado.length} vs ${esperadoBytes.length} bytes)`)
        // Las marcas de «Kick Uno» en el XML: los valores de la prueba verificada.
        const marcas = await page.evaluate((txt) => {
          const doc = new DOMParser().parseFromString(txt, 'application/xml')
          if (doc.querySelector('parsererror')) return 'XML roto'
          const t = [...doc.querySelectorAll('COLLECTION > TRACK')].find((x) => x.getAttribute('Name') === 'Kick Uno')
          return t ? [...t.querySelectorAll('POSITION_MARK')].map((p) => Object.fromEntries([...p.attributes].map((a) => [a.name, a.value]))) : null
        }, bajado.toString('utf8'))
        igual(marcas, [
          { Name: 'Drop', Type: '0', Start: '1.000', Num: '0', Red: '255', Green: '77', Blue: '90' },
          { Name: '', Type: '4', Start: '4.000', End: '6.000', Num: '2', Red: '255', Green: '154', Blue: '46' },
          { Name: '', Type: '0', Start: '0.500', Num: '-1' },
        ], 'las marcas del XML no son las creadas (o no tienen los colores del dueño)')
        afirmar(!bajado.toString('utf8').includes('<TEMPO'), 'sin tildar la grilla, el XML no lleva TEMPO')
        const hecho = await hasta(() => page.$eval('.rbx-hecho', (e) => e.textContent).catch(() => null), (v) => v, 'no dice que se descargó')
        afirmar(hecho.includes(nombre) && hecho.includes('rekordbox xml'), `el aviso no nombra el archivo ni cómo importarlo: ${hecho}`)

        // La grilla: tildarla muestra el aviso de que es ESTIMADA (el de la API).
        await page.click('.rbx-grilla input')
        igual(await page.$eval('#rbx-grilla-d', (e) => e.textContent), res.aviso_grilla, 'el aviso de la grilla estimada no es el de la API')

        // 400 px sin desborde; Escape cierra y el foco vuelve a la acción.
        await page.setViewport({ width: 400, height: 860 })
        await hasta(() => page.evaluate(() => document.documentElement.clientWidth), (w) => w <= 400, 'el viewport no pasó a 400 px')
        const dsb = await desbordeDe(page)
        afirmar(dsb.scroll <= dsb.ancho && dsb.fuera.length === 0, `diálogo de exportar a 400 px: hay contenido fuera del ancho: ${json(dsb)}`)
        await page.keyboard.press('Escape')
        await hasta(() => page.$('.rbx-dialog'), (v) => !v, 'Escape no cerró el diálogo')
        await hasta(() => page.evaluate(() => document.activeElement?.querySelector?.('.crate-accion-t')?.textContent ?? null),
          (v) => v === 'Exportar a Rekordbox (con cues)', 'el foco no volvió a la acción de exportar')
        await limpiarMarcas(ctx, uno.analisis.radio_id)
      } finally {
        await borrarImportadas(ctx)
      }
    }],
  ]
}

/* ============================================================================
   f54 — el editor grande de cues (opción D) desde «Mis playlists».

   Los temas son los WAV generados de base_juguete.editor_d (BPM, key y dónde cae cada kick
   conocidos por construcción); cada caso los da de alta en la base del motor, los importa como
   la carpeta «Editor» y al final deshace todo. Lo esperado sale de la API (/marcas, /onda3) o
   del generador (dónde suena cada kick); nada del código del front.
   ========================================================================== */
function casosF54() {
  const tituloDe = (t) => t.archivo.replace(/\.wav$/i, '')
  const coma2 = (x) => Number(x).toFixed(2).replace('.', ',')

  const preparar = async (ctx) => {
    mutarBase(ctx, 'editor_alta', json(ctx.base.editor_d))
    const raices = (await api(ctx, '/api/importar/carpetas')).raices
    const raiz = raices.findIndex((r) => r.nombre === ctx.base.importar_raiz)
    afirmar(raiz >= 0, `la API no lista la raíz «${ctx.base.importar_raiz}»`)
    const r = await apiPedir(ctx, '/api/importar/carpeta', 'POST', { raiz: String(raiz), ruta: ctx.base.editor_carpeta })
    afirmar(r.status === 200 && r.data.exito, `no pude importar la carpeta del editor: ${json(r)}`)
    const items = (await api(ctx, `/api/playlists/${r.data.id}`)).data.items
    const por = {}
    for (const [k, t] of Object.entries(ctx.base.editor_d)) {
      const item = items.find((i) => i.titulo === tituloDe(t))
      afirmar(item && item.analisis.estado === 'analizado' && item.analisis.radio_id, `«${k}» no quedó analizado en la playlist: ${json(item)}`)
      await limpiarMarcas(ctx, item.analisis.radio_id)
      por[k] = { ...t, item, rid: item.analisis.radio_id }
    }
    return { pid: r.data.id, items, por }
  }
  const desarmar = async (ctx, p) => {
    if (p) {
      for (const x of Object.values(p.por)) await limpiarMarcas(ctx, x.rid).catch(() => {})
      await apiPedir(ctx, `/api/playlists/${p.pid}`, 'DELETE')
    }
    mutarBase(ctx, 'editor_baja', json(ctx.base.editor_d))
  }
  const conEditor = async (ctx, fn) => {
    let p = null
    try { p = await preparar(ctx); await fn(p) } finally { await desarmar(ctx, p) }
  }

  const abrirPlaylist = async (page, ctx) => {
    await page.goto(`${ctx.url}/`, { waitUntil: 'domcontentloaded' })
    await hasta(() => page.evaluate(() => [...document.querySelectorAll('.pl-item')].some((b) => b.querySelector('.pl-item-name')?.textContent === 'Editor')),
      (v) => v, 'la playlist «Editor» no aparece en el rail')
    await page.evaluate(() => [...document.querySelectorAll('.pl-item')].find((b) => b.querySelector('.pl-item-name')?.textContent === 'Editor').click())
    await hasta(() => page.$eval('.crate-head .name-edit', (i) => i.value).catch(() => null), (v) => v === 'Editor', 'no se abrió la playlist «Editor»')
  }
  // Al pasar de tema (AvPág/RePág) la onda vieja NO se borra en el mismo render que el título:
  // la borra el efecto «cambiar de tema» de EditorCues, que corre después. Entre los dos hay
  // renders con el título nuevo y la grilla del tema anterior (medido: ~15 ms), y esperar solo
  // «no dice Leyendo» los toma por la onda cargada. `pasarTema` aprieta la tecla con un
  // observador ya puesto que anota cuándo aparece «Leyendo el audio…» (el render del efecto,
  // siempre antes de que llegue la onda nueva); con `trasCambio`, `esperarOnda` exige haberlo
  // visto: la onda que se ve después es la del tema nuevo, nunca la anterior.
  const pasarTema = async (page, tecla) => {
    await page.evaluate(() => {
      window.__eddLeyendo = false
      const obs = new MutationObserver(() => {
        if (document.querySelector('.edd-msg')?.textContent?.includes('Leyendo')) { window.__eddLeyendo = true; obs.disconnect() }
      })
      obs.observe(document.querySelector('.edd'), { subtree: true, childList: true, characterData: true })
    })
    await page.keyboard.press(tecla)
  }
  const esperarOnda = (page, que, trasCambio = false) => hasta(() => page.evaluate(() => ({
    msg: document.querySelector('.edd-msg')?.textContent ?? null,
    estado: document.querySelector('.edd-grilla-estado')?.textContent ?? null,
    leyendo: window.__eddLeyendo === true,
  })), (v) => !v.msg && v.estado && !v.estado.includes('Leyendo') && (!trasCambio || v.leyendo),
  `${que}: la onda no terminó de cargar`, 20000)
  // Abre el editor desde la fila del tema (f62: la vista previa de la onda, cuyo nombre es
  // «Marcar cues de «tema»» seguido del resumen de sus marcas entre paréntesis).
  const abrirEditorD = async (page, ctx, titulo) => {
    await abrirPlaylist(page, ctx)
    const etiqueta = `Marcar cues de «${titulo}»`
    const boton = (l) => [...document.querySelectorAll('button')].find((b) => {
      const a = b.getAttribute('aria-label') || ''
      return (a === l || a.startsWith(`${l} (`)) && window.__visible(b)
    })
    await hasta(() => page.evaluate((l, f) => Boolean(new Function(`return ${f}`)()(l)), etiqueta, boton.toString()),
      (v) => v, `no está el botón «${etiqueta}»`)
    await page.evaluate((l, f) => new Function(`return ${f}`)()(l).click(), etiqueta, boton.toString())
    await page.waitForSelector('.edd[role="dialog"]', { timeout: ESPERA_MS })
    await hasta(() => page.$eval('#edd-titulo', (h) => h.textContent).catch(() => null), (v) => v && v.startsWith(titulo), `el editor no abrió «${titulo}»`)
    await esperarOnda(page, titulo)
  }
  // f62: lo que muestra la vista previa de la fila del tema `rid` (posiciones en % de la caja,
  // medidas en pantalla, y los colores pintados), y los --cue-* de :root resueltos por el navegador.
  const leerPreview = (page, rid) => page.evaluate((id) => {
    const b = document.querySelector(`.pv[data-pv="${id}"]`)
    if (!b) return null
    const caja = b.querySelector('.pv-caja').getBoundingClientRect()
    const color = (v) => { const e = document.createElement('i'); e.style.color = `var(${v})`; document.body.appendChild(e); const c = getComputedStyle(e).color; e.remove(); return c }
    const s = b.querySelector('svg.pv-onda')
    const graves = s ? [...(s.querySelector('.pv-graves')?.getAttribute('d') ?? '').matchAll(/M(\d+) /g)].map((m) => Number(m[1])) : []
    return {
      fase: (b.className.match(/\bis-(\w+)/) || [])[1] ?? null, tag: b.tagName, tabIndex: b.tabIndex,
      label: b.getAttribute('aria-label'), title: b.getAttribute('title') ?? '', texto: b.querySelector('.pv-txt')?.textContent ?? null,
      dots: b.closest('.trk').querySelectorAll('.cue-puntos').length,
      svg: s ? { vb: s.getAttribute('viewBox'), ancho: s.getBoundingClientRect().width / caja.width, graves } : null,
      marcas: [...b.querySelectorAll('.pv-mk')].map((m) => {
        const r = m.getBoundingClientRect()
        const cs = getComputedStyle(m)
        return { id: Number(m.dataset.id), tipo: m.dataset.tipo, x: ((r.left - caja.left) / caja.width) * 100, w: (r.width / caja.width) * 100,
          centro: (((r.left + r.right) / 2 - caja.left) / caja.width) * 100, fondo: cs.backgroundColor, arriba: cs.borderTopColor, izq: cs.borderLeftColor }
      }),
      colores: { cues: [1, 2, 3, 4, 5, 6, 7, 8].map((n) => color(`--cue-${n}`)), mem: color('--cue-mem'), loop: color('--cue-loop') },
    }
  }, rid)
  const onda3Api = async (ctx, rid) => (await apiPedir(ctx, `/api/radio/tracks/${rid}/onda3?puntos=10`)).data
  const leerOnda = (page) => page.evaluate(() => {
    const o = document.querySelector('.edd-onda')
    return {
      desde: Number(o.dataset.desde), hasta: Number(o.dataset.hasta),
      beats: [...document.querySelectorAll('.edd-beat')].map((b) => ({ t: Number(b.dataset.t), left: parseFloat(b.style.left), compas: b.classList.contains('is-compas') })),
      atenuada: o.classList.contains('is-atenuada'),
      zooms: [...document.querySelectorAll('.edd-zoom button[aria-pressed]')].map((b) => [b.textContent, b.getAttribute('aria-pressed')]),
      unidad: document.querySelector('.edd-zoom-u')?.textContent ?? null,
      estado: document.querySelector('.edd-grilla-estado')?.textContent ?? null,
      estadoClase: document.querySelector('.edd-grilla-estado')?.className ?? null,
      snap: document.querySelector('.edd-grilla-ctrl input[type=checkbox]') ? { on: document.querySelector('.edd-grilla-ctrl input[type=checkbox]').checked, off: document.querySelector('.edd-grilla-ctrl input[type=checkbox]').disabled } : null,
    }
  })
  // Las líneas que tiene que haber en [desde, hasta]: primer beat (+ ajuste) + j·período, de la API.
  const lineasEsperadas = (g, off, desde, hasta) => {
    const f = g.primer_beat_s + off
    const out = []
    for (let j = Math.ceil((desde - f) / g.periodo_s - 1e-9); f + j * g.periodo_s <= hasta + 1e-9; j++) {
      const t = f + j * g.periodo_s
      if (t >= 0) out.push(t)
    }
    return out
  }
  const mismasLineas = (real, esp, v, que) => {
    igual(real.length, esp.length, `${que}: cantidad de líneas (pantalla ${json(real.map((b) => b.t))}, API ${json(esp)})`)
    real.forEach((b, k) => {
      afirmar(Math.abs(b.t - esp[k]) < 1e-5, `${que}: la línea ${k} está en ${b.t} y la API dice ${esp[k]}`)
      const pct = ((esp[k] - v.desde) / (v.hasta - v.desde)) * 100
      afirmar(Math.abs(b.left - pct) < 0.01, `${que}: la línea ${k} está dibujada en ${b.left}% y le toca ${pct}%`)
    })
  }
  const clicZoom = async (page, txt) => {
    await page.evaluate((t) => [...document.querySelectorAll('.edd-zoom button[aria-pressed]')].find((b) => b.textContent === t).click(), txt)
  }
  const reloj = (page) => page.$eval('.edd-reloj', (e) => e.textContent)
  // Aprieta (con modificadores) y espera la escritura de marcas que tiene que disparar.
  const conTecla = async (page, teclas, metodo) => {
    const r = page.waitForResponse((x) => x.request().method() === metodo && /\/api\/radio\/tracks\/[0-9a-f]{16}\/marcas/.test(new URL(x.url()).pathname), { timeout: ESPERA_MS })
    const [mods, k] = [teclas.slice(0, -1), teclas[teclas.length - 1]]
    for (const m of mods) await page.keyboard.down(m)
    await page.keyboard.press(k)
    for (const m of [...mods].reverse()) await page.keyboard.up(m)
    const res = await r
    return { status: res.status(), data: await res.json() }
  }
  // Las filas de la lista como se ven, y como tienen que verse según la API.
  // La lista compacta (crítica de f54): una fila por marca, EN ORDEN DE TIEMPO, con el rótulo, el
  // tiempo (o entrada → salida) y el nombre (data-nombre: el texto visible dice «sin nombre»).
  const leerFilas = (page) => page.$$eval('.edd-fila[data-id]', (rs) => rs.map((r) => ({
    id: Number(r.dataset.id), tipo: r.dataset.tipo, rotulo: r.querySelector('.edd-tag')?.textContent ?? null,
    tiempos: (r.querySelector('.edd-fila-t')?.textContent ?? '').split(' → '), nombre: r.dataset.nombre ?? null,
  })))
  const PADS = 'ABCDEFGH'
  // Despliega la fila de una marca para editarla (tipo, pad, tiempos, nombre, «Mover acá»).
  const abrirFila = async (page, id) => {
    const b = `.edd-fila[data-id="${id}"] .edd-editar`
    await page.waitForSelector(b, { timeout: ESPERA_MS })
    if (await page.$eval(b, (x) => x.getAttribute('aria-expanded')) !== 'true') await page.click(b)
    await page.waitForSelector(`.edd-fila[data-id="${id}"] .edd-fila-ed`, { timeout: ESPERA_MS })
  }
  const filasEsperadas = (marcas) => [...marcas].sort((p, q) => p.inicio - q.inicio || p.id - q.id).map((m) => ({
    id: m.id, tipo: m.tipo,
    rotulo: m.tipo === 'cue' ? PADS[m.num] : m.tipo === 'memory' ? 'M' : Number.isInteger(m.num) ? `Loop ${PADS[m.num]}` : 'Loop',
    tiempos: [m.inicio, ...(m.tipo === 'loop' ? [m.fin] : [])].map(tiempoTabla), nombre: m.nombre || '',
  }))
  // Espera a que la pantalla y la API coincidan, releyendo LAS DOS en cada intento: una API
  // leída una sola vez podía ser de antes de que el server procesara la escritura.
  const filasComoApi = async (page, ctx, rid, que) => {
    const x = await hasta(async () => {
      const api_ = await marcasApi(ctx, rid)
      return { api_, pantalla: await leerFilas(page), esperada: filasEsperadas(api_) }
    }, (v) => json(v.pantalla) === json(v.esperada), `${que}: la lista no es la de la API`)
    return x.api_
  }
  const chicos = (page, sel) => page.$$eval(sel, (els) => els.filter((e) => window.__visible(e)).map((e) => {
    const r = e.getBoundingClientRect()
    return { el: `${e.tagName.toLowerCase()}.${String(e.className).split(' ')[0]} «${(e.getAttribute('aria-label') || e.textContent).trim().slice(0, 30)}»`, w: Math.round(r.width), h: Math.round(r.height) }
  }).filter((x) => x.w < 44 || x.h < 44))
  // Qué fracción de la altura de la onda ocupa el color de los GRAVES (el de su muestra en la
  // leyenda) en la columna de cada tiempo.
  const alturaGraves = (page, tiempos) => page.evaluate((ts) => {
    const cv = document.querySelector('.edd-onda canvas')
    const o = document.querySelector('.edd-onda')
    const [desde, hasta] = [Number(o.dataset.desde), Number(o.dataset.hasta)]
    const m = getComputedStyle(document.querySelector('.edd-sw.is-graves')).backgroundColor.match(/\d+/g).map(Number)
    const c = cv.getContext('2d')
    return ts.map((t) => {
      const col = Math.round(((t - desde) / (hasta - desde)) * cv.width)
      const d = c.getImageData(col, 0, 1, cv.height).data
      let n = 0
      for (let i = 0; i < d.length; i += 4) if (Math.abs(d[i] - m[0]) < 8 && Math.abs(d[i + 1] - m[1]) < 8 && Math.abs(d[i + 2] - m[2]) < 8) n++
      return n / cv.height
    })
  }, tiempos)

  return [
    /* f62: la vista previa de la onda en cada fila de Mis playlists. Lo esperado sale de la API
       (/marcas: tiempos, tipo y pad; track.dur: el eje; /preview: las bandas) y de los colores
       --cue-* de la tabla única (los lee el navegador de :root, no del código del front). */
    ['preview (f62): la onda de cada fila con las marcas de la API en su lugar y con SU color; Enter la abre en el editor D y el foco vuelve', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        const crear = [{ tipo: 'cue', inicio: 10, num: 0 }, { tipo: 'cue', inicio: 40.5, num: 2 }, { tipo: 'cue', inicio: 85, num: 7 },
          { tipo: 'memory', inicio: 60 }, { tipo: 'loop', inicio: 20, fin: 30, num: 4 }, { tipo: 'loop', inicio: 70, fin: 75.25 }]
        for (const m of crear) {
          const r = await apiPedir(ctx, rutaMarcas(t.rid), 'POST', m)
          afirmar(r.status === 201, `no pude crear la marca ${json(m)}: ${json(r)}`)
        }
        const mApi = (await apiPedir(ctx, rutaMarcas(t.rid))).data
        const dur = mApi.track.dur
        const prev = await apiPedir(ctx, `/api/radio/tracks/${t.rid}/preview?puntos=160`)
        afirmar(prev.status === 200 && prev.data.bandas, `/preview no contestó la onda: ${json(prev)}`)
        await abrirPlaylist(page, ctx)
        const v = await hasta(() => leerPreview(page, t.rid), (x) => x && x.fase === 'ok' && x.marcas.length === crear.length,
          'la vista previa de «Con grilla» no dibujó la onda con sus marcas')
        // Botón enfocable, con nombre que dice qué abre y cuántas de cada una (contrato de la playlist).
        igual([v.tag, v.tabIndex, v.label], ['BUTTON', 0, `Marcar cues de «${t.item.titulo}» (3 hot cues, 1 memory, 2 loops)`],
          'la vista previa no es un botón enfocable con el resumen de las marcas')
        igual(v.dots, 0, 'quedaron los puntos viejos de «● N hot cue» en la fila')
        // Cada marca en su lugar (eje = track.dur de la API) y con su color.
        const pct = (s) => (s / dur) * 100
        const TOL = 0.4      // % del ancho de 140 px ≈ 0,56 px
        for (const m of mApi.marcas) {
          const p = v.marcas.find((x) => x.id === m.id)
          afirmar(p, `la marca ${m.id} (${m.tipo} en ${m.inicio}) no está en la vista previa`)
          igual(p.tipo, m.tipo, `la marca ${m.id} se dibuja como ${p.tipo}`)
          if (m.tipo === 'cue') {
            afirmar(Math.abs(p.centro - pct(m.inicio)) < TOL, `el hot cue ${m.num + 1} está en ${p.centro}% y le toca ${pct(m.inicio)}%`)
            igual(p.fondo, v.colores.cues[m.num], `el hot cue del pad ${m.num + 1} no tiene el color de su pad (--cue-${m.num + 1})`)
          } else if (m.tipo === 'memory') {
            afirmar(Math.abs(p.x - pct(m.inicio)) < TOL, `la memory está en ${p.x}% y le toca ${pct(m.inicio)}%`)
            igual(p.izq, v.colores.mem, 'la memory no tiene el color neutro (--cue-mem)')
          } else {
            afirmar(Math.abs(p.x - pct(m.inicio)) < TOL && Math.abs(p.w - (pct(m.fin) - pct(m.inicio))) < TOL,
              `el loop ${m.inicio}→${m.fin} ocupa ${p.x}% + ${p.w}% y le toca ${pct(m.inicio)}% + ${pct(m.fin) - pct(m.inicio)}%`)
            igual(p.arriba, v.colores.loop, `el loop${Number.isInteger(m.num) ? ` del pad ${m.num + 1}` : ''} no es naranja (--cue-loop)`)
          }
        }
        afirmar(new Set(v.marcas.filter((m) => m.tipo === 'cue').map((m) => m.fondo)).size === 3, `los tres hot cues tienen que tener tres colores distintos: ${json(v.marcas)}`)
        // La onda: los puntos de /preview, en el tramo del audio sobre el eje de la biblioteca.
        afirmar(v.svg && v.svg.vb === `0 0 ${prev.data.puntos} 24`, `la onda no tiene los ${prev.data.puntos} puntos de /preview: ${json(v.svg)}`)
        afirmar(Math.abs(v.svg.ancho - Math.min(1, prev.data.duracion_audio / dur)) < 0.01, `la onda ocupa ${v.svg.ancho} del ancho y el audio dura ${prev.data.duracion_audio} de ${dur} s`)
        const g = prev.data.bandas.graves
        const sinGraves = g.map((x, i) => (x === 0 ? i : -1)).filter((i) => i >= 0)
        const fuertes = g.map((x, i) => (x >= 0.25 ? i : -1)).filter((i) => i >= 0)
        afirmar(fuertes.length > 0, 'el tema generado no tiene graves en /preview')
        afirmar(fuertes.every((i) => v.svg.graves.includes(i)), 'hay columnas con graves fuertes en /preview sin barra en la vista previa')
        afirmar(sinGraves.every((i) => !v.svg.graves.includes(i)), 'hay barras de graves donde /preview dice 0 (onda inventada)')
        // Teclado: foco + Enter abre el editor D en ESTE tema; Esc devuelve el foco a la vista previa.
        await page.evaluate((rid) => document.querySelector(`.pv[data-pv="${rid}"]`).focus(), t.rid)
        await page.keyboard.press('Enter')
        await page.waitForSelector('.edd[role="dialog"]', { timeout: ESPERA_MS })
        await hasta(() => page.$eval('#edd-titulo', (h) => h.textContent).catch(() => null), (x) => x && x.startsWith(t.item.titulo), 'Enter en la vista previa no abrió el editor D de «Con grilla»')
        await esperarOnda(page, t.item.titulo)
        await cerrarEditorD(page)
        await hasta(() => page.evaluate(() => document.activeElement?.dataset?.pv ?? null), (x) => x === t.rid, 'al cerrar, el foco no volvió a la vista previa')
      })
    }],

    ['preview (f62): lo que se marca en el editor D llega a la vista previa al cerrar, y también lo que se guarda DESPUÉS de cerrar', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Sin grilla']
        const pedidosMarcas = []
        let demorar = false
        await interceptar(page, async (req) => {
          const ruta = new URL(req.url()).pathname === rutaMarcas(t.rid)
          if (ruta && req.method() === 'GET') pedidosMarcas.push(Date.now())
          if (!demorar || req.method() !== 'POST' || !ruta) return false
          demorar = false
          // Vuelve DESPUÉS de la relectura que hace la playlist al cerrar el editor.
          await new Promise((res) => setTimeout(res, 2000))
          await req.continue()
          return true
        })
        await abrirPlaylist(page, ctx)
        await hasta(() => leerPreview(page, t.rid), (x) => x && x.fase === 'ok' && x.marcas.length === 0, 'la vista previa de «Sin grilla» no cargó (sin marcas)')
        // Clic (mouse) en la vista previa: abre ESE tema.
        await page.click(`.pv[data-pv="${t.rid}"]`)
        await page.waitForSelector('.edd[role="dialog"]', { timeout: ESPERA_MS })
        await hasta(() => page.$eval('#edd-titulo', (h) => h.textContent).catch(() => null), (x) => x && x.startsWith(t.item.titulo), 'el clic no abrió el editor D de «Sin grilla»')
        await esperarOnda(page, t.item.titulo)
        await page.focus('.edd-onda')
        const r1 = await teclaQueGuarda(page, '1')
        igual(r1.status, 201, 'el hot cue A no se guardó')
        await cerrarEditorD(page)
        const comoApi = async (n) => {
          const m = await marcasApi(ctx, t.rid)
          return hasta(() => leerPreview(page, t.rid), (x) => x && x.fase === 'ok' && m.length === n && x.marcas.length === n
            && m.every((a) => x.marcas.some((p) => p.id === a.id)), `la vista previa no muestra las ${n} marcas de la API (${json(m.map((a) => a.id))})`)
        }
        let v = await comoApi(1)
        igual([v.label, v.marcas[0].fondo], [`Marcar cues de «${t.item.titulo}» (1 hot cue)`, v.colores.cues[0]], 'el hot cue nuevo no está con el color del pad A')
        // Tarde: el POST vuelve 2 s después de cerrar; la relectura del cierre no lo ve.
        await page.click(`.pv[data-pv="${t.rid}"]`)
        await page.waitForSelector('.edd[role="dialog"]', { timeout: ESPERA_MS })
        await esperarOnda(page, 'Sin grilla (2)')
        await page.focus('.edd-onda')
        demorar = true
        const post = page.waitForResponse((x) => x.request().method() === 'POST' && new URL(x.url()).pathname === rutaMarcas(t.rid), { timeout: ESPERA_MS })
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('2')
        await page.keyboard.press('Escape')
        await hasta(() => page.$('.edd'), (x) => !x, 'Esc tenía que cerrar (la escritura todavía no volvió)')
        const antes = pedidosMarcas.length
        igual((await post).status(), 201, 'el POST demorado tenía que guardarse')
        v = await comoApi(2)
        afirmar(pedidosMarcas.length > antes, 'la vista previa no volvió a leer las marcas cuando el cambio tardío se confirmó')
        igual(v.label, `Marcar cues de «${t.item.titulo}» (2 hot cues)`, 'el resumen no cuenta el hot cue que se guardó tarde')
      })
    }],

    ['preview (f62): solo se piden las filas a la vista; sin archivo no hay onda inventada; una onda que no se lee lo dice', async (page, ctx) => {
      await conEditor(ctx, async ({ pid, por }) => {
        const pedidos = []     // `${tipo} ${rid}` de /preview y /marcas, en orden
        const sinArchivo = por['Sin BPM']
        const roto = por['Sin grilla']
        await interceptar(page, async (req) => {
          const u = new URL(req.url())
          const m = /^\/api\/radio\/tracks\/([0-9a-f]{16})\/(preview|marcas)$/.exec(u.pathname)
          if (m && req.method() === 'GET') {
            pedidos.push(`${m[2]} ${m[1]}`)
            if (m[2] === 'preview' && m[1] === roto.rid) {
              await req.respond({ status: 422, contentType: 'application/json', body: JSON.stringify({ error: 'no pude leer el audio para dibujar la onda: prueba' }) })
              return true
            }
          }
          // La playlist como la manda la API, con un tema que dice «no encuentro el archivo».
          if (u.pathname === `/api/playlists/${pid}` && req.method() === 'GET') {
            const r = await apiPedir(ctx, u.pathname)
            for (const it of r.data.data.items) if (it.analisis?.radio_id === sinArchivo.rid) it.archivo_estado = 'no-encontrado'
            await req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(r.data) })
            return true
          }
          return false
        })
        await page.setViewport({ width: 1440, height: 420 })
        await abrirPlaylist(page, ctx)
        await page.evaluate(() => window.scrollTo(0, 0))
        const filas = () => page.evaluate(() => [...document.querySelectorAll('.results-crate .trk')].map((r) => {
          const p = r.querySelector('.pv, .pv-vacio')
          const b = p.getBoundingClientRect()
          return { titulo: r.querySelector('.trk-title').textContent, rid: p.dataset.pv ?? null, visible: b.bottom > 0 && b.top < innerHeight,
            vacia: p.classList.contains('pv-vacio') ? p.textContent : null, onda: Boolean(p.querySelector('svg')) }
        }))
        // Una vuelta de red: lo que se fuera a pedir ya salió.
        const f0 = await hasta(filas, (f) => f.length === 3, 'la playlist no muestra sus 3 temas')
        await page.evaluate(() => fetch('/api/playlists').then((r) => r.text()))
        const fuera = f0.filter((f) => !f.visible && f.rid)
        afirmar(fuera.length > 0, `todas las filas están a la vista: el caso no probaría la carga perezosa (${json(f0)})`)
        igual(pedidos.filter((p) => fuera.some((f) => p.endsWith(f.rid))), [], 'se pidió la onda o las marcas de filas que no están a la vista')
        // Sin archivo: el lugar lo dice y no se pide nada de ese tema.
        const sa = f0.find((f) => f.titulo === sinArchivo.item.titulo)
        igual([sa.vacia, sa.onda, sa.rid], ['sin archivo', false, null], 'el tema sin archivo tiene que decir «sin archivo», sin onda')
        // De a una: cada fila que entra en pantalla pide SU onda y SUS marcas, una vez.
        for (const f of f0.filter((x) => x.rid)) {
          await page.evaluate((rid) => document.querySelector(`.pv[data-pv="${rid}"]`).scrollIntoView({ block: 'center' }), f.rid)
          await hasta(async () => pedidos.filter((p) => p.endsWith(f.rid)).sort(), (p) => json(p) === json([`marcas ${f.rid}`, `preview ${f.rid}`]),
            `«${f.titulo}» entró en pantalla y no pidió su onda y sus marcas (una vez cada una)`)
        }
        igual(pedidos.filter((p) => p.endsWith(sinArchivo.rid)), [], 'se pidió la onda del tema sin archivo')
        // La onda que no se lee: «sin onda» con el motivo, y nada dibujado.
        const r = await hasta(() => leerPreview(page, roto.rid), (x) => x && x.fase === 'error', 'la vista previa con /preview en 422 no dijo nada')
        afirmar(r.texto === 'sin onda' && r.title.includes('no pude leer el audio para dibujar la onda: prueba') && !r.svg,
          `la onda que no se lee tiene que decir «sin onda» con el motivo, sin dibujo: ${json(r)}`)
        // Volver a pasar por una fila ya cargada no la vuelve a pedir.
        const n = pedidos.length
        await page.evaluate(() => window.scrollTo(0, 0))
        await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
        await page.evaluate(() => fetch('/api/playlists').then((x) => x.text()))
        igual(pedidos.length, n, 'una fila ya cargada volvió a pedir su onda o sus marcas')
      })
    }],

    ['preview (f62, auditoría): sin analizar con marcas importadas muestra el estado y los puntos; un 409 y la base ocupada no quedan pegados', async (page, ctx) => {
      await conEditor(ctx, async ({ pid, por }) => {
        const pendiente = por['Con grilla']      // se ve «pendiente», con marcas reales en la base
        const analizando = por['Sin BPM']
        const ocupado = por['Sin grilla']        // /preview 409 y /marcas «base ocupada» la primera vez
        for (const m of [{ tipo: 'cue', inicio: 1, num: 0 }, { tipo: 'cue', inicio: 2, num: 1 }, { tipo: 'loop', inicio: 4, fin: 6, num: 2 }]) {
          const r = await apiPedir(ctx, rutaMarcas(pendiente.rid), 'POST', m)
          afirmar(r.status === 201, `no pude crear la marca ${json(m)}: ${json(r)}`)
        }
        const pedidos = []
        let ocupadaPreview = true
        let ocupadaMarcas = true
        await interceptar(page, async (req) => {
          const u = new URL(req.url())
          const m = /^\/api\/radio\/tracks\/([0-9a-f]{16})\/(preview|marcas)$/.exec(u.pathname)
          if (m && req.method() === 'GET' && m[1] === ocupado.rid) {
            pedidos.push(m[2])
            if (m[2] === 'preview' && ocupadaPreview) {
              ocupadaPreview = false
              await req.respond({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: 'La biblioteca está ocupada.' }) })
              return true
            }
            if (m[2] === 'marcas' && ocupadaMarcas) {
              ocupadaMarcas = false
              // Lo que contesta el server con la base ocupada (un scan): 200 sin track.
              await req.respond({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ configurada: true, estado: 'base-ocupada', motivo: 'La biblioteca está ocupada.', track: null, marcas: [], limites: null }) })
              return true
            }
          }
          // La playlist como la manda la API, con un tema «pendiente» y otro «analizando».
          if (u.pathname === `/api/playlists/${pid}` && req.method() === 'GET') {
            const r = await apiPedir(ctx, u.pathname)
            for (const it of r.data.data.items) {
              if (it.analisis?.radio_id === pendiente.rid) it.analisis.estado = 'pendiente'
              if (it.analisis?.radio_id === analizando.rid) it.analisis.estado = 'analizando'
            }
            await req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(r.data) })
            return true
          }
          return false
        })
        const items = (await api(ctx, `/api/playlists/${pid}`)).data.items
        const mPend = items.find((i) => i.analisis?.radio_id === pendiente.rid).analisis.marcas
        igual([mPend.pads, mPend.memory, mPend.loop], [[0, 1], 0, 1], 'la API no cuenta las marcas creadas')
        await page.setViewport({ width: 1440, height: 420 })
        await abrirPlaylist(page, ctx)
        const fila = (titulo) => page.evaluate((t) => {
          const r = [...document.querySelectorAll('.results-crate .trk')].find((x) => x.querySelector('.trk-title')?.textContent === t)
          const v = r?.querySelector('.pv-vacio')
          const color = (n) => { const e = document.createElement('i'); e.style.color = `var(${n})`; document.body.appendChild(e); const c = getComputedStyle(e).color; e.remove(); return c }
          return v ? {
            texto: v.querySelector('.pv-caja')?.textContent ?? null,
            marcas: v.querySelector('.cue-puntos-txt')?.textContent ?? null,
            puntos: [...v.querySelectorAll('.cue-puntos i')].map((i) => getComputedStyle(i).backgroundColor),
            ocultos: [...v.querySelectorAll('.cue-puntos i')].every((i) => i.closest('[aria-hidden="true"]')),
            esperados: [color('--cue-1'), color('--cue-2'), color('--cue-loop')],
            pv: Boolean(r.querySelector('.pv')),
          } : null
        }, titulo)
        // Sin analizar, con marcas importadas: el estado Y cuántas de cada una, con sus colores.
        const fp = await hasta(() => fila(pendiente.item.titulo), (x) => x && x.marcas, 'la fila sin analizar no muestra sus marcas')
        igual([fp.texto, fp.marcas, fp.puntos, fp.ocultos, fp.pv], ['sin analizar', '2 hot cues, 1 loop', fp.esperados, true, false],
          'la fila sin analizar tiene que decir su estado y sus marcas (2 hot cues, 1 loop) con los colores de pad y loop')
        const fa = await fila(analizando.item.titulo)
        igual([fa.texto, fa.marcas], ['analizando', null], 'la fila que se está analizando tiene que decir «analizando»')
        // El 409 y la base ocupada: se dicen (también al lector de pantalla) y no se cuentan las marcas viejas.
        const pv = `.pv[data-pv="${ocupado.rid}"]`
        await page.evaluate((s) => document.querySelector(s).scrollIntoView({ block: 'center' }), pv)
        const leerOcupado = () => page.evaluate((s) => {
          const b = document.querySelector(s)
          const d = document.getElementById(b.getAttribute('aria-describedby') || '')
          return { fase: (b.className.match(/\bis-(\w+)/) || [])[1], label: b.getAttribute('aria-label'), desc: d ? d.textContent : null,
            onda: Boolean(b.querySelector('svg')), visible: (() => { const r = b.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight })() }
        }, pv)
        const e1 = await hasta(leerOcupado, (x) => x.fase === 'error' && x.desc, 'con /preview en 409 la fila no dijo nada')
        igual(e1.label, `Marcar cues de «${ocupado.item.titulo}» (marcas sin leer)`, 'con la base ocupada no se pueden contar las marcas')
        afirmar(e1.desc.includes('Sin onda: La biblioteca está ocupada.') && e1.desc.includes('Sin marcas: La biblioteca está ocupada.'),
          `la descripción accesible no dice por qué no hay onda ni marcas: ${json(e1.desc)}`)
        afirmar(!e1.desc.includes('..'), `la descripción accesible repite el punto final: ${json(e1.desc)}`)
        // Se va de la pantalla y vuelve: se piden otra vez y ahora llegan.
        await page.evaluate(() => window.scrollTo(0, 0))
        if ((await leerOcupado()).visible) await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
        afirmar(!(await leerOcupado()).visible, 'no pude sacar la fila de la pantalla')
        await page.evaluate(() => fetch('/api/playlists').then((x) => x.text()))
        await page.evaluate((s) => document.querySelector(s).scrollIntoView({ block: 'center' }), pv)
        const e2 = await hasta(leerOcupado, (x) => x.fase === 'ok' && x.onda, 'al volver a la pantalla, la onda no se volvió a pedir')
        igual([e2.label, e2.desc], [`Marcar cues de «${ocupado.item.titulo}» (sin marcas)`, null], 'al volver, las marcas tenían que leerse y los avisos irse')
        igual(pedidos.sort(), ['marcas', 'marcas', 'preview', 'preview'], 'cada cosa pasajera se tenía que pedir otra vez, una sola vez')
      })
    }],

    ['preview (f62, re-auditoría): una onda y unas marcas que llegan con la fila fuera de pantalla se dibujan al volver, sin pedirlas otra vez', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        const r0 = await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'cue', inicio: 30, num: 1 })
        afirmar(r0.status === 201, `no pude crear la marca: ${json(r0)}`)
        const retenidos = []     // pedidos de la fila retenidos hasta que la fila se va
        const pedidos = []
        await interceptar(page, (req) => {
          const m = /^\/api\/radio\/tracks\/([0-9a-f]{16})\/(preview|marcas)$/.exec(new URL(req.url()).pathname)
          if (!m || req.method() !== 'GET' || m[1] !== t.rid) return false
          pedidos.push(m[2])
          return new Promise((res) => { retenidos.push({ que: m[2], soltar: () => { req.continue().catch(() => {}); res(true) } }) })
        })
        await page.setViewport({ width: 1440, height: 420 })
        await abrirPlaylist(page, ctx)
        const pv = `.pv[data-pv="${t.rid}"]`
        const visible = () => page.evaluate((s) => { const r = document.querySelector(s).getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight }, pv)
        await page.evaluate((s) => document.querySelector(s).scrollIntoView({ block: 'center' }), pv)
        await hasta(async () => retenidos.map((x) => x.que).sort(), (q) => json(q) === json(['marcas', 'preview']), 'la fila a la vista no pidió su onda y sus marcas')
        // Se va de la pantalla con los dos pedidos en vuelo; recién ahí contestan.
        await page.evaluate(() => window.scrollTo(0, 0))
        if (await visible()) await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
        afirmar(!(await visible()), 'no pude sacar la fila de la pantalla')
        const llegan = ['preview', 'marcas'].map((q) => page.waitForResponse((x) => new URL(x.url()).pathname === `/api/radio/tracks/${t.rid}/${q}`, { timeout: ESPERA_MS }))
        for (const x of retenidos.splice(0)) x.soltar()
        await Promise.all(llegan)
        await page.evaluate(() => fetch('/api/playlists').then((x) => x.text()))
        // Vuelve: lo que llegó mientras no estaba se dibuja, sin otro pedido.
        await page.evaluate((s) => document.querySelector(s).scrollIntoView({ block: 'center' }), pv)
        const v = await hasta(() => leerPreview(page, t.rid), (x) => x && x.fase === 'ok' && x.svg && x.marcas.length === 1,
          'al volver a la pantalla, la onda y las marcas que llegaron mientras no estaba no se dibujaron')
        igual(v.marcas[0].fondo, v.colores.cues[1], 'el hot cue del pad B no tiene su color')
        igual(pedidos.sort(), ['marcas', 'preview'], 'lo que ya había llegado se volvió a pedir')
      })
    }],

    ['editor D (f54): desde la playlist, BPM/key de la API, onda de 3 bandas con el kick en los graves, grilla de la API y zoom → rango', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        await abrirEditorD(page, ctx, t.item.titulo)
        const dlg = await page.$eval('.edd', (d) => [d.getAttribute('role'), d.getAttribute('aria-modal'), document.getElementById(d.getAttribute('aria-labelledby'))?.id])
        igual(dlg, ['dialog', 'true', 'edd-titulo'], 'el editor no es un diálogo modal con título')
        // Cabecera: lo medido, de la API, con un decimal y las dos notaciones de la key.
        const tr = (await apiPedir(ctx, rutaMarcas(t.rid))).data.track
        igual(tr.bpm, t.bpm, 'la base no tiene el BPM con que se generó el tema')
        const chips = await page.$$eval('.edd-chip', (cs) => cs.map((c) => c.textContent))
        igual(chips.slice(0, 2), [`${bpm1(tr.bpm)} BPM`, `${tr.camelot} · ${tr.tonalidad}`], 'la cabecera no dice el BPM (1 decimal) y la key (Camelot · clásica) de la API')

        // La grilla: la de la API, con el rótulo «estimada» y su confianza.
        const g = (await onda3Api(ctx, t.rid)).grilla
        afirmar(g && g.primer_beat_s !== null, `el motor no estimó grilla para el tema generado: ${json(g)}`)
        let o = await leerOnda(page)
        afirmar(o.estado.startsWith('estimada') && o.estado.includes(`confianza ${coma2(g.confianza)}`) && o.estado.includes(`nitidez ${coma2(g.concentracion)}`)
          && o.estado.includes('orientativa') && o.estado.includes('verificala sobre un kick'),
        `el estado de la grilla no dice «estimada», que es orientativa, y la confianza y la nitidez (concentración) de la API: ${json(o.estado)}`)
        igual(o.unidad, 'compases', 'con grilla el zoom va en compases')
        // Zoom por defecto: 8 compases = 32 beats del período de la API.
        igual(o.zooms.find((z) => z[1] === 'true')[0], '8', 'el zoom no arranca en 8 compases')
        afirmar(Math.abs((o.hasta - o.desde) - 32 * g.periodo_s) < 0.002, `8 compases tendrían que ser ${32 * g.periodo_s} s y la onda muestra ${o.hasta - o.desde}`)
        mismasLineas(o.beats, lineasEsperadas(g, 0, o.desde, o.hasta), o, '8 compases')
        afirmar(!o.atenuada && o.snap && o.snap.on, `con confianza ${g.confianza} la grilla no va atenuada y el snap arranca prendido: ${json(o)}`)
        // 2 compases y 32 (más que el tema: el tema entero, según la duración de la API).
        await clicZoom(page, '2')
        o = await hasta(() => leerOnda(page), (x) => Math.abs((x.hasta - x.desde) - 8 * g.periodo_s) < 0.002, '2 compases no son 8 beats del período de la API')
        mismasLineas(o.beats, lineasEsperadas(g, 0, o.desde, o.hasta), o, '2 compases')
        await clicZoom(page, '32')
        // 32 compases = 128 beats del período de la API (o el tema entero si dura menos).
        const ancho32 = Math.min(128 * g.periodo_s, tr.dur)
        o = await hasta(() => leerOnda(page), (x) => Math.abs((x.hasta - x.desde) - ancho32) < 0.002, `32 compases tendrían que ser ${ancho32} s a la vista`)

        // El kick se ve: con 2 compases, la barra de graves en cada kick GENERADO es alta, y medio
        // beat después (donde suena el hat) casi no hay graves.
        await clicZoom(page, '2')
        const p = 60 / t.bpm
        // La barra más alta en los 40 ms que siguen a cada kick generado, contra la de los 40 ms
        // que siguen al contratiempo (medio beat después).
        const kicks = [0, 1, 2, 3].map((j) => t.primer_kick_s + j * p)
        const desdes = [...kicks, ...kicks.map((k) => k + p / 2)]
        const pasos = [0, 0.01, 0.02, 0.03, 0.04]
        const maxima = async () => {
          const a = await alturaGraves(page, desdes.flatMap((x) => pasos.map((d) => x + d)))
          return desdes.map((_, i) => Math.max(...a.slice(i * pasos.length, (i + 1) * pasos.length)))
        }
        const alt = await hasta(maxima, (a) => a.slice(0, 4).every((x) => x > 0.3), 'la barra de graves no se ve alta donde suena cada kick generado')
        for (let k = 0; k < 4; k++) afirmar(alt[k] > 4 * alt[4 + k], `el kick ${k + 1} no se distingue del contratiempo en los graves: ${json(alt)}`)
      })
    }],

    ['editor D (f54): la grilla es estimada — ocultar, correr ½ beat y fino (queda en el navegador); atenuada con confianza baja; sin grilla no dibuja y dice por qué', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        const g = (await onda3Api(ctx, t.rid)).grilla
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.focus('.edd-onda')
        // H: medio beat (el error típico es el contratiempo).
        await page.keyboard.press('h')
        let o = await hasta(() => leerOnda(page), (x) => x.estado.includes('+½ beat'), 'H no corrió la grilla medio beat')
        mismasLineas(o.beats, lineasEsperadas(g, g.periodo_s / 2, o.desde, o.hasta), o, 'medio beat')
        await page.click('.edd-grilla-ctrl > summary')          // «Ajustar grilla» (plegado)
        await page.click('button[aria-label="−5 ms: correr la grilla 5 milisegundos para atrás"]')
        o = await hasta(() => leerOnda(page), (x) => x.estado.includes('+½ beat −5 ms'), 'el botón fino no corrió 5 ms')
        mismasLineas(o.beats, lineasEsperadas(g, g.periodo_s / 2 - 0.005, o.desde, o.hasta), o, 'medio beat − 5 ms')
        // G: se oculta (y sin grilla a la vista no se ajusta nada a ella).
        await page.focus('.edd-onda')
        await page.keyboard.press('g')
        o = await hasta(() => leerOnda(page), (x) => x.beats.length === 0, 'G no ocultó la grilla')
        afirmar(o.estado.includes('oculta') && o.snap.off && !o.snap.on, `oculta: tiene que decirlo y el snap queda apagado: ${json(o)}`)
        // Recargar: el ajuste quedó en este navegador.
        await abrirEditorD(page, ctx, t.item.titulo)
        o = await leerOnda(page)
        afirmar(o.beats.length === 0 && o.estado.includes('oculta'), `después de recargar la grilla tenía que seguir oculta: ${json(o)}`)
        await page.focus('.edd-onda')
        await page.keyboard.press('g')
        o = await hasta(() => leerOnda(page), (x) => x.beats.length > 0, 'G no volvió a mostrar la grilla')
        mismasLineas(o.beats, lineasEsperadas(g, g.periodo_s / 2 - 0.005, o.desde, o.hasta), o, 'el ajuste después de recargar')
        await page.evaluate(() => [...document.querySelectorAll('.edd-grilla-btns button')].find((b) => b.textContent === 'Volver a la del motor').click())
        o = await hasta(() => leerOnda(page), (x) => x.estado.includes('sin ajuste'), '«Volver a la del motor» no volvió al ajuste de fábrica')
        mismasLineas(o.beats, lineasEsperadas(g, 0, o.desde, o.hasta), o, 'de vuelta a la del motor')

        // AvPág: el tema sin grilla (kicks en cada corchea). No se dibuja ninguna línea, se dice
        // el motivo de la API y el zoom pasa a segundos.
        const g2 = (await onda3Api(ctx, por['Sin grilla'].rid)).grilla
        afirmar(g2 && g2.primer_beat_s === null && g2.motivo, `el tema sin grilla trae grilla en la API: ${json(g2)}`)
        await pasarTema(page, 'PageDown')
        await hasta(() => page.$eval('#edd-titulo', (h) => h.textContent), (v) => v.startsWith(por['Sin grilla'].item.titulo), 'AvPág no pasó al tema siguiente')
        await esperarOnda(page, 'sin grilla', true)
        o = await leerOnda(page)
        igual([o.beats.length, o.estado, o.unidad, o.zooms.map((z) => z[0])],
          [0, `Sin grilla de beats: ${g2.motivo}. No se dibuja ninguna (una grilla inventada mentiría).`, 'segundos', ['4', '8', '15', '30', '60']],
          'sin grilla: ninguna línea, el motivo de la API y el zoom en segundos')
        afirmar(Math.abs((o.hasta - o.desde) - 15) < 0.002, `sin grilla el zoom por defecto son 15 s: ${o.hasta - o.desde}`)
        afirmar(o.snap === null, 'sin grilla no hay casilla de «ajustar a la grilla»')
        // El tema sin BPM: «?» y sin beats.
        await pasarTema(page, 'PageDown')
        await hasta(() => page.$eval('#edd-titulo', (h) => h.textContent), (v) => v.startsWith(por['Sin BPM'].item.titulo), 'AvPág no pasó al tercer tema')
        await esperarOnda(page, 'sin BPM', true)
        const tr3 = (await apiPedir(ctx, rutaMarcas(por['Sin BPM'].rid))).data.track
        igual(tr3.bpm, null, 'la API tendría que decir que el tema sin pulso no tiene BPM')
        igual((await page.$$eval('.edd-chip', (cs) => cs.map((c) => c.textContent))).slice(0, 2), ['? BPM', '? key'], 'sin BPM ni key medidos la cabecera dice «?» (no un número)')
        o = await leerOnda(page)
        igual([o.beats.length, o.estado], [0, 'Sin grilla de beats: el tema no tiene BPM medido. No se dibuja ninguna (una grilla inventada mentiría).'], 'sin BPM no hay grilla')

        // Confianza baja (la respuesta de /onda3 con la confianza bajada a 0,25, lo único que
        // cambia): atenuada, dicho, y el snap arranca apagado.
        let cambio = { confianza: 0.25 }
        await interceptar(page, async (req) => {
          if (!/\/onda3/.test(req.url())) return false
          const u = new URL(req.url())
          const d = await (await fetch(ctx.url + u.pathname + u.search)).json()
          if (d.grilla && d.grilla.primer_beat_s !== null) Object.assign(d.grilla, cambio)
          await req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(d) })
          return true
        })
        await abrirEditorD(page, ctx, t.item.titulo)
        o = await leerOnda(page)
        afirmar(o.atenuada && o.estadoClase.includes('is-baja') && o.estado.includes('confianza 0,25 (baja)'), `con confianza 0,25 la grilla va atenuada y lo dice: ${json(o)}`)
        afirmar(o.beats.length > 0 && o.snap && !o.snap.on && !o.snap.off, `atenuada se dibuja igual, con el snap apagado (y prendible): ${json(o)}`)
        // Confianza 1,00 con nitidez 0,37 (el caso del acorde sostenido de la auditoría: fase
        // corrida 56 ms): la confianza sola no la salva, va atenuada y lo dice.
        cambio = { confianza: 1, concentracion: 0.37 }
        await abrirEditorD(page, ctx, t.item.titulo)
        o = await leerOnda(page)
        afirmar(o.atenuada && o.estado.includes('confianza 1,00 · nitidez 0,37 (baja)') && o.estado.includes('puede estar corrida') && !o.snap.on,
          `con nitidez 0,37 la grilla va atenuada aunque la confianza sea 1,00, y lo dice: ${json(o)}`)
      })
    }],

    ['editor D (f54): pads A–H con el teclado, snap a la grilla, memory, loop de N beats, lista editable y deshacer / rehacer contra la API', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        const g = (await onda3Api(ctx, t.rid)).grilla
        const P = g.periodo_s
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.focus('.edd-onda')
        // 3 beats con → (el período de la grilla de la API) y la tecla 1 (pad A vacío): un hot
        // cue A en la línea de grilla más cercana (snap prendido con confianza alta).
        let x = 0
        for (let i = 0; i < 3; i++) { await page.keyboard.press('ArrowRight'); x = ms3(x + P) }
        igual(await reloj(page), tiempoTabla(x), '→ no movió el cursor un período de la grilla')
        const lin = g.primer_beat_s + Math.round((x - g.primer_beat_s) / P) * P
        const a = await conTecla(page, ['1'], 'POST')
        igual([a.status, a.data.marca.tipo, a.data.marca.num, a.data.marca.inicio], [201, 'cue', 0, ms3(lin)], 'la tecla 1 no fijó el hot cue A en la línea de grilla más cercana')
        // Q apaga el snap: Mayús+→ dos veces (±10 ms) y M = memory exactamente ahí.
        await page.keyboard.press('q')
        await hasta(() => leerOnda(page), (o) => !o.snap.on, 'Q no apagó el snap')
        for (let i = 0; i < 2; i++) { await page.keyboard.down('Shift'); await page.keyboard.press('ArrowRight'); await page.keyboard.up('Shift'); x = ms3(x + 0.01) }
        const mem = await conTecla(page, ['m'], 'POST')
        igual([mem.data.marca.tipo, mem.data.marca.inicio], ['memory', x], 'M sin snap: la memory en el cursor (3 beats + 20 ms)')
        // L: un loop de 4 beats (los del período de la grilla) desde el cursor; siempre naranja.
        const lp = await conTecla(page, ['l'], 'POST')
        igual([lp.data.marca.tipo, lp.data.marca.inicio, lp.data.marca.fin, lp.data.marca.num], ['loop', x, ms3(x + 4 * P), null], 'L: loop de 4 beats desde el cursor')
        let api_ = await filasComoApi(page, ctx, t.rid, 'después de A, M y L')
        // Los colores son los de la tabla del motor y la memory no depende del color: es hueca y dice M.
        const vis = await page.evaluate(() => [...document.querySelectorAll('.edd-fila')].map((r) => {
          const b = r.querySelector('.edd-tag')
          const cs = getComputedStyle(b)
          return [r.dataset.tipo, b.textContent, cs.backgroundColor, cs.color]
        }))
        // Los colores pedidos por el dueño (motor/cue_marks.py), escritos acá: A rojo, loop
        // naranja, memory sin relleno con borde y letra en el neutro de la tabla.
        const COLOR = { cue: ['rgb(255, 77, 90)', 'rgb(22, 24, 38)'], loop: ['rgb(255, 154, 46)', 'rgb(22, 24, 38)'], memory: ['rgba(0, 0, 0, 0)', 'rgb(242, 243, 248)'] }
        igual(vis, filasEsperadas(api_).map((f) => [f.tipo, f.rotulo, ...COLOR[f.tipo]]), 'la lista no lleva la letra y los colores de la tabla del motor (A rojo, loop naranja, memory hueca)')
        // El pad A dice su letra y su tiempo, y el pad lleno lleva al cue.
        await page.focus('.edd-onda')
        await page.keyboard.press('End')
        await page.click('.edd-pad[data-pad="0"]')
        igual(await reloj(page), tiempoTabla(ms3(lin)), 'tocar el pad A lleno no llevó el cursor a su cue')
        const padA = await page.$eval('.edd-pad[data-pad="0"]', (b) => [b.querySelector('.edd-pad-n').textContent, b.querySelector('.edd-pad-tiempo').textContent])
        igual(padA, ['A', tiempoTabla(ms3(lin))], 'el pad A no dice su letra y su tiempo')

        // Renombrar desde la lista.
        const cueA = api_.find((m) => m.tipo === 'cue')
        await abrirFila(page, cueA.id)
        const nom = `.edd-fila[data-id="${cueA.id}"] .edd-in-n`
        await page.click(nom)
        await page.type(nom, 'Drop')
        const ren = await conTecla(page, ['Enter'], 'PATCH')
        igual([ren.status, ren.data.marca.nombre], [200, 'Drop'], 'renombrar desde la lista')
        // Cambiar el tipo: la memory pasa a hot cue (toma el primer pad libre: B).
        const m0 = api_.find((m) => m.tipo === 'memory')
        const borra = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await abrirFila(page, m0.id)
        await page.select(`.edd-fila[data-id="${m0.id}"] select.edd-tipo`, 'cue')
        igual((await borra).status(), 200, 'cambiar el tipo no borró la marca vieja')
        api_ = await filasComoApi(page, ctx, t.rid, 'después de pasar la memory a hot cue')
        const nuevoB = api_.find((m) => m.tipo === 'cue' && m.num === 1)
        afirmar(nuevoB && nuevoB.inicio === m0.inicio && !api_.some((m) => m.tipo === 'memory'), `la memory no pasó a hot cue B en el mismo tiempo: ${json(api_)}`)

        // Ctrl+Z: vuelve la memory (con otro id) y se va el hot cue B; Ctrl+Y lo rehace.
        await page.focus('.edd-onda')
        const d1 = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control')
        await d1
        api_ = await filasComoApi(page, ctx, t.rid, 'después de Ctrl+Z')
        afirmar(api_.some((m) => m.tipo === 'memory' && m.inicio === m0.inicio) && !api_.some((m) => m.tipo === 'cue' && m.num === 1), `Ctrl+Z no deshizo el cambio de tipo: ${json(api_)}`)
        const d2 = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await page.keyboard.down('Control'); await page.keyboard.press('y'); await page.keyboard.up('Control')
        await d2
        api_ = await filasComoApi(page, ctx, t.rid, 'después de Ctrl+Y')
        afirmar(api_.some((m) => m.tipo === 'cue' && m.num === 1 && m.inicio === m0.inicio) && !api_.some((m) => m.tipo === 'memory'), `Ctrl+Y no rehízo el cambio de tipo: ${json(api_)}`)
        // Mayús+1 borra el pad A; Ctrl+Z lo vuelve a poner igual (tiempo, pad y nombre).
        const del = await conTecla(page, ['Shift', 'Digit1'], 'DELETE')
        igual(del.status, 200, 'Mayús+1 no borró el pad A')
        afirmar(!(await marcasApi(ctx, t.rid)).some((m) => m.num === 0), 'el pad A sigue en la API')
        const vuelve = await conTecla(page, ['Control', 'z'], 'POST')
        igual([vuelve.data.marca.tipo, vuelve.data.marca.num, vuelve.data.marca.inicio, vuelve.data.marca.nombre], ['cue', 0, ms3(lin), 'Drop'], 'Ctrl+Z no volvió a poner el pad A como estaba')
        await filasComoApi(page, ctx, t.rid, 'después de deshacer el borrado')
        // Escribiendo un nombre ningún atajo dispara (ni Ctrl+Z del editor). El pad A recreado
        // tiene otro id en la base.
        const pedidos = pedidosDeMarcas(page)
        await abrirFila(page, vuelve.data.marca.id)
        await page.click(`.edd-fila[data-id="${vuelve.data.marca.id}"] .edd-in-n`)
        await page.keyboard.type('c1m')
        await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control')
        await page.keyboard.press('Escape')
        igual(pedidos, [], 'escribir en el nombre disparó atajos')
        afirmar(await page.$('.edd'), 'Escape en un campo cerró el editor (tenía que solo deshacer lo escrito)')
      })
    }],

    ['editor D (f63): el loop activo suena solo el loop (salta de la salida a la entrada), R y «Loop activo» salen y vuelven, un clic afuera sale, borrarlo / Ctrl+Z / pasar de tema lo limpian', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        await abrirEditorD(page, ctx, t.item.titulo)
        // Cada `currentTime = x` que hace la página, con dónde estaba el audio en ese instante: así
        // se mide cuánto se pasó de la salida cada salto (la precisión del loop).
        await page.evaluate(() => {
          const d = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime')
          window.__saltos = []
          window.__currentTimeOriginal = d
          Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {
            configurable: true,
            get() { return d.get.call(this) },
            set(v) { window.__saltos.push({ antes: d.get.call(this), a: v, sonando: !this.paused }); d.set.call(this, v) },
          })
        })
        try {
        const boton = () => page.$eval('.edd-reloop', (b) => ({ pressed: b.getAttribute('aria-pressed'), disabled: b.getAttribute('aria-disabled'), atajo: b.getAttribute('aria-keyshortcuts') }))
        const activoEnOnda = () => page.evaluate(() => ({
          banda: [...document.querySelectorAll('.edd-mk-loop.is-activo')].map((e) => Number(e.dataset.id)),
          bandera: [...document.querySelectorAll('.edd-flag.is-activo')].map((e) => Number(e.dataset.id)),
        }))
        const audioT = () => page.evaluate(() => [...window.__medios].find((m) => !m.paused)?.currentTime ?? null)
        // currentTime del audio que suena, en cada cuadro durante `ms`.
        const muestrear = (ms) => page.evaluate((dur) => new Promise((res) => {
          const a = [...window.__medios].find((m) => !m.paused)
          if (!a) { res(null); return }
          const out = []
          const t0 = performance.now()
          const f = () => { out.push(a.currentTime); if (performance.now() - t0 < dur) requestAnimationFrame(f); else res(out) }
          requestAnimationFrame(f)
        }), ms)
        const play = async () => {
          await page.focus('.edd-onda')
          await page.keyboard.press('Space')
          await hasta(() => sonando(page), (s) => s.length === 1, 'Espacio no hizo sonar el tema')
        }
        const pausa = async () => {
          await page.focus('.edd-onda')
          await page.keyboard.press('Space')
          await hasta(() => sonando(page), (s) => s.length === 0, 'Espacio no pausó')
        }

        igual(await boton(), { pressed: 'false', disabled: 'true', atajo: 'R' }, 'sin loop, «Loop activo» tiene que estar apagado y deshabilitado (con su atajo R)')
        // Un loop de 4 beats (L) a 8 beats del principio: queda ACTIVO y el cursor en su entrada.
        await page.focus('.edd-onda')
        await page.keyboard.press('Home')
        for (let i = 0; i < 8; i++) await page.keyboard.press('ArrowRight')
        const cre = await conTecla(page, ['l'], 'POST')
        igual(cre.status, 201, 'L no creó el loop')
        const lp = cre.data.marca
        const largo = lp.fin - lp.inicio
        afirmar(lp.tipo === 'loop' && largo > 1.5 && largo < 2.2, `el loop de 4 beats a 128 BPM tiene que durar ~1,875 s: ${json(lp)}`)
        await hasta(boton, (b) => b.pressed === 'true' && b.disabled === null, 'el loop recién creado no quedó activo (aria-pressed)')
        igual(await activoEnOnda(), { banda: [lp.id], bandera: [lp.id] }, 'la banda y la bandera del loop activo no están resaltadas')

        // Play: durante más de 2 vueltas el audio no sale de [entrada, salida] y vuelve a la entrada.
        await play()
        const m1 = await muestrear(Math.round(largo * 2.6 * 1000))
        afirmar(m1 && m1.length > 30, `no pude muestrear el audio: ${json(m1)}`)
        const fuera = m1.filter((x) => x < lp.inicio - 0.03 || x > lp.fin + 0.03)
        igual(fuera, [], `con el loop activo el audio salió de [${lp.inicio}, ${lp.fin}]`)
        const vueltas = m1.filter((x, i) => i > 0 && x < m1[i - 1] - largo / 2).length
        afirmar(vueltas >= 2, `en ${(largo * 2.6).toFixed(1)} s el loop tenía que volver a la entrada al menos 2 veces: ${vueltas} (min ${Math.min(...m1)}, max ${Math.max(...m1)})`)
        // La precisión: cuánto se pasó de la salida el audio en cada salto (+ tarde, − antes).
        const saltos = (await page.evaluate(() => window.__saltos)).filter((s) => s.sonando && s.antes > lp.inicio + largo / 2 && s.a < lp.inicio + largo / 2)
        afirmar(saltos.length >= 2, `no hubo saltos de la salida a la entrada: ${json(await page.evaluate(() => window.__saltos))}`)
        const err = saltos.map((s) => (s.antes - lp.fin) * 1000)
        const prom = err.reduce((x, y) => x + y, 0) / err.length
        console.log(`    [f63] salto del loop: ${err.length} saltos, error ${Math.min(...err).toFixed(1)} … ${Math.max(...err).toFixed(1)} ms (promedio ${prom.toFixed(1)} ms) respecto de la salida`)
        afirmar(err.every((e) => Math.abs(e) < 30), `el salto tiene que caer a menos de 30 ms de la salida: ${json(err.map((e) => Math.round(e * 10) / 10))}`)
        // El salto conserva lo que se pasó: cae en la entrada + lo pasado (el beat no se corre).
        for (const s of saltos) afirmar(Math.abs(s.a - (lp.inicio + Math.max(0, s.antes - lp.fin))) < 0.002, `el salto no conservó lo que se pasó de la salida: ${json(s)}`)

        // Arrastrar la salida del loop activo mientras suena (a 2 beats): el salto usa la salida NUEVA.
        const muestraEn = (m, l, que) => {
          igual(m.filter((x) => x < l.inicio - 0.03 || x > l.fin + 0.03), [], `${que}: el audio salió de [${l.inicio}, ${l.fin}]`)
          afirmar(m.filter((x, i) => i > 0 && x < m[i - 1] - (l.fin - l.inicio) / 2).length >= 2, `${que}: el loop no volvió a la entrada 2 veces`)
        }
        const tirador = await page.$eval(`.edd-mk-loop[data-id="${lp.id}"] .edd-loop-fin`, (e) => { const r = e.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 } })
        const ov = await page.$eval('.edd-onda', (e) => { const r = e.getBoundingClientRect(); return { left: r.left, width: r.width, desde: Number(e.dataset.desde), hasta: Number(e.dataset.hasta) } })
        const xMeta = ov.left + ((lp.fin - largo / 2 - ov.desde) / (ov.hasta - ov.desde)) * ov.width
        const mover = page.waitForResponse((r) => r.request().method() === 'PATCH', { timeout: ESPERA_MS })
        await page.mouse.move(tirador.x, tirador.y)
        await page.mouse.down()
        await page.mouse.move(xMeta, tirador.y, { steps: 6 })
        await page.mouse.up()
        const movido = (await (await mover).json()).marcas.find((m) => m.id === lp.id)
        afirmar(movido && movido.fin < lp.fin - 0.5 && movido.inicio === lp.inicio, `arrastrar la salida no la acortó: ${json(movido)}`)
        igual((await boton()).pressed, 'true', 'mover la salida del loop activo lo sacó del loop')
        muestraEn(await muestrear(Math.round((movido.fin - movido.inicio) * 2.6 * 1000)), movido, 'con la salida arrastrada')
        // Ctrl+Z del movimiento: vuelve la salida de antes y el loop sigue activo con ella.
        await page.focus('.edd-onda')
        const und = await conTecla(page, ['Control', 'z'], 'PATCH')
        igual(und.data.marcas.find((m) => m.id === lp.id)?.fin, lp.fin, 'Ctrl+Z no devolvió la salida del loop')
        igual((await boton()).pressed, 'true', 'Ctrl+Z del movimiento sacó del loop')
        const m4 = await muestrear(Math.round(largo * 2.6 * 1000))
        muestraEn(m4, lp, 'después de Ctrl+Z del movimiento')
        afirmar(Math.max(...m4) > movido.fin + 0.2, `después de Ctrl+Z el loop sigue saltando en la salida arrastrada (max ${Math.max(...m4)})`)

        // «Loop activo» (el botón): sale del loop y la reproducción sigue más allá de la salida.
        await page.click('.edd-reloop')
        igual((await boton()).pressed, 'false', 'el botón no salió del loop (aria-pressed)')
        igual(await activoEnOnda(), { banda: [], bandera: [] }, 'fuera del loop la banda sigue resaltada')
        await hasta(audioT, (x) => x !== null && x > lp.fin + 0.3, 'al salir del loop la reproducción no pasó la salida', largo * 1000 + 4000)
        // R: lo vuelve a activar (vuelve a la entrada) y otra vez suena solo el loop.
        await page.focus('.edd-onda')
        await page.keyboard.press('r')
        igual((await boton()).pressed, 'true', 'R no volvió a activar el loop')
        const m2 = await muestrear(Math.round(largo * 1.5 * 1000))
        igual(m2.filter((x) => x < lp.inicio - 0.03 || x > lp.fin + 0.03), [], 'después de R el audio salió del loop')
        afirmar(m2.some((x, i) => i > 0 && x < m2[i - 1] - largo / 2), 'después de R el loop no volvió a la entrada')
        // R otra vez: sale.
        await page.keyboard.press('r')
        igual((await boton()).pressed, 'false', 'R no salió del loop')
        await hasta(audioT, (x) => x !== null && x > lp.fin + 0.3, 'al salir con R la reproducción no pasó la salida', largo * 1000 + 4000)
        // Un clic afuera del loop (en la vista general, al 80 % del tema) sale del loop.
        await page.keyboard.press('r')
        igual((await boton()).pressed, 'true', 'R no volvió a activar el loop (antes del clic afuera)')
        const g = await page.$eval('.edd-gen', (e) => { const r = e.getBoundingClientRect(); return { x: r.left + r.width * 0.8, y: r.top + r.height / 2 } })
        await page.mouse.click(g.x, g.y)
        igual((await boton()).pressed, 'false', 'un clic afuera del loop no salió del loop')
        await hasta(audioT, (x) => x !== null && x > 70, 'el clic en la vista general no movió el audio')
        await pausa()

        // Su fila de la lista (el rótulo) lo activa y lleva el cursor a la entrada.
        await page.click(`.edd-fila[data-id="${lp.id}"] .edd-tag`)
        igual((await boton()).pressed, 'true', 'tocar la fila del loop no lo activó')
        igual(await reloj(page), tiempoTabla(lp.inicio), 'tocar la fila del loop no llevó el cursor a su entrada')
        // Borrarlo (la X de su fila) lo limpia: no queda nada para activar.
        const del = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await page.click(`.edd-fila[data-id="${lp.id}"] .edd-borrar`)
        igual((await del).status(), 200, 'la X no borró el loop')
        await hasta(boton, (b) => b.pressed === 'false' && b.disabled === 'true', 'borrar el loop activo no lo limpió')
        // Ctrl+Z lo vuelve a poner (otro id), pero no lo vuelve a activar.
        const vuelve = await conTecla(page, ['Control', 'z'], 'POST')
        igual(vuelve.status, 201, 'Ctrl+Z no volvió a poner el loop')
        igual(await boton(), { pressed: 'false', disabled: 'true', atajo: 'R' }, 'el loop recreado por Ctrl+Z no tiene que quedar activo')
        // Ctrl+Z de la CREACIÓN de un loop lo limpia: crear otro (L, activo) y deshacerlo.
        await page.focus('.edd-onda')
        for (let i = 0; i < 8; i++) await page.keyboard.press('ArrowRight')
        const cre2 = await conTecla(page, ['l'], 'POST')
        igual(cre2.status, 201, 'L no creó el segundo loop')
        await hasta(boton, (b) => b.pressed === 'true', 'el segundo loop no quedó activo')
        const des = await conTecla(page, ['Control', 'z'], 'DELETE')
        igual(des.status, 200, 'Ctrl+Z no deshizo la creación del loop')
        await hasta(boton, (b) => b.pressed === 'false' && b.disabled === 'true', 'deshacer la creación del loop activo no lo limpió')
        // El pad: un hot loop se activa desde su pad; pasar de tema lo limpia.
        await page.click('.edd-pads-box .edd-snap input[type=checkbox]')
        await page.focus('.edd-onda')
        const cre3 = await conTecla(page, ['l'], 'POST')
        igual(cre3.data.marca.num, 0, 'el hot loop no fue al pad A')
        await page.click('.edd-reloop')
        igual((await boton()).pressed, 'false', 'el botón no salió del hot loop')
        await page.click('.edd-pad[data-pad="0"]')
        igual((await boton()).pressed, 'true', 'tocar el pad del hot loop no lo activó')
        igual(await page.$eval('.edd-pad[data-pad="0"]', (p) => p.classList.contains('is-activo')), true, 'el pad del loop activo no está resaltado')
        // Pasar de tema lo limpia en el MISMO render en que la pantalla empieza a leer el tema nuevo
        // (no recién cuando llega su lista): se anota aria-pressed cuando aparece «Leyendo el audio…».
        await page.evaluate(() => {
          window.__reloopAlCambiar = null
          const obs = new MutationObserver(() => {
            if (document.querySelector('.edd-msg')?.textContent?.includes('Leyendo')) {
              window.__reloopAlCambiar = document.querySelector('.edd-reloop')?.getAttribute('aria-pressed') ?? 'sin botón'
              obs.disconnect()
            }
          })
          obs.observe(document.querySelector('.edd'), { subtree: true, childList: true, characterData: true, attributes: true })
        })
        await pasarTema(page, 'PageDown')
        await hasta(() => page.$eval('#edd-titulo', (h) => h.firstChild.textContent), (v) => v === por['Sin grilla'].item.titulo, 'AvPág no pasó de tema')
        igual(await page.evaluate(() => window.__reloopAlCambiar), 'false', 'al pasar de tema el loop del tema anterior siguió activo mientras se leía el nuevo')
        await hasta(boton, (b) => b.pressed === 'false' && b.disabled === 'true', 'pasar de tema no limpió el loop activo')
        // R con el loop activo borrado por OTRO camino (la X de su pad): avisa que no hay loop.
        await pasarTema(page, 'PageUp')
        await hasta(() => page.$eval('#edd-titulo', (h) => h.firstChild.textContent), (v) => v === t.item.titulo, 'RePág no volvió al tema')
        await esperarOnda(page, 'vuelta al tema del loop', true)
        await hasta(() => page.$('.edd-pad[data-pad="0"].is-loop'), (v) => Boolean(v), 'el hot loop del pad A no está al volver')
        await page.click('.edd-pad[data-pad="0"]')
        igual((await boton()).pressed, 'true', 'tocar el pad del hot loop (al volver) no lo activó')
        igual(await page.$eval('.edd-pad[data-pad="0"]', (p) => p.getAttribute('aria-label').endsWith(' · loop activo')), true, 'el pad del loop activo no lo dice a un lector de pantalla')
        const delPad = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await page.click('.edd-x-pad[data-x-pad="0"]')
        igual((await delPad).status(), 200, 'la X del pad no borró el hot loop')
        await hasta(boton, (b) => b.pressed === 'false' && b.disabled === 'true', 'borrar el loop activo desde su pad no lo limpió')
        await page.focus('.edd-onda')
        await page.keyboard.press('r')
        await hasta(() => page.$eval('.edd-aviso', (e) => e.textContent), (v) => v.includes('No hay un loop para activar'), 'R sin loop (borrado desde el pad) no avisó')
        } finally {
          await page.evaluate(() => { if (window.__currentTimeOriginal) Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', window.__currentTimeOriginal) }).catch(() => {})
        }
      })
    }],

    ['editor D (f63): sin requestAnimationFrame (pestaña oculta) el loop sigue saltando con los timers; un loop que termina en el final del tema vuelve a la entrada', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        // Un loop que termina justo en el final del tema (creado por la API antes de abrir el editor).
        const o3 = await onda3Api(ctx, t.rid)
        const finTema = Math.floor(Math.min(Number(o3.duracion_audio), t.dur) * 1000) / 1000
        const rFin = await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'loop', inicio: ms3(finTema - 1.875), fin: finTema })
        afirmar(rFin.status === 201, `no pude crear el loop del final del tema: ${json(rFin)}`)
        const lFin = rFin.data.marca
        await abrirEditorD(page, ctx, t.item.titulo)
        const boton = () => page.$eval('.edd-reloop', (b) => b.getAttribute('aria-pressed'))
        // Muestreo con setInterval (sin requestAnimationFrame).
        const muestrearT = (ms) => page.evaluate((dur) => new Promise((res) => {
          const a = [...window.__medios].find((m) => !m.paused)
          if (!a) { res(null); return }
          const out = []
          const t0 = performance.now()
          const iv = setInterval(() => { out.push(a.currentTime); if (performance.now() - t0 >= dur) { clearInterval(iv); res(out) } }, 15)
        }), ms)
        const enLoop = (m, l, que) => {
          afirmar(m && m.length > 30, `${que}: no pude muestrear el audio: ${json(m)}`)
          igual(m.filter((x) => x < l.inicio - 0.03 || x > l.fin + 0.03), [], `${que}: el audio salió de [${l.inicio}, ${l.fin}]`)
          const v = m.filter((x, i) => i > 0 && x < m[i - 1] - (l.fin - l.inicio) / 2).length
          afirmar(v >= 2, `${que}: el loop tenía que volver a la entrada al menos 2 veces: ${v}`)
        }
        const play = async () => {
          await page.focus('.edd-onda')
          await page.keyboard.press('Space')
          await hasta(() => sonando(page), (s) => s.length === 1, 'Espacio no hizo sonar el tema')
        }
        const pausa = async () => {
          await page.focus('.edd-onda')
          await page.keyboard.press('Space')
          await hasta(() => sonando(page), (s) => s.length === 0, 'Espacio no pausó')
        }
        // Como con la pestaña oculta: requestAnimationFrame no corre nunca. Y sin `timeupdate` (se
        // corta en la captura, antes de llegar al <audio>): así lo único que sostiene el loop es la
        // cadena de timers (cada salto re-arma el de la salida siguiente).
        await page.evaluate(() => {
          window.__rafOriginal = window.requestAnimationFrame
          window.requestAnimationFrame = () => 0
          window.__sinTimeupdate = (e) => e.stopImmediatePropagation()
          window.addEventListener('timeupdate', window.__sinTimeupdate, true)
        })
        try {
          await page.focus('.edd-onda')
          await page.keyboard.press('Home')
          for (let i = 0; i < 8; i++) await page.keyboard.press('ArrowRight')
          const cre = await conTecla(page, ['l'], 'POST')
          igual(cre.status, 201, 'L no creó el loop')
          const lp = cre.data.marca
          await hasta(boton, (b) => b === 'true', 'el loop recién creado no quedó activo')
          await play()
          enLoop(await muestrearT(Math.round((lp.fin - lp.inicio) * 2.6 * 1000)), lp, 'sin requestAnimationFrame')
          await pausa()
        } finally {
          await page.evaluate(() => {
            if (window.__rafOriginal) window.requestAnimationFrame = window.__rafOriginal
            if (window.__sinTimeupdate) window.removeEventListener('timeupdate', window.__sinTimeupdate, true)
          }).catch(() => {})
        }
        // El loop del final: su fila lo activa; al llegar al final del tema vuelve a la entrada y sigue sonando.
        await page.click(`.edd-fila[data-id="${lFin.id}"] .edd-tag`)
        igual(await boton(), 'true', 'tocar la fila del loop del final no lo activó')
        igual(await page.$eval(`.edd-fila[data-id="${lFin.id}"] .edd-tag`, (b) => b.getAttribute('aria-label').startsWith('Activar loop') && b.getAttribute('aria-label').endsWith(' · loop activo')), true,
          'el rótulo de la fila de un loop tiene que decir «Activar loop …» y « · loop activo» si es el activo')
        // Que el salto de los timers no llegue antes del final (se ignora todo `currentTime = x` con el
        // audio sonando hasta que el <audio> termina): así se prueba el camino del `ended`. El
        // `ended` se ve primero en la captura de window, antes que el <audio>.
        await page.evaluate(() => {
          const d = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime')
          window.__ctOriginal = d
          window.__bloqueo = true
          window.__terminado = false
          Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {
            configurable: true,
            get() { return d.get.call(this) },
            set(v) { if (window.__bloqueo && !this.paused) return; d.set.call(this, v) },
          })
          window.__alTerminar = () => { window.__bloqueo = false; window.__terminado = true }
          window.addEventListener('ended', window.__alTerminar, true)
        })
        try {
          await play()
          await hasta(() => page.evaluate(() => window.__terminado), (v) => v === true, 'el audio no llegó al final del tema', (lFin.fin - lFin.inicio) * 1000 + 6000)
          await hasta(() => sonando(page), (s) => s.length === 1, 'al terminar el tema con el loop activo no volvió a sonar desde la entrada')
          enLoop(await muestrearT(Math.round((lFin.fin - lFin.inicio) * 2.6 * 1000)), lFin, 'loop que termina en el final del tema')
          afirmar((await sonando(page)).length === 1, 'el loop del final del tema dejó de sonar')
          await pausa()
        } finally {
          await page.evaluate(() => {
            if (window.__ctOriginal) Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', window.__ctOriginal)
            if (window.__alTerminar) window.removeEventListener('ended', window.__alTerminar, true)
          }).catch(() => {})
        }
      })
    }],

    ['editor D (f63): un loop de 10 ms no se activa (fila, pad, R) y un loop activo que QUEDA de 10 ms sale del loop y la reproducción sigue', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        // Por la API (el server acepta cualquier salida > entrada): un loop normal, un memory loop
        // de 10 ms y un hot loop de 10 ms en el pad B.
        const crear = async (m, que) => {
          const r = await apiPedir(ctx, rutaMarcas(t.rid), 'POST', m)
          afirmar(r.status === 201, `no pude crear ${que}: ${json(r)}`)
          return r.data.marca
        }
        const normal = await crear({ tipo: 'loop', inicio: 10, fin: 11.875 }, 'el loop normal')
        const corto = await crear({ tipo: 'loop', inicio: 30, fin: 30.01 }, 'el loop de 10 ms')
        const cortoPad = await crear({ tipo: 'loop', inicio: 40, fin: 40.01, num: 1 }, 'el hot loop de 10 ms')
        await abrirEditorD(page, ctx, t.item.titulo)
        const boton = () => page.$eval('.edd-reloop', (b) => b.getAttribute('aria-pressed'))
        const avisoEd = () => page.$eval('.edd-aviso', (e) => e.textContent).catch(() => '')
        const muyCorto = (que) => hasta(avisoEd, (x) => x.startsWith('⚠ El loop'), `${que}: no avisó que el loop es demasiado corto`)
        // Otro aviso antes (I: «Entrada del loop en …»), para que el siguiente se vea aunque sea
        // el mismo texto que el anterior.
        const otroAviso = async () => {
          await page.focus('.edd-onda')
          await page.keyboard.press('i')
          await hasta(avisoEd, (x) => x.startsWith('Entrada del loop'), 'I no dejó su aviso')
        }
        const audioT = () => page.evaluate(() => [...window.__medios].find((m) => !m.paused)?.currentTime ?? null)

        // La fila del loop de 10 ms: no lo activa, avisa.
        await page.click(`.edd-fila[data-id="${corto.id}"] .edd-tag`)
        await muyCorto('la fila del loop de 10 ms')
        igual(await boton(), 'false', 'tocar la fila de un loop de 10 ms lo activó')
        // El pad del hot loop de 10 ms: tampoco.
        await otroAviso()
        await page.click('.edd-pad[data-pad="1"]')
        await muyCorto('el pad del hot loop de 10 ms')
        igual(await boton(), 'false', 'tocar el pad de un hot loop de 10 ms lo activó')
        afirmar(cortoPad.num === 1, `el hot loop de 10 ms no quedó en el pad B: ${json(cortoPad)}`)

        // El loop normal activo y sonando; su salida pasa a 10 ms de la entrada (otra pestaña).
        await page.click(`.edd-fila[data-id="${normal.id}"] .edd-tag`)
        igual(await boton(), 'true', 'tocar la fila del loop normal no lo activó')
        await page.focus('.edd-onda')
        await page.keyboard.press('Space')
        await hasta(() => sonando(page), (s) => s.length === 1, 'Espacio no hizo sonar el tema')
        const fin = ms3(normal.inicio + 0.01)
        const pr = await apiPedir(ctx, `${rutaMarcas(t.rid)}/${normal.id}`, 'PATCH', { fin })
        afirmar(pr.status === 200, `el server no aceptó la salida a 10 ms: ${json(pr)}`)
        // Cualquier escritura del editor trae la lista nueva (una memory donde está el cursor).
        await page.focus('.edd-onda')
        const mem = await conTecla(page, ['m'], 'POST')
        igual(mem.data.marcas.find((m) => m.id === normal.id)?.fin, fin, 'la lista nueva no trae la salida a 10 ms')
        await hasta(boton, (b) => b === 'false', 'el loop activo que quedó de 10 ms siguió activo')
        await muyCorto('el loop activo que quedó de 10 ms')
        // La reproducción sigue de largo (sin la tormenta de saltos a la entrada).
        const t0 = await audioT()
        await hasta(audioT, (x) => x !== null && x > fin + 0.2 && x > t0 + 0.5, `con el loop de 10 ms desactivado la reproducción no siguió (desde ${t0})`, 6000)
        // R: avisa que es demasiado corto (no «No hay un loop») y no lo activa.
        await otroAviso()
        await page.keyboard.press('r')
        await muyCorto('R con el loop que quedó de 10 ms')
        igual(await boton(), 'false', 'R activó un loop de 10 ms')
        await page.keyboard.press('Space')
        await hasta(() => sonando(page), (s) => s.length === 0, 'Espacio no pausó')
      })
    }],

    ['editor D (f54): se reproduce con el audio local y el cabezal va con el audio; teclado, foco, Esc devuelve el foco, 44 px y 400 px', async (page, ctx) => {
      await conEditor(ctx, async ({ pid, por }) => {
        const t = por['Con grilla']
        // f55: con género, la playlist puede armar un set (la radio arma solo con un género).
        const gen = await apiPedir(ctx, `/api/playlists/${pid}/genero`, 'POST', { genero: 'Techno' })
        afirmar(gen.status === 200 && gen.data.exito, `no pude ponerle género a la playlist del editor: ${json(gen)}`)
        const radio = (await apiPedir(ctx, `/api/radio/playlists/${pid}`)).data
        afirmar(radio.puede_armar === true, `con género la radio tendría que poder armar un set con la playlist del editor: ${json(radio)}`)
        await abrirPlaylist(page, ctx)
        // Las tres acciones habilitadas a la vez: marcar cues (f54), armar un set (f55) y
        // exportar a Rekordbox con cues (f56).
        const acciones = await hasta(() => page.$$eval('.crate-accion', (bs) => bs.map((b) => [b.querySelector('.crate-accion-t')?.textContent, b.getAttribute('aria-disabled')])),
          (a) => a.length === 3 && a[1][1] === null, 'la acción «Armar un set» no se habilitó')
        igual(acciones, [['Marcar cues', null], ['Armar un set', null], ['Exportar a Rekordbox (con cues)', null]], 'las tres acciones de la playlist tienen que estar habilitadas')
        // Desde la acción «Marcar cues» de la playlist, con el teclado (foco + Enter, como lo
        // haría alguien sin mouse): abre el primer tema analizado.
        await page.evaluate(() => [...document.querySelectorAll('.crate-accion')].find((b) => b.textContent.includes('Marcar cues')).focus())
        await page.keyboard.press('Enter')
        await page.waitForSelector('.edd[role="dialog"]', { timeout: ESPERA_MS })
        await esperarOnda(page, 'desde Marcar cues')
        igual(await page.$eval('#edd-titulo', (h) => h.firstChild.textContent), t.item.titulo, '«Marcar cues» no abrió el primer tema de la playlist')
        igual(await page.evaluate(() => document.activeElement?.classList.contains('edd-onda')), true, 'el foco no arranca en la onda (los atajos tienen que andar de entrada)')
        // Espacio: suena el audio LOCAL del tema y el cabezal lo sigue.
        await page.keyboard.press('Space')
        const ruta = `/api/radio/audio/${t.rid}`
        await hasta(() => sonando(page), (s) => json(s) === json([ruta]), 'Espacio no hizo sonar (solo) el audio del tema')
        await hasta(() => page.evaluate(() => [...window.__medios][0]?.currentTime ?? 0), (c) => c > 0.6, 'el audio no avanza')
        const sync = await page.evaluate(() => {
          const a = [...window.__medios].find((m) => !m.paused)
          const o = document.querySelector('.edd-onda')
          const [d, h] = [Number(o.dataset.desde), Number(o.dataset.hasta)]
          return { esperado: ((a.currentTime - d) / (h - d)) * 100, dibujado: parseFloat(document.querySelector('.edd-onda .edd-cabezal').style.left) }
        })
        afirmar(Math.abs(sync.esperado - sync.dibujado) < 1.5, `el cabezal no va con el audio: ${json(sync)}`)
        await page.keyboard.press('Space')
        await hasta(() => sonando(page), (s) => s.length === 0, 'Espacio no pausó')
        // Navegación entre temas y vuelta.
        await page.keyboard.press('PageDown')
        await hasta(() => page.$eval('#edd-titulo', (h) => h.firstChild.textContent), (v) => v === por['Sin grilla'].item.titulo, 'AvPág no pasó al tema siguiente de la playlist')
        igual(await page.$eval('.edd-sel select', (s) => s.options[s.selectedIndex].textContent), `2. ${por['Sin grilla'].item.artista ? `${por['Sin grilla'].item.artista} — ` : ''}${por['Sin grilla'].item.titulo}`, 'el selector de temas no sigue al tema abierto')
        await pasarTema(page, 'PageUp')
        await hasta(() => page.$eval('#edd-titulo', (h) => h.firstChild.textContent), (v) => v === t.item.titulo, 'RePág no volvió al tema anterior')
        await esperarOnda(page, 'vuelta', true)
        // Todo lo que se toca mide 44 px o más.
        igual(await chicos(page, '.edd button, .edd select, .edd input:not([type=checkbox]), .edd-snap, .edd [role=slider]'), [], 'hay controles del editor de menos de 44 px')
        // Atajos visibles y con aria-keyshortcuts.
        const atajos = await page.$$eval('.edd [aria-keyshortcuts]', (bs) => bs.map((b) => b.getAttribute('aria-keyshortcuts')))
        for (const k of ['Space', 'C', 'M', 'I', 'O', 'L', 'G', 'H', 'Q', '1', '8', 'Control+Z', 'PageDown', 'Escape']) afirmar(atajos.includes(k), `falta el atajo ${k} en los controles`)
        // Tab no se escapa del editor.
        await page.focus('.edd-cerrar')
        for (let i = 0; i < 80; i++) {
          await page.keyboard.press('Tab')
          afirmar(await page.evaluate(() => Boolean(document.activeElement?.closest('.edd'))), `Tab sacó el foco del editor (paso ${i + 1})`)
        }
        // 400 px: nada se sale del ancho.
        await page.setViewport({ width: 400, height: 860 })
        await hasta(() => page.evaluate(() => document.documentElement.clientWidth), (w) => w <= 400, 'el viewport no pasó a 400 px')
        const d = await desbordeDe(page)
        afirmar(d.scroll <= d.ancho && d.fuera.length === 0, `editor a 400 px: hay contenido fuera del ancho: ${json(d)}`)
        igual(await page.$eval('.edd-fondo', (f) => f.scrollWidth <= f.clientWidth), true, 'a 400 px el editor tiene desplazamiento horizontal')
        // Esc cierra y el foco vuelve a lo que lo abrió.
        await page.focus('.edd-onda')
        await page.keyboard.press('Escape')
        await hasta(() => page.$('.edd'), (v) => !v, 'Escape no cerró el editor')
        // El botón mismo (no el <body>, que también «contiene» el texto: así el test era ciego).
        igual(await page.evaluate(() => {
          const a = document.activeElement
          return a && a.classList.contains('crate-accion') ? a.querySelector('.crate-accion-t')?.textContent ?? null : `${a?.tagName}.${a?.className}`
        }), 'Marcar cues', 'al cerrar, el foco no volvió a «Marcar cues»')
        igual(await sonando(page), [], 'al cerrar quedó algo sonando')
      })
    }],

    ['editor D (f54): con DPR 1,25 (Windows al 125 %) el kick se dibuja en el azul puro de los graves, no rayado', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1.25 })
        await abrirEditorD(page, ctx, t.item.titulo)
        igual(await page.evaluate(() => window.devicePixelRatio), 1.25, 'la página no quedó a DPR 1,25')
        await clicZoom(page, '2')
        // Sin grilla ni marcas encima: se mide la onda sola (el dibujo del canvas). Las líneas de
        // tiempo y de kick son a propósito y se miden en su propio caso: acá se esconden.
        await page.addStyleTag({ content: '.edd-tick,.edd-golpe{display:none!important}' })
        await page.focus('.edd-onda')
        await page.keyboard.press('g')
        await hasta(() => page.$$eval('.edd-beat', (b) => b.length), (n) => n === 0, 'G no ocultó la grilla')
        // Se mide la PANTALLA, no el bitmap del canvas (auditoría f54: un canvas en píxeles CSS
        // que el navegador estira ×1,25 tiene el bitmap limpio y la pantalla sucia): una captura
        // de puppeteer de la onda, a 1,25 píxeles físicos por píxel CSS. En los 40 ms después de
        // cada kick generado, de los píxeles «azulados», cuántos son el azul PURO de los graves (el
        // de la muestra de la leyenda); y de TODOS los píxeles, cuántos son exactamente uno de los
        // colores de la onda o el fondo (un estirado mezcla cada borde entre colores).
        const pureza = async (ts) => {
          const caja = await page.$eval('.edd-onda', (e) => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, width: r.width, height: r.height } })
          const b64 = await page.screenshot({ clip: caja, encoding: 'base64' })
          return page.evaluate(async (png, lista) => {
            const o = document.querySelector('.edd-onda')
            const [desde, hasta] = [Number(o.dataset.desde), Number(o.dataset.hasta)]
            const css = getComputedStyle(document.documentElement)
            const rgb = (v) => { const h = v.trim().replace('#', ''); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) }
            const m = getComputedStyle(document.querySelector('.edd-sw.is-graves')).backgroundColor.match(/\d+/g).map(Number)
            const paleta = [m, rgb(css.getPropertyValue('--edd-medios')), rgb(css.getPropertyValue('--edd-agudos')), rgb(css.getPropertyValue('--edd-fondo-onda'))]
            const img = new Image()
            img.src = `data:image/png;base64,${png}`
            await img.decode()
            const cv = document.createElement('canvas')
            cv.width = img.naturalWidth
            cv.height = img.naturalHeight
            const c = cv.getContext('2d')
            c.drawImage(img, 0, 0)
            const cerca = (p, q) => Math.abs(p[0] - q[0]) <= 3 && Math.abs(p[1] - q[1]) <= 3 && Math.abs(p[2] - q[2]) <= 3
            return lista.map((t) => {
              const x0 = Math.floor(((t - desde) / (hasta - desde)) * cv.width)
              const x1 = Math.ceil(((t + 0.04 - desde) / (hasta - desde)) * cv.width)
              const d = c.getImageData(x0, 0, Math.max(1, x1 - x0), cv.height).data
              let azules = 0, puros = 0, total = 0, dePaleta = 0
              for (let i = 0; i < d.length; i += 4) {
                const p = [d[i], d[i + 1], d[i + 2]]
                total++
                if (paleta.some((q) => cerca(p, q))) dePaleta++
                if (p[2] > 150 && p[2] - p[0] > 80 && p[2] - p[1] > 40) {
                  azules++
                  if (cerca(p, m)) puros++
                }
              }
              const r3 = (x) => Math.round(x * 1000) / 1000
              return { ancho: cv.width, bitmap: o.querySelector('canvas').width, azules, fraccion: azules ? r3(puros / azules) : 0, paleta: r3(dePaleta / total) }
            })
          }, b64, ts)
        }
        const p = 60 / t.bpm
        const kicks = [0, 1, 2, 3].map((j) => t.primer_kick_s + j * p)
        const v = await hasta(() => pureza(kicks), (x) => x.every((k) => k.azules > 200), 'no se dibujaron los graves de los kicks')
        for (const [k, x] of v.entries()) afirmar(x.fraccion >= 0.85, `kick ${k + 1} a DPR 1,25: en pantalla solo ${x.fraccion} de los píxeles azules son el azul puro de los graves (rayado): ${json(v)}`)
        // Y a la resolución de la pantalla: un píxel del dibujo por píxel físico de la captura. Un
        // canvas en píxeles CSS que el navegador estira no se ve rayado (Chrome lo agranda casi sin
        // mezclar: medido 0,93-0,99) pero pierde 1 de cada 5 columnas de detalle del kick.
        afirmar(Math.abs(v[0].bitmap - v[0].ancho) <= 1, `la onda no se dibuja a la resolución de la pantalla: ${v[0].bitmap} columnas de dibujo para ${v[0].ancho} píxeles físicos`)
        // La grilla oculta queda guardada en este navegador: se vuelve a mostrar para los casos que siguen.
        await page.focus('.edd-onda')
        await page.keyboard.press('g')
        await hasta(() => page.$$eval('.edd-beat', (b) => b.length), (n) => n > 0, 'G no volvió a mostrar la grilla')
      })
    }],

    ['editor D (f54): fallas del servidor — un éxito no borra el aviso de lo perdido; un rechazo relee la lista y sale del historial', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        await abrirEditorD(page, ctx, t.item.titulo)
        const estadoTxt = () => page.$eval('.edd-estado', (e) => e.textContent)
        const avisoTxt = () => page.$eval('.edd-falla', (e) => e.textContent).catch(() => null)

        // (1) La primera M recibe un 500 del servidor; la segunda sale bien.
        let fallar = 1
        await interceptar(page, async (req) => {
          if (fallar > 0 && req.method() === 'POST' && /\/marcas$/.test(new URL(req.url()).pathname)) {
            fallar -= 1
            await req.respond({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'falla simulada del servidor' }) })
            return true
          }
          return false
        })
        await page.focus('.edd-onda')
        igual((await conTecla(page, ['m'], 'POST')).status, 500, 'la primera M tenía que recibir el 500 simulado')
        await page.keyboard.press('ArrowRight')
        const bien = await conTecla(page, ['m'], 'POST')
        igual(bien.status, 201, 'la segunda M tenía que guardarse')
        let api_ = await filasComoApi(page, ctx, t.rid, 'después de una M fallida y una buena')
        igual(api_.map((m) => m.id), [bien.data.marca.id], 'en la base quedó solo la memory que se guardó')
        let est = await hasta(estadoTxt, (s) => !s.includes('Guardando'), 'el estado no terminó de guardar')
        afirmar(!est.startsWith('✓') && est.includes('1 cambio no se guardó'), `con una memory perdida el estado no puede decir «✓ Guardado» a secas: ${json(est)}`)
        const av = await avisoTxt()
        afirmar(av && av.includes('Memory cue en') && av.includes('falla simulada del servidor'), `el aviso de la memory perdida (con el motivo del server) se borró con el éxito: ${json(av)}`)
        await page.click('.edd-descartar')
        est = await hasta(estadoTxt, (s) => s.startsWith('✓ Guardado'), 'después de descartar el aviso (lista igual al server) tiene que decir «✓ Guardado»')

        // (2a) Un hot cue que otra pestaña borra: deshacer su creación ya está hecho en la base:
        // Ctrl+Z relee y no manda nada (ni un 404).
        const avisoEd = () => page.$eval('.edd-aviso', (e) => e.textContent)
        const ctrl = async (k) => { await page.keyboard.down('Control'); await page.keyboard.press(k); await page.keyboard.up('Control') }
        await page.focus('.edd-onda')
        await page.keyboard.press('ArrowRight')
        const cue = await conTecla(page, ['c'], 'POST')
        igual(cue.status, 201, 'C no creó el hot cue')
        igual((await apiPedir(ctx, `${rutaMarcas(t.rid)}/${cue.data.marca.id}`, 'DELETE')).status, 200, 'la «otra pestaña» no pudo borrar el cue')
        let escrituras = pedidosDeMarcas(page)
        await ctrl('z')
        await hasta(avisoEd, (x) => x === 'Deshecho.', 'Ctrl+Z sobre un cue que ya no está no terminó en «Deshecho.»')
        igual(escrituras, [], 'deshacer algo que la base ya tiene hecho no puede mandar escrituras')
        await filasComoApi(page, ctx, t.rid, 'después de deshacer el cue ajeno')
        // (2b) Renombrar la memory y que otra pestaña la borre: Ctrl+Z (el PATCH de vuelta) da
        // 404; la lista se relee, el paso sale del historial y el próximo Ctrl+Z no lo repite.
        await abrirFila(page, bien.data.marca.id)
        const nomMem = `.edd-fila[data-id="${bien.data.marca.id}"] .edd-in-n`
        await page.click(nomMem)
        await page.type(nomMem, 'Break')
        igual((await conTecla(page, ['Enter'], 'PATCH')).status, 200, 'no se renombró la memory')
        igual((await apiPedir(ctx, `${rutaMarcas(t.rid)}/${bien.data.marca.id}`, 'DELETE')).status, 200, 'la «otra pestaña» no pudo borrar la memory')
        await page.focus('.edd-onda')
        const z1 = await conTecla(page, ['Control', 'z'], 'PATCH')
        igual(z1.status, 404, 'deshacer el nombre de una memory que ya no existe tiene que dar 404')
        await filasComoApi(page, ctx, t.rid, 'después del 404 la lista tiene que ser la del servidor')
        afirmar((await avisoTxt() || '').includes(z1.data.error), `el aviso no dice el motivo del server (${z1.data.error})`)
        await hasta(avisoEd, (x) => x.includes('Lo saqué del historial'), 'el rechazo no dice que el paso salió del historial')
        escrituras = pedidosDeMarcas(page)
        await ctrl('z')     // el paso anterior: crear la memory → ya no está: «Deshecho.» sin pedidos
        await hasta(avisoEd, (x) => x === 'Deshecho.', 'el segundo Ctrl+Z no deshizo el paso anterior')
        await ctrl('z')
        await hasta(avisoEd, (x) => x === 'No hay nada para deshacer.', 'después del paso rechazado y el anterior, no tiene que quedar nada para deshacer')
        igual(escrituras, [], 'el segundo Ctrl+Z repitió el pedido imposible (el PATCH del 404)')
        api_ = await filasComoApi(page, ctx, t.rid, 'después de los Ctrl+Z')
        igual(api_, [], 'la base tiene que quedar vacía')

        // (3) «Al cursor» sobre una marca que otra pestaña borró: 404 y la fila se va.
        const otra = (await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'memory', inicio: 2 })).data.marca
        // Fijar A en el cursor: la lista que vuelve trae también la memory de la otra pestaña. Se
        // espera la respuesta del POST antes de comparar con la API (si no, la API se leía antes
        // de que el server lo procesara: el caso era intermitente).
        const postA = page.waitForResponse((r) => r.request().method() === 'POST' && /\/marcas$/.test(new URL(r.url()).pathname), { timeout: ESPERA_MS })
        await page.click('.edd-pad[data-pad="0"]')
        igual((await postA).status(), 201, 'el pad A no se fijó')
        await filasComoApi(page, ctx, t.rid, 'la lista no trajo la memory creada por la otra pestaña')
        await apiPedir(ctx, `${rutaMarcas(t.rid)}/${otra.id}`, 'DELETE')
        const patch = page.waitForResponse((r) => r.request().method() === 'PATCH', { timeout: ESPERA_MS })
        await abrirFila(page, otra.id)
        await page.click(`.edd-fila[data-id="${otra.id}"] .edd-alcursor`)
        const rp = await patch
        igual(rp.status(), 404, '«Al cursor» sobre una marca borrada tenía que dar 404')
        await hasta(() => page.$(`.edd-fila[data-id="${otra.id}"]`), (x) => !x, 'la fila de la marca borrada quedó en la lista')
        await filasComoApi(page, ctx, t.rid, 'después del 404 de «Al cursor»')

        // (4) Un pad que otra pestaña ocupó: la tecla 2 (pad B, vacío en esta pantalla) da 400 y
        // la lista pasa a mostrar el pad B de la otra pestaña.
        const ajena = (await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'cue', inicio: 5, num: 1 })).data.marca
        igual(await page.$eval('.edd-pad[data-pad="1"]', (b) => b.classList.contains('is-vacio')), true, 'el pad B tendría que verse vacío antes de releer')
        await page.focus('.edd-onda')
        await page.keyboard.press('ArrowRight')            // otro tiempo: en el del pad A ya hay un cue
        const b = await conTecla(page, ['2'], 'POST')
        igual(b.status, 400, 'fijar el pad B ocupado por otra pestaña tenía que dar 400')
        await filasComoApi(page, ctx, t.rid, 'después del 400 la lista tiene que traer el pad B de la otra pestaña')
        igual(await page.$eval('.edd-pad[data-pad="1"] .edd-pad-tiempo', (e) => e.textContent), tiempoTabla(ajena.inicio), 'el pad B no muestra el cue que puso la otra pestaña')
        afirmar((await avisoTxt() || '').includes(b.data.error), `el aviso no dice el motivo del 400 (${b.data.error})`)
        // Desde «Entendido» fallaron tres (el PATCH de 2b, el de 3 y el POST de 4): en plural bien escrito.
        await hasta(estadoTxt, (s) => s.includes('3 cambios no se guardaron (ver el aviso)'), 'el estado no cuenta las tres fallas (o las pluraliza mal)')

        // (5) Un cambio de tipo deshecho A MEDIAS: memory → hot cue (crear el cue + borrar la
        // memory); otra pestaña borra el cue; Ctrl+Z recrea la memory (sale) y borrar el cue da
        // 404. La memory recreada ya está en el servidor: el próximo Ctrl+Z la tiene que sacar.
        await page.focus('.edd-onda')
        await page.keyboard.press('ArrowRight')
        const mem5 = await conTecla(page, ['m'], 'POST')
        igual(mem5.status, 201, 'M no creó la memory del paso (5)')
        await filasComoApi(page, ctx, t.rid, 'con la memory del paso (5)')
        const nuevoCue = page.waitForResponse((r) => r.request().method() === 'POST', { timeout: ESPERA_MS })
        const borraMem = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await abrirFila(page, mem5.data.marca.id)
        await page.select(`.edd-fila[data-id="${mem5.data.marca.id}"] select.edd-tipo`, 'cue')
        const cue5 = await (await nuevoCue).json()
        igual((await borraMem).status(), 200, 'cambiar el tipo no borró la memory')
        await filasComoApi(page, ctx, t.rid, 'después de pasar la memory a hot cue')
        igual((await apiPedir(ctx, `${rutaMarcas(t.rid)}/${cue5.marca.id}`, 'DELETE')).status, 200, 'la «otra pestaña» no pudo borrar el cue nuevo')
        await page.focus('.edd-onda')
        // Deshacer relee antes de mandar nada (yaHecha): el borrado del cue que la otra pestaña
        // ya borró NO se manda (en la otra línea de f54 se mandaba y daba 404: el paso quedaba a
        // medias y había que rescatar lo guardado; acá no queda a medias).
        const escr5 = pedidosDeMarcas(page)
        const recrea = page.waitForResponse((r) => r.request().method() === 'POST', { timeout: ESPERA_MS })
        await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control')
        const memVuelta = await (await recrea).json()
        await hasta(() => page.$eval('.edd-aviso', (e) => e.textContent), (x) => x === 'Deshecho.', 'el Ctrl+Z con el cue ya borrado por otra pestaña no terminó bien')
        igual(escr5.filter((x) => x.startsWith('DELETE')), [], 'Ctrl+Z mandó el borrado de un cue que ya no estaba (la relectura no lo tiene)')
        igual([memVuelta.marca.tipo, memVuelta.marca.inicio], ['memory', mem5.data.marca.inicio], 'Ctrl+Z no recreó la memory')
        await filasComoApi(page, ctx, t.rid, 'después del Ctrl+Z con el cue ya borrado')
        await hasta(estadoTxt, (s) => !s.includes('Guardando'), 'el estado no terminó de guardar')
        const nFallas = () => page.$$eval('.edd-fallas li', (ls) => ls.length)
        const fallasAntes = await nFallas()
        const z5 = await conTecla(page, ['Control', 'z'], 'DELETE')
        igual([z5.status, (z5.data.marcas || []).some((x) => x.id === memVuelta.marca.id)], [200, false],
          'el Ctrl+Z siguiente no sacó la memory que quedó guardada a medias (el paso a medias se perdió del historial)')
        await filasComoApi(page, ctx, t.rid, 'después de deshacer lo que quedó a medias')
        // Y el Ctrl+Z que sigue es el paso ANTERIOR del historial (el hot cue del pad A del paso
        // (3)), no otra vez la memory: con la memory recreada dos veces en el historial, este
        // pedido sería un DELETE de la memory que ya no existe (404 y un aviso de falla espurio).
        const padA = (await marcasApi(ctx, t.rid)).find((x) => x.tipo === 'cue' && x.num === 0)
        afirmar(padA, 'antes del último Ctrl+Z tendría que estar el hot cue del pad A del paso (3)')
        const z6 = await conTecla(page, ['Control', 'z'], 'DELETE')
        igual([z6.status, (z6.data.marcas || []).some((x) => x.id === padA.id)], [200, false],
          'el último Ctrl+Z no borró el hot cue del pad A (el historial repitió un paso)')
        await filasComoApi(page, ctx, t.rid, 'después de deshacer el pad A')
        igual(await nFallas(), fallasAntes, 'deshacer lo que quedó a medias y el paso anterior agregó avisos de falla')

        // (6) REHACER a medias: memory → hot cue, Ctrl+Z (entero), otra pestaña borra la memory
        // recreada y Ctrl+Y: el hot cue se vuelve a crear (sale) y borrar la memory da 404. Ese
        // hot cue nuevo no lo crea ningún otro paso: tiene que quedar en el historial, y el
        // próximo Ctrl+Z lo saca (si se perdiera, el Ctrl+Z iría a la memory borrada: 404).
        await page.focus('.edd-onda')
        await page.keyboard.press('ArrowRight')
        const mem6 = await conTecla(page, ['m'], 'POST')
        igual(mem6.status, 201, '(6): M no creó la memory')
        await filasComoApi(page, ctx, t.rid, '(6): con la memory')
        const cue6p = page.waitForResponse((r) => r.request().method() === 'POST', { timeout: ESPERA_MS })
        const mem6d = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await abrirFila(page, mem6.data.marca.id)
        await page.select(`.edd-fila[data-id="${mem6.data.marca.id}"] select.edd-tipo`, 'cue')
        igual([(await cue6p).status(), (await mem6d).status()], [201, 200], '(6): el cambio de tipo no se hizo entero')
        await filasComoApi(page, ctx, t.rid, '(6): con el hot cue')
        await page.focus('.edd-onda')
        const memDeVuelta = page.waitForResponse((r) => r.request().method() === 'POST', { timeout: ESPERA_MS })
        const cueSeVa = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control')
        const memX = await (await memDeVuelta).json()
        igual((await cueSeVa).status(), 200, '(6): Ctrl+Z no sacó el hot cue')
        await filasComoApi(page, ctx, t.rid, '(6): después del Ctrl+Z entero')
        await hasta(estadoTxt, (s) => !s.includes('Guardando'), '(6): el estado no terminó de guardar')
        igual((await apiPedir(ctx, `${rutaMarcas(t.rid)}/${memX.marca.id}`, 'DELETE')).status, 200, '(6): la «otra pestaña» no pudo borrar la memory recreada')
        const escr6 = pedidosDeMarcas(page)
        const cueOtraVez = page.waitForResponse((r) => r.request().method() === 'POST', { timeout: ESPERA_MS })
        await page.keyboard.down('Control'); await page.keyboard.press('y'); await page.keyboard.up('Control')
        const cueY = await (await cueOtraVez).json()
        await hasta(() => page.$eval('.edd-aviso', (e) => e.textContent), (x) => x === 'Rehecho.', '(6): el Ctrl+Y con la memory ya borrada por otra pestaña no terminó bien')
        igual(escr6.filter((x) => x.startsWith('DELETE')), [], '(6): Ctrl+Y mandó el borrado de una memory que ya no estaba')
        await filasComoApi(page, ctx, t.rid, '(6): después del Ctrl+Y a medias')
        await hasta(estadoTxt, (s) => !s.includes('Guardando'), '(6): el estado no terminó de guardar')
        const z7 = await conTecla(page, ['Control', 'z'], 'DELETE')
        igual([z7.status, (z7.data.marcas || []).some((x) => x.id === cueY.marca.id)], [200, false],
          '(6): el Ctrl+Z después del Ctrl+Y a medias no sacó el hot cue que quedó guardado (se perdió del historial)')
        await filasComoApi(page, ctx, t.rid, '(6): después de deshacer lo que quedó a medias')
      })
    }],
    ['editor D (f54): lo que vuelve tarde con OTRO tema abierto no toca el tema nuevo (ni historial, ni aviso, ni marcas)', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const [t1, t2, t3] = [por['Con grilla'], por['Sin grilla'], por['Sin BPM']]
        // Las escrituras de marcas que se pidan demorar salen 1,5 s más tarde (llegan al server
        // con el otro tema ya abierto).
        const demorar = { DELETE: false, POST: false, ms: 1500 }
        await interceptar(page, async (req) => {
          if (!demorar[req.method()] || !/\/marcas(\/\d+)?$/.test(new URL(req.url()).pathname)) return false
          await new Promise((r) => setTimeout(r, demorar.ms))
          await req.continue()
          return true
        })
        const titulo = () => page.$eval('#edd-titulo', (h) => h.firstChild.textContent)
        const avisoEd = () => page.$eval('.edd-aviso', (e) => e.textContent)
        const ctrlZ = async () => { await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control') }
        // Un tiempo de gracia para afirmar que algo NO pasó: la respuesta ya llegó (se esperó) y
        // la página la procesa en un par de microtareas.
        const gracia = () => new Promise((r) => setTimeout(r, 400))
        const escrituras = pedidosDeMarcas(page)
        const aOtro = (rid, desde) => escrituras.slice(desde).filter((e) => e.includes(rid))
        const pasarA = async (tecla, t, que) => {
          await page.keyboard.press(tecla)
          await hasta(titulo, (v) => v === t.item.titulo, `${que}: no pasó a «${t.item.titulo}»`)
          await esperarOnda(page, que)
        }

        // (1) El repro de la auditoría: C crea el hot cue A, Mayús+1 lo borra con el DELETE
        // demorado y AvPág. El aviso y el paso del borrado son del tema 1, no del 2.
        await abrirEditorD(page, ctx, t1.item.titulo)
        await page.focus('.edd-onda')
        igual((await conTecla(page, ['c'], 'POST')).status, 201, 'C no creó el hot cue A')
        demorar.DELETE = true
        const tarde = page.waitForResponse((x) => x.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await page.keyboard.down('Shift'); await page.keyboard.press('Digit1'); await page.keyboard.up('Shift')
        await pasarA('PageDown', t2, 'tema 2')
        igual((await tarde).status(), 200, 'el DELETE demorado no llegó')
        demorar.DELETE = false
        await gracia()
        igual(await marcasApi(ctx, t1.rid), [], 'el DELETE tardío igual tenía que borrar el cue en SU tema (el 1)')
        igual(await avisoEd(), '', 'el aviso «Quité…» del tema 1 apareció en el tema 2')
        let desde = escrituras.length
        await ctrlZ()
        await hasta(avisoEd, (x) => x === 'No hay nada para deshacer.', 'en el tema 2 no hay nada para deshacer')
        igual(escrituras.slice(desde), [], 'Ctrl+Z en el tema 2 mandó una escritura (la marca fantasma)')
        igual(await marcasApi(ctx, t2.rid), [], 'apareció una marca en el tema 2')

        // (2) La variante benigna: una memory con el POST demorado y AvPág. El paso de la memory
        // no puede quedar en el historial del tema 3 (antes, Ctrl+Z daba un 404 de un paso ajeno).
        demorar.POST = true
        const tarde2 = page.waitForResponse((x) => x.request().method() === 'POST', { timeout: ESPERA_MS })
        await page.focus('.edd-onda')
        await page.keyboard.press('m')
        await pasarA('PageDown', t3, 'tema 3')
        igual((await tarde2).status(), 201, 'el POST demorado no llegó')
        demorar.POST = false
        await gracia()
        igual((await marcasApi(ctx, t2.rid)).map((m) => m.tipo), ['memory'], 'la memory tardía igual tenía que quedar en SU tema (el 2)')
        desde = escrituras.length
        await page.focus('.edd-onda')
        await ctrlZ()
        await hasta(avisoEd, (x) => x === 'No hay nada para deshacer.', 'en el tema 3 no hay nada para deshacer')
        igual(escrituras.slice(desde), [], 'Ctrl+Z en el tema 3 mandó una escritura (el paso de la memory del tema 2)')
        await filasComoApi(page, ctx, t3.rid, 'la lista del tema 3')

        // (3) Cambiar el tipo (crear la nueva + borrar la vieja) con el POST demorado y AvPág: se
        // termina en el tema de ORIGEN (queda solo la nueva) y el tema nuevo no se entera.
        await pasarA('PageUp', t2, 'de vuelta al tema 2')
        const mem = (await marcasApi(ctx, t2.rid))[0]
        await filasComoApi(page, ctx, t2.rid, 'la lista del tema 2')
        demorar.POST = true
        const tarde3 = page.waitForResponse((x) => x.request().method() === 'POST', { timeout: ESPERA_MS })
        await abrirFila(page, mem.id)
        await page.select(`.edd-fila[data-id="${mem.id}"] select.edd-tipo`, 'cue')
        await page.focus('.edd-onda')
        desde = escrituras.length
        await pasarA('PageDown', t3, 'otra vez el tema 3')
        igual((await tarde3).status(), 201, 'el POST demorado del cambio de tipo no llegó')
        demorar.POST = false
        const t2final = await hasta(() => marcasApi(ctx, t2.rid), (m) => m.length === 1 && m[0].tipo === 'cue',
          'el cambio de tipo no se terminó en el tema 2 (tenía que quedar solo el hot cue)')
        igual(t2final[0].inicio, mem.inicio, 'el hot cue nuevo no quedó en el tiempo de la memory')
        await page.focus('.edd-onda')
        await ctrlZ()
        await hasta(avisoEd, (x) => x === 'No hay nada para deshacer.', 'en el tema 3 no hay nada para deshacer (el grupo es del 2)')
        igual(aOtro(t3.rid, desde), [], 'el cambio de tipo del tema 2 escribió en el tema 3')
        igual(await marcasApi(ctx, t3.rid), [], 'apareció una marca en el tema 3')

        // (4) Ir y VOLVER al mismo tema con un pedido en vuelo (A → B → A): el tema es el mismo,
        // pero lo que vuelve es de la visita anterior; no toca la nueva (la época cambió).
        await page.focus('.edd-onda')
        igual((await conTecla(page, ['c'], 'POST')).status, 201, 'C no creó el hot cue en el tema 3')
        Object.assign(demorar, { DELETE: true, ms: 4000 })
        const tarde4 = page.waitForResponse((x) => x.request().method() === 'DELETE', { timeout: 15000 })
        await page.keyboard.down('Shift'); await page.keyboard.press('Digit1'); await page.keyboard.up('Shift')
        await pasarA('PageUp', t2, 'ida al tema 2')
        await pasarA('PageDown', t3, 'vuelta al tema 3')
        igual((await tarde4).status(), 200, 'el DELETE demorado no llegó')
        Object.assign(demorar, { DELETE: false, ms: 1500 })
        await gracia()
        igual(await marcasApi(ctx, t3.rid), [], 'el DELETE tardío igual tenía que borrar el cue')
        igual(await avisoEd(), '', 'el aviso «Quité…» de la visita anterior apareció al volver al mismo tema')
        // Pero la lista sí muestra lo que el server tiene (el cue ya no está): se relee.
        await filasComoApi(page, ctx, t3.rid, 'al volver al mismo tema, la lista no muestra lo que hizo el pedido tardío')
        desde = escrituras.length
        await page.focus('.edd-onda')
        await ctrlZ()
        await hasta(avisoEd, (x) => x === 'No hay nada para deshacer.', 'al volver al tema, el historial de la visita anterior no puede quedar')
        igual(escrituras.slice(desde), [], 'Ctrl+Z al volver mandó una escritura de la visita anterior')
      })
    }],

    ['editor D (f54): deshacer un grupo que falla a mitad (5xx) se reintenta sin duplicar; un rehacer rechazado sale del historial', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        let fallarDelete = 0
        await interceptar(page, async (req) => {
          if (fallarDelete > 0 && req.method() === 'DELETE' && /\/marcas\/\d+$/.test(new URL(req.url()).pathname)) {
            fallarDelete -= 1
            await req.respond({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'falla simulada a mitad del grupo' }) })
            return true
          }
          return false
        })
        const avisoEd = () => page.$eval('.edd-aviso', (e) => e.textContent)
        const ctrl = async (k) => { await page.keyboard.down('Control'); await page.keyboard.press(k); await page.keyboard.up('Control') }
        const escrituras = pedidosDeMarcas(page)
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.focus('.edd-onda')
        await page.keyboard.press('ArrowRight')
        const mem = (await conTecla(page, ['m'], 'POST')).data.marca
        // memory → hot cue: un paso «grupo» (crear el cue + borrar la memory).
        const borra = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await abrirFila(page, mem.id)
        await page.select(`.edd-fila[data-id="${mem.id}"] select.edd-tipo`, 'cue')
        igual((await borra).status(), 200, 'el cambio de tipo no borró la memory')
        let api_ = await filasComoApi(page, ctx, t.rid, 'después del cambio de tipo')
        const cue = api_.find((m) => m.tipo === 'cue')
        afirmar(cue && api_.length === 1, `tenía que quedar solo el hot cue: ${json(api_)}`)

        // Ctrl+Z = crear la memory (sale bien) + borrar el cue (500): queda a medias.
        fallarDelete = 1
        const quinientos = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await page.focus('.edd-onda')
        await ctrl('z')
        igual((await quinientos).status(), 500, 'el DELETE del cue tenía que recibir el 500 simulado')
        await hasta(avisoEd, (x) => x.includes('quedó a medias'), 'el aviso no dice que el deshacer quedó a medias')
        api_ = await filasComoApi(page, ctx, t.rid, 'a medias: la lista tiene que ser la del servidor')
        igual(api_.map((m) => m.tipo).sort(), ['cue', 'memory'], 'a medias, en la base están la memory recreada y el cue')
        // Reintentar: la memory ya está (no se vuelve a crear), solo falta borrar el cue.
        let desde = escrituras.length
        const segunda = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await ctrl('z')
        igual((await segunda).status(), 200, 'el reintento no borró el cue')
        await hasta(avisoEd, (x) => x === 'Deshecho.', 'el reintento no terminó en «Deshecho.»')
        igual(escrituras.slice(desde), [`DELETE ${rutaMarcas(t.rid)}/${cue.id}`], 'el reintento tenía que mandar SOLO el borrado del cue (nada de volver a crear la memory)')
        api_ = await filasComoApi(page, ctx, t.rid, 'después del reintento')
        igual(api_.map((m) => [m.tipo, m.inicio]), [['memory', mem.inicio]], 'tiene que quedar UNA memory, en su tiempo (sin duplicar)')

        // Rehacer rechazado: otra pestaña ocupa el pad del cue (otro tiempo) → Ctrl+Y da 400,
        // el paso sale del futuro, y Ctrl+Z sigue con el paso anterior (crear la memory).
        const ajena = (await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'cue', inicio: 9, num: cue.num })).data.marca
        afirmar(ajena && ajena.num === cue.num, 'la «otra pestaña» no pudo ocupar el pad')
        await page.focus('.edd-onda')
        const y1 = await conTecla(page, ['Control', 'y'], 'POST')
        igual(y1.status, 400, 'rehacer el cue en un pad ocupado tenía que dar 400')
        await hasta(avisoEd, (x) => x.startsWith('Rehacer no se pudo') && x.includes('Lo saqué del historial'), 'el rehacer rechazado no dice que salió del historial')
        desde = escrituras.length
        await ctrl('y')
        await hasta(avisoEd, (x) => x === 'No hay nada para rehacer.', 'después del rehacer rechazado no tiene que quedar nada para rehacer')
        const z = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await ctrl('z')
        const rz = await z
        igual([rz.status(), new URL(rz.url()).pathname], [200, `${rutaMarcas(t.rid)}/${api_[0].id}`], 'Ctrl+Z después del rehacer rechazado tenía que deshacer la memory (el paso anterior)')
        igual(escrituras.slice(desde), [`DELETE ${rutaMarcas(t.rid)}/${api_[0].id}`], 'después del rechazo no se tiene que repetir ningún pedido')
        await filasComoApi(page, ctx, t.rid, 'al final')
      })
    }],

    ['editor D (f54): sin red: «releyendo…» y después «sin confirmar»; Volver a leer reconcilia; Esc pide confirmación y el aviso pasa a la playlist', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        let red = true
        await interceptar(page, async (req) => {
          if (red || !/\/marcas(\/\d+)?$/.test(new URL(req.url()).pathname)) return false
          // Sin red: las escrituras no salen; la relectura tarda 1,2 s en fallar (para ver «releyendo…»).
          if (req.method() === 'GET') await new Promise((r) => setTimeout(r, 1200))
          await req.abort('internetdisconnected')
          return true
        })
        const estadoTxt = () => page.$eval('.edd-estado', (e) => e.textContent)
        const avisoEd = () => page.$eval('.edd-aviso', (e) => e.textContent)
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.focus('.edd-onda')
        red = false
        await page.keyboard.press('m')
        await hasta(estadoTxt, (s) => s === '⚠ 1 cambio sin confirmar: releyendo el servidor…', 'mientras relee no puede decir que no pudo releer')
        await hasta(estadoTxt, (s) => s === '⚠ 1 cambio sin confirmar y no pude volver a leer el servidor: la lista puede no ser la que quedó guardada.',
          'con la relectura caída tiene que decir «sin confirmar»')
        const av = await page.$eval('.edd-falla', (e) => e.textContent)
        afirmar(av.includes('Memory cue en') && av.includes('No pude conectar'), `el aviso no dice qué no se guardó ni por qué: ${json(av)}`)
        // Esc no cierra a la primera.
        await page.keyboard.press('Escape')
        await hasta(avisoEd, (x) => x.includes('Esc otra vez'), 'el primer Esc no avisó que hay cambios sin confirmar')
        afirmar(await page.$('.edd'), 'el primer Esc cerró el editor con cambios sin confirmar')
        // Vuelve la red: «Volver a leer del servidor» reconcilia (la memory no llegó a la base).
        red = true
        await page.click('.edd-releer')
        await hasta(estadoTxt, (s) => !s.startsWith('⚠') && s.includes('1 cambio no se guardó'), 'Volver a leer no reconcilió la lista')
        await filasComoApi(page, ctx, t.rid, 'después de releer')
        // Otra vez sin red y sin poder releer: Esc, Esc cierra igual y el aviso queda en la playlist.
        red = false
        await page.focus('.edd-onda')
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('m')
        await hasta(estadoTxt, (s) => s.startsWith('⚠ 1 cambio sin confirmar y no pude'), 'la segunda falla no quedó sin confirmar')
        await page.keyboard.press('Escape')
        await hasta(avisoEd, (x) => x.includes('Esc otra vez'), 'el primer Esc tenía que pedir confirmación')
        await page.keyboard.press('Escape')
        await hasta(() => page.$('.edd'), (v) => !v, 'el segundo Esc tenía que cerrar igual')
        const toast = await hasta(() => page.$$eval('.toast-danger', (ts) => ts.map((x) => x.textContent)), (ts) => ts.length > 0, 'al cerrar con cambios sin confirmar no apareció el aviso en la playlist')
        afirmar(toast[0].includes('1 cambio de cues sin confirmar') && toast[0].includes('Memory cue en'), `el aviso de la playlist no dice qué quedó sin confirmar: ${json(toast)}`)
        red = true
      })
    }],

    ['editor D (f54): una respuesta vieja que llega tarde no pisa la lista nueva', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        // Las creaciones van de a una (crítica de f54, P0-2): la escritura que compite con la
        // creación demorada es un RENOMBRE de una memory que ya estaba.
        const pre = (await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'memory', inicio: 9 })).data.marca
        // Un solo interceptor para todo el caso, con modos:
        //   retener: la creación llega al server ya, su RESPUESTA vuelve 1,2 s tarde (parte 1);
        //   encolar: el PEDIDO de la creación sale 1,2 s tarde (parte 2);
        //   fallarPatch: el renombre recibe un 500 sin llegar al server (dispara la relectura).
        let retener = 0
        let encolar = 0
        let fallarPatch = 0
        await interceptar(page, async (req) => {
          const u = new URL(req.url())
          if (req.method() === 'POST' && /\/marcas$/.test(u.pathname)) {
            if (retener > 0) {
              retener -= 1
              const r = await fetch(ctx.url + u.pathname, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: req.postData() })
              const cuerpo = await r.text()
              await new Promise((ok) => setTimeout(ok, 1200))
              await req.respond({ status: r.status, contentType: 'application/json', body: cuerpo })
              return true
            }
            if (encolar > 0) {
              encolar -= 1
              await new Promise((ok) => setTimeout(ok, 1200))
              await req.continue()
              return true
            }
          }
          if (req.method() === 'PATCH' && fallarPatch > 0) {
            fallarPatch -= 1
            await req.respond({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'falla simulada' }) })
            return true
          }
          return false
        })
        await abrirEditorD(page, ctx, t.item.titulo)
        const vuelven = []
        page.on('response', (x) => { if (x.request().method() === 'POST' && /\/marcas$/.test(new URL(x.url()).pathname)) vuelven.push(x.status()) })
        const renombrar = async (nombre) => {
          await abrirFila(page, pre.id)
          const sel = `.edd-fila[data-id="${pre.id}"] .edd-in-n`
          await page.click(sel, { clickCount: 3 })
          await page.type(sel, nombre)
          await page.keyboard.press('Enter')
          await page.focus('.edd-onda')
        }

        // (1) La creación llega al server enseguida pero su respuesta (la lista SIN el renombre)
        // vuelve después de la del renombre: no tiene que pisarla.
        retener = 1
        await page.focus('.edd-onda')
        await page.keyboard.press('m')
        const patch = page.waitForResponse((x) => x.request().method() === 'PATCH', { timeout: ESPERA_MS })
        await renombrar('Break')
        igual((await patch).status(), 200, 'el renombre no salió')
        await hasta(() => vuelven.length, (n) => n === 1, 'no volvió la creación retenida')
        await new Promise((r) => setTimeout(r, 400))       // gracia: la página procesa la tardía
        const api_ = await marcasApi(ctx, t.rid)
        igual(api_.map((m) => [m.tipo, m.nombre]).sort(), [['memory', 'Break'], ['memory', null]].sort(), 'la base tiene que tener la memory nueva y la renombrada')
        await filasComoApi(page, ctx, t.rid, 'la respuesta tardía (sin el renombre) pisó la lista nueva')

        // (2) Una relectura que llega al server ANTES que una creación encolada: la creación sale
        // 1,2 s tarde; mientras, un renombre falla (500) y dispara la relectura, que trae la lista
        // sin la memory nueva. Cuando la creación vuelve (su lista es más vieja que la relectura y
        // no se aplica), se relee de nuevo: la pantalla termina con la memory.
        encolar = 1
        fallarPatch = 1
        vuelven.length = 0
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('ArrowRight')            // dos beats: con snap uno vuelve a la misma línea
        await page.keyboard.press('m')                       // encolada 1,2 s
        const p500 = page.waitForResponse((x) => x.request().method() === 'PATCH', { timeout: ESPERA_MS })
        await renombrar('Otro')                            // 500 → relectura (sin la encolada)
        igual((await p500).status(), 500, 'el renombre tenía que recibir el 500 simulado')
        await hasta(() => vuelven.length, (n) => n === 1, 'no volvió la creación encolada')
        await new Promise((r) => setTimeout(r, 400))
        igual((await marcasApi(ctx, t.rid)).length, 3, 'la base tiene que tener la memory encolada')
        await filasComoApi(page, ctx, t.rid, 'la relectura vieja dejó la lista sin la memory encolada (no se volvió a leer)')
      })
    }],

    ['editor D (f54): sin duplicados desde la pantalla (C, M, L, I/O en el mismo lugar), y dos marcas iguales hechas en otra pestaña son dos al deshacer', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        const avisoEd = () => page.$eval('.edd-aviso', (e) => e.textContent)
        const sel = () => page.evaluate(() => [...document.querySelectorAll('.edd-fila.is-sel')].map((r) => Number(r.dataset.id)))
        const escrituras = pedidosDeMarcas(page)
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.focus('.edd-onda')
        await page.keyboard.press('ArrowRight')

        // C dos veces en el mismo lugar: un solo hot cue; el segundo avisa y selecciona el primero.
        const a = (await conTecla(page, ['c'], 'POST')).data.marca
        let desde = escrituras.length
        await page.keyboard.press('c')
        await hasta(avisoEd, (x) => x.startsWith(`⚠ Ya hay un hot cue (pad A) en ${tiempoTabla(a.inicio)}`), 'la segunda C no avisó que ya hay un cue')
        await hasta(sel, (v) => json(v) === json([a.id]), 'la segunda C no seleccionó el cue que ya estaba')
        igual(escrituras.slice(desde), [], 'la segunda C no puede crear otro cue en el mismo tiempo')
        igual(await page.$$eval('.edd-pad.is-lleno', (ps) => ps.length), 1, 'tiene que haber un solo pad lleno')
        // Lo mismo con el pad de otra letra en el mismo tiempo (tecla 2), con M, con L y con I/O.
        await page.keyboard.press('2')
        await hasta(avisoEd, (x) => x.startsWith('⚠ Ya hay un hot cue'), 'la tecla 2 en el mismo tiempo no avisó')
        const mem = (await conTecla(page, ['m'], 'POST')).data.marca
        desde = escrituras.length
        await page.keyboard.press('m')
        await hasta(avisoEd, (x) => x.startsWith(`⚠ Ya hay una memory en ${tiempoTabla(mem.inicio)}`), 'la segunda M no avisó')
        const lp = (await conTecla(page, ['l'], 'POST')).data.marca
        desde = escrituras.length
        // f63: el loop igual que ya está se ACTIVA. Primero R lo saca del loop (el recién creado
        // queda activo), así la segunda L tiene algo que activar.
        const reloop = () => page.$eval('.edd-reloop', (b) => b.getAttribute('aria-pressed'))
        await page.keyboard.press('r')
        igual(await reloop(), 'false', 'R no sacó del loop recién creado')
        await page.keyboard.press('l')
        await hasta(avisoEd, (x) => x.startsWith('⚠ Ya hay un loop igual') && x.includes('activé ese'), 'la segunda L no avisó (ni dijo que activó el que ya está)')
        igual(await reloop(), 'true', 'la segunda L no activó el loop igual que ya estaba')
        await page.keyboard.press('r')
        igual(await reloop(), 'false', 'R no sacó del loop (antes del I/O)')
        await page.keyboard.press('i')
        for (let i = 0; i < 4; i++) await page.keyboard.press('ArrowRight')
        await page.keyboard.press('o')                     // la misma entrada y salida que el de L
        await hasta(avisoEd, (x) => x.startsWith('⚠ Ya hay un loop igual') && x.includes('activé ese'), 'un I/O igual al loop no avisó (ni dijo que activó el que ya está)')
        igual(await reloop(), 'true', 'un I/O igual al loop no activó el que ya estaba')
        await hasta(sel, (v) => json(v) === json([lp.id]), 'el I/O repetido no seleccionó el loop que ya estaba')
        igual(escrituras.slice(desde), [], 'M, L e I/O repetidos no pueden crear marcas')
        igual((await filasComoApi(page, ctx, t.rid, 'sin duplicados')).length, 3, 'tienen que quedar 3 marcas: el cue, la memory y el loop')

        // Dos memory iguales hechas en OTRA pestaña (el server las permite): borrar una y Ctrl+Z
        // vuelve a dejar dos (antes, «Deshecho.» sin mandar nada y quedaba una).
        const otra = (await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'memory', inicio: mem.inicio })).data.marca
        await page.keyboard.press('ArrowRight')
        await conTecla(page, ['c'], 'POST')                 // cualquier escritura trae la lista nueva
        await filasComoApi(page, ctx, t.rid, 'con la memory de la otra pestaña')
        const del = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await page.click(`.edd-fila[data-id="${otra.id}"] .edd-borrar`)
        igual((await del).status(), 200, 'no se borró la memory de la otra pestaña')
        await page.focus('.edd-onda')
        const z = await conTecla(page, ['Control', 'z'], 'POST')
        igual([z.status, z.data.marca.tipo, z.data.marca.inicio], [201, 'memory', mem.inicio], 'Ctrl+Z tenía que volver a crear la memory borrada (no es «ya hecha» porque haya otra igual)')
        const api_ = await filasComoApi(page, ctx, t.rid, 'después de deshacer el borrado')
        igual(api_.filter((m) => m.tipo === 'memory').length, 2, 'después de deshacer tienen que volver a ser dos memory')
      })
    }],

    ['editor D (f54): lo que se marca mientras un deshacer viaja (fallido o lento) no se pierde del historial', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        let modo = null            // 'falla': el DELETE vuelve 500 a los 1,5 s; 'lento': sale a los 1,5 s
        await interceptar(page, async (req) => {
          if (!modo || req.method() !== 'DELETE' || !/\/marcas\/\d+$/.test(new URL(req.url()).pathname)) return false
          const m = modo
          modo = null
          await new Promise((r) => setTimeout(r, 1500))
          if (m === 'falla') await req.respond({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'falla simulada' }) })
          else await req.continue()
          return true
        })
        const avisoEd = () => page.$eval('.edd-aviso', (e) => e.textContent)
        const ctrlZ = async () => { await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control') }
        const borrado = async () => {
          const r = await page.waitForResponse((x) => x.request().method() === 'DELETE', { timeout: ESPERA_MS })
          return [r.status(), new URL(r.url()).pathname]
        }
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.focus('.edd-onda')

        // (a) Ctrl+Z del cue con el DELETE fallando a los 1,5 s; mientras tanto, M crea una memory.
        const cue = (await conTecla(page, ['c'], 'POST')).data.marca
        modo = 'falla'
        let r = borrado()
        await ctrlZ()
        await page.keyboard.press('ArrowRight')
        const mem = (await conTecla(page, ['m'], 'POST')).data.marca
        igual((await r)[0], 500, 'el DELETE del cue tenía que fallar')
        await hasta(avisoEd, (x) => x.startsWith('Deshacer no se pudo'), 'el deshacer fallido no avisó')
        // El historial tiene los dos pasos: Ctrl+Z deshace la memory (la última) y después el cue.
        r = borrado()
        await ctrlZ()
        igual(await r, [200, `${rutaMarcas(t.rid)}/${mem.id}`], 'Ctrl+Z tenía que deshacer la memory creada durante el deshacer fallido (se borró del historial)')
        r = borrado()
        await ctrlZ()
        igual(await r, [200, `${rutaMarcas(t.rid)}/${cue.id}`], 'el siguiente Ctrl+Z tenía que deshacer el cue')
        await filasComoApi(page, ctx, t.rid, 'después de (a)')

        // (b) Lo mismo con un deshacer que sale bien pero lento.
        await hasta(avisoEd, (x) => x === 'Deshecho.', 'el último deshacer no terminó')
        const cue2 = (await conTecla(page, ['c'], 'POST')).data.marca
        modo = 'lento'
        r = borrado()
        await ctrlZ()
        await page.keyboard.press('ArrowRight')
        const mem2 = (await conTecla(page, ['m'], 'POST')).data.marca
        igual(await r, [200, `${rutaMarcas(t.rid)}/${cue2.id}`], 'el deshacer lento del cue no salió')
        await hasta(avisoEd, (x) => x === 'Deshecho.', 'el deshacer lento no terminó')
        r = borrado()
        await ctrlZ()
        igual(await r, [200, `${rutaMarcas(t.rid)}/${mem2.id}`], 'después del deshacer lento, Ctrl+Z tenía que deshacer la memory (no «nada para deshacer»)')
        igual(await filasComoApi(page, ctx, t.rid, 'después de (b)'), [], 'tenía que quedar todo deshecho')
      })
    }],

    ['editor D (f54): un cambio de tipo que queda a medias en otro tema (AvPág + DELETE fallido) se avisa y no miente al volver', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const [t2, t3] = [por['Sin grilla'], por['Sin BPM']]
        let demorarPost = false
        let fallarDelete = false
        await interceptar(page, async (req) => {
          const u = new URL(req.url())
          if (!/\/marcas(\/\d+)?$/.test(u.pathname)) return false
          if (demorarPost && req.method() === 'POST') { demorarPost = false; await new Promise((r) => setTimeout(r, 1500)); await req.continue(); return true }
          if (fallarDelete && req.method() === 'DELETE') {
            fallarDelete = false
            await req.respond({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'falla simulada' }) })
            return true
          }
          return false
        })
        const titulo = () => page.$eval('#edd-titulo', (h) => h.firstChild.textContent)
        const avisoEd = () => page.$eval('.edd-aviso', (e) => e.textContent)
        await abrirEditorD(page, ctx, t2.item.titulo)
        await page.focus('.edd-onda')
        const mem = (await conTecla(page, ['m'], 'POST')).data.marca
        await filasComoApi(page, ctx, t2.rid, 'con la memory')
        demorarPost = true
        fallarDelete = true
        const borra = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await abrirFila(page, mem.id)
        await page.select(`.edd-fila[data-id="${mem.id}"] select.edd-tipo`, 'cue')
        await page.focus('.edd-onda')
        await page.keyboard.press('PageDown')
        await hasta(titulo, (v) => v === t3.item.titulo, 'AvPág no pasó al tema 3')
        igual((await borra).status(), 500, 'el borrado de la memory (en el tema 2) tenía que fallar')
        const aviso = await hasta(avisoEd, (x) => x.includes('quedó a medias'), 'quedó a medias en el tema 2 y no se avisó')
        afirmar(aviso.startsWith(`«${t2.item.titulo}»`), `el aviso tiene que nombrar el tema donde quedó a medias: ${json(aviso)}`)
        await filasComoApi(page, ctx, t3.rid, 'el tema 3 no se toca')
        // Al volver al tema 2: el aviso está ahí y la lista es la del server (las dos marcas).
        await page.keyboard.press('PageUp')
        await hasta(titulo, (v) => v === t2.item.titulo, 'RePág no volvió al tema 2')
        await esperarOnda(page, 'tema 2')
        const av = await hasta(() => page.$eval('.edd-falla', (e) => e.textContent).catch(() => ''), (x) => x.includes('quedó a medias'), 'al volver al tema 2 no está el aviso de lo que quedó a medias')
        afirmar(av.includes('Borrá la que sobra'), `el aviso no dice qué hacer: ${json(av)}`)
        const api_ = await filasComoApi(page, ctx, t2.rid, 'al volver al tema 2')
        igual(api_.map((m) => m.tipo).sort(), ['cue', 'memory'], 'en el tema 2 tienen que estar las dos (la memory vieja y el cue nuevo)')
        const est = await page.$eval('.edd-estado', (e) => e.textContent)
        afirmar(!est.startsWith('✓') && est.includes('1 cambio no se guardó'), `el estado del tema 2 no puede decir que está todo guardado: ${json(est)}`)
      })
    }],

    ['editor D (f54): cerrar con una escritura en vuelo que después falla: la playlist lo avisa', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        let fallar = false
        await interceptar(page, async (req) => {
          if (!fallar || req.method() !== 'POST' || !/\/marcas$/.test(new URL(req.url()).pathname)) return false
          fallar = false
          await new Promise((r) => setTimeout(r, 1500))
          await req.respond({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'falla simulada después de cerrar' }) })
          return true
        })
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.focus('.edd-onda')
        fallar = true
        const post = page.waitForResponse((r) => r.request().method() === 'POST', { timeout: ESPERA_MS })
        await page.keyboard.press('m')
        await page.keyboard.press('Escape')
        await hasta(() => page.$('.edd'), (v) => !v, 'Esc tenía que cerrar (la escritura todavía no falló)')
        igual((await post).status(), 500, 'el POST tenía que fallar después de cerrar')
        const toast = await hasta(() => page.$$eval('.toast-danger', (ts) => ts.map((x) => x.textContent)), (ts) => ts.length > 0, 'la falla de después de cerrar no se avisó en la playlist')
        afirmar(toast[0].includes('Un cambio de cues no se guardó') && toast[0].includes('Memory cue en') && toast[0].includes('falla simulada después de cerrar'),
          `el aviso no dice qué no se guardó ni por qué: ${json(toast)}`)
        igual(await marcasApi(ctx, t.rid), [], 'la memory no tenía que quedar en la base')
      })
    }],
    ['editor D (f54): Loop in / Loop out de punta a punta — pendiente a la vista, errores a la vista, crear, ver, editar y deshacer', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Sin grilla']                   // como el tema del dueño: con BPM, sin grilla
        const avisoEd = () => page.$eval('.edd-aviso', (e) => e.textContent)
        const pendiente = () => page.$eval('.edd-loop-pend', (e) => e.textContent).catch(() => null)
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.focus('.edd-onda')
        const P = 60 / t.bpm
        // El cursor, como lo mueve → / ←: un período (60/BPM medido), redondeado a ms en cada paso.
        let x = 0
        const flecha = async (k, dir) => { for (let i = 0; i < k; i++) { await page.keyboard.press(dir > 0 ? 'ArrowRight' : 'ArrowLeft'); x = Math.round((x + dir * P) * 1000) / 1000 } }
        // O antes de I: no crea nada y lo dice A LA VISTA (junto al transporte).
        const escrituras = pedidosDeMarcas(page)
        await page.keyboard.press('o')
        await hasta(avisoEd, (x) => x.includes('Primero marcá la entrada del loop (I)'), 'O sin I no avisó')
        afirmar(await page.$eval('.edd-aviso', (e) => window.__visible(e) && e.getBoundingClientRect().top < window.innerHeight), 'el aviso de O sin I no se ve en pantalla')
        igual(escrituras, [], 'O sin I no puede crear nada')
        // I: queda «in marcado, falta el out» en pantalla, con la línea de entrada en la onda.
        await flecha(2, 1)
        await page.keyboard.press('i')
        const entrada = x
        const pend = await hasta(pendiente, (x) => x !== null, 'después de I no se ve el loop pendiente')
        afirmar(pend.includes('falta la salida') && pend.includes(tiempoTabla(entrada)), `el pendiente no dice la entrada y que falta la salida: ${json(pend)}`)
        afirmar(await page.$('.edd-onda .edd-loopin'), 'no se dibuja la entrada del loop en la onda')
        // Una salida ANTES de la entrada: error a la vista, el pendiente sigue.
        await flecha(1, -1)
        await page.keyboard.press('o')
        await hasta(avisoEd, (x) => x.includes('después de la entrada'), 'una salida antes de la entrada no avisó')
        igual(escrituras, [], 'una salida antes de la entrada no puede crear nada')
        afirmar(await pendiente(), 'el error no puede tirar el loop pendiente')
        // Salida 4 beats después de la entrada: se crea el loop, se dibuja con su rango.
        await flecha(5, 1)
        const lp = await conTecla(page, ['o'], 'POST')
        const salida = x
        igual([lp.status, lp.data.marca.tipo, lp.data.marca.inicio, lp.data.marca.fin], [201, 'loop', entrada, salida], 'I / O no creó el loop con su entrada y su salida')
        await hasta(pendiente, (x) => x === null, 'después de O el pendiente tenía que irse')
        const caja = await page.$eval(`.edd-mk-loop[data-id="${lp.data.marca.id}"]`, (e) => [parseFloat(e.style.left), parseFloat(e.style.width)])
        const o = await page.$eval('.edd-onda', (e) => [Number(e.dataset.desde), Number(e.dataset.hasta)])
        const pct = (x) => ((x - o[0]) / (o[1] - o[0])) * 100
        afirmar(Math.abs(caja[0] - pct(entrada)) < 0.05 && Math.abs(caja[1] - (pct(salida) - pct(entrada))) < 0.05, `el loop no se dibuja en su rango: ${json(caja)}`)
        // Editarlo desde la lista (la salida) y deshacerlo.
        await abrirFila(page, lp.data.marca.id)
        const inputs = await page.$$(`.edd-fila[data-id="${lp.data.marca.id}"] .edd-in-t`)
        igual(inputs.length, 2, 'la fila del loop tiene que tener entrada y salida')
        await inputs[1].click({ clickCount: 3 })
        await inputs[1].type(tiempoTabla(salida + P))
        const ed = await conTecla(page, ['Enter'], 'PATCH')
        igual([ed.status, ed.data.marca.fin], [200, Math.round((salida + P) * 1000) / 1000], 'no se pudo editar la salida del loop')
        await page.focus('.edd-onda')
        const z = await conTecla(page, ['Control', 'z'], 'PATCH')
        igual(z.data.marca.fin, salida, 'Ctrl+Z no devolvió la salida del loop')
        const z2 = await conTecla(page, ['Control', 'z'], 'DELETE')
        igual(z2.status, 200, 'Ctrl+Z no deshizo el loop')
        igual(await marcasApi(ctx, t.rid), [], 'el loop se tenía que deshacer')
        // Un loop in pendiente y cambio de tema: no queda pendiente en el tema nuevo.
        await page.keyboard.press('i')
        await hasta(pendiente, (x) => x !== null, 'I no dejó el pendiente')
        await page.keyboard.press('PageDown')
        await hasta(() => page.$eval('#edd-titulo', (h) => h.firstChild.textContent), (v) => v === por['Sin BPM'].item.titulo, 'AvPág no pasó de tema')
        igual(await pendiente(), null, 'el loop pendiente pasó al tema nuevo')
      })
    }],
    ['editor D (f54): la onda se lee — banderas sin pisarse, kicks visibles dentro de un loop (captura), líneas de tiempo y de kick, marca sin nombre', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        const P = 60 / t.bpm
        // El caso de la captura del dueño: un hot cue con nombre y un loop de 4 beats en el MISMO
        // punto (un kick), y un hot cue sin nombre más adelante.
        const T = Math.round((t.primer_kick_s + 2 * P) * 1000) / 1000
        const cue = (await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'cue', inicio: T, num: 0, nombre: 'Primer cue' })).data.marca
        const lp = (await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'loop', inicio: T, fin: Math.round((T + 4 * P) * 1000) / 1000 })).data.marca
        await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'cue', inicio: Math.round((T + 6 * P) * 1000) / 1000, num: 1 })
        await abrirEditorD(page, ctx, t.item.titulo)
        await clicZoom(page, '2')
        await hasta(() => page.$$eval('.edd-flag', (f) => f.length), (n) => n >= 2, 'no aparecieron las banderas')

        // Banderas: ninguna se pisa con otra (rectángulos en pantalla) y están las dos del mismo punto.
        const flags = await page.$$eval('.edd-flag', (fs) => fs.map((f) => { const r = f.getBoundingClientRect(); return { id: Number(f.dataset.id), txt: f.textContent, l: r.left, r: r.right, t: r.top, b: r.bottom } }))
        for (let i = 0; i < flags.length; i++) {
          for (let j = i + 1; j < flags.length; j++) {
            const [p, q] = [flags[i], flags[j]]
            const pisa = p.l < q.r - 0.5 && q.l < p.r - 0.5 && p.t < q.b - 0.5 && q.t < p.b - 0.5
            afirmar(!pisa, `las banderas «${p.txt}» y «${q.txt}» se pisan: ${json([p, q])}`)
          }
        }
        const deCue = flags.find((f) => f.id === cue.id)
        const deLoop = flags.find((f) => f.id === lp.id)
        afirmar(deCue && deCue.txt === 'A · Primer cue' && deLoop && deLoop.txt === '⟲ 4 beats', `el hot cue (con su nombre) y el loop del mismo punto tienen que verse los dos: ${json(flags)}`)
        afirmar(deCue.t < deLoop.t, 'el hot cue tiene prioridad (fila de arriba) sobre el loop')

        // Dentro del loop los kicks siguen distinguibles: en una captura de pantalla, la columna
        // más «azul» (graves vistos a través del relleno) en los 40 ms de cada kick dentro del loop,
        // contra la de los 40 ms del contratiempo.
        const caja = await page.$eval('.edd-onda', (e) => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, width: r.width, height: r.height } })
        const b64 = await page.screenshot({ clip: caja, encoding: 'base64' })
        const kicksLoop = [0, 1, 2, 3].map((j) => T + j * P)
        const azules = await page.evaluate(async (png, ks, p) => {
          const o = document.querySelector('.edd-onda')
          const [desde, hasta] = [Number(o.dataset.desde), Number(o.dataset.hasta)]
          const img = new Image()
          img.src = `data:image/png;base64,${png}`
          await img.decode()
          const cv = document.createElement('canvas')
          cv.width = img.naturalWidth
          cv.height = img.naturalHeight
          const c = cv.getContext('2d')
          c.drawImage(img, 0, 0)
          const col = (t) => {
            const x = Math.round(((t - desde) / (hasta - desde)) * cv.width)
            const d = c.getImageData(x, 0, 1, cv.height).data
            let n = 0
            for (let i = 0; i < d.length; i += 4) if (d[i + 2] > d[i] + 40 && d[i + 2] > d[i + 1] + 30) n++
            return n / cv.height
          }
          const max = (t0) => Math.max(...[0, 0.01, 0.02, 0.03, 0.04].map((dd) => col(t0 + dd)))
          return ks.map((k) => [max(k), max(k + p / 2)])
        }, b64, kicksLoop, P)
        for (const [k, [kick, contra]] of azules.entries()) {
          afirmar(kick > 0.25 && kick > 3 * Math.max(contra, 0.01), `dentro del loop el kick ${k + 1} no se distingue (relleno demasiado opaco): ${json(azules)}`)
        }

        // Con grilla, la grilla manda: no se dibujan golpes de graves detectados (no son beats).
        await hasta(() => page.$eval('.edd-onda', (e) => e.dataset.tramo), (v) => v === 'detalle', 'no cargó el tramo con resolución')
        igual(await page.$$eval('.edd-golpe', (k) => k.length), 0, 'con grilla no van los golpes de graves detectados')

        // Loops idénticos encimados como en la base del dueño (32 en total, el máximo por tema): UN
        // relleno (detrás de la onda), un borde y una bandera con «×31», y los kicks se ven adentro.
        const fin2 = Math.round((T + 8 * P) * 1000) / 1000
        const estados32 = []
        for (let i = 0; i < 31; i++) estados32.push((await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'loop', inicio: T, fin: fin2 })).status)
        igual(estados32.filter((s) => s !== 201), [], 'no se pudieron crear los 31 loops iguales por la API')
        await abrirEditorD(page, ctx, t.item.titulo)
        await clicZoom(page, '2')
        const grupos = await hasta(() => page.$$eval('.edd-mk-loop', (ls) => ls.map((l) => Number(l.dataset.n))), (v) => v.includes(31), 'los 31 loops iguales no se juntaron en uno')
        igual(grupos.sort((a, b) => a - b), [1, 31], 'un borde por grupo: el loop de 4 beats y los 31 iguales de 8')
        const b32 = await page.$$eval('.edd-flag', (fs) => fs.map((f) => f.textContent).filter((x) => x.includes('×31')))
        igual(b32.length, 1, 'tiene que haber una bandera con «×31»')
        const caja2 = await page.$eval('.edd-onda', (e) => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, width: r.width, height: r.height } })
        const png2 = await page.screenshot({ clip: caja2, encoding: 'base64' })
        const azules2 = await page.evaluate(async (png, ks, p) => {
          const o = document.querySelector('.edd-onda')
          const [desde, hasta] = [Number(o.dataset.desde), Number(o.dataset.hasta)]
          const img = new Image()
          img.src = `data:image/png;base64,${png}`
          await img.decode()
          const cv = document.createElement('canvas')
          cv.width = img.naturalWidth
          cv.height = img.naturalHeight
          const c = cv.getContext('2d')
          c.drawImage(img, 0, 0)
          const col = (t) => {
            const x = Math.round(((t - desde) / (hasta - desde)) * cv.width)
            const d = c.getImageData(x, 0, 1, cv.height).data
            let n = 0
            for (let i = 0; i < d.length; i += 4) if (d[i + 2] > d[i] + 40 && d[i + 2] > d[i + 1] + 30) n++
            return n / cv.height
          }
          const max = (t0) => Math.max(...[0, 0.01, 0.02, 0.03, 0.04].map((dd) => col(t0 + dd)))
          return ks.map((k) => [max(k), max(k + p / 2)])
        }, png2, [1, 2, 3, 4].map((j) => T + j * P), P)          // dentro del loop y de la vista (2 compases)
        // Y el relleno es un tinte translúcido detrás de la onda: entre dos kicks, cerca del borde de
        // arriba (debajo de la franja de 6 px), el fondo es naranja oscuro, no naranja lleno.
        const fondoLoop = await page.evaluate(async (png, t0) => {
          const o = document.querySelector('.edd-onda')
          const [desde, hasta] = [Number(o.dataset.desde), Number(o.dataset.hasta)]
          const img = new Image()
          img.src = `data:image/png;base64,${png}`
          await img.decode()
          const cv = document.createElement('canvas')
          cv.width = img.naturalWidth
          cv.height = img.naturalHeight
          const c = cv.getContext('2d')
          c.drawImage(img, 0, 0)
          const x = Math.round(((t0 - desde) / (hasta - desde)) * cv.width)
          return [...c.getImageData(x, Math.round(cv.height * 0.12), 1, 1).data].slice(0, 3)
        }, png2, T + 1.5 * P + 0.06)
        afirmar(fondoLoop[0] - fondoLoop[2] > 10 && fondoLoop[0] < 110, `el fondo del loop tiene que ser un tinte naranja translúcido: ${json(fondoLoop)}`)
        for (const [k, [kick, contra]] of azules2.entries()) {
          afirmar(kick > 0.25 && kick > 3 * Math.max(contra, 0.01), `con 31 loops encimados el kick ${k + 2} no se distingue (el relleno se acumula): ${json(azules2)}`)
        }
        // La marca sin nombre se ve como tal (no como si «Hot cue» fuera su nombre).
        igual(await page.$eval('.edd-pad[data-pad="1"] .edd-pad-nom', (e) => e.textContent), 'Sin nombre', 'el pad de un hot cue sin nombre')

        // Sin grilla: líneas de tiempo en cada marca de la regla, y kicks igual.
        await page.focus('.edd-onda')
        await page.keyboard.press('PageDown')
        await hasta(() => page.$eval('#edd-titulo', (h) => h.firstChild.textContent), (v) => v === por['Sin grilla'].item.titulo, 'AvPág no pasó al tema sin grilla')
        await esperarOnda(page, 'tema sin grilla')
        const regla = await page.$$eval('.edd-regla > span', (s) => s.map((x) => parseFloat(x.style.left)))
        const ticks = await page.$$eval('.edd-tick', (s) => s.map((x) => parseFloat(x.style.left)))
        igual(ticks, regla, 'sin grilla tiene que haber una línea de tiempo en cada marca de la regla')
        const sg = por['Sin grilla']
        const o2 = await page.$eval('.edd-onda', (e) => [Number(e.dataset.desde), Number(e.dataset.hasta)])
        const gen2 = []
        for (let j = 0; sg.primer_kick_s + j * (P / 2) <= o2[1]; j++) if (sg.primer_kick_s + j * (P / 2) >= o2[0]) gen2.push(sg.primer_kick_s + j * (P / 2))
        const k2 = await hasta(() => page.$$eval('.edd-golpe', (ks) => ks.map((k) => Number(k.dataset.t))), (v) => v.length > 0, 'sin grilla no se dibujaron los golpes de graves')
        igual(k2.length, gen2.length, 'sin grilla, una línea por kick generado (en cada corchea)')
        k2.forEach((x, k) => afirmar(Math.abs(x - gen2[k]) <= 0.015, `sin grilla, la línea de kick ${k + 1} está en ${x} y el kick en ${gen2[k]}`))
        // Un pad sin golpes (tema sin BPM): ningún kick (no se inventa).
        await page.keyboard.press('PageDown')
        await hasta(() => page.$eval('#edd-titulo', (h) => h.firstChild.textContent), (v) => v === por['Sin BPM'].item.titulo, 'AvPág no pasó al tema sin BPM')
        await esperarOnda(page, 'tema sin BPM')
        await hasta(() => page.$eval('.edd-onda', (e) => e.dataset.tramo), (v) => v === 'detalle', 'el tema sin BPM no cargó el tramo con resolución')
        igual(await page.$$eval('.edd-golpe', (k) => k.length), 0, 'en un pad sin golpes no puede haber golpes de graves')
      })
    }],
    ['editor D (f54): a 1440×900 se marca sin scroll — transporte, marcar, deshacer y el aviso a la vista en los tres temas', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const medir = () => page.evaluate(() => {
          const r = (sel) => { const e = document.querySelector(sel); if (!e) return null; const b = e.getBoundingClientRect(); return { top: Math.round(b.top), bottom: Math.round(b.bottom), visible: window.__visible(e) } }
          return {
            scroll: document.querySelector('.edd-fondo').scrollTop,
            onda: r('.edd-onda'),
            marcar: [...document.querySelectorAll('.edd-marcar button')].map((b) => { const x = b.getBoundingClientRect(); return [b.textContent.replace(/\s+/g, ' ').trim(), Math.round(x.bottom)] }),
            aviso: r('.edd-avisos'),
            pads: r('.edd-pads'),
          }
        })
        // Una marca sin nombre en el primer tema, para medir el contraste del «sin nombre».
        const sinNom = (await apiPedir(ctx, rutaMarcas(por['Con grilla'].rid), 'POST', { tipo: 'memory', inicio: 30 })).data.marca
        await abrirEditorD(page, ctx, por['Con grilla'].item.titulo)
        await abrirFila(page, sinNom.id)
        const ph = await page.$eval(`.edd-fila[data-id="${sinNom.id}"] .edd-in-n`, (i) => {
          const rgb = (s) => (s.match(/[\d.]+/g) || []).map(Number)
          const lum = ([r, g, b]) => { const f = (x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4 }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b) }
          const [a, b] = [lum(rgb(getComputedStyle(i, '::placeholder').color)), lum(rgb(getComputedStyle(i).backgroundColor))].sort((x, y) => y - x)
          return Math.round(((a + 0.05) / (b + 0.05)) * 100) / 100
        })
        afirmar(ph >= 4.5, `el placeholder «sin nombre» tiene contraste ${ph}:1 (mínimo 4,5)`)
        await page.click(`.edd-fila[data-id="${sinNom.id}"] .edd-editar`)
        for (const [k, t] of [por['Con grilla'], por['Sin grilla'], por['Sin BPM']].entries()) {
          if (k > 0) {
            await page.focus('.edd-onda')
            await page.keyboard.press('PageDown')
            await hasta(() => page.$eval('#edd-titulo', (h) => h.firstChild.textContent), (v) => v === t.item.titulo, `AvPág no pasó a «${t.item.titulo}»`)
            await esperarOnda(page, t.item.titulo)
          }
          // Una acción, para que el aviso tenga texto.
          await page.focus('.edd-onda')
          await page.keyboard.press('o')                  // sin entrada: avisa
          await hasta(() => page.$eval('.edd-aviso', (e) => e.textContent), (x) => x.includes('Primero marcá la entrada'), 'O sin entrada no avisó')
          const m = await medir()
          igual(m.scroll, 0, `«${t.item.titulo}»: la pantalla se desplazó`)
          for (const [txt, bottom] of m.marcar) afirmar(bottom <= 900, `«${t.item.titulo}»: «${txt}» queda fuera de la pantalla (y = ${bottom})`)
          afirmar(m.marcar.some(([txt]) => txt.startsWith('Hot cue')) && m.marcar.some(([txt]) => txt.startsWith('Loop out')) && m.marcar.some(([txt]) => txt.includes('Deshacer')),
            `faltan acciones en la fila de marcar: ${json(m.marcar)}`)
          afirmar(m.aviso.visible && m.aviso.bottom <= 900, `«${t.item.titulo}»: el aviso no se ve: ${json(m.aviso)}`)
          afirmar(m.onda.top < m.marcar[0][1], 'la onda tiene que estar arriba de las acciones')
          if (t === por['Sin BPM']) {
            // Sin marcas: los 8 pads vacíos, con la letra legible (≥ 4,5:1 contra su fondo).
            // Con la opacidad incluida (antes la letra iba al 55 % sobre el color del pad).
            const pads = await page.$$eval('.edd-pad .edd-pad-n', (ns) => ns.map((e, i) => {
              const rgb = (s) => (s.match(/[\d.]+/g) || []).map(Number)
              const lum = ([r, g, b]) => { const f = (x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4 }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b) }
              let op = 1
              for (let a = e; a && a !== document.body; a = a.parentElement) op *= Number(getComputedStyle(a).opacity)
              const fondo = rgb(getComputedStyle(e.closest('.edd-pad')).backgroundColor)
              const letra = rgb(getComputedStyle(e).color).slice(0, 3).map((v, k) => op * v + (1 - op) * fondo[k])
              const [x, y] = [lum(letra), lum(fondo)].sort((p, q) => q - p)
              return { q: `pad ${i + 1}`, ratio: Math.round(((x + 0.05) / (y + 0.05)) * 100) / 100 }
            }))
            for (const x of pads) afirmar(x.ratio >= 4.5, `letra de pad vacío con contraste ${x.ratio}:1 (mínimo 4,5): ${x.q}`)
          }
        }
      })
    }],

    ['editor D (f54): «Borrar repetidos» — dice cuántas, pide confirmación, no borra sin el «Sí», deja una de cada y se deshace', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Sin grilla']
        // Lo que tiene hoy la base del dueño, en chico: 6 loops iguales y 4 hot cues en el mismo tiempo.
        const P = 60 / t.bpm
        for (let i = 0; i < 6; i++) await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'loop', inicio: 2, fin: Math.round((2 + 4 * P) * 1000) / 1000 })
        for (let n = 0; n < 4; n++) await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'cue', inicio: 6.5, num: n })
        const antes = await marcasApi(ctx, t.rid)
        await abrirEditorD(page, ctx, t.item.titulo)
        const txt = await hasta(() => page.$eval('.edd-repetidos', (e) => e.textContent).catch(() => null), (x) => x !== null, 'no apareció el aviso de repetidos')
        afirmar(txt.includes('5 loops repetidos en 0:02.000') && txt.includes('3 hot cues repetidos en 0:06.500'), `el aviso no dice cuántas y dónde: ${json(txt)}`)
        const escrituras = pedidosDeMarcas(page)
        await page.click('.edd-borrar-rep')
        const conf = await page.$eval('.edd-rep-conf', (e) => e.textContent)
        afirmar(conf.includes('Voy a borrar 8 marcas'), `la confirmación no dice cuántas va a borrar: ${json(conf)}`)
        igual(escrituras, [], 'abrir la confirmación no puede borrar nada')
        await page.click('.edd-rep-no')
        await hasta(() => page.$('.edd-rep-conf'), (x) => !x, '«No» no cerró la confirmación')
        igual(escrituras, [], '«No» no puede borrar nada')
        igual((await marcasApi(ctx, t.rid)).length, antes.length, 'sin el «Sí» no se borró nada')
        // Sí: quedan una de cada (la más vieja), y el aviso se va.
        await page.click('.edd-borrar-rep')
        await page.click('.edd-rep-si')
        await hasta(() => escrituras.length, (n) => n === 8, 'no se mandaron los 8 borrados')
        const despues = await filasComoApi(page, ctx, t.rid, 'después de borrar repetidos')
        const ids = (tipo) => antes.filter((m) => m.tipo === tipo).map((m) => m.id).sort((a, b) => a - b)
        igual(despues.map((m) => m.id).sort((a, b) => a - b), [ids('loop')[0], ids('cue')[0]].sort((a, b) => a - b), 'tiene que quedar la más vieja de cada grupo')
        await hasta(() => page.$('.edd-repetidos'), (x) => !x, 'el aviso de repetidos no se fue')
        // Ctrl+Z: un solo paso las vuelve a poner todas.
        await page.focus('.edd-onda')
        await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control')
        await hasta(() => marcasApi(ctx, t.rid), (m) => m.length === antes.length, 'Ctrl+Z no volvió a poner las 8 repetidas')
        await filasComoApi(page, ctx, t.rid, 'después de deshacer')
      })
    }],

    ['editor D (f54): «Marcar un kick» (K) ancla la grilla sin grilla del motor, y «Solo graves» (B) deja solo los graves', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Sin grilla']                     // kicks en cada corchea: el motor no da grilla
        const g = (await onda3Api(ctx, t.rid)).grilla
        afirmar(g && g.primer_beat_s === null && g.periodo_s > 0, `el tema sin grilla tiene que traer período y no grilla: ${json(g)}`)
        await abrirEditorD(page, ctx, t.item.titulo)
        igual(await page.$eval('.edd-grilla-chip', (e) => e.textContent), 'SIN GRILLA · marcá un kick (K)', 'sin grilla el chip tiene que pedir que se marque un kick')
        // El cursor sobre un kick generado (0,1 + 2 × medio beat), con → (60/BPM) y Mayús+→ (10 ms).
        const P = 60 / t.bpm
        let x = 0
        await page.focus('.edd-onda')
        await page.keyboard.press('ArrowRight'); x = Math.round((x + P) * 1000) / 1000
        for (let i = 0; i < 10; i++) { await page.keyboard.down('Shift'); await page.keyboard.press('ArrowRight'); await page.keyboard.up('Shift'); x = Math.round((x + 0.01) * 1000) / 1000 }
        await page.keyboard.press('k')
        await hasta(() => page.$eval('.edd-grilla-chip', (e) => e.textContent), (v) => v === 'GRILLA ANCLADA POR VOS', 'K no ancló la grilla')
        let o = await leerOnda(page)
        igual(o.unidad, 'compases', 'con la grilla anclada el zoom va en compases')
        mismasLineas(o.beats, lineasEsperadas({ primer_beat_s: ((x % g.periodo_s) + g.periodo_s) % g.periodo_s, periodo_s: g.periodo_s }, 0, o.desde, o.hasta), o, 'grilla anclada')
        afirmar(o.beats.some((b) => Math.abs(b.t - x) < 1e-5 && b.compas), `la línea del ancla tiene que ser el 1 del compás: ${json(o.beats.slice(0, 6))}`)
        // Queda en el navegador: al volver a abrir sigue anclada; «Sacar el ancla» la quita.
        await abrirEditorD(page, ctx, t.item.titulo)
        igual(await page.$eval('.edd-grilla-chip', (e) => e.textContent), 'GRILLA ANCLADA POR VOS', 'el ancla no quedó guardada en el navegador')
        await page.click('.edd-grilla-ctrl > summary')
        await page.click('.edd-sacar-ancla')
        await hasta(() => page.$eval('.edd-grilla-chip', (e) => e.textContent), (v) => v.startsWith('SIN GRILLA'), '«Sacar el ancla» no la quitó')
        igual(await page.$$eval('.edd-beat', (b) => b.length), 0, 'sin ancla no queda ninguna línea de beat')
        // Con grilla del motor, K no la reemplaza: lo dice.
        await abrirEditorD(page, ctx, por['Con grilla'].item.titulo)
        await page.focus('.edd-onda')
        await page.keyboard.press('k')
        await hasta(() => page.$eval('.edd-aviso', (e) => e.textContent), (v) => v.startsWith('El motor ya estimó una grilla'), 'con grilla, K tenía que decir que se ajusta con «Ajustar grilla»')

        // B: solo graves. En una captura de la onda, antes hay píxeles de medios o agudos; después, ninguno.
        const colores = async () => {
          const caja = await page.$eval('.edd-onda', (e) => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, width: r.width, height: r.height } })
          const b64 = await page.screenshot({ clip: caja, encoding: 'base64' })
          return page.evaluate(async (png) => {
            const css = getComputedStyle(document.documentElement)
            const rgb = (v) => { const h = v.trim().replace('#', ''); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) }
            const [gr, me, ag] = ['--edd-graves', '--edd-medios', '--edd-agudos'].map((n) => rgb(css.getPropertyValue(n)))
            const img = new Image()
            img.src = `data:image/png;base64,${png}`
            await img.decode()
            const cv = document.createElement('canvas')
            cv.width = img.naturalWidth
            cv.height = img.naturalHeight
            const c = cv.getContext('2d')
            c.drawImage(img, 0, 0)
            const d = c.getImageData(0, 0, cv.width, cv.height).data
            const cerca = (i, q) => Math.abs(d[i] - q[0]) <= 3 && Math.abs(d[i + 1] - q[1]) <= 3 && Math.abs(d[i + 2] - q[2]) <= 3
            let g = 0, m = 0, a = 0
            for (let i = 0; i < d.length; i += 4) { if (cerca(i, gr)) g++; else if (cerca(i, me)) m++; else if (cerca(i, ag)) a++ }
            return { graves: g, medios: m, agudos: a }
          }, b64)
        }
        await hasta(() => page.$eval('.edd-onda', (e) => e.dataset.tramo), (v) => v === 'detalle', 'no cargó el tramo con resolución')
        const antes = await colores()
        afirmar(antes.graves > 0 && antes.medios + antes.agudos > 0, `antes de B tiene que haber graves y medios o agudos: ${json(antes)}`)
        await page.keyboard.press('b')
        await hasta(() => page.$eval('.edd-solog', (e) => e.getAttribute('aria-pressed')), (v) => v === 'true', 'B no prendió «Solo graves»')
        const despues = await hasta(colores, (c) => c.medios + c.agudos === 0, 'con «Solo graves» siguen dibujados medios o agudos')
        afirmar(despues.graves > 0, `con «Solo graves» tienen que quedar los graves: ${json(despues)}`)
      })
    }],

    ['editor D (f54): las creaciones van de a una — C → C con el primer POST demorado: dos pads distintos, y C C en el mismo lugar: uno solo', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        let demorar = 0
        await interceptar(page, async (req) => {
          if (demorar <= 0 || req.method() !== 'POST' || !/\/marcas$/.test(new URL(req.url()).pathname)) return false
          demorar -= 1
          await new Promise((r) => setTimeout(r, 1000))
          await req.continue()
          return true
        })
        const vueltas = []
        page.on('response', (x) => { if (x.request().method() === 'POST' && /\/marcas$/.test(new URL(x.url()).pathname)) vueltas.push(x.status()) })
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.focus('.edd-onda')
        // C (POST demorado 1 s), →, C enseguida: el segundo espera y toma el pad B (antes los dos
        // pedían el A con una lista vieja y el segundo daba 400).
        // Dos beats: con «Pegar a la grilla» un solo → vuelve a la misma línea.
        demorar = 1
        await page.keyboard.press('c')
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('c')
        await hasta(() => vueltas.length, (n) => n === 2, 'no volvieron las dos creaciones')
        igual(vueltas, [201, 201], 'las dos creaciones tenían que salir bien (sin 400 por pad ocupado)')
        const api_ = await filasComoApi(page, ctx, t.rid, 'después de C → C')
        igual(api_.map((m) => m.num).sort(), [0, 1], 'tenían que quedar en los pads A y B')
        // C C en el mismo lugar con el primero demorado: el segundo, al llegar su turno, ve el primero.
        demorar = 1
        vueltas.length = 0
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('c')
        await page.keyboard.press('c')
        await hasta(() => page.$eval('.edd-aviso', (e) => e.textContent), (x) => x.startsWith('⚠ Ya hay un hot cue'), 'la segunda C (en cola) no avisó que ya hay uno')
        igual(vueltas, [201], 'C C en el mismo lugar tenía que crear UN cue')
        igual((await filasComoApi(page, ctx, t.rid, 'después de C C')).length, 3, 'tienen que quedar 3 hot cues')
        // C (POST demorado), M (espera su turno) y AvPág: el cue queda en este tema; la memory no
        // se crea en ninguno de los dos, y el tema nuevo lo dice (antes se perdía en silencio).
        demorar = 1
        vueltas.length = 0
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('c')
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('m')
        await page.keyboard.press('PageDown')
        await hasta(() => page.$eval('.edd-aviso', (e) => e.textContent), (x) => x.includes('esperaba su turno no se creó porque pasaste de tema'), 'la memory en cola que se cortó por AvPág no se avisó')
        await hasta(() => vueltas.length, (n) => n === 1, 'no volvió el POST del cue')
        await new Promise((r) => setTimeout(r, 300))
        igual(vueltas, [201], 'tenía que salir solo el POST del cue (la memory en cola no)')
        const finalA = await marcasApi(ctx, t.rid)
        igual([finalA.filter((m) => m.tipo === 'cue').length, finalA.filter((m) => m.tipo === 'memory').length], [4, 0], 'en el tema de origen: el cue nuevo sí, la memory no')
        igual(await marcasApi(ctx, por['Sin grilla'].rid), [], 'el tema nuevo no puede recibir nada')
      })
    }],

    ['editor D (f54): hot loop — «El loop va a un pad», el rótulo cuenta los beats con el período de la grilla; «sin pad», borrar y Ctrl+Z lo devuelven como estaba', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        // La grilla de /onda3 con otro período (0,5 s = 120 BPM) que el BPM de la base (128): el
        // loop se crea con el período de la GRILLA, y el rótulo del pad lo tiene que contar con
        // el mismo (con 60/128 un loop de 4 beats diría «4.3 beats»). Lo único que cambia.
        const P = 0.5
        await interceptar(page, async (req) => {
          if (!/\/onda3/.test(req.url())) return false
          const u = new URL(req.url())
          const d = await (await fetch(ctx.url + u.pathname + u.search)).json()
          if (d.grilla && d.grilla.primer_beat_s !== null) d.grilla.periodo_s = P
          await req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(d) })
          return true
        })
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.evaluate(() => [...document.querySelectorAll('label.edd-snap')].find((l) => l.textContent.includes('Guardar el loop en un pad')).querySelector('input').click())
        await page.focus('.edd-onda')
        // L con «en un pad»: un loop de 4 beats de la grilla en el primer pad libre (A).
        const lp = await conTecla(page, ['l'], 'POST')
        igual([lp.status, lp.data.marca.tipo, lp.data.marca.num], [201, 'loop', 0], 'L con «Guardar el loop en un pad» no creó el loop en el pad A')
        const m = lp.data.marca
        afirmar(Math.abs((m.fin - m.inicio) - 4 * P) < 0.0015, `el loop no dura 4 beats de la grilla (${4 * P} s): ${json(m)}`)
        await hasta(() => page.$eval('.edd-pad[data-pad="0"] .edd-pad-nom', (e) => e.textContent).catch(() => null), (v) => v === '⟲ 4 beats',
          'el pad A no dice «⟲ 4 beats» (los beats contados con el período de la grilla con que se creó)')
        // «sin pad» desde la lista: el loop sigue, sin pad.
        const pr = page.waitForResponse((r) => r.request().method() === 'PATCH', { timeout: ESPERA_MS })
        await abrirFila(page, m.id)
        await page.select(`.edd-fila[data-id="${m.id}"] select.edd-pad-sel`, '')
        igual((await pr).status(), 200, '«sin pad» no se guardó')
        let api_ = await filasComoApi(page, ctx, t.rid, 'después de «sin pad»')
        igual(api_.map((x) => [x.id, x.tipo, x.num]), [[m.id, 'loop', null]], 'el loop no quedó sin pad')
        // Borrarlo y Ctrl+Z: vuelve (otro id) como estaba al borrarlo, sin pad.
        const dr = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await page.click(`.edd-fila[data-id="${m.id}"] .edd-borrar`)
        igual((await dr).status(), 200, 'Borrar no borró el loop')
        await page.focus('.edd-onda')
        const z1 = await conTecla(page, ['Control', 'z'], 'POST')
        igual([z1.status, z1.data.marca.tipo, z1.data.marca.num, z1.data.marca.inicio, z1.data.marca.fin], [201, 'loop', null, m.inicio, m.fin], 'Ctrl+Z no volvió a poner el loop como estaba (sin pad)')
        // Ctrl+Z otra vez: vuelve a su pad, sobre la marca RECREADA (su id nuevo).
        const z2 = await conTecla(page, ['Control', 'z'], 'PATCH')
        igual([z2.status, z2.data.marca.id, z2.data.marca.num], [200, z1.data.marca.id, 0], 'el segundo Ctrl+Z no devolvió el loop a su pad A (o fue a otro id)')
        await filasComoApi(page, ctx, t.rid, 'después de devolverlo a su pad')
        // Mayús+1 borra el hot loop del pad A; Ctrl+Z lo recrea EN SU PAD.
        const del = await conTecla(page, ['Shift', 'Digit1'], 'DELETE')
        igual(del.status, 200, 'Mayús+1 no borró el hot loop del pad A')
        const z3 = await conTecla(page, ['Control', 'z'], 'POST')
        igual([z3.status, z3.data.marca.tipo, z3.data.marca.num, z3.data.marca.inicio, z3.data.marca.fin], [201, 'loop', 0, m.inicio, m.fin], 'Ctrl+Z no recreó el hot loop en su pad A')
        api_ = await filasComoApi(page, ctx, t.rid, 'después de recrear el hot loop')
        igual(api_.map((x) => [x.tipo, x.num]), [['loop', 0]], 'tendría que quedar solo el hot loop del pad A')
        await hasta(() => page.$eval('.edd-pad[data-pad="0"] .edd-pad-nom', (e) => e.textContent).catch(() => null), (v) => v === '⟲ 4 beats',
          'el pad A recreado no dice «⟲ 4 beats»')
      })
    }],

    ['editor D (f54): al pasar de tema, ni el historial ni el ajuste del anterior llegan al nuevo (Ctrl+Z y G en el render del cambio; una respuesta que llega tarde)', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const A = por['Con grilla']
        const B = por['Sin grilla']
        const clave = (rid) => `musiflix.editorD.grilla.${rid}`
        const pedidos = pedidosDeMarcas(page)
        const temaCargado = (que) => hasta(() => page.evaluate(() => ({
          grilla: document.querySelector('.edd-grilla-estado')?.textContent ?? '',
          estado: document.querySelector('.edd-estado')?.textContent ?? '',
        })), (v) => v.grilla && !v.grilla.includes('Leyendo') && v.estado.trim() !== '' && !v.estado.includes('Guardando'), `${que}: el tema no terminó de cargar`, 20000)
        const botonesHistorial = () => page.$$eval('.edd-marcar button[aria-keyshortcuts="Control+Z"], .edd-marcar button[aria-keyshortcuts="Control+Y"]', (bs) => bs.map((b) => b.disabled))

        // (1) En A: una memory y su borrado. Un Ctrl+Z con el historial de A la vuelve a CREAR.
        await abrirEditorD(page, ctx, A.item.titulo)
        await page.focus('.edd-onda')
        const mem = await conTecla(page, ['m'], 'POST')
        igual(mem.status, 201, 'M no creó la memory en A')
        const dr = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await page.click(`.edd-fila[data-id="${mem.data.marca.id}"] .edd-borrar`)
        igual((await dr).status(), 200, 'no se borró la memory de A')
        await filasComoApi(page, ctx, A.rid, 'A sin marcas')
        await page.evaluate((k) => localStorage.removeItem(k), clave(B.rid))
        pedidos.length = 0
        // AvPág y, apenas React pinta el tema nuevo (título de B) y ANTES del efecto que vacía
        // historial, ajuste y onda, Ctrl+Z y G: en el mismo turno de JS, sin esperar nada.
        const vista = await page.evaluate(async (tituloB) => {
          const onda = document.querySelector('.edd-onda')
          const tecla = (key, extra = {}) => onda.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...extra }))
          tecla('PageDown', { code: 'PageDown' })
          for (let i = 0; i < 10 && !(document.querySelector('#edd-titulo')?.textContent || '').startsWith(tituloB); i++) await Promise.resolve()
          const v = {
            titulo: document.querySelector('#edd-titulo')?.textContent ?? null,
            leyendo: Boolean(document.querySelector('.edd-msg')?.textContent?.includes('Leyendo')),
            grilla: document.querySelector('.edd-grilla-estado')?.textContent ?? null,
            botonesGrilla: [...document.querySelectorAll('.edd-grilla-btns button')].map((b) => b.disabled),
          }
          tecla('z', { code: 'KeyZ', ctrlKey: true })
          tecla('g', { code: 'KeyG' })
          return v
        }, B.item.titulo)
        // Se llega al render del cambio con el título de B. Puede ser con la grilla de A todavía (el
        // efecto que la vacía corre después) o ya vaciada («Leyendo el audio…»: con React 18 la
        // tecla vacía el estado en el mismo turno). En los dos, lo de abajo verifica que Ctrl+Z y
        // G no mandaron nada ni guardaron el ajuste de A bajo la clave de B.
        afirmar((vista.titulo || '').startsWith(B.item.titulo), `no se llegó al render del cambio de tema: ${json(vista)}`)
        if (!vista.leyendo) {
          afirmar((vista.grilla || '').startsWith('estimada'), `render del cambio sin la grilla de A: ${json(vista)}`)
          // Sin los datos del tema nuevo, los botones de la grilla (que no harían nada) se ven deshabilitados.
          afirmar(vista.botonesGrilla.length > 0 && vista.botonesGrilla.every(Boolean), `en el render del cambio de tema los botones de la grilla no están deshabilitados: ${json(vista.botonesGrilla)}`)
        }
        await temaCargado('B')
        await new Promise((r) => setTimeout(r, 300))
        igual(pedidos, [], 'Ctrl+Z en el render del cambio de tema mandó una escritura con el historial del tema anterior')
        igual(await marcasApi(ctx, B.rid), [], 'el tema nuevo recibió una marca del historial del anterior')
        igual(await page.evaluate((k) => localStorage.getItem(k), clave(B.rid)), null, 'G en el render del cambio guardó el ajuste del tema anterior bajo la clave del nuevo')
        igual(await botonesHistorial(), [true, true], 'el tema nuevo arrancó con historial')

        // (2) De vuelta en A, una memory nueva; Ctrl+Z (que la borra) y AvPág en el mismo turno, con
        // la respuesta del DELETE demorada: llega con B abierto y no puede tocar su historial.
        await pasarTema(page, 'PageUp')
        await hasta(() => page.$eval('#edd-titulo', (h) => h.textContent), (v) => v.startsWith(A.item.titulo), 'RePág no volvió a A')
        await esperarOnda(page, 'A', true)
        await temaCargado('A')
        await page.focus('.edd-onda')
        const mem2 = await conTecla(page, ['m'], 'POST')
        igual(mem2.status, 201, 'M no creó la segunda memory en A')
        await filasComoApi(page, ctx, A.rid, 'A con la segunda memory')
        // Una sola intercepción para todo el caso: demora 1,5 s los pedidos de marcas del método
        // que diga `demora.metodo` (null = ninguno).
        const demora = { metodo: 'DELETE' }
        await interceptar(page, async (req) => {
          if (demora.metodo && req.method() === demora.metodo && /\/marcas/.test(new URL(req.url()).pathname)) await new Promise((r) => setTimeout(r, 1500))
          return false
        })
        const tecleado = (teclas) => page.evaluate((ts) => {
          const onda = document.querySelector('.edd-onda')
          for (const [key, extra] of ts) onda.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...extra }))
        }, teclas)
        // Espera la respuesta demorada de A con B ya cargado (si llegara antes, el caso no
        // probaría nada) y revisa que B no se enteró: sin historial, sin «✓ Guardado», sin marcas.
        const respuestaTardia = async (metodo, que) => {
          let llego = false
          const tarde = page.waitForResponse((r) => r.request().method() === metodo && /\/marcas/.test(new URL(r.url()).pathname), { timeout: ESPERA_MS })
          tarde.then(() => { llego = true }, () => {})
          return async () => {
            await hasta(() => page.$eval('#edd-titulo', (h) => h.textContent), (v) => v.startsWith(B.item.titulo), `${que}: AvPág no pasó a B`)
            await temaCargado(`${que}: B`)
            afirmar(!llego, `${que}: la respuesta de A llegó antes de que B terminara de cargar: el caso no probaría nada`)
            const r = await tarde
            await new Promise((ok) => setTimeout(ok, 400))
            igual(await botonesHistorial(), [true, true], `${que}: la respuesta tardía de A le registró un paso a B (Deshacer/Rehacer habilitado en B)`)
            const est = await page.$eval('.edd-estado', (e) => e.textContent)
            afirmar(!est.includes('Guardado'), `${que}: B dice «Guardado» por una escritura de A: ${json(est)}`)
            igual(await marcasApi(ctx, B.rid), [], `${que}: B recibió marcas`)
            return r
          }
        }
        const volverA = async (que) => {
          await pasarTema(page, 'PageUp')
          await hasta(() => page.$eval('#edd-titulo', (h) => h.textContent), (v) => v.startsWith(A.item.titulo), `${que}: RePág no volvió a A`)
          await esperarOnda(page, `${que}: A`, true)
          await temaCargado(`${que}: A`)
        }
        const enB2 = await respuestaTardia('DELETE', 'Ctrl+Z')
        await tecleado([['z', { code: 'KeyZ', ctrlKey: true }], ['PageDown', { code: 'PageDown' }]])
        igual((await enB2()).status(), 200, 'el DELETE del Ctrl+Z de A no se hizo')
        afirmar(!(await page.$eval('.edd-aviso', (e) => e.textContent)).includes('Deshecho'), 'B dice «Deshecho.» por un Ctrl+Z del tema anterior')

        // (3) Una acción DIRECTA en A (M) y AvPág, con el POST demorado: la memory se crea en A
        // (su tema) y B no registra el paso, ni dice «✓ Guardado», ni recibe la marca.
        demora.metodo = null
        await volverA('(3)')
        demora.metodo = 'POST'
        const enB3 = await respuestaTardia('POST', 'M')
        await tecleado([['m', { code: 'KeyM' }], ['PageDown', { code: 'PageDown' }]])
        igual((await enB3()).status(), 201, 'el POST de la M de A no se hizo')
        demora.metodo = null
        const enA3 = await marcasApi(ctx, A.rid)
        igual(enA3.map((x) => x.tipo), ['memory'], 'la memory de la M tenía que quedar en A (su tema)')

        // (4) Cambiar el tipo en A (memory → hot cue: crear el cue y RECIÉN después borrar la
        // memory) y AvPág, con el POST demorado: el cambio se completa contra A aunque llegue con
        // B abierto. En A queda UNA marca (la nueva), no memory + hot cue en el mismo tiempo.
        await volverA('(4)')
        await filasComoApi(page, ctx, A.rid, '(4): A con la memory')
        demora.metodo = 'POST'
        const enB4 = await respuestaTardia('POST', 'cambio de tipo')
        await abrirFila(page, enA3[0].id)
        await page.select(`.edd-fila[data-id="${enA3[0].id}"] select.edd-tipo`, 'cue')
        await tecleado([['PageDown', { code: 'PageDown' }]])
        igual((await enB4()).status(), 201, 'el POST del cambio de tipo de A no se hizo')
        demora.metodo = null
        await hasta(() => marcasApi(ctx, A.rid), (ms) => ms.length === 1 && ms[0].tipo === 'cue' && ms[0].inicio === enA3[0].inicio,
          'el cambio de tipo interrumpido por AvPág no se completó en A (tenía que quedar solo el hot cue nuevo)')
        await volverA('(4) vuelta')
        const finalA = await filasComoApi(page, ctx, A.rid, '(4): la lista de A después del cambio de tipo')
        igual(finalA.map((x) => x.tipo), ['cue'], 'A no muestra una sola marca (la nueva)')

        // (5) Lo mismo con un Ctrl+Z de un cambio de tipo (un grupo: crear la vieja + borrar la
        // nueva) interrumpido por AvPág: las dos sub-operaciones se completan contra A.
        const borraCue = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await abrirFila(page, finalA[0].id)
        await page.select(`.edd-fila[data-id="${finalA[0].id}"] select.edd-tipo`, 'memory')
        igual((await borraCue).status(), 200, '(5): pasar el hot cue a memory no borró el cue')
        const conMemory = await filasComoApi(page, ctx, A.rid, '(5): A con la memory')
        igual(conMemory.map((x) => x.tipo), ['memory'], '(5): A tendría que tener solo la memory')
        await hasta(() => page.$eval('.edd-estado', (e) => e.textContent), (s) => !s.includes('Guardando'), '(5): el estado no terminó de guardar')
        demora.metodo = 'POST'
        const enB5 = await respuestaTardia('POST', 'Ctrl+Z del cambio de tipo')
        await tecleado([['z', { code: 'KeyZ', ctrlKey: true }], ['PageDown', { code: 'PageDown' }]])
        igual((await enB5()).status(), 201, 'el POST del Ctrl+Z del cambio de tipo no se hizo')
        demora.metodo = null
        await hasta(() => marcasApi(ctx, A.rid), (ms) => ms.length === 1 && ms[0].tipo === 'cue' && ms[0].inicio === conMemory[0].inicio,
          'el Ctrl+Z del cambio de tipo interrumpido por AvPág no se completó en A (tenía que quedar solo el hot cue recreado)')
        // (6) Igual que (5) pero AvPág cuando el POST del grupo YA salió (después de la relectura
        // previa al deshacer): la respuesta vuelve con B abierto y el borrado que falta se
        // termina contra A (sin esto quedaban memory + hot cue en el mismo tiempo).
        await volverA('(6)')
        const conCue = await filasComoApi(page, ctx, A.rid, '(6): A con el hot cue')
        await abrirFila(page, conCue[0].id)
        const borra6 = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await page.select(`.edd-fila[data-id="${conCue[0].id}"] select.edd-tipo`, 'memory')
        igual((await borra6).status(), 200, '(6): pasar el hot cue a memory no borró el cue')
        await filasComoApi(page, ctx, A.rid, '(6): A con la memory')
        await hasta(() => page.$eval('.edd-estado', (e) => e.textContent), (s) => !s.includes('Guardando'), '(6): el estado no terminó de guardar')
        demora.metodo = 'POST'
        const enB6 = await respuestaTardia('POST', '(6) Ctrl+Z con el POST ya en camino')
        const salio = page.waitForRequest((r) => r.method() === 'POST' && /\/marcas$/.test(new URL(r.url()).pathname), { timeout: ESPERA_MS })
        await tecleado([['z', { code: 'KeyZ', ctrlKey: true }]])
        await salio
        await tecleado([['PageDown', { code: 'PageDown' }]])
        igual((await enB6()).status(), 201, '(6): el POST del Ctrl+Z no se hizo')
        demora.metodo = null
        await hasta(() => marcasApi(ctx, A.rid), (ms) => ms.length === 1 && ms[0].tipo === 'cue',
          '(6): el grupo cortado a la mitad por AvPág no se completó en A (tenía que quedar solo el hot cue recreado, sin la memory)')
        igual(await marcasApi(ctx, B.rid), [], '(6): B no puede recibir nada')
      })
    }],
    ['editor D (f54): un Ctrl+Z rechazado A MEDIAS (memory de otra sesión → hot cue; el DELETE da 400) deja lo guardado en el historial y lo dice', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        // La memory la hizo OTRA sesión: no está en el historial de este editor.
        const ajena = (await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'memory', inicio: 7 })).data.marca
        let rechazar = 0
        await interceptar(page, async (req) => {
          if (rechazar > 0 && req.method() === 'DELETE' && /\/marcas\/\d+$/.test(new URL(req.url()).pathname)) {
            rechazar -= 1
            await req.respond({ status: 400, contentType: 'application/json', body: JSON.stringify({ error: 'rechazo simulado' }) })
            return true
          }
          return false
        })
        await abrirEditorD(page, ctx, t.item.titulo)
        await abrirFila(page, ajena.id)
        const borraMem = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await page.select(`.edd-fila[data-id="${ajena.id}"] select.edd-tipo`, 'cue')
        igual((await borraMem).status(), 200, 'pasar la memory a hot cue no borró la memory')
        igual((await filasComoApi(page, ctx, t.rid, 'con el hot cue')).map((m) => m.tipo), ['cue'], 'tenía que quedar solo el hot cue')
        await hasta(() => page.$eval('.edd-estado', (e) => e.textContent), (s) => !s.includes('Guardando'), 'el estado no terminó de guardar')
        // Ctrl+Z = [crear la memory, borrar el cue]: la memory vuelve y el DELETE del cue da 400.
        rechazar = 1
        await page.focus('.edd-onda')
        const recrea = page.waitForResponse((r) => r.request().method() === 'POST', { timeout: ESPERA_MS })
        const d400 = page.waitForResponse((r) => r.request().method() === 'DELETE', { timeout: ESPERA_MS })
        await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control')
        const memVuelta = await (await recrea).json()
        igual((await d400).status(), 400, 'el borrado del cue tenía que recibir el 400 simulado')
        const despues = await filasComoApi(page, ctx, t.rid, 'después del Ctrl+Z a medias')
        igual(despues.map((m) => m.tipo).sort(), ['cue', 'memory'], 'en la base quedan el cue y la memory recreada')
        const av = await hasta(() => page.$eval('.edd-aviso', (e) => e.textContent), (x) => x.includes('quedó a medias'), 'el aviso no dice que quedó a medias')
        afirmar(av.includes('SÍ se guardó') && !av.includes('Lo saqué del historial'), `el aviso tiene que decir que una parte sí se guardó: ${json(av)}`)
        igual(await page.$eval('.edd-marcar button[aria-keyshortcuts="Control+Z"]', (b) => b.disabled), false, 'Deshacer quedó deshabilitado: lo guardado se perdió del historial')
        // El Ctrl+Z siguiente saca lo que sí se guardó (la memory recreada): vuelve a quedar el cue.
        const z2 = await conTecla(page, ['Control', 'z'], 'DELETE')
        igual([z2.status, (z2.data.marcas || []).map((m) => m.id)], [200, despues.filter((m) => m.tipo === 'cue').map((m) => m.id)],
          `el Ctrl+Z siguiente tenía que borrar la memory recreada (${memVuelta.marca.id})`)
        await filasComoApi(page, ctx, t.rid, 'después de deshacer lo guardado a medias')
      })
    }],

    ['editor D (f54): «Borrar repetidos» que falla a la mitad dice cuántas borró y cuántas quedan (no «quedó una de cada»); Ctrl+Z las devuelve', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Sin grilla']
        for (let i = 0; i < 6; i++) await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'loop', inicio: 2, fin: 4 })
        let deletes = 0
        await interceptar(page, async (req) => {
          if (req.method() !== 'DELETE' || !/\/marcas\/\d+$/.test(new URL(req.url()).pathname)) return false
          deletes += 1
          if (deletes !== 3) return false
          await req.respond({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'falla simulada' }) })
          return true
        })
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.click('.edd-borrar-rep')
        await page.click('.edd-rep-si')
        const av = await hasta(() => page.$eval('.edd-aviso', (e) => e.textContent), (x) => x.startsWith('⚠ Borré'), 'no avisó lo que pasó con el borrado a medias')
        afirmar(av.startsWith('⚠ Borré 2 de 5; quedan 3 repetidas') && !av.includes('quedó una de cada'), `el aviso miente sobre lo que se borró: ${json(av)}`)
        await hasta(() => page.$eval('.edd-repetidos', (e) => e.textContent).catch(() => ''), (x) => x.includes('Hay 3 loops repetidos'), 'el aviso de repetidos no quedó con las 3 que faltan')
        igual((await filasComoApi(page, ctx, t.rid, 'después del borrado a medias')).length, 4, 'tenían que quedar 4 loops (6 − 2)')
        await page.focus('.edd-onda')
        await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control')
        await hasta(() => marcasApi(ctx, t.rid), (m) => m.length === 6, 'Ctrl+Z no volvió a poner las 2 que se borraron')
      })
    }],

    ['editor D (f54): Ctrl+Z apretado mientras una creación viaja espera su turno y deshace ESA creación (no la anterior, y el futuro queda)', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        let demorar = 0
        await interceptar(page, async (req) => {
          if (demorar <= 0 || req.method() !== 'POST' || !/\/marcas$/.test(new URL(req.url()).pathname)) return false
          demorar -= 1
          await new Promise((r) => setTimeout(r, 1200))
          await req.continue()
          return true
        })
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.focus('.edd-onda')
        const mem = await conTecla(page, ['m'], 'POST')
        igual(mem.status, 201, 'M no creó la memory')
        await filasComoApi(page, ctx, t.rid, 'con la memory')
        const escr = pedidosDeMarcas(page)
        demorar = 1
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('c')                         // POST demorado 1,2 s
        await new Promise((r) => setTimeout(r, 150))
        await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control')
        await hasta(() => escr.length, (n) => n === 2, 'no salieron el POST del cue y el DELETE del deshacer')
        await hasta(() => page.$eval('.edd-aviso', (e) => e.textContent), (x) => x === 'Deshecho.', 'el Ctrl+Z no terminó')
        afirmar(escr[0].startsWith('POST') && escr[1].startsWith('DELETE'), `el orden tenía que ser crear el cue y después deshacerlo: ${json(escr)}`)
        const api_ = await filasComoApi(page, ctx, t.rid, 'después del Ctrl+Z')
        igual(api_.map((m) => [m.id, m.tipo]), [[mem.data.marca.id, 'memory']], 'el Ctrl+Z deshizo otra cosa: tenía que quedar la memory y no el cue')
        igual(await page.$eval('.edd-marcar button[aria-keyshortcuts="Control+Y"]', (b) => b.disabled), false, 'Rehacer quedó deshabilitado: se perdió el futuro')
        const y = await conTecla(page, ['Control', 'y'], 'POST')
        igual([y.status, y.data.marca.tipo], [201, 'cue'], 'Ctrl+Y no volvió a poner el cue')
      })
    }],

    ['editor D (f54): «Borrar repetidos» deja el hot loop con nombre del pad (no el suelto más viejo) y el diálogo dice cuál queda', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Sin grilla']
        const suelto = (await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'loop', inicio: 20, fin: 22 })).data.marca
        const hot = (await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'loop', inicio: 20, fin: 22, num: 0, nombre: 'Intro' })).data.marca
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.click('.edd-borrar-rep')
        const conf = await page.$eval('.edd-rep-conf', (e) => e.textContent)
        afirmar(conf.includes('queda loop del pad A 0:20.000 a 0:22.000 «Intro»'), `el diálogo no dice cuál queda: ${json(conf)}`)
        await page.click('.edd-rep-si')
        const api_ = await hasta(() => marcasApi(ctx, t.rid), (m) => m.length === 1, 'no se borró el repetido')
        igual(api_.map((m) => m.id), [hot.id], `se borró el hot loop con nombre (tenía que irse el suelto ${suelto.id})`)
      })
    }],

    ['editor D (f54): K con un navegador que no deja guardar: la grilla se ancla y se dice que dura hasta cerrar la pantalla', async (page, ctx) => {
      await page.evaluateOnNewDocument(() => {
        const orig = Storage.prototype.setItem
        Storage.prototype.setItem = function (k, v) {
          if (String(k).startsWith('musiflix.editorD.')) throw new DOMException('bloqueado', 'SecurityError')
          return orig.call(this, k, v)
        }
      })
      await conEditor(ctx, async ({ por }) => {
        const t = por['Sin grilla']
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.focus('.edd-onda')
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('k')
        await hasta(() => page.$eval('.edd-grilla-chip', (e) => e.textContent), (v) => v === 'GRILLA ANCLADA POR VOS', 'K no ancló la grilla')
        const av = await page.$eval('.edd-aviso', (e) => e.textContent)
        afirmar(av.startsWith('⚠ Grilla anclada') && av.includes('solo hasta que cierres esta pantalla'), `el aviso no dice que no se pudo guardar: ${json(av)}`)
        const ayuda = await page.$eval('#edd-ayuda', (e) => e.textContent)
        afirmar(ayuda.includes('Este navegador no deja guardar') && !ayuda.includes('quedan guardados solo en este navegador'), `la ayuda sigue diciendo que queda guardado: ${json(ayuda)}`)
      })
    }],

    ['editor D (f54): a 400×800 marcar y el aviso quedan a la vista sin scroll (y con scroll); banderas y tirador del loop de 24 px o más', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        const P = 60 / t.bpm
        await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'loop', inicio: 1, fin: Math.round((1 + 4 * P) * 1000) / 1000 })
        await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'cue', inicio: 1, num: 0, nombre: 'Primer cue con un nombre largo' })
        // 1440: objetivos táctiles (WCAG 2.5.8: 24 × 24).
        await abrirEditorD(page, ctx, t.item.titulo)
        await clicZoom(page, '2')
        const tam = await page.evaluate(() => ({
          flags: [...document.querySelectorAll('.edd-flag')].map((f) => Math.round(f.getBoundingClientRect().height)),
          fin: (() => { const r = document.querySelector('.edd-loop-fin').getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)] })(),
        }))
        afirmar(tam.flags.length > 0 && tam.flags.every((h) => h >= 24), `banderas de menos de 24 px de alto: ${json(tam.flags)}`)
        afirmar(tam.fin[0] >= 24 && tam.fin[1] >= 24, `el tirador de la salida del loop mide ${json(tam.fin)} (mínimo 24 × 24)`)
        // 400 × 800 (celular).
        await page.setViewport({ width: 400, height: 800 })
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.focus('.edd-onda')
        await page.keyboard.press('o')                      // sin entrada: avisa
        await hasta(() => page.$eval('.edd-aviso', (e) => e.textContent), (x) => x.includes('Primero marcá la entrada'), 'O sin entrada no avisó')
        const medir = () => page.evaluate(() => {
          const r = (e) => { const b = e.getBoundingClientRect(); return { top: Math.round(b.top), bottom: Math.round(b.bottom) } }
          return {
            botones: [...document.querySelectorAll('.edd-marcar button')].map((b) => ({ txt: (b.getAttribute('aria-label') || b.textContent).trim().slice(0, 20), ...r(b) })),
            aviso: r(document.querySelector('.edd-aviso')),
            ancho: document.documentElement.scrollWidth,
            alto: window.innerHeight,
          }
        })
        for (const scroll of [0, 700]) {
          await page.$eval('.edd-fondo', (f, y) => { f.scrollTop = y }, scroll)
          await new Promise((r) => setTimeout(r, 100))
          const m = await medir()
          for (const b of m.botones) afirmar(b.top >= 0 && b.bottom <= m.alto, `con scroll ${scroll}: «${b.txt}» queda fuera de la pantalla (${b.top}–${b.bottom} de ${m.alto})`)
          afirmar(m.aviso.top >= 0 && m.aviso.bottom <= m.alto, `con scroll ${scroll}: el aviso queda fuera de la pantalla: ${json(m.aviso)}`)
          igual(m.ancho, 400, 'a 400 px no puede haber scroll horizontal')
        }
        // La lista no esconde los nombres en el celular.
        const nom = await page.$$eval('.edd-fila-n', (ns) => ns.filter((n) => window.__visible(n)).map((n) => n.textContent))
        afirmar(nom.includes('Primer cue con un nombre largo'), `a 400 px la lista no muestra el nombre: ${json(nom)}`)
      })
    }],

    ['editor D (f54): textos — «memory cue repetida», beats con coma, y el aviso de una creación que falla no dice que se creó', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        const P = (await onda3Api(ctx, t.rid)).grilla.periodo_s
        await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'memory', inicio: 5 })
        await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'memory', inicio: 5 })
        await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'loop', inicio: 1, fin: Math.round((1 + 4.3 * P) * 1000) / 1000 })
        let fallar = 0
        await interceptar(page, async (req) => {
          if (fallar <= 0 || req.method() !== 'POST' || !/\/marcas$/.test(new URL(req.url()).pathname)) return false
          fallar -= 1
          await req.respond({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'falla simulada' }) })
          return true
        })
        await abrirEditorD(page, ctx, t.item.titulo)
        const rep = await page.$eval('.edd-rep-txt', (e) => e.textContent)
        afirmar(rep.includes('1 memory cue repetida en 0:05.000'), `concordancia: ${json(rep)}`)
        await clicZoom(page, '4')
        const flags = await page.$$eval('.edd-flag', (fs) => fs.map((f) => f.textContent))
        afirmar(flags.some((f) => f.includes('⟲ 4,3 beats')), `los beats del loop van con coma: ${json(flags)}`)
        fallar = 1
        await page.focus('.edd-onda')
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('m')
        const av = await hasta(() => page.$eval('.edd-aviso', (e) => e.textContent), (x) => x.includes('no se guardó'), 'la memory que falló no avisó que no se guardó')
        afirmar(av.startsWith('⚠ Memory cue en'), `el aviso de la memory que falló: ${json(av)}`)
      })
    }],

    ['editor D (f54): lo que se dice de la grilla y los golpes — chip «GRILLA ESTIMADA», golpes solo en un tema sin grilla (no al ocultarla), Loop out sin entrada deshabilitado, Ctrl+Z mientras carga', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        // Con grilla: el chip a la vista dice que es estimada.
        await abrirEditorD(page, ctx, por['Con grilla'].item.titulo)
        const chip = await page.$eval('.edd-grilla-chip', (e) => ({ txt: e.textContent, visible: window.__visible(e) }))
        afirmar(chip.visible && chip.txt.startsWith('GRILLA ESTIMADA · conf'), `el chip de la grilla estimada no está a la vista: ${json(chip)}`)
        await hasta(() => page.$eval('.edd-onda', (e) => e.dataset.tramo), (v) => v === 'detalle', 'no cargó el tramo con resolución')
        // G la oculta: el tema SIGUE teniendo grilla, no aparecen golpes de graves.
        await page.focus('.edd-onda')
        await page.keyboard.press('g')
        await hasta(() => page.$$eval('.edd-beat', (b) => b.length), (n) => n === 0, 'G no ocultó la grilla')
        await new Promise((r) => setTimeout(r, 300))
        igual(await page.$$eval('.edd-golpe', (k) => k.length), 0, 'con la grilla oculta no se dibujan golpes de graves (el tema tiene grilla)')
        await page.keyboard.press('g')
        // Loop out: deshabilitado sin entrada, primario con entrada.
        igual(await page.$eval('.edd-loopout', (b) => b.getAttribute('aria-disabled')), 'true', 'Loop out sin entrada tiene que verse deshabilitado')
        await page.keyboard.press('i')
        await hasta(() => page.$eval('.edd-loopout', (b) => [b.getAttribute('aria-disabled'), b.classList.contains('btn-primary')]), (v) => v[0] === null && v[1], 'con la entrada marcada Loop out tenía que habilitarse')
        // Sin grilla: la leyenda dice qué son las marquitas.
        const B = por['Sin grilla']
        let demorarGet = false
        await interceptar(page, async (req) => {
          if (!demorarGet || req.method() !== 'GET' || !new URL(req.url()).pathname.endsWith(`/tracks/${B.rid}/marcas`)) return false
          await new Promise((r) => setTimeout(r, 1500))
          await req.continue()
          return true
        })
        demorarGet = true
        await page.focus('.edd-onda')
        await page.keyboard.press('PageDown')
        await hasta(() => page.$eval('#edd-titulo', (h) => h.firstChild.textContent), (v) => v === B.item.titulo, 'AvPág no pasó al tema sin grilla')
        await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control')
        await hasta(() => page.$eval('.edd-aviso', (e) => e.textContent), (x) => x.startsWith('Todavía estoy leyendo el tema'), 'Ctrl+Z mientras carga el tema no dijo por qué no hace nada')
        demorarGet = false
        await esperarOnda(page, B.item.titulo)
        await hasta(() => page.$eval('.edd-onda', (e) => e.dataset.tramo), (v) => v === 'detalle', 'no cargó el tramo con resolución (sin grilla)')
        await hasta(() => page.$$eval('.edd-golpe', (k) => k.length), (n) => n > 0, 'sin grilla no se dibujaron los golpes de graves')
        const ley = await page.$eval('.edd-leyenda', (e) => e.textContent)
        afirmar(ley.includes('golpes de graves (detectados, no son beats)'), `la leyenda no dice qué son los golpes: ${json(ley)}`)
      })
    }],
    ['editor D (f54): Ctrl+Z en cola detrás de una creación que FALLA (500 y sin red) no deshace el paso anterior y lo dice', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        let modo = null                               // '500' | 'red' | null
        await interceptar(page, async (req) => {
          if (!modo || req.method() !== 'POST' || !/\/marcas$/.test(new URL(req.url()).pathname)) return false
          const m = modo
          modo = null
          await new Promise((r) => setTimeout(r, 600))
          if (m === 'red') await req.abort('failed')
          else await req.respond({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'falla simulada' }) })
          return true
        })
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.focus('.edd-onda')
        const mem = await conTecla(page, ['m'], 'POST')
        igual(mem.status, 201, 'M no creó la memory')
        await filasComoApi(page, ctx, t.rid, 'con la memory')
        for (const caso of ['500', 'red']) {
          const escr = pedidosDeMarcas(page)
          modo = caso
          await page.keyboard.press('ArrowRight')
          await page.keyboard.press('ArrowRight')
          await page.keyboard.press('c')                 // el POST falla a los 600 ms
          await new Promise((r) => setTimeout(r, 100))
          await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control')
          await hasta(() => page.$eval('.edd-aviso', (e) => e.textContent), (x) => x === 'No había nada que deshacer: el hot cue no se guardó.',
            `(${caso}) el Ctrl+Z en cola detrás del hot cue que falló no lo dijo`)
          await new Promise((r) => setTimeout(r, 500))
          igual(escr.filter((x) => !x.startsWith('POST')), [], `(${caso}) el Ctrl+Z en cola mandó algo (deshizo el paso anterior)`)
          igual((await marcasApi(ctx, t.rid)).map((m) => [m.id, m.tipo]), [[mem.data.marca.id, 'memory']], `(${caso}) la memory anterior tenía que seguir`)
          igual(await page.$eval('.edd-marcar button[aria-keyshortcuts="Control+Z"]', (b) => b.disabled), false, `(${caso}) el paso de la memory tenía que seguir en el historial`)
        }
      })
    }],

    ['editor D (f54): AvPág con un Ctrl+Z en cola no lo pierde en silencio: el tema nuevo dice que no se deshizo', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const A = por['Con grilla']
        const B = por['Sin grilla']
        let demorar = 0
        await interceptar(page, async (req) => {
          if (demorar <= 0 || req.method() !== 'POST' || !/\/marcas$/.test(new URL(req.url()).pathname)) return false
          demorar -= 1
          await new Promise((r) => setTimeout(r, 1500))
          await req.continue()
          return true
        })
        await abrirEditorD(page, ctx, A.item.titulo)
        await page.focus('.edd-onda')
        const escr = pedidosDeMarcas(page)
        demorar = 1
        await page.keyboard.press('c')                   // POST demorado 1,5 s
        await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control')   // en cola
        await page.keyboard.press('PageDown')
        await hasta(() => page.$eval('#edd-titulo', (h) => h.firstChild.textContent), (v) => v === B.item.titulo, 'AvPág no pasó a B')
        await hasta(() => page.$eval('.edd-aviso', (e) => e.textContent), (x) => x === `«${A.item.titulo}»: el Ctrl+Z en cola no se deshizo porque pasaste de tema.`,
          'el Ctrl+Z en cola se descartó en silencio al pasar de tema')
        await hasta(() => escr.length, (n) => n === 1, 'no salió el POST del cue')
        await new Promise((r) => setTimeout(r, 1800))
        igual(escr.filter((x) => x.startsWith('DELETE')), [], 'el Ctrl+Z en cola no puede aplicarse en ningún tema')
        igual((await marcasApi(ctx, A.rid)).map((m) => m.tipo), ['cue'], 'el cue se crea en A (su tema)')
        igual(await page.$eval('.edd-aviso', (e) => e.textContent), `«${A.item.titulo}»: el Ctrl+Z en cola no se deshizo porque pasaste de tema.`, 'el aviso tiene que quedar')
      })
    }],

    ['editor D (f54): en el celular (400×800 y 360×640) el selector «N beats» del dock se lee entero y los botones siguen de 44 px', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        for (const [w, h] of [[400, 800], [360, 640]]) {
          await page.setViewport({ width: w, height: h })
          await abrirEditorD(page, ctx, por['Con grilla'].item.titulo)
          for (const n of ['4', '16']) {
            await page.select('.edd-dock .edd-loopn', n)
            const m = await page.$eval('.edd-dock .edd-loopn', (s) => {
              const cs = getComputedStyle(s)
              const c = document.createElement('canvas').getContext('2d')
              c.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`
              const texto = s.options[s.selectedIndex].text
              // El select es nativo: Chrome dibuja la flecha (~20 px) DENTRO de la caja de contenido,
              // así que al texto le queda el ancho menos los dos paddings y la flecha.
              const libre = s.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - 20
              return { texto, mide: Math.ceil(c.measureText(texto).width), libre: Math.floor(libre), alto: Math.round(s.getBoundingClientRect().height) }
            })
            afirmar(m.mide <= m.libre, `a ${w} px «${m.texto}» no entra en el selector: mide ${m.mide} px y hay ${m.libre}`)
            afirmar(m.alto >= 44, `a ${w} px el selector mide ${m.alto} px de alto`)
          }
          const bajos = await page.$$eval('.edd-dock .edd-marcar button', (bs) => bs.map((b) => [b.getAttribute('aria-label') || b.textContent.trim(), Math.round(b.getBoundingClientRect().height), Math.round(b.getBoundingClientRect().width)]).filter(([, hh, ww]) => hh < 44 || ww < 44))
          igual(bajos, [], `a ${w} px hay botones del dock de menos de 44 px`)
          igual(await page.evaluate(() => document.documentElement.scrollWidth), w, `a ${w} px no puede haber scroll horizontal`)
        }
      })
    }],
    ['editor D (f54): la X quita la marca mal puesta — desde el pad, la bandera y la lista: un DELETE, aviso, Ctrl+Z la devuelve igual y Ctrl+Y la vuelve a quitar; no mueve el cabezal', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        const P = (await onda3Api(ctx, t.rid)).grilla.periodo_s
        const cue = (await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'cue', inicio: 1, num: 0, nombre: 'Primer cue' })).data.marca
        const mem = (await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'memory', inicio: 3 })).data.marca
        const finLoop = Math.round((5 + 4 * P) * 1000) / 1000
        const hot = (await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'loop', inicio: 5, fin: finLoop, num: 1, nombre: 'Intro' })).data.marca
        await abrirEditorD(page, ctx, t.item.titulo)
        await page.focus('.edd-onda')
        await page.keyboard.press('ArrowRight')
        await page.keyboard.press('ArrowRight')
        const cabezal = () => page.$eval('.edd-onda', (o) => o.getAttribute('aria-valuetext'))
        const antes = await cabezal()
        const escr = pedidosDeMarcas(page)
        const aviso = () => page.$eval('.edd-aviso', (e) => e.textContent)
        const sinVuelo = () => hasta(() => page.$eval('.edd-estado', (e) => e.textContent), (s) => !s.includes('Guardando'), 'el estado no terminó de guardar')

        // (1) Desde el pad A: un DELETE, el aviso, la marca se va y el cabezal no se mueve.
        igual(await page.$eval('.edd-x-pad[data-x-pad="0"]', (b) => b.getAttribute('aria-label')), 'Quitar hot cue A «Primer cue» en 0:01.000', 'aria-label de la X del pad A')
        await page.click('.edd-x-pad[data-x-pad="0"]')
        await hasta(aviso, (x) => x === 'Quité el hot cue A «Primer cue» (0:01.000). Ctrl+Z lo vuelve a poner.', 'la X del pad no avisó')
        await sinVuelo()
        igual(escr, [`DELETE ${rutaMarcas(t.rid)}/${cue.id}`], 'la X del pad tenía que mandar UN DELETE y nada más')
        igual(await cabezal(), antes, 'la X del pad movió el cabezal')
        igual(await page.$('.edd-pad[data-pad="0"].is-vacio') !== null, true, 'el pad A tenía que quedar vacío')
        igual(await page.$('.edd-x-pad[data-x-pad="0"]'), null, 'un pad vacío no tiene X')
        // Ctrl+Z la devuelve con su pad, color y nombre; Ctrl+Y la vuelve a quitar.
        await page.focus('.edd-onda')
        const z = await conTecla(page, ['Control', 'z'], 'POST')
        igual([z.status, z.data.marca.tipo, z.data.marca.num, z.data.marca.inicio, z.data.marca.nombre], [201, 'cue', 0, 1, 'Primer cue'], 'Ctrl+Z no devolvió el hot cue A como estaba')
        await hasta(() => page.$eval('.edd-pad[data-pad="0"]', (b) => [b.classList.contains('cue-c1'), b.querySelector('.edd-pad-nom')?.textContent]), (v) => v[0] && v[1] === 'Primer cue', 'el pad A no volvió con su color y su nombre')
        const y = await conTecla(page, ['Control', 'y'], 'DELETE')
        igual([y.status, (y.data.marcas || []).some((m) => m.id === z.data.marca.id)], [200, false], 'Ctrl+Y no volvió a quitar el hot cue A')
        await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control')
        const cueVuelta = (await hasta(() => marcasApi(ctx, t.rid), (ms) => ms.some((m) => m.tipo === 'cue'), 'el segundo Ctrl+Z no devolvió el cue')).find((m) => m.tipo === 'cue')
        await sinVuelo()

        // (2) Desde la bandera de la memory: discreta (no se ve hasta pasar el mouse), un DELETE, no arrastra ni selecciona.
        const flagMem = `.edd-flag[data-id="${mem.id}"]`
        igual(await page.$eval(`${flagMem} .edd-x-flag`, (b) => getComputedStyle(b).opacity), '0', 'la X de la bandera se tiene que ver solo al pasar el mouse (o con foco)')
        await page.hover(flagMem)
        await hasta(() => page.$eval(`${flagMem} .edd-x-flag`, (b) => getComputedStyle(b).opacity), (o) => o === '1', 'al pasar el mouse la X de la bandera no apareció')
        escr.length = 0
        await page.click(`${flagMem} .edd-x-flag`)
        await hasta(aviso, (x) => x === 'Quité la memory cue (0:03.000). Ctrl+Z lo vuelve a poner.', 'la X de la bandera no avisó')
        await sinVuelo()
        igual(escr, [`DELETE ${rutaMarcas(t.rid)}/${mem.id}`], 'la X de la bandera tenía que mandar UN DELETE (sin arrastrar: ningún PATCH)')
        igual(await cabezal(), antes, 'la X de la bandera movió el cabezal')
        igual(await page.$$eval('.edd-flag.is-sel, .edd-mk-loop.is-sel', (x) => x.length), 0, 'la X de la bandera seleccionó una marca')

        // (3) El hot loop tiene X en su pad B y en su bandera: las dos juntas son UN pedido.
        escr.length = 0
        await page.evaluate((id) => {
          document.querySelector('.edd-x-pad[data-x-pad="1"]').click()
          document.querySelector(`.edd-flag[data-id="${id}"] .edd-x-flag`).click()
        }, hot.id)
        await hasta(aviso, (x) => x.startsWith('Quité el loop del pad B «Intro»'), 'la X del hot loop no avisó')
        await sinVuelo()
        await new Promise((r) => setTimeout(r, 300))
        igual(escr, [`DELETE ${rutaMarcas(t.rid)}/${hot.id}`], 'las X del pad y de la bandera del MISMO hot loop mandaron más de un pedido')
        await page.focus('.edd-onda')
        const zl = await conTecla(page, ['Control', 'z'], 'POST')
        igual([zl.data.marca.tipo, zl.data.marca.num, zl.data.marca.nombre, zl.data.marca.inicio, zl.data.marca.fin], ['loop', 1, 'Intro', 5, finLoop], 'Ctrl+Z no devolvió el hot loop como estaba')
        await sinVuelo()

        // (4) Desde la lista: la X al alcance directo (sin abrir «Editar»).
        escr.length = 0
        igual(await page.$eval(`.edd-fila[data-id="${cueVuelta.id}"] .edd-borrar`, (b) => b.getAttribute('aria-label')), 'Quitar hot cue A «Primer cue» en 0:01.000', 'aria-label de la X de la lista')
        await page.click(`.edd-fila[data-id="${cueVuelta.id}"] .edd-borrar`)
        await hasta(aviso, (x) => x.startsWith('Quité el hot cue A'), 'la X de la lista no avisó')
        await sinVuelo()
        igual(escr, [`DELETE ${rutaMarcas(t.rid)}/${cueVuelta.id}`], 'la X de la lista tenía que mandar UN DELETE')
        await filasComoApi(page, ctx, t.rid, 'después de quitar desde la lista')
      })
    }],

    ['editor D (f54): la X con el servidor caído (500) avisa ⚠ y la marca sigue; contraste, objetivo táctil, foco y sin solaparse a 1440 y 400 px', async (page, ctx) => {
      await conEditor(ctx, async ({ por }) => {
        const t = por['Con grilla']
        // Los 8 pads llenos (los 8 colores, con el H blanco, el B verde y el D amarillo).
        for (let n = 0; n < 8; n++) await apiPedir(ctx, rutaMarcas(t.rid), 'POST', { tipo: 'cue', inicio: 1 + n * 0.9, num: n, nombre: `Cue ${n + 1}` })
        let fallar = 0
        await interceptar(page, async (req) => {
          if (fallar <= 0 || req.method() !== 'DELETE') return false
          fallar -= 1
          await req.respond({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'falla simulada' }) })
          return true
        })
        await abrirEditorD(page, ctx, t.item.titulo)
        // 500: el aviso lo dice y la marca sigue.
        fallar = 1
        await page.click('.edd-x-pad[data-x-pad="7"]')
        const av = await hasta(() => page.$eval('.edd-aviso', (e) => e.textContent), (x) => x.startsWith('⚠ No pude quitar'), 'la X con un 500 no avisó')
        afirmar(av.startsWith('⚠ No pude quitar el hot cue H «Cue 8» (0:07.300): no se guardó'), `aviso de la falla: ${json(av)}`)
        igual((await marcasApi(ctx, t.rid)).length, 8, 'con el 500 la marca tenía que seguir')
        igual(await page.$('.edd-pad[data-pad="7"].is-lleno') !== null, true, 'el pad H tenía que seguir lleno')

        // Contraste de la cruz sobre su fondo (≥ 4,5:1) en cada pad y bandera, y tamaño (≥ 24 × 24).
        const medir = () => page.$$eval('.edd-x', (xs) => xs.map((x) => {
          const rgb = (s) => (s.match(/[\d.]+/g) || []).map(Number)
          const lum = ([r, g, b]) => { const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4 }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b) }
          const cs = getComputedStyle(x)
          const fondo = rgb(cs.backgroundColor)
          const cruz = rgb(getComputedStyle(x, '::before').color || cs.color)
          const [a, b] = [lum(cruz), lum(fondo.length === 4 && fondo[3] === 0 ? [11, 12, 18] : fondo)].sort((p, q) => q - p)
          const r = x.getBoundingClientRect()
          return { q: x.getAttribute('aria-label'), ratio: Math.round(((a + 0.05) / (b + 0.05)) * 100) / 100, w: Math.round(r.width), h: Math.round(r.height) }
        }))
        const xs = await medir()
        afirmar(xs.length >= 8 + 8, `faltan X (pads + lista): ${xs.length}`)
        for (const x of xs) {
          afirmar(x.ratio >= 4.5, `la cruz de «${x.q}» tiene contraste ${x.ratio}:1 (mínimo 4,5)`)
          afirmar(x.w >= 24 && x.h >= 24, `«${x.q}» mide ${x.w}×${x.h} (mínimo 24 × 24)`)
        }
        // Foco con teclado: la X de una bandera se ve con foco (sin mouse).
        const flag = await page.$eval('.edd-flag.con-x', (f) => f.dataset.id)
        await page.keyboard.press('Shift')                // la última interacción es de teclado
        await page.focus(`.edd-flag[data-id="${flag}"] .edd-x-flag`)
        await hasta(() => page.$eval(`.edd-flag[data-id="${flag}"] .edd-x-flag`, (b) => [getComputedStyle(b).opacity, getComputedStyle(b).outlineStyle]), (v) => v[0] === '1' && v[1] !== 'none',
          'con foco de teclado la X de la bandera no se ve o no tiene foco visible')
        // La X del pad no tapa el tiempo ni el número de tecla.
        const choques = async () => page.$$eval('.edd-pad-w.is-lleno', (ws) => ws.flatMap((w) => {
          const x = w.querySelector('.edd-x-pad').getBoundingClientRect()
          return [...w.querySelectorAll('.edd-pad-tiempo, .edd-pad-nom, .edd-kbd')].filter((e) => window.__visible(e)).map((e) => e.getBoundingClientRect())
            .filter((r) => r.width > 0 && !(r.right <= x.left || r.left >= x.right || r.bottom <= x.top || r.top >= x.bottom))
            .map(() => w.querySelector('.edd-pad').dataset.pad)
        }))
        igual(await choques(), [], 'a 1440 la X del pad tapa el tiempo, el nombre o el número de tecla')
        igual(await page.evaluate(() => [document.documentElement.scrollWidth, document.querySelector('.edd-fondo').scrollWidth <= document.querySelector('.edd-fondo').clientWidth]), [1440, true], 'a 1440 la X agregó scroll horizontal')
        // 400 px: la X del pad de 44 de alto, sin solaparse ni scroll horizontal.
        await page.setViewport({ width: 400, height: 800 })
        await abrirEditorD(page, ctx, t.item.titulo)
        const padX = await page.$$eval('.edd-x-pad', (bs) => bs.map((b) => Math.round(b.getBoundingClientRect().height)))
        afirmar(padX.length === 8 && padX.every((h) => h >= 44), `a 400 px la X de los pads tiene que tener 44 de alto: ${json(padX)}`)
        igual(await choques(), [], 'a 400 la X del pad tapa el tiempo o el nombre')
        igual(await page.evaluate(() => document.documentElement.scrollWidth), 400, 'a 400 px la X agregó scroll horizontal')
      })
    }],
  ]
}
