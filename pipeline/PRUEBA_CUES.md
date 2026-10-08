# Prueba: ¿Rekordbox toma nuestros cues y loops? (tarea 5.68)

Nunca se comprobó contra un Rekordbox real que acepte el XML que escribimos. Esta prueba
lo hace con **un solo track** y cuatro marcas, para saber cómo escribir los cues que
después se marquen en Radio DJ. El pipeline sigue **sin** escribir cues: esto es aparte.

## Antes de empezar: elegí bien el track

> **Usá una copia o un track que no te importe.** Si el track ya está en tu colección de
> Rekordbox con cues, importar el XML puede **pisarlos o mezclarlos** con los de la prueba.
> No hagas la prueba sobre un track con cues valiosos.

- Tiene que durar **más de 94 s** (el loop termina en 1:34). Si es más corto, el script avisa y
  no escribe nada.
- Lo ideal: un archivo que **no** esté en tu colección. Si querés probar también qué pasa
  con cues existentes, hacé una copia del archivo, importala en Rekordbox, marcale un par de
  cues a mano, sacales una captura, y recién ahí seguí.

## 1. Generar el XML

Desde la carpeta del repo, en PowerShell:

```powershell
.venv\Scripts\python.exe -m pipeline.prueba_cues_xml --audio "C:\ruta\al\track.mp3" --salida "C:\ruta\fuera\del\repo\prueba_cues.xml"
```

- El audio **no se modifica**: solo se lee para medir cuánto dura.
- `--salida`: guardalo **fuera del repo** (el XML lleva la ruta de tu archivo).
- Opcional: `--db "$env:USERPROFILE\.djradio\biblioteca.sqlite"` agrega BPM y tonalidad si el
  track está analizado por el motor. La base solo se lee; si su versión es vieja, el script
  no la abre. Sin `--db`, el XML no lleva BPM ni tonalidad (no se inventan).
- Los segundos se pueden cambiar: `--hot1 30 --hot2 60 --memory 15 --loop 90 --loop-largo 4`
  y el nombre del primero con `--nombre-hot1 "..."`.

El script imprime las cuatro marcas que escribió. Deberían ser estas:

| Marca | Type | Num | Start | End | Name | Color (R,G,B) |
|---|---|---|---|---|---|---|
| hot cue 1 (A) | 0 | 0 | 30.000 | — | `PRUEBA hot 1` | rojo (230,40,40) |
| hot cue 2 (B) | 0 | 1 | 60.000 | — | *(vacío)* | azul (48,90,255) |
| memory cue | 0 | -1 | 15.000 | — | `PRUEBA memory` | *(sin color)* |
| loop en hot cue 3 (C) | 4 | 2 | 90.000 | 94.000 | `PRUEBA loop 4s` | naranja (255,160,0) |

## 2. Importarlo en Rekordbox

1. **Preferencias → Avanzado → rekordbox xml** (en algunas versiones está dentro de la
   pestaña *Base de datos*). En **"Biblioteca importada"** elegí la ruta del `prueba_cues.xml`.
2. Si en el panel de la izquierda no aparece el árbol **"rekordbox xml"**: Preferencias →
   Vista → Layout, y tildá *rekordbox xml*.
3. En el árbol: **rekordbox xml → Playlists → MusiFlix** (o *Todos los tracks*). Ahí está el
   track, con el nombre del archivo.
4. **Arrastrá el track a la Colección** (o clic derecho → *Importar a la colección*). Si
   Rekordbox pregunta si reemplaza la información de un track que ya existe, **anotá la
   pregunta textual** y qué elegiste.

## 3. Qué mirar

Cargá el track en un deck y mirá la lista de hot cues y memory cues:

- [ ] **Hot cue A** en **0:30.000**, nombre `PRUEBA hot 1`, **rojo**.
- [ ] **Hot cue B** en **1:00.000**, sin nombre, **azul**.
- [ ] **Memory cue** en **0:15.000**, nombre `PRUEBA memory`.
- [ ] **Hot cue C como loop** de **1:30.000 a 1:34.000** (4 s), nombre `PRUEBA loop 4s`,
      **naranja**. Que sea un *loop* (al dispararlo, repite) y no un cue suelto.
- [ ] El segundo exacto: poné la aguja en cada marca y mirá el tiempo en pantalla.
- [ ] Si el track ya tenía cues: ¿siguen, se borraron o quedaron mezclados con los nuevos?
- [ ] Secundario: el BPM y la tonalidad que muestra (si generaste con `--db`), y si
      Rekordbox reanalizó el track solo.

## 4. El paso que decide: exportar de vuelta

Esto es lo más valioso de la prueba. **Archivo → Exportar colección en formato xml**,
guardalo fuera del repo, abrilo con el Bloc de notas, buscá el nombre del track y **copiá
sus líneas `<POSITION_MARK .../>`** al cuadro de abajo. Así vemos cómo escribe Rekordbox
*sus propias* marcas, que es la verdad, no la especificación.

## 5. Resultado (2026-10-08, PC de casa)

Versión de Rekordbox: **7.2.16** · Fecha: **2026-10-08** · ¿El track ya estaba en la colección?
**No**: se usó una COPIA de un track en una carpeta aparte (el original no se tocó).

| Marca | Escribimos Type / Num / Start / End | ¿Apareció? | Qué mostró Rekordbox | Cómo la reexportó (Type / Num / Start / End) |
|---|---|---|---|---|
| hot cue 1 | 0 / 0 / 30.000 / — | sí | pad A, "PRUEBA hot 1", rojo | 0 / 0 / 30.000 / — · nombre y color iguales |
| hot cue 2 | 0 / 1 / 60.000 / — | sí | pad B, sin nombre (muestra "01:00"), azul | 0 / 1 / 60.000 / — · color igual |
| memory cue | 0 / -1 / 15.000 / — | sí | triángulo rojo sobre la onda, al principio | 0 / -1 / 15.000 / — · nombre igual |
| loop | 4 / 2 / 90.000 / 94.000 | sí | pad C con el ícono de loop, "PRUEBA loop 4s", naranja | 4 / 2 / 90.000 / 94.000 · nombre y color iguales |

**El reexport es idéntico a lo que escribimos**, atributo por atributo. Rekordbox además analizó
el track solo (147.00 BPM, Ebm): el XML de la prueba no llevaba BPM ni tonalidad.

Cues que el track ya tenía: no aplica (era una copia nueva).

### Conclusión

Vale la **especificación pública**: `Type` 0 = cue, 4 = loop (con `End`); `Num` -1 = memory,
0-7 = hot cue A-H. Lo confirma también el XML real de la biblioteca (export de Rekordbox 7.2.16):
las 243 marcas Type 4 traen `End` (0,75-6,96 s, mediana 3,2 s). La lectura de
`ground_truth/cues.py` ("Type=4, Num=0, sin nombre" = memory cue anónimo) era incorrecta: son
loops en el hot cue A. Corregido en f47: el parser lee `End` y `cues.clase()` clasifica cada
marca con esta lectura.

## La duda que esta prueba resolvió

Había dos lecturas de `Type` y `Num` que se contradecían:

- **Especificación pública del XML de Rekordbox:** `Type` 0 = cue, 1 = fade-in, 2 = fade-out,
  3 = load, 4 = loop; `Num` -1 = memory cue, 0-7 = hot cues A-H; el loop lleva `End`.
  El XML de esta prueba sigue esta lectura.
- **Lo que suponía `ground_truth/cues.py`:** `Type=4, Num=0, sin nombre` = "memory cue
  anónimo", y el parser no leía `End`.

**Resultado (§5): vale la especificación.** `ground_truth` ya se corrigió: el parser lee `End`
y `cues.clase()` clasifica cada marca con esta lectura.

## Al terminar

Borrá las marcas de prueba del track en Rekordbox (o el track de la colección, si era una
copia) y, si querés, sacá la ruta del XML de Preferencias → Avanzado → rekordbox xml.
