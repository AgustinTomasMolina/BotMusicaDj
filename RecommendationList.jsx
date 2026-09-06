// Componente: RecommendationList
// Lista de recomendaciones

import React, { useState, useEffect } from 'react';
import CancionCard from './CancionCard';
import musicAPI from '../services/api';

export const RecommendationList = ({ genero, onDownload }) => {
  const [canciones, setCanciones] = useState([]);
  const [loading, setLoading] = useState(false);
  const [cantidad, setCantidad] = useState(15);

  useEffect(() => {
    if (genero) {
      loadRecommendations();
    }
  }, [genero, cantidad]);

  const loadRecommendations = async () => {
    setLoading(true);
    try {
      const results = await musicAPI.recomendar(genero, cantidad);
      if (results.exito) {
        setCanciones(results.datos.canciones || []);
      }
    } catch (error) {
      console.error('Error cargando recomendaciones:', error);
    } finally {
      setLoading(false);
    }
  };

  if (!genero) {
    return <div className="empty-state">Selecciona un género para ver recomendaciones</div>;
  }

  return (
    <div className="recommendation-list">
      <div className="list-header">
        <h2>🎯 Recomendaciones: {genero}</h2>
        <div className="cantidad-selector">
          <label>Cantidad:</label>
          <select value={cantidad} onChange={(e) => setCantidad(parseInt(e.target.value))}>
            <option value={5}>5</option>
            <option value={10}>10</option>
            <option value={15}>15</option>
            <option value={20}>20</option>
            <option value={30}>30</option>
          </select>
        </div>
      </div>

      {loading && <div className="loading">Cargando recomendaciones...</div>}

      <div className="canciones-grid">
        {canciones.map((cancion, idx) => (
          <CancionCard 
            key={idx}
            cancion={cancion}
            onDownload={onDownload}
          />
        ))}
      </div>

      {!loading && canciones.length === 0 && (
        <div className="empty-state">No se encontraron recomendaciones</div>
      )}
    </div>
  );
};

export default RecommendationList;
