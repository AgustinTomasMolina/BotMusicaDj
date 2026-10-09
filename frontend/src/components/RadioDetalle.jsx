import { useEffect, useRef, useState } from 'react'
import { fmtTiempo } from '../cues'
import { cargadorOndas } from '../ondas'
import { siguienteTab, textoEnergia } from '../radioPantalla'
import { fmtDur } from '../utils'
import Cover from './Cover'
import CueEditor from './CueEditor'
import { CeldaKey } from './RadioLista'

/* ============================================================================
   El detalle del track elegido en la lista del set (f50, el "detalle" del canvas): carátula,
   título, artista, BPM / KEY / DUR, el «¿Por qué este track?» y las pestañas.

   - «¿Por qué este track?» es `Transition.reason()` tal cual (el mismo texto de la fila) y la
     energía del motor (percentil del anterior → percentil de este). No hay puntajes
     inventados, ni «% de match», ni género: el motor multiplica compatibilidad por encaje y la
     biblioteca no tiene género.
   - Pestañas (patrón tablist): «Cues y loops» (el editor de f48), «Información» (lo que la
     biblioteca del motor sabe del archivo) y las que vienen, deshabilitadas.
   - El editor queda montado al pasar a «Información» (el audio y lo que se estaba editando no
     se pierden); se cambia de tema eligiendo otra fila.
   ========================================================================== */

// Licencia y origen son opcionales (decisión del dueño, 2026-10-09): sin valor, «no declarado».
// La API ya manda ese literal (la base lo guarda así, `motor.modelos.NO_DECLARADO`); esto es la
// defensa para una respuesta sin el dato: nunca se dibuja un vacío ni un valor inventado.
const declarado = (v) => (typeof v === 'string' && v.trim() ? v : 'no declarado')

const fmtBpm = (v) => (v === null || v === undefined ? null : Number(v).toFixed(1))

const PESTANAS = [
  { k: 'cues', label: 'Cues y loops' },
  { k: 'info', label: 'Información' },
  { k: 'onda', label: 'Onda avanzada', off: true },
]

/* ---------- «Información»: lo que la biblioteca del motor sabe de este archivo ---------- */
function Informacion({ paso, datos }) {
  const t = paso.track
  const falta = t.en_biblioteca === false
  // La duración del audio DECODIFICADO sale de la misma onda que dibuja el editor (cola
  // compartida: si ya se pidió, no se pide otra vez).
  const [onda, setOnda] = useState(() => cargadorOndas.guardado(t.id) || null)
  useEffect(() => {
    if (falta) return undefined
    let vivo = true
    const p = cargadorOndas.cargar(t.id)
    p.promesa.then((r) => { if (vivo) setOnda(r) }, () => { if (vivo) setOnda({ ok: false, data: { error: 'sin conexión con el servidor' } }) })
    return () => { vivo = false; p.cancelar() }
  }, [t.id, falta])

  if (falta) {
    return (
      <div className="rinfo">
        <p className="rnota">Este archivo ya no está en la biblioteca del motor: lo que se ve en la lista es la foto que se guardó con el set.</p>
        <dl className="rinfo-dl">
          <dt>Licencia</dt><dd>{declarado(t.licencia)}</dd>
          <dt>Origen</dt><dd>{declarado(t.origen)}</dd>
        </dl>
      </div>
    )
  }
  const d = datos && ((datos.track && datos.track.id === t.id) || datos.id === t.id) ? datos : null
  if (!d) return <p className="rnota rinfo-cargando">Leyendo lo que la biblioteca sabe de este archivo…</p>
  if (d.error) return <p className="rnota rnota-err" role="alert">{d.error}</p>
  const x = d.track
  const durAudio = onda && onda.ok && onda.data ? onda.data.duracion_audio : null
  const motivoOnda = onda && !onda.ok ? (onda.data && (onda.data.error || onda.data.motivo)) : null
  return (
    <div className="rinfo">
      <p className="rnota">Lo que tiene hoy la biblioteca del motor para este archivo.</p>
      <dl className="rinfo-dl">
        <dt>Archivo</dt><dd className="mono rinfo-ruta">{x.ruta || '—'}</dd>
        <dt>Formato</dt><dd className="mono">{x.formato ? x.formato.toUpperCase() : '—'}</dd>
        <dt>Duración (biblioteca)</dt><dd className="mono">{fmtTiempo(x.dur, 1) ?? '—'}</dd>
        <dt>Duración del audio</dt>
        <dd className="mono">{durAudio != null ? fmtTiempo(durAudio, 1) : motivoOnda ? <span className="rnota-err">{motivoOnda}</span> : 'leyendo el audio…'}</dd>
        <dt>BPM medido</dt><dd className="mono">{x.bpm == null ? '? (el análisis no encontró pulso)' : fmtBpm(x.bpm)}</dd>
        <dt>Key</dt><dd><CeldaKey t={x} leyenda={null} /></dd>
        <dt>Acuerdo de la key</dt><dd className="mono">{x.key_acuerdo ? `${x.key_acuerdo} tramos` : 'sin medir'}</dd>
        <dt>Energía</dt><dd className="mono">{x.energia_pct ?? '—'} <span className="rinfo-nota">percentil en la biblioteca</span></dd>
        <dt>Licencia</dt><dd>{declarado(x.licencia)}</dd>
        <dt>Origen</dt><dd className="rinfo-ruta">{declarado(x.origen)}</dd>
      </dl>
    </div>
  )
}

export default function Detalle({ paso, anterior, leyenda, calificar, notaCalificar, onConteo, focoEditor = 0 }) {
  const [tab, setTab] = useState('cues')
  // Lo que la API de marcas dijo del tema abierto en el editor (para «Información»).
  const [datos, setDatos] = useState(null)
  const tabsRef = useRef(null)
  const raizRef = useRef(null)

  // Una fila elegida con el mouse o con Enter: el foco va a la onda del editor (si la pestaña
  // «Cues y loops» está a la vista), así C, M, I/O y Espacio actúan enseguida. Con el detalle al
  // costado (pegajoso) puede desplazarse adentro del panel; apilado (teléfono), la página no
  // salta hasta el editor.
  useEffect(() => {
    if (!focoEditor) return
    const raiz = raizRef.current
    const onda = raiz && raiz.querySelector('#r-panel-cues:not([hidden]) .cue-wave')
    if (!onda) return
    const alCostado = getComputedStyle(raiz).position === 'sticky'
    onda.focus({ preventScroll: !alCostado })
  }, [focoEditor])

  if (!paso) {
    return (
      <aside className="rdet" id="r-det" aria-label="Detalle del track">
        <p className="rnota rdet-vacio">Cuando haya un set, elegí un track de la lista: acá van su porqué, sus cues y lo que la biblioteca sabe de él.</p>
      </aside>
    )
  }
  const t = paso.track
  const falta = t.en_biblioteca === false

  const elegir = (k, foco = false) => {
    const p = PESTANAS.find((x) => x.k === k)
    if (!p || p.off) return
    setTab(k)
    if (foco) tabsRef.current?.querySelector(`#r-tab-${k}`)?.focus()
  }
  const teclaTabs = (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return
    const k = siguienteTab(PESTANAS, tab, e.key)
    if (k === null) return
    e.preventDefault()
    elegir(k, true)
  }

  return (
    <aside className="rdet" id="r-det" aria-labelledby="r-det-h" ref={raizRef}>
      <div className="rdet-cab">
        <span className="rdet-art" aria-hidden="true"><Cover track={t} /></span>
        <div className="rdet-id">
          <span className="rdet-th">Track {paso.n}</span>
          <h2 id="r-det-h" className="rdet-titulo">{t.titulo}</h2>
          <p className="rdet-artista">{t.artista || '—'}</p>
          <div className="rdet-chips">
            <span className="mb" title="BPM medido por el motor"><span className="mb-label">BPM</span><b>{fmtBpm(t.bpm) ?? '—'}</b></span>
            <span className="rdet-key"><span className="mb-label">Key</span><CeldaKey t={t} leyenda={leyenda} /></span>
            <span className="mb" title="Duración"><span className="mb-label">Dur</span><b>{t.dur != null ? fmtDur(t.dur) : '—'}</b></span>
          </div>
        </div>
      </div>

      <section className="rdet-why" aria-labelledby="r-why-h">
        <h3 id="r-why-h" className="rdet-th">{paso.es_semilla ? '¿Por qué este track? · es la semilla del set' : '¿Por qué este track? · encaje con el anterior'}</h3>
        <p className="rdet-motivo mono">{paso.motivo}</p>
        <p className="rdet-energia">
          Energía <span className="rinfo-nota">(percentil en tu biblioteca)</span>:{' '}
          <span className="mono">{textoEnergia(anterior ? anterior.track.energia_pct : null, t.energia_pct, { hayAnterior: !!anterior })}</span>
        </p>
        <p className="rnota">Lo que dice el motor, sin puntajes inventados. El género no se usa: la biblioteca no lo tiene.</p>
        {notaCalificar && <p className="rnota">{notaCalificar}</p>}
        {calificar}
      </section>

      <div className="rdet-tabs" role="tablist" aria-label="Detalle del track" ref={tabsRef} onKeyDown={teclaTabs}>
        {PESTANAS.map((p) => (
          <button key={p.k} type="button" role="tab" id={`r-tab-${p.k}`} className={`rtab${tab === p.k ? ' is-on' : ''}`}
            aria-selected={tab === p.k} aria-controls={p.off ? undefined : `r-panel-${p.k}`}
            aria-disabled={p.off || undefined} tabIndex={tab === p.k ? 0 : -1}
            onClick={() => elegir(p.k)}>
            <span>{p.label}</span>{p.off && <span className="rtab-pronto"><span className="sr-only">, </span>próximamente</span>}
          </button>
        ))}
      </div>

      <div role="tabpanel" id="r-panel-cues" aria-labelledby="r-tab-cues" className="rdet-panel" hidden={tab !== 'cues'}>
        {falta
          ? <p className="rnota">Este archivo ya no está en la biblioteca del motor: no hay audio ni duración contra los cuales marcar.</p>
          : <CueEditor trackId={t.id} titulo={t.titulo} onConteo={onConteo} onDatos={(id, d) => setDatos(d)} />}
      </div>
      <div role="tabpanel" id="r-panel-info" aria-labelledby="r-tab-info" className="rdet-panel" hidden={tab !== 'info'}>
        {tab === 'info' && <Informacion paso={paso} datos={datos} />}
      </div>
    </aside>
  )
}
