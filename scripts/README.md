# scripts/

Lanzadores de Windows (`.bat`) y utilidades que no son parte del paquete Python
(no se instalan con `pip install`).

## Lanzadores

Se pueden ejecutar con doble clic desde el Explorador. Todos se paran solos en la
raíz del repo, así que funcionan sin importar desde dónde se los llame.

| Script | Qué hace |
|---|---|
| `PANEL_CONTROL.bat` | Menú que agrupa todo lo de abajo. **Punto de entrada recomendado.** |
| `setup_y_ejecutar.bat` | Instalación completa la primera vez: pip, `requirements.txt` y tests del motor. |
| `install_deps.bat` | Solo instalar/actualizar dependencias desde `requirements.txt`. |
| `iniciar_web.bat` | Levanta `uvicorn server:app` en el puerto 8000 y abre el navegador. |
| `iniciar_api.bat` | Lo mismo, pero sin abrir el navegador (para consumir la API o ver `/docs`). |
| `iniciar_discord_bot.bat` | Arranca `discord_bot.py` (instala `discord.py` si falta). |
| `demo.bat` | Corre `quickstart.py`, el demo interactivo. |
| `ejecutar_pruebas.bat` | `pytest motor/tests` + los chequeos de `test_bot.py`. |
| `recompilar-frontend.bat` | `npm run build` en `frontend/`; el build queda en `frontend/dist`. |

`_entorno.bat` no se ejecuta directo: es el helper que usan los demás para
ubicarse en la raíz del repo y resolver el intérprete de Python (prefiere
`.venv\` o `venv\` del repo; si no hay, usa `py` o `python` del PATH).

## Notas

- El entrypoint del servidor es **`server:app`**, el mismo que usa el `Dockerfile`.
  `api.py` es una API anterior que quedó sin uso: no la levanta ningún script.
- Los scripts instalan desde `requirements.txt`, no con listas de paquetes sueltas.

## Utilidades pendientes

Candidatos a vivir acá cuando se escriban:

- `benchmark_bpm.py` — correr la detección contra una carpeta de tracks con BPM
  ya conocido (los que trae Rekordbox en los tags) y sacar la distribución de error.
- `ingest_fma.py` — bajar metadata de Free Music Archive / Jamendo con licencia y
  URL de origen por track.
- `dump_set.py` — exportar un set armado a texto para pegarlo en una descripción
  de SoundCloud.
