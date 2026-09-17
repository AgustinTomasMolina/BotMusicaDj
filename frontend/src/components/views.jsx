import { useState, useEffect, useRef } from 'react'
import { getBiblioteca, audioUrl } from '../api'
import { fmtDur, FUENTE_CORTO, songKey, metaKey } from '../utils'
import { DlButton, PreviewLayer, QualityBadge } from './common'
import { AddToPlaylist } from './AddToPlaylist'
import { IconDownload, IconActivity, IconCompare, IconSparkles } from './icons'

// fuente → clase de plataforma de Nocturne (define el color --pf del chip)
const PF = { youtube: 'pf-yt', soundcloud: 'pf-sc', spotify: 'pf-sp', ligaudio: 'pf-m1', hitplayer: 'pf-m2', deezer: 'pf-sp' }

/* ---------- Pantalla de inicio ---------- */
const NoteIcon = () => (
  <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="7" cy="17" r="3" /><path d="M10 17V5l9-2v3l-9 2" /></svg>
)

// Tarjeta de un track de la biblioteca: carátula (con play/pausa), título, artista y ＋ a playlist.
function LibCard({ t, i, playing, onToggle }) {
  const key = t.camelot || t.tonalidad || ''
  const meta = [t.bpm ? String(t.bpm) : null, key || null].filter(Boolean).join(' · ')
  return (
    <div className="lib-card" style={{ animationDelay: `${Math.min(i, 12) * 0.04}s` }}>
      <div className={`lib-cover g${(i % 6) + 1}`}>
        {/* El nombre incluye el tema: 18 botones "Reproducir" iguales no dicen cuál suena. */}
        <button type="button" className={`lib-play${playing ? ' on' : ''}`}
          aria-label={`${playing ? 'Pausar' : 'Reproducir'} ${t.titulo}`} onClick={onToggle}>
          {playing
            ? <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" /><rect x="14" y="5" width="4" height="14" /></svg>
            : <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z" /></svg>}
        </button>
        {meta && <span className="lib-meta">{meta}</span>}
      </div>
      <div className="lib-title" title={t.titulo}>{t.titulo}</div>
      <div className="lib-artist" title={t.artista}>{t.artista || '—'}</div>
      <div className="lib-actions">
        <AddToPlaylist track={{ titulo: t.titulo, artista: t.artista, bpm: t.bpm, camelot: t.camelot, genero: t.genero }} />
      </div>
    </div>
  )
}

/* ---------- Pantalla de inicio: estantes por género desde la biblioteca local ---------- */
export function Home({ toast }) {
  const [data, setData] = useState(null)      // {total, configurada, generos:[{genero,tracks}]}
  const [gf, setGf] = useState('Todos')       // filtro de género
  const [playing, setPlaying] = useState(null) // id del track sonando
  const audioRef = useRef(null)

  useEffect(() => {
    let vivo = true
    getBiblioteca()
      .then((d) => {
        if (!vivo) return
        // Orden semi-aleatorio de los estantes: la home se siente distinta cada vez que entrás.
        const gs = [...(d.generos || [])].sort(() => Math.random() - 0.5)
        setData({ ...d, generos: gs })
      })
      // Sin conexión no es "sin configurar": se explica con motivo en vez de pedir variables.
      .catch(() => vivo && setData({ total: 0, configurada: false, generos: [],
        motivo: 'No pude conectar con el servidor para leer la biblioteca. Revisá que esté corriendo y recargá.' }))
    // El <audio> se monta DESPUÉS (cuando llegan los datos): el ref se lee al desmontar a propósito.
    return () => { vivo = false; if (audioRef.current) audioRef.current.pause() } // eslint-disable-line react-hooks/exhaustive-deps
  }, [])

  const toggle = (t) => {
    const a = audioRef.current
    if (!a) return
    if (playing === t.id) { a.pause(); setPlaying(null); return }
    a.src = audioUrl(t.id)
    a.play().then(() => setPlaying(t.id)).catch((e) => {
      // AbortError = se cambió de tema antes de que arrancara: no es un error para el usuario.
      if (e && e.name === 'AbortError') return
      // Antes: toast('…') con toast siendo un objeto → TypeError y el error nunca se mostraba.
      toast?.danger({ title: 'No pude reproducir ese audio', body: `«${t.titulo}»: puede que el archivo se haya movido o borrado.` })
      setPlaying(null)
    })
  }

  if (!data) return <div className="empty"><span className="spinner" aria-hidden="true" /><p>Cargando tu biblioteca…</p></div>
  if (!data.total) {
    return (
      <div className="empty">
        <div className="empty-art"><NoteIcon /></div>
        <h3>{data.motivo ? 'Biblioteca no disponible' : data.configurada ? 'Tu biblioteca está vacía' : 'Conectá tu biblioteca'}</h3>
        {/* motivo: el server explica por qué no pudo leerla (sin lector, XML ilegible). */}
        <p>{data.motivo
          ? `${data.motivo} Mientras tanto, buscá un tema arriba.`
          : data.configurada
            ? 'No encontré audios resueltos. Revisá las rutas de la biblioteca (MUSIFLIX_LIBRARY_ROOTS).'
            : 'Definí MUSIFLIX_LIBRARY_XML y MUSIFLIX_LIBRARY_ROOTS para ver tus temas por género acá. Mientras tanto, buscá un tema arriba.'}</p>
      </div>
    )
  }

  const chips = ['Todos', ...data.generos.map((s) => s.genero)]
  const shelves = gf === 'Todos' ? data.generos : data.generos.filter((s) => s.genero === gf)

  return (
    <div className="home">
      {/* onError: si el archivo falla a mitad de camino, el botón no queda en "Pausar". */}
      <audio ref={audioRef} onEnded={() => setPlaying(null)} onError={() => setPlaying(null)} preload="none" />
      <div className="home-head">
        <h1>Para arrancar</h1>
        <p className="muted">{data.total} temas en tu biblioteca · el orden cambia cada vez que entrás</p>
      </div>
      {/* Botones con aria-pressed: role="tab" sin tabpanel ni flechas prometía un teclado que no existía. */}
      <div className="genre-chips" role="group" aria-label="Filtrar por género">
        {chips.map((g) => (
          <button key={g} type="button" aria-pressed={gf === g}
            className={`chip${gf === g ? ' on' : ''}`} onClick={() => setGf(g)}>{g}</button>
        ))}
      </div>
      {shelves.map((shelf, si) => (
        <section className="shelf" style={{ animationDelay: `${Math.min(si, 8) * 0.06}s` }} key={shelf.genero}>
          <div className="shelf-head"><h2>{shelf.genero}</h2><span className="muted">{shelf.tracks.length}</span></div>
          <div className="shelf-row">
            {shelf.tracks.slice(0, 18).map((t, i) => (
              <LibCard key={t.id} t={t} i={i} playing={playing === t.id} onToggle={() => toggle(t)} />
            ))}
          </div>
        </section>
      ))}
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
        {/* Carátula decorativa: el título está al lado. */}
        {c.thumbnail ? <img src={c.thumbnail} alt="" loading="lazy" onError={(e) => { e.currentTarget.style.display = 'none' }} /> : <div className="thumb-ph" />}
        <button type="button" className="thumb-play" aria-label={`Reproducir ${c.titulo}`} onClick={(e) => { e.stopPropagation(); onPlay(c) }}>
          <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5l10 6.5-10 6.5z" fill="currentColor" /></svg>
        </button>
        {/* key por canción: al pasar a otra versión de la fila se monta una capa nueva (volumen y cue incluidos). */}
        {rowPrev && <PreviewLayer key={songKey(rowPrev)} song={rowPrev} />}
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
        <button type="button" className="btn btn-icon-sm" onClick={() => onSpek(c)} title="Espectrograma (Spek)" aria-label={`Espectrograma de ${c.titulo}`}><IconActivity size={16} /></button>
        {g.opciones.length > 1 &&
          <button type="button" className="btn btn-icon-sm" onClick={() => onCompare(g.opciones, g.consulta || c.titulo)} title="Comparar versiones" aria-label={`Comparar versiones de ${c.titulo}`}><IconCompare size={16} /></button>}
        {onParecidas &&
          <button type="button" className="btn btn-icon-sm" onClick={() => onParecidas(c)} title="Temas parecidos" aria-label={`Temas parecidos a ${c.titulo}`}><IconSparkles size={16} /></button>}
        <AddToPlaylist track={{ ...c, bpm, genero, camelot: key }} />
        <DlButton dl={dl} label={c.titulo} onClick={() => onDownload(c)}><IconDownload size={15} /></DlButton>
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
      {/* Sin encabezado visible en esta vista: uno oculto para navegar por títulos con lector. */}
      <h1 className="sr-only">{esBusqueda ? `Resultados${query ? ` de ${query}` : ''}` : seed ? `Parecidas a ${seed.titulo}` : 'Resultados de la lista'}</h1>
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
            {allBusy ? <><span className="spinner" aria-hidden="true" /> {allLabel}</> : (allLabel || `Descargar todas (${formato.toUpperCase()})`)}
          </button>
        </span>
        {/* El resumen final de "Descargar todas" se anuncia (el progreso ya sale en los avisos). */}
        <span className="sr-only" role="status">{!allBusy && allLabel ? allLabel.replace('✓ ', '') : ''}</span>
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
  const [err, setErr] = useState('')
  const areaRef = useRef(null)
  const lineas = text.split('\n').map((s) => s.trim()).filter(Boolean)
  const n = lineas.length
  const nums = Array.from({ length: Math.max(text.split('\n').length, 1) }, (_, i) => i + 1).join('\n')
  const submit = async () => {
    // Antes, con la lista vacía el botón no hacía nada y no decía por qué.
    if (!lineas.length) { setErr('Pegá al menos un tema (uno por línea) para buscar.'); areaRef.current?.focus(); return }
    setBusy(true)
    try { await onBuscar(text.trim()) } finally { setBusy(false) }
  }
  return (
    <div style={{ maxWidth: 680, margin: '0 auto', padding: '0 var(--space-3)' }}>
      <h1 className="sr-only">Modo lista</h1>
      <div className="eyebrow" style={{ marginBottom: 'var(--space-2)' }} id="lista-ayuda">Modo lista (DJ) — un tema por línea, hasta 3 versiones de cada uno</div>
      <div className="paste">
        <div className="paste-nums" aria-hidden="true">{nums}</div>
        <textarea ref={areaRef} spellCheck="false" value={text} onChange={(e) => { setText(e.target.value); if (err) setErr('') }}
          aria-label="Pegá tu lista de temas" aria-describedby={err ? 'lista-ayuda lista-error' : 'lista-ayuda'} aria-invalid={!!err}
          placeholder={'Skrillex - Bangarang\nDaft Punk - One More Time\nFisher - Losing It\n…'} />
      </div>
      {err && <p id="lista-error" role="alert" className="note-warn" style={{ margin: 'var(--space-2) 0 0' }}>{err}</p>}
      <div className="paste-foot">
        <span className="mono">{n} línea{n !== 1 ? 's' : ''}</span>
        <span className="push cluster" style={{ gap: 'var(--space-2)' }}>
          <button type="button" className="btn btn-ghost" onClick={onCancel}>Cancelar</button>
          <button type="button" className="btn btn-primary" onClick={submit} disabled={busy}>
            {busy ? <><span className="spinner" aria-hidden="true" /> Buscando…</> : `Buscar lista${formato ? ` (${formato.toUpperCase()})` : ''}`}
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
