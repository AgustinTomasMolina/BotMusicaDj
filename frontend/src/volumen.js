// El volumen de toda la app (f50): un solo valor, el del reproductor (PlayerProvider), que
// respetan la barra de abajo, el <audio> de la pantalla de radio y el del editor de cues.
// Lógica pura (sin React ni DOM real) para probarla con `node --test` (test/volumen.test.mjs).
//
// Por defecto va a un nivel MEDIO: el dueño se quejó de que sonaba muy fuerte (estaba al 80 %),
// y §6 pide "nunca autoplay con volumen alto". Lo que el usuario elige se acuerda entre
// sesiones; si no eligió nada (o lo guardado no es un volumen) vuelve el de fábrica.

import { escribiendo } from './cues'

export const VOL_KEY = 'musiflix.volumen'
export const VOL_DEFECTO = 0.45
export const VOL_PASO = 0.05

// El volumen guardado (0..1) o el de fábrica. `storage` es localStorage o algo con getItem;
// puede no existir o tirar (modo privado, almacenamiento bloqueado): nunca rompe.
export function leerVolumen(storage) {
  try {
    const crudo = storage ? storage.getItem(VOL_KEY) : null
    if (crudo === null || crudo === undefined || String(crudo).trim() === '') return VOL_DEFECTO
    const v = Number(crudo)
    return Number.isFinite(v) && v >= 0 && v <= 1 ? v : VOL_DEFECTO
  } catch {
    return VOL_DEFECTO
  }
}

// Guarda el volumen; si no se puede, queda solo para esta sesión (no es un error).
export function guardarVolumen(storage, v) {
  try { if (storage) storage.setItem(VOL_KEY, String(v)) } catch { /* sin almacenamiento */ }
}

// Un paso de volumen hacia arriba (dir = 1) o abajo (-1), sobre la grilla de 5 en 5 y dentro
// de 0..1. Redondeado a dos decimales: 0.45 + 0.05 tiene que dar 0.5, no 0.5000000000000001.
export function pasoVolumen(v, dir) {
  const base = Number.isFinite(Number(v)) ? Number(v) : VOL_DEFECTO
  const n = Math.round(base / VOL_PASO) + (dir > 0 ? 1 : -1)
  const x = Math.min(1, Math.max(0, n * VOL_PASO))
  return Math.round(x * 100) / 100
}

// ¿Esta tecla sube (1) o baja (-1) el volumen? Solo ↑ y ↓, sin modificadores (Shift+↑ en un
// campo selecciona texto) y nunca mientras se escribe ni sobre un control que ya usa las
// flechas por su cuenta (el propio deslizador del volumen, un desplegable).
export function teclaVolumen(e) {
  if (!e || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey || e.isComposing) return null
  if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return null
  const t = e.target
  if (escribiendo(t)) return null
  const tag = ((t && t.tagName) || '').toLowerCase()
  if (tag === 'input' && ((t.type || '').toLowerCase() === 'range')) return null
  return e.key === 'ArrowUp' ? 1 : -1
}
