// Componente: SearchBar
// Barra de búsqueda principal

import React, { useState } from 'react';
import { FiSearch } from 'react-icons/fi';
import musicAPI from '../services/api';

export const SearchBar = ({ onSearch }) => {
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSearch = async (e) => {
    e.preventDefault();
    if (!query.trim()) return;

    setLoading(true);
    try {
      const results = await musicAPI.buscar(query, 10);
      onSearch(results);
    } catch (error) {
      console.error('Error en búsqueda:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="search-bar">
      <form onSubmit={handleSearch}>
        <div className="search-input-wrapper">
          <FiSearch className="search-icon" />
          <input
            type="text"
            placeholder="🎵 Busca una canción, artista o género..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            disabled={loading}
          />
          <button type="submit" disabled={loading}>
            {loading ? 'Buscando...' : 'Buscar'}
          </button>
        </div>
      </form>
    </div>
  );
};

export default SearchBar;
