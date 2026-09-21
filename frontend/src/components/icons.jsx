/* Iconos SVG de línea (estilo Lucide/Feather): stroke=currentColor, así heredan el
   color del botón. Reemplazan los emojis de Windows por algo más pro y consistente. */

const S = ({ size = 20, children, ...p }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...p}>
    {children}
  </svg>
)

export const IconHome = (p) => <S {...p}><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><path d="M9 22V12h6v10" /></S>
export const IconHeadphones = (p) => <S {...p}><path d="M3 14h3a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a2 2 0 0 1-2-2v-4a9 9 0 0 1 18 0v4a2 2 0 0 1-2 2h-1a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1h3" /></S>
// Oído (preview activada) y oído tachado (desactivada) — trazos de Lucide "ear"/"ear-off".
export const IconEar = (p) => <S {...p}><path d="M6 8.5a6.5 6.5 0 1 1 13 0c0 6-6 6-6 10a3.5 3.5 0 1 1-7 0" /><path d="M15 8.5a2.5 2.5 0 0 0-5 0v1a2 2 0 1 1 0 4" /></S>
export const IconEarOff = (p) => <S {...p}><path d="M6 18.5a3.5 3.5 0 1 0 7 0c0-1.57.92-2.52 2.04-3.46" /><path d="M6 8.5c0-.75.13-1.47.36-2.14" /><path d="M8.8 3.15A6.5 6.5 0 0 1 19 8.5c0 1.63-.44 2.81-1.09 3.76" /><path d="M12.5 6A2.5 2.5 0 0 1 15 8.5M10 13a2 2 0 0 0 1.82-1.18" /><path d="m2 2 20 20" /></S>
export const IconList = (p) => <S {...p}><path d="M8 6h13" /><path d="M8 12h13" /><path d="M8 18h13" /><path d="M3 6h.01" /><path d="M3 12h.01" /><path d="M3 18h.01" /></S>
export const IconHistory = (p) => <S {...p}><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" /><path d="M12 7v5l3 2" /></S>
export const IconTerminal = (p) => <S {...p}><path d="m4 17 6-6-6-6" /><path d="M12 19h8" /></S>
export const IconPlay = (p) => <S {...p}><polygon points="6 3 20 12 6 21" /></S>
export const IconDownload = (p) => <S {...p}><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" /></S>
export const IconActivity = (p) => <S {...p}><path d="M22 12h-4l-3 9L9 3l-3 9H2" /></S>
export const IconCompare = (p) => <S {...p}><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M12 3v18" /></S>
export const IconSparkles = (p) => <S {...p}><path d="m12 3 1.9 4.1L18 9l-4.1 1.9L12 15l-1.9-4.1L6 9l4.1-1.9z" /><path d="M19 14.5l.7 1.6 1.8.7-1.8.7-.7 1.6-.7-1.6-1.8-.7 1.8-.7z" /></S>
export const IconTrash = (p) => <S {...p}><path d="M3 6h18" /><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" /><path d="M6 6v14a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V6" /><path d="M10 11v6" /><path d="M14 11v6" /></S>
export const IconSearch = (p) => <S {...p}><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></S>
export const IconFilter = (p) => <S {...p}><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" /></S>
// Emisión (ondas saliendo de un punto) y no un ▶: la radio ARMA un set, no lo reproduce sola.
// Mismo criterio que el mockup de design/dj-radio (README, 2026-09-16).
export const IconRadio = (p) => <S {...p}><circle cx="12" cy="12" r="2" /><path d="M7.8 16.2a6 6 0 0 1 0-8.4" /><path d="M16.2 7.8a6 6 0 0 1 0 8.4" /><path d="M4.9 19.1a10 10 0 0 1 0-14.2" /><path d="M19.1 4.9a10 10 0 0 1 0 14.2" /></S>
export const IconPause = (p) => <S {...p} fill="currentColor" stroke="none"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></S>
export const IconPlayFill = (p) => <S {...p} fill="currentColor" stroke="none"><path d="M8 5v14l11-7z" /></S>
