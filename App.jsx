// App.jsx - Componente Principal
// Frontend Web de Music Bot

import React, { useState } from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import SearchBar from './components/SearchBar';
import RecommendationList from './components/RecommendationList';
import CancionCard from './components/CancionCard';
import musicAPI from './services/api';
import './styles/index.css';

function App() {
  const [selectedGenero, setSelectedGenero] = useState('House');
  const [searchResults, setSearchResults] = useState([]);
  const [descargas, setDescargas] = useState([]);
  const [formato, setFormato] = useState('mp3');
  const [showDownloads, setShowDownloads] = useState(false);

  const handleSearch = (results) => {
    if (results.exito) {
      setSearchResults(results.datos.canciones || []);
    }
  };

  const handleDownload = async (cancion) => {
    // Aquí iría lógica de descarga
    alert(`Descargando: ${cancion.titulo}`);
  };

  const handleGeneroSelect = (genero) => {
    setSelectedGenero(genero);
  };

  const cargarDescargas = async () => {
    const results = await musicAPI.listarDescargas();
    if (results.exito) {
      setDescargas(results.datos.archivos || []);
    }
    setShowDownloads(!showDownloads);
  };

  const generos = [
    'House', 'Techno', 'Trance', 'Ambient',
    'Deep House', 'Electro', 'Drum & Bass'
  ];

  return (
    <div className="app">
      <header className="header">
        <div className="header-content">
          <h1>🎵 Music Bot</h1>
          <p>Bot Inteligente de Búsqueda y Descarga de Música</p>
        </div>
        <button className="btn-downloads" onClick={cargarDescargas}>
          📂 Descargas ({descargas.length})
        </button>
      </header>

      <main className="main-content">
        <SearchBar onSearch={handleSearch} />

        <div className="container">
          {/* Géneros Sidebar */}
          <aside className="sidebar">
            <h3>🎵 Géneros</h3>
            <div className="generos-list">
              {generos.map((genero) => (
                <button
                  key={genero}
                  className={`genero-btn ${selectedGenero === genero ? 'active' : ''}`}
                  onClick={() => handleGeneroSelect(genero)}
                >
                  {genero}
                </button>
              ))}
            </div>

            <h3>📥 Formato</h3>
            <select 
              value={formato} 
              onChange={(e) => setFormato(e.target.value)}
              className="formato-select"
            >
              <option value="wav">WAV (Mejor calidad)</option>
              <option value="aiff">AIFF (Profesional)</option>
              <option value="flac">FLAC (Sin pérdida)</option>
              <option value="mp3">MP3 (Última opción)</option>
            </select>
          </aside>

          {/* Contenido Principal */}
          <section className="content">
            {showDownloads ? (
              <div className="descargas-section">
                <h2>📂 Archivos Descargados</h2>
                <div className="descargas-list">
                  {descargas.length === 0 ? (
                    <p>No hay descargas aún</p>
                  ) : (
                    descargas.map((archivo, idx) => (
                      <div key={idx} className="descarga-item">
                        <span className="nombre">{archivo.nombre}</span>
                        <span className="tamaño">{archivo.tamaño_mb.toFixed(2)} MB</span>
                      </div>
                    ))
                  )}
                </div>
              </div>
            ) : (
              <>
                {searchResults.length > 0 ? (
                  <div className="search-results">
                    <h2>🔍 Resultados de Búsqueda</h2>
                    <div className="canciones-grid">
                      {searchResults.map((cancion, idx) => (
                        <CancionCard 
                          key={idx}
                          cancion={cancion}
                          onDownload={handleDownload}
                        />
                      ))}
                    </div>
                  </div>
                ) : (
                  <RecommendationList 
                    genero={selectedGenero}
                    onDownload={handleDownload}
                  />
                )}
              </>
            )}
          </section>
        </div>
      </main>

      <footer className="footer">
        <p>🎵 Music Bot v1.0.0 | Busca, recomienda y descarga música inteligentemente</p>
      </footer>
    </div>
  );
}

export default App;
