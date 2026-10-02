import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { fmtDur } from '../utils'
import { useDialog } from '../hooks'
import { gradeClass } from './common'
import {
  listarPlaylists, getPlaylist, editarPlaylist,
  borrarPlaylistMia, quitarItemPlaylist, exportarPlaylist, avisarPlaylists,
} from '../api'
import { crearPlaylistConPrompt } from '../playlists'
import Cover from './Cover'
import { usePlayer } from '../player/context'
import { fromCrateItem } from '../player/track'
import { descargas, faltantes, textoLote } from '../playlistDescarga'

/* Iconos inline (Phosphor-ish) */
const S = (p, sz = 16) => <svg width={sz} height={sz} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{p}</svg>
const IcoPlus = () => S(<path d="M12 5v14M5 12h14" />)
const IcoExport = () => S(<><path d="M12 15V4M8 8l4-4 4 4" /><path d="M5 19h14" /></>)
const IcoDown = () => S(<><path d="M12 4v10M8 11l4 4 4-4" /><path d="M5 19h14" /></>)
const IcoItunes = () => S(<><path d="M9 18V5l10-2v13" /><circle cx="6.5" cy="18" r="2.5" /><circle cx="16.5" cy="16" r="2.5" /></>)
const IcoTrash = () => S(<><path d="M4 7h16M9 7V5h6v2M7 7v13h10V7" /></>, 15)
const IcoX = () => S(<path d="M6 6l12 12M18 6L6 18" />, 15)
const IcoPlay = () => <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5l10 6.5-10 6.5z" fill="currentColor" /></svg>
const IcoPause = () => <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><rect x="6.5" y="5.5" width="4" height="13" rx="1" fill="currentColor" /><rect x="13.5" y="5.5" width="4" height="13" rx="1" fill="currentColor" /></svg>
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
  // Antes no cerraba con Escape ni movía el foco: ahora igual que los otros diálogos.
  const dialogRef = useRef(null)
  useDialog(dialogRef, true, onClose)
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
      <div className="dialog" style={{ width: 'min(520px,100%)' }} ref={dialogRef} role="dialog" aria-modal="true"
        aria-label={`Exportar playlist ${crate.nombre}`} tabIndex={-1}>
        <div className="cluster" style={{ flexWrap: 'nowrap' }}>
          <div><div className="eyebrow">Exportar playlist</div><div className="dialog-title" style={{ marginTop: 2 }}>{crate.nombre}</div></div>
          <button type="button" className="btn btn-icon btn-icon-sm push" aria-label="Cerrar" onClick={onClose} data-autofocus><IcoX /></button>
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

export default function Playlists({ activePlaylist, setActivePlaylist, toast, onPlay, initialId, pick, onSeleccion, formato = 'wav' }) {
  const [lists, setLists] = useState(null)
  const [selId, setSelId] = useState(null)
  const [crate, setCrate] = useState(null)
  const [exportOpen, setExportOpen] = useState(false)
  const player = usePlayer()
  // Cola de la barra: los temas de esta playlist, en su orden.
  const reproducir = (k) => onPlay?.((crate?.items || []).map(fromCrateItem), k)

  const cargarLista = async () => { try { const d = await listarPlaylists(); const ps = d.playlists || []; setLists(ps); return ps } catch { setLists([]); return [] } }
  const cargarCrate = async (id) => { if (!id) { setCrate(null); return } try { const d = await getPlaylist(id); setCrate(d.exito ? d.data : null) } catch { setCrate(null) } }

  useEffect(() => { cargarLista() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  // La lista cambia desde afuera (el ＋ del rail, el ＋ de una tarjeta): sin esto, crear la
  // primera playlist desde el rail dejaba esta pantalla en "todavía no tenés playlists".
  useEffect(() => {
    const recargar = () => { cargarLista() }
    window.addEventListener('musiflix:playlists', recargar)
    return () => window.removeEventListener('musiflix:playlists', recargar)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  // Qué playlist se muestra. El pedido del rail se aplica SOLO cuando cambia `pick` (un click
  // nuevo): si no, cualquier recarga de la lista (activar, renombrar, quitar, borrar, el evento
  // 'musiflix:playlists') volvía a la del rail y pisaba lo elegido en la lista interna.
  // Recargar la lista solo repara una selección nula o que ya no existe (la playlist borrada).
  const pickAplicado = useRef(null)
  useEffect(() => {
    if (!lists || !lists.length) return
    if (pick !== pickAplicado.current && initialId != null && lists.some((p) => p.id === initialId)) {
      pickAplicado.current = pick
      setSelId(initialId)
      return
    }
    if (selId == null || !lists.some((p) => p.id === selId)) setSelId(lists[0].id)
  }, [lists, initialId, pick]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { cargarCrate(selId) }, [selId]) // eslint-disable-line react-hooks/exhaustive-deps
  // Cuál está abierta lo marca el rail, que en escritorio es la única lista: se lo avisamos.
  // La selección también cambia sola (al crear una, o al borrar la abierta).
  useEffect(() => { onSeleccion?.(selId) }, [selId]) // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = async () => { await cargarLista(); await cargarCrate(selId) }

  // Bajar desde la playlist (f41). El estado de las descargas vive en un store de módulo
  // (src/playlistDescarga.js): si el dueño se va a otra pantalla mientras baja, al volver
  // ve en qué va. Cada descarga que termina bien recarga la playlist abierta (el item ya
  // tiene archivo, nota y formato) y avisa al rail (cambió "descargados").
  const dl = useSyncExternalStore(descargas.subscribe, descargas.getSnapshot)
  const hechosVistos = useRef(dl.hechos)
  useEffect(() => {
    if (dl.hechos === hechosVistos.current) return
    hechosVistos.current = dl.hechos
    cargarCrate(selId); avisarPlaylists()
  }, [dl.hechos]) // eslint-disable-line react-hooks/exhaustive-deps
  const bajarUno = async (it) => {
    if (!crate) return
    const r = await descargas.bajarUno(crate.id, it, formato)
    const quien = `${it.titulo}${it.artista ? ` — ${it.artista}` : ''}`
    if (r.exito) {
      const g = r.calidad?.grade ?? r.item?.grade
      toast.ok({ title: quien, body: `Descargado y etiquetado${g && g !== '?' ? ` · nota ${g}` : ''} · ${(r.item?.formato || formato).toUpperCase()}.` })
    } else {
      toast.danger({ title: `No se bajó: ${quien}`, body: r.mensaje, actions: [{ label: 'Reintentar', onClick: () => bajarUno(it) }] })
    }
  }
  const bajarTodos = () => { if (crate) descargas.bajarTodos(crate.id, crate.items, formato) }

  // Foco del teclado en la tanda (B5). "Bajar los N que faltan" queda montado mientras baja
  // (aria-disabled, no `disabled`: un botón deshabilitado pierde el foco). Pero al terminar
  // puede desaparecer (no falta ninguno) y lo mismo "Detener" o la X del resumen: el foco se
  // iría a <body> y el teclado arrancaría de cero. Si el foco estaba en esos controles y se
  // perdió, pasa al resumen (o al botón, si sigue).
  const resumenRef = useRef(null)
  const btnTodosRef = useRef(null)
  const focoEnLote = useRef(false)
  const marcaFoco = {
    onFocus: () => { focoEnLote.current = true },
    // relatedTarget null = el elemento se desmontó (o el foco no fue a ningún lado): se recuerda.
    onBlur: (e) => { if (e.relatedTarget) focoEnLote.current = false },
  }
  useEffect(() => {
    if (!focoEnLote.current) return
    const a = document.activeElement
    if (a && a !== document.body) return
    const destino = resumenRef.current || btnTodosRef.current
    if (destino) destino.focus()
    else focoEnLote.current = false
  }, [dl.lote, crate]) // eslint-disable-line react-hooks/exhaustive-deps

  // Antes estas acciones no tenían catch: sin conexión fallaban en silencio.
  const fallo = (title) => toast.danger({ title, body: 'Revisá que el servidor esté corriendo y probá de nuevo.' })
  // Mismo flujo que el ＋ del rail (src/playlists.js), con la playlist nueva ya seleccionada acá.
  const nueva = () => crearPlaylistConPrompt({ toast, onCreada: async (p) => { await cargarLista(); setSelId(p.id) } })
  const activar = async (id, nombre) => {
    try { await editarPlaylist(id, { activar: true }); setActivePlaylist({ id, nombre }); await cargarLista() }
    catch { fallo('No pude marcarla como activa') }
  }
  const renombrar = async (input) => {
    const nombre = input.value
    // Nombre vacío: se vuelve al que tenía (antes quedaba el campo vacío y el server con el viejo).
    if (crate && !nombre.trim()) { input.value = crate.nombre; return }
    if (!crate || nombre === crate.nombre) return
    try { await editarPlaylist(crate.id, { nombre: nombre.trim() }); avisarPlaylists(); cargarLista() }
    catch { input.value = crate.nombre; fallo('No pude renombrar la playlist') }
  }
  const borrar = async () => {
    if (!crate || !window.confirm(`¿Borrar la playlist "${crate.nombre}"? No se borran los archivos, solo la lista.`)) return
    try {
      await borrarPlaylistMia(crate.id)
      avisarPlaylists()
      if (activePlaylist?.id === crate.id) setActivePlaylist(null)
      const ps = await cargarLista(); setSelId(ps[0]?.id || null)
      toast.info({ title: 'Playlist borrada' })
    } catch { fallo('No pude borrar la playlist') }
  }
  const quitar = async (itemId) => {
    if (!crate) return
    // Si espera en una tanda, que no se pida (C5): la fila deja de existir.
    try { await quitarItemPlaylist(crate.id, itemId); descargas.olvidar(itemId); avisarPlaylists(); refresh() }
    catch { fallo('No pude quitar el tema') }
  }

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
  // Lo que falta y se puede bajar acá; los "sin link" (archivos de la biblioteca) no cuentan.
  const nFaltan = crate ? faltantes(crate.items).length : 0
  const lote = dl.lote
  const loteAca = lote && crate && lote.pid === crate.id
  const loteEnCurso = lote && !lote.terminado
  const loteHechos = lote ? lote.ok + lote.fallidos.length + (lote.quitados || []).length : 0
  const loteAcaEnCurso = !!(loteAca && loteEnCurso)

  return (
    <div className="crates">
      {/* Nombre propio: el rail lateral ya es "Mis playlists" y dos regiones con el mismo nombre
          no se distinguen en la lista de regiones del lector de pantalla. */}
      <aside className="crates-aside" aria-label="Elegir playlist">
        <div className="cluster" style={{ padding: 'var(--space-1) var(--space-2)' }}>
          <span className="eyebrow">Mis playlists</span>
          <button type="button" className="btn btn-icon-sm push" aria-label="Nueva playlist" title="Nueva playlist" onClick={nueva}><IcoPlus /></button>
        </div>
        <div className="cratelist">
          {(lists || []).map((p) => (
            <button type="button" key={p.id} className="crate-item" aria-current={p.id === selId} onClick={() => setSelId(p.id)}>
              {p.activa ? <span className="now-dot" title="Playlist activa" aria-hidden="true" /> : <span style={{ width: 5, flex: 'none' }} />}
              <span style={{ minWidth: 0, flex: 1 }}>
                {/* La activa se marcaba solo con un punto de color: el texto oculto lo dice. */}
                <span className="crate-name">{p.nombre}{p.activa && <span className="sr-only"> (activa)</span>}</span>
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
                    onBlur={(e) => renombrar(e.target)} onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur() }} />
                </div>
                <div className="crate-actions cluster" style={{ gap: 'var(--space-2)' }}>
                  {!crate.activa && <button type="button" className="btn btn-secondary btn-sm" onClick={() => activar(crate.id, crate.nombre)}>Marcar activa</button>}
                  {/* Una sola tanda a la vez (de a un tema): si hay otra en curso, el botón espera.
                      Mientras baja la de ESTA playlist sigue montado (con el foco, si lo tenía). */}
                  {(nFaltan > 0 || loteAcaEnCurso) && (
                    <button type="button" ref={btnTodosRef} className="btn btn-secondary btn-sm" {...marcaFoco}
                      onClick={() => { if (!loteEnCurso) bajarTodos() }} aria-disabled={loteEnCurso ? true : undefined}
                      title={loteAcaEnCurso ? undefined : loteEnCurso ? 'Ya se está bajando otra playlist' : `Bajar en ${formato.toUpperCase()}`}>
                      {loteAcaEnCurso ? <><span className="spinner" aria-hidden="true" /> Bajando los que faltan…</>
                        : <><IcoDown /> {nFaltan === 1 ? 'Bajar el que falta' : `Bajar los ${nFaltan} que faltan`}</>}
                    </button>
                  )}
                  <button type="button" className="btn btn-primary btn-sm" onClick={() => setExportOpen(true)} disabled={!crate.items?.length}><IcoExport /> Exportar .m3u8</button>
                  <button type="button" className="btn btn-icon-sm" aria-label="Borrar playlist" title="Borrar playlist" onClick={borrar}><IcoTrash /></button>
                </div>
              </div>
              {/* Progreso de "bajar los que faltan". role=status: el lector anuncia cada cambio
                  ("Bajando 3 de 8 · …") sin mover el foco. */}
              {loteAca && (
                <div className="crate-dl">
                  <div role="status" className="crate-dl-txt" ref={resumenRef} tabIndex={-1}>{textoLote(lote)}</div>
                  <div className="crate-dl-bar" role="progressbar" aria-label="Temas procesados"
                    aria-valuemin={0} aria-valuemax={lote.total} aria-valuenow={loteHechos}
                    aria-valuetext={`${loteHechos} de ${lote.total}`}>
                    <i style={{ width: `${Math.round((loteHechos / lote.total) * 100)}%` }} />
                  </div>
                  {loteEnCurso
                    ? <button type="button" className="btn btn-secondary btn-sm" {...marcaFoco} onClick={() => descargas.detener()}>Detener después de este</button>
                    : <button type="button" className="btn btn-icon-sm" {...marcaFoco} aria-label="Cerrar el resumen de la descarga" onClick={() => descargas.cerrarLote()}><IcoX /></button>}
                </div>
              )}
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
              {(crate.items || []).map((it, k) => {
                const tk = fromCrateItem(it).key
                const suena = player.isPlaying(tk)
                const est = it.descargado ? null : dl.items[it.id]
                // El motivo, en texto y al lado del tema: por qué no se puede bajar (sin link) o
                // por qué falló el último intento (lo que dijo el server, tal cual).
                const motivo = it.descargado ? null
                  : est?.estado === 'error' ? `No se bajó: ${est.mensaje}`
                    : it.motivo_no_bajable || null
                const motivoId = motivo ? `motivo-${it.id}` : undefined
                return (
                <div className="trk" key={it.id}>
                  {/* Reordenar no está implementado: el ícono es decorativo (antes anunciaba "Reordenar" sin hacer nada). */}
                  <div className="drag" aria-hidden="true"><IcoDrag /></div>
                  <div className={`thumb${player.current?.key === tk ? ' is-current' : ''}`} onClick={() => reproducir(k)}>
                    <Cover track={it} />
                    <button type="button" className="thumb-play" aria-label={`${suena ? 'Pausar' : 'Reproducir'} ${it.titulo}`} onClick={(e) => { e.stopPropagation(); reproducir(k) }}>{suena ? <IcoPause /> : <IcoPlay />}</button>
                  </div>
                  <div className="trk-id">
                    <div className="trk-title" title={it.titulo}>{it.titulo}</div>
                    <div className="trk-artist"><span className="truncate">{it.artista}</span><span className="sep">·</span><span className="mono">{fmtDur(it.duracion)}</span></div>
                    {motivo && <div id={motivoId} className={`trk-motivo${est?.estado === 'error' ? ' is-error' : ''}`}>{motivo}</div>}
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
                      : it.motivo_no_bajable
                        ? <span className="status status-need" aria-describedby={motivoId}>Sin link</span>
                        : (() => {
                          // Mientras baja (o espera en la tanda) es el MISMO botón, con
                          // aria-disabled y no `disabled`: así no se pierde el foco del teclado.
                          const ocupado = est?.estado === 'bajando' || est?.estado === 'cola'
                          const accion = est?.estado === 'bajando' ? 'Bajando' : est?.estado === 'cola' ? 'En cola para bajar'
                            : est?.estado === 'error' ? 'Reintentar bajar' : 'Bajar'
                          return (
                            <button type="button" className={`btn btn-secondary btn-sm trk-bajar${ocupado ? ' is-busy' : ''}`}
                              aria-label={`${accion} «${it.titulo}»`} aria-disabled={ocupado || undefined}
                              aria-describedby={motivoId} title={ocupado ? undefined : `Bajar en ${formato.toUpperCase()}`}
                              onClick={() => { if (!ocupado) bajarUno(it) }}>
                              {est?.estado === 'bajando' ? <><span className="spinner" aria-hidden="true" /> Bajando…</>
                                : est?.estado === 'cola' ? 'En cola'
                                  : <><IcoDown /> {est?.estado === 'error' ? 'Reintentar' : 'Bajar'}</>}
                            </button>
                          )
                        })()}
                  </div>
                  <div className="trk-acts">
                    <button type="button" className="btn btn-icon-sm" aria-label={`Quitar ${it.titulo} de la playlist`} title="Quitar" onClick={() => quitar(it.id)}><IcoX /></button>
                  </div>
                </div>
                )
              })}
            </div>
          </>
        )}
      </section>

      {exportOpen && crate && <ExportDialog crate={crate} toast={toast} onClose={() => setExportOpen(false)} />}
    </div>
  )
}
