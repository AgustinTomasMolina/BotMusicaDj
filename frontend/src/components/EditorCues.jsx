import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { getMarcas, crearMarca, cambiarMarca, borrarMarca, getOnda3, radioAudioUrl, radioAudioMotivo } from '../api'
import { fmtTiempo, parseTiempo, padLibre, marcaDelPad, errorLoop, claseMarca, acotar, avisoDuracion } from '../cues'
import {
  ZOOM_COMPASES, ZOOM_SEGUNDOS, ZOOM_DEFECTO, AJUSTE_FINO_S, PADS, LOOP_BEATS,
  estadoGrilla, periodoBeat, lineasGrilla, snapGrilla, moverAjuste, textoAjuste, leerAjuste, guardarAjuste,
  anchoZoom, ventana, tiempoDeXV, tramoPedido, alcanza, columnas,
  historialVacio, registrar, deshacer, rehacer, reemplazarId, opConId, cuerpoCrear, diferencia, clasificarFalla, yaHecha,
  contarIguales, cerrarPaso, marcaCercana, filasBanderas, detectarGolpesGraves, alturaBanda,
  agruparIguales, repetidas, grillaAnclada, rescatarHechas,
  atajoD, fmtBpmMedido, letraPad, rotuloMarca, compasDe, nombreD, temaVigente, beatsLoop,
} from '../editorD'
import { usePlayer } from '../player/context'
import { useVolumenEn } from '../hooks'
import { IconPause, IconPlayFill } from './icons'

/* ============================================================================
   Editor grande de cues y loops (f54, opción D del canvas «DJ Radio — Editor de cues y
   loops»). Pantalla completa que se abre desde un tema de «Mis playlists» con archivo en la
   biblioteca del motor; pasa al tema anterior / siguiente de la playlist sin cerrar.

   QUÉ MUESTRA
   - La onda de 3 bandas del audio REAL (`/onda3`): graves (kick y bajo) adentro, medios y
     agudos alrededor. Zoom de 2 a 32 compases con la grilla (en segundos sin ella).
   - La grilla de beats ESTIMADA por el motor, que nunca se presenta como verdad (§6):
     rótulo «estimada» siempre, atenuada con confianza baja, botón para ocultarla y para
     correrla (medio beat — el error típico es el contratiempo — o fino). Sin `primer_beat_s`
     no se dibuja nada y se dice por qué. El ajuste vive solo en este navegador.
   - Pads A–H (hot cues y hot loops), memory cues, loops (SIEMPRE naranja), la lista de marcas
     editable, deshacer / rehacer.

   GUARDADO: como el editor de la radio, cada edición va al servidor (los endpoints de marcas,
   con sus guardas) y la lista que se dibuja es la que el servidor devolvió. «Deshacer» manda
   la operación inversa; recrear una marca borrada le da otro id y el historial lo sigue.
   ========================================================================== */

const ms3 = (t) => Math.round(t * 1000) / 1000
const FINO_CURSOR_S = 0.01
const CONEXION = 'No pude conectar con el servidor. Revisá que esté corriendo y volvé a intentar.'
const NOMBRE_TIPO = { cue: 'Hot cue', memory: 'Memory', loop: 'Loop' }
const PLURAL = { cue: ['hot cue', 'hot cues'], memory: ['memory cue', 'memory cues'], loop: ['loop', 'loops'] }

// Ancho de un texto de bandera (700 12px), para ubicarlas sin pisarse. Sin canvas: una estimación.
let ctxMedir = null
function medirTexto(txt) {
  try {
    ctxMedir = ctxMedir || document.createElement('canvas').getContext('2d')
    ctxMedir.font = '700 12px Inter, system-ui, sans-serif'
    return ctxMedir.measureText(txt).width
  } catch { return txt.length * 7.6 }
}
const coma = (x, d = 2) => Number(x).toFixed(d).replace('.', ',')
// Beats de un loop con coma decimal, como el resto de los números («4,3 beats»; «4 beats»).
const fmtBeats = (b) => (Number.isInteger(b) ? String(b) : coma(b, 1))
// Concordancia: «1 memory cue repetida» (la memory es femenina en todo el editor).
const REPETIDO = { cue: 'repetido', memory: 'repetida', loop: 'repetido' }

function motivoDe(r) {
  const d = (r && r.data) || {}
  if (typeof d.error === 'string' && d.error.trim()) return d.error
  if (typeof d.detail === 'string' && d.detail.trim()) return d.detail
  if (d.error_texto) return `El servidor falló (HTTP ${r.status}): ${d.error_texto}`
  return `El servidor contestó HTTP ${r ? r.status : '?'} sin explicación.`
}

function storage() {
  try { return window.localStorage } catch { return null }
}

/* ---------- un campo de tiempo de la lista ---------- */
function CampoTiempo({ valor, etiqueta, onCommit }) {
  const [txt, setTxt] = useState(fmtTiempo(valor))
  const [mal, setMal] = useState(false)
  useEffect(() => { setTxt(fmtTiempo(valor)); setMal(false) }, [valor])
  const commit = () => {
    const t = parseTiempo(txt)
    if (t === null) { setMal(true); return }
    setMal(false)
    if (Math.abs(t - valor) < 0.0005) { setTxt(fmtTiempo(valor)); return }
    onCommit(t)
  }
  return (
    <input className="input edd-in-t mono" value={txt} aria-label={etiqueta} aria-invalid={mal || undefined}
      inputMode="decimal" spellCheck={false} autoComplete="off" title="m:ss.mmm — Enter guarda, Esc vuelve"
      onChange={(e) => setTxt(e.target.value)} onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') { e.preventDefault(); commit() }
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setTxt(fmtTiempo(valor)); setMal(false) }
      }} />
  )
}

function CampoNombre({ valor, etiqueta, max, onCommit }) {
  const [txt, setTxt] = useState(valor || '')
  useEffect(() => { setTxt(valor || '') }, [valor])
  const commit = () => { if (txt.trim() !== (valor || '')) onCommit(txt.trim()) }
  return (
    <input className="input edd-in-n" value={txt} aria-label={etiqueta} maxLength={max || undefined}
      placeholder="sin nombre" autoComplete="off" spellCheck={false}
      onChange={(e) => setTxt(e.target.value)} onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') { e.preventDefault(); commit() }
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setTxt(valor || '') }
      }} />
  )
}

const Kbd = ({ children }) => <kbd className="edd-kbd">{children}</kbd>

/* ---------- la pantalla ---------- */
export default function EditorCues({ temas, inicial, onClose, onFallaTardia }) {
  const [idx, setIdx] = useState(() => Math.max(0, temas.findIndex((t) => t.analisis?.radio_id === inicial)))
  const tema = temas[idx] || null
  const id = tema ? tema.analisis.radio_id : null

  const [datos, setDatos] = useState(null)          // {track, marcas, limites} | {error}
  // /onda3 del tema entero | {error}, con el `id` del tema: la del tema anterior no se usa ni un
  // render (3.ª auditoría f54: entre el título nuevo y el efecto se leía la grilla del anterior).
  const [generalRaw, setGeneral] = useState(null)
  const [detalle, setDetalle] = useState(null)      // /onda3 del tramo a la vista
  // En una pantalla angosta arranca más cerca (4 compases): 8 en 360 px no dejan ver el kick.
  const [zoom, setZoom] = useState(() => (typeof window !== 'undefined' && window.innerWidth < 640 ? ZOOM_DEFECTO - 1 : ZOOM_DEFECTO))
  const [centro, setCentro] = useState(0)
  const [cursor, setCursor] = useState(0)
  const [sonando, setSonando] = useState(false)
  const [ajuste, setAjuste] = useState({ offset: 0, oculta: false })
  const [snap, setSnap] = useState(false)
  const [historial, setHistorial] = useState(historialVacio)
  const [estado, setEstado] = useState({ fase: 'leido' })
  // Lo que el servidor no confirmó: {id, texto, reconciliada}. `reconciliada` = después de la
  // falla se volvió a leer la lista del servidor y la pantalla muestra esa (auditoría f54: un
  // éxito posterior ya no borra el aviso de una marca que se perdió).
  const [fallas, setFallas] = useState([])          // las últimas 5 (el total va aparte)
  const [fallasTotal, setFallasTotal] = useState(0)
  const [releyendo, setReleyendo] = useState(0)      // relecturas del servidor en vuelo
  const [cierre, setCierre] = useState(false)        // Esc con cambios sin confirmar: pedir otra vez
  const [aviso, setAviso] = useState('')
  const [loopIn, setLoopIn] = useState(null)
  const [sel, setSel] = useState(null)              // marca seleccionada (resaltada en lista, pad y bandera)
  const [soloGraves, setSoloGraves] = useState(false) // B: solo la banda de graves (para buscar el kick)
  const [abierta, setAbierta] = useState(null)      // la fila de la lista desplegada para editar
  const [confirmaRep, setConfirmaRep] = useState(false) // «Borrar repetidos» pidiendo confirmación
  const [noGuarda, setNoGuarda] = useState(false)       // este navegador no deja guardar el ajuste
  const [, setPulso] = useState(0)                      // re-render para correr la cola de Ctrl+Z
  const [loopBeats, setLoopBeats] = useState(4)
  const [loopPad, setLoopPad] = useState(false)
  const [arrastre, setArrastre] = useState(null)    // {id, borde, t}
  const [anchoPx, setAnchoPx] = useState(0)

  const rootRef = useRef(null)
  const audioRef = useRef(null)
  const ondaRef = useRef(null)
  const canvasRef = useRef(null)
  const generalRef = useRef(null)
  const cabezalRef = useRef(null)
  const cabezalGenRef = useRef(null)
  const relojRef = useRef(null)
  const cursorRef = useRef(0)
  const idRef = useRef(id)
  idRef.current = id
  const vuelo = useRef({ n: 0 })
  const lista = useRef({ seq: 0, aplicada: 0 })   // la lista que llega más tarde no pisa a una más nueva
  const fallaSeq = useRef(0)
  // Sube cada vez que se cambia de tema: lo que vuelve de un pedido de una época anterior no
  // toca el tema abierto (auditoría f54: marcas fantasma en OTRO tema).
  const epoca = useRef(0)
  const arrastreRef = useRef(null)
  const panRef = useRef(null)
  const ocupado = useRef(false)          // una operación de deshacer/rehacer en curso
  const cola = useRef([])                // Ctrl+Z / Ctrl+Y apretados mientras tanto (−1 / +1)
  const creaciones = useRef({ n: 0, p: Promise.resolve() })   // las creaciones, de a una, por tema (ver enCola)
  // La última lista que mandó el servidor, al instante (sin esperar el render): con esta se
  // decide «ya hay una ahí» y el pad libre.
  const listaServidor = useRef([])
  // El editor se cerró: lo que vuelva después y falle se avisa por `onFallaTardia` (la playlist).
  const cerrado = useRef(false)
  const tardia = useRef(onFallaTardia)
  tardia.current = onFallaTardia
  // Cambios de tipo que quedaron a medias en un tema que ya no estaba abierto: tid → [textos].
  // Se muestran al volver a ese tema (con su lista recién leída del servidor).
  const pendientesDe = useRef(new Map())

  const player = usePlayer()
  useVolumenEn(audioRef, player.volume, player.muted)

  const track = datos && datos.track && datos.track.id === id ? datos.track : null
  const marcas = useMemo(() => (track ? datos.marcas : []), [track, datos])
  const durEje = track ? Number(track.dur) || 0 : 0
  const general = generalRaw && generalRaw.id === id ? generalRaw : null
  const durAudio = general && !general.error ? Number(general.duracion_audio) || 0 : 0
  const dur = durEje || durAudio
  const gMotor = general && !general.error ? general.grilla : null
  // Sin grilla del motor, la que ancló el dueño con K (su kick + el período medido): se dice en
  // pantalla que es suya. Con grilla del motor, esa (K no la reemplaza).
  const gAnclada = !estadoGrilla(gMotor).hay ? grillaAnclada(gMotor, track ? track.bpm : null, ajuste.ancla) : null
  const g = gAnclada || gMotor
  const eg = estadoGrilla(g)
  const grillaVisible = eg.hay && !ajuste.oculta
  const periodo = periodoBeat(g, track ? track.bpm : null)
  const ancho = anchoZoom(zoom, g)
  const v = ventana(centro, ancho, dur)
  const snapActivo = snap && grillaVisible
  const max = datos && datos.limites ? datos.limites.nombre_max : null

  /* ---------- cambiar de tema ---------- */
  useEffect(() => {
    if (!id) return undefined
    let vivo = true
    // (la época ya subió en irTema, en el mismo instante del cambio)
    vuelo.current.n = 0
    ocupado.current = false
    cola.current = []
    listaServidor.current = []
    creaciones.current = { n: 0, p: Promise.resolve() }
    setReleyendo(0)
    setCierre(false)
    const a = audioRef.current
    if (a) { a.pause(); a.src = radioAudioUrl(id) }
    cursorRef.current = 0
    setCursor(0)
    setCentro(0)
    setDatos(null)
    setGeneral(null)
    setDetalle(null)
    setHistorial(historialVacio())
    setLoopIn(null)
    setSel(null)
    setAbierta(null)
    setConfirmaRep(false)
    setAviso('')
    const pend = pendientesDe.current.get(id) || []
    pendientesDe.current.delete(id)
    setFallas(pend.map((texto) => ({ id: ++fallaSeq.current, texto, reconciliada: true })))
    setFallasTotal(pend.length)
    setEstado({ fase: 'leido' })
    setAjuste(leerAjuste(storage(), id))
    getMarcas(id)
      .then((r) => {
        if (!vivo) return
        if (r.ok && r.data && r.data.track) { listaServidor.current = r.data.marcas || []; setDatos(r.data) }
        else setDatos({ id, error: r.ok ? (r.data?.motivo || 'El servidor no devolvió el tema.') : motivoDe(r) })
      })
      .catch(() => vivo && setDatos({ id, error: CONEXION }))
    // El tema entero (vista general, duración real del audio y la grilla).
    getOnda3(id, undefined, undefined, 1000)
      .then((r) => {
        if (!vivo) return
        if (r.ok && r.data && r.data.bandas) {
          setGeneral({ ...r.data, id })
          // Snap: prendido solo con una grilla que el motor da por buena.
          const e = estadoGrilla(r.data.grilla)
          setSnap(e.hay && !e.atenuada)
        } else setGeneral({ id, error: motivoDe(r) })
      })
      .catch(() => vivo && setGeneral({ id, error: CONEXION }))
    return () => { vivo = false }
  }, [id])

  // Al cerrar: el audio no queda sonando y lo que esté en vuelo ya no es de esta pantalla.
  useEffect(() => {
    cerrado.current = false
    const a = audioRef.current          // el <audio> es el mismo toda la vida del editor
    return () => { cerrado.current = true; epoca.current += 1; a?.pause() }
  }, [])

  // El ajuste de la grilla se guarda en este navegador, por tema. Recién pasado de tema, el
  // ajuste y la grilla que hay son del anterior: no se guardan bajo la clave del nuevo.
  // Devuelve si quedó guardado: sin localStorage (navegación privada, bloqueado) el ajuste vale
  // solo mientras el editor está abierto, y se dice (auditoría f54, M-2).
  const cambiarAjuste = (nuevo) => {
    if (!temaVigente(id, idRef.current, track)) return false
    setAjuste(nuevo)
    const ok = guardarAjuste(storage(), id, nuevo)
    setNoGuarda(!ok)
    return ok
  }

  /* ---------- tamaño de la onda ---------- */
  useLayoutEffect(() => {
    const el = ondaRef.current
    if (!el) return undefined
    const medir = () => setAnchoPx(Math.max(1, Math.round(el.clientWidth)))
    medir()
    if (typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(medir)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  /* ---------- el tramo a la vista: se pide cuando lo que hay no alcanza ---------- */
  useEffect(() => {
    if (!id || !(durAudio > 0) || !(anchoPx > 0)) return undefined
    if (alcanza(detalle, v, anchoPx, durAudio)) return undefined
    let vivo = true
    const t = setTimeout(() => {
      const p = tramoPedido(v, durAudio, anchoPx)
      if (!(p.hasta > p.desde)) return
      getOnda3(id, p.desde, p.hasta, p.puntos)
        .then((r) => { if (vivo && idRef.current === id && r.ok && r.data && r.data.bandas) setDetalle({ ...r.data, id }) })
        .catch(() => {})
    }, 60)
    return () => { vivo = false; clearTimeout(t) }
  }, [id, durAudio, anchoPx, v.desde, v.hasta, detalle]) // eslint-disable-line react-hooks/exhaustive-deps

  /* ---------- dibujar las 3 bandas ---------- */
  // `extra`: { fondos: [{desde, hasta, color}] } — lo que va DETRÁS de la onda (el relleno de
  // cada loop, uno por grupo de idénticos, y el tramo del loop a medio marcar) — y `soloGraves`.
  const pintar = useCallback((cv, d, vista, extra = {}) => {
    if (!cv) return
    const w = cv.clientWidth
    const h = cv.clientHeight
    if (!(w > 0) || !(h > 0)) return
    // En PÍXELES FÍSICOS y sin transform: con un DPR fraccionario (Windows al 125 / 150 %, o
    // el zoom del navegador) una columna de 1 px CSS cae entre píxeles físicos y el navegador
    // la mezcla con la de al lado: el kick se veía rayado (auditoría f54). Una columna por
    // píxel físico y alturas enteras: cada píxel es un color puro de una banda.
    const dpr = window.devicePixelRatio || 1
    cv.width = Math.round(w * dpr)
    cv.height = Math.round(h * dpr)
    const W = cv.width
    const H = cv.height
    const ctx = cv.getContext('2d')
    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.clearRect(0, 0, W, H)
    if (!d || d.error || !d.bandas) return
    const css = getComputedStyle(document.documentElement)
    const col = (n) => css.getPropertyValue(n).trim()
    // DETRÁS: los rellenos (el loop tiñe el fondo y no tapa los kicks; uno por grupo).
    const X = (t) => Math.round(((t - vista.desde) / (vista.hasta - vista.desde || 1)) * W)
    for (const f of extra.fondos || []) {
      const a = Math.max(0, X(f.desde))
      const b = Math.min(W, X(f.hasta))
      if (b > a) { ctx.fillStyle = col(f.color); ctx.fillRect(a, 0, b - a, H) }
    }
    // La onda: de atrás para adelante, cada banda con SU altura (crítica de f54, §2.3): graves
    // afuera, medios y agudos adentro. Sin transparencias entre bandas.
    const cols = columnas(d, vista, W)
    const alto = H - 2
    for (const banda of extra.soloGraves ? ['graves'] : ['graves', 'medios', 'agudos']) {
      ctx.fillStyle = col(`--edd-${banda}`)
      for (let c = 0; c < cols.length; c++) {
        const x = cols[c]
        if (!x) continue
        const hh = Math.round(alturaBanda(x[banda], banda) * alto)
        if (hh >= 1) ctx.fillRect(c, Math.floor((H - hh) / 2), 1, hh)
      }
    }
  }, [])

  const dDetalle = detalle && detalle.id === id ? detalle : null
  /* ---------- cursor (cabezal) ---------- */
  const vRef = useRef(v)
  vRef.current = v
  const pintarCursor = useCallback((t) => {
    const vv = vRef.current
    const w = vv.hasta - vv.desde
    const p = w > 0 ? ((t - vv.desde) / w) * 100 : 0
    if (cabezalRef.current) {
      cabezalRef.current.style.left = `${p}%`
      cabezalRef.current.style.display = p < 0 || p > 100 ? 'none' : ''
    }
    if (cabezalGenRef.current && dur > 0) cabezalGenRef.current.style.left = `${(t / dur) * 100}%`
    if (relojRef.current) relojRef.current.textContent = fmtTiempo(t)
  }, [dur])
  useLayoutEffect(() => { pintarCursor(cursorRef.current) })

  // La vista sigue al cursor por páginas: cuando el cabezal se va del cuadro, la ventana salta
  // (sin animación, también con prefers-reduced-motion).
  const seguir = useCallback((t) => {
    const vv = vRef.current
    const w = vv.hasta - vv.desde
    if (t < vv.desde || t > vv.hasta - w * 0.02) setCentro(t + w * 0.4)
  }, [])

  useEffect(() => {
    if (!sonando) return undefined
    let raf = 0
    let ultimo = 0
    const tick = (ahora) => {
      const a = audioRef.current
      if (a) {
        cursorRef.current = a.currentTime
        pintarCursor(a.currentTime)
        seguir(a.currentTime)
        if (ahora - ultimo > 250) { ultimo = ahora; setCursor(a.currentTime) }
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [sonando, pintarCursor, seguir])

  const posActual = () => {
    const a = audioRef.current
    return a && !a.paused ? a.currentTime : cursorRef.current
  }

  const irA = (t, { centrar = false } = {}) => {
    const x = ms3(acotar(t, dur))
    cursorRef.current = x
    setCursor(x)
    const a = audioRef.current
    if (a && a.readyState >= 1) { try { a.currentTime = x } catch { /* sin metadata */ } }
    if (centrar) setCentro(x)
    else seguir(x)
    pintarCursor(x)
  }

  const alternar = () => {
    const a = audioRef.current
    if (!a || !track) return
    if (!a.paused) { a.pause(); return }
    if (a.readyState >= 1 && Math.abs(a.currentTime - cursorRef.current) > 0.0005) {
      try { a.currentTime = cursorRef.current } catch { /* sin metadata */ }
    }
    a.play().catch((e) => {
      if (e && e.name === 'AbortError') return
      setSonando(false)
      radioAudioMotivo(id).then((m) => setAviso(m || `No pude reproducir «${track.titulo}»: el navegador no pudo abrir ese audio.`))
    })
  }
  const alArrancar = () => {
    const a = audioRef.current
    document.querySelectorAll('audio,video').forEach((m) => { if (m !== a && !m.paused) m.pause() })
    setSonando(true)
  }
  const alParar = () => {
    const a = audioRef.current
    if (a) { cursorRef.current = a.currentTime; pintarCursor(a.currentTime); setCursor(a.currentTime) }
    setSonando(false)
  }

  /* ---------- escrituras ---------- */
  // REGLAS (auditorías de f54)
  // - La lista que se dibuja es SIEMPRE una que mandó el servidor (respuesta de una escritura
  //   o relectura). `seq` es el orden en que se PIDIÓ: una respuesta vieja no pisa a una nueva;
  //   si la respuesta de una escritura llega tarde y no se aplica, se relee (la de la escritura
  //   puede ser más nueva que la relectura que la tapó).
  // - Cada pedido nace con el tema abierto y su ÉPOCA (`epoca` sube al cambiar de tema). Una
  //   respuesta que vuelve con otro tema abierto no toca nada del tema nuevo: ni la lista, ni
  //   el historial, ni los avisos, ni el estado. Cada paso del historial lleva su `tid`.
  // - Deshacer / rehacer releen la lista del servidor ANTES de mandar nada y saltean lo que ya
  //   está hecho (`yaHecha`): un paso que quedó a medias, o cuyo pedido llegó pero cuya
  //   respuesta se perdió, se puede reintentar sin duplicar marcas.
  const vigente = (tid, ep) => ep === epoca.current && tid === idRef.current

  const ponerLista = (tid, nuevas, seq) => {
    if (tid !== idRef.current || seq < lista.current.aplicada) return false
    lista.current.aplicada = seq
    listaServidor.current = nuevas
    setDatos((d) => (d && d.track && d.track.id === tid ? { ...d, marcas: nuevas } : d))
    return true
  }

  // Vuelve a leer las marcas del servidor. Si sale bien, la pantalla muestra lo que de verdad
  // quedó guardado y las fallas anteriores quedan reconciliadas. Devuelve la lista, o null.
  const releer = async (tid) => {
    const ep = epoca.current
    const hasta = fallaSeq.current
    const seq = ++lista.current.seq
    setReleyendo((n) => n + 1)
    let r = null
    try { r = await getMarcas(tid) } catch { r = null }
    if (!vigente(tid, ep)) return null
    setReleyendo((n) => Math.max(0, n - 1))
    if (!(r && r.ok && Array.isArray(r.data?.marcas))) return null
    ponerLista(tid, r.data.marcas, seq)
    setFallas((f) => f.map((x) => (x.id <= hasta ? { ...x, reconciliada: true } : x)))
    return r.data.marcas
  }

  // Una escritura. Devuelve { ok, data, tid } o { ok: false, tipo, tid } con `tipo`:
  // 'rechazo' | 'reintentable' (clasificarFalla) | 'ajeno' (volvió con otro tema abierto;
  // `servidor` dice si el servidor la hizo igual, en el tema de origen).
  const conEstado = async (que, hacer) => {
    const tid = idRef.current
    const ep = epoca.current
    const seq = ++lista.current.seq
    vuelo.current.n += 1
    setEstado({ fase: 'guardando' })
    let r = null
    try { r = await hacer(tid) } catch { r = null }
    const ok = Boolean(r && r.ok && Array.isArray(r.data?.marcas))
    if (!vigente(tid, ep)) {
      if (cerrado.current) {
        // El editor ya se cerró: si falló, que la playlist lo diga (nadie más se va a enterar).
        if (!ok) tardia.current?.(`${que}: no se guardó. ${r ? motivoDe(r) : CONEXION}`)
      } else if (tid === idRef.current) {
        // Se fue y se volvió al MISMO tema (A → B → A): no se toca el historial de esta visita,
        // pero la lista sí tiene que mostrar lo que el server ya tiene.
        releer(tid)
      }
      return { ok: false, tipo: 'ajeno', servidor: ok, data: ok ? r.data : null, tid, ep }
    }
    vuelo.current.n -= 1
    if (ok) {
      if (!ponerLista(tid, r.data.marcas, seq)) releer(tid)
      if (vuelo.current.n === 0) setEstado({ fase: 'guardado', que, hora: new Date() })
      return { ok: true, data: r.data, tid, ep }
    }
    // El aviso queda (un éxito posterior no lo borra) y se relee la lista del servidor: lo
    // que se ve vuelve a ser lo guardado, no lo que se creía guardado.
    const tipo = clasificarFalla(r ? r.status : null)
    const fid = ++fallaSeq.current
    const extra = tipo === 'reintentable' && r && r.status === 409 ? ' Es pasajero: reintentá en un rato.' : ''
    setFallas((f) => [...f, { id: fid, texto: `${que}: no se guardó. ${r ? motivoDe(r) : CONEXION}${extra}`, reconciliada: false }].slice(-5))
    setFallasTotal((n) => n + 1)
    await releer(tid)
    if (vigente(tid, ep) && vuelo.current.n === 0) setEstado({ fase: 'leido' })
    return { ok: false, tipo, tid, ep }
  }

  // Algo quedó a medias en un tema que ya no está abierto: se avisa acá nombrando ese tema,
  // queda anotado para cuando se vuelva a él, y si el editor se cerró, la playlist lo dice
  // (3.ª auditoría f54: no puede quedar a medias en silencio).
  const avisarAMedias = (origen, texto) => {
    pendientesDe.current.set(origen.tid, [...(pendientesDe.current.get(origen.tid) || []), texto])
    if (cerrado.current) tardia.current?.(texto)
    else if (idRef.current !== origen.tid) setAviso(texto)
    else {
      // Se fue y se volvió al tema de origen (A → B → A): va directo a sus avisos.
      pendientesDe.current.delete(origen.tid)
      setFallas((f) => [...f, { id: ++fallaSeq.current, texto, reconciliada: false }].slice(-5))
      setFallasTotal((n) => n + 1)
      releer(origen.tid)
    }
  }

  // Lo que falta de un paso del historial cuando se pasó de tema en el medio (Ctrl+Z y AvPág
  // seguidos, o un grupo cortado a la mitad): se termina contra el tema de ORIGEN, sin tocar la
  // pantalla del nuevo (no pasa por conEstado). Como deshacer, primero relee y saltea lo que
  // ya está hecho (yaHecha): no duplica. Si algo falla, se avisa como «a medias».
  const terminarEnOrigen = async (origen, resto, cambios, que) => {
    const leer = async () => {
      try { const g = await getMarcas(origen.tid); return g && g.ok && Array.isArray(g.data?.marcas) ? g.data.marcas : null } catch { return null }
    }
    let real = await leer()
    if (!real) {
      avisarAMedias(origen, `«${origen.titulo}»: ${que} quedó sin terminar al pasar de tema (no pude leer el servidor). Revisá sus marcas.`)
      return
    }
    const cc = [...cambios]
    for (const o of resto) {
      let x = o
      for (const [a, b] of cc) x = opConId(x, a, b)
      const y = yaHecha(x, real)
      if (y.hecha) {
        if (x.tipo === 'crear' && y.id !== x.marca.id) cc.push([x.marca.id, y.id])
        continue
      }
      let r = null
      try {
        r = x.tipo === 'crear' ? await crearMarca(origen.tid, cuerpoCrear(x.marca))
          : x.tipo === 'borrar' ? await borrarMarca(origen.tid, x.marca.id)
            : await cambiarMarca(origen.tid, x.id, x.despues)
      } catch { r = null }
      if (!(r && r.ok)) {
        avisarAMedias(origen, `«${origen.titulo}»: ${que} quedó a medias al pasar de tema (${r ? motivoDe(r) : CONEXION}). Revisá sus marcas.`)
        return
      }
      if (x.tipo === 'crear' && r.data?.marca) cc.push([x.marca.id, r.data.marca.id])
      if (Array.isArray(r.data?.marcas)) real = r.data.marcas
    }
    if (idRef.current === origen.tid && !cerrado.current) releer(origen.tid)
  }

  // Un paso del historial contra el servidor, sin duplicar nada (ver REGLAS).
  // { ok, cambios: [[id viejo, id nuevo]] } o { ok: false, tipo, cambios, parcial }.
  const aplicarPaso = async (op, que) => {
    const tid = idRef.current
    const ep = epoca.current
    const origen = { tid, titulo: tema ? tema.titulo : 'otro tema' }
    // Defensa en profundidad: el historial se vacía al cambiar de tema y `hecho` solo registra
    // en el tema vigente, así que esto no debería pasar nunca.
    if (op.tid !== undefined && op.tid !== tid) return { ok: false, tipo: 'ajeno', cambios: [] }
    const ops = op.tipo === 'grupo' ? op.ops : [op]
    let real = await releer(tid)
    // Se pasó de tema mientras se releía (Ctrl+Z y AvPág seguidos): el Ctrl+Z vale para SU tema.
    if (!vigente(tid, ep)) { terminarEnOrigen(origen, ops, [], que); return { ok: false, tipo: 'ajeno', cambios: [] } }
    if (!real) return { ok: false, tipo: 'reintentable', cambios: [], sinLectura: true }
    const cambios = []
    let hechas = 0
    const hechasOps = []                           // lo que quedó aplicado, tal como se aplicó
    for (const [i, o] of ops.entries()) {
      let x = o
      for (const [a, b] of cambios) x = opConId(x, a, b)
      const y = yaHecha(x, real)
      if (y.hecha) {
        if (x.tipo === 'crear' && y.id !== x.marca.id) cambios.push([x.marca.id, y.id])
        hechas += 1
        hechasOps.push(x)
        continue
      }
      const r = x.tipo === 'crear' ? await conEstado(que, (t) => crearMarca(t, cuerpoCrear(x.marca)))
        : x.tipo === 'borrar' ? await conEstado(que, (t) => borrarMarca(t, x.marca.id))
          : await conEstado(que, (t) => cambiarMarca(t, x.id, x.despues))
      if (!r.ok) {
        // Se pasó a otro tema con el grupo a medias y el servidor SÍ hizo esta parte: lo que
        // falta se termina contra el tema de ORIGEN, sin tocar la pantalla del tema nuevo (si no,
        // un Ctrl+Z de un cambio de tipo dejaba las dos marcas en el tema que se dejó).
        if (r.tipo === 'ajeno' && r.servidor && i < ops.length - 1) {
          const cc = x.tipo === 'crear' && r.data ? [...cambios, [x.marca.id, r.data.marca.id]] : cambios
          terminarEnOrigen(origen, ops.slice(i + 1), cc, que)
        } else if (r.tipo === 'ajeno' && !r.servidor && hechas > 0) {
          avisarAMedias(origen, `«${origen.titulo}»: ${que} quedó a medias al pasar de tema (una parte no se guardó). Revisá sus marcas.`)
        }
        return { ok: false, tipo: r.tipo, cambios, parcial: hechas > 0, hechas: hechasOps }
      }
      if (x.tipo === 'crear') cambios.push([x.marca.id, r.data.marca.id])
      real = r.data.marcas
      hechas += 1
      hechasOps.push(x)
    }
    return { ok: true, cambios }
  }

  // Registra un paso confirmado, SOLO si sigue abierto el tema en el que nació. Defensa en
  // profundidad: `conEstado` ya devuelve 'ajeno' (y nadie llama a `hecho`) si el tema cambió;
  // esta guarda queda por si una llamada nueva se olvida de mirar el resultado.
  const hecho = (op, r) => {
    if (!vigente(r.tid, r.ep)) return
    setHistorial((h) => registrar(h, { ...op, tid: r.tid }))
  }

  const crear = async (cuerpo, que) => {
    if (!track) return null
    const r = await conEstado(que, (tid) => crearMarca(tid, cuerpo))
    if (!r.ok) return null
    // `iguales`: cuántas idénticas hay con ella (dos memory en el mismo tiempo son legítimas).
    hecho({ tipo: 'crear', marca: r.data.marca, iguales: contarIguales(r.data.marca, r.data.marcas) }, r)
    return r.data
  }
  const cambiar = async (m, nuevos, que) => {
    const { antes, despues } = diferencia(m, { ...m, ...nuevos })
    if (!Object.keys(despues).length) return null
    const r = await conEstado(que, (tid) => cambiarMarca(tid, m.id, despues))
    if (!r.ok) return null
    hecho({ tipo: 'cambiar', id: m.id, antes, despues }, r)
    return r.data
  }
  const borrar = async (m) => {
    const iguales = contarIguales(m, marcas)
    const r = await conEstado(`Borrar ${nombreD(m)}`, (tid) => borrarMarca(tid, m.id))
    if (!r.ok) return null
    hecho({ tipo: 'borrar', marca: m, iguales }, r)
    setAviso(`Borraste ${nombreD(m)}. Ctrl+Z lo vuelve a poner.`)
    return r.data
  }
  // Cambiar el tipo: se crea la marca nueva y RECIÉN después se borra la vieja (si la nueva
  // no se pudo crear, no se perdió nada). Un solo paso para deshacer. Si mientras tanto se
  // cambió de tema, se termina en el tema de ORIGEN (borrar la vieja, para no dejar las dos) y
  // no se registra nada en el tema nuevo.
  const cambiarTipo = async (m, tipo) => {
    if (tipo === m.tipo) return
    const cuerpo = { tipo, inicio: m.inicio }
    if (m.nombre) cuerpo.nombre = m.nombre
    if (tipo === 'loop') {
      if (!periodo) { setAviso('Sin BPM medido no sé cuánto dura un loop: marcalo con Loop in (I) y Loop out (O).'); return }
      const fin = ms3(m.inicio + loopBeats * periodo)
      const err = errorLoop(m.inicio, fin, dur)
      if (err) { setAviso(err); return }
      cuerpo.fin = fin
    }
    if (tipo === 'cue' && padLibre(marcas) === null) { setAviso('Los 8 pads están ocupados: liberá uno para pasar esta marca a hot cue.'); return }
    const que = `${nombreD(m)} → ${NOMBRE_TIPO[tipo]}`
    const igualesVieja = contarIguales(m, marcas)
    const origen = { tid: idRef.current, titulo: tema ? tema.titulo : 'otro tema' }
    // Quedaron las dos (la nueva y la vieja) en un tema que ya no está abierto: se avisa acá
    // nombrando ese tema, queda anotado para cuando se vuelva a él, y si el editor se cerró, la
    // playlist lo dice (3.ª auditoría f54: no puede quedar a medias en silencio).
    const aMedias = () => avisarAMedias(origen, `«${origen.titulo}»: ${que} quedó a medias (no se pudo borrar la marca vieja): quedaron las dos. Borrá la que sobra.`)
    const r1 = await conEstado(que, (tid) => crearMarca(tid, cuerpo))
    if (!r1.ok) {
      // Se creó la nueva con otro tema abierto: se termina en el tema de ORIGEN (borrar la vieja).
      if (r1.tipo === 'ajeno' && r1.servidor) {
        borrarMarca(r1.tid, m.id).then((x) => { if (!(x && x.ok)) aMedias() }).catch(aMedias)
      }
      return
    }
    const r2 = await conEstado(que, (tid) => borrarMarca(tid, m.id))
    const nueva = { tipo: 'crear', marca: r1.data.marca, iguales: contarIguales(r1.data.marca, r1.data.marcas) }
    if (r2.ok) hecho({ tipo: 'grupo', ops: [nueva, { tipo: 'borrar', marca: m, iguales: igualesVieja }] }, r2)
    else if (r2.tipo !== 'ajeno') hecho(nueva, r1)
    else if (!r2.servidor) aMedias()
  }

  const correrHistorial = async (dir) => {
    // Un Ctrl+Z / Ctrl+Y apretado mientras otro viaja no se pierde: espera su turno (lo corre
    // el efecto de abajo, con el historial ya actualizado).
    // Recién pasado de tema (sin sus datos todavía), el historial que hay es del anterior: no se
    // aplica nada, y mientras el tema nuevo carga, se dice por qué Ctrl+Z no hace nada.
    if (!temaVigente(id, idRef.current, track)) {
      if (id === idRef.current && !track) setAviso('Todavía estoy leyendo el tema: Ctrl+Z y Ctrl+Y no hacen nada hasta que cargue.')
      return
    }
    // Detrás de una creación en vuelo (C y Ctrl+Z enseguida, auditoría f54 I-3): espera a que se
    // registre, así deshace ESA creación y no la anterior (y no se pierde el futuro).
    if (ocupado.current || creaciones.current.n > 0) { cola.current.push(dir); return }
    const paso = dir < 0 ? deshacer(historial) : rehacer(historial)
    if (!paso) { setAviso(dir < 0 ? 'No hay nada para deshacer.' : 'No hay nada para rehacer.'); return }
    // Defensa en profundidad (ver `aplicarPaso`): un paso de otro tema no se aplica nunca acá.
    if (paso.aplicar.tid !== undefined && paso.aplicar.tid !== idRef.current) {
      setAviso('Ese paso es de otro tema: no lo apliqué acá.')
      return
    }
    // El paso, tal como está en el historial: al terminar se lo ubica en el historial ACTUAL
    // (`cerrarPaso`), que pudo cambiar mientras viajaba el pedido (otra edición registrada).
    const original = dir < 0 ? historial.pasado[historial.pasado.length - 1] : historial.futuro[0]
    const ep = epoca.current
    ocupado.current = true
    const r = await aplicarPaso(paso.aplicar, dir < 0 ? 'Deshacer' : 'Rehacer')
    if (ep !== epoca.current) return          // el tema cambió: no se toca nada del nuevo
    ocupado.current = false
    if (r.tipo === 'ajeno') return
    const remapear = (h) => r.cambios.reduce((x, [a, b]) => reemplazarId(x, a, b), h)
    const que = dir < 0 ? 'Deshacer' : 'Rehacer'
    const desenlace = r.ok ? 'ok' : r.tipo === 'rechazo' ? 'rechazo' : 'reintentable'
    // Rechazado A MEDIAS (auditoría f54, I-1): lo que sí se aplicó está en el servidor y no puede
    // quedar fuera del historial; va como un paso propio (rescatarHechas) que Ctrl+Z saca.
    const rescate = !r.ok && r.tipo === 'rechazo' && r.parcial && r.hechas && r.hechas.length > 0
    setHistorial((h) => {
      const x = remapear(cerrarPaso(h, original, dir, desenlace))
      return rescate ? rescatarHechas(x, r.hechas, r.cambios, original.tid) : x
    })
    if (r.ok) {
      setAviso(dir < 0 ? 'Deshecho.' : 'Rehecho.')
    } else if (rescate) {
      setAviso(`⚠ ${que} quedó a medias: el servidor rechazó una parte (el motivo está en el aviso), pero otra parte SÍ se guardó. Lo guardado quedó en el historial como un paso aparte: Ctrl+Z lo saca. La lista es la que tiene el servidor.`)
    } else if (r.tipo === 'rechazo') {
      // Sale del historial (cerrarPaso): si no, cada Ctrl+Z repetiría el mismo pedido imposible.
      setAviso(`${que} no se pudo: el servidor lo rechazó (el motivo está en el aviso). Lo saqué del historial; la lista es la que tiene guardada el servidor.`)
    } else {
      // Sin red, 5xx o la base ocupada: el paso queda para reintentar. Si quedó a medias, lo
      // hecho ya está en la base y el próximo intento saltea esa parte (no duplica).
      setAviso(r.sinLectura
        ? `${que}: no pude leer el servidor, así que no mandé nada. Reintentá cuando vuelva.`
        : r.parcial
          ? `${que} quedó a medias: lo que se hizo ya está guardado y la lista es la del servidor. ${dir < 0 ? 'Ctrl+Z' : 'Ctrl+Y'} de nuevo termina lo que falta, sin repetir lo hecho.`
          : `${que} no se pudo (sin red, el servidor falló o la base estaba ocupada). Quedó en el historial: reintentá.`)
    }
  }

  // El siguiente Ctrl+Z / Ctrl+Y en cola, cuando termina el anterior (después del render que
  // aplicó su resultado: así ve el historial actual).
  useEffect(() => {
    if (ocupado.current || creaciones.current.n > 0 || !cola.current.length) return
    correrHistorial(cola.current.shift())
  })

  /* ---------- poner marcas ---------- */
  // «Borrar repetidos» (crítica de f54, P0-9): deja la marca más vieja de cada grupo y borra el
  // resto, después de que el dueño lo confirmó. Un solo paso del historial: Ctrl+Z las vuelve a
  // poner (con iguales, así no se toman por «ya hechas»).
  const borrarRepetidos = async () => {
    setConfirmaRep(false)
    const tid = idRef.current
    const ep = epoca.current
    const aBorrar = repetidas(listaServidor.current).aBorrar
    const ops = []
    let falla = null
    for (const m of aBorrar) {
      const iguales = contarIguales(m, listaServidor.current)
      const r = await conEstado(`Borrar ${nombreD(m)} (repetida)`, (t) => borrarMarca(t, m.id))
      if (!r.ok) { falla = r; break }
      ops.push({ tipo: 'borrar', marca: m, iguales })
    }
    if (ops.length) hecho(ops.length === 1 ? ops[0] : { tipo: 'grupo', ops }, { tid, ep })
    if (!vigente(tid, ep)) return
    // Lo que de verdad pasó (auditoría f54, I-2): si cortó a la mitad, no dice «quedó una de cada».
    const faltan = aBorrar.length - ops.length
    if (!falla) setAviso(`Borré ${ops.length} marca${ops.length === 1 ? '' : 's'} repetida${ops.length === 1 ? '' : 's'}; quedó una de cada. Ctrl+Z las vuelve a poner.`)
    else setAviso(`⚠ Borré ${ops.length} de ${aBorrar.length}; quedan ${faltan} repetida${faltan === 1 ? '' : 's'}. No se guardó el resto (el motivo está en el aviso).${ops.length ? ' Ctrl+Z vuelve a poner las que borré.' : ''}`)
  }

  // Crea y RECIÉN con la respuesta avisa (auditoría f54, M-5: «Hot cue C en…» quedaba aunque el
  // POST fallara). Con otro tema abierto, no toca el aviso del tema nuevo.
  const crearAvisando = async (cuerpo, texto) => {
    const ep = epoca.current
    const d = await crear(cuerpo, texto)
    if (ep === epoca.current) setAviso(d ? texto : `⚠ ${texto}: no se guardó (el motivo está en el aviso).`)
    return d
  }

  const enGrilla = (t) => (snapActivo ? snapGrilla(t, g, ajuste.offset) : t)
  const aqui = () => ms3(acotar(enGrilla(posActual()), dur))

  // Ya hay una marca igual ahí (±50 ms): no se crea otra; se avisa y se selecciona esa (pedido
  // del dueño: apretar C diez veces llenaba los 8 pads con cues en el mismo tiempo). Mira la
  // ÚLTIMA lista del servidor (`listaServidor`), no la del último render.
  const yaHay = (tipo, inicio, fin = null) => {
    const m = marcaCercana(listaServidor.current, tipo, inicio, fin)
    if (!m) return false
    setSel(m.id)
    const que = tipo === 'cue' ? `un hot cue (pad ${letraPad(m.num)})` : tipo === 'memory' ? 'una memory' : 'un loop igual'
    setAviso(`⚠ Ya hay ${que} en ${fmtTiempo(m.inicio)}${tipo === 'loop' ? ` → ${fmtTiempo(m.fin)}` : ''}. No creé otro: mové el cursor para marcar otro.`)
    return true
  }

  // Una creación por vez (crítica de f54, P0-2): la que se aprieta mientras otra viaja espera su
  // turno y se decide con la lista que devolvió la anterior (el pad libre, «ya hay una ahí»). El
  // tiempo es el del momento en que se apretó. Si mientras tanto se cambió de tema, no se crea.
  const enCola = (fn) => {
    const ep = epoca.current
    const q = creaciones.current                    // la cola de ESTE tema (otro tema, otra cola)
    const titulo = tema ? tema.titulo : 'otro tema'
    const correr = () => {
      // Esperaba su turno y mientras tanto se pasó de tema: no se crea en el tema nuevo (el
      // tiempo y los pads eran del anterior), y se dice.
      if (ep !== epoca.current) {
        if (!cerrado.current) setAviso(`«${titulo}»: una marca que esperaba su turno no se creó porque pasaste de tema. Volvé y marcala de nuevo.`)
        return null
      }
      return fn()
    }
    // Sin nada en vuelo arranca YA (en el mismo turno de la tecla: C y AvPág seguidos crean el
    // cue en el tema donde se apretó C); si no, espera a la anterior.
    q.n += 1
    const siguiente = (q.n === 1 ? Promise.resolve(correr()) : q.p.then(correr))
      .catch(() => null)
      .finally(() => {
        q.n -= 1
        // Un Ctrl+Z que esperaba detrás de las creaciones: un render para que lo corra el efecto.
        if (q.n === 0 && q === creaciones.current && cola.current.length) setPulso((x) => x + 1)
      })
    q.p = siguiente
    return siguiente
  }

  const ponerCue = (num) => {
    if (!track) return null
    const t = aqui()
    return enCola(async () => {
      if (yaHay('cue', t)) return null
      const n = num === undefined ? padLibre(listaServidor.current) : num
      if (n === null) { setAviso('⚠ Los 8 pads (A–H) están ocupados: borrá uno o movelo.'); return null }
      return crearAvisando({ tipo: 'cue', inicio: t, num: n }, `Hot cue ${letraPad(n)} en ${fmtTiempo(t)}`)
    })
  }
  const ponerMemory = () => {
    if (!track) return null
    const t = aqui()
    return enCola(async () => {
      if (yaHay('memory', t)) return null
      return crearAvisando({ tipo: 'memory', inicio: t }, `Memory cue en ${fmtTiempo(t)}`)
    })
  }
  const ponerLoopIn = () => {
    if (!track) return
    const t = aqui()
    setLoopIn(t)
    setAviso(`Entrada del loop en ${fmtTiempo(t)}: mové el cursor y apretá O.`)
  }
  const cuerpoLoop = (inicio, fin) => {
    const c = { tipo: 'loop', inicio, fin }
    if (loopPad) {
      const n = padLibre(listaServidor.current)
      if (n === null) return { error: '⚠ Los 8 pads están ocupados: el loop no puede ir a un pad (destildá «Guardar el loop en un pad»).' }
      c.num = n
    }
    return c
  }
  const ponerLoopOut = () => {
    if (!track) return null
    const t = aqui()
    const entrada = loopIn
    const err = errorLoop(entrada, t, dur)
    if (err) {
      setAviso(entrada !== null && !(t > entrada)
        ? `⚠ La salida del loop tiene que quedar después de la entrada (${fmtTiempo(entrada)}): mové el cursor hacia adelante (→ avanza un beat) y apretá O.`
        : `⚠ ${err}`)
      return null
    }
    return enCola(async () => {
      if (yaHay('loop', entrada, t)) { setLoopIn(null); return null }
      const c = cuerpoLoop(entrada, t)
      if (c.error) { setAviso(c.error); return null }
      const d = await crearAvisando(c, `Loop ${fmtTiempo(entrada)} → ${fmtTiempo(t)}`)
      if (d) setLoopIn((x) => (x === entrada ? null : x))
      return d
    })
  }
  const ponerLoopBeats = () => {
    if (!track) return null
    if (!periodo) { setAviso('⚠ Sin BPM medido no hay beats: marcá el loop con Loop in (I) y Loop out (O).'); return null }
    const t = aqui()
    const fin = ms3(t + loopBeats * periodo)
    const err = errorLoop(t, fin, dur)
    if (err) { setAviso(`⚠ ${err}`); return null }
    return enCola(async () => {
      if (yaHay('loop', t, fin)) return null
      const c = cuerpoLoop(t, fin)
      if (c.error) { setAviso(c.error); return null }
      return crearAvisando(c, `Loop de ${loopBeats} beat${loopBeats === 1 ? '' : 's'} en ${fmtTiempo(t)}`)
    })
  }
  const tocarPad = (num, { borrarlo = false } = {}) => {
    const m = marcaDelPad(marcas, num)
    if (borrarlo) {
      if (!m) { setAviso(`El pad ${letraPad(num)} está vacío.`); return }
      borrar(m)
      return
    }
    if (m) { setAviso(''); irA(m.inicio, { centrar: !sonando }); return }
    ponerCue(num)
  }

  // Un loop se mueve entero (mismo largo); un cue o una memory, a donde está el cursor.
  const moverAlCursor = (m) => {
    const t = aqui()
    if (m.tipo !== 'loop') return cambiar(m, { inicio: t }, `Mover ${nombreD(m)} al cursor`)
    const fin = ms3(t + (m.fin - m.inicio))
    const err = errorLoop(t, fin, dur)
    if (err) { setAviso(err); return null }
    return cambiar(m, { inicio: t, fin }, `Mover ${nombreD(m)} al cursor`)
  }

  const moverBeat = (dir) => {
    if (!track) return
    if (periodo) irA(posActual() + dir * periodo)
    else { irA(posActual() + dir); setAviso('Este tema no tiene BPM medido: las flechas mueven de a 1 segundo.') }
  }

  const cambiarZoom = (dir) => {
    const z = Math.min(ZOOM_COMPASES.length - 1, Math.max(0, zoom + dir))
    setZoom(z)
    setCentro(cursorRef.current)
  }

  /* ---------- atajos ---------- */
  const acciones = useRef({})
  acciones.current = {
    play: alternar,
    beat: (a) => moverBeat(a.dir),
    // fino, grilla, medioBeat, fin: recién pasado de tema, cursor, ajuste, grilla y duración
    // son del anterior hasta que llegan los datos del nuevo (`track`).
    fino: (a) => track && irA(posActual() + a.dir * FINO_CURSOR_S),
    pad: (a) => tocarPad(a.num),
    borrarPad: (a) => tocarPad(a.num, { borrarlo: true }),
    cue: () => ponerCue(),
    memory: ponerMemory,
    loopIn: ponerLoopIn,
    loopOut: ponerLoopOut,
    loopBeats: ponerLoopBeats,
    zoom: (a) => cambiarZoom(a.dir),
    grilla: () => track && eg.hay && cambiarAjuste({ ...ajuste, oculta: !ajuste.oculta }),
    medioBeat: () => track && eg.hay && cambiarAjuste({ ...ajuste, offset: moverAjuste(ajuste.offset, g.periodo_s / 2, g.periodo_s) }),
    snap: () => grillaVisible && setSnap((s) => !s),
    soloGraves: () => setSoloGraves((s) => !s),
    // K: el dueño marca un kick y la grilla se dibuja desde ahí con el período medido (solo si el
    // motor no dio grilla: con grilla, se corrige con ½ beat o ±5 ms).
    ancla: () => {
      if (!track) return
      if (estadoGrilla(gMotor).hay) { setAviso('El motor ya estimó una grilla: si está corrida, usá «Ajustar grilla» (½ beat, ±5 ms).'); return }
      const t = ms3(posActual())
      if (!grillaAnclada(gMotor, track.bpm, t)) { setAviso('⚠ Sin BPM medido no hay período: no se puede anclar una grilla.'); return }
      const guardada = cambiarAjuste({ ...ajuste, offset: 0, ancla: t })
      setAviso(guardada
        ? `Grilla anclada en ${fmtTiempo(t)}: ese kick es el 1 y el período es el que midió el motor. K otra vez la mueve; «Sacar el ancla» la quita.`
        : `⚠ Grilla anclada en ${fmtTiempo(t)} solo hasta que cierres esta pantalla: este navegador no deja guardar. Ese kick es el 1 y el período es el que midió el motor.`)
    },
    deshacer: () => correrHistorial(-1),
    rehacer: () => correrHistorial(1),
    inicio: () => irA(0, { centrar: true }),
    fin: () => track && irA(dur, { centrar: true }),
    tema: (a) => irTema(idx + a.dir),
    cerrar: () => cerrar(),
  }

  // Cerrar con cambios sin confirmar (no se pudo releer el servidor) pide una segunda vez, y al
  // cerrar igual el aviso viaja a la playlist (auditoría f54: Esc no puede tirarlo en silencio).
  const cerrar = () => {
    if (sinConfirmar > 0 && !cierre) {
      setCierre(true)
      setAviso(`Hay ${sinConfirmar} cambio${sinConfirmar === 1 ? '' : 's'} sin confirmar: no sé si quedaron guardados. «Volver a leer del servidor» lo averigua; Esc otra vez (o Cerrar) cierra igual.`)
      return
    }
    onClose(sinConfirmar > 0 ? { sinConfirmar, textos: fallas.filter((f) => !f.reconciliada).map((f) => f.texto) } : undefined)
  }
  const irTema = (k) => {
    if (k < 0 || k >= temas.length || k === idx) return
    epoca.current += 1          // ya: una respuesta que llegue antes del render no es del tema nuevo
    audioRef.current?.pause()
    setIdx(k)
  }

  useEffect(() => {
    const onKey = (e) => {
      if (e.defaultPrevented) return
      const root = rootRef.current
      if (!root) return
      // Tab no se escapa de la pantalla completa (Escape siempre cierra).
      if (e.key === 'Tab') {
        const f = [...root.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])')].filter((el) => el.getClientRects().length)
        if (!f.length) return
        const primero = f[0], ultimo = f[f.length - 1]
        if (!root.contains(document.activeElement)) { e.preventDefault(); primero.focus() }
        else if (e.shiftKey && document.activeElement === primero) { e.preventDefault(); ultimo.focus() }
        else if (!e.shiftKey && document.activeElement === ultimo) { e.preventDefault(); primero.focus() }
        return
      }
      if (e.target !== document.body && !root.contains(e.target)) return
      const a = atajoD(e)
      if (!a) return
      if (e.repeat && !['beat', 'fino', 'zoom'].includes(a.tipo)) { e.preventDefault(); return }
      e.preventDefault()
      acciones.current[a.tipo]?.(a)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  // Al abrir: el foco a la onda (los atajos ya andan) y el fondo no se desplaza.
  useEffect(() => {
    const prev = document.activeElement
    const ov = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    ondaRef.current?.focus({ preventScroll: true })
    return () => {
      document.body.style.overflow = ov
      if (prev && prev.isConnected && typeof prev.focus === 'function') prev.focus({ preventScroll: true })
    }
  }, [])

  /* ---------- la onda con el mouse: clic = ir, arrastrar = desplazar, rueda ---------- */
  const tiempoPuntero = (clientX) => {
    const r = ondaRef.current.getBoundingClientRect()
    return tiempoDeXV(clientX - r.left, v, r.width)
  }
  const ondaDown = (e) => {
    if (e.button !== 0 || !track) return
    e.currentTarget.setPointerCapture?.(e.pointerId)
    panRef.current = { x0: e.clientX, c0: (v.desde + v.hasta) / 2, movio: false }
  }
  const ondaMove = (e) => {
    const a = arrastreRef.current
    if (a) {
      const t = ms3(acotar(enGrilla(tiempoPuntero(e.clientX)), dur))
      arrastreRef.current = { ...a, t, movio: a.movio || Math.abs(t - a.t0) > 0.005 }
      setArrastre(arrastreRef.current)
      return
    }
    const p = panRef.current
    if (!p) return
    const dx = e.clientX - p.x0
    if (!p.movio && Math.abs(dx) < 5) return
    p.movio = true
    const r = ondaRef.current.getBoundingClientRect()
    setCentro(p.c0 - (dx / r.width) * (v.hasta - v.desde))
  }
  const ondaUp = (e) => {
    const p = panRef.current
    panRef.current = null
    if (p && !p.movio) irA(tiempoPuntero(e.clientX))
  }
  useEffect(() => {
    const el = ondaRef.current
    if (!el) return undefined
    const rueda = (e) => {
      e.preventDefault()
      if (e.ctrlKey) { acciones.current.zoom({ dir: e.deltaY < 0 ? 1 : -1 }); return }
      const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY
      // Desde el centro de lo que SE VE (el estado puede haber quedado más allá del borde).
      const vv = vRef.current
      setCentro((vv.desde + vv.hasta) / 2 + (d / 600) * (vv.hasta - vv.desde))
    }
    el.addEventListener('wheel', rueda, { passive: false })
    return () => el.removeEventListener('wheel', rueda)
  }, [])

  /* ---------- arrastrar una marca ---------- */
  const empezarArrastre = (e, m, borde) => {
    if (e.button !== 0) return
    e.stopPropagation()
    e.preventDefault()
    ondaRef.current?.setPointerCapture?.(e.pointerId)
    const t0 = borde === 'fin' ? m.fin : m.inicio
    arrastreRef.current = { id: m.id, borde, t: t0, t0, movio: false }
    setArrastre(arrastreRef.current)
  }
  const soltarArrastre = async () => {
    const a = arrastreRef.current
    if (!a) return false
    arrastreRef.current = null
    const m = marcas.find((x) => x.id === a.id)
    if (!m || !a.movio) { setArrastre(null); if (m) irA(a.t0); return true }
    const campo = a.borde === 'fin' ? 'fin' : 'inicio'
    await cambiar(m, { [campo]: a.t }, `Mover ${nombreD(m)} a ${fmtTiempo(a.t)}`)
    setArrastre(null)
    return true
  }
  const posDe = (m, borde) => (arrastre && arrastre.id === m.id && arrastre.borde === borde ? arrastre.t : (borde === 'fin' ? m.fin : m.inicio))

  /* ---------- vista general ---------- */
  const generalPunto = (e) => {
    const r = generalRef.current.getBoundingClientRect()
    const t = acotar(((e.clientX - r.left) / r.width) * dur, dur)
    irA(t, { centrar: true })
  }
  const teclaGeneral = (e) => {
    const paso = v.hasta - v.desde
    if (e.key === 'ArrowLeft') { e.preventDefault(); e.stopPropagation(); setCentro((v.desde + v.hasta) / 2 - paso) }
    else if (e.key === 'ArrowRight') { e.preventDefault(); e.stopPropagation(); setCentro((v.desde + v.hasta) / 2 + paso) }
  }

  /* ---------- lo que se dibuja ---------- */
  const pct = (t) => ((t - v.desde) / (v.hasta - v.desde || 1)) * 100
  const lineas = grillaVisible ? lineasGrilla(g, ajuste.offset, v.desde, v.hasta) : []
  const conCompas = grillaVisible && Number.isFinite(g.compas_ref)
  const visibles = marcas.filter((m) => {
    const fin = m.tipo === 'loop' ? posDe(m, 'fin') : posDe(m, 'inicio')
    return fin >= v.desde && posDe(m, 'inicio') <= v.hasta
  })
  // Idénticas juntas (una bandera, un relleno y un borde por grupo, con «×n»): la base del dueño
  // tiene 32 loops iguales encimados.
  const gruposVis = agruparIguales(visibles)
  const loopsVis = gruposVis.filter((x) => x.marca.tipo === 'loop')
  const compas = compasDe(cursor, grillaVisible ? g : null, ajuste.offset)
  const bpmTxt = track ? fmtBpmMedido(track.bpm) : null
  const desfasada = track && durAudio ? avisoDuracion(durAudio, durEje) : null
  const unidadZoom = eg.hay ? 'compases' : 'segundos'
  const zooms = eg.hay ? ZOOM_COMPASES : ZOOM_SEGUNDOS
  const hora = estado.hora ? estado.hora.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ''
  const sinConfirmar = fallas.filter((f) => !f.reconciliada).length
  // Mientras haya escrituras o relecturas en vuelo, el estado no afirma que la lista sea la
  // del servidor (auditoría f54).
  const pendientes = vuelo.current.n > 0 || releyendo > 0
  useEffect(() => { if (!sinConfirmar) setCierre(false) }, [sinConfirmar])
  // La marca seleccionada («ya hay una ahí») se trae a la vista en la lista, sin mover el foco.
  useEffect(() => {
    if (sel === null) return
    rootRef.current?.querySelector(`.edd-fila[data-id="${sel}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [sel])
  const noSeGuardo = fallasTotal === 1 ? '1 cambio no se guardó (ver el aviso)' : `${fallasTotal} cambios no se guardaron (ver el aviso)`
  const cuenta = (tipo) => marcas.filter((m) => m.tipo === tipo).length
  const resumen = `${cuenta('cue')} hot cue${cuenta('cue') === 1 ? '' : 's'}, ${cuenta('memory')} memory, ${cuenta('loop')} loop${cuenta('loop') === 1 ? '' : 's'}`
  const etiquetaTema = (t) => `${t.artista ? `${t.artista} — ` : ''}${t.titulo}`
  const rep = repetidas(marcas)
  // Cuál queda de cada grupo, para el diálogo de «Borrar repetidos» (auditoría f54, M-1).
  const quedaTxt = (q) => `${nombreD(q)}${q.tipo === 'cue' ? ` en ${fmtTiempo(q.inicio)}` : ''}${q.nombre ? ` «${q.nombre}»` : ''}`
  // Beats de un loop con el período con que se crea (el de la grilla; sin grilla, 60/BPM).
  const beatsDe = (m) => beatsLoop(m.inicio, m.fin, periodo)
  const ordenadas = [...marcas].sort((p, q) => p.inicio - q.inicio || p.id - q.id)

  // Etiquetas de la regla: compases (si hay grilla con el 1) o tiempos. Una cada ~80 px.
  const etiquetas = Math.max(2, Math.min(8, Math.floor(anchoPx / 80)))
  const regla = (() => {
    if (!(v.hasta > v.desde)) return []
    if (conCompas) {
      const unos = lineas.filter((l) => l.beat === 1)
      const paso = Math.max(1, Math.ceil(unos.length / etiquetas))
      return unos.filter((_, i) => i % paso === 0).map((l) => {
        const c = compasDe(l.t + 1e-4, g, ajuste.offset)
        return { t: l.t, txt: c && c.compas ? `${c.compas}` : '' }
      })
    }
    const w = v.hasta - v.desde
    const paso = [0.5, 1, 2, 5, 10, 15, 30].find((p) => w / p <= etiquetas) || 60
    const out = []
    for (let k = Math.ceil(v.desde / paso - 1e-9); k * paso <= v.hasta + 1e-9; k++) out.push({ t: k * paso, txt: fmtTiempo(k * paso, paso < 1 ? 1 : 0) })
    return out
  })()

  // Banderas (crítica de f54, §2.6): dos carriles, prioridad (el loop a medio marcar, después
  // hot cues y hot loops arriba; loops sin pad y memory abajo), idénticas juntas con «×n» y las
  // que no entran sumadas como «+n» a la que las tapa.
  const textoBandera = (m) => (m.tipo === 'pend' ? '⟲ IN · falta OUT (O)'
    : m.tipo === 'cue' ? `${letraPad(m.num)}${m.nombre ? ` · ${m.nombre}` : ''}`
      : m.tipo === 'memory' ? `▼ ${m.nombre || 'M'}`
        : `${letraPad(m.num) ? `${letraPad(m.num)} ` : ''}⟲ ${beatsDe(m) !== null ? `${fmtBeats(beatsDe(m))} beats` : 'Loop'}${m.nombre ? ` · ${m.nombre}` : ''}`)
  const textoCorto = (m) => (m.tipo === 'pend' ? '⟲ IN' : m.tipo === 'cue' ? letraPad(m.num) : m.tipo === 'memory' ? '▼' : `${letraPad(m.num) || ''}⟲`)
  const banderas = (() => {
    const medir = (txt) => medirTexto(txt) + 16
    const items = gruposVis.map(({ marca: m, ids, n }) => {
      const pos = Math.max(0, Math.min(100, pct(posDe(m, 'inicio'))))
      // Un grupo de repetidas (×n) va antes que una suelta del mismo tipo: es una anomalía a la vista.
      const prio = m.id === sel ? -0.5 : (m.tipo === 'cue' || (m.tipo === 'loop' && Number.isInteger(m.num)) ? 0 : m.tipo === 'loop' ? 1 : 2) - (n > 1 ? 0.25 : 0)
      const extraN = n > 1 ? medirTexto(`×${n}`) + 8 : 0
      return { id: m.id, m, ids, n, x: (pos / 100) * anchoPx, der: pos > 60, prioridad: prio, carril: prio <= 0 ? 0 : 1,
        ancho: Math.min(180, Math.max(24, medir(textoBandera(m)) + extraN + 30)), anchoMin: Math.max(24, medir(textoCorto(m)) + extraN + 30) }
    })
    if (loopIn !== null && loopIn >= v.desde && loopIn <= v.hasta) {
      const pos = pct(loopIn)
      const m = { id: 'pend', tipo: 'pend', inicio: loopIn }
      items.push({ id: 'pend', m, ids: [], n: 1, x: (pos / 100) * anchoPx, der: pos > 60, prioridad: -1, carril: 0,
        ancho: Math.min(180, medir(textoBandera(m))), anchoMin: medir(textoCorto(m)) })
    }
    const lugar = filasBanderas(items)
    const porId = Object.fromEntries(items.map((it) => [it.id, it]))
    return items.filter((it) => lugar[it.id] && !lugar[it.id].tapadaPor)
      .map((it) => ({ ...it, ...lugar[it.id], tapadas: lugar[it.id].adentro.map((x) => porId[x]) }))
  })()
  // Golpes de graves del tramo a la vista, SOLO sin grilla (con grilla, la grilla manda). No son
  // kicks: ver `detectarGolpesGraves` (validado contra temas reales: también marcan contratiempos).
  const golpes = !eg.hay && dDetalle && alcanza(dDetalle, v, 1, durAudio) ? detectarGolpesGraves(dDetalle, v.desde, v.hasta) : []

  // Lo que va DETRÁS de la onda en el canvas: un relleno por grupo de loops idénticos y el tramo
  // del loop a medio marcar (de la entrada al cursor).
  const fondos = [
    ...loopsVis.map(({ marca: m }) => ({ desde: posDe(m, 'inicio'), hasta: posDe(m, 'fin'), color: '--edd-loop-fondo' })),
    ...(loopIn !== null && cursor > loopIn ? [{ desde: loopIn, hasta: cursor, color: '--edd-loop-pend' }] : []),
  ]
  const claveFondos = JSON.stringify(fondos)
  useEffect(() => {
    // Mientras llega el tramo con resolución, se dibuja con la vista general (nunca vacío).
    const d = dDetalle && alcanza(dDetalle, v, 1, durAudio) ? dDetalle : general
    pintar(canvasRef.current, d, v, { fondos, soloGraves })
  }, [pintar, dDetalle, general, v.desde, v.hasta, anchoPx, claveFondos, soloGraves]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!general || general.error) { pintar(generalRef.current, null, { desde: 0, hasta: 1 }); return }
    const todo = { desde: 0, hasta: dur || Number(general.duracion_audio) || 1 }
    pintar(generalRef.current, general, todo, {
      fondos: agruparIguales(marcas.filter((m) => m.tipo === 'loop')).map(({ marca: m }) => ({ desde: m.inicio, hasta: m.fin, color: '--edd-loop-fondo' })),
      soloGraves,
    })
  }, [pintar, general, dur, anchoPx, marcas, soloGraves])

  const anclada = Boolean(g && g.anclada)
  const chipGrilla = !general ? null
    : anclada ? { cls: 'is-tuya', txt: 'GRILLA ANCLADA POR VOS', title: `La anclaste en ${fmtTiempo(ajuste.ancla)} con K: el período es el que midió el motor (${coma(60 / g.periodo_s, 1)} BPM) y ese kick cuenta como el 1.` }
      : !eg.hay ? { cls: 'is-sin', txt: 'SIN GRILLA · marcá un kick (K)', title: `Sin grilla: el motor no sabe si el kick está en la línea o a contratiempo. Poné el cursor sobre un kick y apretá K. (${eg.motivo})` }
        : { cls: eg.atenuada ? 'is-baja' : '', txt: `GRILLA ESTIMADA · conf ${coma(g.confianza)}${eg.atenuada ? ' (baja)' : ''}`, title: 'Orientativa: verificala sobre un kick (detalle en «Ajustar grilla»).' }

  const estadoGrillaTxt = !general ? 'Leyendo el audio…'
    : general.error ? ''
      : anclada ? `Grilla anclada por vos en ${fmtTiempo(ajuste.ancla)} (K) con el período que midió el motor · ${ajuste.oculta ? 'oculta' : textoAjuste(ajuste.offset, g.periodo_s)}`
        : !eg.hay ? `Sin grilla de beats: ${eg.motivo}. No se dibuja ninguna (una grilla inventada mentiría).`
          : `Grilla orientativa del motor, verificala sobre un kick · confianza ${coma(g.confianza)}${eg.baja.includes('confianza') ? ' (baja)' : ''} · nitidez ${Number.isFinite(g.concentracion) ? coma(g.concentracion) : '?'}${eg.baja.includes('nitidez') ? ' (baja)' : ''}${eg.atenuada ? ': se dibuja atenuada y puede estar corrida' : ''} · ${ajuste.oculta ? 'oculta' : textoAjuste(ajuste.offset, g.periodo_s)}`

  const marcaAria = (m) => `${NOMBRE_TIPO[m.tipo]} ${rotuloMarca(m)}${m.nombre ? ` «${m.nombre}»` : ''} · ${fmtTiempo(m.inicio)}${m.tipo === 'loop' ? ` → ${fmtTiempo(m.fin)}` : ''}`

  return (
    <div className="edd-fondo">
      <div className="edd" ref={rootRef} role="dialog" aria-modal="true" aria-labelledby="edd-titulo" aria-describedby="edd-ayuda">
        <audio ref={audioRef} preload="metadata" onPlay={alArrancar} onPause={alParar} onEnded={alParar}
          onError={() => setSonando(false)}
          onLoadedMetadata={(e) => { if (cursorRef.current > 0) { try { e.currentTarget.currentTime = cursorRef.current } catch { /* nada */ } } }} />

        {/* ---- cabecera, en una línea ---- */}
        <header className="edd-cab">
          <div className="edd-cab-t">
            <span className="eyebrow">Editor de cues y loops · tema {idx + 1} de {temas.length}</span>
            <h2 id="edd-titulo" className="edd-h" title={tema ? etiquetaTema(tema) : ''}>{tema ? tema.titulo : '—'}{tema && tema.artista && <span className="edd-artista"> · {tema.artista}</span>}</h2>
          </div>
          <div className="edd-chips">
            <span className="edd-chip mono" title={bpmTxt ? 'BPM medido por el motor' : 'El motor no midió el BPM de este tema'}>
              {bpmTxt ? `${bpmTxt} BPM` : '? BPM'}
            </span>
            <span className="edd-chip mono" title={track && track.key_dudosa ? 'Key dudosa: los tramos del tema no coinciden' : 'Key medida por el motor (Camelot · clásica)'}>
              {track && track.camelot ? `${track.camelot}${track.tonalidad ? ` · ${track.tonalidad}` : ''}` : '? key'}
              {track && track.camelot && track.key_dudosa && <span className="edd-duda" aria-label="key dudosa"> ?</span>}
            </span>
            <span className="edd-chip mono edd-chip-dur">{track ? fmtTiempo(track.dur, 0) : '—'}</span>
          </div>
          <nav className="edd-nav" aria-label="Temas de la playlist">
            <button type="button" className="btn btn-secondary edd-btn edd-ico" onClick={() => irTema(idx - 1)} disabled={idx === 0} aria-keyshortcuts="PageUp" aria-label="Tema anterior (RePág)" title="Tema anterior (RePág)">‹<span className="edd-ico-txt"> Anterior</span></button>
            <label className="edd-sel">
              <span className="sr-only">Tema de la playlist</span>
              <select className="input" value={idx} onChange={(e) => irTema(Number(e.target.value))}>
                {temas.map((t, k) => <option key={t.id} value={k}>{k + 1}. {etiquetaTema(t)}</option>)}
              </select>
            </label>
            <button type="button" className="btn btn-secondary edd-btn edd-ico" onClick={() => irTema(idx + 1)} disabled={idx === temas.length - 1} aria-keyshortcuts="PageDown" aria-label="Tema siguiente (AvPág)" title="Tema siguiente (AvPág)"><span className="edd-ico-txt">Siguiente </span>›</button>
            <button type="button" className="btn btn-secondary edd-btn edd-ico edd-cerrar" onClick={() => cerrar()} aria-keyshortcuts="Escape" aria-label="Cerrar el editor (Esc)" title="Cerrar (Esc)">✕<span className="edd-ico-txt"> Cerrar</span></button>
          </nav>
        </header>

        {datos && datos.error && (
          <div className="alert alert-warn" role="alert"><div><div className="alert-title">No pude abrir este tema</div><p>{datos.error}</p></div></div>
        )}

        {/* ---- la onda grande ---- */}
        <section className="edd-panel edd-ondabox" aria-label="Onda de 3 bandas">
          <div className="edd-regla mono" aria-hidden="true">
            {regla.map((r) => <span key={r.t} className={pct(r.t) > 90 ? 'is-fin' : ''} style={{ left: `${pct(r.t)}%` }}>{r.txt}</span>)}
          </div>
          <div className="edd-flags" aria-hidden="true">
            {banderas.map(({ id: bid, m, n, der, ancho, anchoMin, fila, corta, tapadas }) => (
              <span key={bid} data-id={m.tipo === 'pend' ? undefined : m.id} data-fila={fila} data-n={n}
                className={`edd-flag ${m.tipo === 'pend' ? 'cue-loop' : claseMarca(m)} is-${m.tipo}${der ? ' is-der' : ''}${m.id === sel ? ' is-sel' : ''}`}
                style={{ left: `${Math.max(0, Math.min(100, pct(posDe(m, 'inicio'))))}%`, top: `${fila * 25}px`, maxWidth: `${corta ? anchoMin : ancho}px` }}
                title={m.tipo === 'pend' ? 'Entrada del loop marcada: falta la salida (O)'
                  : `${marcaAria(m)}${n > 1 ? ` · ${n} iguales encimadas` : ''}${tapadas.length ? ` · y ${tapadas.length} más acá: ${tapadas.map((x) => textoBandera(x.m)).join(', ')}` : ''} (arrastrá para moverla)`}
                onPointerDown={(e) => { if (m.tipo !== 'pend') empezarArrastre(e, m, 'inicio') }}>
                {corta ? textoCorto(m) : textoBandera(m)}
                {n > 1 && <b className="edd-flag-n">×{n}</b>}
                {tapadas.length > 0 && <span className="edd-flag-mas">+{tapadas.length}</span>}
              </span>
            ))}
          </div>
          <div className={`edd-onda${grillaVisible && eg.atenuada ? ' is-atenuada' : ''}`} ref={ondaRef} role="slider" tabIndex={0}
            aria-label={`Posición en ${track ? track.titulo : 'el tema'}`}
            aria-valuemin={0} aria-valuemax={Math.round(dur)} aria-valuenow={Math.round(cursor)}
            aria-valuetext={`${fmtTiempo(cursor, 1) ?? '0:00.0'} de ${fmtTiempo(dur, 1) ?? '—'}${compas ? (compas.compas ? `, compás ${compas.compas} beat ${compas.beat}` : `, beat ${compas.beat} antes del primer compás`) : ''}`}
            data-desde={v.desde.toFixed(3)} data-hasta={v.hasta.toFixed(3)}
            data-tramo={dDetalle && alcanza(dDetalle, v, 1, durAudio) ? 'detalle' : 'general'}
            onPointerDown={ondaDown} onPointerMove={ondaMove}
            onPointerUp={async (e) => { if (!(await soltarArrastre())) ondaUp(e) }}
            onPointerCancel={() => { arrastreRef.current = null; setArrastre(null); panRef.current = null }}>
            {/* DETRÁS de la onda: las líneas de tiempo (las de la regla) y, sin grilla, los golpes de
                graves detectados (marquitas arriba y abajo, atenuadas: no son beats). */}
            <div className="edd-grilla edd-fondo-lineas" aria-hidden="true">
              {!conCompas && regla.map((r) => <i key={`t${r.t}`} className="edd-tick" data-t={r.t} style={{ left: `${pct(r.t)}%` }} />)}
              {golpes.map((t) => <i key={`k${t}`} className="edd-golpe" data-t={t} style={{ left: `${pct(t)}%` }} />)}
            </div>
            <canvas ref={canvasRef} className="edd-canvas" aria-hidden="true" />
            <div className="edd-grilla" aria-hidden="true">
              {lineas.map((l) => (
                <i key={l.t} className={`edd-beat${l.beat === 1 ? ' is-compas' : ''}`} data-t={l.t} style={{ left: `${pct(l.t)}%` }} />
              ))}
            </div>
            {/* Un borde por grupo de loops idénticos (el relleno va en el canvas, detrás). */}
            {loopsVis.map(({ marca: m, n }) => (
              <div key={m.id} className={`edd-mk-loop${m.id === sel ? ' is-sel' : ''}`} data-id={m.id} data-n={n} aria-hidden="true"
                style={{ left: `${pct(posDe(m, 'inicio'))}%`, width: `${pct(posDe(m, 'fin')) - pct(posDe(m, 'inicio'))}%` }}>
                <span className="edd-loop-fin" title="Salida del loop (arrastrá para moverla)" onPointerDown={(e) => empezarArrastre(e, m, 'fin')} />
              </div>
            ))}
            {gruposVis.filter(({ marca: m }) => m.tipo !== 'loop').map(({ marca: m }) => (
              <i key={m.id} className={`edd-mk ${claseMarca(m)} is-${m.tipo}`} data-id={m.id} aria-hidden="true" style={{ left: `${pct(posDe(m, 'inicio'))}%` }} />
            ))}
            {loopIn !== null && loopIn >= v.desde && loopIn <= v.hasta && <i className="edd-loopin" style={{ left: `${pct(loopIn)}%` }} aria-hidden="true" />}
            <i className="edd-cabezal" ref={cabezalRef} aria-hidden="true" />
            {!general && <span className="edd-msg">Leyendo el audio…</span>}
            {general && general.error && <span className="edd-msg is-err">{general.error}</span>}
          </div>

          {/* ---- una línea: leyenda corta, la grilla, Solo graves, K, y el ajuste plegado ---- */}
          <div className="edd-ondaestado">
            <span className="edd-leyenda" title="Bandas relativas dentro del tema, no volumen absoluto. La línea de beat marca el ataque del kick; la joroba azul llega unos ms después.">
              <span className="edd-ley"><i className="edd-sw is-graves" aria-hidden="true" />Graves</span>
              <span className="edd-ley"><i className="edd-sw is-medios" aria-hidden="true" />Medios</span>
              <span className="edd-ley"><i className="edd-sw is-agudos" aria-hidden="true" />Agudos</span>
              {golpes.length > 0 && <span className="edd-ley"><i className="edd-sw is-golpe" aria-hidden="true" />golpes de graves (detectados, no son beats)</span>}
            </span>
            {chipGrilla && <span className={`edd-grilla-chip ${chipGrilla.cls}`} title={chipGrilla.title}>{chipGrilla.txt}</span>}
            <button type="button" className="btn btn-secondary edd-btn edd-solog" aria-pressed={soloGraves} aria-keyshortcuts="B"
              onClick={() => acciones.current.soloGraves()}>Solo graves <Kbd>B</Kbd></button>
            {!estadoGrilla(gMotor).hay && (
              <button type="button" className="btn btn-secondary edd-btn edd-ancla" aria-keyshortcuts="K" onClick={() => acciones.current.ancla()}
                title="Poné el cursor sobre un kick y apretá K: la grilla se dibuja desde ahí con el período que midió el motor">Marcar un kick <Kbd>K</Kbd></button>
            )}
            <details className="edd-grilla-ctrl">
              <summary className="btn btn-secondary edd-btn">Ajustar grilla</summary>
              <p className={`edd-grilla-estado${eg.hay ? (eg.atenuada ? ' is-baja' : '') : ' is-sin'}`} role="status">
                {eg.hay && !anclada && <><span className="edd-estimada">estimada</span>{' '}</>}{estadoGrillaTxt}
              </p>
              {eg.hay && (
                <div className="edd-grilla-btns">
                  <button type="button" className="btn btn-secondary edd-btn" aria-pressed={ajuste.oculta} aria-keyshortcuts="G" disabled={!track}
                    onClick={() => acciones.current.grilla()}>{ajuste.oculta ? 'Mostrar grilla' : 'Ocultar grilla'} <Kbd>G</Kbd></button>
                  <button type="button" className="btn btn-secondary edd-btn edd-medio" aria-keyshortcuts="H" disabled={!track || ajuste.oculta}
                    onClick={() => cambiarAjuste({ ...ajuste, offset: moverAjuste(ajuste.offset, g.periodo_s / 2, g.periodo_s) })}>Correr ½ beat <Kbd>H</Kbd></button>
                  <button type="button" className="btn btn-secondary edd-btn" disabled={!track || ajuste.oculta} aria-label="−5 ms: correr la grilla 5 milisegundos para atrás"
                    onClick={() => cambiarAjuste({ ...ajuste, offset: moverAjuste(ajuste.offset, -AJUSTE_FINO_S, g.periodo_s) })}>−5 ms</button>
                  <button type="button" className="btn btn-secondary edd-btn" disabled={!track || ajuste.oculta} aria-label="+5 ms: correr la grilla 5 milisegundos para adelante"
                    onClick={() => cambiarAjuste({ ...ajuste, offset: moverAjuste(ajuste.offset, AJUSTE_FINO_S, g.periodo_s) })}>+5 ms</button>
                  <button type="button" className="btn btn-secondary edd-btn" disabled={!track || !ajuste.offset}
                    onClick={() => cambiarAjuste({ ...ajuste, offset: 0 })}>Volver a la del motor</button>
                  {anclada && <button type="button" className="btn btn-secondary edd-btn edd-sacar-ancla" disabled={!track} onClick={() => { const { ancla, ...resto } = ajuste; void ancla; cambiarAjuste(resto) }}>Sacar el ancla</button>}
                  <label className={`edd-snap${grillaVisible ? '' : ' is-off'}`}>
                    <input type="checkbox" checked={snapActivo} disabled={!grillaVisible} onChange={() => setSnap((s) => !s)} aria-keyshortcuts="Q" />
                    Pegar a la grilla <Kbd>Q</Kbd>
                  </label>
                </div>
              )}
            </details>
          </div>
        </section>

        {/* ---- transporte y marcar, a la vista ---- */}
        <div className="edd-trans">
          <button type="button" className="btn btn-secondary edd-btn edd-play" onClick={alternar} aria-keyshortcuts="Space" aria-disabled={!track || undefined}>
            {sonando ? <IconPause size={14} /> : <IconPlayFill size={14} />}{sonando ? 'Pausar' : 'Reproducir'} <Kbd>Espacio</Kbd>
          </button>
          <button type="button" className="btn btn-secondary edd-btn" onClick={() => moverBeat(-1)} aria-keyshortcuts="ArrowLeft">−1 {periodo ? 'beat' : 's'} <Kbd>←</Kbd></button>
          <button type="button" className="btn btn-secondary edd-btn" onClick={() => moverBeat(1)} aria-keyshortcuts="ArrowRight">+1 {periodo ? 'beat' : 's'} <Kbd>→</Kbd></button>
          <button type="button" className="btn btn-secondary edd-btn" onClick={() => irA(posActual() - FINO_CURSOR_S)} aria-keyshortcuts="Shift+ArrowLeft" aria-label="−10 ms: correr el cursor 10 milisegundos para atrás">−10 ms <Kbd>Mayús ←</Kbd></button>
          <button type="button" className="btn btn-secondary edd-btn" onClick={() => irA(posActual() + FINO_CURSOR_S)} aria-keyshortcuts="Shift+ArrowRight" aria-label="+10 ms: correr el cursor 10 milisegundos para adelante">+10 ms <Kbd>Mayús →</Kbd></button>
          <span className="edd-reloj mono" ref={relojRef} aria-hidden="true">0:00.000</span>
          {compas && <span className="edd-compas mono">{compas.compas ? `compás ${compas.compas}.${compas.beat}` : `beat ${compas.beat} (antes del 1)`} <small>({anclada ? 'anclado' : 'estimado'})</small></span>}
          <span className="edd-flex" />
          <div className="edd-zoom" role="group" aria-label={`Zoom: ${unidadZoom} a la vista`}>
            <button type="button" className="chip edd-zbtn" onClick={() => cambiarZoom(1)} disabled={zoom === 0} aria-label="Acercar" aria-keyshortcuts="+">+</button>
            {zooms.map((z, k) => (
              <button key={z} type="button" className={`chip edd-zbtn${k === zoom ? ' is-on' : ''}`} aria-pressed={k === zoom}
                onClick={() => { setZoom(k); setCentro(cursorRef.current) }}>{z}</button>
            ))}
            <button type="button" className="chip edd-zbtn" onClick={() => cambiarZoom(-1)} disabled={zoom === zooms.length - 1} aria-label="Alejar" aria-keyshortcuts="-">−</button>
            <span className="edd-zoom-u">{unidadZoom}</span>
          </div>
        </div>
        {/* Marcar + el loop a medio marcar + el aviso: en el celular (≤ 640 px) quedan pegados abajo
            de la pantalla, a la vista sin scroll (auditoría f54, M-3); en la compu, en su lugar. */}
        <div className="edd-dock">
        <div className="edd-marcar" role="group" aria-label="Marcar">
          <button type="button" className="btn btn-primary edd-btn" onClick={() => ponerCue()} aria-keyshortcuts="C" aria-disabled={!track || undefined} title="Hot cue en el primer pad libre">Hot cue <Kbd>C</Kbd></button>
          <button type="button" className="btn btn-secondary edd-btn" onClick={ponerMemory} aria-keyshortcuts="M" aria-disabled={!track || undefined}>Memory cue <Kbd>M</Kbd></button>
          <button type="button" className={`btn ${loopIn !== null ? 'btn-secondary' : 'btn-secondary'} edd-btn`} onClick={ponerLoopIn} aria-keyshortcuts="I" aria-disabled={!track || undefined}>Loop in <Kbd>I</Kbd></button>
          <button type="button" className={`btn ${loopIn !== null ? 'btn-primary' : 'btn-secondary'} edd-btn edd-loopout`} onClick={ponerLoopOut} aria-keyshortcuts="O"
            aria-disabled={!track || loopIn === null || undefined} title={loopIn === null ? 'Primero Loop in (I)' : `Cerrar el loop que empieza en ${fmtTiempo(loopIn)}`}>Loop out <Kbd>O</Kbd></button>
          <button type="button" className="btn btn-secondary edd-btn edd-loopb" onClick={ponerLoopBeats} aria-keyshortcuts="L" aria-disabled={!track || !periodo || undefined}>
            Loop {loopBeats} beat{loopBeats === 1 ? '' : 's'} <Kbd>L</Kbd>
          </button>
          <label className="edd-loopn-l">
            <span className="sr-only">Largo del loop en beats</span>
            <select className="input edd-loopn" value={loopBeats} onChange={(e) => setLoopBeats(Number(e.target.value))}>
              {LOOP_BEATS.map((b) => <option key={b} value={b}>{b} beat{b === 1 ? '' : 's'}</option>)}
            </select>
          </label>
          <button type="button" className="btn btn-secondary edd-btn edd-ico" onClick={() => correrHistorial(-1)} disabled={!historial.pasado.length} aria-keyshortcuts="Control+Z" aria-label="Deshacer (Ctrl Z)" title="Deshacer (Ctrl Z)">↶<span className="edd-ico-txt"> Deshacer</span></button>
          <button type="button" className="btn btn-secondary edd-btn edd-ico" onClick={() => correrHistorial(1)} disabled={!historial.futuro.length} aria-keyshortcuts="Control+Y" aria-label="Rehacer (Ctrl Y)" title="Rehacer (Ctrl Y)">↷<span className="edd-ico-txt"> Rehacer</span></button>
        </div>

        {/* El loop a medio marcar, el aviso de lo que pasó y el guardado: pegados a la onda. */}
        {loopIn !== null && (
          <p className="edd-loop-pend" role="status">
            <span>Loop: entrada en <span className="mono">{fmtTiempo(loopIn)}</span> · falta la salida: mové el cursor y apretá «Loop out» <Kbd>O</Kbd></span>
            <button type="button" className="btn btn-secondary edd-btn" onClick={() => setLoopIn(null)}>Cancelar el loop</button>
          </p>
        )}
        <div className="edd-avisos">
          <p className={`edd-nota edd-aviso${aviso.startsWith('⚠') ? ' is-warn' : ''}`} role="status" aria-live="polite">{aviso}</p>
          {/* «✓ Guardado» solo con la lista igual a la del servidor y sin fallas sin avisar:
              una falla no reconciliada (no se pudo releer) manda sobre cualquier éxito. */}
          <p className={`edd-estado is-${sinConfirmar ? 'desincronizado' : estado.fase}`} role="status" aria-live="polite">
            {sinConfirmar > 0 && (releyendo > 0
              ? `⚠ ${sinConfirmar} cambio${sinConfirmar === 1 ? '' : 's'} sin confirmar: releyendo el servidor…`
              : `⚠ ${sinConfirmar} cambio${sinConfirmar === 1 ? '' : 's'} sin confirmar y no pude volver a leer el servidor: la lista puede no ser la que quedó guardada.`)}
            {!sinConfirmar && (pendientes || estado.fase === 'guardando') && <><span className="spinner" aria-hidden="true" /> {releyendo ? 'Releyendo el servidor…' : 'Guardando…'}</>}
            {!sinConfirmar && !pendientes && estado.fase === 'guardado' && !fallasTotal && <>✓ Guardado en la biblioteca {hora && <span className="mono">{hora}</span>} · {resumen}</>}
            {!sinConfirmar && !pendientes && estado.fase === 'guardado' && fallasTotal > 0 && `Lista igual a la del servidor · ${resumen} · ${noSeGuardo}`}
            {!sinConfirmar && !pendientes && estado.fase === 'leido' && track && `${marcas.length ? resumen : 'Sin marcas todavía'} · ${fallasTotal ? `lista releída del servidor · ${noSeGuardo}` : 'se guardan solas en la biblioteca'}`}
          </p>
        </div>
        </div>
        {rep.aBorrar.length > 0 && (
          <div className="alert alert-warn edd-repetidos" role="region" aria-label="Marcas repetidas">
            <div>
              <p className="edd-rep-txt">⚠ Hay {rep.grupos.map((x) => `${x.sobran.length} ${PLURAL[x.tipo][x.sobran.length === 1 ? 0 : 1]} ${REPETIDO[x.tipo]}${x.sobran.length === 1 ? '' : 's'} en ${fmtTiempo(x.inicio)}`).join(', ')}.</p>
              {!confirmaRep
                ? <button type="button" className="btn btn-secondary edd-btn edd-borrar-rep" onClick={() => setConfirmaRep(true)}>Borrar repetidos…</button>
                : (
                  <span className="edd-rep-conf">
                    <span>Voy a borrar {rep.aBorrar.length} marca{rep.aBorrar.length === 1 ? '' : 's'}. De cada grupo queda <span className="edd-rep-queda">{rep.grupos.map((x) => quedaTxt(x.queda)).join(' · ')}</span> (la que tiene nombre o pad; si no, la más vieja). Ctrl+Z las vuelve a poner.</span>
                    <button type="button" className="btn btn-secondary edd-btn edd-rep-si" onClick={() => borrarRepetidos()}>Sí, borrar {rep.aBorrar.length}</button>
                    <button type="button" className="btn btn-secondary edd-btn edd-rep-no" onClick={() => setConfirmaRep(false)}>No</button>
                  </span>
                )}
            </div>
          </div>
        )}
        {fallasTotal > 0 && (
          <div className="alert alert-warn edd-falla" role="alert">
            <div>
              <div className="alert-title">No se guardó</div>
              <ul className="edd-fallas">{fallas.map((f) => <li key={f.id}>{f.texto}</li>)}</ul>
              {fallasTotal > fallas.length && <p className="edd-nota">Y {fallasTotal - fallas.length} aviso{fallasTotal - fallas.length === 1 ? '' : 's'} más de antes (se muestran los últimos {fallas.length}).</p>}
              {sinConfirmar > 0
                ? <button type="button" className="btn btn-secondary edd-btn edd-releer" onClick={() => releer(id)} disabled={releyendo > 0}>Volver a leer del servidor</button>
                : <button type="button" className="btn btn-secondary edd-btn edd-descartar" onClick={() => { setFallas([]); setFallasTotal(0) }}>Entendido: descartar el aviso</button>}
            </div>
          </div>
        )}

        {/* ---- vista general ---- */}
        <section className="edd-panel edd-general" aria-label="Vista general del tema">
          <div className="edd-gen" role="slider" tabIndex={0} aria-label="Parte del tema que muestra la onda grande (clic o arrastre para ir a otra parte; ← → de a una pantalla)"
            title="Vista general. El recuadro es lo que muestra la onda grande: clic o arrastre para ir a otra parte (con el foco acá, ← → pasan de a una pantalla)."
            aria-valuemin={0} aria-valuemax={Math.round(dur)} aria-valuenow={Math.round((v.desde + v.hasta) / 2)}
            aria-valuetext={`de ${fmtTiempo(v.desde, 1)} a ${fmtTiempo(v.hasta, 1)}`}
            onKeyDown={teclaGeneral}
            onPointerDown={(e) => { if (e.button === 0 && dur > 0) { e.currentTarget.setPointerCapture?.(e.pointerId); generalPunto(e) } }}
            onPointerMove={(e) => { if (e.buttons === 1 && dur > 0) generalPunto(e) }}>
            <canvas ref={generalRef} className="edd-canvas" aria-hidden="true" />
            {dur > 0 && agruparIguales(marcas).map(({ marca: m }) => (
              <i key={m.id} className={`edd-gmk ${claseMarca(m)} is-${m.tipo}`} aria-hidden="true"
                style={m.tipo === 'loop' ? { left: `${(m.inicio / dur) * 100}%`, width: `${((m.fin - m.inicio) / dur) * 100}%` } : { left: `${(m.inicio / dur) * 100}%` }} />
            ))}
            {dur > 0 && <i className="edd-vista" aria-hidden="true" style={{ left: `${(v.desde / dur) * 100}%`, width: `${((v.hasta - v.desde) / dur) * 100}%` }} />}
            <i className="edd-cabezal is-gen" ref={cabezalGenRef} aria-hidden="true" />
          </div>
          {desfasada && <p className="edd-nota edd-warn" role="note">{desfasada}</p>}
        </section>

        {/* ---- pads y lista ---- */}
        <div className="edd-abajo">
          <section className="edd-pads-box" aria-labelledby="edd-pads-h">
            <h3 className="edd-sub" id="edd-pads-h">Hot cues</h3>
            <p className="edd-nota edd-pads-nota">Vacío: lo fija. Lleno: salta. Mayús + clic o Mayús + número: lo borra. Teclas <Kbd>1</Kbd>…<Kbd>8</Kbd>.</p>
            <div className="edd-pads">
              {PADS.split('').map((letra, n) => {
                const m = marcaDelPad(marcas, n)
                const loop = m && m.tipo === 'loop'
                const etiqueta = !m ? `Pad ${letra} vacío: fijar un hot cue en el cursor`
                  : loop ? `Pad ${letra}: loop de ${fmtTiempo(m.inicio)} a ${fmtTiempo(m.fin)}${m.nombre ? `, ${m.nombre}` : ''}. Ir al loop`
                    : `Pad ${letra}: hot cue en ${fmtTiempo(m.inicio)}${m.nombre ? `, ${m.nombre}` : ', sin nombre'}. Ir al cue`
                return (
                  <button key={letra} type="button" data-pad={n} aria-keyshortcuts={String(n + 1)}
                    className={`edd-pad ${m ? `is-lleno ${claseMarca(m)}` : `is-vacio cue-c${n + 1}`}${loop ? ' is-loop' : ''}${m && m.id === sel ? ' is-sel' : ''}`}
                    aria-label={etiqueta} title={etiqueta}
                    onClick={(e) => tocarPad(n, { borrarlo: e.shiftKey })}>
                    <span className={`edd-pad-n ${m ? claseMarca(m) : `cue-c${n + 1}`}`} aria-hidden="true">{letra}</span>
                    <span className="edd-pad-t">
                      {!m && <span className="edd-pad-vacio">Vacío</span>}
                      {m && <span className="edd-pad-nom">{loop ? `⟲ ${beatsDe(m) !== null ? `${fmtBeats(beatsDe(m))} beats` : 'Loop'}` : (m.nombre || <span className="edd-sin-nombre">Sin nombre</span>)}</span>}
                      {m && <span className="edd-pad-tiempo mono">{fmtTiempo(m.inicio)}{loop ? ` → ${fmtTiempo(m.fin)}` : ''}</span>}
                    </span>
                    <Kbd>{n + 1}</Kbd>
                  </button>
                )
              })}
            </div>
            <label className="edd-snap">
              <input type="checkbox" checked={loopPad} onChange={(e) => setLoopPad(e.target.checked)} />
              Guardar el loop en un pad (hot loop)
            </label>
          </section>

          <section className="edd-panel edd-lista" aria-labelledby="edd-lista-h">
            <h3 className="edd-sub" id="edd-lista-h">Marcas, en orden de tiempo ({marcas.length})</h3>
            {track && marcas.length === 0 && <p className="edd-nota">Sin marcas. Reproducí y tocá un pad, C, M, L o I / O.</p>}
            <ul className="edd-filas">
              {ordenadas.map((m) => {
                const abiertaEsta = abierta === m.id
                return (
                  <li key={m.id} className={`edd-fila${m.fuera_del_track ? ' is-fuera' : ''}${m.id === sel ? ' is-sel' : ''}${abiertaEsta ? ' is-abierta' : ''}`}
                    data-id={m.id} data-tipo={m.tipo} data-nombre={m.nombre || ''} aria-current={m.id === sel ? 'true' : undefined}>
                    <div className="edd-fila-c">
                      <button type="button" className={`edd-tag ${claseMarca(m)} is-${m.tipo}`} onClick={() => irA(m.inicio, { centrar: true })}
                        aria-label={`Ir a ${nombreD(m)}`} title={`Ir a ${nombreD(m)}`}>{rotuloMarca(m)}</button>
                      <span className="edd-fila-t mono">{fmtTiempo(m.inicio)}{m.tipo === 'loop' ? ` → ${fmtTiempo(m.fin)}` : ''}</span>
                      <span className={`edd-fila-n${m.nombre ? '' : ' edd-sin-nombre'}`}>{m.nombre || 'sin nombre'}</span>
                      <button type="button" className="btn btn-secondary edd-btn edd-editar" aria-expanded={abiertaEsta}
                        aria-label={`Editar ${nombreD(m)}`} onClick={() => setAbierta(abiertaEsta ? null : m.id)}>{abiertaEsta ? 'Listo' : 'Editar'}</button>
                      <button type="button" className="btn btn-secondary edd-btn edd-borrar" onClick={() => borrar(m)} aria-label={`Borrar ${nombreD(m)}`}>Borrar</button>
                    </div>
                    {abiertaEsta && (
                      <div className="edd-fila-ed">
                        <label className="edd-campo edd-campo-tipo">
                          <span className="edd-campo-l">Tipo</span>
                          <select className="input edd-tipo" value={m.tipo} aria-label={`Tipo de ${nombreD(m)}`} onChange={(e) => cambiarTipo(m, e.target.value)}>
                            <option value="cue">Hot cue</option>
                            <option value="memory">Memory</option>
                            <option value="loop">Loop</option>
                          </select>
                        </label>
                        {m.tipo !== 'memory' && (
                          <label className="edd-campo edd-campo-pad">
                            <span className="edd-campo-l">Pad</span>
                            <select className="input edd-pad-sel" value={Number.isInteger(m.num) ? m.num : ''} aria-label={`Pad de ${nombreD(m)}`}
                              onChange={(e) => cambiar(m, { num: e.target.value === '' ? null : Number(e.target.value) }, `Pad de ${nombreD(m)}`)}>
                              {m.tipo === 'loop' && <option value="">sin pad</option>}
                              {PADS.split('').map((l, n) => {
                                const ocupa = marcaDelPad(marcas, n)
                                return <option key={l} value={n} disabled={Boolean(ocupa && ocupa.id !== m.id)}>{l}{ocupa && ocupa.id !== m.id ? ' (ocupado)' : ''}</option>
                              })}
                            </select>
                          </label>
                        )}
                        <div className="edd-campo edd-tiempos">
                          <span className="edd-campo-l">{m.tipo === 'loop' ? 'Entrada → salida' : 'Tiempo'}</span>
                          <CampoTiempo valor={m.inicio} etiqueta={`Tiempo de ${m.tipo === 'loop' ? 'entrada del ' : ''}${nombreD(m)}`}
                            onCommit={(t) => cambiar(m, { inicio: t }, `Mover ${nombreD(m)} a ${fmtTiempo(t)}`)} />
                          {m.tipo === 'loop' && (
                            <CampoTiempo valor={m.fin} etiqueta={`Tiempo de salida del ${nombreD(m)}`}
                              onCommit={(t) => cambiar(m, { fin: t }, `Mover la salida del loop a ${fmtTiempo(t)}`)} />
                          )}
                        </div>
                        <label className="edd-campo edd-campo-nom">
                          <span className="edd-campo-l">Nombre</span>
                          <CampoNombre valor={m.nombre} max={max} etiqueta={`Nombre de ${nombreD(m)}`}
                            onCommit={(n) => cambiar(m, { nombre: n }, `Nombre de ${nombreD(m)}`)} />
                        </label>
                        <button type="button" className="btn btn-secondary edd-btn edd-alcursor" onClick={() => moverAlCursor(m)}
                          aria-label={`Mover ${nombreD(m)} acá (al cursor)`}>Mover acá</button>
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          </section>
        </div>

        <p className="edd-nota edd-ayuda" id="edd-ayuda">
          Atajos con el foco en el editor: <Kbd>Espacio</Kbd> reproduce · <Kbd>←</Kbd> <Kbd>→</Kbd> ±1 beat (Mayús: ±10 ms) · <Kbd>1</Kbd>…<Kbd>8</Kbd> pads ·
          {' '}<Kbd>C</Kbd> <Kbd>M</Kbd> <Kbd>I</Kbd> <Kbd>O</Kbd> <Kbd>L</Kbd> marcas · <Kbd>K</Kbd> marcar un kick · <Kbd>B</Kbd> solo graves · <Kbd>+</Kbd> <Kbd>−</Kbd> zoom · <Kbd>G</Kbd> grilla · <Kbd>H</Kbd> ½ beat · <Kbd>Q</Kbd> pegar a la grilla ·
          {' '}<Kbd>Ctrl Z</Kbd> deshacer · <Kbd>RePág</Kbd> <Kbd>AvPág</Kbd> tema · <Kbd>Inicio</Kbd> <Kbd>Fin</Kbd> · <Kbd>Esc</Kbd> cierra.
          {' '}{noGuarda ? 'Este navegador no deja guardar: el ajuste y el ancla de la grilla duran hasta que cierres esta pantalla.' : 'El ajuste y el ancla de la grilla quedan guardados solo en este navegador.'} Loops siempre en naranja; memory: ▼ con borde y sin relleno.
        </p>
      </div>
    </div>
  )
}
