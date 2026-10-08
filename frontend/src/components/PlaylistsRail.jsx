// Rail lateral con las playlists, SIEMPRE a la vista (no hay que entrar a un menú).
// En escritorio es la ÚNICA lista (la de adentro de la vista Playlists está escondida): click en
// una abre su página, ＋ crea una nueva, y la que está abierta se marca con aria-current.
//
// `biblioteca` (f50, solo en Radio DJ): el estado de la biblioteca del motor ({estado, total}),
// como en el canvas de la radio: abajo de las playlists, cuántos tracks analizó el motor.
export default function PlaylistsRail({ playlists = [], activa, abierta, onOpen, onNueva, biblioteca = null }) {
  const cuenta = (p) =>
    p.total ?? p.n ?? (Array.isArray(p.items) ? p.items.length : (p.count ?? null))
  const esActiva = (p) =>
    activa && (activa === p.id || activa === p.nombre || activa.id === p.id || activa.nombre === p.nombre)

  return (
    <aside className="pl-rail" aria-label="Mis playlists">
      <div className="pl-rail-head">
        <span>Mis playlists</span>
        <button type="button" className="pl-rail-add" aria-label="Crear una playlist nueva" title="Crear una playlist nueva" onClick={() => onNueva()}>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true"><path d="M12 5v14" /><path d="M5 12h14" /></svg>
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
            <button key={p.id ?? p.nombre ?? i} type="button" className={`pl-item${act ? ' on' : ''}`}
              aria-current={abierta != null && abierta === p.id ? 'page' : undefined} onClick={() => onOpen(p.id)}>
              <span className={`pl-thumb g${(i % 6) + 1}`} aria-hidden="true" />
              <span className="pl-item-txt">
                <span className="pl-item-name">{p.nombre || 'Playlist'}</span>
                <span className="pl-item-sub">{n != null ? `${n} tracks` : 'crate'}{act ? ' · activa' : ''}</span>
              </span>
            </button>
          )
        })}
      </div>
      {biblioteca && (
        <section className="pl-rail-lib" aria-labelledby="pl-rail-lib-h">
          <span className="pl-rail-lib-h" id="pl-rail-lib-h">Biblioteca del motor</span>
          <span className="pl-rail-lib-n">
            {biblioteca.estado === 'ok'
              ? `${biblioteca.total} track${biblioteca.total === 1 ? '' : 's'} analizado${biblioteca.total === 1 ? '' : 's'}`
              : biblioteca.configurada ? 'no se pudo leer (el motivo está en la radio)' : 'sin conectar'}
          </span>
        </section>
      )}
      <p className="pl-rail-foot">Siempre a la vista — no hace falta entrar a un menú.</p>
    </aside>
  )
}
