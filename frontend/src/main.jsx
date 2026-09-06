import { createRoot } from 'react-dom/client'
import './nocturne.css'
import App from './App.jsx'
import { ToastProvider } from './toast.jsx'

// Sin StrictMode: evita el doble-montado en dev que duplicaría la conexión del
// WebSocket de la consola y los timers de preview.
createRoot(document.getElementById('root')).render(
  <ToastProvider><App /></ToastProvider>,
)
