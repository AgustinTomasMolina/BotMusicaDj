// E2E del front: lo que la pantalla muestra contra lo que dice la API, en un Chrome de verdad.
//
// Solo lo que la suite de Python no puede ver (los endpoints ya los prueba tests/): que el
// BPM se dibuje con un decimal, que el `?` aparezca, que la carátula se vea, que no suenen dos
// audios a la vez, que el .m3u8 que baja el botón sea el de la API. Los chequeos están en
// pantalla.mjs; este archivo arma y desarma todo lo que necesitan:
//
//   1. busca Chrome (PUPPETEER_EXECUTABLE_PATH o el instalado); sin Chrome se SALTA, no falla;
//   2. buildea el front (`vite build`, el mismo frontend/dist que sirve server.py);
//   3. arma una base de juguete en un directorio temporal (base_juguete.py, con los catálogos
//      de tests/sinteticos.py);
//   4. levanta `uvicorn server:app` en un puerto libre apuntando a esa base, sin Redis;
//   5. corre los chequeos y, pase lo que pase, apaga el server y borra el temporal.
//
// Uso (desde frontend/):  npm run e2e            opciones: --sin-build  --solo=texto
// Python: E2E_PYTHON, o el .venv de la raíz del repo, o `python` del PATH.
// Exit: 0 ok · 1 falló un caso o el setup · 3 casos ok pero quedó el temporal sin borrar.

import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const AQUI = path.dirname(fileURLToPath(import.meta.url))
const FRONT = path.resolve(AQUI, '..')
const REPO = path.resolve(FRONT, '..')
const ARGS = process.argv.slice(2)
const SIN_BUILD = ARGS.includes('--sin-build')
const SOLO = (ARGS.find((a) => a.startsWith('--solo=')) || '').slice('--solo='.length)

// Exit 3: todos los casos pasaron pero el temporal no se pudo borrar (quedó basura en disco).
const EXIT_BASURA = 3

// Sin Chrome el E2E no se puede correr, pero eso no es un fallo del front: se sale con 0 y un
// mensaje que lo dice. Con E2E_EXIGIR_CHROME=1 (por ejemplo en una máquina de CI que sí debe
// tenerlo) la falta de Chrome es un error.
const EXIT_SALTEADO = process.env.E2E_EXIGIR_CHROME === '1' ? 1 : 0

function buscarChrome() {
  const pedido = process.env.PUPPETEER_EXECUTABLE_PATH
  if (pedido) {
    return fs.existsSync(pedido) ? { ruta: pedido } : { falta: `PUPPETEER_EXECUTABLE_PATH=${pedido} no existe.` }
  }
  const candidatos = []
  if (process.platform === 'win32') {
    for (const base of [process.env.PROGRAMFILES, process.env['PROGRAMFILES(X86)'], process.env.LOCALAPPDATA]) {
      if (base) candidatos.push(path.join(base, 'Google', 'Chrome', 'Application', 'chrome.exe'))
    }
  } else if (process.platform === 'darwin') {
    candidatos.push('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
  } else {
    candidatos.push('/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser')
  }
  const ruta = candidatos.find((c) => fs.existsSync(c))
  return ruta ? { ruta } : { falta: `No encontré Chrome en: ${candidatos.join(' · ')}. Definí PUPPETEER_EXECUTABLE_PATH.` }
}

function buscarPython() {
  if (process.env.E2E_PYTHON) return { ruta: process.env.E2E_PYTHON, origen: 'E2E_PYTHON' }
  const venv = process.platform === 'win32'
    ? path.join(REPO, '.venv', 'Scripts', 'python.exe')
    : path.join(REPO, '.venv', 'bin', 'python')
  if (fs.existsSync(venv)) return { ruta: venv, origen: '.venv de la raíz del repo' }
  return { ruta: process.platform === 'win32' ? 'python' : 'python3', origen: 'el del PATH: no hay .venv en la raíz ni E2E_PYTHON' }
}

const esperar = (ms) => new Promise((res) => setTimeout(res, ms))

// Borra el temporal. En Windows un Chrome que tarda en soltar el perfil deja archivos
// tomados (EBUSY) un rato: se reintenta hasta `limiteMs`. Devuelve false si quedó algo.
async function borrar(dir, limiteMs = 30000) {
  const t0 = Date.now()
  let ultimo = null
  while (Date.now() - t0 < limiteMs) {
    try {
      fs.rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
    } catch (e) {
      ultimo = e
    }
    if (!fs.existsSync(dir)) return true
    await esperar(500)
  }
  console.error(`E2E: no pude borrar el temporal ${dir}${ultimo ? ` (${ultimo.code || ultimo.message})` : ''}. Borralo a mano.`)
  return false
}

// Temporales de corridas anteriores que no se pudieron borrar (o de un E2E que está
// corriendo en paralelo en esta máquina): se avisa, no se tocan.
function avisarTemporalesViejos() {
  let viejos = []
  try {
    viejos = fs.readdirSync(os.tmpdir()).filter((n) => n.startsWith('musiflix-e2e-'))
  } catch { /* sin permiso para listar el temporal: no hay nada que avisar */ }
  if (viejos.length) {
    console.warn(`OJO: hay ${viejos.length} temporal(es) de corridas anteriores en ${os.tmpdir()}: ${viejos.join(', ')}. ` +
      'Si no hay otro E2E corriendo, se pueden borrar.')
  }
}

// Un puerto que el sistema da por libre. Nunca el 8000: ahí vive el server de uso diario.
function puertoLibre() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.unref()
    srv.on('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address()
      srv.close(() => (port === 8000 ? puertoLibre().then(resolve, reject) : resolve(port)))
    })
  })
}

function correrSync(cmd, args, opciones, que) {
  const r = spawnSync(cmd, args, { encoding: 'utf8', ...opciones })
  if (r.error || r.status !== 0) {
    throw new Error(`${que} falló (${r.error ? r.error.message : `exit ${r.status}`}):\n${(r.stderr || '') + (r.stdout || '')}`.trim())
  }
  return r.stdout
}

// Espera a que el server conteste. Es una espera de ESTADO (sondea hasta que responde), no un
// sleep: si el proceso se muere antes, corta enseguida con su log.
async function esperarServer(base, proceso, log, limiteMs = 30000) {
  const t0 = Date.now()
  while (Date.now() - t0 < limiteMs) {
    if (proceso.exitCode !== null) throw new Error(`uvicorn terminó con código ${proceso.exitCode} antes de contestar:\n${log.join('')}`)
    try {
      const r = await fetch(`${base}/api/radio/biblioteca`)
      if (r.ok) return
    } catch { /* todavía no escucha */ }
    await new Promise((res) => setTimeout(res, 100))
  }
  throw new Error(`uvicorn no contestó en ${limiteMs / 1000} s:\n${log.join('')}`)
}

// En Windows el python del venv es un lanzador que abre OTRO proceso: matar solo el lanzador
// dejaba el server real vivo, con el puerto y la base tomados. taskkill /T mata el árbol.
function matarArbol(proceso) {
  if (!proceso || proceso.exitCode !== null) return Promise.resolve()
  const fin = new Promise((res) => proceso.once('exit', res))
  if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(proceso.pid), '/T', '/F'], { stdio: 'ignore' })
  else proceso.kill('SIGTERM')
  return Promise.race([fin, new Promise((res) => setTimeout(res, 5000))])
}

async function main() {
  const t0 = Date.now()
  const chrome = buscarChrome()
  if (!chrome.ruta) {
    console.log(EXIT_SALTEADO
      ? `E2E FALLA: no hay Chrome y E2E_EXIGIR_CHROME=1. ${chrome.falta}`
      : `E2E SALTEADO (no es un fallo del front): ${chrome.falta}`)
    return EXIT_SALTEADO
  }
  let puppeteer
  try {
    puppeteer = (await import('puppeteer-core')).default
  } catch {
    console.log(`E2E ${EXIT_SALTEADO ? 'FALLA' : 'SALTEADO'}: falta puppeteer-core. Corré \`npm install\` en frontend/.`)
    return EXIT_SALTEADO
  }

  if (!SIN_BUILD) {
    // Con `npm run e2e`, el script corre con el `node` del PATH, que puede no ser el que corre
    // npm (en la PC del trabajo el del PATH es Node 21 y Vite 8 pide ^20.19 || >=22.12). El
    // build usa el Node de npm si lo hay: es el que el usuario eligió al llamar a npm.
    correrSync(process.env.npm_node_execpath || process.execPath, [path.join(FRONT, 'node_modules', 'vite', 'bin', 'vite.js'), 'build', '--logLevel', 'warn'],
      { cwd: FRONT }, 'vite build')
  }
  if (!fs.existsSync(path.join(FRONT, 'dist', 'index.html'))) throw new Error('No hay frontend/dist: corré sin --sin-build.')

  const { ruta: python, origen: pythonOrigen } = buscarPython()
  console.log(`Python: ${python} (${pythonOrigen})`)
  avisarTemporalesViejos()
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'musiflix-e2e-'))
  let server = null
  let browser = null
  let codigo = 1
  const log = []
  // Ctrl+C a mitad de camino: igual se apaga el server y se borra el temporal.
  const alCortar = () => { limpiar().finally(() => process.exit(130)) }
  process.once('SIGINT', alCortar)
  const limpiar = async () => {
    process.removeListener('SIGINT', alCortar)
    if (browser) {
      const b = browser
      browser = null
      const cerro = await Promise.race([b.close().then(() => true, () => false), esperar(10000).then(() => false)])
      // Plan B: un Chrome que no cierra (máquina cargada) se mata con todo su árbol; si no,
      // sigue con el perfil tomado y el temporal no se puede borrar.
      if (!cerro) {
        console.error('E2E: Chrome no cerró en 10 s; lo mato.')
        await matarArbol(b.process())
      }
    }
    await matarArbol(server)
    server = null
    return borrar(tmp)
  }

  try {
    let base
    try {
      base = JSON.parse(correrSync(python, [path.join(AQUI, 'base_juguete.py'), tmp], { cwd: REPO }, 'base_juguete.py'))
    } catch (e) {
      // El caso típico: sin .venv en la raíz cae al python del PATH, que no tiene numpy ni
      // mutagen (ModuleNotFoundError). Se dice cuál se usó y cómo elegir otro.
      e.message += `\n→ Python usado: ${python} (${pythonOrigen}). Apuntá E2E_PYTHON al Python que tiene requirements.txt instalado.`
      throw e
    }
    const puerto = await puertoLibre()
    const url = `http://127.0.0.1:${puerto}`
    fs.mkdirSync(path.join(tmp, 'datos'), { recursive: true })
    server = spawn(python, ['-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', String(puerto), '--log-level', 'warning'], {
      cwd: REPO,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: {
        ...process.env,
        DJRADIO_DB: base.djradio_db,
        MUSIFLIX_LIBRARY_XML: base.library_xml,
        MUSIFLIX_LIBRARY_ROOTS: base.library_roots.join(path.delimiter),
        MUSIFLIX_DATA_DIR: path.join(tmp, 'datos'),
        MUSIFLIX_DOWNLOADS: path.join(tmp, 'descargas-server'),
        // Vacía y no ausente: server.py hace load_dotenv(), que NO pisa lo que ya está en el
        // entorno. Así un REDIS_URL del .env no manda nada a una cola real.
        REDIS_URL: '',
        PYTHONIOENCODING: 'utf-8',
      },
    })
    server.stdout.on('data', (d) => log.push(String(d)))
    server.stderr.on('data', (d) => log.push(String(d)))
    await esperarServer(url, server, log)

    browser = await puppeteer.launch({
      executablePath: chrome.ruta,
      headless: true,
      userDataDir: path.join(tmp, 'perfil-chrome'),
      args: [
        '--autoplay-policy=no-user-gesture-required', '--mute-audio', '--no-first-run', '--no-default-browser-check',
        // Como root (un contenedor o una CI en Linux) Chrome se niega a arrancar con sandbox.
        // Solo ahí se apaga: en una PC normal el sandbox se queda.
        ...(process.platform === 'linux' && process.getuid?.() === 0 ? ['--no-sandbox'] : []),
      ],
    })
    const { correr } = await import('./pantalla.mjs')
    const resultados = await correr({ browser, url, base, tmp, solo: SOLO })

    const fallas = resultados.filter((r) => !r.ok)
    console.log('')
    for (const r of resultados) {
      console.log(`${r.ok ? '  ok   ' : '  FALLA'} ${r.nombre} (${(r.ms / 1000).toFixed(1)} s)`)
      if (!r.ok) console.log(`         ${r.error.replace(/\n/g, '\n         ')}`)
    }
    console.log(`\n${resultados.length - fallas.length}/${resultados.length} casos ok en ${((Date.now() - t0) / 1000).toFixed(1)} s`)
    if (fallas.length && log.length) console.log(`\nlog del server:\n${log.join('').slice(-3000)}`)
    codigo = fallas.length ? 1 : 0
  } finally {
    if (!(await limpiar()) && codigo === 0) codigo = EXIT_BASURA
  }
  return codigo
}

main().then((code) => process.exit(code), (e) => {
  console.error(`E2E ERROR: ${e.stack || e}`)
  process.exit(1)
})
