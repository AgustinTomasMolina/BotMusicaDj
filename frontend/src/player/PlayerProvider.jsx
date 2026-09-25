import { useEffect, useMemo, useRef, useState } from 'react'
import { PlayerCtx, PlayerTimeCtx } from './context'
import { engineFor, playable, NOMBRE_FUENTE } from './track'
import { AudioEngine, YouTubeEngine, SoundCloudEngine } from './engines'

const VOL_KEY = 'musiflix.volumen'
const loadVol = () => {
  try { const v = Number(window.localStorage.getItem(VOL_KEY)); if (v >= 0 && v <= 1 && window.localStorage.getItem(VOL_KEY) !== null) return v } catch { /* sin storage */ }
  return 0.8
}
const saveVol = (v) => { try { window.localStorage.setItem(VOL_KEY, String(v)) } catch { /* solo esta sesión */ } }

const IFRAME_ENGINES = ['youtube', 'soundcloud']

/* Estado y motores del reproductor. La barra (components/PlayerBar.jsx) es solo la vista:
   todo lo que suena pasa por acá, así hay UN reproductor para toda la app y no se corta al
   navegar (vive arriba de las pantallas, en main.jsx). */
export default function PlayerProvider({ children }) {
  const [queue, setQueue] = useState([])
  const [index, setIndex] = useState(-1)
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState(null)
  const [clock, setClock] = useState({ time: 0, duration: null })
  const [volume, setVol] = useState(loadVol)
  const [muted, setMuted] = useState(false)
  const [engineKind, setEngineKind] = useState(null)   // motor activo (para mostrar el monitor)
  const ytHost = useRef(null)
  const scHost = useRef(null)
  const monitorRef = useRef(null)
  const engines = useRef({})
  const active = useRef(null)
  const saltos = useRef(0)   // temas salteados seguidos por error durante el avance automático
  // Última foto del estado para las funciones estables (se crean una vez, leen de acá).
  const S = useRef({})
  S.current = { queue, index, status, clock, volume, muted }

  const api = useMemo(() => {
    const current = () => S.current.queue[S.current.index] || null
    const cbFor = (name) => ({
      onState: (s, msg) => {
        if (active.current !== name) return            // un motor que ya no es el activo
        if (s === 'ended') { onEnded(); return }
        // Si falla un tema al que se llegó SOLO (fin del anterior), se sigue con el próximo
        // en vez de cortar la música; si lo eligió el usuario, se muestra el error. Tope de
        // saltos seguidos para no recorrer una lista entera de temas rotos.
        if (s === 'error' && saltos.current > 0 && saltos.current < 5 && proximo() != null) {
          start(S.current.queue, proximo(), true)
          return
        }
        setStatus(s)
        if (s === 'error') setError(msg || 'No se pudo reproducir este tema.')
        if (s === 'playing') { setError(null); saltos.current = 0; exclusivo() }
      },
      onTime: (t, d) => {
        if (active.current !== name) return
        setClock({ time: t, duration: d || current()?.duracion || null })
      },
    })
    const engine = (name) => {
      if (!engines.current[name]) {
        if (name === 'audio') engines.current[name] = new AudioEngine(cbFor(name))
        if (name === 'youtube') engines.current[name] = new YouTubeEngine(ytHost.current, cbFor(name))
        if (name === 'soundcloud') engines.current[name] = new SoundCloudEngine(scHost.current, cbFor(name))
      }
      return engines.current[name]
    }
    // Un solo audio en toda la app: pausa cualquier otro <audio>/<video> de la página
    // (p. ej. la pantalla de radio) y avisa para que la app corte el preview del mouse.
    const exclusivo = () => {
      const propio = engines.current.audio?.a
      document.querySelectorAll('audio,video').forEach((m) => { if (m !== propio && !m.paused) m.pause() })
      window.dispatchEvent(new Event('musiflix:player-play'))
    }
    const start = (q, i, auto = false) => {
      const t = q[i]
      if (!t) return
      saltos.current = auto ? saltos.current + 1 : 0
      const kind = engineFor(t)
      const motor = ['audio', ...IFRAME_ENGINES].includes(kind) ? kind : null
      if (active.current && active.current !== motor) engines.current[active.current]?.destroy()
      active.current = motor
      setEngineKind(motor)
      setQueue(q); setIndex(i); setError(null)
      setClock({ time: 0, duration: t.duracion || null })
      if (kind === 'embed') { setStatus('embed'); return }
      if (!motor) {
        setStatus('none')
        setError(`No hay audio que se pueda reproducir desde acá para este tema${t.fuente ? ` (${NOMBRE_FUENTE[t.fuente] || t.fuente})` : ''}.`)
        return
      }
      setStatus('loading')
      exclusivo()
      const e = engine(motor)
      e.setVolume(S.current.muted ? 0 : S.current.volume)
      e.load(t)
    }
    // Fin del tema: sigue con el próximo que se pueda reproducir de la misma lista.
    // (Los botones anterior/siguiente sí pasan por todos, para que se vea qué hay.)
    const proximo = () => {
      const { queue: q, index: i } = S.current
      for (let j = i + 1; j < q.length; j++) if (playable(q[j])) return j
      return null
    }
    const onEnded = () => {
      const j = proximo()
      if (j != null) { start(S.current.queue, j, true); return }
      setStatus('ended')
    }
    const activeEngine = () => (active.current ? engines.current[active.current] : null)
    return {
      playQueue: (tracks, i = 0) => start(tracks, i),
      toggle: () => {
        const e = activeEngine()
        const st = S.current.status
        if (st === 'error') { start(S.current.queue, S.current.index); return }
        if (!e) return
        if (st === 'playing' || st === 'loading') { e.pause(); return }
        exclusivo()
        if (st === 'ended') e.seek(0)
        e.play()
      },
      next: () => { const { queue: q, index: i } = S.current; if (i + 1 < q.length) start(q, i + 1) },
      prev: () => {
        const { queue: q, index: i, clock: c } = S.current
        // Como en cualquier reproductor: pasados 3 s, "anterior" vuelve al principio del tema.
        if (c.time > 3 || i <= 0) { activeEngine()?.seek(0); return }
        start(q, i - 1)
      },
      seek: (s) => { activeEngine()?.seek(s); setClock((c) => ({ ...c, time: s })) },
      setVolume: (v) => {
        const x = Math.max(0, Math.min(1, v))
        setVol(x); saveVol(x); setMuted(false)
        activeEngine()?.setVolume(x)
      },
      toggleMute: () => {
        const m = !S.current.muted
        setMuted(m)
        activeEngine()?.setVolume(m ? 0 : S.current.volume)
      },
      close: () => {
        activeEngine()?.destroy()
        active.current = null
        setEngineKind(null); setQueue([]); setIndex(-1); setStatus('idle'); setError(null)
        setClock({ time: 0, duration: null })
      },
      // Otro audio de la página arrancó: la barra se pausa (no pelean dos audios).
      _yield: () => { if (['playing', 'loading'].includes(S.current.status)) activeEngine()?.pause() },
    }
  }, [])

  // Si arranca otro <audio>/<video> de la página, la barra cede. Captura: 'play' no burbujea.
  useEffect(() => {
    const onPlay = (e) => { if (e.target !== engines.current.audio?.a) api._yield() }
    document.addEventListener('play', onPlay, true)
    return () => document.removeEventListener('play', onPlay, true)
  }, [api])

  // Alto del monitor en --monitor-h (el contenido y los avisos se corren para no quedar abajo).
  const monitorVisible = IFRAME_ENGINES.includes(engineKind)
  useEffect(() => {
    const el = monitorRef.current
    const root = document.documentElement
    if (!el || !monitorVisible) { root.style.setProperty('--monitor-h', '0px'); return }
    const ro = new ResizeObserver(() => root.style.setProperty('--monitor-h', `${Math.ceil(el.getBoundingClientRect().height) + 8}px`))
    ro.observe(el)
    return () => { ro.disconnect(); root.style.setProperty('--monitor-h', '0px') }
  }, [monitorVisible])

  const current = queue[index] || null
  const value = useMemo(() => ({
    ...api, queue, index, current, status, error, volume, muted, engineKind,
    hasPrev: index > 0, hasNext: index >= 0 && index + 1 < queue.length,
    isPlaying: (key) => !!current && current.key === key && (status === 'playing' || status === 'loading'),
  }), [api, queue, index, current, status, error, volume, muted, engineKind])

  const fuenteMonitor = engineKind === 'youtube' ? 'YouTube' : 'SoundCloud'
  return (
    <PlayerCtx.Provider value={value}>
      <PlayerTimeCtx.Provider value={clock}>
        {children}
        {/* Monitor: el reproductor de la fuente, visible (YouTube pide que su reproductor
            embebido se vea; SoundCloud, que no se esconda). La barra lo controla por sus APIs. */}
        <aside className={`deck-monitor is-${engineKind || 'off'}`} ref={monitorRef} hidden={!monitorVisible}
          aria-label={`Reproductor de ${fuenteMonitor}`}>
          <div className="deck-monitor-head">
            <span className="deck-monitor-dot" aria-hidden="true" />
            <span>{engineKind === 'youtube' ? 'Video · YouTube' : 'SoundCloud'}</span>
            <span className="deck-monitor-note">lo controla la barra</span>
          </div>
          <div className="deck-monitor-yt" ref={ytHost} hidden={engineKind !== 'youtube'} />
          <div className="deck-monitor-sc" ref={scHost} hidden={engineKind !== 'soundcloud'} />
        </aside>
      </PlayerTimeCtx.Provider>
    </PlayerCtx.Provider>
  )
}
