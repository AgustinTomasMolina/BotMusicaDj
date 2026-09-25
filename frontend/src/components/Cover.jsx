import { useState } from 'react'
import { coverCandidates, placeholderStyle } from '../cover'

/* Carátula de un tema: prueba las imágenes reales en orden (ver src/cover.js) y, si ninguna
   carga, dibuja el placeholder decorativo. Siempre alt="" y aria-hidden en el placeholder:
   el título y el artista están escritos al lado, la imagen no agrega información. */
export default function Cover({ track, className = '' }) {
  const fuentes = coverCandidates(track)
  const clave = fuentes.join('|')
  // El intento vive junto a la lista para la que se calculó: si cambia el tema, arranca de 0
  // sin un efecto que lo resetee (y sin un render con la imagen del tema anterior).
  const [estado, setEstado] = useState({ clave, i: 0 })
  const i = estado.clave === clave ? estado.i : 0
  const src = fuentes[i]
  if (!src) {
    return (
      <span className={`cover cover-ph ${className}`} style={placeholderStyle(track)} aria-hidden="true">
        <svg className="cover-ph-note" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
          strokeLinecap="round" strokeLinejoin="round"><circle cx="7" cy="17" r="3" /><path d="M10 17V5l9-2v3l-9 2" /></svg>
      </span>
    )
  }
  return (
    <img key={src} className={`cover ${className}`} src={src} alt="" loading="lazy" decoding="async"
      onError={() => setEstado({ clave, i: i + 1 })} />
  )
}
