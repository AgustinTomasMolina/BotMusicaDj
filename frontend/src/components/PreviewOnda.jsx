import { useEffect, useId, useRef, useState } from 'react'
import { ALTO_PREVIEW, BANDAS_PREVIEW, durEje, marcasEnOnda, ondaPreview, resumenMarcas } from '../previewOnda'
import { cargadorMarcasPreview, cargadorPreviews, claveMarcas } from '../previews'
import { puntosMarcas, textoMarcas } from '../playlistImport'

/* ============================================================================
   La vista previa de la onda en una fila de Mis playlists (f62, la columna «Preview» de
   Rekordbox): la onda de 3 bandas del tema entero con las marcas guardadas encima, cada una con
   SU color (pad, memory, loop). Tocarla abre el editor D en ese tema, como «Marcar cues».

   - Se pide SOLO cuando la fila entra en pantalla (IntersectionObserver), por las colas de
     src/previews.js (de a dos). Si la fila se va antes de su turno, el pedido se suelta. Lo
     que la cola no guarda (un 409, un 5xx, la base ocupada) se vuelve a pedir cuando la fila
     entra otra vez en pantalla.
   - Nada se inventa (§6): mientras no llega, la caja vacía; si la onda no se pudo leer, «sin
     onda» con el motivo en el título; las marcas, solo las que devolvió la API.
   - `version` cambia cuando el editor D tocó marcas: se vuelven a leer (la onda no).
   - Es un botón de 44 px de alto (objetivo táctil, pedido del dueño) con la onda de 24 px
     adentro; su nombre dice qué abre y cuántas marcas hay.
   ========================================================================== */

const desdeOnda = (r) => (r && r.ok && r.data && r.data.bandas
  ? { fase: 'ok', data: r.data }
  : { fase: 'error', motivo: (r && r.data && (r.data.error || r.data.motivo)) || 'no se pudo leer la onda de este tema' })

const desdeMarcas = (r) => (r && r.ok && r.data && r.data.track && Array.isArray(r.data.marcas)
  ? { fase: 'ok', track: r.data.track, marcas: r.data.marcas }
  : { fase: 'error', motivo: (r && r.data && (r.data.error || r.data.motivo)) || 'no se pudieron leer las marcas' })

export default function PreviewOnda({ id, titulo, version = 0, resumenBase, onAbrir }) {
  const ref = useRef(null)
  const clave = claveMarcas(id, version)
  const inicialOnda = () => (cargadorPreviews.guardado(id) ? desdeOnda(cargadorPreviews.guardado(id)) : { fase: 'cargando' })
  const [onda, setOnda] = useState(inicialOnda)
  // Las marcas de la versión anterior se siguen viendo hasta que llegan las nuevas.
  const [marcas, setMarcas] = useState(() => (cargadorMarcasPreview.guardado(clave) ? desdeMarcas(cargadorMarcasPreview.guardado(clave)) : { fase: 'cargando' }))

  useEffect(() => {
    const yaOnda = cargadorPreviews.guardado(id)
    const yaMarcas = cargadorMarcasPreview.guardado(clave)
    if (yaOnda) setOnda(desdeOnda(yaOnda))
    else setOnda((o) => (o.fase === 'ok' && o.data && o.id === id ? o : { fase: 'cargando' }))
    if (yaMarcas) setMarcas(desdeMarcas(yaMarcas))
    if (yaOnda && yaMarcas) return undefined
    const el = ref.current
    // Sin IntersectionObserver (no pasa en un navegador de hoy) no se pide nada: antes que pedir
    // todas las filas juntas, la caja vacía.
    if (!el || typeof IntersectionObserver === 'undefined') return undefined
    let vivo = true
    let pOnda = null
    let pMarcas = null
    // Ya no hace falta mirar: las dos respuestas quedaron guardadas. Si alguna no se guardó (algo
    // pasajero), se sigue mirando y se vuelve a pedir cuando la fila entra otra vez en pantalla.
    const listo = () => Boolean(cargadorPreviews.guardado(id) && cargadorMarcasPreview.guardado(clave))
    const io = new IntersectionObserver((entradas) => {
      const visible = entradas.some((e) => e.isIntersecting)
      if (visible) {
        // Lo que llegó (y quedó guardado) mientras la fila estaba fuera de pantalla: el pedido
        // que ya había salido terminó, pero su espera se había soltado y nadie lo dibujó.
        const cOnda = cargadorPreviews.guardado(id)
        const cMarcas = cargadorMarcasPreview.guardado(clave)
        if (cOnda) setOnda({ ...desdeOnda(cOnda), id })
        if (cMarcas) setMarcas(desdeMarcas(cMarcas))
        if (cOnda && cMarcas) { io.disconnect(); return }
        if (!cOnda && !pOnda) {
          const p = cargadorPreviews.cargar(id)
          pOnda = p
          p.promesa.then((r) => {
            if (pOnda === p) pOnda = null
            if (vivo) { setOnda({ ...desdeOnda(r), id }); if (listo()) io.disconnect() }
          }, () => { if (pOnda === p) pOnda = null; if (vivo) setOnda({ fase: 'error', motivo: 'sin conexión con el servidor' }) })
        }
        if (!cMarcas && !pMarcas) {
          const p = cargadorMarcasPreview.cargar(clave)
          pMarcas = p
          p.promesa.then((r) => {
            if (pMarcas === p) pMarcas = null
            if (vivo) { setMarcas(desdeMarcas(r)); if (listo()) io.disconnect() }
          }, () => { if (pMarcas === p) pMarcas = null; if (vivo) setMarcas({ fase: 'error', motivo: 'sin conexión con el servidor' }) })
        }
      } else {
        // Se fue de la pantalla: lo que todavía no salió se saca de la cola.
        if (pOnda) { pOnda.cancelar(); pOnda = null }
        if (pMarcas) { pMarcas.cancelar(); pMarcas = null }
      }
    })
    io.observe(el)
    return () => { vivo = false; io.disconnect(); pOnda?.cancelar(); pMarcas?.cancelar() }
  }, [id, clave]) // eslint-disable-line react-hooks/exhaustive-deps

  const ok = marcas.fase === 'ok'
  const durAudio = onda.fase === 'ok' ? Number(onda.data.duracion_audio) || 0 : 0
  // El eje: el de la biblioteca cuando llegaron las marcas; si no se pudieron leer, el del audio
  // (sin marcas que ubicar, la onda ocupa la caja entera). Mientras las marcas cargan, nada: la
  // onda no se dibuja en un eje que después cambia.
  const dur = ok ? durEje(marcas.track, durAudio) : marcas.fase === 'error' ? durAudio : 0
  const dibujo = onda.fase === 'ok' && dur > 0 ? ondaPreview(onda.data, dur) : null
  const lista = ok ? marcasEnOnda(marcas.marcas, dur) : []
  // Llegó la onda y las marcas ya se resolvieron, pero no hay qué dibujar (sin duración en la
  // biblioteca ni en el audio, o sin puntos): se dice, no queda «cargando» para siempre.
  const sinDibujo = onda.fase === 'ok' && marcas.fase !== 'cargando' && !dibujo
  const motivoOnda = onda.fase === 'error' ? onda.motivo : sinDibujo ? 'sin duración' : null
  // Las marcas que no se pudieron leer no se cuentan con el resumen viejo de la playlist.
  const resumen = ok ? resumenMarcas(marcas.marcas) : marcas.fase === 'error' ? 'marcas sin leer' : resumenBase || null
  // El motivo del servidor ya puede terminar en punto («La biblioteca está ocupada.»).
  const frase = (t) => `${String(t).trim().replace(/[.\s]+$/, '')}.`
  const avisos = [
    motivoOnda ? `Sin onda: ${frase(motivoOnda)}` : null,
    marcas.fase === 'error' ? `Sin marcas: ${frase(marcas.motivo)}` : null,
    lista.some((m) => m.fuera) ? 'Hay marcas después del final del tema (el archivo cambió): se ven pegadas al borde.' : null,
  ].filter(Boolean)
  const fase = motivoOnda ? 'error' : dibujo ? 'ok' : 'cargando'
  const idAvisos = useId()

  return (
    <button type="button" ref={ref} className={`pv is-${fase}`} data-pv={id}
      data-dur={dur > 0 ? dur : undefined}
      aria-label={`Marcar cues de «${titulo}»${resumen ? ` (${resumen})` : ''}`}
      aria-describedby={avisos.length ? idAvisos : undefined}
      title={avisos.length ? avisos.join(' ') : resumen ? `${resumen}. Abrir el editor de cues` : 'Abrir el editor de cues'}
      onClick={onAbrir}>
      <span className="pv-caja" aria-hidden="true">
        {dibujo && (
          <svg className="pv-onda" style={{ width: `${dibujo.ancho * 100}%` }} viewBox={`0 0 ${dibujo.puntos} ${ALTO_PREVIEW}`}
            preserveAspectRatio="none" focusable="false">
            {BANDAS_PREVIEW.map((b) => <path key={b} className={`pv-${b}`} d={dibujo.trazos[b]} />)}
          </svg>
        )}
        {fase === 'error' && <span className="pv-txt">sin onda</span>}
        {lista.map((m) => (
          <i key={m.id} className={`pv-mk is-${m.tipo} ${m.clase}`} data-id={m.id} data-tipo={m.tipo}
            style={m.width === null ? { left: `${m.left}%` } : { left: `${m.left}%`, width: `${m.width}%` }} />
        ))}
      </span>
      {/* Los avisos también para el lector de pantalla (descripción del botón), no solo en el title. */}
      {avisos.length > 0 && <span id={idAvisos} className="pv-avisos" hidden>{avisos.join(' ')}</span>}
    </button>
  )
}

// El lugar de la vista previa cuando no hay nada que dibujar: el tema no tiene archivo en esta
// PC o el motor todavía no lo analizó (`texto` lo dice: «sin archivo», «sin analizar»,
// «analizando», «en cola», «falló»). Texto apagado, nunca una onda de mentira. Si el tema ya
// tiene marcas (importadas de Rekordbox, f56), van los puntos de color y cuántas de cada una:
// sin onda no se pueden ubicar, pero no se esconden.
export function PreviewVacia({ texto, marcas }) {
  const puntos = puntosMarcas(marcas)
  return (
    <span className="pv-vacio">
      <span className="pv-caja">{texto}</span>
      {puntos.length > 0 && (
        <span className="cue-puntos">
          <span aria-hidden="true">{puntos.map((p, i) => <i key={i} className={p.clase} />)}</span>
          <span className="cue-puntos-txt">{textoMarcas(marcas)}</span>
        </span>
      )}
    </span>
  )
}
