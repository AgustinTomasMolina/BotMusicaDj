# Spec — DJ Radio

Documento de referencia del proyecto. Define qué se está construyendo, en qué orden, y
bajo qué reglas. El roadmap operativo vive en Notion (base **DJ Radio — Roadmap**).

**Decisión de encuadre:** herramienta propia primero, web pública después. Dedicación
real: menos de 5 h/semana.

---

## 0. Perfil de uso real (define todo lo demás)

- Controladora Pioneer DDJ-400, 2 canales. Toda transición es A → B.
- **Cada tema suena ~1:30**, con solape de hasta ~1 minuto. Son 30-40 tracks por hora,
  no 12-14. De cada track se usa un **fragmento**, no el tema entero.
- Toca la noche entera, subiendo de a poco. No se especializa en apertura ni en peak time.
- Está arrancando como DJ.

**Dónde se le va el tiempo hoy** (esto reordenó el proyecto entero):
buscar el track → encontrar dónde bajarlo gratis → verificarlo con Spek → organizarlo en
iTunes y Rekordbox → generar los cues.

Eso no es armar sets: es una línea de producción de tracks. El motor de radio resuelve el
último paso de un proceso cuyo cuello de botella está cuatro pasos antes.

---

## 1. Objetivos

### Objetivo primario — bajar el tiempo de preparación de un track

De carpeta de descargas a track listo para pinchar, sin trabajo manual repetitivo.

- **Métrica:** una carpeta de 20 descargas queda procesada (calidad verificada,
  duplicados marcados, tags, BPM, tonalidad y cues) en menos de 10 minutos de atención,
  contra la hora larga que lleva hoy.
- **Horizonte:** ~2 meses.

### Objetivo secundario — que yo arme sets reales con el motor

La radio sirve cuando puedo preparar un set y aceptar la mayoría de lo que propone.

- **Métrica:** en 3 sets reales, ≥60% de las transiciones propuestas se usan sin cambiar.
- **Horizonte:** ~5 meses.

> El pipeline va primero **por dependencia, no por preferencia**: si el BPM está mal por 3
> unidades y la tonalidad falla en el 30% de los temas, la radio propone transiciones
> imposibles por más fino que esté el algoritmo. El pipeline es el piso del motor.

### Objetivo terciario — que la use un segundo DJ

Valida que no está sobreajustado a mi gusto y a mi género.

- **Métrica:** otro DJ lo instala solo y da feedback sobre 20 transiciones de su
  biblioteca.
- **Horizonte:** ~7 meses.

### Objetivo cuaternario — la web pública

Solo si el terciario se cumple. Si nadie más lo usa, la web no tiene sentido todavía, y
decidir eso a tiempo es un éxito, no un fracaso.

### Anti-objetivos

No se construye, hasta que el pipeline y el motor sirvan: cuentas de usuario, agregador de
catálogo, frontend de verdad, recomendación colaborativa, sistema de votos, ni nada que
diga "escalar". Cada una es una forma cómoda de sentir que se avanza sin resolver el
problema difícil.

---

## 2. Fases

| Fase | Qué resuelve | Esfuerzo | Calendario a <5 h/sem |
|---|---|---|---|
| **F0 Auditoría** | Un repo, ground truth desde Rekordbox, benchmark | ~14 h | 3 semanas |
| **F0.5 Pipeline de ingesta** | Calidad, duplicados, tags, cues, salidas | ~22 h | 5 semanas |
| **F1 Motor confiable** | BPM y tonalidad que no mienten, scoring multiplicativo | ~26 h | 7 semanas |
| **F2 Herramienta usable** | Export, preescucha, puente entre tracks, 3 sets reales | ~28 h | 7 semanas |
| **F3 Segundo usuario** | Empaquetado, prueba externa, punto de corte | ~14 h | 4 semanas |
| **F4 Web pública** | Licencias, ingesta, pgvector, API, frontend | ~60 h+ | 4+ meses |

Pipeline andando: **~2 meses**. Radio usable: **~5 meses**. Segundo usuario: **~7 meses**.

**La tarea que desbloquea todo es el XML de Rekordbox.** Trae BPM, tonalidad, beatgrid,
cue points y contadores de reproducción de la biblioteca entera, gratis, en 10 minutos.
Es ground truth para el motor y, en el caso de los cues, ground truth de estructura que no
tiene nadie más. Rekordbox se equivoca bastante en tonalidad: es baseline, no verdad.

---

## 3. El pipeline (F0.5)

Se le apunta a la carpeta de descargas y devuelve:

1. **Verificación de calidad real** — corte espectral contra bitrate declarado. Un 320
   real llega a ~20 kHz; un 128 inflado se corta en ~16. Es el chequeo de Spek,
   automatizado. Bandera para revisar, no veredicto: hay masterizaciones con corte bajo a
   propósito y rips de vinilo que dan raro.
2. **Duplicados** — el mismo track con nombres distintos. Se conserva el de mejor calidad
   *medida*, no el de mejor bitrate declarado.
3. **Análisis** — BPM, tonalidad, energía.
4. **Estructura y cues automáticos** — intro mixeable, breakdown, drop, outro, y el
   fragmento de 90 segundos más fuerte. Central por la rotación de 1:30.
5. **Tags ID3 normalizados.**
6. **Salidas configurables:** carpeta de "Añadir automáticamente a iTunes" (para seguir
   ordenando ahí, decisión tomada: iTunes no se saca del flujo) y XML importable por
   Rekordbox. Los cues viajan solo por el XML; iTunes no los transporta.
7. **Reporte** — "18 archivos, 3 truchos, 2 duplicados, 13 listos".

---

## 4. Umbrales de calidad del motor

Son el contrato. Un cambio que rompe cualquiera de estos no entra.

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

**Regla de proceso:** ningún cambio en features o scoring se mergea sin correr el
benchmark antes y después y pegar los dos números en el commit.

**Regla de oro — el oído gana.** Si el benchmark mejora pero el set suena peor, el
benchmark está mal: se documenta el caso, se agrega al set de referencia y se corrige la
métrica. Las métricas capturan el criterio del oído, no lo reemplazan.

---

## 5. Reglas de ingeniería

**Antes de opinar**

- Leer el código y citar archivo y línea. Nada de "probablemente estés usando".
- No asumir que librosa o essentia hacen lo que dice la doc. Verificar contra ground
  truth. Este proyecto ya se comió un bug de 3 BPM que la documentación no menciona.

**Al cambiar cosas**

- Cambios quirúrgicos, no reescrituras de módulo.
- Todo cambio de scoring viene con el número del benchmark antes y después.
- Determinismo: con `randomness=0` y la misma semilla, la radio da siempre lo mismo.
  Hay un test que lo verifica.
- Nunca inventar BPM ni tonalidad en un test: salen del ground truth o del generador de
  audio sintético.
- Nada de dependencias pesadas (torch, tensorflow) sin justificar y preguntar. El
  proyecto tiene que seguir instalándose con `pip install -r requirements.txt`.

**Cosas que no se tocan**

- Nunca modificar ni pisar los archivos de audio originales. El pipeline escribe copias
  procesadas en un destino aparte.
- `licencia` y `origen` son obligatorios en cualquier modelo de track desde el primer
  día. Retrofitear licencias sobre miles de tracks ya cargados es imposible en la
  práctica.
- El embedding tiene que seguir siendo intercambiable: cualquier backend devuelve un
  `np.ndarray` 1-D y el resto del sistema no se entera.

**Idioma:** docs y comentarios en español, nombres de código en inglés.

---

## 6. Reglas de UX para DJs

Premisa: **un dato que miente es peor que un dato ausente.** Un DJ que se confía de un
BPM equivocado se entera arriba de la cabina.

**Innegociable en pantalla, en cada track**

- BPM con un decimal. Redondear a entero es mentir.
- Camelot *y* tonalidad clásica (8A y Am): cada uno lee la que sabe.
- Duración, artista y licencia. La licencia siempre visible, no en un modal.
- **Calidad real medida**, no la que dice el tag.

**Honestidad de la detección**

- Confianza baja se muestra atenuada o con `?`. Nunca un valor dudoso presentado como
  seguro.
- La radio muestra **por qué** eligió cada track: `+1.8% BPM | 8A → 9A (vecino)`. Una
  recomendación sin explicación no genera confianza, y sin confianza no se usa.

**Fricción**

- Preescucha en 1 click, sin registro. Descarga en 2 clicks, sin gate de mail.
- Export a M3U8 siempre disponible, sin login.
- Nunca autoplay con volumen alto.

Todo lo que el DJ necesita para decidir tiene que estar visible sin abrir el track.

---

## 7. Decisiones de arquitectura ya tomadas

- **Scoring multiplicativo, no aditivo.** `score = encaje_musical × mezclabilidad`.
  Sumando criterios, un track parecidísimo compensa estar fuera de tempo y entra igual.
  Multiplicando, la mezclabilidad es compuerta. `mezclabilidad = bpm^1.0 × key^0.6`: el
  BPM pesa más porque un choque de tonalidad se tapa con EQ, pero un tema fuera de tempo
  no se mezcla.
- **BPM refinado por autocorrelación.** `beat_track` cuantiza a la grilla de frames:
  ~3 BPM de error a 150 BPM con `hop=512`. Se corrige buscando en grilla fina el período
  que mejor explica el pulso y sus múltiplos.
- **Energía como percentil de la biblioteca**, no valor absoluto. Baja de prioridad: con
  rotación de 1:30 la curva se maneja tema a tema en vivo.
- **Chroma relativo** (rotado a la tónica) en el embedding, para capturar carácter
  armónico sin depender de la tonalidad absoluta.
- **Content-based primero, colaborativo después.** Sin usuarios no hay señales implícitas;
  el content-based es lo que resuelve el cold start. Es el orden correcto, no un atajo.
- **Sin sistema de votos.** El matching es por contenido, así que el proyecto no tiene
  problema de arranque en frío; agregar votos le sumaría una debilidad que el diseño ya
  evita. Si hace falta señal social, se registran **acciones** (lo descargó, lo agregó al
  crate, lo pinchó), que son hechos y no opiniones, y salen gratis de lo que el usuario ya
  hace.
- **iTunes queda en el flujo** como destino opcional del pipeline, porque ahí se ordena
  la colección. Los cues van por el XML de Rekordbox en paralelo.

Detalle completo del motor en `claude/proyecto-web-djs-motor-radio.md`.
