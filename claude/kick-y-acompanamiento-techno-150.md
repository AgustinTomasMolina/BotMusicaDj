# Cómo acompañar el kick — Techno 150 BPM (Ableton Live 12)

**Guía visual con diagramas:** https://claude.ai/code/artifact/1bfe6f5c-8df5-48c2-9ca1-dc3beeef45f7

Setup: Ableton Live 12, controladora Pioneer DDJ-400 (2 canales).
Género objetivo: techno / hard groove a 150 BPM.

## Datos de tiempo a 150 BPM

| Figura | Duración |
|---|---|
| 1 negra (1/4) | 400 ms |
| 1 corchea (1/8) | 200 ms |
| 1 semicorchea (1/16) | 100 ms |
| 1 compás (4/4) | 1.6 s |

---

## 1. El kick por dentro (empezar por acá)

Si el kick suena flaco él solo, no hay percusión que lo arregle.

**Duración**: un 909 de fábrica dura 150–200 ms. A 150 BPM hay 400 ms por tiempo.
Estirar el Decay/Release del Simpler o Drum Sampler hasta **280–320 ms**.

**Las tres capas** (empiezan juntas, terminan en momentos distintos):

| Capa | Zona | Duración | Qué aporta |
|---|---|---|---|
| Click / top | 2–6 kHz | 5–10 ms | que se escuche en parlantes chicos |
| Cuerpo / punch | 100–250 Hz | ~150 ms | el golpe |
| Sub | 45–55 Hz | ~300 ms | el peso |

El pitch arranca en ~120 Hz y cae a 50 Hz en los primeros 40 ms.

**Cadena en la pista del kick, en este orden:**

1. **EQ Eight** — paso alto en 30 Hz
2. **Saturator** (Soft Sine) — Drive +10 dB, Output −10 dB → genera armónicos audibles
3. **Drum Buss** — Boom 25 %, Transients +20 %, Drive Medium, Crunch apenas
4. **Compressor** — Attack 10 ms (lento a propósito), Release 100 ms, Ratio 4:1, 3–4 dB GR
5. **Utility** — Bass Mono 120 Hz

**Lo que más cambia: la saturación.** Sin distorsionar, el kick sólo tiene energía debajo
de 100 Hz — zona que los parlantes chicos no reproducen. Al saturarlo aparecen armónicos
en 200/400/800 Hz y el mismo kick se escucha grande en cualquier lado.

**Layering** (si todavía queda flaco): dos kicks en el mismo pad del Drum Rack.
Capa sub con LP 100 Hz, capa cuerpo con HP 100 Hz. Si al sumarlos pierde peso,
Utility con Phase Invert en una de las dos.

Alternativa de distorsión: **Roar** (Suite) en modo multibanda, drive sólo en el medio-grave.

---

## 2. RUMBLE — llenar el hueco entre kicks

El rumble no aporta un sonido nuevo: aporta que el grave no se corte.
Por eso **en solo parece que no hace nada**.

Cadena (pista aparte o Return):

1. Duplicar el kick a una pista nueva
2. **Reverb**: Decay 2–4 s, Dry/Wet 100 %, Pre-delay 0
3. **EQ Eight**: paso alto ~40 Hz, paso bajo ~200 Hz
4. **Compressor** con **Sidechain** → Audio From: pista del Kick, **Post FX**
   - Ratio 8:1 · Attack 0,1 ms · **Release 200 ms** · 8–12 dB de reducción
5. **Utility** → Bass Mono 120 Hz
6. Mezclar por debajo hasta que se sienta, no se escuche

**Regla del Release: ≈ la mitad del tiempo entre kicks.** A 150 BPM = 200 ms.

**Cómo evaluarlo:** nunca en solo. Con todo sonando, apagar y prender la pista RUMBLE.
Si al apagarla el track se achica, está funcionando.

⚠️ Filtrado entre 40 y 200 Hz, el rumble es **inaudible en parlantes de notebook y
auriculares chicos**. No es un error de configuración.

---

## 3. Qué más le sumás (todo arriba de 300 Hz)

| Capa | Para qué sirve | Dónde va |
|---|---|---|
| Open hat | El empuje | Contratiempo, siempre |
| Closed hats | Velocidad y detalle | Semicorcheas, velocity variada |
| Ride / shaker | Sostiene la energía | Corcheas, bajito |
| Percusión con carácter | La identidad del track | Sincopada, en los huecos |
| Clap / snare | Ubica en el compás | 2 y 4, o sólo el 4 |
| Stab | Melodía sin melodía | Contratiempos, con sidechain |
| Atmósfera | El pegamento | Continua, muy por debajo |

**Las dos reglas:**

1. Paso alto en 300 Hz a todo lo nuevo, sin excepción
2. Dos elementos no pueden ocupar el mismo casillero del compás

**Mínimo que ya suena a track:** kick + rumble + open hat en el contratiempo + una percusión suelta.

Groove: en techno va recto, swing máximo 52–54 % en el Groove Pool.
Generación de patrones: MIDI Generators de Live 12 (Rhythm, Euclidean, Seed) + Velocity Shaper.

---

## 4. Reparto de frecuencias

| Elemento | Zona |
|---|---|
| Sub del kick | 45–55 Hz |
| Rumble | 60–180 Hz |
| Cuerpo del kick | 100–250 Hz |
| Percusión / stabs | 300 Hz – 3 kHz |
| Hats | 6–14 kHz |

El único cruce a propósito es rumble / cuerpo del kick en 100–180 Hz. Por eso el sidechain.

## 5. Estéreo

- **Utility** con **Bass Mono** en 120 Hz sobre el grupo de batería
- Kick y rumble al centro; percusión y hats abiertos (Width 120–130 %)
- Reverb y delay sólo en percusión y agudos, nunca en el kick
- Kick con pico alrededor de −6 dBFS antes del master

---

## 6. Si suena mal

| Lo que escuchás | Qué está pasando | Qué tocar |
|---|---|---|
| El kick suena flaco él solo | Dura menos de 200 ms o no tiene armónicos | Decay a 280–320 ms + Saturator Soft Sine |
| En solo el rumble no suena | Filtrado 40–200 Hz, los parlantes chicos no llegan | Normal. Evaluarlo apagando/prendiendo la pista |
| Sigue vacío | Rumble muy bajo o Release muy largo | Subir el fader; Release a 150–200 ms |
| El kick perdió pegada | El rumble le tapa el golpe | Más reducción o Attack más rápido |
| Embarrado | Mucha energía en 150–400 Hz | LP del rumble a 150 Hz; percusión desde 300 Hz |
| Bombea raro | Release corto o Ratio exagerado | Release 200 ms; Ratio 6:1 |
| Cansa a los 30 s | No hay variación | Sacar/agregar un elemento cada 8 compases (13 s) |

---

## 7. Orden de trabajo

1. Kick estirado y saturado, sonando bien **solo** en loop
2. Rumble, balanceado contra el kick
3. Open hat en el contratiempo
4. Closed hats con velocity variada
5. Una o dos percusiones con paso alto en 300 Hz
6. Recién ahora bajo o stabs, si hacen falta
7. Utility con Bass Mono en el grupo de batería
8. Escuchar todo en mono: si el kick sigue pegando, va bien

---

## Pendiente

Confirmar con qué monitorea (parlantes de notebook / auriculares / monitores) para saber
si puede confiar en lo que oye en la zona grave o conviene guiarse con un analizador
de espectro.
