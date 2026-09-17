// Crear una playlist: un solo flujo para los dos lugares que la crean (el ＋ del rail lateral y
// la vista Playlists). Pide el nombre, valida que no esté vacío, avisa al resto de la app y
// deja que quien llama decida qué hacer con la playlist nueva (abrirla, seleccionarla…).
import { crearPlaylist, avisarPlaylists } from './api'

export const HINT_CONEXION = 'Revisá que el servidor esté corriendo y probá de nuevo.'

export async function crearPlaylistConPrompt({ toast, onCreada }) {
  const nombre = window.prompt('Nombre de la nueva playlist:')
  if (!nombre || !nombre.trim()) return null
  try {
    const r = await crearPlaylist(nombre.trim())
    if (r.exito && r.playlist) {
      avisarPlaylists()
      await onCreada?.(r.playlist)
      toast.info({ title: `Playlist "${r.playlist.nombre}" creada` })
      return r.playlist
    }
  } catch { /* cae al aviso de abajo */ }
  toast.danger({ title: 'No pude crear la playlist', body: HINT_CONEXION })
  return null
}
