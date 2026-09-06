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
export const IconEye = (p) => <S {...p}><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" /></S>
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
