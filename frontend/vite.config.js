import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // En desarrollo, Vite corre en :5173 y reenvía al backend FastAPI (:8000):
    //  - /api  → REST (búsqueda, descarga, spectro, meta…)
    // Así el frontend siempre habla con el MISMO origen (location.host) y el mismo
    // código funciona en dev (proxy) y en prod (FastAPI sirve el build + la API).
    // changeOrigin: false EXPLÍCITO: Vite 8 convierte el proxy escrito como string en
    // {target, changeOrigin: true} y reescribe Host a 127.0.0.1:8000; con Origin
    // http://localhost:5173 el server (`_origen_ajeno`) toma el pedido como de otra página y
    // todas las escrituras dan 403 en `npm run dev`. Sin reescribir, Host y Origin coinciden.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
  // El build sale a frontend/dist; FastAPI lo sirve en producción.
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
