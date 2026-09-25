import { useEffect, useRef, useState } from 'react'
import { usePlayer, usePlayerTime } from '../player/context'
import { fmtBpm, NOMBRE_FUENTE } from '../player/track'
import { songKey } from '../utils'
import Cover from './Cover'
import { AddToPlaylist } from './AddToPlaylist'
import { DlButton, QualityBadge, gradeClass } from './common'
import { IconDownload, IconPause, IconPlayFill } from './icons'

/* La barra de reproducción fija al pie ("el deck"). Solo vista: el estado y los motores
   viven en src/player/PlayerProvider.jsx. Aparece cuando algo suena y no se va al navegar.

   Regla §6 en la barra: cada dato se muestra solo si vino. Sin BPM no hay BPM ni marcas de
   compás; sin tonalidad clásica no se "traduce" desde el Camelot; sin duración, la barra de
   progreso queda deshabilitada en vez de inventar un largo. */

const PF = { youtube: 'pf-yt', soundcloud: 'pf-sc', spotify: 'pf-sp', deezer: 'pf-sp', ligaudio: 'pf-m1', hitplayer: 'pf-m2' }
const LOSSLESS = ['wav', 'flac', 'aiff', 'aif']
const SALTO = 5  // segundos por flecha en la barra de posición

const fmtT = (s) => {
  if (s == null || !Number.isFinite(s)) return '–:––'
  s = Math.max(0, Math.floor(s))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(x).padStart(2, '0')}` : `${m}:${String(x).padStart(2, '0')}`
}

const S = (p, sz = 18, extra = {}) => <svg width={sz} height={sz} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...extra}>{p}</svg>
const IcoPrev = () => <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="5" y="5" width="2.4" height="14" rx="1" /><path d="M19 5.8v12.4a.8.8 0 0 1-1.24.67L9.4 12.67a.8.8 0 0 1 0-1.34l8.36-6.2A.8.8 0 0 1 19 5.8z" /></svg>
const IcoNext = () => <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="16.6" y="5" width="2.4" height="14" rx="1" /><path d="M5 5.8v12.4a.8.8 0 0 0 1.24.67l8.36-6.2a.8.8 0 0 0 0-1.34L6.24 5.13A.8.8 0 0 0 5 5.8z" /></svg>
const IcoVol = ({ off }) => S(off
  ? <><path d="M11 5 6 9H3v6h3l5 4z" /><path d="m22 9-6 6M16 9l6 6" /></>
  : <><path d="M11 5 6 9H3v6h3l5 4z" /><path d="M15.5 8.5a5 5 0 0 1 0 7" /><path d="M18.5 5.5a9 9 0 0 1 0 13" /></>, 17)
const IcoX = () => S(<path d="M6 6l12 12M18 6L6 18" />, 16)
const IcoExt = () => S(<><path d="M14 4h6v6" /><path d="M20 4 10 14" /><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></>, 14)

/* Marcas de compás: SACADAS por ahora (decisión del dueño por §6, auditoría de f28).
   Contadas desde el segundo 0 se leían como "acá empieza la frase", y el primer downbeat casi
   nunca está en 0 s. Vuelven cuando haya ancla: el XML de Rekordbox trae la grilla de beats
   por track (`<TEMPO Inizio="0.025" Bpm="128.00" Metro="4/4" Battito="1"/>`; Inizio = segundo
   del primer tiempo, Battito = qué tiempo del compás es). Pasos: leer TEMPO en
   ground_truth/rekordbox.parsear, mandarlo en /api/biblioteca (p. ej. `primer_beat`) y en
   fromLibrary (src/player/track.js), y dibujar acá las marcas desde el primer Battito="1"
   cada 16 compases, dentro de .deck-seek-track (el CSS de `.deck-seek-track>i` sigue). */

export default function PlayerBar({ formato, dl, onDownload, onOpenEmbed }) {
  const p = usePlayer()
  const { time, duration } = usePlayerTime()
  const barRef = useRef(null)
  const [scrub, setScrub] = useState(null)
  const arrastrando = useRef(false)
  const t = p.current

  // Alto real de la barra en --player-h: el contenido, el rail y los avisos se corren para no
  // quedar abajo (cambia con el ancho: en el teléfono la barra tiene tres renglones).
  useEffect(() => {
    const el = barRef.current
    const root = document.documentElement
    if (!el || !t) { root.style.setProperty('--player-h', '0px'); return }
    const ro = new ResizeObserver(() => root.style.setProperty('--player-h', `${Math.ceil(el.getBoundingClientRect().height)}px`))
    ro.observe(el)
    return () => { ro.disconnect(); root.style.setProperty('--player-h', '0px') }
  }, [!!t]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!t) return null

  const sonando = p.status === 'playing'
  const cargando = p.status === 'loading'
  const controlable = !['embed', 'none'].includes(p.status)
  const dur = duration && duration > 0 ? duration : null
  const pos = scrub ?? Math.min(time || 0, dur || 0)
  const bpm = fmtBpm(t)
  const fuente = NOMBRE_FUENTE[t.fuente] || t.fuente || null
  const formatoTrack = t.formato ? t.formato.toLowerCase() : null
  const dlState = t.descargable ? dl[songKey(t.raw)] : null

  const onSeekKey = (e) => {
    if (!dur) return
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') { e.preventDefault(); p.seek(Math.min(dur, (time || 0) + SALTO)) }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') { e.preventDefault(); p.seek(Math.max(0, (time || 0) - SALTO)) }
  }
  const soltar = () => {
    if (!arrastrando.current) return
    arrastrando.current = false
    if (scrub != null) p.seek(scrub)
    setScrub(null)
  }

  // Región viva sobria: cambia con el tema, el estado o un error, nunca con el tiempo.
  // "Cargando" no se anuncia distinto de "Sonando" (sería un anuncio por cada buffering).
  const quien = `${t.titulo}${t.artista ? ` — ${t.artista}` : ''}`
  const anuncio = p.error
    ? `No se pudo reproducir ${t.titulo}. ${p.error}`
    : p.status === 'embed'
      ? `${t.titulo}: Spotify no deja controlar su reproductor desde la barra.`
      : p.status === 'paused' ? `En pausa: ${quien}`
        : p.status === 'ended' ? `Terminó: ${quien}`
          : `Sonando: ${quien}`

  const vol = p.muted ? 0 : Math.round(p.volume * 100)
  const pct = dur ? (pos / dur) * 100 : 0

  return (
    <section className={`deck is-${p.status}`} ref={barRef} aria-label="Reproductor">
      {/* Plato: la carátula es la etiqueta del disco; gira a 33⅓ mientras suena. Decorativo. */}
      <div className={`deck-disc${sonando ? ' is-spinning' : ''}`} aria-hidden="true">
        <div className="deck-platter">
          <span className="deck-label"><Cover track={t} /></span>
        </div>
        <span className="deck-sheen" />
        <span className="deck-spindle" />
      </div>

      <div className="deck-id">
        <div className="deck-title" title={t.titulo}>{t.titulo}</div>
        <div className="deck-sub">
          {fuente && <span className={`deck-src ${PF[t.fuente] || ''}`}><i />{fuente}</span>}
          <span className="truncate" title={t.artista || ''}>{t.artista || 'Artista sin dato'}</span>
          {p.queue.length > 1 && <span className="deck-count mono" title="Posición en la lista desde la que se reprodujo">{p.index + 1}/{p.queue.length}</span>}
        </div>
      </div>

      <div className="deck-transport">
        <button type="button" className="deck-btn" onClick={p.prev} aria-label="Anterior" title="Anterior (pasados 3 s, vuelve al principio)"
          disabled={!controlable && !p.hasPrev}><IcoPrev /></button>
        <button type="button" className="deck-play" onClick={p.toggle} disabled={!controlable}
          aria-label={p.status === 'error' ? 'Reintentar' : sonando || cargando ? 'Pausar' : 'Reproducir'} aria-busy={cargando}>
          {cargando ? <span className="spinner" aria-hidden="true" />
            : sonando ? <IconPause size={20} /> : <IconPlayFill size={20} />}
        </button>
        <button type="button" className="deck-btn" onClick={p.next} aria-label="Siguiente" title="Siguiente" disabled={!p.hasNext}><IcoNext /></button>
      </div>

      <div className="deck-pos">
        <span className="deck-time mono" aria-hidden="true">{fmtT(dur ? pos : null)}</span>
        <div className="deck-seek" style={{ '--pct': `${pct}%` }}>
          <span className="deck-seek-track" aria-hidden="true">
            <span className="deck-seek-fill" />
          </span>
          <input type="range" className="deck-range" min="0" max={dur ? Math.floor(dur) : 0} step="1"
            value={Math.floor(pos)} disabled={!dur || !controlable}
            aria-label="Posición en el tema"
            aria-valuetext={dur ? `${fmtT(pos)} de ${fmtT(dur)}` : 'Duración desconocida'}
            onPointerDown={() => { arrastrando.current = true }}
            onPointerUp={soltar} onPointerCancel={soltar} onKeyDown={onSeekKey}
            onChange={(e) => { const v = Number(e.target.value); if (arrastrando.current) setScrub(v); else p.seek(v) }} />
        </div>
        <span className="deck-time mono" aria-hidden="true">{fmtT(dur)}</span>
      </div>

      <div className="deck-data">
        {bpm && <span className="mb" title={t.bpmMedido ? 'BPM medido sobre este archivo' : 'BPM informado por la fuente'}><span className="mb-label">BPM</span><b>{bpm}</b></span>}
        {(t.camelot || t.tonalidad) && (
          <span className={`mb mb-key${t.keyDudosa ? ' is-dudosa' : ''}`} title="Camelot y tonalidad clásica">
            <span className="mb-label">Key</span>
            {t.camelot && <b>{t.camelot}</b>}
            {t.tonalidad && <span className="rkey-clasica">{t.tonalidad}</span>}
            {t.keyDudosa && <span className="rduda" role="img" aria-label={t.keyLeyenda || 'la detección de la key no es confiable'} title={t.keyLeyenda || 'la detección de la key no es confiable'}>?</span>}
          </span>
        )}
        {formatoTrack && <span className={`mb${LOSSLESS.includes(formatoTrack) ? ' mb-lossless' : ''}`} title="Formato del archivo"><b>{formatoTrack.toUpperCase()}</b></span>}
        {t.grade
          ? <span className={`grade grade-sm ${gradeClass(t.grade)}`} title={`Calidad real medida: ${t.grade}`}>{t.grade}</span>
          : t.origen === 'busqueda' && <QualityBadge c={t.raw} formato={formato} />}
      </div>

      <div className="deck-acts">
        <AddToPlaylist track={t.paraPlaylist} />
        {t.descargable && (
          <DlButton dl={dlState} label={t.titulo} onClick={() => onDownload(t.raw)} className="btn-icon-sm deck-dl"><IconDownload size={15} /></DlButton>
        )}
        <div className="deck-vol">
          <button type="button" className="deck-btn deck-btn-sm" onClick={p.toggleMute}
            aria-label={p.muted ? 'Activar sonido' : 'Silenciar'} aria-pressed={p.muted} title={p.muted ? 'Activar sonido' : 'Silenciar'}>
            <IcoVol off={p.muted || vol === 0} />
          </button>
          <input type="range" className="deck-range deck-range-vol" min="0" max="100" step="5" value={vol}
            style={{ '--pct': `${vol}%` }}
            aria-label="Volumen" aria-valuetext={p.muted ? 'Silenciado' : `${vol} %`}
            onChange={(e) => p.setVolume(Number(e.target.value) / 100)} />
        </div>
        <button type="button" className="deck-btn deck-btn-sm deck-close" onClick={p.close} aria-label="Cerrar el reproductor" title="Cerrar el reproductor"><IcoX /></button>
      </div>

      {(p.error || p.status === 'embed') && (
        <div className="deck-msg" role="note">
          <span>{p.status === 'embed' ? 'Spotify no deja controlar su reproductor desde la barra.' : p.error}</span>
          {p.status === 'embed'
            ? <button type="button" className="btn btn-ghost btn-sm" onClick={() => onOpenEmbed(t.raw)}>Abrir el reproductor de Spotify</button>
            : t.url && /^https?:/.test(t.url) && t.fuente !== 'biblioteca' &&
              <a className="btn btn-ghost btn-sm" href={t.url} target="_blank" rel="noreferrer">Abrir en {fuente || 'la fuente'} <IcoExt /></a>}
        </div>
      )}
      <div className="sr-only" role="status" aria-live="polite">{anuncio}</div>
    </section>
  )
}
