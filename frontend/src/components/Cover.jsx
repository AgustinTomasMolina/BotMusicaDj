import { useState } from 'react'
import { coverCandidates, placeholderStyle } from '../cover'

/* Carátula de un tema: el placeholder decorativo va SIEMPRE abajo y la imagen real encima,
   visible recién cuando cargó (onLoad). Antes, mientras una imagen lazy no llegaba (o si
   nunca llegaba), se veía el marco vacío del color del fondo: exactamente lo que reportó el
   dueño. Si la imagen falla, prueba la siguiente fuente (ver src/cover.js); sin ninguna,
   queda el placeholder. alt="" y aria-hidden: el título y el artista están escritos al lado. */
export default function Cover({ track, className = '' }) {
  const fuentes = coverCandidates(track)
  const clave = fuentes.join('|')
  // El intento vive junto a la lista para la que se calculó: si cambia el tema, arranca de 0
  // sin un efecto que lo resetee (y sin un render con la imagen del tema anterior).
  const [estado, setEstado] = useState({ clave, i: 0, cargada: null })
  const actual = estado.clave === clave ? estado : { clave, i: 0, cargada: null }
  const src = fuentes[actual.i]
  return (
    <span className={`cover cover-stack ${className}`}>
      <span className="cover-ph" style={placeholderStyle(track)} aria-hidden="true">
        <svg className="cover-ph-note" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
          strokeLinecap="round" strokeLinejoin="round"><circle cx="7" cy="17" r="3" /><path d="M10 17V5l9-2v3l-9 2" /></svg>
      </span>
      {src && (
        <img key={src} className={`cover-img${actual.cargada === src ? ' is-loaded' : ''}`} src={src} alt=""
          loading="lazy" decoding="async"
          onLoad={() => setEstado({ clave, i: actual.i, cargada: src })}
          onError={() => setEstado({ clave, i: actual.i + 1, cargada: null })} />
      )}
    </span>
  )
}
