import { useEffect, useRef, useState } from 'react'
import { reducirPicos } from '../minionda'
import { cargadorOndas } from '../ondas'
import { siguienteFila, textoCues } from '../radioPantalla'
import { fmtDur } from '../utils'
import Cover from './Cover'
import { IconPause, IconPlayFill } from './icons'

/* ============================================================================
   La lista del set (f50, el "maestro" del canvas): una fila por paso con carátula, título y
   artista, el porqué del motor, la miniondita, BPM, key, duración y cuántas marcas tiene.

   - Nada se calcula acá (§6): BPM, key, `?` y porqué vienen de la API tal cual, y el conteo
     de marcas de /api/radio/marcas/conteo.
   - La miniondita sale de /api/radio/tracks/{id}/onda (los picos del audio real, cacheados en
     el server) y se pide SOLO cuando la fila entra en pantalla, por la cola compartida con el
     editor (src/ondas.js: de a dos). Mientras no llega, un casillero gris con su forma.
   - Teclado: una sola parada de Tab por lista (la fila elegida); ↑ ↓ Inicio Fin cambian de
     fila. El botón de reproducir de la fila elegida es la parada siguiente.
   ========================================================================== */

const fmtBpm = (v) => (v === null || v === undefined ? null : Number(v).toFixed(1))
const BARRAS = 36

function MiniOnda({ id, disponible }) {
  const ref = useRef(null)
  const desde = (r) => (r && r.ok && Array.isArray(r.data?.picos)
    ? { fase: 'ok', barras: reducirPicos(r.data.picos, BARRAS) }
    : { fase: 'error', motivo: (r && r.data && (r.data.error || r.data.motivo)) || 'no se pudo leer la onda de este tema' })
  const [estado, setEstado] = useState(() => (disponible && cargadorOndas.guardado(id) ? desde(cargadorOndas.guardado(id)) : { fase: 'cargando' }))

  useEffect(() => {
    if (!disponible) return undefined
    const ya = cargadorOndas.guardado(id)
    if (ya) { setEstado(desde(ya)); return undefined }
    setEstado({ fase: 'cargando' })
    const el = ref.current
    // Sin IntersectionObserver (no pasa en un navegador de hoy) queda el casillero gris: antes
    // que pedir las 20 ondas juntas, no pedir ninguna.
    if (!el || typeof IntersectionObserver === 'undefined') return undefined
    let vivo = true
    let pedido = null
    // Solo lo que se ve: si la fila sale de la pantalla antes de su turno, se suelta y la cola
    // no la pide (si ya salió, termina y queda guardada para cuando vuelva).
    const io = new IntersectionObserver((entradas) => {
      const visible = entradas.some((e) => e.isIntersecting)
      if (visible && !pedido) {
        const p = cargadorOndas.cargar(id)
        pedido = p
        p.promesa.then((r) => { if (vivo) { setEstado(desde(r)); io.disconnect() } },
          () => { if (vivo && pedido === p) { pedido = null; setEstado({ fase: 'error', motivo: 'sin conexión con el servidor' }) } })
      } else if (!visible && pedido) {
        pedido.cancelar()
        pedido = null
      }
    })
    io.observe(el)
    return () => { vivo = false; io.disconnect(); if (pedido) pedido.cancelar() }
  }, [id, disponible]) // eslint-disable-line react-hooks/exhaustive-deps

  const fase = disponible ? estado.fase : 'falta'
  return (
    <span ref={ref} className={`rmini is-${fase}`} data-mini={id} aria-hidden="true"
      title={fase === 'error' ? `Sin onda: ${estado.motivo}` : fase === 'falta' ? 'El archivo ya no está en la biblioteca' : undefined}>
      {fase === 'ok' && estado.barras.length > 0 && (
        <svg viewBox={`0 0 ${estado.barras.length} 20`} preserveAspectRatio="none" focusable="false">
          {estado.barras.map((h, i) => {
            const alto = Math.max(1, h * 20)
            return <rect key={i} x={i + 0.15} width="0.7" y={(20 - alto) / 2} height={alto} />
          })}
        </svg>
      )}
    </span>
  )
}

function CeldaKey({ t, leyenda }) {
  const hayKey = !!(t.camelot || t.tonalidad)
  const porQueDuda = leyenda || 'la detección de la key no es confiable'
  if (!hayKey) return <span className="mb mb-key" title="El motor no le reconoció una key"><b>—</b></span>
  return (
    <span className={`mb mb-key${t.key_dudosa ? ' is-dudosa' : ''}`} title="Camelot y tonalidad clásica">
      <b>{t.camelot || '—'}</b>
      <span className="rkey-clasica">{t.tonalidad || '—'}</span>
      {t.key_dudosa && <span className="rduda" role="img" aria-label={porQueDuda} title={porQueDuda}>?</span>}
    </span>
  )
}

export default function ListaSet({ pasos, selN, onSel, sonando, onAudio, errorAudio, conteos, leyenda }) {
  const tablaRef = useRef(null)
  const ns = pasos.map((p) => p.n)

  const ir = (n) => {
    if (n === null || n === undefined) return
    onSel(n)
    // El foco va a la fila nueva: su botón ya es enfocable aunque todavía tenga tabIndex -1.
    tablaRef.current?.querySelector(`.rpaso-sel[data-n="${n}"]`)?.focus()
  }
  const teclaFila = (e, n) => {
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return
    const destino = siguienteFila(ns, n, e.key)
    if (destino === null) return
    e.preventDefault()
    ir(destino)
  }

  return (
    <div className="rtabla" role="table" aria-label={`El set: ${pasos.length} track${pasos.length === 1 ? '' : 's'}`}
      aria-describedby="r-tabla-ayuda" ref={tablaRef}>
      <div role="rowgroup" className="rtabla-head">
        <div role="row" className="rtabla-fila rtabla-th">
          <span role="columnheader" className="c-n">#</span>
          <span role="columnheader" className="c-play"><span className="sr-only">Escuchar</span></span>
          <span role="columnheader" className="c-art"><span className="sr-only">Carátula</span></span>
          <span role="columnheader" className="c-track">Track</span>
          <span role="columnheader" className="c-why"><span className="sr-only">Por qué lo eligió el motor</span></span>
          <span role="columnheader" className="c-onda">Onda</span>
          <span role="columnheader" className="c-bpm">BPM</span>
          <span role="columnheader" className="c-key">Key</span>
          <span role="columnheader" className="c-dur">Dur</span>
          <span role="columnheader" className="c-cues">Cues</span>
        </div>
      </div>
      <div role="rowgroup" className="rset">
        {pasos.map((p) => {
          const t = p.track
          // Solo un set guardado manda `en_biblioteca`: `false` = el archivo ya no está en la
          // biblioteca del motor. El paso se ve entero (es la foto) y no se puede reproducir.
          const falta = t.en_biblioteca === false
          const sel = p.n === selN
          const suena = sonando === t.id
          const n = falta ? undefined : conteos[t.id]
          const err = errorAudio && errorAudio.id === t.id ? errorAudio.mensaje : null
          return (
            <div role="row" key={p.n} data-n={p.n}
              className={`rtabla-fila rpaso${sel ? ' is-sel' : ''}${falta ? ' is-falta' : ''}`}
              onClick={(e) => { if (!e.defaultPrevented) onSel(p.n) }}>
              <span role="cell" className="c-n"><span className="rpaso-n mono">{p.n}</span></span>
              <span role="cell" className="c-play">
                {falta
                  ? <span className="rplay is-sin" aria-hidden="true" />
                  : (
                    <button type="button" className={`rplay${suena ? ' on' : ''}`} tabIndex={sel ? 0 : -1}
                      aria-label={`${suena ? 'Pausar' : 'Reproducir'} ${t.titulo}`}
                      onClick={(e) => { e.preventDefault(); e.stopPropagation(); onAudio(t) }}>
                      {suena ? <IconPause size={14} /> : <IconPlayFill size={14} />}
                    </button>
                  )}
              </span>
              <span role="cell" className="c-art"><span className="rart" aria-hidden="true"><Cover track={t} /></span></span>
              <span role="cell" className="c-track">
                <button type="button" className="rpaso-sel" data-n={p.n} tabIndex={sel ? 0 : -1}
                  aria-current={sel ? 'true' : undefined} aria-controls="r-det"
                  onClick={(e) => { e.preventDefault(); ir(p.n) }} onKeyDown={(e) => teclaFila(e, p.n)}>
                  <span className="rpaso-titulo truncate">{t.titulo}</span>
                  <span className="rpaso-artista truncate">{t.artista || '—'}</span>
                </button>
              </span>
              <span role="cell" className="c-why">
                {/* El porqué TAL CUAL lo escribió el motor (`Transition.reason()`): también en la
                    fila (un renglón propio, a lo ancho), porque §6 pide que lo que el DJ necesita
                    para decidir se vea sin abrir el track. El detalle lo repite en grande. */}
                <span className={`rpaso-why${p.es_semilla ? ' is-seed' : ''}`}>
                  <span className="rpaso-why-ico" aria-hidden="true">{p.es_semilla ? '◉' : '↳'}</span>
                  <span className="mono">{p.motivo}</span>
                </span>
                {(p.es_semilla || falta) && (
                  <span className="rpaso-tags">
                    {p.es_semilla && <span className="rtag-seed">semilla</span>}
                    {falta && <span className="rtag-falta">ya no está en la biblioteca</span>}
                  </span>
                )}
                {/* El 404 de la radio explica si el archivo se movió o si la base se escaneó en
                    otra máquina: se muestra el texto del backend, no uno resumido acá. */}
                {err && <span className="rnota rnota-err rpaso-err" role="alert">{err}</span>}
              </span>
              <span role="cell" className="c-onda"><MiniOnda id={t.id} disponible={!falta} /></span>
              <span role="cell" className="c-bpm"><span className="mb" title="BPM medido por el motor"><span className="mb-label sr-only">BPM</span><b>{fmtBpm(t.bpm) ?? '—'}</b></span></span>
              <span role="cell" className="c-key"><CeldaKey t={t} leyenda={leyenda} /></span>
              <span role="cell" className="c-dur"><span className="mono">{t.dur != null ? fmtDur(t.dur) : '—'}</span></span>
              <span role="cell" className="c-cues">
                <span className={`rcues${n === 0 ? ' is-sin' : ''}`}
                  title={n === undefined ? 'Sin conteo de marcas' : `${n} marca${n === 1 ? '' : 's'} guardada${n === 1 ? '' : 's'} (hot cues, memory y loops)`}>
                  {textoCues(n)}
                </span>
              </span>
            </div>
          )
        })}
      </div>
      <p className="sr-only" id="r-tabla-ayuda">Con el foco en un track: flechas arriba y abajo para cambiar de track; el detalle se abre al costado.</p>
    </div>
  )
}

// Usado por el detalle: el mismo dibujo de key que la fila (Camelot y clásica, con el `?`).
export { CeldaKey }
