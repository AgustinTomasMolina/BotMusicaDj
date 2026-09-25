// Motores de reproducción. Los tres exponen lo mismo para que el reproductor no sepa cuál
// suena:
//
//   load(track)  play()  pause()  seek(segundos)  setVolume(0..1)  destroy()
//
// y avisan por callbacks: onState('loading'|'playing'|'paused'|'ended'|'error', mensaje?)
// y onTime(segundos, duracion). Un mensaje de error dice qué pasó con palabras de la app,
// no el código crudo del proveedor.
//
// Por qué IFrame API y Widget API (y no el <iframe> suelto que usa el modal): sin ellas la
// barra no puede saber el tiempo, ni la duración, ni cuándo terminó el tema, y no puede
// pausar ni saltar. Costo: dos scripts externos que se cargan recién la primera vez que
// suena un tema de esa fuente. Si no cargan (sin red, bloqueados), el motor lo dice.

import { directSources, soundcloudUrl } from './track'
import { youtubeId } from '../cover'

const scripts = {}
function loadScript(src, ready) {
  if (!scripts[src]) {
    scripts[src] = new Promise((resolve, reject) => {
      if (ready()) { resolve(); return }
      const s = document.createElement('script')
      s.src = src
      s.async = true
      s.onload = () => resolve()
      s.onerror = () => { delete scripts[src]; reject(new Error('script')) }
      document.head.appendChild(s)
    })
  }
  return scripts[src]
}

// El script de YouTube avisa por window.onYouTubeIframeAPIReady (no por onload: después
// carga otro script). Con tope de tiempo: sin él, una red que cuelga dejaba "cargando" para
// siempre.
// Además del aviso se mira cada 200 ms si YT.Player ya existe: medido, la primera carga en
// frío a veces no llegaba a avisar antes del tope y el tema quedaba en error aunque el
// reproductor sí había cargado.
let ytReady = null
function loadYouTubeApi() {
  if (window.YT && window.YT.Player) return Promise.resolve()
  if (!ytReady) {
    ytReady = new Promise((resolve, reject) => {
      const prev = window.onYouTubeIframeAPIReady
      let vigia = null
      const listo = () => { clearTimeout(tope); clearInterval(vigia); resolve() }
      const tope = setTimeout(() => { clearInterval(vigia); ytReady = null; reject(new Error('timeout')) }, 30000)
      window.onYouTubeIframeAPIReady = () => { if (prev) prev(); listo() }
      vigia = setInterval(() => { if (window.YT && window.YT.Player) listo() }, 200)
      loadScript('https://www.youtube.com/iframe_api', () => false)
        .catch((e) => { clearTimeout(tope); clearInterval(vigia); ytReady = null; reject(e) })
    })
  }
  return ytReady
}

/* ---------- <audio> propio: biblioteca, radio, MP3 directos, previews ---------- */
export class AudioEngine {
  constructor(cb) {
    this.cb = cb
    this.a = new Audio()
    this.a.preload = 'auto'
    this.a.dataset.musiflixPlayer = '1'
    this.sources = []
    this.i = 0
    const a = this.a
    const t = () => cb.onTime(a.currentTime || 0, Number.isFinite(a.duration) ? a.duration : null)
    a.addEventListener('timeupdate', t)
    a.addEventListener('durationchange', t)
    a.addEventListener('playing', () => cb.onState('playing'))
    a.addEventListener('pause', () => { if (!a.ended) cb.onState('paused') })
    a.addEventListener('waiting', () => cb.onState('loading'))
    a.addEventListener('ended', () => cb.onState('ended'))
    a.addEventListener('error', () => {
      // Siguiente fuente (p. ej. el .m3u8 de Ligaudio → su MP3 directo) antes de rendirse.
      if (this.i + 1 < this.sources.length) { this.i++; this.a.src = this.sources[this.i]; this.play(); return }
      cb.onState('error', 'El navegador no pudo abrir este audio. Puede que el archivo se haya movido o que la fuente ya no lo sirva.')
    })
  }
  load(track) {
    this.sources = directSources(track)
    this.i = 0
    this.cb.onState('loading')
    this.a.src = this.sources[0]
    this.play()
  }
  play() {
    const p = this.a.play()
    if (p && p.catch) p.catch((e) => {
      if (e && e.name === 'AbortError') return            // se cambió de tema antes de arrancar
      if (e && e.name === 'NotAllowedError') this.cb.onState('paused')   // sin gesto del usuario
    })
  }
  pause() { this.a.pause() }
  seek(s) { try { this.a.currentTime = s } catch { /* sin metadata todavía */ } }
  setVolume(v) { this.a.volume = v }
  // Soltar el archivo (no solo pausar): si no, el navegador sigue bajando un WAV de 60 MB.
  // Los eventos que dispare esto los ignora el reproductor (este motor ya no es el activo).
  destroy() { this.sources = []; this.a.pause(); this.a.removeAttribute('src'); this.a.load() }
}

/* ---------- YouTube: IFrame API, el video queda visible en el monitor ---------- */
// 101/150 NO siempre es "el dueño lo desactivó": medido el 2026-09-25, YouTube devolvió 150
// en un Chrome automatizado hasta para videos que su oEmbed declara embebibles (y el <iframe>
// suelto del modal decía "Este video no está disponible" igual). El texto dice las dos causas.
const YT_ERRORES = {
  2: 'YouTube rechazó el pedido (id de video inválido).',
  5: 'El video no se puede reproducir en este navegador.',
  100: 'El video no existe o es privado.',
  101: 'YouTube no deja reproducir este video embebido (error 101): o el dueño lo desactivó o YouTube bloqueó este navegador.',
  150: 'YouTube no deja reproducir este video embebido (error 150): o el dueño lo desactivó o YouTube bloqueó este navegador.',
}
export class YouTubeEngine {
  constructor(host, cb) {
    this.host = host
    this.cb = cb
    this.player = null
    this.timer = null
    this.vol = 0.8
    this.pending = null
  }
  _tick(on) {
    clearInterval(this.timer)
    if (!on) return
    this.timer = setInterval(() => {
      const p = this.player
      if (p && p.getCurrentTime) this.cb.onTime(p.getCurrentTime() || 0, p.getDuration() || null)
    }, 250)
  }
  async load(track) {
    const id = youtubeId(track)
    const seq = (this.seq = (this.seq || 0) + 1)
    this.titulo = track.titulo || ''
    this.cb.onState('loading')
    try {
      await loadYouTubeApi()
    } catch {
      if (seq === this.seq) this.cb.onState('error', 'No pude cargar el reproductor de YouTube (¿sin conexión o bloqueado?).')
      return
    }
    if (seq !== this.seq) return                 // se pidió otro tema mientras cargaba
    if (this.player) {
      if (this.ready) this.player.loadVideoById(id)
      else this.pendingId = id                    // todavía no está listo: lo toma onReady
      this._title()
      return
    }
    const el = document.createElement('div')   // YT reemplaza este nodo por el iframe
    this.host.appendChild(el)
    this.ready = false
    this.pendingId = null
    this.player = new window.YT.Player(el, {
      videoId: id, width: '100%', height: '100%',
      playerVars: { autoplay: 1, playsinline: 1, rel: 0, modestbranding: 1 },
      events: {
        onReady: (e) => {
          this.ready = true
          e.target.setVolume(Math.round(this.vol * 100))
          if (this.pendingId && this.pendingId !== id) e.target.loadVideoById(this.pendingId)
          else e.target.playVideo()
          this.pendingId = null
        },
        onStateChange: (e) => {
          const S = window.YT.PlayerState
          if (e.data === S.PLAYING) { this.cb.onState('playing'); this._tick(true) }
          else if (e.data === S.PAUSED) { this.cb.onState('paused'); this._tick(false) }
          else if (e.data === S.BUFFERING) this.cb.onState('loading')
          else if (e.data === S.ENDED) { this._tick(false); this.cb.onState('ended') }
          const p = this.player
          if (p && p.getDuration) this.cb.onTime(p.getCurrentTime() || 0, p.getDuration() || null)
        },
        onError: (e) => { this._tick(false); this.cb.onState('error', YT_ERRORES[e.data] || 'YouTube no pudo reproducir este video.') },
      },
    })
    this._title()
  }
  _title() {
    // Los iframes necesitan un nombre accesible; YT crea el suyo sin title útil.
    const f = this.host.querySelector('iframe')
    if (f) f.title = `Video de YouTube: ${this.titulo}`
  }
  play() { this.player?.playVideo?.() }
  pause() { this.player?.pauseVideo?.() }
  seek(s) { this.player?.seekTo?.(s, true); this.cb.onTime(s, this.player?.getDuration?.() || null) }
  setVolume(v) { this.vol = v; this.player?.setVolume?.(Math.round(v * 100)) }
  destroy() {
    this._tick(false)
    this.seq = (this.seq || 0) + 1
    try { this.player?.destroy?.() } catch { /* ignore */ }
    this.player = null
    this.ready = false
    this.host.replaceChildren()
  }
}

/* ---------- SoundCloud: Widget API, el widget queda visible en el monitor ---------- */
export class SoundCloudEngine {
  constructor(host, cb) {
    this.host = host
    this.cb = cb
    this.widget = null
    this.iframe = null
    this.vol = 0.8
    this.dur = null
  }
  async load(track) {
    const url = soundcloudUrl(track)
    const seq = (this.seq = (this.seq || 0) + 1)
    this.cb.onState('loading')
    try {
      await loadScript('https://w.soundcloud.com/player/api.js', () => !!(window.SC && window.SC.Widget))
    } catch {
      if (seq === this.seq) this.cb.onState('error', 'No pude cargar el reproductor de SoundCloud (¿sin conexión o bloqueado?).')
      return
    }
    if (seq !== this.seq) return
    const params = '&auto_play=true&visual=false&hide_related=true&show_comments=false&show_reposts=false&show_teaser=false&buying=false&sharing=false&download=false&color=%239184d9'
    this.dur = null
    // Un tema que SoundCloud no deja embeber a veces no avisa nada: el widget queda mudo.
    clearTimeout(this.tope)
    this.tope = setTimeout(() => {
      if (seq === this.seq && this.dur == null) this.cb.onState('error', 'SoundCloud no respondió con este tema (puede que no se pueda embeber o que ya no esté).')
    }, 15000)
    if (this.widget) {
      this.widget.load(url, { auto_play: true, visual: false, hide_related: true, show_comments: false, callback: () => this._ready() })
      if (this.iframe) this.iframe.title = `Reproductor de SoundCloud: ${track.titulo || ''}`
      return
    }
    const f = document.createElement('iframe')
    f.allow = 'autoplay'
    f.title = `Reproductor de SoundCloud: ${track.titulo || ''}`
    f.src = `https://w.soundcloud.com/player/?url=${encodeURIComponent(url)}${params}`
    this.host.appendChild(f)
    this.iframe = f
    const W = window.SC.Widget
    const w = W(f)
    this.widget = w
    w.bind(W.Events.READY, () => this._ready())
    w.bind(W.Events.PLAY, () => this.cb.onState('playing'))
    w.bind(W.Events.PAUSE, () => this.cb.onState('paused'))
    w.bind(W.Events.FINISH, () => this.cb.onState('ended'))
    w.bind(W.Events.ERROR, () => this.cb.onState('error', 'SoundCloud no pudo reproducir este tema (puede que ya no esté o que no se pueda embeber).'))
    w.bind(W.Events.PLAY_PROGRESS, (e) => this.cb.onTime((e.currentPosition || 0) / 1000, this.dur))
  }
  _ready() {
    const w = this.widget
    w.setVolume(Math.round(this.vol * 100))
    w.getDuration((ms) => { this.dur = ms ? ms / 1000 : null; if (this.dur) clearTimeout(this.tope); this.cb.onTime(0, this.dur) })
    w.play()
  }
  play() { this.widget?.play() }
  pause() { this.widget?.pause() }
  seek(s) { this.widget?.seekTo(Math.round(s * 1000)); this.cb.onTime(s, this.dur) }
  setVolume(v) { this.vol = v; this.widget?.setVolume(Math.round(v * 100)) }
  destroy() {
    clearTimeout(this.tope)
    this.seq = (this.seq || 0) + 1
    try { this.widget?.pause() } catch { /* ignore */ }
    this.widget = null
    this.iframe = null
    this.host.replaceChildren()
  }
}
