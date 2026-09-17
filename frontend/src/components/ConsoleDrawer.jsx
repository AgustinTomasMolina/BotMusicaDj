import { useEffect, useRef } from 'react'
import { useDialog } from '../hooks'

const IcoX = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
)

/* Consola en vivo (WebSocket) — cajón derecho, design system Nocturne (.drawer/.log). */
export default function ConsoleDrawer({ open, onClose, connected, lines }) {
  const ref = useRef(null)
  const drawerRef = useRef(null)
  useDialog(drawerRef, open, onClose)
  // Autoscroll al fondo cuando llegan líneas nuevas.
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight }, [lines])
  const cls = (lvl) => (lvl === 'ERROR' ? 'log-err' : lvl === 'WARNING' ? 'log-warn' : '')
  return (
    // Cerrado queda fuera de pantalla: inert lo saca del orden de Tab y del lector.
    <div ref={drawerRef} className={`drawer drawer-right${open ? ' is-open' : ''}`}
      role="dialog" aria-modal="true" aria-label="Consola en vivo" inert={!open}>
      <div className="drawer-head rule-b">
        <span className="eyebrow">Consola en vivo</span>
        <span className="cluster push" style={{ gap: 6 }}>
          {connected && <span className="spinner" aria-hidden="true" />}
          <span className="mono" style={{ fontSize: 11, color: 'var(--color-neutral-500)' }}>{connected ? 'escuchando' : 'desconectado'}</span>
        </span>
        <button type="button" className="btn btn-icon btn-icon-sm" aria-label="Cerrar consola" onClick={onClose} data-autofocus><IcoX /></button>
      </div>
      <div className="drawer-body" ref={ref}>
        <div className="log">
          {lines.map((l, i) => (
            <div className="log-line" key={i}><span className={cls(l.level)}>{l.msg}</span></div>
          ))}
        </div>
      </div>
    </div>
  )
}
