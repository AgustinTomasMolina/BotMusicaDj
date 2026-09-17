// Rail lateral con las playlists, SIEMPRE a la vista (no hay que entrar a un menú).
// Click en una (o en ＋) abre la vista de Playlists.
export default function PlaylistsRail({ playlists = [], activa, onOpen }) {
  const cuenta = (p) =>
    p.total ?? p.n ?? (Array.isArray(p.items) ? p.items.length : (p.count ?? null))
  const esActiva = (p) =>
    activa && (activa === p.id || activa === p.nombre || activa.id === p.id || activa.nombre === p.nombre)

  return (
    <aside className="pl-rail" aria-label="Mis playlists">
      <div className="pl-rail-head">
        <span>Mis playlists</span>
        <button type="button" className="pl-rail-add" aria-label="Nueva playlist" onClick={onOpen}>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M12 5v14" /><path d="M5 12h14" /></svg>
        </button>
      </div>
      <div className="pl-rail-list">
        {playlists.length === 0 && (
          <p className="pl-rail-empty">Todavía no tenés playlists. Creá una con ＋ y aparece acá.</p>
        )}
        {playlists.map((p, i) => {
          const n = cuenta(p)
          const act = esActiva(p)
          return (
            <button key={p.id ?? p.nombre ?? i} type="button" className={`pl-item${act ? ' on' : ''}`} onClick={onOpen}>
              <span className={`pl-thumb g${(i % 6) + 1}`} aria-hidden="true" />
              <span className="pl-item-txt">
                <span className="pl-item-name">{p.nombre || 'Playlist'}</span>
                <span className="pl-item-sub">{n != null ? `${n} tracks` : 'crate'}{act ? ' · activa' : ''}</span>
              </span>
            </button>
          )
        })}
      </div>
      <p className="pl-rail-foot">Siempre a la vista — no hace falta entrar a un menú.</p>
    </aside>
  )
}
