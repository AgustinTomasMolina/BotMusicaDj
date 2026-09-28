import { useEffect, useMemo, useRef, useState } from 'react'
import {
  getRadioBiblioteca, getRadioSet, exportarRadioM3u8, radioAudioUrl, radioAudioMotivo,
  guardarRadioSet, listarRadioSets, getRadioSetGuardado, renombrarRadioSet, borrarRadioSet,
  calificarTransicion, descalificarTransicion,
} from '../api'
import { fmtDur, normalizeText } from '../utils'
import { IconRadio, IconPause, IconPlayFill, IconSearch, IconDownload } from './icons'
import { SetsGuardados, GuardarDialog, CabeceraGuardado, Calificar } from './RadioSets'

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
function Paso({ paso, leyenda, sonando, onAudio, errorAudio, calificar }) {
  const t = paso.track
  // Solo un set guardado manda `en_biblioteca`: `false` = el archivo ya no está en la
  // biblioteca del motor. El paso se ve entero (es la foto) y no se puede reproducir.
  const falta = t.en_biblioteca === false
  return (
    <div className={`rpaso${falta ? ' is-falta' : ''}`} role="listitem">
      {/* El porqué TAL CUAL lo escribió el motor (`Transition.reason()`). §6: una
          recomendación sin explicación no genera confianza. No es aria-hidden: es el dato
          más importante de la fila, también para quien usa lector de pantalla. */}
      <p className={`rpaso-why${paso.es_semilla ? ' is-seed' : ''}`}>
        <span className="rpaso-why-ico" aria-hidden="true">{paso.es_semilla ? '◉' : '↳'}</span>
        <span className="mono">{paso.motivo}</span>
      </p>
      {/* La calificación va entre el porqué y la fila: califica la transición que llega a
          este paso desde el anterior, que es lo que dice el porqué. */}
      {calificar}
      <div className="rpaso-row">
        <span className="rpaso-n mono">{paso.n}</span>
        {falta
          ? <span className="rplay is-sin" aria-hidden="true" />
          : (
            <button type="button" className={`rplay${sonando ? ' on' : ''}`}
              aria-label={`${sonando ? 'Pausar' : 'Reproducir'} ${t.titulo}`} onClick={() => onAudio(t)}>
              {sonando ? <IconPause size={15} /> : <IconPlayFill size={15} />}
            </button>
          )}
        <span className="rpaso-id">
          <span className="rpaso-titulo truncate">{t.titulo}</span>
          <span className="rpaso-artista truncate">{t.artista || '—'}</span>
        </span>
        <DatosTrack t={t} leyenda={leyenda} />
        {paso.es_semilla && <span className="rtag-seed">semilla</span>}
        {falta && <span className="rtag-falta">ya no está en la biblioteca</span>}
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
  // Sets guardados. `guardado` es el set guardado que se está mirando (la respuesta de
  // POST o GET /api/radio/sets/{id}): mientras hay uno, el panel del set dibuja SU foto y no
  // `set_`. `set_` no se pierde al abrir uno: «Cerrar» vuelve al set armado.
  const [guardado, setGuardado] = useState(null)
  const [sets, setSets] = useState({ cargando: true, sets: [], motivo: null })
  const [dialogoGuardar, setDialogoGuardar] = useState(false)
  const [guardando, setGuardando] = useState(false)
  const [errorGuardar, setErrorGuardar] = useState(null)  // {texto, conflicto}
  const [errorSets, setErrorSets] = useState('')          // abrir / renombrar / borrar
  const [aviso, setAviso] = useState('')                  // región viva de guardar y calificar
  const guardadoTituloRef = useRef(null)
  const setTituloRef = useRef(null)
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

  // `params` solo lo pasa «Re-armar el set» (después de un 409 al guardar): re-arma con lo
  // que el set dice que usó, no con lo que hoy tengan los controles.
  const armar = async (params) => {
    if (!params && !elegida) return
    // Antes esto no paraba el audio: con un paso sonando, armar otro set dejaba el track
    // anterior sonando sin ningún botón en «Pausar», y si el armado fallaba (400) la lista
    // desaparecía y el audio seguía sin un solo control en pantalla. También limpia el
    // aviso de un 404 del set viejo, que si no quedaba pegado al set nuevo.
    pararAudio()
    setArmando(true)
    setError('')
    setAvisoExport(null)   // el aviso era del set anterior
    setErrorGuardar(null)
    setErrorSets('')
    // Armar muestra el set nuevo: el set guardado que se miraba se cierra (sigue en la lista).
    setGuardado(null)
    try {
      const r = await getRadioSet(params || { track: elegida.id, ...cfg })
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
  //
  // Con un set guardado abierto se exporta ESE: su config y sus ids. Si la biblioteca cambió
  // desde que se guardó, el backend contesta 409 con el motivo, igual que con un set armado.
  const exportar = async () => {
    const v = guardado || set_
    const semillaId = v && (v.semilla ? v.semilla.id : v.pasos[0]?.track.id)
    if (!v || !semillaId || exportando || armando) return
    setExportando(true)
    setAvisoExport(null)
    try {
      const r = await exportarRadioM3u8({ track: semillaId, ...v.config },
        v.pasos.map((p) => p.track.id))
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
        texto: `Se bajó «${r.nombre}» con los ${v.total} tracks en este orden. En Rekordbox: File → Import → Import Playlist, y elegí ese archivo.`,
      })
    } catch {
      setAvisoExport({ tipo: 'err', texto: 'No pude conectar con el servidor para exportar el set. Revisá que esté corriendo y volvé a intentar.' })
    } finally {
      setExportando(false)
    }
  }

  /* ---------- sets guardados ---------- */

  // La lista con sus resúmenes. Se vuelve a pedir después de cada escritura: el resumen de
  // cada set lo cuenta la API, no esta pantalla.
  const cargarSets = async () => {
    try {
      const r = await listarRadioSets()
      if (r.ok && Array.isArray(r.data?.sets)) {
        // Sin base (o base ocupada) la API contesta 200 con `estado`/`motivo` y la lista vacía.
        setSets({ cargando: false, sets: r.data.sets, motivo: r.data.estado === 'ok' ? null : r.data.motivo })
      } else {
        setSets({ cargando: false, sets: [], motivo: `No pude leer los sets guardados: ${motivoDelRechazo(r.status, r.data)}` })
      }
    } catch {
      setSets({ cargando: false, sets: [], motivo: 'No pude conectar con el servidor para leer los sets guardados. Revisá que esté corriendo y volvé a intentar.' })
    }
  }
  useEffect(() => { cargarSets() }, [intento]) // eslint-disable-line react-hooks/exhaustive-deps

  // Después de que aparece la cabecera de un set guardado (recién guardado o abierto), el
  // foco va a su título: quien usa teclado sabe qué set está mirando (WCAG 2.4.3). Al
  // cerrarlo o borrarlo, al título del panel del set. Se hace en un efecto, con lo que ya se
  // dibujó: con un setTimeout el foco podía llegar antes que la cabecera.
  const [foco, setFoco] = useState(null)   // {a: 'guardado'|'set', vez}
  const enfocarGuardado = () => setFoco({ a: 'guardado', vez: Date.now() })
  const enfocarSet = () => setFoco({ a: 'set', vez: Date.now() })
  useEffect(() => {
    if (!foco) return
    const el = foco.a === 'guardado' ? guardadoTituloRef.current : setTituloRef.current
    el?.focus()
  }, [foco])

  // Guarda el set armado que está en pantalla. Viajan los ids de los pasos que se ven y la
  // huella que devolvió /api/radio/set: si el backend re-arma otra cosa contesta 409 y NO se
  // guarda nada. Nunca se reintenta solo ni se guarda "lo que haya".
  const guardar = async (nombre) => {
    if (!set_ || !set_.semilla || guardando) return
    setDialogoGuardar(false)
    setGuardando(true)
    setErrorGuardar(null)
    try {
      const r = await guardarRadioSet({ track: set_.semilla.id, ...set_.config },
        set_.pasos.map((p) => p.track.id), set_.huella, nombre)
      if (!r.ok || !r.data?.set) {
        setErrorGuardar({ texto: motivoDelRechazo(r.status, r.data), conflicto: r.status === 409 })
        return
      }
      const s = r.data.set
      // Lo que se muestra de acá en más es la foto que devolvió el backend (los mismos pasos:
      // la huella lo garantiza). `set_` se suelta: ya está guardado, y «Cerrar» no tiene que
      // volver a ofrecer guardarlo otra vez.
      setGuardado(s)
      setSet(null)
      setAviso(`Set guardado como #${s.id}${s.nombre ? ` «${s.nombre}»` : ''}. Ya podés calificar sus transiciones.`)
      enfocarGuardado()
      cargarSets()
    } catch {
      setErrorGuardar({ texto: 'No pude conectar con el servidor para guardar el set. Revisá que esté corriendo y volvé a intentar.', conflicto: false })
    } finally {
      setGuardando(false)
    }
  }

  // Abre un set guardado: su FOTO, de GET /api/radio/sets/{id}. No se re-arma nada.
  const abrirGuardado = async (id) => {
    setErrorSets('')
    try {
      const r = await getRadioSetGuardado(id)
      if (!r.ok || !r.data?.set) {
        setErrorSets(r.ok ? (r.data?.motivo || 'El servidor no devolvió el set.') : motivoDelRechazo(r.status, r.data))
        if (r.status === 404) cargarSets()   // ya no existe: la lista estaba vieja
        return
      }
      if (!guardado || guardado.id !== r.data.set.id) pararAudio()
      setAvisoExport(null)
      setErrorGuardar(null)
      setGuardado(r.data.set)
      setAviso(`Abriste el set guardado #${r.data.set.id}${r.data.set.nombre ? ` «${r.data.set.nombre}»` : ' (sin nombre)'}.`)
      enfocarGuardado()
    } catch {
      setErrorSets('No pude conectar con el servidor para abrir el set. Revisá que esté corriendo y volvé a intentar.')
    }
  }

  const cerrarGuardado = () => {
    pararAudio()
    setAvisoExport(null)
    setGuardado(null)
    setErrorSets('')
    enfocarSet()
  }

  // Devuelven true si se pudo, para que la cabecera sepa si cerrar su formulario o diálogo.
  const renombrar = async (nombre) => {
    if (!guardado) return false
    setErrorSets('')
    try {
      const r = await renombrarRadioSet(guardado.id, nombre)
      if (!r.ok) { setErrorSets(`No se renombró: ${motivoDelRechazo(r.status, r.data)}`); return false }
      setGuardado((g) => (g && g.id === r.data.id ? { ...g, nombre: r.data.nombre } : g))
      setAviso(r.data.nombre ? `Set #${r.data.id} renombrado a «${r.data.nombre}».` : `El set #${r.data.id} quedó sin nombre.`)
      cargarSets()
      return true
    } catch {
      setErrorSets('No pude conectar con el servidor para renombrar el set. Revisá que esté corriendo y volvé a intentar.')
      return false
    }
  }

  const borrar = async () => {
    if (!guardado) return false
    const id = guardado.id
    setErrorSets('')
    try {
      const r = await borrarRadioSet(id)
      if (!r.ok) { setErrorSets(`No se borró: ${motivoDelRechazo(r.status, r.data)}`); return false }
      pararAudio()
      setGuardado(null)
      setAviso(`Set guardado #${id} borrado.`)
      cargarSets()
      enfocarSet()
      return true
    } catch {
      setErrorSets('No pude conectar con el servidor para borrar el set. Revisá que esté corriendo y volvé a intentar.')
      return false
    }
  }

  // Aplica al set abierto lo que contestó la API al calificar: la transición y el resumen.
  const aplicarCalificacion = (id, n, cambios, resumen) => {
    setGuardado((g) => (g && g.id === id
      ? { ...g, resumen, transiciones: g.transiciones.map((t) => (t.n === n ? { ...t, ...cambios } : t)) }
      : g))
  }

  const calificar = async (n, calificacion, motivo) => {
    if (!guardado) return { ok: false, error: 'no hay un set guardado abierto.' }
    const id = guardado.id
    try {
      const r = await calificarTransicion(id, n, calificacion, motivo)
      if (!r.ok) return { ok: false, error: motivoDelRechazo(r.status, r.data) }
      const t = r.data.transicion
      aplicarCalificacion(id, n, { calificacion: t.calificacion, motivo: t.motivo, calificada: t.calificada }, r.data.resumen)
      setAviso(`Transición ${t.desde} → ${t.hasta}: ${t.calificacion}${t.motivo ? ` (${t.motivo})` : ''}, guardada.`)
      cargarSets()
      return { ok: true }
    } catch {
      return { ok: false, error: 'no pude conectar con el servidor. Revisá que esté corriendo y volvé a intentar.' }
    }
  }

  const descalificar = async (n) => {
    if (!guardado) return { ok: false, error: 'no hay un set guardado abierto.' }
    const id = guardado.id
    try {
      const r = await descalificarTransicion(id, n)
      if (!r.ok) return { ok: false, error: motivoDelRechazo(r.status, r.data) }
      aplicarCalificacion(id, n, { calificacion: null, motivo: null, calificada: null }, r.data.resumen)
      setAviso(`Transición ${n} → ${n + 1}: sin calificar.`)
      cargarSets()
      return { ok: true }
    } catch {
      return { ok: false, error: 'no pude conectar con el servidor. Revisá que esté corriendo y volvé a intentar.' }
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
          onClick={() => { setLib(null); setSet(null); setGuardado(null); setElegida(null); setIntento((n) => n + 1) }}>
          Reintentar
        </button>
      </div>
    )
  }

  const leyenda = lib.opciones ? lib.opciones.leyenda_key : null
  const curvas = lib.opciones ? lib.opciones.curvas : null
  // Lo que dibuja el panel del set: el set guardado abierto (su foto) o, si no hay, el armado.
  const vista = guardado || set_
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
          <SetsGuardados estado={sets} abiertoId={guardado ? guardado.id : null} onAbrir={abrirGuardado} />
        </div>

        <section className="rpanel rpanel-set" aria-labelledby="r-set-h">
          <div className="rset-head">
            <h2 id="r-set-h" className="rpanel-h" ref={setTituloRef} tabIndex={-1}>3 · El set</h2>
            {vista && (
              <span className="rset-cuenta mono">
                {vista.total} track{vista.total === 1 ? '' : 's'}
                {vista.pedidos != null && ` · ${vista.total} de ${vista.pedidos} pedidos`}
              </span>
            )}
            {/* aria-disabled y no disabled, por lo mismo que «Armar el set»: el botón no
                pierde el foco mientras exporta ni cuando todavía no hay set. */}
            <button type="button" className="btn btn-secondary rexportar"
              aria-disabled={!vista || exportando || armando}
              aria-describedby={vista ? undefined : 'r-exportar-falta'}
              onClick={exportar}>
              {exportando
                ? <><span className="spinner" aria-hidden="true" /> Exportando…</>
                : <><IconDownload size={15} /> Exportar a Rekordbox</>}
            </button>
            {!vista && <span className="sr-only" id="r-exportar-falta">Todavía no hay set: armá uno para poder exportarlo.</span>}
            {/* Guardar: solo el set ARMADO que se ve (un set guardado ya está guardado). */}
            {!guardado && (
              <button type="button" className="btn btn-secondary rguardar"
                aria-disabled={!set_ || guardando || armando}
                aria-describedby={set_ ? undefined : 'r-guardar-falta'}
                onClick={() => { if (set_ && !guardando && !armando) setDialogoGuardar(true) }}>
                {guardando ? <><span className="spinner" aria-hidden="true" /> Guardando…</> : 'Guardar set'}
              </button>
            )}
            {!set_ && !guardado && <span className="sr-only" id="r-guardar-falta">Todavía no hay set: armá uno para poder guardarlo.</span>}
          </div>

          {/* Calificar se habilita recién guardado: la calificación se escribe contra un set
              guardado, y guardarlo es un paso explícito (nunca un guardado silencioso). */}
          {set_ && !guardado && (
            <div className="rguardar-nota"><p className="rnota">Guardá el set para calificar cada transición (OK, regular o mala) mientras lo escuchás.</p></div>
          )}

          {errorGuardar && (
            <div className="alert alert-warn rguardar-error" role="alert">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 4l9 16H3z" /><path d="M12 10v4.5" /><circle cx="12" cy="17.4" r=".9" fill="currentColor" stroke="none" /></svg>
              <div>
                <div className="alert-title">No se guardó el set</div>
                <p>{errorGuardar.texto}</p>
                {/* 409: lo que se ve ya no es lo que el motor arma hoy. Re-armar con lo que el
                    set usó muestra el set de hoy, y ESE es el que se puede guardar. */}
                {errorGuardar.conflicto && set_ && set_.semilla && (
                  <button type="button" className="btn btn-secondary btn-sm rrearmar"
                    onClick={() => armar({ track: set_.semilla.id, ...set_.config })}>
                    Re-armar el set
                  </button>
                )}
              </div>
            </div>
          )}

          {errorSets && (
            <div className="alert alert-warn rsets-error" role="alert">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 4l9 16H3z" /><path d="M12 10v4.5" /><circle cx="12" cy="17.4" r=".9" fill="currentColor" stroke="none" /></svg>
              <div><div className="alert-title">Sets guardados</div><p>{errorSets}</p></div>
            </div>
          )}

          {guardado && (
            <CabeceraGuardado key={guardado.id} set={guardado} tituloRef={guardadoTituloRef}
              onRenombrar={renombrar} onBorrar={borrar} onCerrar={cerrarGuardado} />
          )}

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

          {!vista && !error && (
            <p className="rnota">Elegí una semilla y tocá «Armar el set»: acá van los pasos con su porqué.</p>
          )}

          {vista && (
            <>
              <Curva pasos={vista.pasos} />
              <div className="rset" role="list">
                {vista.pasos.map((p, i) => {
                  // La transición `n` va del paso n al n + 1: la que LLEGA a este paso es la
                  // del anterior. Solo existe en un set guardado (lo que dice la API).
                  const tr = guardado && i > 0 ? guardado.transiciones.find((x) => x.n === vista.pasos[i - 1].n) : null
                  return (
                    <Paso key={p.n} paso={p} leyenda={leyenda}
                      sonando={sonando === p.track.id} onAudio={alternarAudio}
                      errorAudio={errorAudio && errorAudio.id === p.track.id ? errorAudio.mensaje : null}
                      calificar={tr && (
                        <Calificar key={`${guardado.id}-${tr.n}`} setId={guardado.id} t={tr}
                          desdeTitulo={vista.pasos[i - 1].track.titulo} hastaTitulo={p.track.titulo}
                          onGuardar={calificar} onQuitar={descalificar} />
                      )} />
                  )
                })}
              </div>

              {/* Por qué se cortó: el titular y el detalle son los del motor. */}
              {vista.corte && (
                <div className="alert alert-warn" role="status">
                  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 4l9 16H3z" /><path d="M12 10v4.5" /><circle cx="12" cy="17.4" r=".9" fill="currentColor" stroke="none" /></svg>
                  <div>
                    <div className="alert-title">{vista.corte.titular}</div>
                    <p>{vista.corte.detalle}</p>
                    {vista.corte.codigo && <p className="rnota mono">{vista.corte.codigo}</p>}
                  </div>
                </div>
              )}
              {vista.aviso_fragmentos && <p className="rnota">{vista.aviso_fragmentos}</p>}
              {vista.leyenda_key && <p className="rnota">{vista.leyenda_key}</p>}
            </>
          )}
        </section>
      </div>

      {dialogoGuardar && set_ && (
        <GuardarDialog total={set_.total} onGuardar={guardar} onClose={() => setDialogoGuardar(false)} />
      )}

      <div className="sr-only" role="status" aria-live="polite">{anuncio}</div>
      {/* Guardar, abrir, calificar, renombrar y borrar: una frase corta por acción. */}
      <div className="sr-only rsets-aviso" role="status" aria-live="polite">{aviso}</div>
    </div>
  )
}
