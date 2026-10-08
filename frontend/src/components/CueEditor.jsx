import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { getMarcas, crearMarca, cambiarMarca, borrarMarca, radioAudioUrl, radioAudioMotivo } from '../api'
import {
  fmtTiempo, parseTiempo, pasoBeat, pctDe, tiempoDeX, padLibre, hotCue, errorLoop, beatsDeLoop,
  atajo, marcasRegla, fmtRegla, acotar, tramoOnda, avisoDuracion,
} from '../cues'
import { cargadorOndas } from '../ondas'
import { usePlayer } from '../player/context'
import { useVolumenEn } from '../hooks'
import { pasoVolumen, teclaVolumen } from '../volumen'
import { IconPause, IconPlayFill } from './icons'
import Volumen from './Volumen'

/* ============================================================================
   Editor de cues y loops (f48; desde f50 vive en la pestaña «Cues y loops» del detalle de la
   pantalla de radio): la forma de onda con el cursor y las marcas, los controles y la tabla de
   marcas del tema elegido en la lista del set. La lista del set (con su columna «Cues») hace
   de lista de temas: este componente edita el tema `trackId` y avisa con `onConteo` cuántas
   marcas le quedaron, y con `onDatos` lo que la API dice del tema (lo usa «Información»).

   REGLAS
   - Nada se inventa (§6): la onda son los picos del audio REAL (/onda), el BPM y la key son
     los que midió el motor (sin BPM → «?» y sin paso de beat), y las marcas son las que puso
     el dueño. No hay detector automático acá.
   - «Guardado» es lo que el servidor confirmó: cada escritura devuelve la lista entera de
     marcas tal como quedó en la base, y la tabla dibuja ESA lista. No hay escritura optimista.
   - Un solo audio en toda la app: el editor tiene su propio <audio> en el DOM (para leer el
     tiempo exacto al marcar), y entra en el mismo protocolo que la pantalla de radio: al
     arrancar pausa cualquier otro <audio>/<video> de la página, y la barra de abajo
     (PlayerProvider) escucha ese `play` y cede; si arranca la barra, ella pausa a este.
   - El ±1 beat suma o resta 60/BPM al cursor. NO es una grilla (no tenemos el offset del
     primer beat), por eso no hay "ajustar al beat".
   - Los atajos actúan solo con el foco ADENTRO del editor (f50): ahora comparte la pantalla
     con la lista y las pestañas, y una «c» tecleada en otro lado no puede sembrar un cue.
   - Volumen (f50): el de toda la app (PlayerProvider). ↑ ↓ lo mueven con el foco en el editor
     o en la onda, salvo escribiendo o con un modificador.
   ========================================================================== */

const fmtBpm = (v) => (v === null || v === undefined ? null : Number(v).toFixed(1))
const ms3 = (t) => Math.round(t * 1000) / 1000

const NOMBRE_TIPO = { cue: 'Hot cue', memory: 'Memory', loop: 'Loop' }

// El texto de un rechazo: el motivo del backend tal cual; si no hay, el código (lo único cierto).
function motivo(r) {
  const d = (r && r.data) || {}
  if (typeof d.error === 'string' && d.error.trim()) return d.error
  if (typeof d.detail === 'string' && d.detail.trim()) return d.detail
  if (d.error_texto) return `El servidor falló (HTTP ${r.status}): ${d.error_texto}`
  return `El servidor contestó HTTP ${r ? r.status : '?'} sin explicación.`
}

// Cómo se llama una marca para un lector de pantalla y para los avisos.
function nombreMarca(m) {
  if (m.tipo === 'cue') return `hot cue ${m.num + 1}`
  if (m.tipo === 'memory') return `memory cue en ${fmtTiempo(m.inicio)}`
  return `loop ${fmtTiempo(m.inicio)} a ${fmtTiempo(m.fin)}`
}

const claseTag = (m) => (m.tipo === 'cue' ? `cue-c${m.num + 1}` : m.tipo === 'memory' ? 'cue-mem' : 'cue-loop')
const textoTag = (m) => (m.tipo === 'cue' ? String(m.num + 1) : m.tipo === 'memory' ? 'M' : 'L')

const CONEXION = 'No pude conectar con el servidor. Revisá que esté corriendo y volvé a intentar.'

/* ---------- un tiempo editable en la tabla ---------- */
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
    <input className="input cue-in-t mono" value={txt} aria-label={etiqueta} aria-invalid={mal || undefined}
      inputMode="decimal" spellCheck={false} autoComplete="off" title="m:ss.mmm — Enter guarda, Esc vuelve"
      onChange={(e) => setTxt(e.target.value)} onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') { e.preventDefault(); commit() }
        if (e.key === 'Escape') { setTxt(fmtTiempo(valor)); setMal(false) }
      }} />
  )
}

/* ---------- el nombre editable ---------- */
function CampoNombre({ valor, etiqueta, max, onCommit }) {
  const [txt, setTxt] = useState(valor || '')
  useEffect(() => { setTxt(valor || '') }, [valor])
  const commit = () => { if (txt.trim() !== (valor || '')) onCommit(txt) }
  return (
    <input className="input cue-in-n" value={txt} aria-label={etiqueta} maxLength={max || undefined}
      placeholder="sin nombre" autoComplete="off" spellCheck={false}
      onChange={(e) => setTxt(e.target.value)} onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') { e.preventDefault(); commit() }
        if (e.key === 'Escape') setTxt(valor || '')
      }} />
  )
}

/* ---------- la pantalla ---------- */
export default function CueEditor({ trackId, titulo, onConteo, onDatos }) {
  const sel = trackId
  const [datos, setDatos] = useState(null)        // {track, marcas, limites} | {error}
  const [onda, setOnda] = useState(null)          // {picos, duracion_audio} | {error}
  const [sonando, setSonando] = useState(false)
  const [cursor, setCursor] = useState(0)         // para aria y la tabla; el dibujo va por refs
  const [loopIn, setLoopIn] = useState(null)
  const [aviso, setAviso] = useState('')
  const [estado, setEstado] = useState({ fase: 'leido' })   // guardando | guardado | leido
  const [falla, setFalla] = useState(null)        // {texto, reintentar?}
  const [arrastre, setArrastre] = useState(null)  // {id, borde, t, t0}
  const [borrando, setBorrando] = useState(null)  // id de la marca que pide confirmación

  const rootRef = useRef(null)
  const audioRef = useRef(null)
  const ondaRef = useRef(null)
  const canvasBase = useRef(null)
  const canvasSonado = useRef(null)
  const cabezalRef = useRef(null)
  const relojRef = useRef(null)
  const cursorRef = useRef(0)
  const selRef = useRef(sel)
  selRef.current = sel
  const vuelo = useRef({ n: 0, seq: 0, aplicado: 0 })
  const arrastreRef = useRef(null)
  // Los avisos al padre leen la función de ESTE render (pueden cambiar entre renders).
  const avisos = useRef({})
  avisos.current = { onConteo, onDatos }

  // Un solo volumen para toda la app: el de la barra (PlayerProvider), también en este <audio>.
  const player = usePlayer()
  useVolumenEn(audioRef, player.volume, player.muted)

  const track = datos && datos.track && datos.track.id === sel ? datos.track : null
  const marcas = track ? datos.marcas : []
  const dur = track ? Number(track.dur) || 0 : 0
  const bpm = track ? track.bpm : null
  const max = datos && datos.limites ? datos.limites.nombre_max : null

  // Lo que la API dice del tema (lo muestra «Información», sin pedirlo otra vez).
  useEffect(() => {
    if (!datos) return
    const id = datos.track ? datos.track.id : datos.id
    if (id === sel) avisos.current.onDatos?.(sel, datos)
  }, [datos, sel])

  // Pinta el cursor sin pasar por React (60 veces por segundo mientras suena).
  const pintarCursor = useCallback((t) => {
    const p = pctDe(t, dur)
    if (cabezalRef.current) cabezalRef.current.style.left = `${p}%`
    if (canvasSonado.current) canvasSonado.current.style.clipPath = `inset(0 ${100 - p}% 0 0)`
    if (relojRef.current) relojRef.current.textContent = fmtTiempo(t, 1)
  }, [dur])

  // Cambiar de tema: se para el audio, el cursor vuelve a 0 y se piden sus marcas y su onda.
  useEffect(() => {
    let vivo = true
    const a = audioRef.current
    if (a) { a.pause(); a.src = radioAudioUrl(sel) }
    cursorRef.current = 0
    setCursor(0)
    setLoopIn(null)
    setAviso('')
    setDatos(null)
    setOnda(null)
    arrastreRef.current = null
    setArrastre(null)
    setBorrando(null)
    setEstado({ fase: 'leido' })
    getMarcas(sel)
      .then((r) => {
        if (!vivo) return
        if (r.ok && r.data && r.data.track) setDatos(r.data)
        else setDatos({ id: sel, error: r.ok ? (r.data?.motivo || 'El servidor no devolvió el track.') : motivo(r) })
      })
      .catch(() => vivo && setDatos({ id: sel, error: CONEXION }))
    // La onda por la cola compartida con las minionditas (src/ondas.js): pasa adelante de las
    // filas, pero nunca hay más de dos decodificaciones a la vez, y si la fila ya la tenía no
    // se vuelve a pedir.
    const pedido = cargadorOndas.cargar(sel, { urgente: true })
    pedido.promesa
      .then((r) => { if (vivo) setOnda(r.ok && Array.isArray(r.data?.picos) ? r.data : { error: motivo(r) }) })
      .catch(() => vivo && setOnda({ error: CONEXION }))
    return () => { vivo = false; pedido.cancelar() }
  }, [sel])

  // Al desmontar el audio del editor no puede quedar sonando.
  useEffect(() => () => { audioRef.current?.pause() }, [])

  useLayoutEffect(() => { pintarCursor(cursorRef.current) })

  // Mientras suena, el cursor sigue al audio cuadro a cuadro.
  useEffect(() => {
    if (!sonando) return undefined
    let id = 0
    let ultimo = 0
    const tick = (ahora) => {
      const a = audioRef.current
      if (a) {
        cursorRef.current = a.currentTime
        pintarCursor(a.currentTime)
        if (ahora - ultimo > 250) { ultimo = ahora; setCursor(a.currentTime) }
      }
      id = requestAnimationFrame(tick)
    }
    id = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(id)
  }, [sonando, pintarCursor])

  /* ---------- la onda ---------- */
  const dibujar = useCallback(() => {
    const cont = ondaRef.current
    if (!cont || !onda || !Array.isArray(onda.picos) || !(dur > 0)) return
    const css = getComputedStyle(document.documentElement)
    const w = cont.clientWidth
    const h = cont.clientHeight
    const dpr = window.devicePixelRatio || 1
    // Cada pico en SU tiempo sobre el eje de la base (`tramoOnda`): un archivo más corto no se
    // estira, uno más largo no se comprime (se cortan los picos que caen después de `dur`).
    // En los dos casos se avisa abajo (`avisoDuracion`).
    const tramo = tramoOnda(onda.picos.length, Number(onda.duracion_audio), dur)
    const cols = Math.max(1, Math.floor(w * tramo.ancho))
    const picos = onda.picos
    const n = tramo.picos
    for (const [ref, color] of [[canvasBase, '--cue-wave'], [canvasSonado, '--cue-wave-sonado']]) {
      const cv = ref.current
      if (!cv) continue
      cv.width = Math.max(1, Math.round(w * dpr))
      cv.height = Math.max(1, Math.round(h * dpr))
      const ctx = cv.getContext('2d')
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)
      ctx.fillStyle = css.getPropertyValue(color).trim() || 'currentColor'
      for (let c = 0; c < cols; c++) {
        const i0 = Math.floor((c * n) / cols)
        const i1 = Math.max(i0 + 1, Math.floor(((c + 1) * n) / cols))
        let amp = 0
        for (let i = i0; i < i1 && i < n; i++) if (picos[i] > amp) amp = picos[i]
        const alto = Math.max(1, amp * (h - 4))
        ctx.fillRect(c, (h - alto) / 2, 1, alto)
      }
    }
  }, [onda, dur])

  useEffect(() => {
    dibujar()
    const cont = ondaRef.current
    if (!cont || typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(() => dibujar())
    ro.observe(cont)
    return () => ro.disconnect()
  }, [dibujar])

  /* ---------- reproducción ---------- */
  const posActual = () => {
    const a = audioRef.current
    return a && !a.paused ? a.currentTime : cursorRef.current
  }

  const irA = (t) => {
    const v = acotar(t, dur)
    cursorRef.current = v
    pintarCursor(v)
    setCursor(v)
    const a = audioRef.current
    if (a && a.readyState >= 1) { try { a.currentTime = v } catch { /* sin metadata todavía */ } }
  }

  const alternar = () => {
    const a = audioRef.current
    if (!a || !track) return
    if (!a.paused) { a.pause(); return }
    if (a.readyState >= 1 && Math.abs(a.currentTime - cursorRef.current) > 0.0005) {
      try { a.currentTime = cursorRef.current } catch { /* sin metadata todavía */ }
    }
    a.play().catch((e) => {
      if (e && e.name === 'AbortError') return
      setSonando(false)
      radioAudioMotivo(sel).then((m) => setAviso(m || `No pude reproducir «${track.titulo}»: el navegador no pudo abrir ese audio.`))
    })
  }

  const alArrancar = () => {
    const a = audioRef.current
    // Un solo audio: lo que esté sonando en la página se pausa (la barra cede sola).
    document.querySelectorAll('audio,video').forEach((m) => { if (m !== a && !m.paused) m.pause() })
    setSonando(true)
  }
  const alParar = () => {
    const a = audioRef.current
    if (a) { cursorRef.current = a.currentTime; pintarCursor(a.currentTime); setCursor(a.currentTime) }
    setSonando(false)
  }

  /* ---------- escrituras ---------- */
  // Corre una escritura y aplica la lista que devolvió el servidor. «Guardado» recién con la
  // respuesta OK. Si falla, el aviso queda hasta que se reintente o se descarte: una marca que
  // no se guardó no puede desaparecer sin que se note.
  const escribir = async (trackId, hacer, que, { reintentable = true } = {}) => {
    const v = vuelo.current
    const seq = ++v.seq
    v.n += 1
    setEstado({ fase: 'guardando' })
    let r = null
    try { r = await hacer() } catch { r = null }
    v.n -= 1
    if (r && r.ok && Array.isArray(r.data?.marcas)) {
      avisos.current.onConteo?.(trackId, r.data.marcas.length)
      if (trackId === selRef.current && seq >= v.aplicado) {
        v.aplicado = seq
        setDatos((d) => (d && d.track && d.track.id === trackId ? { ...d, marcas: r.data.marcas } : d))
      }
      if (v.n === 0) setEstado({ fase: 'guardado', que, hora: new Date() })
      return r.data
    }
    if (v.n === 0) setEstado({ fase: 'leido' })
    // 400/404: el pedido no vale tal cual (reintentarlo da lo mismo). Sin red, 5xx o 409
    // (base ocupada): reintentar tiene sentido.
    const puede = reintentable && (!r || r.status >= 500 || r.status === 409)
    const texto = `${que}: no se guardó. ${r ? motivo(r) : CONEXION}`
    setFalla({ texto, reintentar: puede ? () => { setFalla(null); escribir(trackId, hacer, que) } : null })
    return null
  }

  const nueva = (cuerpo, que) => {
    const id = sel
    return escribir(id, () => crearMarca(id, cuerpo), que)
  }

  const ponerCue = () => {
    if (!track) return
    if (padLibre(marcas) === null) { setAviso('Ya están los 8 hot cues: borrá uno o movelo.'); return }
    const t = ms3(posActual())
    setAviso('')
    nueva({ tipo: 'cue', inicio: t }, `Hot cue en ${fmtTiempo(t)}`)
  }
  const ponerMemory = () => {
    if (!track) return
    const t = ms3(posActual())
    setAviso('')
    nueva({ tipo: 'memory', inicio: t }, `Memory cue en ${fmtTiempo(t)}`)
  }
  const ponerLoopIn = () => {
    if (!track) return
    const t = ms3(posActual())
    setLoopIn(t)
    setAviso(`Entrada del loop en ${fmtTiempo(t)}: tocá «Loop out» (O) donde termina.`)
  }
  const ponerLoopOut = async () => {
    if (!track) return
    const t = ms3(posActual())
    const err = errorLoop(loopIn, t, dur)
    if (err) { setAviso(err); return }
    setAviso('')
    const entrada = loopIn
    const r = await nueva({ tipo: 'loop', inicio: entrada, fin: t }, `Loop ${fmtTiempo(entrada)} → ${fmtTiempo(t)}`)
    if (r) setLoopIn((x) => (x === entrada ? null : x))
  }
  const moverBeat = (dir) => {
    if (!track) return
    const t = pasoBeat(posActual(), bpm, dir, dur)
    if (t === null) { setAviso('Este tema no tiene BPM medido: sin BPM no hay beat. Hacé clic en la onda o usá Inicio / Fin.'); return }
    irA(t)
  }
  const irAPad = (num) => {
    const m = hotCue(marcas, num)
    if (!m) { setAviso(`El hot cue ${num + 1} está vacío.`); return }
    setAviso('')
    irA(m.inicio)
  }

  const cambiar = (m, cambios, que) => {
    const id = sel
    return escribir(id, () => cambiarMarca(id, m.id, cambios), que)
  }
  const borrar = (m) => {
    const id = sel
    setBorrando(null)
    return escribir(id, () => borrarMarca(id, m.id), `Borrar ${nombreMarca(m)}`)
  }

  // Los atajos leen las funciones de ESTE render (la lista de marcas, el loop pendiente...).
  const acciones = useRef({})
  acciones.current = {
    play: alternar, cue: ponerCue, memory: ponerMemory, loopIn: ponerLoopIn, loopOut: ponerLoopOut,
    beat: (a) => moverBeat(a.dir), ir: (a) => irAPad(a.num),
    volumen: (dir) => player.setVolume(pasoVolumen(player.muted ? 0 : player.volume, dir)),
  }

  useEffect(() => {
    const onKey = (e) => {
      if (e.defaultPrevented) return
      const root = rootRef.current
      if (!root) return
      // Solo con el foco ADENTRO del editor: comparte la pantalla con la lista del set, las
      // pestañas y la barra de abajo, que tienen sus propias teclas.
      if (!root.contains(e.target)) return
      // ↑ ↓: el volumen de la app (las flechas repetidas siguen subiendo o bajando).
      const v = teclaVolumen(e)
      if (v !== null) { e.preventDefault(); acciones.current.volumen(v); return }
      const a = atajo(e)
      if (!a) return
      // Mantener apretada una tecla no siembra marcas en fila; las flechas sí repiten.
      if (e.repeat && a.tipo !== 'beat') { e.preventDefault(); return }
      e.preventDefault()
      acciones.current[a.tipo]?.(a)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  /* ---------- arrastrar una marca sobre la onda ---------- */
  const tiempoPuntero = (e) => {
    const r = ondaRef.current.getBoundingClientRect()
    return ms3(tiempoDeX(e.clientX - r.left, r.width, dur))
  }
  const empezarArrastre = (e, m, borde) => {
    if (e.button !== 0) return
    e.stopPropagation()
    e.preventDefault()
    e.currentTarget.setPointerCapture?.(e.pointerId)
    const t0 = borde === 'fin' ? m.fin : m.inicio
    arrastreRef.current = { id: m.id, borde, t: t0, t0, movio: false }
    setArrastre(arrastreRef.current)
  }
  // El arrastre vive en un ref: los `pointermove` llegan más rápido de lo que React vuelve a
  // dibujar, y el `pointerup` leería un estado viejo (la marca quedaba donde estaba dos
  // movimientos antes). El estado es solo para dibujar la banderita mientras se mueve.
  const moverArrastre = (e) => {
    const a = arrastreRef.current
    if (!a) return
    const t = tiempoPuntero(e)
    arrastreRef.current = { ...a, t, movio: a.movio || Math.abs(t - a.t0) > 0.01 }
    setArrastre(arrastreRef.current)
  }
  const soltarArrastre = async (e, m) => {
    const a = arrastreRef.current
    if (!a || a.id !== m.id) return
    arrastreRef.current = null
    const t = tiempoPuntero(e)          // donde se soltó, no el último movimiento dibujado
    if (!a.movio && Math.abs(t - a.t0) <= 0.01) { setArrastre(null); irA(a.t0); return }   // un clic sin mover = ir a la marca
    setArrastre({ ...a, t })
    const campo = a.borde === 'fin' ? 'fin' : 'inicio'
    await cambiar(m, { [campo]: t }, `Mover ${nombreMarca(m)} a ${fmtTiempo(t)}`)
    setArrastre((x) => (x && x.id === m.id ? null : x))
  }
  const posDe = (m, borde) => (arrastre && arrastre.id === m.id && arrastre.borde === borde ? arrastre.t : (borde === 'fin' ? m.fin : m.inicio))

  /* ---------- teclado de la onda (alternativa al mouse) ---------- */
  const teclaOnda = (e) => {
    if (e.key === 'Home') { e.preventDefault(); irA(0) }
    else if (e.key === 'End') { e.preventDefault(); irA(dur) }
    else if (e.key === 'PageUp') { e.preventDefault(); irA(posActual() + 10) }
    else if (e.key === 'PageDown') { e.preventDefault(); irA(posActual() - 10) }
  }

  const regla = marcasRegla(dur)
  const cuenta = (tipo) => marcas.filter((m) => m.tipo === tipo).length
  const resumen = `${cuenta('cue')} hot cue${cuenta('cue') === 1 ? '' : 's'}, ${cuenta('memory')} memory, ${cuenta('loop')} loop${cuenta('loop') === 1 ? '' : 's'}`
  const desfasada = track && onda && !onda.error ? avisoDuracion(Number(onda.duracion_audio), dur) : null
  const fuera = marcas.filter((m) => m.fuera_del_track).length
  const hora = estado.hora ? estado.hora.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ''

  return (
    <section className="cue-ed" ref={rootRef} aria-label={`Editor de cues y loops de ${titulo || 'este tema'}`}>
      <audio ref={audioRef} preload="metadata" onPlay={alArrancar} onPause={alParar} onEnded={alParar}
        onError={() => { setSonando(false) }}
        onLoadedMetadata={(e) => { if (cursorRef.current > 0) { try { e.currentTarget.currentTime = cursorRef.current } catch { /* nada */ } } }} />

        <div className="cue-main">
          {datos && datos.error && (
            <div className="alert alert-warn" role="alert"><div><div className="alert-title">No pude abrir este tema</div><p>{datos.error}</p></div></div>
          )}

          <div className="cue-wavebox">
            <div className="cue-wave" ref={ondaRef} role="slider" tabIndex={0}
              aria-label={`Posición en ${track ? track.titulo : 'el tema'} (clic o flechas para moverse)`}
              aria-valuemin={0} aria-valuemax={Math.round(dur)} aria-valuenow={Math.round(cursor)}
              aria-valuetext={`${fmtTiempo(cursor, 1) ?? '0:00.0'} de ${fmtTiempo(dur, 1) ?? '—'}`}
              aria-describedby="cue-wave-ayuda"
              onKeyDown={teclaOnda}
              onPointerDown={(e) => { if (e.button === 0 && track) irA(tiempoPuntero(e)) }}
              onPointerMove={moverArrastre}>
              <canvas ref={canvasBase} className="cue-canvas" aria-hidden="true" />
              <canvas ref={canvasSonado} className="cue-canvas is-sonado" aria-hidden="true" />
              {!onda && <span className="cue-wave-msg">Leyendo el audio…</span>}
              {onda && onda.error && <span className="cue-wave-msg is-err">{onda.error}</span>}
              {loopIn !== null && <i className="cue-loopin" style={{ left: `${pctDe(loopIn, dur)}%` }} aria-hidden="true" />}
              {marcas.map((m) => (m.tipo === 'loop' ? (
                <div key={m.id} className="cue-mk-loop" aria-hidden="true"
                  style={{ left: `${pctDe(posDe(m, 'inicio'), dur)}%`, width: `${Math.max(0, pctDe(posDe(m, 'fin'), dur) - pctDe(posDe(m, 'inicio'), dur))}%` }}>
                  <span className="cue-flag cue-loop" title={`Loop ${fmtTiempo(m.inicio)} → ${fmtTiempo(m.fin)} (arrastrá para mover la entrada)`}
                    onPointerDown={(e) => empezarArrastre(e, m, 'inicio')} onPointerUp={(e) => soltarArrastre(e, m)} onPointerCancel={() => { arrastreRef.current = null; setArrastre(null) }}>L</span>
                  <span className="cue-flag cue-loop is-fin" title="Salida del loop (arrastrá para moverla)"
                    onPointerDown={(e) => empezarArrastre(e, m, 'fin')} onPointerUp={(e) => soltarArrastre(e, m)} onPointerCancel={() => { arrastreRef.current = null; setArrastre(null) }}>⟩</span>
                </div>
              ) : (
                <div key={m.id} className={`cue-mk ${claseTag(m)}`} style={{ left: `${pctDe(posDe(m, 'inicio'), dur)}%` }} aria-hidden="true">
                  <span className={`cue-flag ${claseTag(m)}`} title={`${NOMBRE_TIPO[m.tipo]} ${textoTag(m)} · ${fmtTiempo(m.inicio)} (arrastrá para moverla)`}
                    onPointerDown={(e) => empezarArrastre(e, m, 'inicio')} onPointerUp={(e) => soltarArrastre(e, m)} onPointerCancel={() => { arrastreRef.current = null; setArrastre(null) }}>{textoTag(m)}</span>
                </div>
              )))}
              <i className="cue-cabezal" ref={cabezalRef} aria-hidden="true" />
            </div>
            <div className="cue-regla mono" aria-hidden="true">
              {regla.map((t) => (
                <span key={t} className={t === 0 ? 'is-0' : pctDe(t, dur) >= 92 ? 'is-fin' : ''} style={{ left: `${pctDe(t, dur)}%` }}>{fmtRegla(t)}</span>
              ))}
            </div>
            <p className="rnota" id="cue-wave-ayuda">
              Onda del audio real. Clic en la onda para ir a ese punto; arrastrá una marca para moverla (o editá su tiempo en la tabla). En la onda: Inicio / Fin y RePág / AvPág (±10 s).
            </p>
            {desfasada && <p className="rnota cue-aviso-dur" role="note">{desfasada}</p>}
          </div>

          <div className="cue-ctrl">
            <button type="button" className="btn btn-secondary cue-play" onClick={alternar} aria-keyshortcuts="Space" aria-disabled={!track || undefined}>
              {sonando ? <IconPause size={14} /> : <IconPlayFill size={14} />}
              {sonando ? 'Pausar' : 'Reproducir'} <kbd className="cue-kbd">Espacio</kbd>
            </button>
            <button type="button" className="btn btn-primary cue-b-cue" onClick={ponerCue} aria-keyshortcuts="C" aria-disabled={!track || undefined}>Cue aquí <kbd className="cue-kbd">C</kbd></button>
            <button type="button" className="btn btn-secondary cue-b-mem" onClick={ponerMemory} aria-keyshortcuts="M" aria-disabled={!track || undefined}>Memory <kbd className="cue-kbd">M</kbd></button>
            <button type="button" className="btn btn-secondary cue-b-in" onClick={ponerLoopIn} aria-keyshortcuts="I" aria-disabled={!track || undefined}>Loop in <kbd className="cue-kbd">I</kbd></button>
            <button type="button" className="btn btn-secondary cue-b-out" onClick={ponerLoopOut} aria-keyshortcuts="O" aria-disabled={!track || undefined}>Loop out <kbd className="cue-kbd">O</kbd></button>
            <span className="cue-reloj mono" ref={relojRef} aria-hidden="true">0:00.0</span>
          </div>
          {/* El volumen de TODA la app (el mismo de la barra de abajo), al lado de reproducir. */}
          <Volumen className="cue-vol" ayudaId="cue-vol-ayuda" />
          <p className="rnota cue-atajos">
            Los atajos actúan con el foco en el editor (elegir un track de la lista con el mouse o con Enter lo trae acá).{' '}
            <kbd className="cue-kbd">←</kbd> <kbd className="cue-kbd">→</kbd> ±1 beat · <kbd className="cue-kbd">1</kbd>…<kbd className="cue-kbd">8</kbd> ir al hot cue · Espacio reproduce o pausa (con el foco en un botón, Espacio y Enter activan ese botón).
            {' '}<span id="cue-vol-ayuda"><kbd className="cue-kbd">↑</kbd> <kbd className="cue-kbd">↓</kbd> volumen: uno solo para toda la app, se acuerda la próxima vez.</span>
            {' '}{bpm != null
              ? `El beat sale del BPM medido (${fmtBpm(bpm)}); Rekordbox puede tener otra grilla, así que acá no se ajusta nada a la grilla.`
              : track ? 'Este tema no tiene BPM medido (el análisis no encontró pulso): sin BPM no hay ±1 beat.' : ''}
          </p>
          {loopIn !== null && <p className="rnota cue-pendiente">Entrada del loop: <span className="mono">{fmtTiempo(loopIn)}</span> (falta la salida).</p>}

          {/* Avisos de la edición (sin BPM, pad vacío, loop al revés): región viva. */}
          <p className="rnota cue-aviso" role="status" aria-live="polite">{aviso}</p>

          {/* «Guardado» recién con la respuesta del servidor. */}
          <p className={`cue-estado is-${estado.fase}`} role="status" aria-live="polite">
            {estado.fase === 'guardando' && <><span className="spinner" aria-hidden="true" /> Guardando…</>}
            {estado.fase === 'guardado' && <>✓ Guardado en la biblioteca {hora && <span className="mono">{hora}</span>} · {resumen}</>}
            {estado.fase === 'leido' && track && `${marcas.length ? resumen : 'Sin marcas todavía'} · guardadas en la biblioteca`}
          </p>
          {falla && (
            <div className="alert alert-warn cue-falla" role="alert">
              <div>
                <div className="alert-title">No se guardó</div>
                <p>{falla.texto}</p>
                <div className="cue-falla-acc">
                  {falla.reintentar && <button type="button" className="btn btn-secondary btn-sm cue-reintentar" onClick={falla.reintentar}>Reintentar</button>}
                  <button type="button" className="btn btn-secondary btn-sm cue-descartar" onClick={() => setFalla(null)}>Descartar el aviso</button>
                </div>
              </div>
            </div>
          )}
          {fuera > 0 && (
            <p className="rnota cue-aviso-dur" role="note">{fuera} marca{fuera === 1 ? '' : 's'} quedó más allá del final del tema según la biblioteca (¿el archivo cambió?). Se muestran y no se borraron.</p>
          )}

          {/* ---- abajo: la tabla de marcas ---- */}
          <div className="cue-tablabox">
            <table className="cue-tabla">
              <caption className="sr-only">Marcas de {track ? track.titulo : 'este tema'}</caption>
              <thead>
                <tr><th scope="col" className="c-n">N°</th><th scope="col" className="c-tipo">Tipo</th><th scope="col" className="c-t">Tiempo</th><th scope="col" className="c-nom">Nombre</th><th scope="col" className="c-acc"><span className="sr-only">Acciones</span></th></tr>
              </thead>
              <tbody>
                {track && marcas.length === 0 && (
                  <tr><td colSpan={5} className="cue-vacia">Sin marcas. Reproducí y tocá C, M o I / O.</td></tr>
                )}
                {marcas.map((m) => (
                  <tr key={m.id} data-id={m.id} data-tipo={m.tipo} className={m.fuera_del_track ? 'is-fuera' : ''}>
                    <td className="c-n">
                      <button type="button" className={`cue-tag ${claseTag(m)}`} onClick={() => irA(m.inicio)}
                        aria-label={`Ir a ${nombreMarca(m)}`} title={`Ir a ${nombreMarca(m)}`}>{textoTag(m)}</button>
                    </td>
                    <td className="c-tipo">{NOMBRE_TIPO[m.tipo]}</td>
                    <td className="c-t"><div className="cue-tiempos">
                      <CampoTiempo valor={m.inicio} etiqueta={`Tiempo de ${m.tipo === 'loop' ? 'entrada del ' : ''}${nombreMarca(m)}`}
                        onCommit={(t) => cambiar(m, { inicio: t }, `Mover ${nombreMarca(m)} a ${fmtTiempo(t)}`)} />
                      {m.tipo === 'loop' && (
                        <CampoTiempo valor={m.fin} etiqueta={`Tiempo de salida del ${nombreMarca(m)}`}
                          onCommit={(t) => cambiar(m, { fin: t }, `Mover la salida del loop a ${fmtTiempo(t)}`)} />
                      )}
                      {m.tipo === 'loop' && beatsDeLoop(m.inicio, m.fin, bpm) !== null && (
                        <span className="cue-beats">{beatsDeLoop(m.inicio, m.fin, bpm)} beats</span>
                      )}
                    </div></td>
                    <td className="c-nom">
                      <CampoNombre valor={m.nombre} max={max} etiqueta={`Nombre de ${nombreMarca(m)}`}
                        onCommit={(n) => cambiar(m, { nombre: n }, `Nombre de ${nombreMarca(m)}`)} />
                    </td>
                    <td className="c-acc">
                      {borrando === m.id ? (
                        <span className="cue-confirma">
                          <button type="button" className="btn btn-secondary btn-sm cue-si" onClick={() => borrar(m)} aria-label={`Confirmar: borrar ${nombreMarca(m)}`}>Borrar</button>
                          <button type="button" className="btn btn-secondary btn-sm cue-no" onClick={() => setBorrando(null)} aria-label="No borrar">No</button>
                        </span>
                      ) : (
                        <button type="button" className="cue-borrar" onClick={() => setBorrando(m.id)} aria-label={`Borrar ${nombreMarca(m)}`} title="Borrar">
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="rnota">Las marcas quedan guardadas en la biblioteca del motor, atadas a la ruta del archivo: si lo movés o lo renombrás y re-escaneás, quedan guardadas pero sin tema. Llevarlas a Rekordbox (XML con cues) llega en otra etapa.</p>
        </div>
    </section>
  )
}
