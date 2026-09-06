import { useEffect, useRef, useState } from 'react'
import { FORMATOS, SUB, GENEROS } from '../utils'
import { IconHome, IconEye, IconList, IconHistory, IconTerminal, IconHeadphones } from './icons'

/* Filtro de género: presets + escribir uno propio. Sesga la búsqueda hacia ese estilo. */
function GenrePicker({ genero, setGenero }) {
  const [open, setOpen] = useState(false)
  const [txt, setTxt] = useState('')
  const ref = useRef(null)
  useEffect(() => {
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('click', onDoc)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('click', onDoc); document.removeEventListener('keydown', onKey) }
  }, [])
  const pick = (g) => { setGenero(g); setTxt(''); setOpen(false) }
  return (
    <div style={{ position: 'relative' }} ref={ref}>
      <button type="button" className="btn btn-secondary" aria-expanded={open}
        title="Filtrar por género" onClick={(e) => { e.stopPropagation(); setOpen((o) => !o) }}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"><path d="M4 8h10M18 8h2M4 16h4M12 16h8" /><circle cx="16" cy="8" r="2" /><circle cx="10" cy="16" r="2" /></svg>
        {genero || 'Género'}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"><path d="M6 9.5l6 6 6-6" /></svg>
      </button>
      {open && (
        <div className="menu elev-md" style={{ position: 'absolute', top: 'calc(100% + 6px)', right: 0, zIndex: 45, minWidth: 260 }}>
          <div className="menu-label eyebrow">Filtrar por género</div>
          <form style={{ padding: '0 var(--space-1) var(--space-1)' }} onSubmit={(e) => { e.preventDefault(); if (txt.trim()) pick(txt.trim()) }}>
            <input className="input" value={txt} onChange={(e) => setTxt(e.target.value)} placeholder="Escribí un género…" autoComplete="off" />
          </form>
          <button type="button" className="menu-item" aria-checked={!genero} onClick={() => pick('')}>Todos</button>
          {GENEROS.map((g) => (
            <button type="button" key={g} className="menu-item" aria-checked={g === genero} onClick={() => pick(g)}>{g}</button>
          ))}
        </div>
      )}
    </div>
  )
}

/* Dropdown de formato de descarga. */
function FormatPicker({ formato, setFormato }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('click', onDoc)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('click', onDoc); document.removeEventListener('keydown', onKey) }
  }, [])
  return (
    <div style={{ position: 'relative' }} ref={ref}>
      <button type="button" className="btn btn-secondary btn-block" aria-haspopup="listbox" aria-expanded={open}
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o) }}>
        <IconHeadphones size={16} />{formato.toUpperCase()} · {SUB[formato]}
        <svg className="push" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"><path d="M6 9.5l6 6 6-6" /></svg>
      </button>
      {open && (
        <div className="menu elev-md" style={{ position: 'absolute', top: 'calc(100% + 6px)', left: 0, right: 0, zIndex: 45 }} role="listbox">
          {FORMATOS.map((f) => (
            <button key={f.val} type="button" className="menu-item" role="option" aria-checked={f.val === formato}
              onClick={() => { setFormato(f.val); setOpen(false) }}>
              {f.val.toUpperCase()}<span className="push chip-count">{f.desc}</span>
            </button>
          ))}
        </div>
      )}
    </div>
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
            {item(IconEye, 'Preview', togglePreview, previewEnabled)}
            {item(IconList, 'Lista', () => { onLista(); cerrar() })}
            {item(IconHistory, 'Historial', () => { onHistorial(); cerrar() }, historialActive)}
            {item(IconTerminal, 'Consola', () => { onConsola(); cerrar() }, consoleActive)}
          </div>
          <div style={{ marginTop: 'var(--space-4)' }}>
            <div className="eyebrow" style={{ padding: '0 var(--space-3) var(--space-2)' }}>Formato de descarga</div>
            <div style={{ padding: '0 var(--space-3)' }}><FormatPicker formato={formato} setFormato={setFormato} /></div>
          </div>
        </div>
      </aside>
    </>
  )
}
