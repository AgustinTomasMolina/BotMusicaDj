import { createContext, useContext } from 'react'

// El reproductor de la app (barra fija abajo). API para cualquier pantalla:
//
//   const player = usePlayer()
//   player.playQueue(tracks, i)   // tracks normalizados con src/player/track.js (fromLibrary,
//                                 // fromResult, fromCrateItem, fromRadio); suena tracks[i] y
//                                 // anterior/siguiente recorren esa misma lista
//   player.toggle()  player.next()  player.prev()  player.seek(s)  player.setVolume(0..1)
//   player.toggleMute()  player.close()
//   player.current   el track que está cargado (o null)
//   player.status    'idle' | 'loading' | 'playing' | 'paused' | 'ended' | 'error' | 'embed' | 'none'
//   player.isPlaying(key)  ¿ese tema es el que suena ahora?
//
// Un solo audio en toda la app: al arrancar, la barra pausa cualquier otro <audio>/<video>
// de la página y avisa con el evento 'musiflix:player-play' (la app corta el preview del
// mouse con eso); si otro <audio> arranca (p. ej. el de la pantalla de radio), la barra se
// pausa sola.
export const PlayerCtx = createContext(null)
export const usePlayer = () => useContext(PlayerCtx)

// El tiempo va en un contexto aparte: cambia 4 veces por segundo y solo lo lee la barra.
// En el mismo contexto, cada tarjeta de la home se volvería a dibujar con cada tick.
export const PlayerTimeCtx = createContext({ time: 0, duration: null })
export const usePlayerTime = () => useContext(PlayerTimeCtx)
