import { useEffect, useState } from 'react'
import { songKey, metaKey, loadFormat, saveFormat } from './utils'
import { buscar, buscarLista, parecidasLista, descargar, esperarJob, historial, getPlaylistGuardada, borrarPlaylist, limpiarHistorial, playlistActiva, listarPlaylists } from './api'
import { useConsole, useMeta, usePreview } from './hooks'
import { useToast } from './toast.jsx'
import TopBar from './components/TopBar'
import ConsoleDrawer from './components/ConsoleDrawer'
import HistorialDrawer from './components/HistorialDrawer'
import Modal from './components/Modal'
import Playlists from './components/Playlists'
import PlaylistsRail from './components/PlaylistsRail'
import { Home, ResultsView, ListForm, ListResults } from './components/views'

export default function App() {
  // Único estado del formato de descarga: default WAV, o lo que el usuario eligió y quedó guardado.
  const [formato, setFormato] = useState(loadFormat)
  const chooseFormat = (f) => { setFormato(f); saveFormat(f) }
  const [genero, setGenero] = useState('')   // filtro de género para las búsquedas
  const [activePlaylist, setActivePlaylist] = useState(null)  // crate activa (auto-add al descargar)
  const [misPlaylists, setMisPlaylists] = useState([])        // para el rail lateral siempre visible
  const [previewEnabled, setPreviewEnabled] = useState(true)
  const [consoleOpen, setConsoleOpen] = useState(false)
  const [historialOpen, setHistorialOpen] = useState(false)
  const [historialData, setHistorialData] = useState(null)
  const [view, setView] = useState({ kind: 'home' })
  const [modal, setModal] = useState(null)

  // Estado de descarga por canción (sobrevive a cambios de opción).
  const [dl, setDl] = useState({})

  const toast = useToast()
  const { metaMap, enrich } = useMeta()
  const { connected, lines } = useConsole()
  const preview = usePreview(previewEnabled, !!modal)

  // Escape cierra el modal
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') setModal(null) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  // Playlist activa (para el chip de la topbar + auto-add al descargar)
  useEffect(() => { playlistActiva().then((d) => setActivePlaylist(d.activa || null)).catch(() => {}) }, [])
  // Rail lateral: la lista de playlists, siempre a la vista (se refresca si cambia la activa).
  useEffect(() => { listarPlaylists().then((d) => setMisPlaylists(Array.isArray(d) ? d : (d.playlists || []))).catch(() => {}) }, [activePlaylist])
  // …y cuando se crea/borra/modifica una playlist desde cualquier lado (evento 'musiflix:playlists').
  // Antes el rail no se enteraba: crear una desde el ＋ de una tarjeta no la mostraba hasta recargar.
  useEffect(() => {
    const recargar = () => listarPlaylists().then((d) => setMisPlaylists(Array.isArray(d) ? d : (d.playlists || []))).catch(() => {})
    window.addEventListener('musiflix:playlists', recargar)
    return () => window.removeEventListener('musiflix:playlists', recargar)
  }, [])

  /* ---------- Navegación / búsquedas ---------- */
  const goHome = () => { setModal(null); setView({ kind: 'home' }) }
  const openListaForm = () => { setModal(null); setView({ kind: 'listaForm' }) }
  // id opcional (desde el rail): abre esa playlist. Desde otros botones llega el evento → se ignora.
  // `pick` cambia en cada click: volver a tocar en el rail la misma playlist después de elegir otra
  // adentro de la vista tiene que volver a seleccionarla.
  const openPlaylists = (id) => { setModal(null); setView({ kind: 'playlists', id: typeof id === 'number' ? id : null, pick: Date.now() }) }

  // Mensajes de error que dicen qué hacer, no solo qué pasó.
  const HINT_CONEXION = 'Revisá que el servidor esté corriendo y volvé a intentar.'

  const doSearch = async (q) => {
    setModal(null)
    const etiqueta = q ? `"${q}"${genero ? ` · ${genero}` : ''}` : genero
    setView({ kind: 'loading', message: `Buscando ${etiqueta}…` })
    try {
      const d = await buscar(q, formato, genero)
      const grupos = d.grupos && d.grupos.length ? d.grupos : null
      if (!d.exito || !grupos) {
        setView({ kind: 'error', emoji: '😕', message: d.mensaje || 'Sin resultados',
          hint: genero ? 'Probá con otras palabras o quitá el filtro de género.' : 'Probá con otras palabras o con el nombre del artista.' })
        return
      }
      // Una fila por TEMA con sus versiones (mismo componente que el modo lista).
      const sel = grupos.map(() => 0)
      setView({ kind: 'lista', data: { groups: grupos, sel, origen: 'busqueda', query: q, total: grupos.length } })
      enrich(grupos.map((g) => g.opciones[0]))
    } catch {
      setView({ kind: 'error', emoji: '⚠️', message: 'Error al conectar con el servidor.', hint: HINT_CONEXION })
    }
  }

  const applyLista = (d) => {
    if (!d.exito || !d.grupos || !d.grupos.length) {
      setView({ kind: 'error', emoji: '😕', message: (d && d.mensaje) || 'No encontré ninguna de la lista.',
        hint: 'Revisá que haya un tema por línea, idealmente «Artista - Título».' })
      return
    }
    const sel = d.grupos.map(() => 0)
    setView({ kind: 'lista', data: { groups: d.grupos, sel, seed: d.seed, encontradas: d.encontradas, total: d.total, no_encontradas: d.no_encontradas } })
    enrich(d.grupos.map((g) => g.opciones[0]))
  }

  const doBuscarLista = async (text) => {
    setModal(null)
    setView({ kind: 'loading', message: 'Buscando temas (hasta 3 opciones c/u)… puede tardar unos segundos.' })
    try { applyLista(await buscarLista(text, formato)) }
    catch { setView({ kind: 'error', emoji: '⚠️', message: 'Error al conectar con el servidor.', hint: HINT_CONEXION }) }
  }

  const doParecidas = async (c) => {
    setModal(null)
    setView({ kind: 'loading', message: 'Buscando temas parecidos y sus opciones por plataforma… puede tardar unos segundos.' })
    try {
      // Prioridad: el filtro de género elegido > el género conocido del tema
      const gen = genero || c.genero || (metaMap[metaKey(c)] || {}).genero || ''
      const d = await parecidasLista(c.titulo, c.artista, formato, gen)
      if (d.exito) applyLista(d)
      else setView({ kind: 'error', emoji: '😕', message: d.mensaje || 'No se pudo armar la playlist de parecidas.', hint: 'Probá con otro tema.' })
    } catch {
      setView({ kind: 'error', emoji: '⚠️', message: 'Error al armar la playlist de parecidas.', hint: HINT_CONEXION })
    }
  }

  const onSelect = (i, k) => {
    if (view.kind !== 'lista') return
    const sel = [...view.data.sel]
    if (sel[i] === k) return
    sel[i] = k
    setView({ ...view, data: { ...view.data, sel } })
    preview.cancel(); preview.stop()
    enrich([view.data.groups[i].opciones[k]])
  }

  /* ---------- Acciones sobre una canción ---------- */
  const play = (c) => { preview.stop(); setModal({ kind: 'player', song: c }) }
  const openSpek = (c) => { preview.stop(); setModal({ kind: 'spek', song: c }) }
  const openCompare = (options, titulo) => { preview.stop(); setModal({ kind: 'compare', options, titulo }) }

  const onDownload = async (c) => {
    const key = songKey(c)
    setDl((p) => ({ ...p, [key]: { ...(p[key] || {}), state: 'busy' } }))
    try {
      // Mandamos los metadatos que ya tenemos (para taggear el archivo: BPM/key/género/carátula).
      const m = metaMap[metaKey(c)] || {}
      const enviado = await descargar({
        titulo: c.titulo, artista: c.artista, fuente: c.fuente, url: c.url, formato,
        bpm: c.bpm || m.bpm, genero: c.genero || m.genero, camelot: c.camelot,
        thumbnail: c.thumbnail, duracion: c.duracion,
      })
      // Con worker: la descarga se encola → seguimos el job hasta que termina.
      // Sin worker (local): la respuesta ya es el resultado final.
      const d = (enviado && enviado.encolado && enviado.job_id)
        ? await esperarJob(enviado.job_id)
        : enviado
      if (d.exito) {
        setDl((p) => ({ ...p, [key]: { state: 'ok', calidad: d.calidad, title: '' } }))
        const g = d.calidad?.grade
        toast.ok({
          title: `${c.titulo}${c.artista ? ` — ${c.artista}` : ''}`,
          body: `Descargado y etiquetado${g && g !== '?' ? ` · nota ${g}` : ''} · ${formato.toUpperCase()}.`,
        })
        return true
      }
      setDl((p) => ({ ...p, [key]: { state: 'err', title: d.mensaje || '' } }))
      toast.danger({ title: 'No se pudo descargar', body: d.mensaje || 'Falló la descarga.',
        actions: [{ label: 'Reintentar', onClick: () => onDownload(c) }] })
      return false
    } catch {
      setDl((p) => ({ ...p, [key]: { state: 'err' } }))
      toast.danger({ title: 'Error al descargar', body: 'No pude conectar con el servidor.',
        actions: [{ label: 'Reintentar', onClick: () => onDownload(c) }] })
      return false
    }
  }

  const togglePreview = () => {
    setPreviewEnabled((on) => {
      const next = !on
      if (!next) { preview.cancel(); preview.stop() }
      return next
    })
  }

  /* ---------- Historial ---------- */
  // Sin conexión el cajón mostraba "Cargando historial…" para siempre: ahora muestra el error.
  const refreshHistorial = async () => { try { setHistorialData(await historial()) } catch { setHistorialData({ error: true }) } }
  const toggleHistorial = () => {
    setHistorialOpen((o) => {
      const next = !o
      if (next) { setHistorialData(null); refreshHistorial() }
      return next
    })
  }
  const onRunSearch = (q) => { setHistorialOpen(false); doSearch(q) }
  const onOpenPlaylist = async (id) => {
    try {
      const d = await getPlaylistGuardada(id)
      if (d.exito && d.data) {
        setModal(null)
        setView({ kind: 'lista', data: d.data })
        enrich(d.data.groups.map((g) => g.opciones[0]))
        setHistorialOpen(false)
      } else toast.danger({ title: 'No pude abrir esa playlist', body: d.mensaje || 'Puede que se haya borrado. Actualizá el historial.' })
    } catch { toast.danger({ title: 'No pude abrir esa playlist', body: HINT_CONEXION }) }
  }
  // Decisión tomada (a validar): borrar una playlist del historial pide confirmación, igual
  // que vaciar secciones o borrar una playlist propia. Antes se borraba al primer click.
  const onDeletePlaylist = async (id, nombre) => {
    if (!window.confirm(`¿Borrar «${nombre || 'Playlist'}» del historial? No se puede deshacer.`)) return
    try { await borrarPlaylist(id); refreshHistorial() }
    catch { toast.danger({ title: 'No pude borrar la playlist del historial', body: HINT_CONEXION }) }
  }
  const onLimpiar = async (que) => {
    const txt = que === 'todo' ? 'todo el historial' : `las ${que}`
    if (!window.confirm(`¿Vaciar ${txt}? No se puede deshacer.`)) return
    try {
      await limpiarHistorial(que)
      refreshHistorial()
      toast.info({ title: que === 'todo' ? 'Historial vaciado' : `Se vaciaron las ${que}` })
    } catch { toast.danger({ title: 'No pude vaciar el historial' }) }
  }

  /* ---------- Render ---------- */
  const shared = { formato, metaMap, preview, dl, onPlay: play, onSpek: openSpek, onDownload, onCompare: openCompare, onParecidas: doParecidas }
  let body
  if (view.kind === 'home') body = <Home toast={toast} />
  else if (view.kind === 'loading') body = <div className="empty"><span className="spinner" aria-hidden="true" /><p>{view.message}</p></div>
  else if (view.kind === 'error') body = <div className="empty"><p>{view.emoji || '⚠️'} {view.message}</p>{view.hint && <p>{view.hint}</p>}</div>
  else if (view.kind === 'listaForm') body = <ListForm formato={formato} onBuscar={doBuscarLista} onCancel={goHome} />
  else if (view.kind === 'search') body = <ResultsView data={view.data} {...shared} onParecidas={doParecidas} />
  else if (view.kind === 'lista') body = <ListResults data={view.data} {...shared} onSelect={onSelect} onEditar={openListaForm} />
  else if (view.kind === 'playlists') body = <Playlists activePlaylist={activePlaylist} setActivePlaylist={setActivePlaylist} toast={toast} onPlay={play} initialId={view.id} pick={view.pick} />

  // Lo que cambia en pantalla sin mover el foco (buscando, error, resultados) se anuncia
  // por una región viva: sin esto un lector de pantalla no se entera de que terminó.
  const anuncio = view.kind === 'loading' ? view.message
    : view.kind === 'error' ? `${view.message} ${view.hint || ''}`.trim()
    : view.kind === 'lista'
      ? (view.data.origen === 'busqueda'
        ? `${view.data.groups.length} temas encontrados.`
        : `${view.data.encontradas ?? view.data.groups.length} de ${view.data.total ?? view.data.groups.length} temas encontrados.`)
      : ''

  const drawerAbierto = consoleOpen || historialOpen
  return (
    <div className="app">
      <TopBar
        formato={formato} setFormato={chooseFormat}
        genero={genero} setGenero={setGenero}
        onSearch={doSearch}
        previewEnabled={previewEnabled} togglePreview={togglePreview}
        onLista={openListaForm}
        onPlaylists={openPlaylists} activePlaylist={activePlaylist}
        onConsola={() => setConsoleOpen((o) => !o)} consoleActive={consoleOpen}
        onHistorial={toggleHistorial} historialActive={historialOpen}
        onBrand={goHome}
      />
      <div className="app-body">
        {/* Siempre a la vista, en TODAS las pantallas (decisión del dueño 2026-09-17): antes solo
            se dibujaba en la home y al entrar a Playlists o buscar desaparecía. */}
        <PlaylistsRail playlists={misPlaylists} activa={activePlaylist} onOpen={openPlaylists} />

        <main className="app-main"><div className="app-wrap">{body}</div></main>
      </div>
      <div className="sr-only" role="status" aria-live="polite">{anuncio}</div>
      {drawerAbierto && <div className="scrim" onClick={() => { setConsoleOpen(false); setHistorialOpen(false) }} />}
      <ConsoleDrawer open={consoleOpen} onClose={() => setConsoleOpen(false)} connected={connected} lines={lines} />
      <HistorialDrawer open={historialOpen} onClose={() => setHistorialOpen(false)} data={historialData}
        onRunSearch={onRunSearch} onOpenPlaylist={onOpenPlaylist} onDeletePlaylist={onDeletePlaylist} onLimpiar={onLimpiar} />
      <Modal modal={modal} onClose={() => setModal(null)} onDownload={onDownload} />
    </div>
  )
}
