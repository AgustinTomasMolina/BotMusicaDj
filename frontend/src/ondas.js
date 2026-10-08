// La cola de ondas de la pantalla de radio (f50): UNA para las minionditas de la lista y la
// onda del editor, así nunca hay más de dos decodificaciones pedidas a la vez desde esta
// pantalla y el mismo tema no se pide dos veces. La lógica está en minionda.js.
import { getOnda } from './api'
import { crearCargador } from './minionda'

export const CONCURRENCIA_ONDAS = 2

export const cargadorOndas = crearCargador({ pedir: (id) => getOnda(id), concurrencia: CONCURRENCIA_ONDAS, max: 48 })
