# Cómo se escriben los tests acá

Complemento de la spec §5 (`claude/spec-dj-radio.md:137`). Esas reglas son textuales del
documento maestro; esta salió de romper cosas en este repo y es igual de obligatoria.

---

## Un test que solo verifica que algo no explotó no protege de nada

Si la aserción pasa con datos equivocados, el test no está probando el comportamiento:
está probando que el intérprete no tiró una excepción. Eso ya lo sabés por el exit code.

**Formas que no alcanzan, aunque parezcan tests:**

| aserción | qué prueba de verdad |
|---|---|
| `assert resultado is not None` | que la función retornó |
| `assert len(filas) == 3` | que salieron tres filas, no que sean las correctas |
| `assert archivo.exists()` | que se escribió un archivo, no que sirva |
| `assert "TPE1" in tags` | que el campo existe, no qué dice |
| `parsear(x)` sin assert | que el parser no tiró excepción |
| `assert d.is_dir()` | que hay un directorio, no que sea el pedido |

**Lo que sí:** comparar el VALOR contra lo que se esperaba, y que el valor esperado venga
de otro lado que del propio código bajo prueba.

```python
# no protege: pasa con el artista equivocado
assert "TPE1" in tags

# protege: falla si escribe cualquier otra cosa
assert str(tags["TPE1"]) == "Fran Perrotta"
```

---

## Esto no es teoría: dos bugs reales pasaron por acá

**El XML de Rekordbox salía sin datos de análisis.** El test decía
`assert len(parsear(xml)) == 3` y pasaba. Pasaba también con un XML que solo tenía
nombres de archivo — que era exactamente lo que estaba generando. Importarlo obligaba a
Rekordbox a reanalizar todo, o sea el trabajo que el pipeline viene a evitar. El test
reforzado compara `AverageBpm`, `Tonality` y `TotalTime` contra lo que salió del motor.

**26 de 57 copias quedaron ilegibles.** Escribir ID3 pelado sobre un WAV antepone el tag
y rompe el RIFF. El test decía `assert copia.exists() and copia.name == nombre`, y un WAV
destrozado existe y se llama igual. El reforzado abre la copia con mutagen y exige el
contenedor correcto, para los cuatro formatos.

---

## Un test reforzado que no verificaste que falla es otro test ciego

Después de escribir o reforzar un test, **rompé a propósito lo que debería detectar** y
mirá que falle, con un mensaje que diga qué pasó. Si pasa igual, el test no sirve y lo
peor es que ahora parece que sí.

```
# escribí el artista equivocado a propósito
AssertionError: assert 'Artista Equivocado' == 'Fran Perrotta'
```

Poné el motivo en la aserción cuando no sea obvio del diff:

```python
assert t["bpm"] == 128.4, "el AverageBpm no volvió del XML"
```

---

## El escáner

`ast` sobre `*/tests/test_*.py`, marcando funciones cuyas aserciones son **todas** de la
forma débil. Detectó los 4 que se reforzaron en #5.67.

Tiene falsos positivos: `assert not agrupa` tiene forma débil pero afirma la decisión real
bajo prueba, y `assert "myfreemp3" in motivos` afirma qué motivo se detectó. La lista es
para mirar con criterio, no para arreglar a ciegas.
