# Sesión de ground truth — pasos exactos

Para la máquina que tiene el disco con la música y Rekordbox instalado. Asumí que no te
acordás de nada: seguí los pasos de arriba a abajo.

El resultado son unos CSV de pocos kilobytes. **Con esos CSV la evaluación corre en cualquier
computadora**, sin el disco y sin el audio.

---

## 1. Traer el repo y armar el entorno

```bash
git clone https://github.com/AgustinTomasMolina/BotMusicaDj.git
cd BotMusicaDj

python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux/Mac

pip install -r requirements-dev.txt
```

`requirements-dev.txt` incluye a `requirements.txt`, así que instala todo de una:
el stack de audio **con las versiones fijadas** (librosa, numpy, soundfile, mutagen)
más pytest y ruff.

> Las versiones del stack de audio van clavadas, no con `>=`. Otra versión de librosa
> puede devolver otro BPM para el mismo archivo, y entonces esta sesión no se puede
> comparar con la anterior.

Verificá que quedó bien antes de seguir:

```bash
python -m pytest          # tiene que dar todo en verde
```

Si `pytest` no existe, instalaste `requirements.txt` en vez de `requirements-dev.txt`.

---

## 2. Exportar el XML de Rekordbox

En Rekordbox, con la colección cargada:

**File → Export Collection in xml format**

Te pide dónde guardarlo. Guardalo como `Rekordbox.xml` en algún lado fácil de escribir
(el Escritorio sirve).

- Si no encontrás la opción en el menú File, mirá
  **Preferences → Advanced → Database → rekordbox xml**: ahí se configura la ruta del
  XML exportado.
- El XML tiene que salir de la colección **ya analizada por Rekordbox**. Los campos que
  importan son `AverageBpm`, `Tonality` y `TotalTime`: si un track no los trae, el
  comando lo cuenta aparte como "sin referencia" y no lo usa. No se inventa nada.

---

## 3. Enchufar el disco

Anotá la letra de unidad (`D:`, `E:`, …). Es lo que va en `--roots`.

Si la música está en más de un lugar, se pasan varias:
`--roots D:\ "E:\Musica vieja"`.

---

## 4. Pasada corta de prueba (5 minutos)

**Hacé esta primero, siempre.** Confirma que el XML resuelve contra el disco antes de
gastar horas.

```bash
python -m ground_truth.sesion --xml Rekordbox.xml --roots D:\ --salida gt_out --limit 20
```

Antes de analizar nada, imprime el recuento de resolución y pregunta si seguís:

```
RESOLUCIÓN — antes de analizar nada
  tracks en el XML            847
  sin BPM ni tonalidad         12   (no hay contra qué comparar)
  con audio encontrado        810   (96%)
  ambiguos (nombre repetido)   18   (se excluyen)
  sin archivo                   7
```

**Mirá ese número antes de decir que sí.** Si "con audio encontrado" da bajo, el problema
es la letra de unidad o la carpeta, no el motor: cortá, arreglá `--roots` y volvé a
empezar. Analizar con una resolución mala es tirar horas.

---

## 5. La pasada larga

Igual pero sin `--limit`:

```bash
python -m ground_truth.sesion --xml Rekordbox.xml --roots D:\ --salida gt_out
```

Analiza toda la muestra **dos veces**: una sin consenso de tonalidad y otra con consenso,
sobre exactamente los mismos tracks, para poder comparar.

**Se puede cortar.** Guarda el CSV después de cada track. Si se corta la luz, se cierra la
terminal o te vas a dormir, volvé a correr **el mismo comando** y retoma donde quedó, sin
reanalizar nada.

Para dejarlo corriendo solo, agregá `--si` (no pregunta).

---

## 6. Copiarse los resultados

Al terminar, imprime qué archivos hay que llevarse y cuánto pesan. Están todos en
`gt_out/`:

| archivo | qué es |
|---|---|
| `rekordbox_tracks.csv` | el ground truth tal como salió del XML |
| `rekordbox_cues.csv` | los cues de cada track |
| `analisis_sin-consenso.csv` | qué midió el motor con `tono()` |
| `analisis_con-consenso.csv` | qué midió el motor con `tono_consenso()` |
| `evaluacion_<fecha>_sin-consenso.csv` | el cruce contra el ground truth, track por track |
| `evaluacion_<fecha>_con-consenso.csv` | ídem, con consenso |
| `resumen.json` | los totales de la corrida |

Son kilobytes. Copiá la carpeta entera.

---

## 7. En la otra máquina

La etapa B no necesita audio:

```bash
python -m benchmark.evaluar --analisis gt_out/analisis_con-consenso.csv \
                            --ground-truth gt_out/rekordbox_tracks.csv
```

---

## Si falla

| mensaje | qué pasó |
|---|---|
| `No existe el XML` | la ruta del `--xml` está mal, o no lo exportaste todavía |
| `El XML no trae ningún <TRACK>` | exportaste una colección vacía |
| `Ninguno de los N tracks resolvió a un archivo real` | el disco no está enchufado, o `--roots` apunta a otra letra de unidad |
| `No module named pytest` | instalaste `requirements.txt` en vez de `requirements-dev.txt` |

Ninguno de estos se arregla solo ni sigue de largo: el comando corta. Un ground truth
inventado es peor que no tener ground truth.
