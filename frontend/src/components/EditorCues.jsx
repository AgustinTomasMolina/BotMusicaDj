import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { getMarcas, crearMarca, cambiarMarca, borrarMarca, getOnda3, radioAudioUrl, radioAudioMotivo } from '../api'
import { fmtTiempo, parseTiempo, padLibre, marcaDelPad, errorLoop, beatsDeLoop, claseMarca, acotar, avisoDuracion } from '../cues'
import {
  ZOOM_COMPASES, ZOOM_SEGUNDOS, ZOOM_DEFECTO, AJUSTE_FINO_S, PADS, LOOP_BEATS,
  estadoGrilla, periodoBeat, lineasGrilla, snapGrilla, moverAjuste, textoAjuste, leerAjuste, guardarAjuste,
  anchoZoom, ventana, tiempoDeXV, tramoPedido, alcanza, columnas,
  historialVacio, registrar, deshacer, rehacer, reemplazarId, opConId, cuerpoCrear, diferencia, clasificarFalla, yaHecha,
  atajoD, fmtBpmMedido, letraPad, rotuloMarca, compasDe, nombreD, descartarPaso,
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
const coma = (x, d = 2) => Number(x).toFixed(d).replace('.', ',')

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
export default function EditorCues({ temas, inicial, onClose }) {
  const [idx, setIdx] = useState(() => Math.max(0, temas.findIndex((t) => t.analisis?.radio_id === inicial)))
  const tema = temas[idx] || null
  const id = tema ? tema.analisis.radio_id : null

  const [datos, setDatos] = useState(null)          // {track, marcas, limites} | {error}
  const [general, setGeneral] = useState(null)      // /onda3 del tema entero | {error}
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

  const player = usePlayer()
  useVolumenEn(audioRef, player.volume, player.muted)

  const track = datos && datos.track && datos.track.id === id ? datos.track : null
  const marcas = useMemo(() => (track ? datos.marcas : []), [track, datos])
  const durEje = track ? Number(track.dur) || 0 : 0
  const durAudio = general && !general.error ? Number(general.duracion_audio) || 0 : 0
  const dur = durEje || durAudio
  const g = general && !general.error ? general.grilla : null
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
    setReleyendo(0)
    setFallasTotal(0)
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
    setAviso('')
    setFallas([])
    setEstado({ fase: 'leido' })
    setAjuste(leerAjuste(storage(), id))
    getMarcas(id)
      .then((r) => {
        if (!vivo) return
        if (r.ok && r.data && r.data.track) setDatos(r.data)
        else setDatos({ id, error: r.ok ? (r.data?.motivo || 'El servidor no devolvió el tema.') : motivoDe(r) })
      })
      .catch(() => vivo && setDatos({ id, error: CONEXION }))
    // El tema entero (vista general, duración real del audio y la grilla).
    getOnda3(id, undefined, undefined, 1000)
      .then((r) => {
        if (!vivo) return
        if (r.ok && r.data && r.data.bandas) {
          setGeneral(r.data)
          // Snap: prendido solo con una grilla que el motor da por buena.
          const e = estadoGrilla(r.data.grilla)
          setSnap(e.hay && !e.atenuada)
        } else setGeneral({ error: motivoDe(r) })
      })
      .catch(() => vivo && setGeneral({ error: CONEXION }))
    return () => { vivo = false }
  }, [id])

  useEffect(() => () => { audioRef.current?.pause() }, [])

  // El ajuste de la grilla se guarda en este navegador, por tema.
  const cambiarAjuste = (nuevo) => {
    setAjuste(nuevo)
    guardarAjuste(storage(), id, nuevo)
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
  const pintar = useCallback((cv, d, vista) => {
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
    const cA = col('--edd-agudos'), cM = col('--edd-medios'), cG = col('--edd-graves')
    const cols = columnas(d, vista, W)
    const alto = H - 2
    for (let c = 0; c < cols.length; c++) {
      const x = cols[c]
      if (!x) continue
      // Anidadas: afuera lo más alto de las tres (agudos), adentro los graves. El kick se ve
      // como una barra de graves alta en cada beat.
      const hA = Math.round(Math.max(x.agudos, x.medios, x.graves) * alto)
      const hM = Math.round(Math.max(x.medios, x.graves) * alto)
      const hG = Math.round(x.graves * alto)
      for (const [hh, color] of [[hA, cA], [hM, cM], [hG, cG]]) {
        if (hh < 1) continue
        ctx.fillStyle = color
        ctx.fillRect(c, Math.floor((H - hh) / 2), 1, hh)
      }
    }
  }, [])

  const dDetalle = detalle && detalle.id === id ? detalle : null
  useEffect(() => {
    // Mientras llega el tramo con resolución, se dibuja con la vista general (nunca vacío).
    const d = dDetalle && alcanza(dDetalle, v, 1, durAudio) ? dDetalle : general
    pintar(canvasRef.current, d, v)
  }, [pintar, dDetalle, general, v.desde, v.hasta, anchoPx]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!general || general.error) { pintar(generalRef.current, null, { desde: 0, hasta: 1 }); return }
    pintar(generalRef.current, general, { desde: 0, hasta: dur || Number(general.duracion_audio) || 1 })
  }, [pintar, general, dur, anchoPx])

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
    if (!vigente(tid, ep)) return { ok: false, tipo: 'ajeno', servidor: ok, tid, ep }
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

  // Un paso del historial contra el servidor, sin duplicar nada (ver REGLAS).
  // { ok, cambios: [[id viejo, id nuevo]] } o { ok: false, tipo, cambios, parcial }.
  const aplicarPaso = async (op, que) => {
    const tid = idRef.current
    const ep = epoca.current
    if (op.tid !== undefined && op.tid !== tid) return { ok: false, tipo: 'ajeno', cambios: [] }
    let real = await releer(tid)
    if (!vigente(tid, ep)) return { ok: false, tipo: 'ajeno', cambios: [] }
    if (!real) return { ok: false, tipo: 'reintentable', cambios: [], sinLectura: true }
    const cambios = []
    let hechas = 0
    for (const o of op.tipo === 'grupo' ? op.ops : [op]) {
      let x = o
      for (const [a, b] of cambios) x = opConId(x, a, b)
      const y = yaHecha(x, real)
      if (y.hecha) {
        if (x.tipo === 'crear' && y.id !== x.marca.id) cambios.push([x.marca.id, y.id])
        hechas += 1
        continue
      }
      const r = x.tipo === 'crear' ? await conEstado(que, (t) => crearMarca(t, cuerpoCrear(x.marca)))
        : x.tipo === 'borrar' ? await conEstado(que, (t) => borrarMarca(t, x.marca.id))
          : await conEstado(que, (t) => cambiarMarca(t, x.id, x.despues))
      if (!r.ok) return { ok: false, tipo: r.tipo, cambios, parcial: hechas > 0 }
      if (x.tipo === 'crear') cambios.push([x.marca.id, r.data.marca.id])
      real = r.data.marcas
      hechas += 1
    }
    return { ok: true, cambios }
  }

  // Registra un paso confirmado, SOLO si sigue abierto el tema en el que nació.
  const hecho = (op, r) => {
    if (!vigente(r.tid, r.ep)) return
    setHistorial((h) => registrar(h, { ...op, tid: r.tid }))
  }

  const crear = async (cuerpo, que) => {
    if (!track) return null
    const r = await conEstado(que, (tid) => crearMarca(tid, cuerpo))
    if (!r.ok) return null
    hecho({ tipo: 'crear', marca: r.data.marca }, r)
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
    const r = await conEstado(`Borrar ${nombreD(m)}`, (tid) => borrarMarca(tid, m.id))
    if (!r.ok) return null
    hecho({ tipo: 'borrar', marca: m }, r)
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
    const r1 = await conEstado(que, (tid) => crearMarca(tid, cuerpo))
    if (!r1.ok) {
      if (r1.tipo === 'ajeno' && r1.servidor) borrarMarca(r1.tid, m.id).catch(() => {})
      return
    }
    const r2 = await conEstado(que, (tid) => borrarMarca(tid, m.id))
    if (r2.ok) hecho({ tipo: 'grupo', ops: [{ tipo: 'crear', marca: r1.data.marca }, { tipo: 'borrar', marca: m }] }, r2)
    else if (r2.tipo !== 'ajeno') hecho({ tipo: 'crear', marca: r1.data.marca }, r1)
  }

  const correrHistorial = async (dir) => {
    if (ocupado.current) return
    const paso = dir < 0 ? deshacer(historial) : rehacer(historial)
    if (!paso) { setAviso(dir < 0 ? 'No hay nada para deshacer.' : 'No hay nada para rehacer.'); return }
    if (paso.aplicar.tid !== undefined && paso.aplicar.tid !== idRef.current) {
      setAviso('Ese paso es de otro tema: no lo apliqué acá.')
      return
    }
    const ep = epoca.current
    ocupado.current = true
    const r = await aplicarPaso(paso.aplicar, dir < 0 ? 'Deshacer' : 'Rehacer')
    if (ep !== epoca.current) return          // el tema cambió: no se toca nada del nuevo
    ocupado.current = false
    if (r.tipo === 'ajeno') return
    const remapear = (h) => r.cambios.reduce((x, [a, b]) => reemplazarId(x, a, b), h)
    const que = dir < 0 ? 'Deshacer' : 'Rehacer'
    if (r.ok) {
      setHistorial(remapear(paso.h))
      setAviso(dir < 0 ? 'Deshecho.' : 'Rehecho.')
    } else if (r.tipo === 'rechazo') {
      // Sacarlo del historial: si no, cada Ctrl+Z repetiría el mismo pedido imposible.
      setHistorial(remapear(descartarPaso(paso.h, dir)))
      setAviso(`${que} no se pudo: el servidor lo rechazó (el motivo está en el aviso). Lo saqué del historial; la lista es la que tiene guardada el servidor.`)
    } else {
      // Sin red, 5xx o la base ocupada: el paso queda para reintentar. Si quedó a medias, lo
      // hecho ya está en la base y el próximo intento saltea esa parte (no duplica).
      setHistorial(remapear(historial))
      setAviso(r.sinLectura
        ? `${que}: no pude leer el servidor, así que no mandé nada. Reintentá cuando vuelva.`
        : r.parcial
          ? `${que} quedó a medias: lo que se hizo ya está guardado y la lista es la del servidor. ${dir < 0 ? 'Ctrl+Z' : 'Ctrl+Y'} de nuevo termina lo que falta, sin repetir lo hecho.`
          : `${que} no se pudo (sin red, el servidor falló o la base estaba ocupada). Quedó en el historial: reintentá.`)
    }
  }

  /* ---------- poner marcas ---------- */
  const enGrilla = (t) => (snapActivo ? snapGrilla(t, g, ajuste.offset) : t)
  const aqui = () => ms3(acotar(enGrilla(posActual()), dur))

  const ponerCue = (num) => {
    if (!track) return
    const n = num === undefined ? padLibre(marcas) : num
    if (n === null) { setAviso('Los 8 pads (A–H) están ocupados: borrá uno o movelo.'); return }
    const t = aqui()
    setAviso('')
    crear({ tipo: 'cue', inicio: t, num: n }, `Hot cue ${letraPad(n)} en ${fmtTiempo(t)}`)
  }
  const ponerMemory = () => {
    if (!track) return
    const t = aqui()
    setAviso('')
    crear({ tipo: 'memory', inicio: t }, `Memory cue en ${fmtTiempo(t)}`)
  }
  const ponerLoopIn = () => {
    if (!track) return
    const t = aqui()
    setLoopIn(t)
    setAviso(`Entrada del loop en ${fmtTiempo(t)}: tocá «Loop out» (O) donde termina.`)
  }
  const cuerpoLoop = (inicio, fin) => {
    const c = { tipo: 'loop', inicio, fin }
    if (loopPad) {
      const n = padLibre(marcas)
      if (n === null) return { error: 'Los 8 pads están ocupados: el loop no puede ir a un pad (destildá «en un pad»).' }
      c.num = n
    }
    return c
  }
  const ponerLoopOut = async () => {
    if (!track) return
    const t = aqui()
    const err = errorLoop(loopIn, t, dur)
    if (err) { setAviso(err); return }
    const c = cuerpoLoop(loopIn, t)
    if (c.error) { setAviso(c.error); return }
    setAviso('')
    const entrada = loopIn
    const d = await crear(c, `Loop ${fmtTiempo(entrada)} → ${fmtTiempo(t)}`)
    if (d) setLoopIn(null)
  }
  const ponerLoopBeats = async () => {
    if (!track) return
    if (!periodo) { setAviso('Sin BPM medido no hay beats: marcá el loop con Loop in (I) y Loop out (O).'); return }
    const t = aqui()
    const fin = ms3(t + loopBeats * periodo)
    const err = errorLoop(t, fin, dur)
    if (err) { setAviso(err); return }
    const c = cuerpoLoop(t, fin)
    if (c.error) { setAviso(c.error); return }
    setAviso('')
    await crear(c, `Loop de ${loopBeats} beat${loopBeats === 1 ? '' : 's'} en ${fmtTiempo(t)}`)
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
    fino: (a) => irA(posActual() + a.dir * FINO_CURSOR_S),
    pad: (a) => tocarPad(a.num),
    borrarPad: (a) => tocarPad(a.num, { borrarlo: true }),
    cue: () => ponerCue(),
    memory: ponerMemory,
    loopIn: ponerLoopIn,
    loopOut: ponerLoopOut,
    loopBeats: ponerLoopBeats,
    zoom: (a) => cambiarZoom(a.dir),
    grilla: () => eg.hay && cambiarAjuste({ ...ajuste, oculta: !ajuste.oculta }),
    medioBeat: () => eg.hay && cambiarAjuste({ ...ajuste, offset: moverAjuste(ajuste.offset, g.periodo_s / 2, g.periodo_s) }),
    snap: () => grillaVisible && setSnap((s) => !s),
    deshacer: () => correrHistorial(-1),
    rehacer: () => correrHistorial(1),
    inicio: () => irA(0, { centrar: true }),
    fin: () => irA(dur, { centrar: true }),
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
  const noSeGuardo = `${fallasTotal} cambio${fallasTotal === 1 ? '' : 's'} no se guardó${fallasTotal === 1 ? '' : 'ron'} (ver el aviso)`
  const cuenta = (tipo) => marcas.filter((m) => m.tipo === tipo).length
  const resumen = `${cuenta('cue')} hot cue${cuenta('cue') === 1 ? '' : 's'}, ${cuenta('memory')} memory, ${cuenta('loop')} loop${cuenta('loop') === 1 ? '' : 's'}`
  const etiquetaTema = (t) => `${t.artista ? `${t.artista} — ` : ''}${t.titulo}`

  // Etiquetas de la regla: compases (si el motor estimó el 1) o tiempos.
  // Una etiqueta cada ~80 px como mucho (a 400 px de ancho se pisaban).
  const etiquetas = Math.max(2, Math.min(8, Math.floor(anchoPx / 80)))
  const regla = (() => {
    if (!(v.hasta > v.desde)) return []
    if (conCompas) {
      const unos = lineas.filter((l) => l.beat === 1)
      const paso = Math.max(1, Math.ceil(unos.length / etiquetas))
      return unos.filter((_, i) => i % paso === 0).map((l) => {
        const c = compasDe(l.t + 1e-4, g, ajuste.offset)
        return { t: l.t, txt: c ? `${c.compas}` : '' }
      })
    }
    const w = v.hasta - v.desde
    const paso = [0.5, 1, 2, 5, 10, 15, 30].find((p) => w / p <= etiquetas) || 60
    const out = []
    for (let k = Math.ceil(v.desde / paso - 1e-9); k * paso <= v.hasta + 1e-9; k++) out.push({ t: k * paso, txt: fmtTiempo(k * paso, paso < 1 ? 1 : 0) })
    return out
  })()

  const estadoGrillaTxt = !general ? 'Leyendo el audio…'
    : general.error ? ''
      : !eg.hay ? `Sin grilla de beats: ${eg.motivo}. No se dibuja ninguna (una grilla inventada mentiría).`
        : `Grilla orientativa del motor, verificala sobre un kick · confianza ${coma(g.confianza)}${eg.baja.includes('confianza') ? ' (baja)' : ''} · nitidez ${Number.isFinite(g.concentracion) ? coma(g.concentracion) : '?'}${eg.baja.includes('nitidez') ? ' (baja)' : ''}${eg.atenuada ? ': se dibuja atenuada y puede estar corrida' : ''} · ${ajuste.oculta ? 'oculta' : textoAjuste(ajuste.offset, g.periodo_s)}`

  return (
    <div className="edd-fondo">
      <div className="edd" ref={rootRef} role="dialog" aria-modal="true" aria-labelledby="edd-titulo" aria-describedby="edd-ayuda">
        <audio ref={audioRef} preload="metadata" onPlay={alArrancar} onPause={alParar} onEnded={alParar}
          onError={() => setSonando(false)}
          onLoadedMetadata={(e) => { if (cursorRef.current > 0) { try { e.currentTarget.currentTime = cursorRef.current } catch { /* nada */ } } }} />

        {/* ---- cabecera ---- */}
        <header className="edd-cab">
          <div className="edd-cab-t">
            <span className="eyebrow">Editor de cues y loops · tema {idx + 1} de {temas.length}</span>
            <h2 id="edd-titulo" className="edd-h">{tema ? tema.titulo : '—'}{tema && tema.artista && <span className="edd-artista"> · {tema.artista}</span>}</h2>
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
            <button type="button" className="btn btn-secondary edd-btn" onClick={() => irTema(idx - 1)} disabled={idx === 0} aria-keyshortcuts="PageUp">← Anterior <Kbd>RePág</Kbd></button>
            <label className="edd-sel">
              <span className="sr-only">Tema de la playlist</span>
              <select className="input" value={idx} onChange={(e) => irTema(Number(e.target.value))}>
                {temas.map((t, k) => <option key={t.id} value={k}>{k + 1}. {etiquetaTema(t)}</option>)}
              </select>
            </label>
            <button type="button" className="btn btn-secondary edd-btn" onClick={() => irTema(idx + 1)} disabled={idx === temas.length - 1} aria-keyshortcuts="PageDown">Siguiente → <Kbd>AvPág</Kbd></button>
            <button type="button" className="btn btn-secondary edd-btn edd-cerrar" onClick={() => cerrar()} aria-keyshortcuts="Escape">Cerrar <Kbd>Esc</Kbd></button>
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
            {visibles.map((m) => (
              <span key={m.id} data-id={m.id}
                className={`edd-flag ${claseMarca(m)} is-${m.tipo}${pct(posDe(m, 'inicio')) > 60 ? ' is-der' : ''}`}
                style={{ left: `${Math.max(0, Math.min(100, pct(posDe(m, 'inicio'))))}%` }}
                title={`${NOMBRE_TIPO[m.tipo]} ${rotuloMarca(m)} · ${fmtTiempo(m.inicio)} (arrastrá para moverla)`}
                onPointerDown={(e) => empezarArrastre(e, m, 'inicio')}>
                {m.tipo === 'memory' && <i className="edd-tri" />}
                {rotuloMarca(m)}{m.nombre && <span className="edd-flag-nom"> · {m.nombre}</span>}
              </span>
            ))}
          </div>
          <div className={`edd-onda${grillaVisible && eg.atenuada ? ' is-atenuada' : ''}`} ref={ondaRef} role="slider" tabIndex={0}
            aria-label={`Posición en ${track ? track.titulo : 'el tema'}`}
            aria-valuemin={0} aria-valuemax={Math.round(dur)} aria-valuenow={Math.round(cursor)}
            aria-valuetext={`${fmtTiempo(cursor, 1) ?? '0:00.0'} de ${fmtTiempo(dur, 1) ?? '—'}${compas ? (compas.compas ? `, compás ${compas.compas} beat ${compas.beat}` : `, beat ${compas.beat} antes del primer compás`) : ''}`}
            data-desde={v.desde.toFixed(3)} data-hasta={v.hasta.toFixed(3)}
            onPointerDown={ondaDown} onPointerMove={ondaMove}
            onPointerUp={async (e) => { if (!(await soltarArrastre())) ondaUp(e) }}
            onPointerCancel={() => { arrastreRef.current = null; setArrastre(null); panRef.current = null }}>
            <canvas ref={canvasRef} className="edd-canvas" aria-hidden="true" />
            <div className="edd-grilla" aria-hidden="true">
              {lineas.map((l) => (
                <i key={l.t} className={`edd-beat${l.beat === 1 ? ' is-compas' : ''}`} data-t={l.t} style={{ left: `${pct(l.t)}%` }} />
              ))}
            </div>
            {visibles.map((m) => (m.tipo === 'loop' ? (
              <div key={m.id} className="edd-mk-loop" data-id={m.id} aria-hidden="true"
                style={{ left: `${pct(posDe(m, 'inicio'))}%`, width: `${pct(posDe(m, 'fin')) - pct(posDe(m, 'inicio'))}%` }}>
                <span className="edd-loop-fin" title="Salida del loop (arrastrá para moverla)" onPointerDown={(e) => empezarArrastre(e, m, 'fin')} />
              </div>
            ) : (
              <i key={m.id} className={`edd-mk ${claseMarca(m)} is-${m.tipo}`} data-id={m.id} aria-hidden="true" style={{ left: `${pct(posDe(m, 'inicio'))}%` }} />
            )))}
            {loopIn !== null && loopIn >= v.desde && loopIn <= v.hasta && <i className="edd-loopin" style={{ left: `${pct(loopIn)}%` }} aria-hidden="true" />}
            <i className="edd-cabezal" ref={cabezalRef} aria-hidden="true" />
            {!general && <span className="edd-msg">Leyendo el audio…</span>}
            {general && general.error && <span className="edd-msg is-err">{general.error}</span>}
          </div>
          <div className="edd-leyenda">
            <span className="edd-ley"><i className="edd-sw is-graves" aria-hidden="true" />Graves (kick y bajo)</span>
            <span className="edd-ley"><i className="edd-sw is-medios" aria-hidden="true" />Medios</span>
            <span className="edd-ley"><i className="edd-sw is-agudos" aria-hidden="true" />Agudos (hi-hats)</span>
            <span className="edd-ley-nota">Bandas relativas dentro del tema, no volumen absoluto.{conCompas ? ' Línea gruesa = compás (estimado).' : ''} ▼ = memory cue.</span>
          </div>

          {/* ---- la grilla: estimada, siempre dicho ---- */}
          <div className="edd-grilla-ctrl" role="group" aria-label="Grilla de beats estimada">
            <p className={`edd-grilla-estado${eg.hay ? (eg.atenuada ? ' is-baja' : '') : ' is-sin'}`} role="status">
              {eg.hay && <><span className="edd-estimada">estimada</span>{' '}</>}{estadoGrillaTxt}
            </p>
            {eg.hay && (
              <div className="edd-grilla-btns">
                <button type="button" className="btn btn-secondary edd-btn" aria-pressed={ajuste.oculta} aria-keyshortcuts="G"
                  onClick={() => acciones.current.grilla()}>{ajuste.oculta ? 'Mostrar grilla' : 'Ocultar grilla'} <Kbd>G</Kbd></button>
                <button type="button" className="btn btn-secondary edd-btn edd-medio" aria-keyshortcuts="H" disabled={ajuste.oculta}
                  onClick={() => cambiarAjuste({ ...ajuste, offset: moverAjuste(ajuste.offset, g.periodo_s / 2, g.periodo_s) })}>Correr ½ beat <Kbd>H</Kbd></button>
                <button type="button" className="btn btn-secondary edd-btn" disabled={ajuste.oculta} aria-label="Correr la grilla 5 milisegundos para atrás"
                  onClick={() => cambiarAjuste({ ...ajuste, offset: moverAjuste(ajuste.offset, -AJUSTE_FINO_S, g.periodo_s) })}>−5 ms</button>
                <button type="button" className="btn btn-secondary edd-btn" disabled={ajuste.oculta} aria-label="Correr la grilla 5 milisegundos para adelante"
                  onClick={() => cambiarAjuste({ ...ajuste, offset: moverAjuste(ajuste.offset, AJUSTE_FINO_S, g.periodo_s) })}>+5 ms</button>
                <button type="button" className="btn btn-secondary edd-btn" disabled={!ajuste.offset}
                  onClick={() => cambiarAjuste({ ...ajuste, offset: 0 })}>Como la estimó el motor</button>
                <label className={`edd-snap${grillaVisible ? '' : ' is-off'}`}>
                  <input type="checkbox" checked={snapActivo} disabled={!grillaVisible} onChange={() => setSnap((s) => !s)} aria-keyshortcuts="Q" />
                  Ajustar las marcas a la grilla <Kbd>Q</Kbd>
                </label>
              </div>
            )}
          </div>
        </section>

        {/* ---- transporte y zoom ---- */}
        <div className="edd-trans">
          <button type="button" className="btn btn-secondary edd-btn edd-play" onClick={alternar} aria-keyshortcuts="Space" aria-disabled={!track || undefined}>
            {sonando ? <IconPause size={14} /> : <IconPlayFill size={14} />}{sonando ? 'Pausar' : 'Reproducir'} <Kbd>Espacio</Kbd>
          </button>
          <button type="button" className="btn btn-secondary edd-btn" onClick={() => moverBeat(-1)} aria-keyshortcuts="ArrowLeft">−1 {periodo ? 'beat' : 's'} <Kbd>←</Kbd></button>
          <button type="button" className="btn btn-secondary edd-btn" onClick={() => moverBeat(1)} aria-keyshortcuts="ArrowRight">+1 {periodo ? 'beat' : 's'} <Kbd>→</Kbd></button>
          <button type="button" className="btn btn-secondary edd-btn" onClick={() => irA(posActual() - FINO_CURSOR_S)} aria-keyshortcuts="Shift+ArrowLeft" aria-label="Correr el cursor 10 milisegundos para atrás">−10 ms <Kbd>Mayús ←</Kbd></button>
          <button type="button" className="btn btn-secondary edd-btn" onClick={() => irA(posActual() + FINO_CURSOR_S)} aria-keyshortcuts="Shift+ArrowRight" aria-label="Correr el cursor 10 milisegundos para adelante">+10 ms <Kbd>Mayús →</Kbd></button>
          <span className="edd-reloj mono" ref={relojRef} aria-hidden="true">0:00.000</span>
          {compas && <span className="edd-compas mono">{compas.compas ? `compás ${compas.compas}.${compas.beat}` : `beat ${compas.beat} (antes del 1)`} <small>(estimado)</small></span>}
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

        {/* ---- vista general ---- */}
        <section className="edd-panel edd-general" aria-label="Vista general del tema">
          <div className="edd-gen" role="slider" tabIndex={0} aria-label="Parte del tema que muestra la onda grande"
            aria-valuemin={0} aria-valuemax={Math.round(dur)} aria-valuenow={Math.round((v.desde + v.hasta) / 2)}
            aria-valuetext={`de ${fmtTiempo(v.desde, 1)} a ${fmtTiempo(v.hasta, 1)}`}
            onKeyDown={teclaGeneral}
            onPointerDown={(e) => { if (e.button === 0 && dur > 0) { e.currentTarget.setPointerCapture?.(e.pointerId); generalPunto(e) } }}
            onPointerMove={(e) => { if (e.buttons === 1 && dur > 0) generalPunto(e) }}>
            <canvas ref={generalRef} className="edd-canvas" aria-hidden="true" />
            {dur > 0 && marcas.map((m) => (
              <i key={m.id} className={`edd-gmk ${claseMarca(m)} is-${m.tipo}`} aria-hidden="true"
                style={m.tipo === 'loop' ? { left: `${(m.inicio / dur) * 100}%`, width: `${((m.fin - m.inicio) / dur) * 100}%` } : { left: `${(m.inicio / dur) * 100}%` }} />
            ))}
            {dur > 0 && <i className="edd-vista" aria-hidden="true" style={{ left: `${(v.desde / dur) * 100}%`, width: `${((v.hasta - v.desde) / dur) * 100}%` }} />}
            <i className="edd-cabezal is-gen" ref={cabezalGenRef} aria-hidden="true" />
          </div>
          <p className="edd-nota">Vista general. El recuadro es lo que muestra la onda grande: clic o arrastre para ir a otra parte (con el foco acá, ← → pasan de a una pantalla).</p>
          {desfasada && <p className="edd-nota edd-warn" role="note">{desfasada}</p>}
        </section>

        {/* ---- pads, acciones y lista ---- */}
        <div className="edd-abajo">
          <section className="edd-pads-box" aria-labelledby="edd-pads-h">
            <h3 className="edd-sub" id="edd-pads-h">Hot cues A–H · tocá un pad vacío para fijarlo, uno lleno para ir · <Kbd>1</Kbd>…<Kbd>8</Kbd> · Mayús + número lo borra</h3>
            <div className="edd-pads">
              {PADS.split('').map((letra, n) => {
                const m = marcaDelPad(marcas, n)
                const loop = m && m.tipo === 'loop'
                const etiqueta = !m ? `Pad ${letra} vacío: fijar un hot cue en el cursor`
                  : loop ? `Pad ${letra}: loop de ${fmtTiempo(m.inicio)} a ${fmtTiempo(m.fin)}${m.nombre ? `, ${m.nombre}` : ''}. Ir al loop`
                    : `Pad ${letra}: hot cue en ${fmtTiempo(m.inicio)}${m.nombre ? `, ${m.nombre}` : ''}. Ir al cue`
                return (
                  <button key={letra} type="button" data-pad={n} aria-keyshortcuts={String(n + 1)}
                    className={`edd-pad ${m ? `is-lleno ${claseMarca(m)}` : 'is-vacio'}${loop ? ' is-loop' : ''}`}
                    aria-label={etiqueta} title={etiqueta}
                    onClick={(e) => tocarPad(n, { borrarlo: e.shiftKey })}>
                    <span className={`edd-pad-n ${m ? claseMarca(m) : `cue-c${n + 1}`}`} aria-hidden="true">{letra}</span>
                    <span className="edd-pad-t">
                      {!m && <span className="edd-pad-vacio">Vacío</span>}
                      {m && <span className="edd-pad-nom">{loop ? `Loop${beatsDeLoop(m.inicio, m.fin, track && track.bpm) !== null ? ` · ${beatsDeLoop(m.inicio, m.fin, track.bpm)} beats` : ''}` : (m.nombre || 'Hot cue')}</span>}
                      {m && <span className="edd-pad-tiempo mono">{fmtTiempo(m.inicio)}{loop ? ` → ${fmtTiempo(m.fin)}` : ''}</span>}
                    </span>
                    <Kbd>{n + 1}</Kbd>
                  </button>
                )
              })}
            </div>
            <div className="edd-acc">
              <button type="button" className="btn btn-primary edd-btn" onClick={() => ponerCue()} aria-keyshortcuts="C" aria-disabled={!track || undefined}>Hot cue en el primer pad libre <Kbd>C</Kbd></button>
              <button type="button" className="btn btn-secondary edd-btn" onClick={ponerMemory} aria-keyshortcuts="M" aria-disabled={!track || undefined}>Memory <Kbd>M</Kbd></button>
              <button type="button" className="btn btn-secondary edd-btn" onClick={ponerLoopIn} aria-keyshortcuts="I" aria-disabled={!track || undefined}>Loop in <Kbd>I</Kbd></button>
              <button type="button" className="btn btn-secondary edd-btn" onClick={ponerLoopOut} aria-keyshortcuts="O" aria-disabled={!track || undefined}>Loop out <Kbd>O</Kbd></button>
            </div>
            <div className="edd-acc">
              <button type="button" className="btn btn-secondary edd-btn edd-loopb" onClick={ponerLoopBeats} aria-keyshortcuts="L" aria-disabled={!track || !periodo || undefined}>
                Loop de {loopBeats} beat{loopBeats === 1 ? '' : 's'} aquí <Kbd>L</Kbd>
              </button>
              <div className="edd-chips-l" role="group" aria-label="Largo del loop en beats">
                {LOOP_BEATS.map((b) => (
                  <button key={b} type="button" className={`chip edd-zbtn${b === loopBeats ? ' is-on is-loop' : ''}`} aria-pressed={b === loopBeats} onClick={() => setLoopBeats(b)}>{b}</button>
                ))}
              </div>
              <label className="edd-snap">
                <input type="checkbox" checked={loopPad} onChange={(e) => setLoopPad(e.target.checked)} />
                El loop va a un pad (hot loop)
              </label>
            </div>
            <div className="edd-acc">
              <button type="button" className="btn btn-secondary edd-btn" onClick={() => correrHistorial(-1)} disabled={!historial.pasado.length} aria-keyshortcuts="Control+Z">Deshacer <Kbd>Ctrl Z</Kbd></button>
              <button type="button" className="btn btn-secondary edd-btn" onClick={() => correrHistorial(1)} disabled={!historial.futuro.length} aria-keyshortcuts="Control+Y">Rehacer <Kbd>Ctrl Y</Kbd></button>
            </div>
            {loopIn !== null && <p className="edd-nota edd-pend">Entrada del loop: <span className="mono">{fmtTiempo(loopIn)}</span> (falta la salida).</p>}
            <p className="edd-nota edd-aviso" role="status" aria-live="polite">{aviso}</p>
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
          </section>

          <section className="edd-panel edd-lista" aria-labelledby="edd-lista-h">
            <h3 className="edd-sub" id="edd-lista-h">Todas las marcas</h3>
            {track && marcas.length === 0 && <p className="edd-nota">Sin marcas. Reproducí y tocá un pad, C, M, L o I / O.</p>}
            <ul className="edd-filas">
              {marcas.map((m) => (
                <li key={m.id} className={`edd-fila${m.fuera_del_track ? ' is-fuera' : ''}`} data-id={m.id} data-tipo={m.tipo}>
                  <button type="button" className={`edd-tag ${claseMarca(m)} is-${m.tipo}`} onClick={() => irA(m.inicio, { centrar: true })}
                    aria-label={`Ir a ${nombreD(m)}`} title={`Ir a ${nombreD(m)}`}>{rotuloMarca(m)}</button>
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
                  <div className="edd-fila-acc">
                    <button type="button" className="btn btn-secondary edd-btn edd-alcursor" onClick={() => moverAlCursor(m)}
                      aria-label={`Mover ${nombreD(m)} al cursor`}>Al cursor</button>
                    <button type="button" className="btn btn-secondary edd-btn edd-borrar" onClick={() => borrar(m)} aria-label={`Borrar ${nombreD(m)}`}>Borrar</button>
                  </div>
                </li>
              ))}
            </ul>
            <p className="edd-nota">Loops siempre en naranja (con pad o sin pad). Memory: ▼ y «M», con borde y sin relleno, para no confundirla con el pad H.</p>
          </section>
        </div>

        <p className="edd-nota edd-ayuda" id="edd-ayuda">
          Atajos con el foco en el editor: <Kbd>Espacio</Kbd> reproduce · <Kbd>←</Kbd> <Kbd>→</Kbd> ±1 beat (Mayús: ±10 ms) · <Kbd>1</Kbd>…<Kbd>8</Kbd> pads ·
          {' '}<Kbd>C</Kbd> <Kbd>M</Kbd> <Kbd>I</Kbd> <Kbd>O</Kbd> <Kbd>L</Kbd> marcas · <Kbd>+</Kbd> <Kbd>−</Kbd> zoom · <Kbd>G</Kbd> grilla · <Kbd>H</Kbd> ½ beat · <Kbd>Q</Kbd> ajustar a la grilla ·
          {' '}<Kbd>Ctrl Z</Kbd> deshacer · <Kbd>RePág</Kbd> <Kbd>AvPág</Kbd> tema · <Kbd>Inicio</Kbd> <Kbd>Fin</Kbd> · <Kbd>Esc</Kbd> cierra.
          {' '}El ajuste de la grilla queda guardado solo en este navegador.
        </p>
      </div>
    </div>
  )
}
