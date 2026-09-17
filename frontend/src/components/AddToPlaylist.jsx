import { useEffect, useRef, useState } from 'react'
import { useToast } from '../toast.jsx'
import { listarPlaylists, crearPlaylist, agregarAPlaylist, avisarPlaylists } from '../api'

/* Botón "+" en cada resultado → menú "Agregar «tema» a" con la lista de crates y un
   campo "Nueva playlist…" al pie (según playlist-assign.html). Autónomo: usa el toast
   y la API directo, no hace falta cablear handlers desde App. */

const IcoPlus = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>

const MENU_W = 250

export function AddToPlaylist({ track }) {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [lists, setLists] = useState(null)
  const [nombre, setNombre] = useState('')
  const [pos, setPos] = useState(null)
  const ref = useRef(null)
  const btnRef = useRef(null)
  const openRef = useRef(false)
  openRef.current = open

  useEffect(() => {
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    // Escape: cerrar y, si el foco estaba en el menú o en el botón, devolverlo al botón.
    const onKey = (e) => {
      if (e.key !== 'Escape' || !openRef.current) return
      if (ref.current && ref.current.contains(document.activeElement)) btnRef.current?.focus()
      setOpen(false)
    }
    // El menú va con position:fixed (ver ubicar): si scrollea la página o el estante, se
    // recalcula para seguir pegado al botón (cerrarlo en cada scroll lo cerraba solo cuando
    // el foco de teclado hacía scroll hasta el botón).
    let raf = 0
    const onMove = () => {
      if (!openRef.current || raf) return
      raf = requestAnimationFrame(() => { raf = 0; ubicar() })
    }
    document.addEventListener('click', onDoc)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onMove, true)
    window.addEventListener('resize', onMove)
    return () => {
      document.removeEventListener('click', onDoc); document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onMove, true); window.removeEventListener('resize', onMove)
      if (raf) cancelAnimationFrame(raf)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // position:fixed calculada desde el botón. Con absolute el menú quedaba recortado dentro
  // de los estantes de la home (.shelf-row tiene overflow y lo cortaba).
  function ubicar() {
    if (!btnRef.current) return
    const r = btnRef.current.getBoundingClientRect()
    const vw = window.innerWidth, vh = window.innerHeight
    const horiz = r.right >= MENU_W + 8 ? { right: Math.max(8, vw - r.right) } : { left: Math.max(8, r.left) }
    const abajo = vh - r.bottom
    const vert = abajo < 280 && r.top > abajo ? { bottom: vh - r.top + 6 } : { top: r.bottom + 6 }
    setPos({ ...horiz, ...vert, maxHeight: Math.max(160, (vert.top != null ? abajo : r.top) - 16) })
  }

  const abrir = async (e) => {
    e.stopPropagation()
    const willOpen = !open
    if (willOpen) ubicar()
    setOpen(willOpen)
    if (willOpen) { setLists(null); try { const d = await listarPlaylists(); setLists(d.playlists || []) } catch { setLists([]) } }
  }
  const agregar = async (id, nom) => {
    setOpen(false)
    btnRef.current?.focus()   // el menú se desmonta: el foco vuelve al botón, no al principio de la página
    try {
      const r = await agregarAPlaylist(id, track)
      if (r.dup) toast.info({ title: `«${track.titulo}» ya estaba en ${nom}` })
      else { toast.ok({ title: `Agregado a ${nom}`, body: track.titulo }); avisarPlaylists() }
    } catch { toast.danger({ title: 'No pude agregar a la playlist', body: 'Revisá que el servidor esté corriendo y probá de nuevo.' }) }
  }
  const crearYAgregar = async (e) => {
    e.preventDefault()
    const n = nombre.trim()
    if (!n) return
    // Antes sin catch: si fallaba no pasaba nada visible.
    try {
      const c = await crearPlaylist(n)
      if (c.exito) { setNombre(''); avisarPlaylists(); await agregar(c.playlist.id, c.playlist.nombre) }
      else toast.danger({ title: 'No pude crear la playlist' })
    } catch { toast.danger({ title: 'No pude crear la playlist', body: 'Revisá que el servidor esté corriendo y probá de nuevo.' }) }
  }

  return (
    <div className="add-pl" ref={ref} style={{ position: 'relative', display: 'inline-flex' }}>
      <button type="button" className="btn btn-icon-sm" ref={btnRef} aria-expanded={open}
        aria-label={`Agregar ${track.titulo ? `«${track.titulo}» ` : ''}a una playlist`} title="Agregar a una playlist" onClick={abrir}><IcoPlus /></button>
      {open && (
        <div className="menu elev-md" onClick={(e) => e.stopPropagation()} role="group" aria-label={`Agregar «${track.titulo}» a`}
          style={{ position: 'fixed', ...pos, zIndex: 60, minWidth: MENU_W, overflowY: 'auto' }}>
          <div className="menu-label eyebrow truncate">Agregar «{track.titulo}» a</div>
          {lists === null && <div style={{ padding: 'var(--space-2)' }}><span className="spinner" aria-hidden="true" /><span className="sr-only">Cargando playlists</span></div>}
          {lists && lists.map((p) => (
            <button type="button" key={p.id} className="menu-item" onClick={() => agregar(p.id, p.nombre)}>
              <span style={{ width: 15, flex: 'none' }} /><span className="truncate">{p.nombre}</span><span className="push chip-count">{p.total}</span>
            </button>
          ))}
          {lists && lists.length === 0 && (
            <div style={{ padding: '2px var(--space-2) var(--space-2)', fontSize: 12.5, color: 'var(--color-neutral-500)' }}>No tenés playlists todavía.</div>
          )}
          <div className="hr" style={{ margin: 'var(--space-2) 0' }} />
          <form onSubmit={crearYAgregar} style={{ padding: '0 var(--space-2) var(--space-2)' }}>
            <div className="cluster" style={{ flexWrap: 'nowrap', gap: 6 }}>
              <input className="input" value={nombre} onChange={(e) => setNombre(e.target.value)} placeholder="Nueva playlist…"
                style={{ height: 30, minHeight: 30, fontSize: 12.5 }} aria-label="Nombre de la nueva playlist" />
              <button type="submit" className="btn btn-primary btn-sm">Crear</button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}
