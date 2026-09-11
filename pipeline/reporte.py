"""Reporte HTML de revisión. UN archivo, autocontenido, sin servidor ni red.

Abre con doble clic y anda offline. NO es un frontend: es un reporte. Sin framework, sin
build, sin nada que instalar — el CSS y el JS van embebidos y el JS solo hace dos cosas:
recordar las decisiones y bajarlas como JSON (Blob + download).

Lo que muestra cada fila sale de la spec §6, que pide que esté visible SIN abrir nada:
  · artista y título ya normalizados
  · duración
  · calidad REAL MEDIDA (corte en kHz + bandera), nunca el bitrate declarado
  · BPM con UN decimal — redondear a entero es mentir
  · tonalidad en Camelot Y en clásica, las dos
  · confianza baja → el valor va atenuado y con '?', nunca presentado como seguro
  · el diff de tags antes → después, colapsable
"""
import html
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

# Por debajo de esto la tonalidad se muestra atenuada y con '?'. `tono_consenso` devuelve
# la fracción de tramos que coinciden: 0.67 = dos de tres. Un solo tramo discrepando ya
# es motivo para no presentarla como segura.
CONFIANZA_MINIMA = 0.67

APROBADO, DESCARTADO, PENDIENTE = "aprobado", "descartado", "pendiente"


@dataclass
class Fila:
    """Un track en el reporte."""

    archivo: str
    ruta_staging: str
    ruta_original: str
    artista: str
    titulo: str
    duracion_s: float
    corte_khz: float
    bandera: str
    muro_db: float
    bpm: float
    camelot: str
    clasica: str
    confianza: float
    acuerdo: str
    grupo_id: int = 0            # 0 = no está en ningún grupo de duplicados
    accion_duplicado: str = ""   # conservar | descartar, dentro del grupo
    cambios: list = field(default_factory=list)   # (campo, antes, despues, motivo)
    estado: str = PENDIENTE


def estado_por_defecto(bandera: str, grupo_id: int) -> str:
    """Aprobado solo lo que no necesita criterio.

    Sospechoso o duplicado → PENDIENTE. Aprobar por defecto algo que necesita una decisión
    humana convierte la revisión en un trámite, que es justo lo que esta pantalla evita.
    """
    if bandera == "sospechoso" or grupo_id:
        return PENDIENTE
    return APROBADO


def _mmss(seg: float) -> str:
    m, s = divmod(int(seg or 0), 60)
    return f"{m}:{s:02d}"


def _url_local(ruta: str) -> str:
    """file:// para el <audio>. Windows usa backslash y hay que escapar espacios."""
    p = Path(ruta).resolve().as_posix()
    return "file:///" + quote(p, safe="/:")


_CSS = """
:root{--bg:#14161a;--card:#1d2026;--línea:#2b2f38;--txt:#e8eaee;--suave:#9aa2b1;
--ok:#4ade80;--sospecha:#fbbf24;--dup:#60a5fa;--mal:#f87171}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:var(--bg);color:var(--txt);
font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--suave);margin-bottom:20px}
.resumen{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}
.chip{background:var(--card);border:1px solid var(--línea);border-radius:999px;
padding:6px 14px}
.chip b{font-size:16px}
section{margin-bottom:28px}
h2{font-size:15px;margin:0 0 10px;color:var(--suave);text-transform:uppercase;
letter-spacing:.06em}
.track{background:var(--card);border:1px solid var(--línea);border-radius:10px;
padding:12px 14px;margin-bottom:8px}
.track.sospechoso{border-left:3px solid var(--sospecha)}
.track.dup{border-left:3px solid var(--dup)}
.cab{display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
.tit{font-weight:600}
.art{color:var(--suave)}
.vacio{color:var(--mal);font-style:italic}
.datos{display:flex;gap:16px;flex-wrap:wrap;margin:8px 0;color:var(--suave);
font-variant-numeric:tabular-nums}
.datos b{color:var(--txt);font-weight:600}
.dudoso{opacity:.55}
.badge{border-radius:4px;padding:1px 7px;font-size:12px}
.b-ok{background:rgba(74,222,128,.14);color:var(--ok)}
.b-sos{background:rgba(251,191,36,.14);color:var(--sospecha)}
.grupo{border:1px dashed var(--dup);border-radius:10px;padding:10px;margin-bottom:12px}
.grupo>.et{color:var(--dup);font-size:12px;margin-bottom:8px}
audio{height:32px;vertical-align:middle}
details{margin-top:6px}
summary{cursor:pointer;color:var(--suave);font-size:13px}
table.diff{border-collapse:collapse;margin-top:6px;font-size:13px;width:100%}
table.diff td{border-top:1px solid var(--línea);padding:3px 8px;vertical-align:top}
table.diff td:first-child{color:var(--suave);white-space:nowrap}
.acciones{display:flex;gap:4px;margin-top:8px}
.acciones label{border:1px solid var(--línea);border-radius:6px;padding:4px 12px;
cursor:pointer;font-size:13px;color:var(--suave)}
.acciones input{display:none}
.acciones input:checked+span{color:var(--txt);font-weight:600}
.acciones label:has(input[value=aprobado]:checked){border-color:var(--ok);
background:rgba(74,222,128,.1)}
.acciones label:has(input[value=descartado]:checked){border-color:var(--mal);
background:rgba(248,113,113,.1)}
.acciones label:has(input[value=pendiente]:checked){border-color:var(--sospecha);
background:rgba(251,191,36,.1)}
.barra{position:sticky;bottom:0;background:var(--card);border:1px solid var(--línea);
border-radius:10px;padding:12px 16px;display:flex;gap:16px;align-items:center;
margin-top:20px}
button{background:var(--ok);color:#0b0d10;border:0;border-radius:8px;padding:9px 18px;
font-weight:700;cursor:pointer;font-size:14px}
.nota{color:var(--suave);font-size:13px;border-left:2px solid var(--línea);
padding-left:10px;margin:16px 0}
"""

_JS = """
function contar(){
  const c={aprobado:0,descartado:0,pendiente:0};
  document.querySelectorAll('input[type=radio]:checked').forEach(r=>c[r.value]++);
  document.getElementById('n-aprobado').textContent=c.aprobado;
  document.getElementById('n-descartado').textContent=c.descartado;
  document.getElementById('n-pendiente').textContent=c.pendiente;
}
document.addEventListener('change',e=>{if(e.target.type==='radio')contar()});
// El <audio> arranca a volumen medio: esto se revisa con auriculares puestos.
document.addEventListener('play',e=>{
  e.target.volume=0.5;
  document.querySelectorAll('audio').forEach(a=>{if(a!==e.target)a.pause()});
},true);
function bajar(){
  const decisiones=[];
  document.querySelectorAll('.track').forEach(t=>{
    const r=t.querySelector('input[type=radio]:checked');
    decisiones.push({archivo:t.dataset.archivo,ruta_staging:t.dataset.staging,
      artista:t.dataset.artista,titulo:t.dataset.titulo,grupo_id:+t.dataset.grupo,
      estado:r?r.value:'pendiente'});
  });
  const doc={version:1,generado:new Date().toISOString(),
    staging:document.body.dataset.staging,decisiones:decisiones};
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([JSON.stringify(doc,null,2)],
    {type:'application/json'}));
  a.download='decisiones.json'; a.click(); URL.revokeObjectURL(a.href);
}
contar();
"""


def _dato_key(f: Fila) -> str:
    """Camelot + clásica juntas. Atenuado y con '?' si la confianza es baja (§6)."""
    if not f.camelot or f.camelot == "?":
        return '<span class="dudoso">key ?</span>'
    par = f"{html.escape(f.camelot)} · {html.escape(f.clasica)}" if f.clasica else html.escape(f.camelot)
    if f.confianza < CONFIANZA_MINIMA:
        acuerdo = f" {html.escape(f.acuerdo)}" if f.acuerdo else ""
        return (f'<span class="dudoso" title="confianza {f.confianza:.2f}{acuerdo}">'
                f'{par} ?</span>')
    return f"<b>{par}</b>"


def _fila_html(f: Fila) -> str:
    clase = "track" + (" sospechoso" if f.bandera == "sospechoso" else "") + (" dup" if f.grupo_id else "")
    titulo = html.escape(f.titulo) if f.titulo else '<span class="vacio">(sin título)</span>'
    artista = html.escape(f.artista) if f.artista else '<span class="vacio">(sin artista)</span>'
    badge = ('<span class="badge b-sos">sospechoso</span>' if f.bandera == "sospechoso"
             else '<span class="badge b-ok">ok</span>')

    diff = ""
    if f.cambios:
        filas = "".join(
            f"<tr><td>{html.escape(c[0])}</td><td>{html.escape(str(c[1]) or '—')}</td>"
            f"<td>→ {html.escape(str(c[2]) or '—')}</td>"
            f"<td style='color:var(--suave)'>{html.escape(str(c[3]))}</td></tr>"
            for c in f.cambios)
        diff = (f"<details><summary>{len(f.cambios)} cambios en los tags</summary>"
                f"<table class='diff'>{filas}</table></details>")

    dup = ""
    if f.grupo_id and f.accion_duplicado:
        dup = f' <span class="badge b-sos">duplicado: {html.escape(f.accion_duplicado)}</span>'

    radios = "".join(
        f'<label><input type="radio" name="e-{html.escape(f.archivo)}" value="{v}"'
        f'{" checked" if f.estado == v else ""}><span>{t}</span></label>'
        for v, t in ((APROBADO, "aprobar"), (DESCARTADO, "descartar"), (PENDIENTE, "pendiente")))

    return f"""<div class="{clase}" data-archivo="{html.escape(f.archivo)}"
 data-staging="{html.escape(f.ruta_staging)}" data-artista="{html.escape(f.artista)}"
 data-titulo="{html.escape(f.titulo)}" data-grupo="{f.grupo_id}">
  <div class="cab"><span class="tit">{titulo}</span><span class="art">{artista}</span>
    {badge}{dup}</div>
  <div class="datos">
    <span>{_mmss(f.duracion_s)}</span>
    <span>BPM <b>{f.bpm:.1f}</b></span>
    <span>{_dato_key(f)}</span>
    <span>corte <b>{f.corte_khz:.1f} kHz</b></span>
    <span>muro {f.muro_db:.0f} dB</span>
  </div>
  <audio controls preload="none" src="{_url_local(f.ruta_staging)}"></audio>
  {diff}
  <div class="acciones">{radios}</div>
</div>"""


def _seccion(titulo: str, filas: list[Fila], abierta: bool = True) -> str:
    if not filas:
        return ""
    cuerpo = "".join(_fila_html(f) for f in filas)
    if abierta:
        return f"<section><h2>{html.escape(titulo)}</h2>{cuerpo}</section>"
    return (f"<section><details><summary><h2 style='display:inline'>"
            f"{html.escape(titulo)}</h2></summary>{cuerpo}</details></section>")


def _seccion_grupos(grupos: dict[int, list[Fila]]) -> str:
    if not grupos:
        return ""
    bloques = []
    for gid, filas in sorted(grupos.items()):
        cuerpo = "".join(_fila_html(f) for f in filas)
        bloques.append(f'<div class="grupo"><div class="et">grupo {gid} — '
                       f'{len(filas)} archivos con el mismo audio</div>{cuerpo}</div>')
    return f"<section><h2>Duplicados</h2>{''.join(bloques)}</section>"


def generar(filas: list[Fila], staging: Path, destino: Path) -> Path:
    """Escribe el HTML. Devuelve la ruta."""
    grupos: dict[int, list[Fila]] = {}
    sospechosos, resto = [], []
    for f in filas:
        if f.grupo_id:
            grupos.setdefault(f.grupo_id, []).append(f)
        elif f.bandera == "sospechoso":
            sospechosos.append(f)
        else:
            resto.append(f)

    n = {APROBADO: 0, DESCARTADO: 0, PENDIENTE: 0}
    for f in filas:
        n[f.estado] = n.get(f.estado, 0) + 1

    cuerpo = (
        _seccion_grupos(grupos)
        + _seccion("Sospechosos — calidad para revisar", sospechosos)
        + _seccion(f"Sin novedad ({len(resto)})", resto, abierta=False)
    )

    doc = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>MusiFlix — revisión</title><style>{_CSS}</style></head>
<body data-staging="{html.escape(str(staging))}">
<h1>Revisión antes de mandar a iTunes</h1>
<div class="sub">{len(filas)} tracks · las copias procesadas están en
 <code>{html.escape(str(staging))}</code> · los originales no se tocaron</div>
<div class="resumen">
  <span class="chip">aprobados <b id="n-aprobado">{n[APROBADO]}</b></span>
  <span class="chip">pendientes <b id="n-pendiente">{n[PENDIENTE]}</b></span>
  <span class="chip">descartados <b id="n-descartado">{n[DESCARTADO]}</b></span>
  <span class="chip">duplicados <b>{sum(len(v) for v in grupos.values())}</b></span>
</div>
<div class="nota">Los CUE POINTS viajan <b>solo por el XML de Rekordbox</b>. iTunes no los
transporta: si importás por iTunes, los cues no llegan. Por eso <code>aplicar</code>
escribe las dos salidas.</div>
{cuerpo}
<div class="barra">
  <button onclick="bajar()">Bajar decisiones.json</button>
  <span class="sub" style="margin:0">Después:
   <code>python -m pipeline.aplicar --decisiones decisiones.json</code></span>
</div>
<script>{_JS}</script>
</body></html>"""

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(doc, encoding="utf-8")
    return destino


def filas_a_json(filas: list[Fila]) -> str:
    """Las mismas filas en JSON, por si se quiere procesar sin pasar por el HTML."""
    return json.dumps([asdict(f) for f in filas], ensure_ascii=False, indent=1)
