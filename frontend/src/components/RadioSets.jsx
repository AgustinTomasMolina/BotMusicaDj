import { useEffect, useRef, useState } from 'react'
import { useDialog } from '../hooks'

/* ============================================================================
   Sets guardados de la Radio DJ (tarea 16): guardar el set que se ve, calificar cada
   transición (ok / regular / mala) y volver a un set guardado.

   Misma regla que Radio.jsx (spec §6): acá no se calcula nada del motor. La foto de un set
   guardado, sus calificaciones y el resumen vienen de /api/radio/sets*; lo único que se hace
   con esos datos es dibujarlos (y restar `sin_calificar` del total de transiciones para decir
   "3 de 9 calificadas", que son dos números de la API). Los componentes de este archivo no
   hablan con la red: reciben callbacks de Radio.jsx, que es quien pide y muestra el motivo
   del backend tal cual.
   ========================================================================== */

// Los tres niveles, en el orden y con los valores que acepta la API (`motor/saved_sets.py`).
// El texto es solo la etiqueta en pantalla.
const NIVELES = [
  { k: 'ok', label: 'OK' },
  { k: 'regular', label: 'Regular' },
  { k: 'mala', label: 'Mala' },
]

// Fecha legible del ISO 8601 UTC que manda la API. Formatear una fecha no la cambia; el valor
// exacto queda en `dateTime` para quien lo necesite (y para el E2E).
function Fecha({ iso }) {
  let texto = iso
  try {
    const d = new Date(iso)
    if (!Number.isNaN(d.getTime())) texto = d.toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'short' })
  } catch { /* se muestra el ISO tal cual */ }
  return <time dateTime={iso}>{texto}</time>
}

const nombreDeSet = (s) => (s.nombre ? `«${s.nombre}»` : 'sin nombre')

/* El reparto de calificaciones tal cual lo manda la API (`resumen`). */
export function Resumen({ resumen, compacto = false }) {
  if (!resumen) return null
  const total = resumen.ok + resumen.regular + resumen.mala + resumen.sin_calificar
  const calificadas = total - resumen.sin_calificar
  // Adentro del botón de la lista va un <span>: un <p> no puede ir dentro de un <button>.
  const Tag = compacto ? 'span' : 'p'
  return (
    <Tag className={`rresumen${compacto ? ' is-compacto' : ''}`}>
      {!compacto && (
        <span className="rresumen-cuenta" data-k="calificadas">
          <b className="mono">{calificadas}</b> de <b className="mono">{total}</b> transicion{total === 1 ? '' : 'es'} calificada{total === 1 ? '' : 's'}
        </span>
      )}
      {NIVELES.map((n) => (
        <span key={n.k} className={`rresumen-n is-${n.k}`} data-k={n.k}>{n.label} <b className="mono">{resumen[n.k]}</b></span>
      ))}
      <span className="rresumen-n is-sin" data-k="sin_calificar">Sin calificar <b className="mono">{resumen.sin_calificar}</b></span>
    </Tag>
  )
}

/* ---------- La lista de sets guardados ---------- */
export function SetsGuardados({ estado, abiertoId, onAbrir }) {
  return (
    <section className="rpanel" aria-labelledby="r-sets-h">
      <h2 id="r-sets-h" className="rpanel-h">Sets guardados</h2>
      <p className="rpanel-sub">Abrí uno para ver la foto que guardaste, escucharlo y seguir calificando.</p>
      {estado.cargando && <p className="rnota">Cargando los sets guardados…</p>}
      {!estado.cargando && estado.motivo && <p className="rnota rnota-err" role="alert">{estado.motivo}</p>}
      {!estado.cargando && !estado.motivo && estado.sets.length === 0 && (
        <p className="rnota">Todavía no guardaste ningún set. Armá uno y tocá «Guardar set».</p>
      )}
      {estado.sets.length > 0 && (
        <ul className="rsets" aria-label="Sets guardados">
          {estado.sets.map((s) => (
            <li key={s.id}>
              {/* Sin aria-label: el nombre accesible es el contenido entero (nombre, fecha,
                  semilla, largo y resumen), que es lo que hace falta para elegir uno. */}
              <button type="button" className="rsg" aria-pressed={abiertoId === s.id} onClick={() => onAbrir(s.id)}>
                <span className="rsg-top">
                  <span className="rsg-nombre truncate">{s.nombre || <i className="rsg-sin">Sin nombre</i>}</span>
                  <span className="rsg-id mono">#{s.id}</span>
                </span>
                <span className="rsg-meta">
                  <Fecha iso={s.guardado} />
                  <span className="rsg-semilla truncate" data-k="semilla">desde {s.semilla}</span>
                  <span className="mono" data-k="largo">{s.total} de {s.pedidos} tracks</span>
                  {s.curva && <span className="mono" data-k="curva">{s.curva}</span>}
                </span>
                <Resumen resumen={s.resumen} compacto />
                {s.faltan > 0 && (
                  <span className="rsg-faltan" data-k="faltan">{s.faltan} track{s.faltan === 1 ? '' : 's'} ya no está{s.faltan === 1 ? '' : 'n'} en la biblioteca</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/* ---------- Diálogo: guardar el set que se ve ---------- */
export function GuardarDialog({ total, onGuardar, onClose }) {
  const ref = useRef(null)
  const [nombre, setNombre] = useState('')
  useDialog(ref, true, onClose)
  return (
    <div className="dialog-backdrop" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <form className="dialog" ref={ref} role="dialog" aria-modal="true" aria-labelledby="r-guardar-h"
        aria-describedby="r-guardar-d" tabIndex={-1}
        onSubmit={(e) => { e.preventDefault(); onGuardar(nombre) }}>
        <div className="dialog-title" id="r-guardar-h">Guardar el set</div>
        <p className="dialog-body" id="r-guardar-d">
          Se guarda la foto de lo que estás viendo: los {total} tracks, sus datos y el porqué de cada
          transición. Después podés calificar cada transición mientras lo escuchás.
        </p>
        <label className="rcfg-label" htmlFor="r-guardar-nombre">Nombre (opcional)</label>
        <input className="input" id="r-guardar-nombre" value={nombre} maxLength={200} autoComplete="off"
          onChange={(e) => setNombre(e.target.value)} placeholder="Ej.: prueba peak viernes" data-autofocus />
        <div className="dialog-actions">
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancelar</button>
          <button type="submit" className="btn btn-primary">Guardar set</button>
        </div>
      </form>
    </div>
  )
}

/* ---------- Diálogo: confirmar el borrado ---------- */
function BorrarDialog({ set, onBorrar, onClose }) {
  const ref = useRef(null)
  useDialog(ref, true, onClose)
  const r = set.resumen || { ok: 0, regular: 0, mala: 0 }
  const calificadas = r.ok + r.regular + r.mala
  return (
    <div className="dialog-backdrop" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="dialog" ref={ref} role="alertdialog" aria-modal="true" aria-labelledby="r-borrar-h"
        aria-describedby="r-borrar-d" tabIndex={-1}>
        <div className="dialog-title" id="r-borrar-h">¿Borrar el set guardado #{set.id} ({nombreDeSet(set)})?</div>
        <p className="dialog-body" id="r-borrar-d">
          Se borran la foto del set y sus {calificadas} calificacion{calificadas === 1 ? '' : 'es'}. Los archivos de
          audio no se tocan. No se puede deshacer.
        </p>
        <div className="dialog-actions">
          <button type="button" className="btn btn-secondary" onClick={onClose} data-autofocus>Cancelar</button>
          <button type="button" className="btn btn-secondary rborrar-ok" onClick={onBorrar}>Borrar el set</button>
        </div>
      </div>
    </div>
  )
}

/* ---------- Cabecera del set guardado que se está mirando ---------- */
export function CabeceraGuardado({ set, tituloRef, onRenombrar, onBorrar, onCerrar }) {
  const [editando, setEditando] = useState(false)
  const [nombre, setNombre] = useState(set.nombre || '')
  const [borrando, setBorrando] = useState(false)
  const [ocupado, setOcupado] = useState(false)
  const renombrarRef = useRef(null)
  const inputRef = useRef(null)

  // Al abrir el formulario, el foco al campo; al cerrarlo, de vuelta a «Renombrar» (que
  // vuelve a existir recién en ese render). La primera vez no se mueve nada.
  const abierto = useRef(false)
  useEffect(() => {
    if (editando) { inputRef.current?.focus(); abierto.current = true }
    else if (abierto.current) { renombrarRef.current?.focus(); abierto.current = false }
  }, [editando])

  const cancelar = () => {
    setEditando(false)
    setNombre(set.nombre || '')
  }
  const guardarNombre = async (e) => {
    e.preventDefault()
    if (ocupado) return
    setOcupado(true)
    const ok = await onRenombrar(nombre)
    setOcupado(false)
    if (ok) cancelar()
  }

  return (
    <div className="rguardado" aria-labelledby="r-guardado-h" role="region">
      <div className="rguardado-top">
        <h3 id="r-guardado-h" className="rguardado-h" ref={tituloRef} tabIndex={-1}>
          Set guardado <span className="mono">#{set.id}</span>
          <span className="rguardado-nombre">{set.nombre ? `«${set.nombre}»` : <i>sin nombre</i>}</span>
        </h3>
        <span className="rguardado-fecha">guardado el <Fecha iso={set.guardado} /></span>
      </div>
      {editando ? (
        <form className="rguardado-form" onSubmit={guardarNombre}
          onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); cancelar() } }}>
          <label className="sr-only" htmlFor="r-renombrar">Nombre nuevo del set #{set.id} (vacío = sin nombre)</label>
          <input className="input" id="r-renombrar" ref={inputRef} value={nombre} maxLength={200} autoComplete="off"
            onChange={(e) => setNombre(e.target.value)} placeholder="Sin nombre" />
          <button type="submit" className="btn btn-primary btn-sm" aria-disabled={ocupado}>Guardar nombre</button>
          <button type="button" className="btn btn-secondary btn-sm" onClick={cancelar}>Cancelar</button>
        </form>
      ) : (
        <div className="rguardado-acciones">
          <button type="button" className="btn btn-secondary btn-sm" ref={renombrarRef} onClick={() => { setNombre(set.nombre || ''); setEditando(true) }}>Renombrar</button>
          <button type="button" className="btn btn-secondary btn-sm rborrar" onClick={() => setBorrando(true)}>Borrar</button>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onCerrar}>Cerrar</button>
        </div>
      )}
      <Resumen resumen={set.resumen} />
      {set.faltan > 0 && (
        <p className="rnota">
          {set.faltan} track{set.faltan === 1 ? '' : 's'} de este set ya no está{set.faltan === 1 ? '' : 'n'} en la biblioteca del
          motor: se ve{set.faltan === 1 ? '' : 'n'} con los datos de la foto, pero no se puede{set.faltan === 1 ? '' : 'n'} reproducir.
        </p>
      )}
      {borrando && (
        <BorrarDialog set={set} onClose={() => setBorrando(false)}
          onBorrar={async () => { const ok = await onBorrar(); if (!ok) setBorrando(false) }} />
      )}
    </div>
  )
}

/* ---------- Calificar una transición ----------
   Pensado para usarlo con el tema sonando: un clic en OK o Regular ya guarda. «Mala» necesita
   el motivo, así que abre el campo con el foco puesto y guarda al apretar Enter. Mientras
   tanto la calificación guardada sigue siendo la anterior, y se dice. */
export function Calificar({ setId, t, desdeTitulo, hastaTitulo, onGuardar, onQuitar }) {
  const guardada = t.calificacion                       // lo que dice la API
  const [elegida, setElegida] = useState(null)          // una elección todavía sin guardar (mala sin motivo)
  const [motivo, setMotivo] = useState(t.motivo || '')
  const [conMotivo, setConMotivo] = useState(false)     // «Agregar motivo» en ok / regular
  const [ocupado, setOcupado] = useState(false)
  const [error, setError] = useState('')
  const motivoRef = useRef(null)
  // Pedido de foco al campo de motivo: se cumple en un efecto, cuando el campo ya se dibujó.
  const [focoMotivo, setFocoMotivo] = useState(0)
  useEffect(() => { if (focoMotivo) motivoRef.current?.focus() }, [focoMotivo])
  const sel = elegida ?? guardada
  const id = `rcal-${setId}-${t.n}`
  const verMotivo = sel === 'mala' || conMotivo || !!t.motivo

  // Cuando la API cambia lo guardado (otra calificación, «Quitar»), el campo muestra lo guardado.
  useEffect(() => { setMotivo(t.motivo || '') }, [t.motivo, t.calificacion])

  const guardar = async (nivel, texto) => {
    setOcupado(true)
    setError('')
    const r = await onGuardar(t.n, nivel, texto)
    setOcupado(false)
    if (r.ok) {
      setElegida(null)
      setConMotivo(false)
    } else {
      setError(r.error)
    }
    return r.ok
  }

  const elegir = (nivel) => {
    if (ocupado) return
    setError('')
    const texto = motivo.trim() ? motivo : null
    if (nivel === 'mala' && !texto) {
      // Sin motivo no se manda: la API lo rechazaría, y lo que se quiere es escribirlo.
      setElegida('mala')
      setFocoMotivo((x) => x + 1)
      return
    }
    setElegida(nivel)
    guardar(nivel, texto)
  }

  const enviarMotivo = (e) => {
    e.preventDefault()
    if (ocupado || !sel) return
    // Se manda también vacío: si es «mala», el motivo por el que no se guarda lo escribe la API.
    guardar(sel, motivo)
  }

  const quitar = async () => {
    if (ocupado) return
    setOcupado(true)
    setError('')
    const r = await onQuitar(t.n)
    setOcupado(false)
    if (r.ok) { setElegida(null); setConMotivo(false) } else setError(r.error)
  }

  const pendienteMala = elegida === 'mala' && guardada !== 'mala'
  return (
    <div className={`rcal${sel ? ` is-${sel}` : ''}`} data-n={t.n}>
      <div className="rcal-fila">
        <div className="seg rcal-seg" role="radiogroup"
          aria-label={`Calificación de la transición ${t.desde} → ${t.hasta} (${desdeTitulo} → ${hastaTitulo})`}>
          {NIVELES.map((n) => (
            <label key={n.k} className={`seg-opt rcal-opt is-${n.k}`}>
              <input type="radio" name={id} value={n.k} checked={sel === n.k}
                onChange={() => elegir(n.k)} />
              <b>{n.label}</b>
            </label>
          ))}
        </div>
        {guardada && (
          <button type="button" className="btn btn-ghost btn-sm rcal-quitar" onClick={quitar}
            aria-label={`Quitar la calificación de la transición ${t.desde} → ${t.hasta}`}>Quitar</button>
        )}
        {sel && sel !== 'mala' && !verMotivo && (
          <button type="button" className="btn btn-ghost btn-sm rcal-mas"
            onClick={() => { setConMotivo(true); setFocoMotivo((x) => x + 1) }}>
            Agregar motivo
          </button>
        )}
        {/* Para la vista: el lector de pantalla ya oye el radio marcado y el aviso de la
            región viva de la pantalla al guardar. */}
        <span className="rcal-estado" aria-hidden="true">
          {ocupado ? 'Guardando…' : guardada ? 'guardada' : 'sin calificar'}
        </span>
      </div>
      {verMotivo && sel && (
        <form className="rcal-motivo" onSubmit={enviarMotivo}>
          <label className="sr-only" htmlFor={`${id}-motivo`}>
            {sel === 'mala' ? 'Motivo (obligatorio): qué sonó mal' : 'Motivo (opcional)'} en la transición {t.desde} → {t.hasta}
          </label>
          <input className="input" id={`${id}-motivo`} ref={motivoRef} value={motivo} maxLength={2000} autoComplete="off"
            onChange={(e) => setMotivo(e.target.value)} aria-describedby={pendienteMala ? `${id}-falta` : undefined}
            placeholder={sel === 'mala' ? 'Qué sonó mal (obligatorio)' : 'Motivo (opcional)'} />
          <button type="submit" className="btn btn-secondary btn-sm">Guardar motivo</button>
        </form>
      )}
      {pendienteMala && (
        <p className="rnota rcal-falta" id={`${id}-falta`}>
          «Mala» se guarda con el motivo: escribí qué sonó mal y apretá Enter.
          {guardada ? ` Mientras tanto sigue guardada como «${guardada}».` : ' Mientras tanto sigue sin calificar.'}
        </p>
      )}
      {error && <p className="rnota rnota-err rcal-error" role="alert">No se guardó: {error}</p>}
    </div>
  )
}
