import { useEffect, useRef, useState } from 'react'
import { FORMATOS, GENEROS, normalizeText } from '../utils'
import { IconHome, IconEar, IconEarOff, IconList, IconHistory, IconTerminal } from './icons'

/* Filtro de género: buscador + lista con scroll + escribir uno propio. Sesga la búsqueda
   hacia ese estilo. El género elegido queda arriba como chip aunque el filtro lo oculte. */
function GenrePicker({ genero, setGenero }) {
  const [open, setOpen] = useState(false)
  const [txt, setTxt] = useState('')
  const ref = useRef(null)
  useEffect(() => {
    // isConnected: al quitar el chip del género, el botón clickeado ya se desmontó y no
    // debe contar como "click afuera" (si no, el menú se cerraría solo).
    const onDoc = (e) => { if (ref.current && e.target.isConnected && !ref.current.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('click', onDoc)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('click', onDoc); document.removeEventListener('keydown', onKey) }
  }, [])
  const pick = (g) => { setGenero(g); setTxt(''); setOpen(false) }
  const query = normalizeText(txt)
  const matches = query ? GENEROS.filter((g) => normalizeText(g).includes(query)) : GENEROS
  // Enter: si el texto coincide exacto con un preset (o hay una sola coincidencia) usa ese;
  // si no, busca por el género tal cual lo escribió el usuario.
  const submitTxt = (e) => {
    e.preventDefault()
    const t = txt.trim()
    if (!t) return
    const exact = GENEROS.find((g) => normalizeText(g) === query)
    pick(exact || (matches.length === 1 ? matches[0] : t))
  }
  return (
    <div style={{ position: 'relative' }} ref={ref}>
      <button type="button" className="btn btn-secondary" aria-expanded={open}
        title="Filtrar por género" onClick={(e) => { e.stopPropagation(); setOpen((o) => !o) }}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"><path d="M4 8h10M18 8h2M4 16h4M12 16h8" /><circle cx="16" cy="8" r="2" /><circle cx="10" cy="16" r="2" /></svg>
        {genero || 'Género'}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"><path d="M6 9.5l6 6 6-6" /></svg>
      </button>
      {open && (
        <div className="menu genre-menu elev-md" style={{ position: 'absolute', top: 'calc(100% + 6px)', right: 0, zIndex: 45 }}>
          <div className="menu-label eyebrow">Filtrar por género</div>
          {genero && (
            <div className="genre-selected">
              <button type="button" className="chip" aria-pressed="true" title="Quitar el filtro de género"
                aria-label={`Quitar género ${genero}`} onClick={() => setGenero('')}>
                {genero}<span aria-hidden="true">✕</span>
              </button>
            </div>
          )}
          <form style={{ padding: '0 var(--space-1) var(--space-1)' }} onSubmit={submitTxt}>
            <input className="input" value={txt} onChange={(e) => setTxt(e.target.value)} autoFocus
              placeholder="Buscá o escribí un género…" autoComplete="off" aria-label="Buscar género" />
          </form>
          <div className="genre-list" role="group" aria-label="Géneros">
            {!query && <button type="button" className="menu-item" aria-checked={!genero} onClick={() => pick('')}>Todos</button>}
            {matches.map((g) => (
              <button type="button" key={g} className="menu-item" aria-checked={g === genero} onClick={() => pick(g)}>{g}</button>
            ))}
            {query && !matches.length && (
              <div className="genre-empty">Sin géneros para «{txt.trim()}». Enter para buscar igual.</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

/* Selector segmentado de formato de descarga: siempre a la vista (no en un menú). */
function FormatSelector({ formato, setFormato }) {
  return (
    <div className="seg seg-fmt" role="radiogroup" aria-label="Formato de descarga">
      {FORMATOS.map((f) => (
        <label key={f.val} className="seg-opt" title={f.desc}>
          <input type="radio" name="formato-descarga" value={f.val}
            checked={f.val === formato} onChange={() => setFormato(f.val)} />
          <b>{f.val.toUpperCase()}</b><span className="seg-sub">{f.tag}</span>
        </label>
      ))}
    </div>
  )
}

/* Switch de preview: checkbox real con estado evidente (relleno + texto + oído). */
function PreviewSwitch({ enabled, onToggle }) {
  return (
    <label className={`switch${enabled ? ' is-on' : ''}`}
      title="Al pasar el mouse sobre un tema suena un fragmento">
      <input type="checkbox" role="switch" checked={enabled} aria-checked={enabled} onChange={onToggle} />
      <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
      {enabled ? <IconEar size={16} /> : <IconEarOff size={16} />}
      <span>{enabled ? 'Preview activada' : 'Preview desactivada'}</span>
    </label>
  )
}

const IconCrate = ({ size = 17 }) => <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 2 2 7l10 5 10-5z" /><path d="M2 12l10 5 10-5" /><path d="M2 17l10 5 10-5" /></svg>

export default function TopBar({ formato, setFormato, onSearch, previewEnabled, togglePreview, onLista, onConsola, consoleActive, onHistorial, historialActive, onBrand, genero, setGenero, onPlaylists, activePlaylist }) {
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const submit = async (e) => {
    e.preventDefault()
    const t = q.trim()
    if (!t && !genero) return   // permite buscar solo por género
    setBusy(true)
    try { await onSearch(t) } finally { setBusy(false) }
  }
  const cerrar = () => setMenuOpen(false)
  const item = (Icon, label, onClick, active) => (
    <button type="button" className="navitem" aria-current={active ? 'page' : undefined} onClick={onClick}>
      <Icon size={17} />{label}
    </button>
  )
  return (
    <>
      <div className="topbar-stack">
        <header className="topbar rule-b">
          <button type="button" className="btn btn-icon btn-icon-sm" aria-label="Menú" aria-expanded={menuOpen}
            onClick={() => setMenuOpen((o) => !o)}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"><path d="M4 7h16M4 12h16M4 17h16" /></svg>
          </button>
          <button type="button" className="brand" onClick={onBrand} title="Inicio" style={{ background: 'none', border: 0, cursor: 'pointer' }}>
            <span className="brand-mark"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><circle cx="7" cy="17" r="3" /><path d="M10 17V5l9-2v3l-9 2" /></svg></span>
            <span>Musi<em>Flix</em></span>
          </button>
          <form className="search" onSubmit={submit}>
            <span className="search-icon">
              {busy ? <span className="spinner" /> : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.6-3.6" /></svg>}
            </span>
            <input className="input" value={q} onChange={(e) => setQ(e.target.value)} type="search"
              placeholder="Buscá una canción, artista o género…" autoComplete="off" />
          </form>
          <GenrePicker genero={genero} setGenero={setGenero} />
          <button type="button" className="chip" title={activePlaylist ? `Agregando descargas a "${activePlaylist.nombre}"` : 'Mis playlists'}
            aria-pressed={!!activePlaylist} onClick={onPlaylists} style={{ gap: 6 }}>
            <IconCrate size={14} />{activePlaylist ? activePlaylist.nombre : 'Playlists'}
          </button>
        </header>
        {/* Barra de descarga: formato y preview a la vista en todas las pantallas, justo
            debajo de la búsqueda y antes de los resultados y botones de descarga. */}
        <div className="dlbar rule-b">
          <span className="eyebrow">Formato de descarga</span>
          <FormatSelector formato={formato} setFormato={setFormato} />
          <div className="dlbar-push"><PreviewSwitch enabled={previewEnabled} onToggle={togglePreview} /></div>
        </div>
      </div>

      {menuOpen && <div className="scrim" onClick={cerrar} />}
      <aside className={`drawer drawer-left${menuOpen ? ' is-open' : ''}`}>
        <div className="drawer-head rule-b">
          <span className="eyebrow">Módulos</span>
          <button type="button" className="btn btn-icon btn-icon-sm push" aria-label="Cerrar" onClick={cerrar}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </div>
        <div className="drawer-body">
          <div className="navlist">
            {item(IconHome, 'Inicio', () => { onBrand(); cerrar() })}
            {item(IconCrate, 'Mis Playlists', () => { onPlaylists(); cerrar() })}
            {item(IconList, 'Lista', () => { onLista(); cerrar() })}
            {item(IconHistory, 'Historial', () => { onHistorial(); cerrar() }, historialActive)}
            {item(IconTerminal, 'Consola', () => { onConsola(); cerrar() }, consoleActive)}
          </div>
        </div>
      </aside>
    </>
  )
}
