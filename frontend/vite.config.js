import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // En desarrollo, Vite corre en :5173 y reenvía al backend FastAPI (:8000):
    //  - /api  → REST (búsqueda, descarga, spectro, meta…)
    //  - /ws   → WebSocket de la consola en vivo (ws:true hace el upgrade)
    // Así el frontend siempre habla con el MISMO origen (location.host) y el mismo
    // código funciona en dev (proxy) y en prod (FastAPI sirve el build + la API).
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/ws': { target: 'http://127.0.0.1:8000', ws: true },
    },
  },
  // El build sale a frontend/dist; FastAPI lo sirve en producción.
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
