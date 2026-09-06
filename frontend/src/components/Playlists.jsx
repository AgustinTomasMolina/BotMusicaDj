import { useEffect, useState } from 'react'
import { fmtDur } from '../utils'
import { gradeClass } from './common'
import {
  listarPlaylists, getPlaylist, crearPlaylist, editarPlaylist,
  borrarPlaylistMia, quitarItemPlaylist, exportarPlaylist,
} from '../api'

/* Iconos inline (Phosphor-ish) */
const S = (p, sz = 16) => <svg width={sz} height={sz} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{p}</svg>
const IcoPlus = () => S(<path d="M12 5v14M5 12h14" />)
const IcoExport = () => S(<><path d="M12 15V4M8 8l4-4 4 4" /><path d="M5 19h14" /></>)
const IcoDown = () => S(<><path d="M12 4v10M8 11l4 4 4-4" /><path d="M5 19h14" /></>)
const IcoItunes = () => S(<><path d="M9 18V5l10-2v13" /><circle cx="6.5" cy="18" r="2.5" /><circle cx="16.5" cy="16" r="2.5" /></>)
const IcoTrash = () => S(<><path d="M4 7h16M9 7V5h6v2M7 7v13h10V7" /></>, 15)
const IcoX = () => S(<path d="M6 6l12 12M18 6L6 18" />, 15)
const IcoPlay = () => <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5l10 6.5-10 6.5z" fill="currentColor" /></svg>
const IcoDrag = () => <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="9" cy="6" r="1.5" /><circle cx="15" cy="6" r="1.5" /><circle cx="9" cy="12" r="1.5" /><circle cx="15" cy="12" r="1.5" /><circle cx="9" cy="18" r="1.5" /><circle cx="15" cy="18" r="1.5" /></svg>
const IcoCheck = () => S(<path d="M5 13l4.5 4.5L19 7" />, 12)
const IcoWarn = () => S(<><path d="M12 4l9 16H3z" /><path d="M12 10v4.5" /><circle cx="12" cy="17.4" r=".9" fill="currentColor" stroke="none" /></>, 17)
const IcoInfo = () => S(<><circle cx="12" cy="12" r="8" /><path d="M12 11v5" /><circle cx="12" cy="8.2" r=".9" fill="currentColor" stroke="none" /></>, 17)
const IcoFile = () => S(<><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v4h4" /></>)

const fmtLong = (s) => {
  s = parseInt(s) || 0
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(x).padStart(2, '0')}` : `${m}:${String(x).padStart(2, '0')}`
}

/* Diálogo de exportar (.m3u8) con guard de calidad → recibo por toast. */
function ExportDialog({ crate, onClose, toast }) {
  const items = crate.items || []
  const sinBajar = items.filter((i) => !i.descargado).length
  const bajos = items.filter((i) => i.descargado && (i.grade === 'D' || i.grade === 'F'))
  const incluidos = items.length - sinBajar
  const [busy, setBusy] = useState(false)
  const exportar = async () => {
    setBusy(true)
    try {
      const r = await exportarPlaylist(crate.id)
      if (r.exito) {
        const t = r.bajos && r.bajos.length ? toast.warn : toast.ok
        t({ title: `Playlist exportada · ${r.incluidos} tema${r.incluidos === 1 ? '' : 's'}`,
          body: `${r.archivo}${r.excluidos ? ` · ${r.excluidos} sin bajar excluidos` : ''}` })
        onClose()
      } else toast.danger({ title: 'No pude exportar', body: r.mensaje })
    } catch { toast.danger({ title: 'Error al exportar' }) } finally { setBusy(false) }
  }
  return (
    <div className="dialog-backdrop" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="dialog" style={{ width: 'min(520px,100%)' }}>
        <div className="cluster" style={{ flexWrap: 'nowrap' }}>
          <div><div className="eyebrow">Exportar playlist</div><div className="dialog-title" style={{ marginTop: 2 }}>{crate.nombre}</div></div>
          <button type="button" className="btn btn-icon btn-icon-sm push" aria-label="Cerrar" onClick={onClose}><IcoX /></button>
        </div>
        <div className="destpick">
          <button type="button" className="dest" aria-pressed="true"><b><IcoFile />Archivo .m3u8</b><span>Universal. Lo abre Rekordbox, Serato, VLC e iTunes. Rutas a tus archivos locales.</span></button>
          <button type="button" className="dest" disabled style={{ opacity: 0.45 }} title="Próximamente"><b><IcoItunes />Enviar a iTunes</b><span>Windows · próximamente.</span></button>
        </div>
        <dl className="kv" style={{ fontSize: 12.5 }}>
          <dt>Temas a exportar</dt><dd>{incluidos} de {items.length}</dd>
          <dt>Archivo</dt><dd>{crate.nombre}.m3u8</dd>
        </dl>
        {bajos.length > 0 && (
          <div className="alert alert-warn"><IcoWarn /><div>
            <div className="alert-title">{bajos.length} tema{bajos.length === 1 ? '' : 's'} de nota baja</div>
            <p>{bajos.map((b) => b.titulo).join(', ')} — por debajo de 192 kbps, en un equipo de club se nota.</p>
          </div></div>
        )}
        {sinBajar > 0 && (
          <div className="alert"><IcoInfo /><div>
            <div className="alert-title">{sinBajar} sin bajar quedan afuera</div>
            <p>La playlist apunta a archivos locales: los que no descargaste no pueden entrar.</p>
          </div></div>
        )}
        <div className="dialog-actions">
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancelar</button>
          <button type="button" className="btn btn-primary" onClick={exportar} disabled={busy || incluidos === 0}>
            {busy ? <span className="spinner" /> : <><IcoExport /> Exportar {incluidos} tema{incluidos === 1 ? '' : 's'}</>}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Playlists({ activePlaylist, setActivePlaylist, toast, onPlay }) {
  const [lists, setLists] = useState(null)
  const [selId, setSelId] = useState(null)
  const [crate, setCrate] = useState(null)
  const [exportOpen, setExportOpen] = useState(false)

  const cargarLista = async () => { try { const d = await listarPlaylists(); const ps = d.playlists || []; setLists(ps); return ps } catch { setLists([]); return [] } }
  const cargarCrate = async (id) => { if (!id) { setCrate(null); return } try { const d = await getPlaylist(id); setCrate(d.exito ? d.data : null) } catch { setCrate(null) } }

  useEffect(() => { (async () => { const ps = await cargarLista(); if (ps.length) setSelId(ps[0].id) })() }, [])
  useEffect(() => { cargarCrate(selId) }, [selId]) // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = async () => { await cargarLista(); await cargarCrate(selId) }

  const nueva = async () => {
    const nombre = window.prompt('Nombre de la nueva playlist:')
    if (!nombre || !nombre.trim()) return
    const r = await crearPlaylist(nombre.trim())
    if (r.exito) { await cargarLista(); setSelId(r.playlist.id); toast.info({ title: `Playlist "${r.playlist.nombre}" creada` }) }
  }
  const activar = async (id, nombre) => { await editarPlaylist(id, { activar: true }); setActivePlaylist({ id, nombre }); await cargarLista() }
  const renombrar = async (nombre) => { if (!crate || !nombre.trim() || nombre === crate.nombre) return; await editarPlaylist(crate.id, { nombre: nombre.trim() }); cargarLista() }
  const borrar = async () => {
    if (!crate || !window.confirm(`¿Borrar la playlist "${crate.nombre}"? No se borran los archivos, solo la lista.`)) return
    await borrarPlaylistMia(crate.id)
    if (activePlaylist?.id === crate.id) setActivePlaylist(null)
    const ps = await cargarLista(); setSelId(ps[0]?.id || null)
    toast.info({ title: 'Playlist borrada' })
  }
  const quitar = async (itemId) => { if (!crate) return; await quitarItemPlaylist(crate.id, itemId); refresh() }

  if (lists && lists.length === 0) {
    return (
      <div className="empty">
        <div className="empty-art">{S(<><path d="M9 18V5l10-2v13" /><circle cx="6.5" cy="18" r="2.5" /><circle cx="16.5" cy="16" r="2.5" /></>, 26)}</div>
        <h3>Todavía no tenés playlists</h3>
        <p>Creá un crate (ej. "Hard Techno"), marcalo como activo y los temas que descargues se cargan solos. Después lo exportás a iTunes/Rekordbox.</p>
        <button type="button" className="btn btn-primary" style={{ marginTop: 'var(--space-3)' }} onClick={nueva}><IcoPlus /> Crear playlist</button>
      </div>
    )
  }

  const m = crate?.metrics
  const maxKey = m ? Math.max(1, ...m.keys) : 1

  return (
    <div className="crates">
      <aside className="crates-aside">
        <div className="cluster" style={{ padding: 'var(--space-1) var(--space-2)' }}>
          <span className="eyebrow">Mis playlists</span>
          <button type="button" className="btn btn-icon-sm push" aria-label="Nueva playlist" title="Nueva playlist" onClick={nueva}><IcoPlus /></button>
        </div>
        <div className="cratelist">
          {(lists || []).map((p) => (
            <button type="button" key={p.id} className="crate-item" aria-current={p.id === selId} onClick={() => setSelId(p.id)}>
              {p.activa ? <span className="now-dot" title="Playlist activa" /> : <span style={{ width: 5, flex: 'none' }} />}
              <span style={{ minWidth: 0, flex: 1 }}>
                <span className="crate-name">{p.nombre}</span>
                <span className="crate-sub">{p.total} tema{p.total === 1 ? '' : 's'} · {fmtLong(p.duracion)}{p.bpm_prom ? ` · ${p.bpm_prom} BPM` : ''}</span>
              </span>
            </button>
          ))}
          {!lists && <div style={{ padding: 'var(--space-2)', color: 'var(--color-neutral-600)', fontSize: 12 }}><span className="spinner" /></div>}
        </div>
      </aside>

      <section style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
        {crate && (
          <>
            <div className="crate-head">
              <div className="cluster" style={{ alignItems: 'flex-end' }}>
                <div style={{ flex: 1, minWidth: 180 }}>
                  <div className="eyebrow" style={{ marginBottom: 4 }}>{crate.activa ? 'Playlist activa' : 'Playlist'}</div>
                  <input key={crate.id} className="name-edit" defaultValue={crate.nombre} aria-label="Nombre de la playlist"
                    onBlur={(e) => renombrar(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur() }} />
                </div>
                <div className="crate-actions cluster" style={{ gap: 'var(--space-2)' }}>
                  {!crate.activa && <button type="button" className="btn btn-secondary btn-sm" onClick={() => activar(crate.id, crate.nombre)}>Marcar activa</button>}
                  <button type="button" className="btn btn-primary btn-sm" onClick={() => setExportOpen(true)} disabled={!crate.items?.length}><IcoExport /> Exportar .m3u8</button>
                  <button type="button" className="btn btn-icon-sm" aria-label="Borrar playlist" title="Borrar playlist" onClick={borrar}><IcoTrash /></button>
                </div>
              </div>
              {m && (
                <div className="metrics">
                  <div className="metric"><b>{m.total}</b><span>Temas</span></div>
                  <div className="metric"><b>{fmtLong(m.duracion)}</b><span>Duración</span></div>
                  {m.bpm_prom && <div className="metric"><b>{m.bpm_prom}</b><span>BPM promedio</span></div>}
                  {m.bpm_min && <div className="metric"><b>{m.bpm_min}–{m.bpm_max}</b><span>Rango BPM</span></div>}
                  {m.peak && (
                    <div className="metric" style={{ gap: 6 }}>
                      <span className="keyspread" role="img" aria-label="Distribución de keys">
                        {m.keys.map((h, i) => <i key={i} className={m.peak === i + 1 ? 'is-peak' : ''} style={{ height: h ? Math.round(3 + (h / maxKey) * 27) : 3 }} />)}
                      </span>
                      <span style={{ fontSize: 10, letterSpacing: '0.09em', textTransform: 'uppercase', color: 'var(--color-neutral-600)' }}>Keys · {m.compat_dominante} de {m.total} compatibles</span>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="results results-crate">
              {(crate.items || []).length === 0 && (
                <div className="empty" style={{ padding: 'var(--space-6) var(--space-3)' }}>
                  <p>{crate.activa ? 'Descargá temas con esta playlist activa y aparecen acá.' : 'Marcá esta playlist como activa y bajá temas para llenarla.'}</p>
                </div>
              )}
              {(crate.items || []).map((it) => (
                <div className="trk" key={it.id}>
                  <div className="drag" aria-label="Reordenar"><IcoDrag /></div>
                  <div className="thumb" onClick={() => onPlay?.(it)}>
                    {it.thumbnail ? <img src={it.thumbnail} loading="lazy" onError={(e) => { e.currentTarget.style.display = 'none' }} /> : <div className="thumb-ph" />}
                    <button type="button" className="thumb-play" aria-label="Reproducir" onClick={(e) => { e.stopPropagation(); onPlay?.(it) }}><IcoPlay /></button>
                  </div>
                  <div className="trk-id">
                    <div className="trk-title" title={it.titulo}>{it.titulo}</div>
                    <div className="trk-artist"><span className="truncate">{it.artista}</span><span className="sep">·</span><span className="mono">{fmtDur(it.duracion)}</span></div>
                  </div>
                  <div className="trk-meta">
                    {it.bpm && <span className="mb"><span className="mb-label">BPM</span><b>{it.bpm}</b></span>}
                    {it.camelot && <span className="mb mb-key"><span className="mb-label">KEY</span><b>{it.camelot}</b></span>}
                    {(it.formato || it.genero) && <span className="mb"><b>{(it.formato || '').toUpperCase() || it.genero}</b></span>}
                  </div>
                  <div className="trk-grade">{it.grade && it.grade !== '?' && <span className={`grade ${gradeClass(it.grade)}`}>{it.grade}</span>}</div>
                  <div className="trk-status">
                    {it.descargado
                      ? <span className="status status-have"><IcoCheck /> Descargado</span>
                      : <span className="status status-need"><IcoDown /> Falta bajar</span>}
                  </div>
                  <div className="trk-acts">
                    <button type="button" className="btn btn-icon-sm" aria-label="Quitar de la playlist" title="Quitar" onClick={() => quitar(it.id)}><IcoX /></button>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </section>

      {exportOpen && crate && <ExportDialog crate={crate} toast={toast} onClose={() => setExportOpen(false)} />}
    </div>
  )
}
