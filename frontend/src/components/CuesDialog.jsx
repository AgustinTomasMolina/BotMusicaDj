import { useRef, useState } from 'react'
import { useDialog } from '../hooks'
import CueEditor from './CueEditor'

/* Diálogo «Marcar cues» de una playlist (f53, acción 1 del tablero F): el editor de cues que
   ya existe (CueEditor, el de la radio) sobre los temas de la playlist que el motor ya
   analizó. Se puede pasar de un tema a otro sin cerrar. El editor trabaja con el id de la
   radio (`analisis.radio_id`): las marcas son las mismas que ve la pantalla de Radio DJ. */
export default function CuesDialog({ temas, inicial, onClose }) {
  const [id, setId] = useState(inicial ?? temas[0]?.analisis?.radio_id)
  const ref = useRef(null)
  useDialog(ref, true, onClose)
  const i = Math.max(0, temas.findIndex((t) => t.analisis?.radio_id === id))
  const tema = temas[i]
  const etiqueta = (t) => `${t.artista ? `${t.artista} — ` : ''}${t.titulo}`
  return (
    <div className="dialog-backdrop" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="dialog cues-dialog" ref={ref} role="dialog" aria-modal="true" aria-labelledby="cues-dlg-t" tabIndex={-1}>
        <div className="cluster" style={{ flexWrap: 'nowrap' }}>
          <div style={{ minWidth: 0 }}>
            <div className="eyebrow">Marcar cues</div>
            <div className="dialog-title truncate" id="cues-dlg-t">{tema ? etiqueta(tema) : 'Sin temas analizados'}</div>
          </div>
          <button type="button" className="btn btn-icon btn-icon-sm push" aria-label="Cerrar el editor de cues" onClick={onClose} data-autofocus>✕</button>
        </div>
        {temas.length > 1 && (
          <div className="cluster cues-dlg-nav">
            <button type="button" className="btn btn-secondary btn-sm" disabled={i === 0} onClick={() => setId(temas[i - 1].analisis.radio_id)}>← Anterior</button>
            <label className="cues-dlg-sel">
              <span className="sr-only">Tema</span>
              <select className="input" value={id} onChange={(e) => setId(e.target.value)}>
                {temas.map((t, k) => <option key={t.id} value={t.analisis.radio_id}>{k + 1}. {etiqueta(t)}</option>)}
              </select>
            </label>
            <button type="button" className="btn btn-secondary btn-sm" disabled={i === temas.length - 1} onClick={() => setId(temas[i + 1].analisis.radio_id)}>Siguiente →</button>
          </div>
        )}
        {tema && <CueEditor key={id} trackId={id} titulo={tema.titulo} />}
      </div>
    </div>
  )
}
