// Componente: CancionCard
// Tarjeta individual de canción

import React from 'react';
import { FiPlay, FiDownload } from 'react-icons/fi';

export const CancionCard = ({ cancion, onDownload }) => {
  const {
    titulo = 'Desconocido',
    artista = 'Desconocido',
    puntuacion = 0,
    popularidad = 0,
    fuente = 'Unknown'
  } = cancion;

  const renderStars = (score) => {
    return '⭐'.repeat(Math.ceil(score / 20));
  };

  return (
    <div className="cancion-card">
      <div className="card-header">
        <div className="card-info">
          <h3 className="titulo">{titulo}</h3>
          <p className="artista">{artista}</p>
        </div>
        <span className={`fuente-badge ${fuente.toLowerCase()}`}>
          {fuente}
        </span>
      </div>

      <div className="card-stats">
        <div className="stat">
          <span className="label">Puntuación:</span>
          <span className="value">
            {renderStars(puntuacion)} {puntuacion.toFixed(1)}/100
          </span>
        </div>
        <div className="stat">
          <span className="label">Popularidad:</span>
          <span className="value">{popularidad}/100</span>
        </div>
      </div>

      <div className="card-actions">
        <button className="btn-play" title="Reproducir">
          <FiPlay /> Reproducir
        </button>
        <button 
          className="btn-download" 
          title="Descargar"
          onClick={() => onDownload(cancion)}
        >
          <FiDownload /> Descargar
        </button>
      </div>
    </div>
  );
};

export default CancionCard;
