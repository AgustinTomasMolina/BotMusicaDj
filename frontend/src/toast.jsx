import { createContext, useCallback, useContext, useMemo, useState } from 'react'

/* Sistema de toasts (reemplaza los alert()). Abajo a la derecha, máx 3 a la vez.
   ok/warn/info se van solos a los 5s (la barra .toast-life muestra el tiempo);
   danger se queda hasta que lo cerrás, porque suele llevar una acción. */

const ToastCtx = createContext(null)
export const useToast = () => useContext(ToastCtx)

let _id = 0

const S = (p) => <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{p}</svg>
const ICON = {
  ok: S(<path d="M5 13l4.5 4.5L19 7" />),
  warn: S(<><path d="M12 4l9 16H3z" /><path d="M12 10v4.5" /><circle cx="12" cy="17.4" r=".9" fill="currentColor" stroke="none" /></>),
  danger: S(<><circle cx="12" cy="12" r="8" /><path d="M9.2 9.2l5.6 5.6M14.8 9.2l-5.6 5.6" /></>),
  info: S(<path d="M4 7h16M4 12h16M4 17h10" />),
}
const IcoX = () => <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>

export function ToastProvider({ children }) {
  const [items, setItems] = useState([])
  const remove = useCallback((id) => setItems((xs) => xs.filter((t) => t.id !== id)), [])
  const push = useCallback((role, opts) => {
    const id = ++_id
    setItems((xs) => [...xs, { id, role, ...opts }].slice(-3)) // máx 3, el más nuevo abajo
    if (role !== 'danger') setTimeout(() => remove(id), 5000)
    return id
  }, [remove])

  const api = useMemo(() => ({
    ok: (o) => push('ok', o), warn: (o) => push('warn', o),
    danger: (o) => push('danger', o), info: (o) => push('info', o), remove,
  }), [push, remove])

  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div className="toasts">
        {items.map((t) => (
          <div key={t.id} className={`toast toast-${t.role}`}>
            {ICON[t.role]}
            <div style={{ flex: 1, minWidth: 0 }}>
              {t.title && <div className="toast-title">{t.title}</div>}
              {t.body && <p className="toast-body">{t.body}</p>}
              {t.actions && t.actions.length > 0 && (
                <div className="cluster" style={{ gap: 'var(--space-2)', marginTop: 'var(--space-2)' }}>
                  {t.actions.map((a, i) => (
                    <button key={i} type="button" className="btn btn-ghost btn-sm"
                      onClick={() => { try { a.onClick?.() } finally { remove(t.id) } }}>{a.label}</button>
                  ))}
                </div>
              )}
            </div>
            <button type="button" className="btn btn-icon-sm" aria-label="Cerrar" onClick={() => remove(t.id)}><IcoX /></button>
            {t.role !== 'danger' && <div className="toast-life"><i /></div>}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}
