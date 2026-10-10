import { useEffect, useRef, useState } from 'react'
import { useDialog } from '../hooks'
import {
  leerXmlRekordbox, leerXmlConfigurado, importarRekordbox, listarCarpetas, importarCarpeta,
  analizarPlaylist, avisarPlaylists,
} from '../api'
import { textoEncontrados, detalleFaltantes, motivoDe } from '../playlistImport'
import { textoCuesImportados, textoCuesXml } from '../exportRekordbox'

/* ============================================================================
   Diálogo «Importar…» (f53, tablero G del canvas): dos pestañas.
   - «Desde Rekordbox»: elegir el XML (o el configurado en el server), ver cuántos temas y
     playlists trae, tildar playlists. Cada una dice «N de M encontrados» y por qué faltan los
     otros (nombre repetido, no está en tus carpetas, no está en la colección): no se esconden.
   - «Desde una carpeta de la PC»: navegar las carpetas de música permitidas (por su nombre,
     nunca la ruta de la PC) con cuántos audios tiene cada una, e importar una.
   Después de importar: se abre la playlist y arranca su análisis con el motor.
   ========================================================================== */

const PESTANAS = [{ k: 'rekordbox', label: 'Desde Rekordbox' }, { k: 'carpeta', label: 'Desde una carpeta de la PC' }]

function PestanaRekordbox({ onListo, ocupado, setOcupado }) {
  const [lectura, setLectura] = useState(null)        // respuesta de /leer
  const [error, setError] = useState(null)
  const [elegidas, setElegidas] = useState(() => new Set())
  const [actualizar, setActualizar] = useState(false)
  const [pisarCues, setPisarCues] = useState(false)    // f56: por defecto ganan los de la página
  const [resultado, setResultado] = useState(null)

  const leer = async (pedido) => {
    setOcupado(true); setError(null); setResultado(null)
    try {
      const r = await pedido()
      if (!r.ok) { setError(motivoDe(r)); setLectura(null); return }
      setLectura(r.data)
      // Por defecto, las que tienen algo encontrado y no están importadas.
      setElegidas(new Set(r.data.playlists.filter((p) => p.encontrados > 0 && !p.ya_importada).map((p) => p.id)))
    } catch { setError('No pude conectar con el servidor. Revisá que esté corriendo.') } finally { setOcupado(false) }
  }
  const alElegir = (e) => { const f = e.target.files?.[0]; if (f) leer(() => leerXmlRekordbox(f)) }
  const conmutar = (id) => setElegidas((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const hayYa = lectura && lectura.playlists.some((p) => elegidas.has(p.id) && p.ya_importada)

  const importar = async () => {
    setOcupado(true); setError(null)
    try {
      const r = await importarRekordbox(lectura.token, [...elegidas], actualizar, actualizar && pisarCues)
      if (!r.ok) { setError(motivoDe(r)); return }
      setResultado(r.data.resultados)
      const hechas = r.data.resultados.filter((x) => x.estado === 'creada' || x.estado === 'actualizada')
      if (hechas.length) onListo(hechas.map((x) => x.id), r.data.resultados)
    } catch { setError('No pude conectar con el servidor.') } finally { setOcupado(false) }
  }

  return (
    <div className="imp-panel">
      <p className="imp-ayuda">En Rekordbox: <b>Archivo › Exportar colección en formato xml</b>. Elegí ese archivo acá: se leen sus playlists y se buscan los temas en tus carpetas de música. Los audios no se tocan.</p>
      <div className="imp-fila">
        <label className="btn btn-secondary btn-sm imp-file">
          Elegir el XML…
          <input type="file" accept=".xml,application/xml,text/xml" onChange={alElegir} disabled={ocupado} />
        </label>
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => leer(leerXmlConfigurado)} disabled={ocupado}>Usar el XML configurado</button>
      </div>
      {error && <p className="imp-error" role="alert">{error}</p>}
      {lectura && (
        <>
          <p className="imp-resumen" role="status">
            <b>{lectura.archivo}</b>: {lectura.temas} tema{lectura.temas === 1 ? '' : 's'} · {lectura.playlists.length} playlist{lectura.playlists.length === 1 ? '' : 's'}
          </p>
          {lectura.motivo_raices && <p className="imp-error" role="alert">{lectura.motivo_raices}</p>}
          {lectura.playlists.length === 0 && <p className="imp-ayuda">Este XML no trae playlists.</p>}
          <ul className="imp-lista" aria-label="Playlists del XML">
            {lectura.playlists.map((p) => {
              const faltan = detalleFaltantes(p)
              const idDet = `imp-det-${p.id}`
              return (
                <li key={p.id} className="imp-pl">
                  <label className="imp-check">
                    <input type="checkbox" checked={elegidas.has(p.id)} onChange={() => conmutar(p.id)} aria-describedby={idDet} />
                    <span className="imp-pl-txt">
                      <span className="imp-pl-nombre">{p.nombre}</span>
                      {p.carpeta && <span className="imp-pl-carpeta">{p.carpeta}</span>}
                    </span>
                    <span className={`imp-pl-n${p.encontrados < p.total ? ' is-warn' : ''}`}>{textoEncontrados(p)}</span>
                  </label>
                  <div id={idDet} className="imp-pl-det">
                    {textoCuesXml(p) && <span className="imp-pl-cues">Trae {textoCuesXml(p)} de Rekordbox</span>}
                    {faltan.map((t) => <span key={t}>{t}</span>)}
                    {p.ya_importada && <span>Ya importada como «{p.ya_importada.nombre}»</span>}
                  </div>
                </li>
              )
            })}
          </ul>
          {hayYa && (
            <label className="imp-check imp-actualizar">
              <input type="checkbox" checked={actualizar} onChange={(e) => setActualizar(e.target.checked)} />
              <span>Actualizar las que ya importé (suma los temas nuevos; no borra nada ni pisa el género que cambiaste)</span>
            </label>
          )}
          {hayYa && actualizar && (
            <label className="imp-check imp-actualizar imp-pisar">
              <input type="checkbox" checked={pisarCues} onChange={(e) => setPisarCues(e.target.checked)} aria-describedby="imp-pisar-d" />
              <span>Pisar mis cues con los de Rekordbox</span>
            </label>
          )}
          {hayYa && actualizar && (
            <p className="imp-ayuda" id="imp-pisar-d">{pisarCues
              ? 'Si un pad tiene un cue distinto en la página y en Rekordbox, queda el de Rekordbox. Las demás marcas de la página no se borran.'
              : 'Sin tildar, los cues que marcaste en la página ganan: de Rekordbox solo se suman los que no están.'}</p>
          )}
          {resultado && (
            <ul className="imp-resultado" aria-label="Resultado">
              {resultado.map((x) => (
                <li key={x.playlist}>{x.ruta}: {x.estado === 'creada' ? `importada (${x.temas} temas)`
                  : x.estado === 'actualizada' ? `actualizada (${x.agregados} nuevos)`
                    : x.estado === 'ya-importada' ? 'ya estaba importada: tildá «Actualizar» para sumar lo nuevo' : x.mensaje}
                {textoCuesImportados(x.cues) && <span className="imp-cues">{textoCuesImportados(x.cues)}</span>}</li>
              ))}
            </ul>
          )}
          <div className="dialog-actions">
            <button type="button" className="btn btn-primary" onClick={importar} disabled={ocupado || elegidas.size === 0}>
              {ocupado ? <span className="spinner" aria-hidden="true" /> : null}
              Importar {elegidas.size} playlist{elegidas.size === 1 ? '' : 's'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

function PestanaCarpeta({ onListo, ocupado, setOcupado }) {
  const [raices, setRaices] = useState(null)
  const [motivo, setMotivo] = useState(null)
  const [aca, setAca] = useState(null)                // {raiz, ruta, nombre, audios, subcarpetas}
  const [error, setError] = useState(null)
  const [recursivo, setRecursivo] = useState(false)
  const [ya, setYa] = useState(null)

  useEffect(() => {
    listarCarpetas().then((r) => {
      if (!r.ok) { setError(motivoDe(r)); return }
      setRaices(r.data.raices); setMotivo(r.data.motivo)
    }).catch(() => setError('No pude conectar con el servidor.'))
  }, [])

  const abrir = async (raiz, ruta) => {
    setOcupado(true); setError(null); setYa(null)
    try {
      const r = await listarCarpetas(raiz, ruta)
      if (!r.ok) { setError(motivoDe(r)); return }
      setAca({ ...r.data, raizId: raiz })
    } catch { setError('No pude conectar con el servidor.') } finally { setOcupado(false) }
  }
  const subir = () => {
    if (!aca) return
    const partes = aca.ruta ? aca.ruta.split('/') : []
    if (!partes.length) { setAca(null); return }
    abrir(aca.raizId, partes.slice(0, -1).join('/'))
  }
  const importar = async (actualizar = false) => {
    setOcupado(true); setError(null)
    try {
      const r = await importarCarpeta(aca.raizId, aca.ruta, recursivo, actualizar)
      if (!r.ok) { setError(motivoDe(r)); return }
      if (r.data.estado === 'ya-importada') { setYa(r.data); return }
      onListo([r.data.id], [r.data])
    } catch { setError('No pude conectar con el servidor.') } finally { setOcupado(false) }
  }

  return (
    <div className="imp-panel">
      <p className="imp-ayuda">Elegí una carpeta: sus audios se vuelven una playlist en orden alfabético, con título, artista y género de los tags (si el tag no trae género, queda sin género). Los audios no se tocan.</p>
      {error && <p className="imp-error" role="alert">{error}</p>}
      {motivo && <p className="imp-error" role="alert">{motivo}</p>}
      {!aca && raices && raices.length > 0 && (
        <ul className="imp-lista" aria-label="Tus carpetas de música">
          {raices.map((r) => (
            <li key={r.id}><button type="button" className="imp-carpeta" onClick={() => abrir(r.id, '')} disabled={!r.disponible || ocupado}>
              <span className="imp-pl-nombre">{r.nombre}</span>
              <span className="imp-pl-n">{r.disponible ? 'abrir' : 'no disponible'}</span>
            </button></li>
          ))}
        </ul>
      )}
      {aca && (
        <>
          <div className="imp-fila">
            <button type="button" className="btn btn-secondary btn-sm" onClick={subir} disabled={ocupado}>← Subir</button>
            <span className="imp-donde" aria-live="polite">{aca.raiz.nombre}{aca.ruta ? ` / ${aca.ruta.split('/').join(' / ')}` : ''}</span>
          </div>
          <p className="imp-resumen" role="status">{aca.audios} audio{aca.audios === 1 ? '' : 's'} en esta carpeta · {aca.subcarpetas.length} subcarpeta{aca.subcarpetas.length === 1 ? '' : 's'}</p>
          {aca.subcarpetas.length > 0 && (
            <ul className="imp-lista" aria-label="Subcarpetas">
              {aca.subcarpetas.map((s) => (
                <li key={s.nombre}><button type="button" className="imp-carpeta" disabled={ocupado}
                  onClick={() => abrir(aca.raizId, aca.ruta ? `${aca.ruta}/${s.nombre}` : s.nombre)}>
                  <span className="imp-pl-nombre">{s.nombre}</span>
                  <span className="imp-pl-n">{s.audios} audio{s.audios === 1 ? '' : 's'}{s.subcarpetas ? ` · ${s.subcarpetas} subcarpeta${s.subcarpetas === 1 ? '' : 's'}` : ''}</span>
                </button></li>
              ))}
            </ul>
          )}
          <label className="imp-check imp-actualizar">
            <input type="checkbox" checked={recursivo} onChange={(e) => setRecursivo(e.target.checked)} />
            <span>Incluir las subcarpetas</span>
          </label>
          {ya && (
            <p className="imp-error" role="alert">Esta carpeta ya está importada como «{ya.nombre}».{' '}
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => importar(true)} disabled={ocupado}>Actualizarla (suma los temas nuevos)</button></p>
          )}
          <div className="dialog-actions">
            <button type="button" className="btn btn-primary" onClick={() => importar(false)} disabled={ocupado || (!aca.audios && !recursivo)}>
              {ocupado ? <span className="spinner" aria-hidden="true" /> : null}
              Importar «{aca.ruta ? aca.ruta.split('/').pop() : aca.raiz.nombre}»
            </button>
          </div>
        </>
      )}
    </div>
  )
}

export default function ImportarDialog({ onClose, onImportada, toast }) {
  const [tab, setTab] = useState('rekordbox')
  const [ocupado, setOcupado] = useState(false)
  const ref = useRef(null)
  const tabsRef = useRef(null)
  useDialog(ref, true, onClose)

  const teclaTabs = (e) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
    e.preventDefault()
    const i = PESTANAS.findIndex((p) => p.k === tab)
    const j = (i + (e.key === 'ArrowRight' ? 1 : PESTANAS.length - 1)) % PESTANAS.length
    setTab(PESTANAS[j].k)
    tabsRef.current?.querySelectorAll('[role=tab]')[j]?.focus()
  }

  // Después de importar: arranca el análisis de cada una y se abre la primera.
  const listo = async (ids, resultados = []) => {
    avisarPlaylists()
    for (const id of ids) { try { await analizarPlaylist(id) } catch { /* la pantalla lo reintenta */ } }
    // f56: lo que pasó con los cues de Rekordbox va en el aviso (el diálogo se cierra).
    const cues = resultados.map((x) => textoCuesImportados(x.cues) && `${x.ruta}: ${textoCuesImportados(x.cues)}`).filter(Boolean)
    const conError = resultados.some((x) => x.cues?.error)
    const t = conError ? toast?.warn : toast?.ok
    t?.({ title: ids.length === 1 ? 'Playlist importada' : `${ids.length} playlists importadas`,
      body: ['Ya arrancó el análisis con el motor.', ...cues].join(' ') })
    onImportada?.(ids[0])
  }

  return (
    <div className="dialog-backdrop" onClick={(e) => { if (e.target === e.currentTarget && !ocupado) onClose() }}>
      <div className="dialog imp-dialog" ref={ref} role="dialog" aria-modal="true" aria-labelledby="imp-titulo" tabIndex={-1}>
        <div className="cluster" style={{ flexWrap: 'nowrap' }}>
          <div className="dialog-title" id="imp-titulo">Importar playlists</div>
          <button type="button" className="btn btn-icon btn-icon-sm push" aria-label="Cerrar" onClick={onClose}>✕</button>
        </div>
        {/* El foco arranca en la primera pestaña (no en «Cerrar»): lo primero es elegir de dónde. */}
        <div className="rdet-tabs imp-tabs" role="tablist" aria-label="De dónde importar" ref={tabsRef} onKeyDown={teclaTabs}>
          {PESTANAS.map((p) => (
            <button key={p.k} type="button" role="tab" id={`imp-tab-${p.k}`} className={`rtab${tab === p.k ? ' is-on' : ''}`}
              aria-selected={tab === p.k} aria-controls={`imp-panel-${p.k}`} tabIndex={tab === p.k ? 0 : -1}
              data-autofocus={p.k === PESTANAS[0].k ? true : undefined}
              onClick={() => setTab(p.k)}>{p.label}</button>
          ))}
        </div>
        <div role="tabpanel" id="imp-panel-rekordbox" aria-labelledby="imp-tab-rekordbox" hidden={tab !== 'rekordbox'}>
          <PestanaRekordbox onListo={listo} ocupado={ocupado} setOcupado={setOcupado} />
        </div>
        <div role="tabpanel" id="imp-panel-carpeta" aria-labelledby="imp-tab-carpeta" hidden={tab !== 'carpeta'}>
          {tab === 'carpeta' && <PestanaCarpeta onListo={listo} ocupado={ocupado} setOcupado={setOcupado} />}
        </div>
      </div>
    </div>
  )
}
