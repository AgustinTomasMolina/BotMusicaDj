import { useCallback, useEffect, useRef, useState } from 'react'
import { metaKey, previewable } from './utils'
import { fetchMeta } from './api'

/* ---------- Consola en vivo (WebSocket) ---------- */
// Mismo origen que la app: en dev Vite proxya /ws → :8000 (ver vite.config.js);
// en prod FastAPI sirve el build y el WS en el mismo host.
export function useConsole() {
  const [connected, setConnected] = useState(false)
  const [lines, setLines] = useState([])
  const wsRef = useRef(null)
  const stopped = useRef(false)

  useEffect(() => {
    stopped.current = false
    const addLine = (msg, level) =>
      setLines((prev) => {
        const next = [...prev, { msg, level: level || 'INFO' }]
        return next.length > 500 ? next.slice(next.length - 500) : next
      })

    function connect() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${location.host}/ws/console`)
      wsRef.current = ws
      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        if (!stopped.current) {
          addLine('🔴 Consola desconectada, reintentando…', 'WARNING')
          setTimeout(connect, 1500)
        }
      }
      ws.onmessage = (e) => {
        try { const d = JSON.parse(e.data); addLine(d.msg, d.level) } catch { /* ignore */ }
      }
    }
    connect()
    return () => { stopped.current = true; try { wsRef.current && wsRef.current.close() } catch { /* ignore */ } }
  }, [])

  return { connected, lines }
}

/* ---------- Preview al pasar el mouse (estilo Netflix) ---------- */
const PREVIEW_DELAY = 450
export function usePreview(enabled, blocked) {
  const [current, setCurrent] = useState(null) // { key, song }
  const timer = useRef(null)
  const curRef = useRef(null)
  curRef.current = current

  const cancel = useCallback(() => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null }
  }, [])
  const stop = useCallback(() => { cancel(); setCurrent(null) }, [cancel])
  const schedule = useCallback((key, song) => {
    if (!enabled || blocked) return
    if (curRef.current && curRef.current.key === key) return // ya sonando esa misma
    if (!previewable(song)) return                            // Spotify sin audio → solo zoom
    cancel()
    timer.current = setTimeout(() => setCurrent({ key, song }), PREVIEW_DELAY)
  }, [enabled, blocked, cancel])

  // Al deshabilitar el preview o abrir el modal, cortar lo que suene.
  useEffect(() => { if (blocked || !enabled) { cancel(); setCurrent(null) } }, [blocked, enabled, cancel])

  return { current, schedule, cancel, stop }
}

/* ---------- Metadatos lazy (BPM/género) ---------- */
// Devuelve un mapa metaKey → { bpm, genero, done } que las badges consultan.
export function useMeta() {
  const [metaMap, setMetaMap] = useState({})
  const seen = useRef(new Set())

  const enrich = useCallback(async (songs) => {
    const pend = {}
    for (const c of songs || []) {
      if (!c) continue
      if (c.bpm && c.genero) continue // ya viene completo
      const k = metaKey(c)
      if (seen.current.has(k)) continue
      seen.current.add(k)
      pend[k] = c
    }
    const keys = Object.keys(pend)
    if (!keys.length) return
    let idx = 0
    const worker = async () => {
      while (idx < keys.length) {
        const k = keys[idx++]
        const c = pend[k]
        let d = { bpm: null, genero: null }
        try { d = await fetchMeta(c.titulo, c.artista) } catch { /* ignore */ }
        setMetaMap((prev) => ({ ...prev, [k]: { bpm: d.bpm || null, genero: d.genero || null, done: true } }))
      }
    }
    await Promise.all(Array.from({ length: 8 }, worker))
  }, [])

  return { metaMap, enrich }
}
