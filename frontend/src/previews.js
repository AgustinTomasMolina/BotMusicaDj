// Las colas de la vista previa de las filas de Mis playlists (f62). La lógica (tope de
// concurrencia, pedidos compartidos, caché, cancelar lo que salió de pantalla) es la de
// minionda.js; acá solo se dice qué se pide.
//
// - La onda (/preview, 3 × 160 valores): de a DOS, como las minionditas de la radio. La primera
//   vez el servidor decodifica el archivo entero (después queda en su caché), y en la PC del
//   dueño, con poca memoria, no tienen que salir diez juntas. Se guarda por tema: la onda no
//   cambia al marcar cues.
// - Las marcas (/marcas, solo la base): por `id#versión`. Cuando el editor D cambia algo, la
//   playlist sube la versión y la fila las vuelve a leer; la onda no se vuelve a pedir.
import { getMarcas, getPreview } from './api'
import { crearCargador } from './minionda'
import { PUNTOS_PREVIEW } from './previewOnda'

export const CONCURRENCIA_PREVIEW = 2

export const cargadorPreviews = crearCargador({
  pedir: (id) => getPreview(id, PUNTOS_PREVIEW), concurrencia: CONCURRENCIA_PREVIEW, max: 96,
})

export const claveMarcas = (id, version) => `${id}#${version}`

export const cargadorMarcasPreview = crearCargador({
  pedir: (clave) => getMarcas(clave.slice(0, clave.indexOf('#'))), concurrencia: CONCURRENCIA_PREVIEW, max: 96,
})
