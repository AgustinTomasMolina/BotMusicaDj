import { useEffect, useRef, useState } from 'react'
import { useToast } from '../toast.jsx'
import { listarPlaylists, crearPlaylist, agregarAPlaylist } from '../api'

/* Botón "+" en cada resultado → menú "Agregar «tema» a" con la lista de crates y un
   campo "Nueva playlist…" al pie (según playlist-assign.html). Autónomo: usa el toast
   y la API directo, no hace falta cablear handlers desde App. */

const IcoPlus = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>

export function AddToPlaylist({ track }) {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [lists, setLists] = useState(null)
  const [nombre, setNombre] = useState('')
  const ref = useRef(null)

  useEffect(() => {
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('click', onDoc)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('click', onDoc); document.removeEventListener('keydown', onKey) }
  }, [])

  const abrir = async (e) => {
    e.stopPropagation()
    const willOpen = !open
    setOpen(willOpen)
    if (willOpen) { setLists(null); try { const d = await listarPlaylists(); setLists(d.playlists || []) } catch { setLists([]) } }
  }
  const agregar = async (id, nom) => {
    setOpen(false)
    try {
      const r = await agregarAPlaylist(id, track)
      if (r.dup) toast.info({ title: `«${track.titulo}» ya estaba en ${nom}` })
      else toast.ok({ title: `Agregado a ${nom}`, body: track.titulo })
    } catch { toast.danger({ title: 'No pude agregar a la playlist' }) }
  }
  const crearYAgregar = async (e) => {
    e.preventDefault()
    const n = nombre.trim()
    if (!n) return
    const c = await crearPlaylist(n)
    if (c.exito) { setNombre(''); await agregar(c.playlist.id, c.playlist.nombre) }
  }

  return (
    <div className="add-pl" ref={ref} style={{ position: 'relative', display: 'inline-flex' }}>
      <button type="button" className="btn btn-icon-sm" aria-label="Agregar a playlist" title="Agregar a una playlist" onClick={abrir}><IcoPlus /></button>
      {open && (
        <div className="menu elev-md" onClick={(e) => e.stopPropagation()}
          style={{ position: 'absolute', top: 'calc(100% + 6px)', right: 0, zIndex: 60, minWidth: 250 }}>
          <div className="menu-label eyebrow truncate">Agregar «{track.titulo}» a</div>
          {lists === null && <div style={{ padding: 'var(--space-2)' }}><span className="spinner" /></div>}
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
