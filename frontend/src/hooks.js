import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { metaKey, previewable } from './utils'
import { fetchMeta } from './api'

/* ---------- Diálogos y cajones: foco accesible ----------
   Mientras `open`: mueve el foco adentro (al [data-autofocus] o al primer control), Escape
   llama a onClose y Tab no se escapa al fondo (Escape siempre saca: no es una trampa).
   Al cerrar devuelve el foco a lo que lo tenía antes de abrir (el disparador). */
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),' +
  'textarea:not([disabled]),iframe:not([tabindex="-1"]),[tabindex]:not([tabindex="-1"])'
export function useDialog(ref, open, onClose) {
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  useEffect(() => {
    if (!open) return
    const prev = document.activeElement
    const node = ref.current
    const focusables = () => (node ? [...node.querySelectorAll(FOCUSABLE)].filter((el) => el.getClientRects().length) : [])
    const inicial = node && (node.querySelector('[data-autofocus]') || focusables()[0] || node)
    if (inicial) inicial.focus({ preventScroll: true })
    const onKey = (e) => {
      if (e.key === 'Escape') { onCloseRef.current?.(); return }
      if (e.key !== 'Tab' || !node) return
      const f = focusables()
      if (!f.length) { e.preventDefault(); return }
      const primero = f[0], ultimo = f[f.length - 1]
      if (!node.contains(document.activeElement)) { e.preventDefault(); primero.focus() }
      else if (e.shiftKey && document.activeElement === primero) { e.preventDefault(); ultimo.focus() }
      else if (!e.shiftKey && document.activeElement === ultimo) { e.preventDefault(); primero.focus() }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      if (prev && prev.isConnected && typeof prev.focus === 'function') prev.focus({ preventScroll: true })
    }
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps
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

/* ---------- El volumen de la app en un <audio> propio (f50) ----------
   La barra de abajo aplica el volumen en su motor; una pantalla que tiene su PROPIO <audio>
   (la radio, el editor de cues) lo aplica con esto: el mismo número y el mismo silencio que
   la barra, así hay un solo volumen en toda la app. */
export function useVolumenEn(ref, volume, muted) {
  useEffect(() => {
    const a = ref.current
    if (!a) return
    a.volume = Math.max(0, Math.min(1, Number(volume) || 0))
    a.muted = !!muted
  })
}

/* ---------- El volumen de la app en un preview o reproductor embebido (f50) ----------
   El preview del mouse y el reproductor del modal iban a un volumen fijo (55 % y 50 %): con
   la app al 10 % el preview sonaba al 55 %. Ahora siguen el de la app (`volumenPreview` en
   src/volumen.js): el <audio> directo, y YouTube / SoundCloud por su API de mensajes, al
   cargar el iframe y cada vez que el volumen cambia. */
export function useVolumenEmbebido(ref, vol) {
  const volRef = useRef(vol)
  volRef.current = vol
  const mandar = (root) => {
    const v = volRef.current
    const cien = Math.round(v * 100)
    root.querySelectorAll('audio').forEach((a) => { a.volume = v })
    root.querySelectorAll('iframe[data-yt]').forEach((f) => {
      try { f.contentWindow.postMessage(JSON.stringify({ event: 'command', func: 'setVolume', args: [cien] }), '*') } catch { /* ignore */ }
    })
    root.querySelectorAll('iframe[data-sc]').forEach((f) => {
      try { f.contentWindow.postMessage(JSON.stringify({ method: 'setVolume', value: cien }), '*') } catch { /* ignore */ }
    })
  }
  // Un iframe no escucha mensajes hasta cargar: se le manda el volumen al cargar. Va en un
  // layout effect: un <audio autoPlay> recién montado tiene volumen 1 hasta que alguien se lo
  // baja, y con un efecto común había un instante (medible) en que el preview arrancaba fuerte.
  useLayoutEffect(() => {
    const root = ref.current
    if (!root) return undefined
    // Un <audio> nuevo (otro tema en el mismo reproductor) arranca con el volumen de la app.
    root.querySelectorAll('audio').forEach((a) => { if (a.volume !== volRef.current) a.volume = volRef.current })
    const frames = [...root.querySelectorAll('iframe')]
    const alCargar = () => mandar(root)
    frames.forEach((f) => f.addEventListener('load', alCargar))
    return () => frames.forEach((f) => f.removeEventListener('load', alCargar))
  })
  useEffect(() => { if (ref.current) mandar(ref.current) }, [vol]) // eslint-disable-line react-hooks/exhaustive-deps
}
