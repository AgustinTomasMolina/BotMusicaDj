import { useEffect, useMemo, useRef, useState } from 'react'
import { getRadioBiblioteca, getRadioSet, exportarRadioM3u8, radioAudioUrl, radioAudioMotivo } from '../api'
import { fmtDur, normalizeText } from '../utils'
import { IconRadio, IconPause, IconPlayFill, IconSearch, IconDownload } from './icons'

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

// El backend redondea el BPM a un decimal: `toFixed(1)` con ese mismo decimal no redondea
// nada, solo evita que 128.0 se dibuje "128" (§6: redondear el BPM a entero es mentir).
const fmtBpm = (v) => (v === null || v === undefined ? null : Number(v).toFixed(1))

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
  // El percentil ya viene redondeado por el motor (`energia_pct`, el MISMO número que
  // imprime la terminal): se dibuja tal cual. El 0..1 crudo también viaja en la respuesta,
  // pero no se muestra — el dueño eligió percentil, no los dos (2026-09-21).
  const energia = t.energia_pct
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
      <span className="mb" title="Energía: percentil dentro de la biblioteca (0-100) — ese porcentaje de tu biblioteca tiene menos energía que este track">
        <span className="mb-label">Energía</span><b>{energia ?? '—'}</b>
      </span>
      {conDuracion && (
        <span className="mb" title="Duración"><span className="mb-label">Dur</span><b>{t.dur != null ? fmtDur(t.dur) : '—'}</b></span>
      )}
    </span>
  )
}

/* El texto de un pedido rechazado. Tres fuentes, en este orden:

   1. `error` — el motivo que escribió el motor (semilla que no es un track, curva que no
      existe). Va TAL CUAL: inventar uno propio sería dar dos explicaciones para una regla.
   2. `detail` — la validación de FastAPI. Llega, por ejemplo, con un largo de 2.5: antes
      esto se veía como "El servidor rechazó el pedido (422)", que no dice qué arreglar.
   3. El cuerpo crudo (un 500 en text/plain) o, si no hay nada, el código. Decir el número
      no es lindo, pero es lo único cierto que hay y distingue "el servidor falló" de "no
      hay servidor". */
function motivoDelRechazo(status, data) {
  const d = data || {}
  if (typeof d.error === 'string' && d.error.trim()) return d.error
  if (Array.isArray(d.detail)) {
    const partes = d.detail.map((x) => {
      const campo = Array.isArray(x.loc) && x.loc.length ? x.loc[x.loc.length - 1] : null
      return campo ? `${campo}: ${x.msg}` : x.msg
    }).filter(Boolean)
    if (partes.length) return `El servidor no aceptó estos valores (HTTP ${status}) — ${partes.join(' · ')}`
  }
  if (typeof d.detail === 'string' && d.detail.trim()) return `${d.detail} (HTTP ${status})`
  if (d.error_texto) return `El servidor falló al armar el set (HTTP ${status}): ${d.error_texto}`
  return `El servidor rechazó el pedido con HTTP ${status} y sin explicación.`
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
      {/* La leyenda del `?` va acá y no solo debajo del set: en esta lista ya hay keys
          dudosas antes de armar nada, y un `?` que solo se explica en un `title` es un
          símbolo mudo para quien no usa mouse (§6). Es el mismo texto del motor, y que
          aparezca una vez por tabla es lo que hace la propia CLI (`list` y `radio` lo
          imprimen cada una al pie). */}
      {leyenda && <p className="rnota">{leyenda}</p>}
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
      {/* aria-disabled y NO disabled: un botón que se deshabilita con el foco puesto hace
          que Chrome mande el foco al <body>, y quien arma el set con teclado vuelve al
          principio de la página en cada intento (WCAG 2.4.3). Así el botón sigue enfocado
          mientras arma y el click se ignora acá. */}
      <button type="button" className="btn btn-primary rarmar" aria-disabled={armando || !elegida}
        aria-describedby={elegida ? undefined : 'r-armar-falta'}
        onClick={() => { if (!armando && elegida) onArmar() }}>
        {armando
          ? <><span className="spinner" aria-hidden="true" /> Armando el set…</>
          : <><IconRadio size={16} /> Armar el set</>}
      </button>
      {!elegida && <p className="rnota" id="r-armar-falta">Elegí primero un track semilla de la lista.</p>}
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
  const hay = pasos.some((p) => p.track.energia_pct !== null && p.track.energia_pct !== undefined)
  if (!hay) return null
  return (
    <div className="rcurva" aria-hidden="true" title="Energía de cada paso (percentil dentro de la biblioteca)">
      {pasos.map((p) => {
        // La altura sale del mismo percentil 0-100 que dice la fila: una barra y un número
        // que salieran de cuentas distintas se contradicen en pantalla.
        const e = p.track.energia_pct
        return <i key={p.n} style={{ height: e == null ? '2px' : `${Math.max(2, e)}%` }} className={e == null ? 'is-sin' : ''} />
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
  const [exportando, setExportando] = useState(false)
  const [avisoExport, setAvisoExport] = useState(null) // {tipo: 'ok'|'err', texto}
  const audioRef = useRef(null)
  // Qué track tiene cargado el <audio>. Sin esto, «Reproducir» después de «Pausar»
  // reasignaba `src` y el track arrancaba de cero: el botón decía Pausar y actuaba como
  // Detener.
  const cargadoRef = useRef(null)
  // Al volver de un «Reintentar» el bloque que tenía el foco ya no existe y Chrome lo manda
  // al <body>. El foco va al encabezado de lo que acaba de aparecer (WCAG 2.4.3).
  const tituloRef = useRef(null)

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
    // El ref se LEE en el cleanup, no al montar: cuando este efecto corre, el <audio>
    // todavía no está en el DOM (se dibuja más abajo, con los datos ya cargados), así que
    // capturarlo en una variable acá guardaba null y no pausaba nada. Mismo patrón que
    // `views.jsx`.
    return () => { vivo = false; if (audioRef.current) audioRef.current.pause() } // eslint-disable-line react-hooks/exhaustive-deps
  }, [intento])

  // Al volver de un «Reintentar», el foco va al encabezado de lo que se acaba de dibujar.
  useEffect(() => { if (intento > 0 && lib) tituloRef.current?.focus({ preventScroll: true }) }, [intento, lib])

  // Corta lo que esté sonando y limpia su estado. El set que viene puede no tener ese paso.
  const pararAudio = () => {
    if (audioRef.current) audioRef.current.pause()
    setSonando(null)
    setErrorAudio(null)
  }

  const armar = async () => {
    if (!elegida) return
    // Antes esto no paraba el audio: con un paso sonando, armar otro set dejaba el track
    // anterior sonando sin ningún botón en «Pausar», y si el armado fallaba (400) la lista
    // desaparecía y el audio seguía sin un solo control en pantalla. También limpia el
    // aviso de un 404 del set viejo, que si no quedaba pegado al set nuevo.
    pararAudio()
    setArmando(true)
    setError('')
    setAvisoExport(null)   // el aviso era del set anterior
    try {
      const r = await getRadioSet({ track: elegida.id, ...cfg })
      if (!r.ok) {
        // 400: semilla que no es un track, curva inexistente, randomness fuera de 0..1. El
        // texto lo escribe el motor y se muestra tal cual — inventar uno propio acá sería
        // dar dos explicaciones distintas para la misma regla.
        setSet(null)
        setError(motivoDelRechazo(r.status, r.data))
        return
      }
      if (!Array.isArray(r.data?.pasos)) {
        // 200 con un cuerpo que no es un set (un proxy que devuelve HTML, por ejemplo).
        // Sin esto, `set_.pasos.map` más abajo tira y la pantalla queda en blanco: la app no
        // tiene ErrorBoundary, así que un cuerpo raro se llevaba puesta toda la vista.
        setSet(null)
        setError('El servidor contestó algo que no es un set. Revisá que estés hablando con MusiFlix y no con otra cosa.')
        return
      }
      setSet(r.data)
      // Lo que se USÓ, que puede no ser lo que se pidió (un campo vacío lo llenó el motor).
      if (r.data.config) setCfg({ ...r.data.config })
    } catch {
      // Acá SÍ es un problema de red: el fetch no llegó a tener respuesta. Un servidor que
      // contesta 500 ya no cae en esta rama (ver `cuerpoRadio` en api.js).
      setSet(null)
      setError('No pude conectar con el servidor para armar el set. Revisá que esté corriendo y volvé a intentar.')
    } finally {
      setArmando(false)
    }
  }

  // Baja el set que está EN PANTALLA como .m3u8 para Rekordbox. Se piden los parámetros que
  // el set dice que usó (`set_.config`), no los de los controles: si el DJ tocó un control
  // después de armar, el archivo igual tiene que ser la lista que está viendo. Y viajan los
  // ids en pantalla: si el backend re-arma otra cosa (un scan entre medio), contesta 409 con
  // el motivo en vez de bajar otro set.
  const exportar = async () => {
    if (!set_ || !set_.semilla || exportando || armando) return
    setExportando(true)
    setAvisoExport(null)
    try {
      const r = await exportarRadioM3u8({ track: set_.semilla.id, ...set_.config },
        set_.pasos.map((p) => p.track.id))
      if (!r.ok) {
        setAvisoExport({ tipo: 'err', texto: motivoDelRechazo(r.status, r.data) })
        return
      }
      // Descarga real: un <a download> con el blob. El nombre es el que eligió el backend
      // (ya saneado para Windows); el navegador lo deja en la carpeta de descargas.
      const url = URL.createObjectURL(r.blob)
      const a = document.createElement('a')
      a.href = url
      a.download = r.nombre
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 30000)
      setAvisoExport({
        tipo: 'ok',
        texto: `Se bajó «${r.nombre}» con los ${set_.total} tracks en este orden. En Rekordbox: File → Import → Import Playlist, y elegí ese archivo.`,
      })
    } catch {
      setAvisoExport({ tipo: 'err', texto: 'No pude conectar con el servidor para exportar el set. Revisá que esté corriendo y volvé a intentar.' })
    } finally {
      setExportando(false)
    }
  }

  // Un solo <audio> en la pantalla: es imposible que suenen dos pasos a la vez.
  const alternarAudio = async (t) => {
    const a = audioRef.current
    if (!a) return
    setErrorAudio(null)
    if (sonando === t.id) { a.pause(); setSonando(null); return }
    // Solo se recarga si es OTRO track: reasignar `src` al mismo reinicia la reproducción,
    // así que «Pausar» y volver a darle play mandaba el tema al principio.
    if (cargadoRef.current !== t.id) {
      a.src = radioAudioUrl(t.id)
      cargadoRef.current = t.id
    }
    try {
      await a.play()
      setSonando(t.id)
    } catch (e) {
      if (e && e.name === 'AbortError') return   // se cambió de tema antes de arrancar
      setSonando(null)
      // El elemento quedó con un error pegado: se olvida qué tenía cargado para que un
      // segundo intento vuelva a pedir el archivo (puede haber vuelto a su lugar).
      cargadoRef.current = null
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
        <h3 ref={tituloRef} tabIndex={-1}>{lib.configurada ? 'La radio no puede leer la biblioteca' : 'Conectá la biblioteca del motor'}</h3>
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
    : set_
      ? `Set de ${set_.total} track${set_.total === 1 ? '' : 's'} desde ${set_.semilla ? set_.semilla.label : 'la semilla'}.`
      : ''

  return (
    <div className="radiodj">
      {/* onError: si el archivo falla a mitad de camino el botón no queda en "Pausar".
          onPause: si lo pausa otro (la barra de reproducción, que es el único audio de la app),
          el botón vuelve a "Reproducir" en vez de quedar en "Pausar" con un click muerto. */}
      <audio ref={audioRef} onEnded={() => setSonando(null)} onError={() => setSonando(null)}
        onPause={() => setSonando(null)} preload="none" />
      <div className="radiodj-head">
        <h1 ref={tituloRef} tabIndex={-1}>Radio DJ</h1>
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
            {/* aria-disabled y no disabled, por lo mismo que «Armar el set»: el botón no
                pierde el foco mientras exporta ni cuando todavía no hay set. */}
            <button type="button" className="btn btn-secondary rexportar"
              aria-disabled={!set_ || exportando || armando}
              aria-describedby={set_ ? undefined : 'r-exportar-falta'}
              onClick={exportar}>
              {exportando
                ? <><span className="spinner" aria-hidden="true" /> Exportando…</>
                : <><IconDownload size={15} /> Exportar a Rekordbox</>}
            </button>
            {!set_ && <span className="sr-only" id="r-exportar-falta">Todavía no hay set: armá uno para poder exportarlo.</span>}
          </div>

          {avisoExport && avisoExport.tipo === 'err' && (
            <div className="alert alert-warn" role="alert">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 4l9 16H3z" /><path d="M12 10v4.5" /><circle cx="12" cy="17.4" r=".9" fill="currentColor" stroke="none" /></svg>
              <div><div className="alert-title">No se exportó el set</div><p>{avisoExport.texto}</p></div>
            </div>
          )}
          {avisoExport && avisoExport.tipo === 'ok' && (
            <p className="rnota rexportar-ok" role="status">{avisoExport.texto}</p>
          )}

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
