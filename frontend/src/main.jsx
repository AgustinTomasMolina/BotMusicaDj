import { createRoot } from 'react-dom/client'
import './nocturne.css'
import App from './App.jsx'
import { ToastProvider } from './toast.jsx'
import PlayerProvider from './player/PlayerProvider.jsx'

// Sin StrictMode: evita el doble-montado en dev que duplicaría la conexión del
// WebSocket de la consola y los timers de preview.
createRoot(document.getElementById('root')).render(
  // El reproductor vive arriba de App: al cambiar de pantalla no se desmonta ni se corta.
  <ToastProvider><PlayerProvider><App /></PlayerProvider></ToastProvider>,
)
