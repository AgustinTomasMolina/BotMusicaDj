# CLAUDE.md — DJ Radio

Herramienta propia para DJs: **pipeline que procesa una carpeta de descargas** (calidad,
duplicados, BPM, tonalidad, cues, tags, salidas a iTunes/Rekordbox) + **motor de
recomendación tipo radio** para armar sets. Primero herramienta propia, después web pública.

**Fase actual: F0 (Auditoría) / arranque.** El repo hoy resuelve parte de la *adquisición*
de tracks (búsqueda multi-plataforma, verificación Spek, tags, export .m3u8/iTunes), pero el
**pipeline formal (F0.5), el motor (F1) y el benchmark (F0) todavía no existen**. El estado
operativo vivo está en Notion: base **DJ Radio — Roadmap** (columnas Fase/Estado).

## Documentos (`claude/`)
- **`spec-dj-radio.md`** — documento maestro. Objetivos, fases, umbrales, reglas. **Leerlo primero.**
- **`proyecto-web-djs-motor-radio.md`** — detalle del motor de radio y la arquitectura web.
- Referencia personal de música (no del software): `kick-y-acompanamiento-techno-150.md`
  (producción del kick), `ruta-teoria-musical-desde-cero.md` (teoría), `plan-ia-produccion-12-semanas.md`
  (plan de producción). Consultar solo si la tarea toca producción/teoría musical.

## Umbrales de calidad del motor (spec §4 — son el contrato)
Un cambio que rompe cualquiera de estos no entra.

| Métrica | Umbral |
|---|---|
| Error de BPM (p95) | ≤ 1.0 BPM, tolerando ambigüedad de octava |
| Tonalidad exacta | ≥ 85% |
| Tonalidad compatible (exacta, relativo o vecino) | ≥ 95% |
| Transiciones fuera de ±8% de BPM | 0% |
| Choques armónicos (compatibilidad < 0.4) | ≤ 10% |
| Curva de energía (Spearman posición vs energía) | ≥ 0.5 |
| Tiempo de análisis | ≤ 10 s/track |
| Latencia de la radio | < 200 ms con 10k tracks |

## Reglas de ingeniería (spec §5 — textuales)

> Cómo se escriben los tests: **`CONTRIBUTING.md`**. Regla corta — un test que solo
> verifica que algo no explotó no protege de nada (existencia, longitud, `is not None`,
> "el parser no tiró excepción"), y un test reforzado que no verificaste que falla es
> otro test ciego.

**Antes de opinar**
- Leer el código y citar archivo y línea. Nada de "probablemente estés usando".
- No asumir que librosa o essentia hacen lo que dice la doc. Verificar contra ground truth.
  Este proyecto ya se comió un bug de 3 BPM que la documentación no menciona.

**Al cambiar cosas**
- Cambios quirúrgicos, no reescrituras de módulo.
- Todo cambio de scoring viene con el número del benchmark antes y después.
- Determinismo: con `randomness=0` y la misma semilla, la radio da siempre lo mismo. Hay un test que lo verifica.
- Nunca inventar BPM ni tonalidad en un test: salen del ground truth o del generador de audio sintético.
- Nada de dependencias pesadas (torch, tensorflow) sin justificar y preguntar. El proyecto
  tiene que seguir instalándose con `pip install -r requirements.txt`.

**Cosas que no se tocan**
- Nunca modificar ni pisar los archivos de audio originales. El pipeline escribe copias procesadas en un destino aparte.
- `licencia` y `origen` son obligatorios en cualquier modelo de track desde el primer día.
- El embedding tiene que seguir siendo intercambiable: cualquier backend devuelve un `np.ndarray` 1-D y el resto del sistema no se entera.

## Regla de oro — el oído gana
**Si el benchmark mejora pero el set suena peor, el benchmark está mal.** Se documenta el
caso, se agrega al set de referencia y se corrige la métrica. Las métricas capturan el
criterio del oído, no lo reemplazan.

## UX (spec §6): un dato que miente es peor que un dato ausente
- BPM **con un decimal** (redondear a entero es mentir). Camelot *y* tonalidad clásica.
  Licencia y calidad real medida siempre visibles. Confianza baja → atenuado o `?`.
- La radio muestra **por qué** eligió cada track: `+1.8% BPM | 8A → 9A (vecino)`.

## Convenciones
- **Idioma:** docs y comentarios en **español**, nombres de código en **inglés**.
- **Cómo hablarme:** explicaciones paso a paso, prácticas, sobre lo que veo en pantalla.
  Soy principiante en **producción musical**, no en programación (en código, hablá técnico).
