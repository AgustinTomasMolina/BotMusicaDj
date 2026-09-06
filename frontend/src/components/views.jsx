import { useState } from 'react'
import { fmtDur, FUENTE_CORTO, songKey, metaKey } from '../utils'
import { DlButton, PreviewLayer, QualityBadge } from './common'
import { AddToPlaylist } from './AddToPlaylist'
import { IconDownload, IconActivity, IconCompare, IconSparkles } from './icons'

// fuente → clase de plataforma de Nocturne (define el color --pf del chip)
const PF = { youtube: 'pf-yt', soundcloud: 'pf-sc', spotify: 'pf-sp', ligaudio: 'pf-m1', hitplayer: 'pf-m2', deezer: 'pf-sp' }

/* ---------- Pantalla de inicio ---------- */
export function Home() {
  return (
    <div className="empty">
      <div className="empty-art">
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"><circle cx="7" cy="17" r="3" /><path d="M10 17V5l9-2v3l-9 2" /></svg>
      </div>
      <h3>Buscá tu próximo track</h3>
      <p>Escribí un tema, artista o género y compará la calidad de cada versión antes de bajar. O usá <b>Lista</b> (en el menú ☰) para pegar un set entero.</p>
    </div>
  )
}

/* ---------- Fila de un tema: 7 columnas Nocturne (.trk) ---------- */
function TrackRow({ g, i, sel, formato, metaMap, preview, dl, onPlay, onSpek, onDownload, onSelect, onCompare, onParecidas }) {
  const c = g.opciones[sel]
  const thumbKey = `t${i}`
  const rowPrev = preview.current && (preview.current.key === thumbKey || preview.current.key.startsWith(`o${i}:`))
    ? preview.current.song : null
  const m = metaMap[metaKey(c)] || {}
  const bpm = c.bpm || m.bpm
  const genero = c.genero || m.genero
  const key = c.camelot
  return (
    <div className="trk"
      onMouseEnter={() => preview.schedule(thumbKey, c)}
      onMouseLeave={() => { preview.cancel(); preview.stop() }}>
      <div className="trk-idx">{String(i + 1).padStart(2, '0')}</div>
      <div className="thumb" onClick={() => onPlay(c)}>
        {c.thumbnail ? <img src={c.thumbnail} loading="lazy" onError={(e) => { e.currentTarget.style.display = 'none' }} /> : <div className="thumb-ph" />}
        <button type="button" className="thumb-play" aria-label="Reproducir" onClick={(e) => { e.stopPropagation(); onPlay(c) }}>
          <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5l10 6.5-10 6.5z" fill="currentColor" /></svg>
        </button>
        {rowPrev && <PreviewLayer song={rowPrev} />}
      </div>
      <div className="trk-id">
        <div className="trk-title" title={c.titulo}>{c.titulo}</div>
        <div className="trk-artist"><span className="truncate">{c.artista}</span><span className="sep">·</span><span className="mono">{fmtDur(c.duracion)}</span></div>
      </div>
      <div className="trk-meta">
        {bpm && <span className="mb"><span className="mb-label">BPM</span><b>{bpm}</b></span>}
        {key && <span className="mb mb-key"><span className="mb-label">KEY</span><b>{key}{c.compat ? ' ' + c.compat : ''}</b></span>}
        {genero && <span className="mb"><b>{genero}</b></span>}
      </div>
      <div className="trk-grade"><QualityBadge c={c} formato={formato} /></div>
      <div className="trk-vers vchips">
        {g.opciones.map((o, k) => {
          const f = (o.fuente || '').toLowerCase()
          const okey = `o${i}:${k}`
          return (
            <button key={k} type="button" className={`vchip ${PF[f] || ''}`} aria-pressed={k === sel}
              onMouseEnter={() => preview.schedule(okey, o)}
              onClick={() => onSelect(i, k)}
              title={`Opción ${k + 1} · ${o.fuente} — ${o.titulo}`}>
              <span className="n">{k + 1}</span><span className="dot" />{FUENTE_CORTO[f] || o.fuente || '?'}
            </button>
          )
        })}
      </div>
      <div className="trk-acts">
        <button type="button" className="btn btn-icon-sm" onClick={() => onSpek(c)} title="Espectrograma (Spek)"><IconActivity size={16} /></button>
        {g.opciones.length > 1 &&
          <button type="button" className="btn btn-icon-sm" onClick={() => onCompare(g.opciones, g.consulta || c.titulo)} title="Comparar versiones"><IconCompare size={16} /></button>}
        {onParecidas &&
          <button type="button" className="btn btn-icon-sm" onClick={() => onParecidas(c)} title="Temas parecidos"><IconSparkles size={16} /></button>}
        <AddToPlaylist track={{ ...c, bpm, genero, camelot: key }} />
        <DlButton dl={dl} onClick={() => onDownload(c)}><IconDownload size={15} /></DlButton>
      </div>
    </div>
  )
}

/* ---------- Vista de resultados (búsqueda unificada / modo lista / parecidas) ---------- */
export function ListResults({ data, formato, metaMap, preview, dl, onPlay, onSpek, onDownload, onSelect, onEditar, onCompare, onParecidas }) {
  const { groups, sel, seed, encontradas, total, no_encontradas, origen, query } = data
  const esBusqueda = origen === 'busqueda'
  const [allLabel, setAllLabel] = useState(null)
  const [allBusy, setAllBusy] = useState(false)

  const descargarTodas = async () => {
    setAllBusy(true)
    let ok = 0
    for (let i = 0; i < groups.length; i++) {
      const c = groups[i].opciones[sel[i]]
      setAllLabel(`Bajando ${i + 1}/${groups.length}…`)
      const good = await onDownload(c)
      if (good) ok++
    }
    setAllBusy(false)
    setAllLabel(`✓ ${ok}/${groups.length} descargadas`)
  }

  return (
    <>
      {seed && (
        <div className="seedbar" style={{ margin: '0 var(--space-3) var(--space-3)' }}>
          <div style={{ minWidth: 0 }}>
            <div className="eyebrow">Parecidas a</div>
            <h4 className="truncate">{seed.titulo} — {seed.artista}</h4>
            <div className="cluster" style={{ gap: 5, marginTop: 6 }}>
              {seed.bpm && <span className="mb"><span className="mb-label">BPM</span><b>{seed.bpm}</b></span>}
              {seed.camelot && <span className="mb mb-key"><span className="mb-label">KEY</span><b>{seed.camelot}</b></span>}
              {seed.tono && <span className="mb"><span className="mb-label">Tono</span><b>{seed.tono}</b></span>}
            </div>
          </div>
        </div>
      )}

      <div className="cluster" style={{ padding: '0 var(--space-3) var(--space-3)' }}>
        <span className="eyebrow">{esBusqueda ? `Resultados${query ? ` · ${query}` : ''}` : `${encontradas}/${total} encontradas`}</span>
        <span className="push cluster" style={{ gap: 'var(--space-2)' }}>
          {!esBusqueda && <button type="button" className="btn btn-ghost" onClick={onEditar}>Editar lista</button>}
          <button type="button" className="btn btn-primary" onClick={descargarTodas} disabled={allBusy}>
            {allBusy ? <><span className="spinner" /> {allLabel}</> : (allLabel || `Descargar todas (${formato.toUpperCase()})`)}
          </button>
        </span>
      </div>

      {no_encontradas && no_encontradas.length > 0 && (
        <div className="alert alert-warn" style={{ margin: '0 var(--space-3) var(--space-3)' }}>
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 4l9 16H3z" /><path d="M12 10v4.5" /><circle cx="12" cy="17.4" r=".9" fill="currentColor" stroke="none" /></svg>
          <div><div className="alert-title">Sin resultado ({no_encontradas.length})</div><p>{no_encontradas.join(' · ')}</p></div>
        </div>
      )}

      <div className="results">
        <div className="results-head">
          <div style={{ textAlign: 'right' }}>#</div>
          <div>Art</div>
          <div>Tema / artista</div>
          <div className="col-meta">Metadata</div>
          <div>Nota</div>
          <div>Versiones</div>
          <div style={{ textAlign: 'right' }}>Acciones</div>
        </div>
        {groups.map((g, i) => (
          <TrackRow key={i} g={g} i={i} sel={sel[i]} formato={formato} metaMap={metaMap} preview={preview}
            dl={dl[songKey(g.opciones[sel[i]])]} onPlay={onPlay} onSpek={onSpek} onDownload={onDownload}
            onSelect={onSelect} onCompare={onCompare} onParecidas={onParecidas} />
        ))}
      </div>
    </>
  )
}

/* ---------- Modo lista: pegar tracks (área .paste) ---------- */
export function ListForm({ formato, onBuscar, onCancel }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const lineas = text.split('\n').map((s) => s.trim()).filter(Boolean)
  const n = lineas.length
  const nums = Array.from({ length: Math.max(text.split('\n').length, 1) }, (_, i) => i + 1).join('\n')
  const submit = async () => {
    if (!lineas.length) return
    setBusy(true)
    try { await onBuscar(text.trim()) } finally { setBusy(false) }
  }
  return (
    <div style={{ maxWidth: 680, margin: '0 auto', padding: '0 var(--space-3)' }}>
      <div className="eyebrow" style={{ marginBottom: 'var(--space-2)' }}>Modo lista (DJ) — un tema por línea, hasta 3 versiones de cada uno</div>
      <div className="paste">
        <div className="paste-nums">{nums}</div>
        <textarea spellCheck="false" value={text} onChange={(e) => setText(e.target.value)} aria-label="Pegá tu lista de temas"
          placeholder={'Skrillex - Bangarang\nDaft Punk - One More Time\nFisher - Losing It\n…'} />
      </div>
      <div className="paste-foot">
        <span className="mono">{n} línea{n !== 1 ? 's' : ''}</span>
        <span className="push cluster" style={{ gap: 'var(--space-2)' }}>
          <button type="button" className="btn btn-ghost" onClick={onCancel}>Cancelar</button>
          <button type="button" className="btn btn-primary" onClick={submit} disabled={busy}>
            {busy ? <><span className="spinner" /> Buscando…</> : `Buscar lista${formato ? ` (${formato.toUpperCase()})` : ''}`}
          </button>
        </span>
      </div>
    </div>
  )
}

/* ResultsView: la búsqueda ahora se enruta a ListResults (vista unificada por tema).
   Se mantiene el export por compatibilidad con App.jsx; delega en ListResults. */
export function ResultsView(props) {
  return <ListResults {...props} />
}
