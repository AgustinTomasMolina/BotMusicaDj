// API Service - Frontend Web
// Comunicación con backend Python

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

// Interfaz de servicios

export const musicAPI = {
  // Búsqueda
  buscar: async (query, limite = 10) => {
    const response = await fetch(`${API_BASE_URL}/api/v1/buscar`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, limite })
    });
    return response.json();
  },

  // Búsqueda por género
  buscarGenero: async (genero, cantidad = 15) => {
    const response = await fetch(`${API_BASE_URL}/api/v1/buscar-genero`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ genero, cantidad })
    });
    return response.json();
  },

  // Recomendaciones
  recomendar: async (genero, cantidad = 15) => {
    const response = await fetch(`${API_BASE_URL}/api/v1/recomendar`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ genero, cantidad })
    });
    return response.json();
  },

  // Descargar
  descargar: async (genero, cantidad = 15, formato = 'mp3') => {
    const response = await fetch(`${API_BASE_URL}/api/v1/descargar`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ genero, cantidad, formato })
    });
    return response.json();
  },

  // Listar descargas
  listarDescargas: async () => {
    const response = await fetch(`${API_BASE_URL}/api/v1/descargas`);
    return response.json();
  },

  // Formatos
  obtenerFormatos: async () => {
    const response = await fetch(`${API_BASE_URL}/api/v1/formatos`);
    return response.json();
  },

  // Géneros
  obtenerGeneros: async () => {
    const response = await fetch(`${API_BASE_URL}/api/v1/generos-populares`);
    return response.json();
  },

  // Health
  health: async () => {
    const response = await fetch(`${API_BASE_URL}/health`);
    return response.json();
  }
};

export default musicAPI;
