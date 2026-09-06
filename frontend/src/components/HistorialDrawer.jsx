/* Cajón lateral con el historial persistido: búsquedas, playlists (modo lista) y
   descargas. Migrado a la design system Nocturne (.drawer / .hist / .grade). */

const IcoX = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
const IcoSearch = () => <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.6-3.6" /></svg>
const IcoDown = () => <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 4v10M8 11l4 4 4-4M5 19h14" /></svg>
const IcoList = () => <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h10" /></svg>
const IcoTrash = () => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 7h16M9 7V5h6v2M7 7v13h10V7" /></svg>

// Fecha relativa simple ("recién", "hace 5 min", "ayer", o fecha corta).
function hace(iso) {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (isNaN(t)) return ''
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 60) return 'recién'
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`
  const d = Math.floor(s / 86400)
  if (d === 1) return 'ayer'
  if (d < 7) return `hace ${d} d`
  return new Date(t).toLocaleDateString()
}

const gradeClass = (g) => ({ A: 'grade-a', 'A-': 'grade-am', B: 'grade-b', C: 'grade-c', D: 'grade-d', F: 'grade-f' }[g] || '')

function ItemCol({ title, sub }) {
  return (
    <span style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0, flex: 1 }}>
      <span className="truncate">{title}</span>
      <span className="mono" style={{ fontSize: 10.5, color: 'var(--color-neutral-600)' }}>{sub}</span>
    </span>
  )
}

function Section({ title, count, onClear, children }) {
  return (
    <div style={{ marginTop: 'var(--space-3)' }}>
      <div className="cluster" style={{ padding: '0 var(--space-1) 4px' }}>
        <span className="eyebrow">{title}</span>
        {count != null && <span className="mono" style={{ fontSize: 10.5, color: 'var(--color-neutral-600)' }}>{count}</span>}
        {onClear && count > 0 &&
          <button type="button" className="btn btn-icon-sm push" title="Vaciar esta sección" onClick={onClear}><IcoTrash /></button>}
      </div>
      <div className="hist">{children}</div>
    </div>
  )
}

const Vacio = ({ children }) => <div style={{ padding: 'var(--space-2)', color: 'var(--color-neutral-600)', fontSize: 12 }}>{children}</div>

export default function HistorialDrawer({ open, onClose, data, onRunSearch, onOpenPlaylist, onDeletePlaylist, onLimpiar }) {
  const busquedas = data?.busquedas || []
  const playlists = data?.playlists || []
  const descargas = data?.descargas || []
  const total = busquedas.length + playlists.length + descargas.length

  return (
    <aside className={`drawer drawer-right${open ? ' is-open' : ''}`}>
      <div className="drawer-head rule-b">
        <span className="eyebrow">Historial</span>
        {data && total > 0 &&
          <button type="button" className="btn btn-ghost push" style={{ fontSize: 12 }} onClick={() => onLimpiar('todo')}>Vaciar todo</button>}
        <button type="button" className={`btn btn-icon btn-icon-sm${data && total > 0 ? '' : ' push'}`} aria-label="Cerrar" onClick={onClose}><IcoX /></button>
      </div>
      <div className="drawer-body">
        {!data ? (
          <div className="cluster" style={{ gap: 9, color: 'var(--color-neutral-500)', fontSize: 13, padding: 'var(--space-3)' }}><span className="spinner" /> Cargando historial…</div>
        ) : (
          <>
            <Section title="Búsquedas" count={busquedas.length} onClear={() => onLimpiar('busquedas')}>
              {busquedas.length === 0 && <Vacio>Sin búsquedas todavía.</Vacio>}
              {busquedas.map((b) => (
                <button type="button" key={b.id} className="hist-item" onClick={() => onRunSearch(b.query)} title="Volver a buscar">
                  <IcoSearch />
                  <ItemCol title={b.query} sub={`${b.total} tema${b.total === 1 ? '' : 's'} · ${hace(b.creado_en)}`} />
                </button>
              ))}
            </Section>

            <Section title="Playlists" count={playlists.length} onClear={() => onLimpiar('playlists')}>
              {playlists.length === 0 && <Vacio>No guardaste playlists aún.</Vacio>}
              {playlists.map((p) => (
                <div key={p.id} className="hist-item" role="button" title="Abrir playlist" onClick={() => onOpenPlaylist(p.id)}>
                  <IcoList />
                  <ItemCol title={p.nombre || 'Playlist'} sub={`${p.total} tema${p.total === 1 ? '' : 's'} · ${hace(p.creado_en)}`} />
                  <button type="button" className="btn btn-icon-sm" title="Borrar del historial" onClick={(e) => { e.stopPropagation(); onDeletePlaylist(p.id) }}><IcoX /></button>
                </div>
              ))}
            </Section>

            <Section title="Descargas" count={descargas.length} onClear={() => onLimpiar('descargas')}>
              {descargas.length === 0 && <Vacio>Todavía no descargaste nada.</Vacio>}
              {descargas.map((d) => (
                <div key={d.id} className="hist-item" style={{ cursor: 'default' }}>
                  <IcoDown />
                  <ItemCol title={`${d.titulo}${d.artista ? ` — ${d.artista}` : ''}`} sub={`${(d.formato || '').toUpperCase()} · ${d.fuente} · ${hace(d.creado_en)}`} />
                  {d.grade && d.grade !== '?' && <span className={`grade grade-sm ${gradeClass(d.grade)}`}>{d.grade}</span>}
                </div>
              ))}
            </Section>
          </>
        )}
      </div>
    </aside>
  )
}
