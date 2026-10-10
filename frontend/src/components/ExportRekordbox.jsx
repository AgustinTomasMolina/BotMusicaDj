import { useEffect, useRef, useState } from 'react'
import { useDialog } from '../hooks'
import { resumenRekordbox, exportarRekordbox } from '../api'
import { motivoDe } from '../playlistImport'
import { resumenExport, textoExportado } from '../exportRekordbox'

/* ============================================================================
   «Exportar a Rekordbox (con cues)» (f56, tablero F, acción 3).
   Antes de bajar nada dice qué va (temas y marcas) y qué queda afuera con su motivo (lo pide
   el server: GET /api/playlists/{id}/rekordbox). La grilla de beats estimada NO va salvo que
   el dueño la tilde, con el aviso a la vista. El XML lleva la ruta completa de cada tema (es lo
   que Rekordbox necesita): la pantalla lo avisa, aunque ella nunca muestra rutas.
   ========================================================================== */
export default function ExportRekordbox({ crate, onClose, toast }) {
  const [resumen, setResumen] = useState(null)
  const [error, setError] = useState(null)
  const [grilla, setGrilla] = useState(false)
  const [busy, setBusy] = useState(false)
  const [hecho, setHecho] = useState(null)
  const ref = useRef(null)
  useDialog(ref, true, onClose)

  useEffect(() => {
    let vivo = true
    resumenRekordbox(crate.id).then((r) => {
      if (!vivo) return
      if (r.ok) setResumen(r.data)
      else setError(motivoDe(r))
    }).catch(() => { if (vivo) setError('No pude conectar con el servidor. Revisá que esté corriendo.') })
    return () => { vivo = false }
  }, [crate.id])

  const v = resumenExport(resumen)
  const descargar = async () => {
    if (!v?.puede || busy) return
    setBusy(true); setError(null)
    try {
      const r = await exportarRekordbox(crate.id, grilla)
      if (!r.ok) { setError(motivoDe(r)); return }
      // Descarga real: un <a download> con el blob y el nombre que eligió el server.
      const url = URL.createObjectURL(r.blob)
      const a = document.createElement('a')
      a.href = url
      a.download = r.nombre
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      const txt = textoExportado(r.nombre, r.cuenta, grilla)
      setHecho(txt)
      toast?.ok({ title: 'XML para Rekordbox descargado', body: txt })
    } catch { setError('No pude conectar con el servidor.') } finally { setBusy(false) }
  }

  return (
    <div className="dialog-backdrop" onClick={(e) => { if (e.target === e.currentTarget && !busy) onClose() }}>
      <div className="dialog rbx-dialog" ref={ref} role="dialog" aria-modal="true" aria-labelledby="rbx-titulo" tabIndex={-1}>
        <div className="cluster" style={{ flexWrap: 'nowrap' }}>
          <div>
            <div className="eyebrow">Exportar a Rekordbox (con cues)</div>
            <div className="dialog-title" id="rbx-titulo" style={{ marginTop: 2 }}>{crate.nombre}</div>
          </div>
          <button type="button" className="btn btn-icon btn-icon-sm push" aria-label="Cerrar" onClick={onClose}>✕</button>
        </div>
        {!resumen && !error && <p className="rbx-cargando" role="status"><span className="spinner" aria-hidden="true" /> Mirando qué se puede exportar…</p>}
        {error && <p className="imp-error" role="alert">{error}</p>}
        {v && (
          <>
            <dl className="kv rbx-kv">
              <dt>Temas</dt><dd className="rbx-temas">{v.temas}</dd>
              <dt>Marcas</dt><dd className="rbx-marcas">{v.marcas}</dd>
              <dt>Archivo</dt><dd className="rbx-archivo">{resumen.archivo}</dd>
            </dl>
            {v.avisos.length > 0 && (
              <ul className="rbx-avisos" aria-label="Avisos">
                {v.avisos.map((t) => <li key={t}>{t}</li>)}
              </ul>
            )}
            {v.omitidos.length > 0 && (
              <div className="rbx-omitidos">
                <p className="rbx-sub" id="rbx-omitidos-t">{v.omitidos.length === 1 ? 'Queda afuera 1 tema' : `Quedan afuera ${v.omitidos.length} temas`}:</p>
                <ul aria-labelledby="rbx-omitidos-t">
                  {v.omitidos.map((o) => <li key={o.id}>{o.texto}</li>)}
                </ul>
              </div>
            )}
            <p className="rbx-nota rbx-rutas" role="note">{resumen.aviso_rutas}</p>
            <label className="imp-check rbx-grilla">
              <input type="checkbox" checked={grilla} onChange={(e) => setGrilla(e.target.checked)} aria-describedby="rbx-grilla-d" />
              <span>Incluir la grilla de beats estimada (experimental)</span>
            </label>
            <p className={`rbx-nota${grilla ? ' is-warn' : ''}`} id="rbx-grilla-d">{grilla ? resumen.aviso_grilla : 'Sin tildar, el XML no lleva grilla: Rekordbox analiza la suya.'}</p>
            {hecho && (
              <div className="rbx-hecho" role="status">
                <p><b>Descargado:</b> {hecho}</p>
                <p>En Rekordbox: <b>Preferencias › Avanzado › rekordbox xml</b>, elegí este archivo; después, en el árbol <b>rekordbox xml › Playlists</b>, arrastrá la playlist a tu colección.</p>
              </div>
            )}
            <div className="dialog-actions">
              <button type="button" className="btn btn-secondary" onClick={onClose}>Cerrar</button>
              <button type="button" className="btn btn-primary rbx-descargar" onClick={descargar}
                aria-disabled={!v.puede || busy ? 'true' : undefined}>
                {busy ? <span className="spinner" aria-hidden="true" /> : null}{v.boton}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
