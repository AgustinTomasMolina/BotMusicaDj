import { useState, useEffect, useRef } from 'react'
import { getBiblioteca } from '../api'
import { fmtDur, FUENTE_CORTO, songKey, metaKey } from '../utils'
import { DlButton, PreviewLayer, QualityBadge, gradeClass } from './common'
import { AddToPlaylist } from './AddToPlaylist'
import Cover from './Cover'
import { usePlayer } from '../player/context'
import { fromLibrary, fromResult, fmtBpm } from '../player/track'
import { IconDownload, IconActivity, IconCompare, IconRadioTower, IconLock, IconPlus } from './icons'
import { ordenVisible, filaMezcla, medidoTexto, estadoMedicion, aclaracionOrden } from '../stationMezcla'

// fuente → clase de plataforma de Nocturne (define el color --pf del chip)
const PF = { youtube: 'pf-yt', soundcloud: 'pf-sc', spotify: 'pf-sp', ligaudio: 'pf-m1', hitplayer: 'pf-m2', deezer: 'pf-sp' }

/* ---------- Pantalla de inicio ---------- */
const NoteIcon = () => (
  <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="7" cy="17" r="3" /><path d="M10 17V5l9-2v3l-9 2" /></svg>
)

// Tarjeta de un track de la biblioteca: carátula (con play/pausa), título, artista y ＋ a playlist.
// La carátula es la embebida en el archivo (/api/cover/{id}); sin ella, el placeholder
// decorativo (antes: un degradado oscuro casi del color del fondo, elegido por posición).
function LibCard({ t, i, playing, onToggle }) {
  const key = t.camelot || t.tonalidad || ''
  // BPM con un decimal (§6): el XML lo trae medido con decimales; "128" sería redondear.
  const bpm = fmtBpm({ bpm: t.bpm, bpmMedido: true })
  const meta = [bpm, key || null].filter(Boolean).join(' · ')
  return (
    <div className="lib-card" style={{ animationDelay: `${Math.min(i, 12) * 0.04}s` }}>
      <div className={`lib-cover${playing ? ' is-playing' : ''}`}>
        <Cover track={fromLibrary(t)} className="lib-cover-img" />
        {/* El nombre incluye el tema: 18 botones "Reproducir" iguales no dicen cuál suena. */}
        <button type="button" className={`lib-play${playing ? ' on' : ''}`}
          aria-label={`${playing ? 'Pausar' : 'Reproducir'} ${t.titulo}`} onClick={onToggle}>
          {playing
            ? <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" /><rect x="14" y="5" width="4" height="14" /></svg>
            : <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z" /></svg>}
        </button>
        {meta && <span className="lib-meta">{meta}</span>}
      </div>
      <div className="lib-title" title={t.titulo}>{t.titulo}</div>
      <div className="lib-artist" title={t.artista}>{t.artista || '—'}</div>
      <div className="lib-actions">
        <AddToPlaylist track={{ titulo: t.titulo, artista: t.artista, bpm: t.bpm, camelot: t.camelot, genero: t.genero }} />
      </div>
    </div>
  )
}

/* ---------- Pantalla de inicio: estantes por género desde la biblioteca local ---------- */
// Suena en la barra de abajo (antes tenía su propio <audio>, que se cortaba al salir de la
// home): la cola es el estante desde el que se tocó play.
export function Home({ onPlay }) {
  const [data, setData] = useState(null)      // {total, configurada, generos:[{genero,tracks}]}
  const [gf, setGf] = useState('Todos')       // filtro de género
  const player = usePlayer()

  useEffect(() => {
    let vivo = true
    getBiblioteca()
      .then((d) => {
        if (!vivo) return
        // Orden semi-aleatorio de los estantes: la home se siente distinta cada vez que entrás.
        const gs = [...(d.generos || [])].sort(() => Math.random() - 0.5)
        setData({ ...d, generos: gs })
      })
      // Sin conexión no es "sin configurar": se explica con motivo en vez de pedir variables.
      .catch(() => vivo && setData({ total: 0, configurada: false, generos: [],
        motivo: 'No pude conectar con el servidor para leer la biblioteca. Revisá que esté corriendo y recargá.' }))
    return () => { vivo = false }
  }, [])

  // Si el archivo no se puede abrir, la barra lo dice (antes era un aviso suelto).
  const toggle = (shelf, k) => onPlay(shelf.tracks.slice(0, 18).map(fromLibrary), k)

  if (!data) return <div className="empty"><span className="spinner" aria-hidden="true" /><p>Cargando tu biblioteca…</p></div>
  if (!data.total) {
    return (
      <div className="empty">
        <div className="empty-art"><NoteIcon /></div>
        <h3>{data.motivo ? 'Biblioteca no disponible' : data.configurada ? 'Tu biblioteca está vacía' : 'Conectá tu biblioteca'}</h3>
        {/* motivo: el server explica por qué no pudo leerla (sin lector, XML ilegible). */}
        <p>{data.motivo
          ? `${data.motivo} Mientras tanto, buscá un tema arriba.`
          : data.configurada
            ? 'No encontré audios resueltos. Revisá las rutas de la biblioteca (MUSIFLIX_LIBRARY_ROOTS).'
            : 'Definí MUSIFLIX_LIBRARY_XML y MUSIFLIX_LIBRARY_ROOTS para ver tus temas por género acá. Mientras tanto, buscá un tema arriba.'}</p>
      </div>
    )
  }

  const chips = ['Todos', ...data.generos.map((s) => s.genero)]
  const shelves = gf === 'Todos' ? data.generos : data.generos.filter((s) => s.genero === gf)

  return (
    <div className="home">
      <div className="home-head">
        <h1>Para arrancar</h1>
        <p className="muted">{data.total} temas en tu biblioteca · el orden cambia cada vez que entrás</p>
      </div>
      {/* Botones con aria-pressed: role="tab" sin tabpanel ni flechas prometía un teclado que no existía. */}
      <div className="genre-chips" role="group" aria-label="Filtrar por género">
        {chips.map((g) => (
          <button key={g} type="button" aria-pressed={gf === g}
            className={`chip${gf === g ? ' on' : ''}`} onClick={() => setGf(g)}>{g}</button>
        ))}
      </div>
      {shelves.map((shelf, si) => (
        <section className="shelf" style={{ animationDelay: `${Math.min(si, 8) * 0.06}s` }} key={shelf.genero}>
          <div className="shelf-head"><h2>{shelf.genero}</h2><span className="muted">{shelf.tracks.length}</span></div>
          <div className="shelf-row">
            {shelf.tracks.slice(0, 18).map((t, i) => (
              <LibCard key={t.id} t={t} i={i} playing={player.isPlaying(`biblioteca|${t.id}`)} onToggle={() => toggle(shelf, i)} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

/* ---------- Estado de las versiones de una fila ----------
   Dos estados distintos, y los dos se ven sin depender del color:
   - ELEGIDA (la que bajan Descargar y el play de la fila): pastilla llena con un tilde en vez
     del número.
   - SONANDO (la que está cargada en la barra): barritas de nivel en vez del punto y un aro;
     quietas si la barra está en pausa. La fila además dice en texto qué opción suena. */
const IconChosen = () => <svg className="vchip-ok" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
const Eq = ({ on }) => <span className={`eq${on ? '' : ' is-quieto'}`} aria-hidden="true"><i /><i /><i /></span>
const ROW_STATUS = {
  playing: 'Sonando', loading: 'Cargando', paused: 'En pausa', ended: 'Terminó',
  error: 'No se pudo reproducir', embed: 'En el reproductor de Spotify', none: 'Sin audio',
}
const sourceName = (o) => FUENTE_CORTO[(o?.fuente || '').toLowerCase()] || o?.fuente || 'fuente desconocida'

/* ---------- Fila de un tema: 7 columnas Nocturne (.trk) ---------- */
// VERSIONES son pastillas en línea en todas las vistas (búsqueda, modo lista, playlists
// guardadas y Station). La Station tuvo un diseño plegado (f38, "+N versiones" con sub-lista)
// que el dueño cambió por estas mismas pastillas (f40).
function TrackRow({ g, i, pos = i, sel, formato, metaMap, preview, dl, playing, current, loadedIdx, playerStatus, onPlay, onSpek, onDownload, onSelect, onCompare, onStation, medido, mezclaFila }) {
  const c = g.opciones[sel]
  // f45: `medido` = análisis de audio de la fila de la Station (undefined fuera de la Station;
  // null mientras se analiza). `pos` = lugar en pantalla (cambia con el orden "Para mezclar").
  const esMedida = medido !== undefined
  const med = esMedida && medido ? medidoTexto(medido) : null
  // Station (f40): la fila ES el tema de la Station. Título, artista y duración son los suyos
  // aunque se elija la versión de otra plataforma: el título de un video ("… (Official Video)")
  // o su duración con intro hacían parecer que la fila era otra edición.
  const ficha = g.base || c
  // "Sonando" (barritas animadas, aro) SOLO cuando suena de verdad. Mientras carga —2 a 5 s
  // en frío en YouTube/SoundCloud— decirlo sería mentir (§6); la pastilla dice "cargando"
  // con las barritas quietas, igual que la fila ("Cargando: opción…").
  const isLive = loadedIdx >= 0 && playerStatus === 'playing'
  const thumbKey = `t${i}`
  const rowPrev = preview.current && (preview.current.key === thumbKey || preview.current.key.startsWith(`o${i}:`))
    ? preview.current.song : null
  // Station (f36): si se eligió la versión de otra plataforma, BPM/género siguen siendo los del
  // tema de la Station (`g.base`), que es el mismo tema.
  const m = metaMap[metaKey(c)] || (g.base ? metaMap[metaKey(g.base)] : null) || {}
  // El tema de la Station es Go+ (30 s) pero se eligió una versión completa de otra plataforma.
  const goPlusResuelto = !c.solo_preview && g.opciones.some((o) => o.estacion && o.solo_preview)
  const bpm = c.bpm || m.bpm
  const genero = c.genero || m.genero
  const key = c.camelot
  return (
    <div className={`trk${loadedIdx >= 0 ? ' is-sonando' : ''}`} aria-current={loadedIdx >= 0 ? 'true' : undefined}
      onMouseEnter={() => preview.schedule(thumbKey, c)}
      onMouseLeave={() => { preview.cancel(); preview.stop() }}>
      <div className="trk-idx">{String(pos + 1).padStart(2, '0')}</div>
      <div className={`thumb${current ? ' is-current' : ''}`} onClick={() => onPlay(pos)}>
        {/* Carátula decorativa: el título está al lado. Si la imagen no carga, prueba la
            siguiente fuente y después el placeholder (ver src/cover.js). */}
        <Cover track={c} />
        <button type="button" className="thumb-play" aria-label={`${playing ? 'Pausar' : 'Reproducir'} ${c.titulo}`} onClick={(e) => { e.stopPropagation(); onPlay(pos) }}>
          {playing
            ? <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><rect x="6.5" y="5.5" width="4" height="13" rx="1" fill="currentColor" /><rect x="13.5" y="5.5" width="4" height="13" rx="1" fill="currentColor" /></svg>
            : <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5l10 6.5-10 6.5z" fill="currentColor" /></svg>}
        </button>
        {/* key por canción: al pasar a otra versión de la fila se monta una capa nueva (volumen y cue incluidos). */}
        {rowPrev && <PreviewLayer key={songKey(rowPrev)} song={rowPrev} />}
      </div>
      <div className="trk-id">
        <div className="trk-title" title={ficha.titulo}>{ficha.titulo}</div>
        {/* Qué versión de esta fila está en la barra, en texto: el color y las barritas solas
            no alcanzan (pedido del dueño 2026-09-28: "no se ve en qué reproducción estás parado"). */}
        {loadedIdx >= 0 && (
          <div className={`trk-now${isLive ? ' is-on' : ''}`}>
            <Eq on={isLive} />
            <span>{ROW_STATUS[playerStatus] || 'En la barra'}: opción {loadedIdx + 1} · {sourceName(g.opciones[loadedIdx])}</span>
          </div>
        )}
        <div className="trk-artist"><span className="truncate">{ficha.artista}</span><span className="sep">·</span><span className="mono">{fmtDur(ficha.duracion)}</span></div>
        {/* Station (f36): por qué faltan versiones (una plataforma que no contestó, SoundCloud
            que frenó) y, si el tema es Go+, de dónde sale el tema completo. */}
        {goPlusResuelto && <div className="trk-note">SoundCloud solo da 30 s (Go+): se baja completo de {sourceName(c)}.</div>}
        {g.motivo && <div className="trk-note is-warn" title={g.motivo}>{g.motivo}</div>}
        {/* f45: el "por qué" del motor (`+1.8% BPM | 8A → 9A (vecino)`) o por qué quedó al final. */}
        {mezclaFila?.razon && <div className="trk-note mono trk-razon" title="Por qué va en este lugar">{mezclaFila.razon}</div>}
        {mezclaFila?.motivo && <div className="trk-note is-warn trk-razon" title={mezclaFila.motivo}>Al final: {mezclaFila.motivo}</div>}
      </div>
      <div className="trk-meta">
        {/* f45: en la Station, BPM y key MEDIDOS sobre el audio (un decimal, "?" si no se pudo o
            si la key es dudosa). El BPM de metadata se rotula como tal: no es una medición (§6). */}
        {esMedida && (med
          ? <>
            <span className="mb mb-medido" title={medido.ok ? `Medido sobre ${medido.preview ? 'el preview de 30 s (no se sabe si vale para el tema entero)' : `${Math.round(medido.tramo_s || 0)} s del audio`}` : (medido.motivo || 'No se pudo analizar')}>
              <span className="mb-label">BPM medido</span><b>{med.bpm}</b></span>
            <span className="mb mb-key mb-medido" title={medido.ok && medido.key_dudosa ? 'Key dudosa: los tramos del tema no votaron todos lo mismo' : undefined}>
              <span className="mb-label">KEY medida</span><b>{med.key}</b></span>
            {medido.ok && medido.preview && <span className="mb mb-warn" title="El BPM y la key salen del preview de 30 s"><b>Medido en preview</b></span>}
          </>
          : <span className="mb mb-medido"><span className="mb-label">BPM medido</span><b>…</b></span>)}
        {bpm && <span className="mb"><span className="mb-label">{esMedida ? 'BPM meta' : 'BPM'}</span><b>{bpm}</b></span>}
        {key && <span className="mb mb-key"><span className="mb-label">{esMedida ? 'KEY meta' : 'KEY'}</span><b>{key}{c.compat ? ' ' + c.compat : ''}</b></span>}
        {genero && <span className="mb"><b>{genero}</b></span>}
        {/* Station de SoundCloud: un tema Go+ solo suena 30 s acá; decirlo, no mostrarlo como completo. */}
        {c.solo_preview && <span className="mb mb-warn" title="SoundCloud solo deja escuchar 30 s de este tema (Go+)"><b>Preview 30 s</b></span>}
        {c.reproducible === false && !c.solo_preview &&
          <span className="mb mb-warn" title="SoundCloud no ofrece un audio que la barra pueda abrir para este tema"><b>Sin audio acá</b></span>}
      </div>
      <div className="trk-grade"><QualityBadge c={c} formato={formato} /></div>
      <div className="trk-vers vchips">
        {g.opciones.map((o, k) => {
          const f = (o.fuente || '').toLowerCase()
          const okey = `o${i}:${k}`
          const chosen = k === sel
          const sounding = k === loadedIdx
          // Nombre accesible completo: aria-pressed dice "elegida"; lo que suena va en el texto
          // (y en aria-current), porque un lector no ve las barritas.
          const stateText = [chosen ? 'elegida' : null, sounding ? (isLive ? 'sonando ahora' : playerStatus === 'loading' ? 'cargando' : 'cargada en la barra') : null].filter(Boolean).join(', ')
          // Nota de cada versión (f36: la Station la trae calculada por /api/calidad). Un preview
          // de 30 s no tiene nota: no es el tema. Spotify tampoco: se baja buscando en YouTube.
          const grade = o.calidad ? (o.calidad.ok ? o.calidad.grade : '?') : null
          const gradeText = o.solo_preview ? 'solo 30 s (Go+)' : grade ? `nota ${grade}` : null
          // f40-r2: el Extended es OTRA edición que la que sonó en la Station (más larga): la
          // pastilla lo dice con su duración. Una versión con la duración sin verificar se
          // ofrece atenuada y lo dice (no viene elegida nunca, ver bestOption).
          const sinVerificar = o.duracion_verificada === false
          const ext = o.edicion === 'extended' ? (sinVerificar ? 'Extended' : `Extended · ${fmtDur(o.duracion)}`) : null
          const extras = [ext, sinVerificar ? 'duración sin verificar' : null].filter(Boolean).join(', ')
          return (
            <button key={k} type="button" className={`vchip ${PF[f] || ''}${sinVerificar ? ' is-sin-verificar' : ''}${sounding ? ' is-playing' : ''}${sounding && isLive ? ' is-on' : ''}`}
              aria-pressed={chosen} aria-current={sounding ? 'true' : undefined}
              aria-label={`Opción ${k + 1}: ${sourceName(o)}${gradeText ? `, ${gradeText}` : ''}${extras ? `, ${extras}` : ''}${stateText ? ` (${stateText})` : ''}`}
              onMouseEnter={() => preview.schedule(okey, o)}
              onClick={() => onSelect(i, k)}
              title={`Opción ${k + 1} · ${sourceName(o)} — ${o.titulo}${gradeText ? ` · ${gradeText}` : ''}${o.calidad?.calidad ? ` (${o.calidad.calidad})` : ''}${ext ? (sinVerificar ? ' · Extended según su título: otra edición que la de la Station' : ` · ${ext}: otra edición, más larga que la de la Station`) : ''}${sinVerificar ? ' · duración sin verificar: no se elige sola' : ''}${f === 'spotify' ? ' · se baja buscándolo en YouTube' : ''}${stateText ? ` · ${stateText}` : ''}`}>
              {chosen ? <IconChosen /> : <span className="n">{k + 1}</span>}
              {sounding ? <Eq on={isLive} /> : <span className="dot" />}
              {FUENTE_CORTO[f] || o.fuente || '?'}
              {ext && <span className="vchip-ed" aria-hidden="true">{sinVerificar ? 'Extended' : `Extended ${fmtDur(o.duracion)}`}</span>}
              {o.solo_preview
                ? <span className="vchip-grade is-preview" aria-hidden="true">30 s</span>
                : grade && <span className={`vchip-grade ${gradeClass(grade)}`} aria-hidden="true">{grade}</span>}
            </button>
          )
        })}
      </div>
      {/* Acciones (f36): separadas de las versiones por una línea, todas del mismo tamaño
          (32 px, íconos de 16) y con nombre y tooltip. */}
      <div className="trk-acts" role="group" aria-label={`Acciones de ${c.titulo}`}>
        <button type="button" className="btn btn-icon-sm" onClick={() => onSpek(c)} title="Espectrograma (Spek)" aria-label={`Espectrograma de ${c.titulo}`}><IconActivity size={16} /></button>
        {g.opciones.length > 1 &&
          <button type="button" className="btn btn-icon-sm" onClick={() => onCompare(g.opciones, g.consulta || c.titulo)} title="Comparar versiones" aria-label={`Comparar versiones de ${c.titulo}`}><IconCompare size={16} /></button>}
        {/* "Station", no "Radio": la Radio de la barra de arriba es la del motor local. */}
        {onStation &&
          <button type="button" className="btn btn-icon-sm" onClick={() => onStation(c, g)} title="Station de SoundCloud: temas del mismo estilo según SoundCloud" aria-label={`Station de SoundCloud de ${c.titulo}`}><IconRadioTower size={16} /></button>}
        <AddToPlaylist track={{ ...c, bpm, genero, camelot: key }} />
        {c.solo_preview
          ? <button type="button" className="btn btn-secondary btn-dl" disabled title="SoundCloud solo da 30 s de este tema: no se descarga como si fuera el tema"
            aria-label={`Descargar ${c.titulo} (no disponible: SoundCloud solo da un fragmento de 30 s)`}><IconDownload size={16} /></button>
          : <DlButton dl={dl} label={c.titulo} onClick={() => onDownload(c)}><IconDownload size={16} /></DlButton>}
      </div>
    </div>
  )
}

/* ---------- Station: encabezado, panel de estado y filas pendientes (f46) ---------- */
// Diseño elegido por el dueño ("A — una barra de estado con dos progresos"): el título y una
// línea que dice de quién es la recomendación; abajo, un panel con el selector de orden a la
// izquierda y los dos avances (versiones y medición) a la derecha.

const plural = (n, uno, varios) => `${n} ${n === 1 ? uno : varios}`
const textoMedidos = (med) => `${plural(med.medidos, 'medido', 'medidos')}${med.sinMedir ? `, ${med.sinMedir} sin poder medir` : ''}`

// Una barra de avance con su rótulo. Mientras carga, "hechos / total"; al terminar, el estado final.
function Avance({ id, rotulo, detalle, now, max, listo, final, tono }) {
  const pct = max > 0 ? Math.min(100, Math.round((now / max) * 100)) : 0
  return (
    <div className="st-avance" data-avance={id} data-estado={listo ? 'listo' : 'cargando'}>
      <div className="st-avance-head">
        <span id={`st-avance-${id}`}>{rotulo}{detalle && <span className="st-avance-det"> {detalle}</span>}</span>
        <span className="st-avance-val">{listo ? final : `${now} / ${max}`}</span>
      </div>
      <div className={`st-bar is-${tono}`} role="progressbar" aria-labelledby={`st-avance-${id}`}
        aria-valuemin={0} aria-valuemax={max} aria-valuenow={now} aria-valuetext={listo ? final : `${now} de ${max}`}>
        <i style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

function StationEstado({ modo, setModo, hechos, total, cargando, conOtras, med, analizando, mezcla }) {
  const acl = aclaracionOrden({ analizando, mezcla, modo, hechos: med.hechos, total: med.total })
  // Una sola región viva para la medición, con dos textos: al empezar y al terminar (no uno por
  // tema: antes "Analizando N de M…" era role=status y se anunciaba cada tema). El avance de las
  // versiones ya lo anuncia la región de App.jsx (al abrir y al terminar).
  const anuncio = analizando
    ? `Midiendo BPM y key de ${plural(med.total, 'tema', 'temas')}.`
    : `Medición terminada: ${textoMedidos(med)}. ${acl.bloqueado ? acl.texto : '«Para mezclar» disponible.'}`
  return (
    <section className="st-estado" aria-label="Estado de la Station">
      <div className="station-orden">
        <span className="st-rotulo" id="station-orden-label">Orden</span>
        <div className="st-seg" role="group" aria-labelledby="station-orden-label">
          <button type="button" aria-pressed={modo === 'soundcloud'} onClick={() => setModo('soundcloud')}>SoundCloud</button>
          <button type="button" aria-pressed={modo === 'mezcla'} disabled={acl.bloqueado} aria-describedby="station-orden-hint"
            onClick={() => setModo('mezcla')}>
            {acl.bloqueado && <IconLock size={14} />}Para mezclar
          </button>
        </div>
        <p className="st-hint" id="station-orden-hint" data-bloqueado={acl.bloqueado ? 'true' : 'false'}>{acl.texto}</p>
      </div>
      <div className="st-avances">
        <Avance id="versiones" tono="accent" now={hechos} max={total} listo={!cargando}
          rotulo={cargando ? 'Buscando versiones en otras plataformas' : 'Versiones en otras plataformas'}
          final={`${conOtras} de ${total} con versiones`} />
        {med.total > 0
          ? <Avance id="medicion" tono="info" now={med.hechos} max={med.total} listo={med.hechos >= med.total}
            rotulo={med.hechos >= med.total ? 'BPM y key' : 'Midiendo BPM y key'}
            detalle={med.incluyeSemilla ? '(incluye el tema original)' : null}
            final={textoMedidos(med)} />
          : <p className="st-hint">No hay temas con id de SoundCloud para medir BPM y key.</p>}
      </div>
      <span className="sr-only" role="status">{anuncio}</span>
    </section>
  )
}

// Un tema de la Station que todavía no tiene sus versiones: la forma de la fila final (mismas
// columnas), con casilleros grises donde van los datos y las acciones deshabilitadas.
// "Buscando versiones…" si su pedido ya salió; "En cola" si espera turno (stationVersions.js).
function FilaPendiente({ item, pos, buscando }) {
  return (
    <div className="trk-pending" aria-hidden="true" data-estado={buscando ? 'buscando' : 'cola'}>
      <div className="trk-idx">{String(pos + 1).padStart(2, '0')}</div>
      <div className="thumb"><Cover track={item} /></div>
      <div className="trk-id">
        <div className="trk-title" title={item.titulo}>{item.titulo}</div>
        <div className="trk-artist"><span className="truncate">{item.artista}</span>
          {item.duracion ? <><span className="sep">·</span><span className="mono">{fmtDur(item.duracion)}</span></> : null}</div>
      </div>
      <div className="trk-meta"><span className="sk" style={{ width: 72 }} /><span className="sk" style={{ width: 56 }} /></div>
      <div className="trk-grade"><span className="sk sk-grade" /></div>
      <div className="trk-vers st-pend-vers">
        {buscando ? <><span className="spinner" /> Buscando versiones…</> : 'En cola'}
      </div>
      <div className="trk-acts">
        <button type="button" className="btn btn-icon-sm" disabled tabIndex={-1}><IconPlus size={16} /></button>
        <button type="button" className="btn btn-icon-sm" disabled tabIndex={-1}><IconDownload size={16} /></button>
      </div>
    </div>
  )
}

/* ---------- Vista de resultados (búsqueda unificada / modo lista / Station) ---------- */
export function ListResults({ data, formato, metaMap, preview, dl, onPlay, onSpek, onDownload, onSelect, onEditar, onCompare, onStation }) {
  const { groups, sel, encontradas, total, no_encontradas, origen, query, station, cargando, items } = data
  const esBusqueda = origen === 'busqueda'
  const esStation = origen === 'station'
  const player = usePlayer()
  // f45: "Orden: SoundCloud | Para mezclar". El default es SoundCloud; `vista` son los índices
  // de `groups` en el orden en pantalla (con SoundCloud, la identidad).
  const [modo, setModo] = useState('soundcloud')
  const { analisis, analizando, mezcla, pedidos = 0 } = data
  const vista = esStation ? ordenVisible(groups, modo, mezcla) : groups.map((_, k) => k)
  // f46: la medición cuenta lo mismo que se manda a medir (refsAnalisis): semilla + temas con id.
  const med = esStation ? estadoMedicion(analisis, station?.video_id, items) : null
  // Cola de la barra: la versión elegida de cada tema, en el orden de la lista EN PANTALLA.
  const reproducir = (p) => onPlay(vista.map((k) => fromResult(groups[k].opciones[sel[k]], metaMap, { n: sel[k] + 1, de: groups[k].opciones.length })), p)
  const [allLabel, setAllLabel] = useState(null)
  const [allBusy, setAllBusy] = useState(false)
  // Station cargando (f36): "Descargar todas" pregunta si bajar las filas listas o esperar.
  const [askAll, setAskAll] = useState(false)
  const [waitAll, setWaitAll] = useState(false)
  const faltan = esStation && cargando ? total - groups.length : 0

  const descargarTodas = async () => {
    setAskAll(false)
    setAllBusy(true)
    let ok = 0
    // Una foto de las filas de ahora: las que lleguen mientras baja no entran en esta tanda.
    const filas = groups.map((g, i) => g.opciones[sel[i]])
    const bajables = filas.filter((c) => !c.solo_preview)   // 30 s de SoundCloud no son el tema
    for (let i = 0; i < bajables.length; i++) {
      setAllLabel(`Bajando ${i + 1}/${bajables.length}…`)
      const good = await onDownload(bajables[i])
      if (good) ok++
    }
    setAllBusy(false)
    const sinTema = filas.length - bajables.length
    setAllLabel(`✓ ${ok}/${bajables.length} descargadas${sinTema ? ` · ${sinTema} sin versión completa` : ''}`)
  }
  const pedirTodas = () => (faltan > 0 ? setAskAll(true) : descargarTodas())
  // "Esperar y bajar todas": arranca sola cuando la última fila llega.
  useEffect(() => {
    if (waitAll && esStation && !cargando) { setWaitAll(false); descargarTodas() }
  }, [waitAll, esStation, cargando]) // eslint-disable-line react-hooks/exhaustive-deps
  // Cuántas filas terminaron con versiones de otras plataformas (resumen al terminar).
  const conOtras = esStation ? groups.filter((g) => g.opciones.some((o) => !o.estacion)).length : 0
  // f46: TODOS los temas que faltan, con la forma de la fila final (antes, solo el próximo).
  const pendientes = esStation && cargando && items ? items.slice(groups.length) : []

  return (
    <>
      {/* Sin encabezado visible en esta vista: uno oculto para navegar por títulos con lector. */}
      {!esStation && <h1 className="sr-only">{esBusqueda ? `Resultados${query ? ` de ${query}` : ''}` : 'Resultados de la lista'}</h1>}

      <div className={`cluster${esStation ? ' station-top' : ''}`} style={{ padding: '0 var(--space-3) var(--space-3)' }}>
        {esStation
          // Encabezado honesto (f46): es el recomendador de SoundCloud (su orden, sus temas), no
          // una medición nuestra; por eso no hay BPM ni tonalidad de la semilla acá. "SoundCloud"
          // una sola vez: en la línea de abajo, que dice de quién es la recomendación.
          ? <header className="station-head">
            <h1 className="station-title">Station de «{station?.titulo}»</h1>
            <p className="station-sub">
              {station?.artista && <>{station.artista} · </>}
              {plural(total, 'tema recomendado', 'temas recomendados')} por SoundCloud
              {modo === 'mezcla' && ` · ${total === 1 ? 'ordenado' : 'ordenados'} para mezclar por el motor`}
            </p>
          </header>
          : <span className="eyebrow">{esBusqueda ? `Resultados${query ? ` · ${query}` : ''}` : `${encontradas}/${total} encontradas`}</span>}
        <span className="push cluster" style={{ gap: 'var(--space-2)' }}>
          {!esBusqueda && !esStation && <button type="button" className="btn btn-ghost" onClick={onEditar}>Editar lista</button>}
          <button type="button" className="btn btn-primary" onClick={pedirTodas} disabled={allBusy || waitAll || !groups.length} aria-expanded={faltan > 0 ? askAll : undefined}>
            {allBusy ? <><span className="spinner" aria-hidden="true" /> {allLabel}</>
              : waitAll ? `Esperando ${faltan} temas…`
              : (allLabel || `Descargar todas (${formato.toUpperCase()})`)}
          </button>
        </span>
        {/* El resumen final de "Descargar todas" se anuncia (el progreso ya sale en los avisos). */}
        <span className="sr-only" role="status">{!allBusy && allLabel ? allLabel.replace('✓ ', '') : ''}</span>
      </div>
      {/* Station todavía cargando: no se baja "todas" sin avisar que faltan. */}
      {askAll && faltan > 0 && (
        <div className="alert alert-warn station-ask" role="group" aria-label="Descargar todas" style={{ margin: '0 var(--space-3) var(--space-3)' }}>
          <div>
            <div className="alert-title">Todavía faltan {faltan} de {total} temas</div>
            <p>Podés bajar ahora los {groups.length} que ya tienen versiones (la mejor de cada uno) o esperar a que estén todos.</p>
            <div className="cluster">
              <button type="button" className="btn btn-primary" onClick={descargarTodas}>Bajar los {groups.length} listos</button>
              <button type="button" className="btn btn-secondary" onClick={() => { setAskAll(false); setWaitAll(true) }}>Esperar y bajar los {total}</button>
              <button type="button" className="btn btn-ghost" onClick={() => setAskAll(false)}>Cancelar</button>
            </div>
          </div>
        </div>
      )}
      {waitAll && (
        <p className="muted station-wait" style={{ margin: '0 var(--space-3) var(--space-3)' }}>
          Se bajan todos cuando terminen de cargar (faltan {faltan}).{' '}
          <button type="button" className="btn btn-ghost" onClick={() => setWaitAll(false)}>No esperar</button>
        </p>
      )}

      {esStation && (
        <StationEstado modo={modo} setModo={setModo} hechos={groups.length} total={total} cargando={cargando}
          conOtras={conOtras} med={med} analizando={analizando} mezcla={mezcla} />
      )}
      {esStation && modo === 'mezcla' && (
        <div className="cluster st-mezcla-nota">
          {mezcla?.aviso && <span className="note-warn">{mezcla.aviso}</span>}
          <span className="muted">Un orden armado por la compuerta de BPM (±8 %) y la key: no dice si el set suena bien, eso lo decide tu oído.</span>
        </div>
      )}

      {no_encontradas && no_encontradas.length > 0 && (
        <div className="alert alert-warn" style={{ margin: '0 var(--space-3) var(--space-3)' }}>
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 4l9 16H3z" /><path d="M12 10v4.5" /><circle cx="12" cy="17.4" r=".9" fill="currentColor" stroke="none" /></svg>
          <div><div className="alert-title">Sin resultado ({no_encontradas.length})</div><p>{no_encontradas.join(' · ')}</p></div>
        </div>
      )}

      <div className={`results${esStation ? ' results-station' : ''}`}>
        <div className="results-head">
          <div style={{ textAlign: 'right' }}>#</div>
          <div>Art</div>
          <div>Tema / artista</div>
          <div className="col-meta">Metadata</div>
          <div>Nota</div>
          <div>Versiones</div>
          <div style={{ textAlign: 'right' }}>Acciones</div>
        </div>
        {vista.map((i, p) => [groups[i], i, p]).map(([g, i, p]) => (
          <TrackRow key={i} g={g} i={i} pos={p} sel={sel[i]} formato={formato} metaMap={metaMap} preview={preview}
            medido={esStation && analisis ? (analisis[String(g.base?.video_id ?? '')] ?? null) : undefined}
            mezclaFila={esStation ? filaMezcla(g, modo, mezcla) : null}
            dl={dl[songKey(g.opciones[sel[i]])]} onPlay={reproducir} onSpek={onSpek} onDownload={onDownload}
            current={player.current?.key === songKey(g.opciones[sel[i]])}
            loadedIdx={player.current ? g.opciones.findIndex((o) => songKey(o) === player.current.key) : -1}
            playerStatus={player.status}
            playing={player.isPlaying(songKey(g.opciones[sel[i]]))}
            onSelect={onSelect} onCompare={onCompare} onStation={onStation} />
        ))}
        {/* Los temas de la Station que todavía buscan sus versiones (f46: con la forma de la fila). */}
        {pendientes.map((it, k) => (
          <FilaPendiente key={`p${groups.length + k}`} item={it} pos={groups.length + k} buscando={groups.length + k < pedidos} />
        ))}
      </div>
    </>
  )
}

/* ---------- Modo lista: pegar tracks (área .paste) ---------- */
export function ListForm({ formato, onBuscar, onCancel }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const areaRef = useRef(null)
  const lineas = text.split('\n').map((s) => s.trim()).filter(Boolean)
  const n = lineas.length
  const nums = Array.from({ length: Math.max(text.split('\n').length, 1) }, (_, i) => i + 1).join('\n')
  const submit = async () => {
    // Antes, con la lista vacía el botón no hacía nada y no decía por qué.
    if (!lineas.length) { setErr('Pegá al menos un tema (uno por línea) para buscar.'); areaRef.current?.focus(); return }
    setBusy(true)
    try { await onBuscar(text.trim()) } finally { setBusy(false) }
  }
  return (
    <div style={{ maxWidth: 680, margin: '0 auto', padding: '0 var(--space-3)' }}>
      <h1 className="sr-only">Modo lista</h1>
      <div className="eyebrow" style={{ marginBottom: 'var(--space-2)' }} id="lista-ayuda">Modo lista (DJ) — un tema por línea, hasta 3 versiones de cada uno</div>
      <div className="paste">
        <div className="paste-nums" aria-hidden="true">{nums}</div>
        <textarea ref={areaRef} spellCheck="false" value={text} onChange={(e) => { setText(e.target.value); if (err) setErr('') }}
          aria-label="Pegá tu lista de temas" aria-describedby={err ? 'lista-ayuda lista-error' : 'lista-ayuda'} aria-invalid={!!err}
          placeholder={'Skrillex - Bangarang\nDaft Punk - One More Time\nFisher - Losing It\n…'} />
      </div>
      {err && <p id="lista-error" role="alert" className="note-warn" style={{ margin: 'var(--space-2) 0 0' }}>{err}</p>}
      <div className="paste-foot">
        <span className="mono">{n} línea{n !== 1 ? 's' : ''}</span>
        <span className="push cluster" style={{ gap: 'var(--space-2)' }}>
          <button type="button" className="btn btn-ghost" onClick={onCancel}>Cancelar</button>
          <button type="button" className="btn btn-primary" onClick={submit} disabled={busy}>
            {busy ? <><span className="spinner" aria-hidden="true" /> Buscando…</> : `Buscar lista${formato ? ` (${formato.toUpperCase()})` : ''}`}
          </button>
        </span>
      </div>
    </div>
  )
}

/* ResultsView: la búsqueda ahora se enruta a ListResults (vista unificada por tema).
   Se mantiene el export por compatibilidad con App.jsx; delega en ListResults. */
export function ResultsView(props) {
  return <ListResults {...props} />
}
