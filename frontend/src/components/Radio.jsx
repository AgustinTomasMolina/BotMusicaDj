import { useEffect, useMemo, useRef, useState } from 'react'
import { getRadioBiblioteca, getRadioSet, radioAudioUrl, radioAudioMotivo } from '../api'
import { fmtDur, normalizeText } from '../utils'
import { IconRadio, IconPause, IconPlayFill, IconSearch } from './icons'

/* ============================================================================
   Pantalla de Radio DJ: elegir semilla → ajustar la radio → el set.

   REGLA DE LA PANTALLA (spec §6: un dato que miente es peor que uno ausente): acá NO se
   calcula nada. El BPM, las dos notaciones de la key, el `?` de confianza, la energía, el
   porqué de cada transición, el titular del corte y la leyenda vienen del motor a través de
   /api/radio/*. Lo único que hace este archivo con un número es FORMATEARLO con los
   decimales que el backend ya fijó (`toFixed` sobre un valor ya redondeado no cambia el
   valor) y, si no viene, dibujar un guion. Ningún porcentaje, ningún promedio, ninguna
   traducción Camelot → clásica de este lado: recalcular el % de una transición es
   exactamente lo que el módulo de la radio prohíbe (la pantalla terminaría diciendo
   "+7.9%" sobre una transición que la compuerta midió en 8.1%).
   ========================================================================== */

// Cuántos tracks se dibujan en la lista para elegir semilla. Una biblioteca de 10k filas
// no entra en el DOM sin que la pantalla se arrastre; el resto se alcanza con el buscador
// y el pie dice cuántos quedaron afuera (nunca se esconde en silencio).
const MAX_LISTA = 200

// El backend redondea el BPM a un decimal y la energía a tres: `toFixed` con esos mismos
// decimales no redondea nada, solo evita que 128.0 se dibuje "128" (§6: redondear el BPM a
// entero es mentir) y que la columna de energía baile entre "0.3" y "0.265".
const fmtBpm = (v) => (v === null || v === undefined ? null : Number(v).toFixed(1))
const fmtEnergia = (v) => (v === null || v === undefined ? null : Number(v).toFixed(3))

// Los controles de la radio. Los VALORES no están acá: salen de `opciones.config_default`
// del backend, que los lee del propio RadioConfig.
const CAMPOS = [
  { k: 'largo', label: 'Largo', min: 1, step: 1, ayuda: 'cuántos tracks pedís' },
  { k: 'randomness', label: 'Aleatoriedad', min: 0, max: 1, step: 0.1, ayuda: 'en 0 la radio da siempre el mismo set' },
  { k: 'semilla', label: 'Semilla del azar', step: 1, ayuda: 'solo cuenta con aleatoriedad mayor que 0' },
  { k: 'artist_gap', label: 'artist_gap', min: 0, step: 1, ayuda: 'mínimo de tracks entre dos temas del mismo artista' },
  { k: 'mmr_lambda', label: 'mmr_lambda', min: 0, step: 0.05, ayuda: 'cuánto se castiga elegir algo parecido a lo ya elegido' },
]

/* Los datos de un track como los pide §6: BPM con un decimal, Camelot Y clásica, el `?` de
   confianza y la energía. Lo que el backend manda `null` se dibuja como guion, no como 0. */
function DatosTrack({ t, leyenda, conDuracion = true }) {
  const bpm = fmtBpm(t.bpm)
  const energia = fmtEnergia(t.energia)
  const hayKey = !!(t.camelot || t.tonalidad)
  // El `?` explica de qué duda: el texto es el del motor (`leyenda_key`), no uno de acá.
  const porQueDuda = leyenda || 'la detección de la key no es confiable'
  return (
    <span className="rdatos">
      <span className="mb" title="BPM medido por el motor">
        <span className="mb-label">BPM</span><b>{bpm ?? '—'}</b>
      </span>
      {hayKey ? (
        <span className={`mb mb-key${t.key_dudosa ? ' is-dudosa' : ''}`} title="Camelot y tonalidad clásica">
          <span className="mb-label">Key</span>
          <b>{t.camelot || '—'}</b>
          <span className="rkey-clasica">{t.tonalidad || '—'}</span>
          {t.key_dudosa && <span className="rduda" role="img" aria-label={porQueDuda} title={porQueDuda}>?</span>}
        </span>
      ) : (
        // Sin Camelot ni clásica no hay key que mostrar: guion, y sin `?` (no se duda de
        // algo que no está). El motivo de que falte lo dice el motor en `info <track>`.
        <span className="mb mb-key" title="El motor no le reconoció una key"><span className="mb-label">Key</span><b>—</b></span>
      )}
      <span className="mb" title="Energía: percentil dentro de la biblioteca (0 = la más baja, 1 = la más alta)">
        <span className="mb-label">Energía</span><b>{energia ?? '—'}</b>
      </span>
      {conDuracion && (
        <span className="mb" title="Duración"><span className="mb-label">Dur</span><b>{t.dur != null ? fmtDur(t.dur) : '—'}</b></span>
      )}
    </span>
  )
}

/* ---------- 1. Elegir la semilla ---------- */
function Semillero({ tracks, leyenda, elegida, onElegir, q, setQ }) {
  const filtrados = useMemo(() => {
    const n = normalizeText(q)
    if (!n) return tracks
    return tracks.filter((t) => normalizeText(`${t.label} ${t.titulo} ${t.artista}`).includes(n))
  }, [tracks, q])
  const visibles = filtrados.slice(0, MAX_LISTA)

  return (
    <section className="rpanel" aria-labelledby="r-semilla-h">
      <h2 id="r-semilla-h" className="rpanel-h">1 · El track semilla</h2>
      <p className="rpanel-sub">Todo el set se arma contra su BPM y su key.</p>
      <div className="rbuscar">
        <span className="rbuscar-ico" aria-hidden="true"><IconSearch size={15} /></span>
        <input className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Filtrá por tema o artista…" autoComplete="off"
          aria-label="Filtrar la biblioteca del motor" aria-controls="r-lista-semillas" />
      </div>
      <div className="rlista" id="r-lista-semillas" role="list">
        {visibles.length === 0 && (
          <p className="rnota">Ningún track de la biblioteca coincide con «{q.trim()}».</p>
        )}
        {visibles.map((t) => (
          <div role="listitem" key={t.id}>
            {/* Lo que no es un track se marca y NO se esconde: `python -m motor list` también
                lo muestra, y esconderlo haría que el DJ no encuentre un archivo que sabe que
                escaneó. Sigue siendo elegible acá a propósito: quien lo intente recibe el
                motivo del motor (400 de /api/radio/set), no un botón muerto sin explicación. */}
            <button type="button" className={`rsem${t.es_track ? '' : ' is-notrack'}`}
              aria-pressed={elegida && elegida.id === t.id} onClick={() => onElegir(t)}>
              <span className="rsem-id">
                <span className="rsem-titulo truncate">{t.titulo}</span>
                <span className="rsem-artista truncate">{t.artista || '—'}</span>
              </span>
              <DatosTrack t={t} leyenda={leyenda} />
              {!t.es_track && <span className="rtag-notrack">no es un track</span>}
            </button>
          </div>
        ))}
      </div>
      <p className="rlista-pie">
        {filtrados.length > visibles.length
          ? `Se dibujan ${visibles.length} de ${filtrados.length} — filtrá para ver el resto.`
          : `${filtrados.length} de ${tracks.length} tracks de la biblioteca del motor.`}
      </p>
    </section>
  )
}

/* ---------- 2. Los controles (valores del backend, nunca escritos acá) ---------- */
function Controles({ cfg, setCfg, curvas, elegida, onArmar, armando }) {
  const set1 = (k, v) => setCfg((c) => ({ ...c, [k]: v }))
  return (
    <section className="rpanel" aria-labelledby="r-cfg-h">
      <h2 id="r-cfg-h" className="rpanel-h">2 · La radio</h2>
      <p className="rpanel-sub">Los valores son los del motor. Un campo vacío usa el de fábrica.</p>
      <div className="rcfg">
        {curvas && curvas.length > 0 && (
          <div className="rcfg-campo rcfg-curva">
            <span className="rcfg-label" id="r-curva-lbl">Curva de energía</span>
            <div className="seg" role="radiogroup" aria-labelledby="r-curva-lbl">
              {curvas.map((c) => (
                <label key={c} className="seg-opt">
                  <input type="radio" name="radio-curva" value={c}
                    checked={cfg.curva === c} onChange={() => set1('curva', c)} />
                  <b className="mono">{c}</b>
                </label>
              ))}
            </div>
          </div>
        )}
        {CAMPOS.map((f) => (
          <div className="rcfg-campo" key={f.k}>
            <label className="rcfg-label" htmlFor={`r-cfg-${f.k}`}>{f.label}</label>
            <input className="input" id={`r-cfg-${f.k}`} type="number"
              min={f.min} max={f.max} step={f.step}
              value={cfg[f.k] ?? ''} aria-describedby={`r-cfg-${f.k}-ay`}
              onChange={(e) => set1(f.k, e.target.value)} />
            <span className="rcfg-ayuda" id={`r-cfg-${f.k}-ay`}>{f.ayuda}</span>
          </div>
        ))}
      </div>
      <button type="button" className="btn btn-primary rarmar" onClick={onArmar} disabled={armando || !elegida}>
        {armando
          ? <><span className="spinner" aria-hidden="true" /> Armando el set…</>
          : <><IconRadio size={16} /> Armar el set</>}
      </button>
      {!elegida && <p className="rnota">Elegí primero un track semilla de la lista.</p>}
    </section>
  )
}

/* ---------- 3. El set ---------- */
function Paso({ paso, leyenda, sonando, onAudio, errorAudio }) {
  const t = paso.track
  return (
    <div className="rpaso" role="listitem">
      {/* El porqué TAL CUAL lo escribió el motor (`Transition.reason()`). §6: una
          recomendación sin explicación no genera confianza. No es aria-hidden: es el dato
          más importante de la fila, también para quien usa lector de pantalla. */}
      <p className={`rpaso-why${paso.es_semilla ? ' is-seed' : ''}`}>
        <span className="rpaso-why-ico" aria-hidden="true">{paso.es_semilla ? '◉' : '↳'}</span>
        <span className="mono">{paso.motivo}</span>
      </p>
      <div className="rpaso-row">
        <span className="rpaso-n mono">{paso.n}</span>
        <button type="button" className={`rplay${sonando ? ' on' : ''}`}
          aria-label={`${sonando ? 'Pausar' : 'Reproducir'} ${t.titulo}`} onClick={() => onAudio(t)}>
          {sonando ? <IconPause size={15} /> : <IconPlayFill size={15} />}
        </button>
        <span className="rpaso-id">
          <span className="rpaso-titulo truncate">{t.titulo}</span>
          <span className="rpaso-artista truncate">{t.artista || '—'}</span>
        </span>
        <DatosTrack t={t} leyenda={leyenda} />
        {paso.es_semilla && <span className="rtag-seed">semilla</span>}
      </div>
      {/* El 404 de la radio explica si el archivo se movió o si la base se escaneó en otra
          máquina: se muestra el texto del backend, no uno resumido acá. */}
      {errorAudio && <p className="rnota rnota-err" role="alert">{errorAudio}</p>}
    </div>
  )
}

/* Curva de energía del set: una barra por paso con el percentil que YA está en cada fila.
   Es un dibujo de datos que están en pantalla (por eso aria-hidden), no una métrica nueva:
   el desvío y el Spearman de la spec §4 los calcula el motor y la API todavía no los manda,
   así que no se muestran en vez de inventarlos. */
function Curva({ pasos }) {
  const hay = pasos.some((p) => p.track.energia !== null && p.track.energia !== undefined)
  if (!hay) return null
  return (
    <div className="rcurva" aria-hidden="true" title="Energía de cada paso (percentil dentro de la biblioteca)">
      {pasos.map((p) => {
        const e = p.track.energia
        return <i key={p.n} style={{ height: e == null ? '2px' : `${Math.max(2, e * 100)}%` }} className={e == null ? 'is-sin' : ''} />
      })}
    </div>
  )
}

/* ---------- La pantalla ---------- */
export default function Radio() {
  const [lib, setLib] = useState(null)        // respuesta de /api/radio/biblioteca
  const [cfg, setCfg] = useState(null)        // lo que muestran los controles
  const [q, setQ] = useState('')
  const [elegida, setElegida] = useState(null)
  const [set_, setSet] = useState(null)       // respuesta de /api/radio/set
  const [armando, setArmando] = useState(false)
  const [error, setError] = useState('')      // motivo del backend (400) o de conexión
  const [sonando, setSonando] = useState(null)
  const [errorAudio, setErrorAudio] = useState(null)   // {id, mensaje}
  const [intento, setIntento] = useState(0)            // el botón «Reintentar» vuelve a pedir
  const audioRef = useRef(null)

  useEffect(() => {
    let vivo = true
    getRadioBiblioteca()
      .then((d) => {
        if (!vivo) return
        setLib(d)
        // Los defaults de los controles son los del motor. Si el backend no los manda
        // (sin el paquete motor), no se inventan: la pantalla muestra el motivo.
        if (d.opciones && d.opciones.config_default) setCfg({ ...d.opciones.config_default })
      })
      .catch(() => vivo && setLib({
        configurada: false, estado: 'sin-conexion', total: 0, tracks: [], opciones: null,
        motivo: 'No pude conectar con el servidor para leer la biblioteca del motor. Revisá que esté corriendo y volvé a intentar.',
      }))
    const audio = audioRef.current
    return () => { vivo = false; if (audio) audio.pause() }
  }, [intento])

  const armar = async () => {
    if (!elegida) return
    setArmando(true)
    setError('')
    try {
      const r = await getRadioSet({ track: elegida.id, ...cfg })
      if (!r.ok) {
        // 400: semilla que no es un track, curva inexistente, randomness fuera de 0..1. El
        // texto lo escribe el motor y se muestra tal cual — inventar uno propio acá sería
        // dar dos explicaciones distintas para la misma regla.
        setSet(null)
        setError(r.data && r.data.error ? r.data.error : `El servidor rechazó el pedido (${r.status}).`)
        return
      }
      setSet(r.data)
      // Lo que se USÓ, que puede no ser lo que se pidió (un campo vacío lo llenó el motor).
      if (r.data.config) setCfg({ ...r.data.config })
    } catch {
      setSet(null)
      setError('No pude conectar con el servidor para armar el set. Revisá que esté corriendo y volvé a intentar.')
    } finally {
      setArmando(false)
    }
  }

  // Un solo <audio> en la pantalla: es imposible que suenen dos pasos a la vez.
  const alternarAudio = async (t) => {
    const a = audioRef.current
    if (!a) return
    setErrorAudio(null)
    if (sonando === t.id) { a.pause(); setSonando(null); return }
    a.src = radioAudioUrl(t.id)
    try {
      await a.play()
      setSonando(t.id)
    } catch (e) {
      if (e && e.name === 'AbortError') return   // se cambió de tema antes de arrancar
      setSonando(null)
      const motivo = await radioAudioMotivo(t.id)
      setErrorAudio({ id: t.id, mensaje: motivo || `No pude reproducir «${t.titulo}»: el navegador no pudo abrir ese audio.` })
    }
  }

  if (!lib) {
    return <div className="empty"><span className="spinner" aria-hidden="true" /><p>Cargando la biblioteca del motor…</p></div>
  }

  // Sin base, sin motor, base ocupada, vacía o ilegible: el backend manda SIEMPRE el motivo
  // y el motivo dice qué hacer. Nunca una pantalla vacía sin explicación.
  if (lib.estado !== 'ok') {
    return (
      <div className="empty">
        <div className="empty-art"><IconRadio size={26} /></div>
        <h3>{lib.configurada ? 'La radio no puede leer la biblioteca' : 'Conectá la biblioteca del motor'}</h3>
        <p>{lib.motivo}</p>
        <p className="rnota mono">estado: {lib.estado}</p>
        <button type="button" className="btn btn-secondary"
          onClick={() => { setLib(null); setSet(null); setElegida(null); setIntento((n) => n + 1) }}>
          Reintentar
        </button>
      </div>
    )
  }

  const leyenda = lib.opciones ? lib.opciones.leyenda_key : null
  const curvas = lib.opciones ? lib.opciones.curvas : null
  // El error ya se anuncia solo (role="alert" en el aviso), así que no se repite acá.
  const anuncio = armando
    ? 'Armando el set…'
    : set_ ? `Set de ${set_.total} tracks desde ${set_.semilla ? set_.semilla.label : 'la semilla'}.` : ''

  return (
    <div className="radiodj">
      {/* onError: si el archivo falla a mitad de camino el botón no queda en "Pausar". */}
      <audio ref={audioRef} onEnded={() => setSonando(null)} onError={() => setSonando(null)} preload="none" />
      <div className="radiodj-head">
        <h1>Radio DJ</h1>
        <p className="muted">
          {lib.total} tracks analizados por el motor · el set lo arma el motor y cada paso dice por qué está ahí
        </p>
      </div>

      <div className="radiodj-grid">
        <div className="radiodj-col">
          <Semillero tracks={lib.tracks} leyenda={leyenda} elegida={elegida} onElegir={setElegida} q={q} setQ={setQ} />
          {cfg
            ? <Controles cfg={cfg} setCfg={setCfg} curvas={curvas} elegida={elegida} onArmar={armar} armando={armando} />
            : <p className="rnota">No puedo dibujar los controles: el servidor no mandó los valores del motor.</p>}
        </div>

        <section className="rpanel rpanel-set" aria-labelledby="r-set-h">
          <div className="rset-head">
            <h2 id="r-set-h" className="rpanel-h">3 · El set</h2>
            {set_ && (
              <span className="rset-cuenta mono">
                {set_.total} track{set_.total === 1 ? '' : 's'}
                {set_.pedidos != null && ` · ${set_.total} de ${set_.pedidos} pedidos`}
              </span>
            )}
          </div>

          {error && (
            <div className="alert alert-warn" role="alert">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 4l9 16H3z" /><path d="M12 10v4.5" /><circle cx="12" cy="17.4" r=".9" fill="currentColor" stroke="none" /></svg>
              <div><div className="alert-title">No se armó el set</div><p>{error}</p></div>
            </div>
          )}

          {!set_ && !error && (
            <p className="rnota">Elegí una semilla y tocá «Armar el set»: acá van los pasos con su porqué.</p>
          )}

          {set_ && (
            <>
              <Curva pasos={set_.pasos} />
              <div className="rset" role="list">
                {set_.pasos.map((p) => (
                  <Paso key={p.n} paso={p} leyenda={leyenda}
                    sonando={sonando === p.track.id} onAudio={alternarAudio}
                    errorAudio={errorAudio && errorAudio.id === p.track.id ? errorAudio.mensaje : null} />
                ))}
              </div>

              {/* Por qué se cortó: el titular y el detalle son los del motor. */}
              {set_.corte && (
                <div className="alert alert-warn" role="status">
                  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 4l9 16H3z" /><path d="M12 10v4.5" /><circle cx="12" cy="17.4" r=".9" fill="currentColor" stroke="none" /></svg>
                  <div>
                    <div className="alert-title">{set_.corte.titular}</div>
                    <p>{set_.corte.detalle}</p>
                    {set_.corte.codigo && <p className="rnota mono">{set_.corte.codigo}</p>}
                  </div>
                </div>
              )}
              {set_.aviso_fragmentos && <p className="rnota">{set_.aviso_fragmentos}</p>}
              {set_.leyenda_key && <p className="rnota">{set_.leyenda_key}</p>}
            </>
          )}
        </section>
      </div>

      <div className="sr-only" role="status" aria-live="polite">{anuncio}</div>
    </div>
  )
}
