/* Estado de la carpeta vigilada (f65) en una línea, arriba del historial (f66).

   Va en el cajón del historial porque es donde ya están las descargas: la carpeta vigilada es
   la otra puerta de entrada de temas nuevos (lo que se baja a mano o con otra herramienta y
   cae en MUSIC_WATCH_FOLDER), y el cajón se abre desde cualquier pantalla. Muestra solo lo que
   devuelve GET /api/vigilancia (ver src/vigilancia.js); "Escanear ahora" adelanta el próximo
   ciclo del server (POST /api/vigilancia/escanear), que corre en su hilo: el resultado no está
   en la respuesta, así que se vuelve a pedir el estado unos segundos después.

   Está montado SOLO con el cajón abierto (HistorialDrawer): cada apertura arranca de cero
   ("Leyendo…") y lo que todavía estaba en vuelo al cerrar no puede tocar la próxima. `vivo`
   cubre lo que vuelve después de desmontar: ni estado ni relectura. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { vigilancia, escanearVigilancia } from '../api'
import { resumenVigilancia } from '../vigilancia'

const RELEER_MS = 4000

export default function VigilanciaEstado() {
  const [r, setR] = useState(null)              // la respuesta de GET /api/vigilancia
  const [pidiendo, setPidiendo] = useState(false)
  const [aviso, setAviso] = useState('')
  const timer = useRef(null)
  const vivo = useRef(false)                    // false: desmontado (el cajón se cerró)
  const tanda = useRef(0)                       // sube con cada «Escanear ahora»

  const cargar = useCallback(async () => {
    let d
    try { d = await vigilancia() } catch { d = { error: true } }
    if (vivo.current) setR(d)
  }, [])
  useEffect(() => {
    vivo.current = true
    cargar()
    return () => { vivo.current = false; clearTimeout(timer.current) }
  }, [cargar])

  const escanear = async () => {
    // Lo primero: la relectura de un escaneo anterior no puede borrar el aviso de este.
    clearTimeout(timer.current)
    const mia = ++tanda.current
    setPidiendo(true)
    setAviso('')
    try {
      const d = await escanearVigilancia()
      if (!vivo.current) return
      if (d && d.exito) {
        setR({ exito: true, activa: true, estado: d.estado })
        setAviso(d.encolado ? 'Escaneo pedido: arranca en unos segundos.' : 'El vigilante no está corriendo: no se pudo adelantar el escaneo.')
        // Al releer, "Escaneo pedido" ya cumplió: lo que pasó lo dice el estado nuevo (salvo
        // que mientras releía se haya pedido otro escaneo: el aviso es de ese).
        const pedido = d.encolado
        timer.current = setTimeout(async () => {
          await cargar()
          if (pedido && vivo.current && tanda.current === mia) setAviso('')
        }, RELEER_MS)
      } else setAviso((d && d.mensaje) || 'No se pudo pedir el escaneo.')
    } catch {
      if (vivo.current) setAviso('No pude conectar con el servidor.')
    } finally {
      if (vivo.current) setPidiendo(false)
    }
  }

  if (!r) return <div className="vig" aria-busy="true"><span className="vig-msg">Leyendo la carpeta vigilada…</span></div>
  const v = resumenVigilancia(r)
  return (
    <section className="vig" aria-label="Carpeta vigilada" data-estado={v.tipo}>
      <div className="vig-head">
        <span className="vig-titulo">{v.titulo}</span>
        {v.tipo === 'activa' &&
          <button type="button" className="btn btn-secondary btn-sm" onClick={escanear} disabled={pidiendo}>
            {pidiendo ? 'Pidiendo…' : 'Escanear ahora'}
          </button>}
      </div>
      {v.partes.length > 0 && <span className="vig-partes">{v.partes.join(' · ')}</span>}
      {v.procesando.length > 0 && <span className="vig-partes">Procesando: {v.procesando.join(', ')}</span>}
      {v.reporte && <span className="vig-partes">Último reporte: {v.reporte}</span>}
      {v.aviso && <span className="vig-aviso">{v.aviso}</span>}
      <span className="vig-msg" role="status">{aviso}</span>
    </section>
  )
}
