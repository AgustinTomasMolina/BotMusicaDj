import { useEffect, useRef, useState } from 'react'
import { spectroUrl } from '../api'
import { SRC_COLOR, FUENTE_CORTO } from '../utils'
import { getCalidad, GRADE_RANK, gradeClass } from './common'

const IcoX = () => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
const IcoDown = () => <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 4v10M8 11l4 4 4-4M5 19h14" /></svg>
const IcoCheck = () => <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 13l4.5 4.5L19 7" /></svg>

/* Reproductor embebido (YouTube/Spotify/SoundCloud o audio directo). */
function Player({ song: c }) {
  const ref = useRef(null)
  useEffect(() => {
    const b = ref.current
    if (!b) return
    const a = b.querySelector('audio'); if (a) a.volume = 0.5
    const yt = b.querySelector('iframe[data-yt]')
    if (yt) yt.addEventListener('load', () => { try { yt.contentWindow.postMessage('{"event":"command","func":"setVolume","args":[50]}', '*') } catch { /* ignore */ } })
    const sc = b.querySelector('iframe[data-sc]')
    if (sc) sc.addEventListener('load', () => { try { sc.contentWindow.postMessage(JSON.stringify({ method: 'setVolume', value: 50 }), '*') } catch { /* ignore */ } })
  }, [c])

  let node
  if (c.fuente === 'youtube' && c.video_id) {
    node = <div className="player-frame"><iframe data-yt="1" src={`https://www.youtube.com/embed/${c.video_id}?autoplay=1&enablejsapi=1`} allow="autoplay; encrypted-media" allowFullScreen /></div>
  } else if (c.fuente === 'spotify' && c.id) {
    node = <iframe style={{ width: '100%', height: 152, border: 0, borderRadius: 'var(--radius-md)' }} src={`https://open.spotify.com/embed/track/${c.id}`} />
  } else if (c.fuente === 'soundcloud' && c.video_id) {
    const tk = encodeURIComponent('https://api.soundcloud.com/tracks/' + c.video_id)
    node = <iframe data-sc="1" style={{ width: '100%', height: 166, border: 0, borderRadius: 'var(--radius-md)' }} src={`https://w.soundcloud.com/player/?url=${tk}&auto_play=true&hide_related=true&color=%23ff5500`} />
  } else if (c.stream_url) {
    node = <audio controls autoPlay src={c.stream_url} style={{ width: '100%' }} />
  } else if (c.preview_url) {
    node = <audio controls autoPlay src={c.preview_url} style={{ width: '100%' }} />
  } else {
    node = <p className="text-muted">▶ <a href={c.url} target="_blank" rel="noreferrer">Abrir en {c.fuente}</a></p>
  }
  return <div ref={ref}>{node}</div>
}

/* Espectrograma (Spek) del audio real, con spinner y fallback de error. */
function SpekViewer({ song: c }) {
  const [state, setState] = useState('loading') // loading | ok | err
  return (
    <>
      {state === 'loading' && <div className="note-warn" style={{ color: 'var(--color-neutral-500)' }}><span className="spinner" /> Analizando el audio real… (puede tardar unos segundos)</div>}
      {state === 'err' && <div className="note-warn">No pude generar el espectrograma de este tema.</div>}
      <figure className="spectro" style={{ display: state === 'ok' ? 'block' : 'none' }}>
        <img src={spectroUrl(c)} alt="Espectrograma" onLoad={() => setState('ok')} onError={() => setState('err')} />
      </figure>
      <p className="text-muted" style={{ fontSize: 12, lineHeight: 1.5, margin: 0 }}>
        El eje vertical es la frecuencia. Fijate hasta qué altura llega el color: si corta abajo de ~16 kHz es un MP3
        pobre o un rip de YouTube; si llega limpio hasta ~19-20 kHz, es buena calidad para tocar.
      </p>
    </>
  )
}

/* Una columna del comparador: fuente + nota grande + espectrograma + descargar. */
function CompareColumn({ song: o, best, onDownload }) {
  const [cal, setCal] = useState(null)
  const [spek, setSpek] = useState('loading') // loading | ok | err
  const [dl, setDl] = useState(null)          // null | busy | ok | err
  const f = (o.fuente || '').toLowerCase()
  useEffect(() => { let a = true; getCalidad(o).then((r) => { if (a) setCal(r) }); return () => { a = false } }, [o])

  const bajar = async () => {
    setDl('busy')
    try { setDl((await onDownload(o)) ? 'ok' : 'err') } catch { setDl('err') }
  }
  const dlState = dl === 'ok' ? 'done' : dl === 'err' ? 'fail' : dl
  return (
    <div className={`cmp-col${best ? ' is-best' : ''}`}>
      <div className="cluster" style={{ gap: 'var(--space-2)' }}>
        <span className="cmp-src"><span className="dot" style={{ width: 8, height: 8, borderRadius: '50%', background: SRC_COLOR[f] || 'var(--color-neutral-500)' }} />{FUENTE_CORTO[f] || o.fuente || '?'}</span>
        {best && <span className="cmp-best-flag push"><IcoCheck />mejor</span>}
      </div>
      <div><span className={`grade grade-lg ${gradeClass(cal?.grade)}`}>{cal?.grade || '…'}</span></div>
      <figure className="spectro">
        {spek === 'loading' && <div className="note-warn" style={{ color: 'var(--color-neutral-500)', padding: 'var(--space-3)' }}><span className="spinner" /></div>}
        {spek === 'err' && <div className="note-warn" style={{ padding: 'var(--space-3)' }}>sin espectrograma</div>}
        <img style={{ display: spek === 'ok' ? 'block' : 'none' }} src={spectroUrl(o)} alt="Espectrograma"
          onLoad={() => setSpek('ok')} onError={() => setSpek('err')} />
      </figure>
      <div className="text-muted" style={{ fontSize: 12 }} title={cal?.calidad || ''}>{cal ? (cal.calidad || 'no analizable') : 'analizando…'}</div>
      <button className="btn btn-secondary btn-block btn-dl" data-state={dlState} onClick={bajar} disabled={dl === 'busy'}>
        {dl === 'busy' ? <><span className="spinner" /> Bajando</> : dl === 'ok' ? <><IcoCheck /> Descargada</> : dl === 'err' ? '✗ Reintentar' : <><IcoDown /> Bajar esta</>}
      </button>
    </div>
  )
}

/* Comparador lado a lado de las versiones (opciones por plataforma) de un track. */
function Comparator({ options, onDownload }) {
  const [cals, setCals] = useState({}) // idx → calidad (para marcar la mejor)
  useEffect(() => {
    let alive = true
    options.forEach((o, i) => getCalidad(o).then((r) => { if (alive) setCals((p) => ({ ...p, [i]: r })) }))
    return () => { alive = false }
  }, [options])

  let bestIdx = -1, bestRank = -1
  options.forEach((o, i) => {
    const g = cals[i]?.grade
    const r = g ? (GRADE_RANK[g] ?? 0) : -1
    if (r > bestRank) { bestRank = r; bestIdx = i }
  })

  return (
    <>
      <div className="cmp">
        {options.map((o, i) => <CompareColumn key={i} song={o} best={i === bestIdx && bestRank > 0} onDownload={onDownload} />)}
      </div>
      <p className="text-muted" style={{ fontSize: 12, lineHeight: 1.5, marginTop: 'var(--space-3)' }}>
        Cada columna es una <b>versión del mismo tema</b> en otra plataforma. Mirá hasta qué altura llega el color del
        espectrograma y quedate con la de mayor nota (marcada <b>mejor</b>). Bajá esa.
      </p>
    </>
  )
}

export default function Modal({ modal, onClose, onDownload }) {
  if (!modal) return null
  const c = modal.song
  const stop = (e) => { if (e.target === e.currentTarget) onClose() }

  // Comparador: hoja ancha
  if (modal.kind === 'compare') {
    return (
      <div className="dialog-backdrop" onClick={stop}>
        <div className="sheet elev-lg">
          <div className="sheet-head rule-b">
            <div style={{ minWidth: 0 }}><div className="eyebrow">Comparar versiones</div><h4 style={{ margin: '2px 0 0' }} className="truncate">{modal.titulo}</h4></div>
            <button className="btn btn-icon btn-icon-sm push" onClick={onClose} aria-label="Cerrar"><IcoX /></button>
          </div>
          <div className="sheet-body">
            <Comparator options={modal.options} onDownload={onDownload} />
          </div>
        </div>
      </div>
    )
  }

  // Spek: diálogo mediano
  if (modal.kind === 'spek') {
    return (
      <div className="dialog-backdrop" onClick={stop}>
        <div className="dialog elev-lg" style={{ width: 'min(620px, 100%)' }}>
          <div className="cluster" style={{ flexWrap: 'nowrap' }}>
            <div style={{ minWidth: 0 }}><div className="eyebrow">Spek</div><div className="dialog-title truncate" style={{ marginTop: 2, fontSize: 18 }}>{c.titulo} <span className="text-muted">— {c.artista}</span></div></div>
            <button className="btn btn-icon btn-icon-sm push" onClick={onClose} aria-label="Cerrar"><IcoX /></button>
          </div>
          <SpekViewer song={c} />
        </div>
      </div>
    )
  }

  // Reproductor
  return (
    <div className="dialog-backdrop" onClick={stop}>
      <div className="sheet player">
        <div className="sheet-head rule-b">
          <span className="thumb" style={{ width: 34, height: 34 }}>{c.thumbnail ? <img src={c.thumbnail} alt="" /> : <span className="thumb-ph" />}</span>
          <div style={{ minWidth: 0 }}><div className="trk-title truncate">{c.titulo}</div><div className="trk-artist"><span className="truncate">{c.artista}</span></div></div>
          <button className="btn btn-icon btn-icon-sm push" onClick={onClose} aria-label="Cerrar"><IcoX /></button>
        </div>
        <div className="sheet-body">
          <Player song={c} />
        </div>
      </div>
    </div>
  )
}
