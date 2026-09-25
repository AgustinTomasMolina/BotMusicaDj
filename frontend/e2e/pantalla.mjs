// Los chequeos del E2E (los corre run.mjs, que arma la base de juguete y el server).
//
// Regla de todos los casos: lo que se ESPERA sale de la API (o de la base de juguete), nunca
// del código del front. El front no se importa acá: si cambia cómo dibuja un dato, el E2E se
// tiene que enterar, no copiar el cambio. Los dos formatos que sí están escritos acá (BPM con
// un decimal, duración m:ss) son el contrato de §6, no una copia del front.
//
// Sin esperas fijas: cada paso espera un ESTADO (un selector, un atributo, qué audio suena)
// con un tope, y si el tope se vence el error dice qué quedó en pantalla.

import fs from 'node:fs'
import path from 'node:path'

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
  await respuesta
  await hasta(() => page.evaluate(() => ({
    armando: /Armando/.test(document.querySelector('.rarmar')?.textContent || ''),
    pasos: document.querySelectorAll('.rpaso').length,
  })), (v) => !v.armando && v.pasos > 0, 'el set no se dibujó después de armarlo')
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

async function semillaUno(ctx) {
  const lib = await api(ctx, '/api/radio/biblioteca')
  const uno = lib.tracks.find((t) => t.titulo === 'Uno')
  afirmar(uno, '/api/radio/biblioteca no trae el track «Uno» de la base de juguete')
  return { lib, uno }
}

/* ---------- casos ---------- */

const CASOS = [

  ['home: cada tarjeta muestra el BPM (un decimal) y la key de /api/biblioteca', async (page, ctx) => {
    const lib = await api(ctx, '/api/biblioteca')
    await abrirHome(page, ctx)
    const esperado = lib.generos.flatMap((g) => g.tracks.slice(0, 18)).map((t) => ({
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
    const tracks = lib.generos.flatMap((g) => g.tracks.slice(0, 18))
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
        img: img ? { cargada: img.complete && img.naturalWidth > 0, opacidad: getComputedStyle(img).opacity } : null,
        // El placeholder se "ve" si está visible y tiene su dibujo (el degradado), no el
        // fondo liso del recuadro.
        placeholder: !!ph && window.__visible(ph) && phCs.opacity === '1' && /gradient/.test(phCs.backgroundImage),
      }
    }, titulo)
    const conImagen = new Set(ctx.base.caratula_dibujable)
    for (const t of tracks) {
      if (conImagen.has(t.id)) {
        await hasta(() => leer(t.titulo), (v) => v.img && v.img.cargada && v.img.opacidad === '1',
          `«${t.titulo}» trae carátula (/api/cover/${t.id}) y la tarjeta no la termina mostrando`)
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
    }
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
    }))
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
    // `.app` tiene overflow-x:clip: la página NUNCA scrollea de costado, lo que se pase del
    // ancho queda cortado e invisible (peor que un scroll). Por eso no alcanza con mirar
    // scrollWidth: se busca cualquier elemento visible que se salga del viewport. Lo que
    // está adentro de un contenedor que recorta o scrollea (los estantes de la home, que se
    // deslizan de costado a propósito, o un texto truncado) se juzga por su contenedor.
    const desborde = () => page.evaluate(() => {
      const ancho = document.documentElement.clientWidth
      const recorta = (e) => /auto|scroll|hidden|clip/.test(getComputedStyle(e).overflowX)
      const fuera = [...document.querySelectorAll('body *')].filter((e) => {
        const r = e.getBoundingClientRect()
        if (r.width <= 1 || r.height <= 1 || !window.__visible(e)) return false
        if (r.right <= ancho + 1 && r.left >= -1) return false
        // Un panel fijo que está entero fuera de la pantalla es un cajón cerrado (off-canvas),
        // no contenido cortado.
        for (let a = e; a && a !== document.body; a = a.parentElement) {
          if (getComputedStyle(a).position === 'fixed') {
            const ra = a.getBoundingClientRect()
            if (ra.right <= 0 || ra.left >= ancho) return false
            break
          }
        }
        for (let a = e.parentElement; a && !a.classList.contains('app') && a !== document.body; a = a.parentElement) {
          if (recorta(a)) return false
        }
        return true
      }).slice(0, 4).map((e) => `${e.tagName.toLowerCase()}.${String(e.className).split(' ')[0]} (${Math.round(e.getBoundingClientRect().left)}..${Math.round(e.getBoundingClientRect().right)})`)
      return { scroll: document.documentElement.scrollWidth, ancho, fuera }
    })
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
]

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
