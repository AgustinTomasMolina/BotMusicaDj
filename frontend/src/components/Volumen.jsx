import { usePlayer } from '../player/context'

/* Volumen y silencio (f50). Es el MISMO volumen de la barra de abajo (PlayerProvider): mover
   este deslizador mueve el de la barra y al revés, y los dos se acuerdan entre sesiones.
   Silenciado, el deslizador muestra 0 (igual que la barra). */
const Ico = ({ off }) => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M11 5 6 9H3v6h3l5 4z" />
    {off ? <path d="m22 9-6 6M16 9l6 6" /> : <><path d="M15.5 8.5a5 5 0 0 1 0 7" /><path d="M18.5 5.5a9 9 0 0 1 0 13" /></>}
  </svg>
)

export default function Volumen({ className = '', ayudaId }) {
  const p = usePlayer()
  const vol = p.muted ? 0 : Math.round(p.volume * 100)
  return (
    <div className={`vol ${className}`}>
      <button type="button" className="vol-mute" onClick={p.toggleMute}
        aria-label={p.muted ? 'Activar sonido' : 'Silenciar'} aria-pressed={p.muted}
        title={p.muted ? 'Activar sonido' : 'Silenciar'}>
        <Ico off={p.muted || vol === 0} />
      </button>
      {/* role="slider" explícito (el range ya lo es): lo pide la pantalla y no cuesta nada. */}
      <input type="range" role="slider" className="vol-range" min="0" max="100" step="5" value={vol}
        style={{ '--pct': `${vol}%` }}
        aria-label="Volumen" aria-valuemin={0} aria-valuemax={100} aria-valuenow={vol}
        aria-valuetext={p.muted ? 'Silenciado' : `${vol} %`} aria-describedby={ayudaId}
        onChange={(e) => p.setVolume(Number(e.target.value) / 100)} />
      <span className="vol-pct mono" aria-hidden="true">{vol} %</span>
    </div>
  )
}
