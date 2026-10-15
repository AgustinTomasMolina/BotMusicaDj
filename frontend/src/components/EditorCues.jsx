import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { getMarcas, crearMarca, cambiarMarca, borrarMarca, getOnda3, radioAudioUrl, radioAudioMotivo } from '../api'
import { fmtTiempo, parseTiempo, padLibre, marcaDelPad, errorLoop, claseMarca, acotar, avisoDuracion } from '../cues'
import {
  ZOOM_COMPASES, ZOOM_SEGUNDOS, ZOOM_DEFECTO, AJUSTE_FINO_S, PADS, LOOP_BEATS,
  estadoGrilla, periodoBeat, lineasGrilla, snapGrilla, moverAjuste, textoAjuste, leerAjuste, guardarAjuste,
  anchoZoom, ventana, tiempoDeXV, tramoPedido, alcanza, columnas,
  historialVacio, registrar, deshacer, rehacer, reemplazarId, cuerpoCrear, diferencia,
  atajoD, fmtBpmMedido, letraPad, rotuloMarca, compasDe, nombreD, descartarPaso, temaVigente, beatsLoop,
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
  const [fallas, setFallas] = useState([])
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
  const rechazado = useRef(false)                 // la última escritura fallida, ¿la rechazó el servidor?
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

  // El ajuste de la grilla se guarda en este navegador, por tema. Recién pasado de tema, el
  // ajuste y la grilla que hay son del anterior: no se guardan bajo la clave del nuevo.
  const cambiarAjuste = (nuevo) => {
    if (!temaVigente(id, idRef.current, track)) return
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
  // La lista que se dibuja es SIEMPRE una que mandó el servidor (respuesta de una escritura o
  // relectura). `seq` es el orden en que se PIDIÓ: una respuesta vieja no pisa a una nueva.
  const ponerLista = (tid, nuevas, seq) => {
    if (tid !== idRef.current || seq < lista.current.aplicada) return
    lista.current.aplicada = seq
    setDatos((d) => (d && d.track && d.track.id === tid ? { ...d, marcas: nuevas } : d))
  }

  // Vuelve a leer las marcas del servidor. Si sale bien, la pantalla muestra lo que de verdad
  // quedó guardado y las fallas hasta `hasta` (o todas) quedan reconciliadas.
  const releer = async (tid, hasta) => {
    const seq = ++lista.current.seq
    let r = null
    try { r = await getMarcas(tid) } catch { r = null }
    if (!(r && r.ok && Array.isArray(r.data?.marcas))) return false
    ponerLista(tid, r.data.marcas, seq)
    if (tid === idRef.current) setFallas((f) => f.map((x) => (hasta === undefined || x.id <= hasta ? { ...x, reconciliada: true } : x)))
    return true
  }

  const conEstado = async (que, hacer) => {
    const tid = id
    const seq = ++lista.current.seq
    vuelo.current.n += 1
    setEstado({ fase: 'guardando' })
    let r = null
    try { r = await hacer(tid) } catch { r = null }
    vuelo.current.n -= 1
    // La respuesta llegó con OTRO tema abierto. El pedido se hizo contra SU tema (`tid`) y vale:
    // se devuelve como siempre, para que lo que sigue (borrar la marca vieja de un cambio de
    // tipo, la próxima sub-operación de un grupo) se complete contra ese tema y el servidor
    // quede coherente. Lo que NO se toca es la pantalla del tema abierto: lista, estado, avisos
    // e historial (los que llaman registran el paso solo con `temaVigente`).
    if (!temaVigente(tid, idRef.current)) return r && r.ok && Array.isArray(r.data?.marcas) ? r.data : null
    if (r && r.ok && Array.isArray(r.data?.marcas)) {
      ponerLista(tid, r.data.marcas, seq)
      rechazado.current = false
      if (vuelo.current.n === 0) setEstado({ fase: 'guardado', que, hora: new Date() })
      return r.data
    }
    // 400 / 404 / 409: el servidor RECHAZÓ el pedido tal cual (una marca que otra pestaña
    // borró, un pad que otra ocupó, la base ocupada): repetirlo da lo mismo. 5xx o sin red:
    // puede no haber llegado; el historial no se toca y se puede volver a intentar. En los dos
    // casos el aviso queda (un éxito posterior no lo borra) y se relee la lista del servidor:
    // lo que se ve vuelve a ser lo guardado, no lo que se creía guardado.
    rechazado.current = Boolean(r) && [400, 404, 409].includes(r.status)
    const fid = ++fallaSeq.current
    if (tid === idRef.current) {
      setFallas((f) => [...f, { id: fid, texto: `${que}: no se guardó. ${r ? motivoDe(r) : CONEXION}`, reconciliada: false }].slice(-5))
    }
    await releer(tid, fid)
    if (vuelo.current.n === 0) setEstado({ fase: 'leido' })
    return null
  }

  // Las operaciones del historial contra el servidor. Devuelve { ok, cambios, hechas }:
  // `cambios`, los ids que cambiaron ([viejo, nuevo]); `hechas`, las operaciones simples que el
  // servidor confirmó tal como se mandaron. En un grupo que falla a medias, `ok` es false y
  // `hechas` dice qué alcanzó a quedar guardado.
  const aplicar = async (op, que) => {
    const tid = id
    const simple = (d, cambios) => (d ? { ok: true, cambios, hechas: [op] } : { ok: false, cambios: [], hechas: [] })
    if (op.tipo === 'crear') {
      const d = await conEstado(que, () => crearMarca(tid, cuerpoCrear(op.marca)))
      return simple(d, d ? [[op.marca.id, d.marca.id]] : [])
    }
    if (op.tipo === 'borrar') return simple(await conEstado(que, () => borrarMarca(tid, op.marca.id)), [])
    if (op.tipo === 'cambiar') return simple(await conEstado(que, () => cambiarMarca(tid, op.id, op.despues)), [])
    const cambios = []
    const hechas = []
    for (const o of op.ops) {
      let x = o
      for (const [a, b] of cambios) x = reemplazarId({ pasado: [x], futuro: [] }, a, b).pasado[0]
      const r = await aplicar(x, que)
      cambios.push(...r.cambios)
      hechas.push(...r.hechas)
      if (!r.ok) return { ok: false, cambios, hechas }
    }
    return { ok: true, cambios, hechas }
  }

  const hecho = (op) => setHistorial((h) => registrar(h, op))
  // Un paso se registra solo si su tema sigue abierto: el historial del tema que se dejó ya se
  // vació al pasar de tema, y el del tema nuevo no es suyo.
  const hechoEn = (tid, op) => { if (temaVigente(tid, idRef.current)) hecho(op) }

  const crear = async (cuerpo, que) => {
    if (!track) return null
    const tid = id
    const d = await conEstado(que, (t) => crearMarca(t, cuerpo))
    if (d) hechoEn(tid, { tipo: 'crear', marca: d.marca })
    return d
  }
  const cambiar = async (m, nuevos, que) => {
    const { antes, despues } = diferencia(m, { ...m, ...nuevos })
    if (!Object.keys(despues).length) return null
    const tid = id
    const d = await conEstado(que, (t) => cambiarMarca(t, m.id, despues))
    if (d) hechoEn(tid, { tipo: 'cambiar', id: m.id, antes, despues })
    return d
  }
  const borrar = async (m) => {
    const tid = id
    const d = await conEstado(`Borrar ${nombreD(m)}`, (t) => borrarMarca(t, m.id))
    if (d && temaVigente(tid, idRef.current)) { hecho({ tipo: 'borrar', marca: m }); setAviso(`Borraste ${nombreD(m)}. Ctrl+Z lo vuelve a poner.`) }
    return d
  }
  // Cambiar el tipo: se crea la marca nueva y RECIÉN después se borra la vieja (si la nueva
  // no se pudo crear, no se perdió nada). Un solo paso para deshacer.
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
    const tid = id
    const d = await conEstado(que, (t) => crearMarca(t, cuerpo))
    if (!d) return
    const b = await conEstado(que, (t) => borrarMarca(t, m.id))
    if (b) hechoEn(tid, { tipo: 'grupo', ops: [{ tipo: 'crear', marca: d.marca }, { tipo: 'borrar', marca: m }] })
    else hechoEn(tid, { tipo: 'crear', marca: d.marca })
  }

  const correrHistorial = async (dir) => {
    // Recién pasado de tema, `historial` todavía es el del anterior (lo vacía el efecto).
    const tid = id
    if (!temaVigente(tid, idRef.current, track)) return
    if (ocupado.current) return
    const paso = dir < 0 ? deshacer(historial) : rehacer(historial)
    if (!paso) { setAviso(dir < 0 ? 'No hay nada para deshacer.' : 'No hay nada para rehacer.'); return }
    ocupado.current = true
    const r = await aplicar(paso.aplicar, dir < 0 ? 'Deshacer' : 'Rehacer')
    ocupado.current = false
    // Se pasó a otro tema mientras tanto: su historial no es este.
    if (!temaVigente(tid, idRef.current)) return
    if (!r.ok) {
      // Rechazado: sacarlo del historial (si no, cada Ctrl+Z repetiría el mismo pedido
      // imposible). Sin red o 5xx: queda donde estaba, para volver a intentar — salvo que
      // parte de un grupo ya se haya guardado: repetirlo entero la repetiría. Lo que se
      // guardó queda en el historial como un paso propio (se puede deshacer).
      if (rechazado.current || r.hechas.length) {
        setHistorial(descartarPaso(paso.h, dir, r.hechas, r.cambios))
        setAviso(`${dir < 0 ? 'Deshacer' : 'Rehacer'} no se pudo${r.hechas.length ? ' completo' : ''}: ${rechazado.current ? 'el servidor lo rechazó' : 'el servidor no contestó'} (el motivo está en el aviso). ${r.hechas.length ? 'Lo que alcanzó a guardarse quedó en el historial como un paso aparte' : 'Lo saqué del historial'}; la lista es la que tiene guardada el servidor.`)
      }
      return
    }
    let h = paso.h
    for (const [a, b] of r.cambios) h = reemplazarId(h, a, b)
    setHistorial(h)
    setAviso(dir < 0 ? 'Deshecho.' : 'Rehecho.')
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
    deshacer: () => correrHistorial(-1),
    rehacer: () => correrHistorial(1),
    inicio: () => irA(0, { centrar: true }),
    fin: () => track && irA(dur, { centrar: true }),
    tema: (a) => irTema(idx + a.dir),
    cerrar: () => onClose(),
  }

  const irTema = (k) => {
    if (k < 0 || k >= temas.length || k === idx) return
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
            <button type="button" className="btn btn-secondary edd-btn edd-cerrar" onClick={onClose} aria-keyshortcuts="Escape">Cerrar <Kbd>Esc</Kbd></button>
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
                <button type="button" className="btn btn-secondary edd-btn" aria-pressed={ajuste.oculta} aria-keyshortcuts="G" disabled={!track}
                  onClick={() => acciones.current.grilla()}>{ajuste.oculta ? 'Mostrar grilla' : 'Ocultar grilla'} <Kbd>G</Kbd></button>
                <button type="button" className="btn btn-secondary edd-btn edd-medio" aria-keyshortcuts="H" disabled={!track || ajuste.oculta}
                  onClick={() => cambiarAjuste({ ...ajuste, offset: moverAjuste(ajuste.offset, g.periodo_s / 2, g.periodo_s) })}>Correr ½ beat <Kbd>H</Kbd></button>
                <button type="button" className="btn btn-secondary edd-btn" disabled={!track || ajuste.oculta} aria-label="Correr la grilla 5 milisegundos para atrás"
                  onClick={() => cambiarAjuste({ ...ajuste, offset: moverAjuste(ajuste.offset, -AJUSTE_FINO_S, g.periodo_s) })}>−5 ms</button>
                <button type="button" className="btn btn-secondary edd-btn" disabled={!track || ajuste.oculta} aria-label="Correr la grilla 5 milisegundos para adelante"
                  onClick={() => cambiarAjuste({ ...ajuste, offset: moverAjuste(ajuste.offset, AJUSTE_FINO_S, g.periodo_s) })}>+5 ms</button>
                <button type="button" className="btn btn-secondary edd-btn" disabled={!track || !ajuste.offset}
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
                      {m && <span className="edd-pad-nom">{loop ? `Loop${beatsLoop(m.inicio, m.fin, periodo) !== null ? ` · ${beatsLoop(m.inicio, m.fin, periodo)} beats` : ''}` : (m.nombre || 'Hot cue')}</span>}
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
              {sinConfirmar > 0 && `⚠ ${sinConfirmar} cambio${sinConfirmar === 1 ? '' : 's'} sin confirmar y no pude volver a leer el servidor: la lista puede no ser la que quedó guardada.`}
              {!sinConfirmar && estado.fase === 'guardando' && <><span className="spinner" aria-hidden="true" /> Guardando…</>}
              {!sinConfirmar && estado.fase === 'guardado' && !fallas.length && <>✓ Guardado en la biblioteca {hora && <span className="mono">{hora}</span>} · {resumen}</>}
              {!sinConfirmar && estado.fase === 'guardado' && fallas.length > 0 && `Lista igual a la del servidor · ${resumen} · ${fallas.length} cambio${fallas.length === 1 ? '' : 's'} no se guardó${fallas.length === 1 ? '' : 'ron'} (ver el aviso)`}
              {!sinConfirmar && estado.fase === 'leido' && track && `${marcas.length ? resumen : 'Sin marcas todavía'} · ${fallas.length ? `lista releída del servidor · ${fallas.length} cambio${fallas.length === 1 ? '' : 's'} no se guardó${fallas.length === 1 ? '' : 'ron'} (ver el aviso)` : 'se guardan solas en la biblioteca'}`}
            </p>
            {fallas.length > 0 && (
              <div className="alert alert-warn edd-falla" role="alert">
                <div>
                  <div className="alert-title">No se guardó</div>
                  <ul className="edd-fallas">{fallas.map((f) => <li key={f.id}>{f.texto}</li>)}</ul>
                  {sinConfirmar > 0
                    ? <button type="button" className="btn btn-secondary edd-btn edd-releer" onClick={() => releer(id)}>Volver a leer del servidor</button>
                    : <button type="button" className="btn btn-secondary edd-btn edd-descartar" onClick={() => setFallas([])}>Entendido: descartar el aviso</button>}
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
