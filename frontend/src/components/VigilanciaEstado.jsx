/* Estado de la carpeta vigilada (f65) en una línea, arriba del historial (f66).

   Va en el cajón del historial porque es donde ya están las descargas: la carpeta vigilada es
   la otra puerta de entrada de temas nuevos (lo que se baja a mano o con otra herramienta y
   cae en MUSIC_WATCH_FOLDER), y el cajón se abre desde cualquier pantalla. Muestra solo lo que
   devuelve GET /api/vigilancia (ver src/vigilancia.js); "Escanear ahora" adelanta el próximo
   ciclo del server (POST /api/vigilancia/escanear), que corre en su hilo: el resultado no está
   en la respuesta, así que se vuelve a pedir el estado unos segundos después. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { vigilancia, escanearVigilancia } from '../api'
import { resumenVigilancia } from '../vigilancia'

const RELEER_MS = 4000

export default function VigilanciaEstado({ open }) {
  const [r, setR] = useState(null)              // la respuesta de GET /api/vigilancia
  const [pidiendo, setPidiendo] = useState(false)
  const [aviso, setAviso] = useState('')
  const timer = useRef(null)

  const cargar = useCallback(async () => {
    try { setR(await vigilancia()) } catch { setR({ error: true }) }
  }, [])
  useEffect(() => {
    // Cerrado: se olvida lo leído, así al volver a abrir se ve "Leyendo…" y no el estado de antes.
    if (!open) { setR(null); setAviso(''); return undefined }
    setAviso('')
    cargar()
    return () => clearTimeout(timer.current)
  }, [open, cargar])

  const escanear = async () => {
    setPidiendo(true)
    setAviso('')
    try {
      const d = await escanearVigilancia()
      if (d && d.exito) {
        setR({ exito: true, activa: true, estado: d.estado })
        setAviso(d.encolado ? 'Escaneo pedido: arranca en unos segundos.' : 'El vigilante no está corriendo: no se pudo adelantar el escaneo.')
        clearTimeout(timer.current)
        // Al releer, "Escaneo pedido" ya cumplió: lo que pasó lo dice el estado nuevo.
        const pedido = d.encolado
        timer.current = setTimeout(async () => { await cargar(); if (pedido) setAviso('') }, RELEER_MS)
      } else setAviso((d && d.mensaje) || 'No se pudo pedir el escaneo.')
    } catch {
      setAviso('No pude conectar con el servidor.')
    } finally {
      setPidiendo(false)
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
