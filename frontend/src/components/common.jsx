import { useEffect, useRef, useState } from 'react'
import { metaKey, songKey, cuePoint } from '../utils'
import { calidad as fetchCalidad } from '../api'

/* ---------- Nota de calidad (A/B/C/D/F) con carga lazy ----------
   Analizar la calidad real cuesta (resolver yt-dlp / decodificar audio), así que:
   - se cachea por canción a nivel de módulo (una sola vez por track/versión), y
   - se limita a 3 análisis en paralelo (cola), para no martillar YouTube. */
const _calCache = new Map()
let _active = 0
const _queue = []
function _pump() {
  while (_active < 3 && _queue.length) {
    const job = _queue.shift()
    _active++
    job.fn().then((r) => job.resolve(r), () => job.resolve(null)).finally(() => { _active--; _pump() })
  }
}
function _limited(fn) { return new Promise((resolve) => { _queue.push({ fn, resolve }); _pump() }) }

// Fetch de calidad cacheado + con límite de concurrencia. Compartido por el badge
// y el comparador de versiones, para no analizar dos veces la misma canción.
export function getCalidad(c) {
  const key = songKey(c)
  if (_calCache.has(key)) return Promise.resolve(_calCache.get(key))
  return _limited(() => fetchCalidad(c)).then((r) => { if (r) _calCache.set(key, r); return r })
}

// Ranking de notas para elegir "la mejor" versión.
export const GRADE_RANK = { A: 6, 'A-': 5, B: 4, C: 3, D: 2, F: 1, '?': 0 }

// Nota (A/A-/B/C/D/F) → clase de color de Nocturne (.grade-a … -f).
const _GC = { A: 'grade-a', 'A-': 'grade-am', B: 'grade-b', C: 'grade-c', D: 'grade-d', F: 'grade-f' }
export function gradeClass(g) { return _GC[g] || '' }

const _LOSSLESS = ['wav', 'flac', 'aiff']

// Íconos chicos inline (Phosphor-ish) para el botón de descarga.
const IcoCheck = () => <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 13l4.5 4.5L19 7" /></svg>
const IcoRetry = () => <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 12a8 8 0 11-2.6-5.9M20 4v4h-4" /></svg>
const IcoWarn = () => <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 4l9 16H3z" /><path d="M12 10v4" /><circle cx="12" cy="17" r=".9" fill="currentColor" stroke="none" /></svg>

export function QualityBadge({ c, formato }) {
  const key = songKey(c)
  const [res, setRes] = useState(() => _calCache.get(key) || null)
  useEffect(() => {
    let alive = true
    getCalidad(c).then((r) => { if (alive) setRes(r) })
    return () => { alive = false }
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!res) return <span className="grade grade-sm" style={{ opacity: 0.5 }} title="Analizando calidad real…">…</span>
  if (!res.ok) return <span className="grade grade-sm" title="No pude analizar la calidad de este tema">?</span>

  const fmt = (formato || 'mp3').toLowerCase()
  const fakeLossless = res.lossy && _LOSSLESS.includes(fmt)
  const title = `Calidad real: ${res.grade} — ${res.calidad}` +
    (fakeLossless ? ` · ⚠️ la fuente es lossy: bajar en ${fmt.toUpperCase()} NO mejora la calidad (fake lossless)` : '')
  return (
    <>
      <span className={`grade grade-sm ${gradeClass(res.grade)}`} title={title}>{res.grade}</span>
      {fakeLossless && <span className="note-warn" title={title}><IcoWarn /></span>}
    </>
  )
}

/* Metadata de una canción como pastillas Nocturne (.mb): nota de calidad, BPM, key,
   género, formato y fuente (BPM/género lazy vía metaMap). */
export function Badges({ c, formato, metaMap, calidad }) {
  const src = (c.fuente || '').toLowerCase()
  const m = metaMap[metaKey(c)] || {}
  const bpm = c.bpm || m.bpm
  const bpmDone = c.bpm || m.done
  const genero = c.genero || m.genero
  const genDone = c.genero || m.done
  const fijoMp3 = src === 'ligaudio' || src === 'hitplayer'
  const fmt = fijoMp3 ? 'mp3' : (formato || 'mp3').toLowerCase()
  const fmtVal = fmt.toUpperCase()
  const lossless = _LOSSLESS.includes(fmt)
  return (
    <>
      <QualityBadge c={c} formato={formato} />
      {bpm
        ? <span className="mb" title="BPM"><span className="mb-label">BPM</span><b>{bpm}</b></span>
        : (!bpmDone && <span className="mb" title="Calculando BPM…"><span className="mb-label">BPM</span><b>…</b></span>)}
      {c.camelot &&
        <span className="mb mb-key" title="Key — compatibilidad de mezcla"><span className="mb-label">KEY</span><b>{c.camelot}{c.compat ? ' ' + c.compat : ''}</b></span>}
      {genero
        ? <span className="mb" title="Género"><b>{genero}</b></span>
        : (!genDone && <span className="mb" title="Buscando género…"><b>…</b></span>)}
      <span className={`mb${lossless ? ' mb-lossless' : ''}`} title={fijoMp3 ? 'Esta fuente baja siempre en MP3' : 'Formato que se va a descargar'}><b>{fmtVal}</b></span>
      <span className="mb" title="Fuente"><b>{c.fuente}</b></span>
      {calidad && calidad.grade &&
        <span className={`grade grade-sm ${gradeClass(calidad.grade)}`} title={`Calidad real — ${calidad.metodo || ''}`}>{calidad.grade}</span>}
    </>
  )
}

/* Botón de descarga con el ciclo de estados de Nocturne (reposo / busy con anillo /
   done ✓ / fail). App manda dl.state = busy|ok|err; lo mapeamos a busy|done|fail. */
export function DlButton({ dl, onClick, children, className }) {
  const st = dl?.state
  const nocturneState = st === 'ok' ? 'done' : st === 'err' ? 'fail' : st
  let content = children
  if (st === 'busy') content = <span className="spinner" />
  else if (st === 'ok') content = <IcoCheck />
  else if (st === 'err') content = <IcoRetry />
  return (
    <button className={`btn btn-secondary btn-dl ${className || ''}`} data-state={nocturneState}
      onClick={onClick} title={dl?.title || 'Descargar'} aria-label="Descargar">
      {content}
    </button>
  )
}

/* Capa de preview (autoplay) que se monta sobre un thumbnail Nocturne (.thumb).
   Arranca cerca del DROP (no en la intro): YouTube con start=, audio con currentTime,
   SoundCloud con seekTo (best-effort). Los snippets de 30s (Deezer) ya son la parte
   buena → cue=0. Renderiza en .thumb-prev + el sello .now (SONANDO). */
export function PreviewLayer({ song: c }) {
  const ref = useRef(null)
  const cue = cuePoint(c)
  let node = null, seekAudio = 0
  const media = { width: '100%', height: '100%', border: 0, display: 'block', objectFit: 'cover' }
  if (c.fuente === 'youtube' && c.video_id) {
    node = <iframe data-yt="1" style={media} src={`https://www.youtube.com/embed/${c.video_id}?autoplay=1&controls=0&modestbranding=1&rel=0&enablejsapi=1&start=${cue}`} allow="autoplay" />
  } else if (c.fuente === 'soundcloud' && c.video_id) {
    const tk = encodeURIComponent('https://api.soundcloud.com/tracks/' + c.video_id)
    node = <iframe data-sc="1" style={media} src={`https://w.soundcloud.com/player/?url=${tk}&auto_play=true&visual=false&hide_related=true&buying=false&sharing=false&download=false&show_comments=false`} allow="autoplay" />
  } else if (c.stream_url) {
    seekAudio = cue
    node = <audio autoPlay src={c.stream_url} />
  } else if (c.preview_url) {
    node = <audio autoPlay src={c.preview_url} /> // snippet de 30s → ya es la parte buena
  } else {
    return null
  }

  useEffect(() => {
    const root = ref.current
    if (!root) return
    const a = root.querySelector('audio')
    if (a) {
      a.volume = 0.55
      if (seekAudio) {
        const seek = () => { try { if (seekAudio < (a.duration || Infinity)) a.currentTime = seekAudio } catch { /* ignore */ } }
        a.addEventListener('loadedmetadata', seek, { once: true })
        if (a.readyState >= 1) seek()
      }
    }
    const yt = root.querySelector('iframe[data-yt]')
    if (yt) yt.addEventListener('load', () => { try { yt.contentWindow.postMessage('{"event":"command","func":"setVolume","args":[55]}', '*') } catch { /* ignore */ } })
    const sc = root.querySelector('iframe[data-sc]')
    if (sc) sc.addEventListener('load', () => {
      try {
        sc.contentWindow.postMessage(JSON.stringify({ method: 'setVolume', value: 55 }), '*')
        if (cue) setTimeout(() => { try { sc.contentWindow.postMessage(JSON.stringify({ method: 'seekTo', value: cue * 1000 }), '*') } catch { /* ignore */ } }, 350)
      } catch { /* ignore */ }
    })
  }, [])

  return (
    <>
      <span className="thumb-prev" ref={ref} style={{ opacity: 1 }}>{node}</span>
      <span className="now"><span className="now-dot" />SONANDO</span>
    </>
  )
}

/* Fila con scroll horizontal (queda del layout viejo; el nuevo layout usa filas .trk,
   pero se conserva para no romper importaciones). Shift+rueda o gesto horizontal mueve
   la fila; la rueda vertical normal scrollea la página. */
export function RowScroll({ children }) {
  const ref = useRef(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const onWheel = (e) => {
      if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        el.scrollLeft += (e.deltaX || e.deltaY)
        e.preventDefault()
      }
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])
  return <div className="row-scroll" ref={ref}>{children}</div>
}
