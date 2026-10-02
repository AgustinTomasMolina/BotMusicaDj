// Bajar desde la playlist (f41): un tema o "los N que faltan", sin volver al buscador.
//
// El estado vive en un store de módulo y no en la pantalla: el dueño puede irse a buscar
// otra cosa mientras baja la playlist y, al volver, ve en qué va. La pantalla lo lee con
// useSyncExternalStore.
//
// Qué se baja lo decide el server con el item guardado; acá solo viaja (pid, item, formato).
// Un fallo queda con el motivo que dio el server (o el que se sabe: sin conexión, HTTP 500),
// nunca como "listo": un dato que miente es peor que uno ausente (spec §6).
import { descargarItemPlaylist, esperarJob } from './api'

// Tope para seguir un job encolado: el mismo de jobs.encolar (job_timeout 900 s). El de
// esperarJob por defecto (3 min) cortaba un WAV largo que el worker seguía bajando.
export const TIMEOUT_JOB_MS = 900000

// Lo que se puede bajar desde acá: no está bajado y el server no dio motivo para no poder.
export const faltantes = (items) => (items || []).filter((it) => !it.descargado && !it.motivo_no_bajable)

// Baja UN item. Devuelve {exito, mensaje, item?, calidad?, quitado?}; nunca tira.
// `quitado: true` = el server dice que el tema ya no está en la playlist (lo quitaron mientras
// esperaba): no es un error que se pueda mostrar en su fila, porque la fila ya no existe.
export async function bajarItem(pid, itemId, formato, { pedir = descargarItemPlaylist, esperar = esperarJob } = {}) {
  let r
  try { r = await pedir(pid, itemId, formato) } catch {
    return { exito: false, mensaje: 'No pude conectar con el servidor.' }
  }
  const d = r.data || {}
  // Con worker: la descarga se encola y se sigue el job; su resultado tiene la misma forma.
  if (r.ok && d.encolado && d.job_id) {
    const fin = await esperar(d.job_id, { timeout: TIMEOUT_JOB_MS })
    return { exito: fin?.exito === true, mensaje: fin?.mensaje || (fin?.exito ? '' : 'El trabajo terminó sin decir por qué.'),
      item: fin?.item ?? null, calidad: fin?.calidad ?? null, ...(fin?.quitado === true ? { quitado: true } : {}) }
  }
  if (typeof d.exito === 'boolean') {
    return { exito: d.exito, mensaje: d.mensaje || (d.exito ? '' : `El servidor contestó ${r.status} sin motivo.`),
      item: d.item ?? null, calidad: d.calidad ?? null, ...(d.quitado === true ? { quitado: true } : {}) }
  }
  return { exito: false, mensaje: `El servidor falló al bajar (HTTP ${r.status})${d.error_texto ? `: ${d.error_texto}` : '.'}` }
}

// "Bajando 3 de 8 · Tema…" / el resumen al terminar. null si no hay lote.
export function textoLote(lote) {
  if (!lote) return null
  if (!lote.terminado) return `Bajando ${lote.n} de ${lote.total} · ${lote.titulo}…`
  const fallos = lote.fallidos.length
  const quitados = (lote.quitados || []).length
  const base = `${lote.ok} de ${lote.total} bajado${lote.total === 1 ? '' : 's'}`
  const corte = lote.detenido ? ' · detenido' : ''
  // Los que se quitaron de la playlist durante la tanda no son errores "con el motivo en el
  // tema": su fila ya no existe. Se cuentan aparte, con lo que pasó.
  const q = quitados ? ` · ${quitados} quitado${quitados === 1 ? '' : 's'} de la playlist mientras bajaba` : ''
  if (fallos) return `${base} · ${fallos} con error (el motivo está en cada tema)${q}${corte}`
  return quitados ? `${base}${q}${corte}` : `Listo: ${base}${corte}`
}

// El store. `bajar` es inyectable para los tests (un doble de bajarItem).
export function crearDescargas({ bajar = bajarItem } = {}) {
  // items: {[itemId]: {estado: 'cola'|'bajando'|'error', mensaje?}} — un bajado sale del mapa
  //   (lo dice la playlist recargada, no este estado).
  // lote:  null | {pid, total, n, titulo, ok, fallidos: [{id, titulo, mensaje}], quitados: [id],
  //                terminado, detenido}
  // hechos: cuántas descargas terminaron bien (la pantalla recarga la playlist cuando cambia).
  let estado = { items: {}, lote: null, hechos: 0 }
  const subs = new Set()
  let detener = false
  // Items que la pantalla quitó de la playlist (olvidar): la tanda no los pide al server.
  const quitadosAca = new Set()
  const set = (fn) => { estado = fn(estado); subs.forEach((f) => f()) }
  const setItem = (id, v) => set((e) => {
    const items = { ...e.items }
    if (v) items[id] = v; else delete items[id]
    return { ...e, items }
  })

  const uno = async (pid, item, formato) => {
    setItem(item.id, { estado: 'bajando' })
    const r = await bajar(pid, item.id, formato)
    if (r.exito) {
      setItem(item.id, null)
      set((e) => ({ ...e, hechos: e.hechos + 1 }))
    } else if (r.quitado) {
      setItem(item.id, null)      // la fila ya no está: no hay dónde mostrar un error
    } else {
      setItem(item.id, { estado: 'error', mensaje: r.mensaje })
    }
    return r
  }

  return {
    subscribe: (f) => { subs.add(f); return () => subs.delete(f) },
    getSnapshot: () => estado,
    ocupado: (id) => ['cola', 'bajando'].includes(estado.items[id]?.estado),

    // Un tema (el botón de la fila). Si ya está en cola o bajando, no se pide dos veces.
    async bajarUno(pid, item, formato) {
      if (this.ocupado(item.id)) return { exito: false, mensaje: 'Ya se está bajando.' }
      return uno(pid, item, formato)
    },

    // Los que faltan, de a uno: el progreso dice exactamente cuál baja, y no se le pide al
    // server (yt-dlp + ffmpeg) más de una conversión a la vez por playlist.
    async bajarTodos(pid, items, formato) {
      if (estado.lote && !estado.lote.terminado) return null
      const cola = faltantes(items).filter((it) => !this.ocupado(it.id))
      if (!cola.length) return null
      detener = false
      set((e) => {
        const its = { ...e.items }
        for (const it of cola) its[it.id] = { estado: 'cola' }
        return { ...e, items: its, lote: { pid, total: cola.length, n: 1, titulo: cola[0].titulo, ok: 0, fallidos: [], quitados: [], terminado: false, detenido: false } }
      })
      for (let i = 0; i < cola.length; i++) {
        const it = cola[i]
        if (detener) {
          // Los que no llegaron a bajarse vuelven a "falta bajar", sin error: nadie los intentó.
          set((e) => { const its = { ...e.items }; for (const x of cola.slice(i)) if (its[x.id]?.estado === 'cola') delete its[x.id]; return { ...e, items: its } })
          set((e) => ({ ...e, lote: { ...e.lote, terminado: true, detenido: true } }))
          return estado.lote
        }
        if (quitadosAca.has(it.id)) {
          // Lo quitaron mientras esperaba en la cola: no se pide (el server diría 404).
          setItem(it.id, null)
          set((e) => ({ ...e, lote: { ...e.lote, quitados: [...e.lote.quitados, it.id] } }))
          continue
        }
        set((e) => ({ ...e, lote: { ...e.lote, n: i + 1, titulo: it.titulo } }))
        const r = await uno(pid, it, formato)
        set((e) => ({ ...e, lote: r.exito
          ? { ...e.lote, ok: e.lote.ok + 1 }
          : r.quitado
            ? { ...e.lote, quitados: [...e.lote.quitados, it.id] }
            : { ...e.lote, fallidos: [...e.lote.fallidos, { id: it.id, titulo: it.titulo, mensaje: r.mensaje }] } }))
      }
      set((e) => ({ ...e, lote: { ...e.lote, terminado: true } }))
      return estado.lote
    },

    // La pantalla quitó el item de la playlist: si espera en la tanda, no se pide.
    olvidar(id) { quitadosAca.add(id) },
    // Corta DESPUÉS del tema que está bajando (ese ya está en el server y termina igual).
    detener() { detener = true },
    // Cierra el resumen de un lote terminado.
    cerrarLote() { if (estado.lote?.terminado) set((e) => ({ ...e, lote: null })) },
  }
}

export const descargas = crearDescargas()
