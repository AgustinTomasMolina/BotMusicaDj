"""
API REST - FastAPI para el Bot de Música
Endpoints para búsqueda, descarga y recomendaciones
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import logging
from main import MusicBot
import asyncio

# Configuración
app = FastAPI(
    title="🎵 Music Bot API",
    description="API para búsqueda, descarga y recomendación de música",
    version="1.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger(__name__)
bot = MusicBot()


# ============ MODELOS PYDANTIC ============

class CancionResponse(BaseModel):
    titulo: str
    artista: str
    fuente: str
    url: str
    duracion: int
    popularidad: Optional[int] = None
    puntuacion: Optional[float] = None


class BusquedaRequest(BaseModel):
    query: str
    limite: int = 10


class BusquedaGeneroRequest(BaseModel):
    genero: str
    cantidad: int = 15


class DescargaRequest(BaseModel):
    genero: str
    cantidad: int = 15
    formato: str = "mp3"


class RespuestaGenerica(BaseModel):
    exito: bool
    mensaje: str
    datos: Optional[dict] = None


# ============ ENDPOINTS ============

@app.get("/", tags=["Info"])
async def raiz():
    """Información del API"""
    return {
        "nombre": "🎵 Music Bot API",
        "version": "1.0.0",
        "descripcion": "API para búsqueda, descarga y recomendación de música",
        "documentacion": "/docs"
    }


@app.get("/health", tags=["Info"])
async def health():
    """Estado de salud del API"""
    return {
        "status": "✅ OK",
        "agentes": {
            "search": "✓ Activo",
            "download": "✓ Activo",
            "recommendation": "✓ Activo"
        }
    }


@app.post("/api/v1/buscar", response_model=RespuestaGenerica, tags=["Búsqueda"])
async def buscar_canciones(request: BusquedaRequest):
    """
    Busca canciones por término
    
    **Parámetros:**
    - query: Término de búsqueda (ej: "Techno")
    - limite: Cantidad máxima de resultados (default: 10)
    """
    try:
        logger.info(f"🔍 Búsqueda: {request.query}")
        
        resultados = bot.search_agent.buscar_con_fallback(
            request.query,
            limit=request.limite
        )
        
        if not resultados:
            raise HTTPException(status_code=404, detail="No se encontraron canciones")
        
        return {
            "exito": True,
            "mensaje": f"Encontradas {len(resultados)} canciones",
            "datos": {"canciones": resultados}
        }
    
    except Exception as e:
        logger.error(f"❌ Error en búsqueda: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/buscar-genero", response_model=RespuestaGenerica, tags=["Búsqueda"])
async def buscar_por_genero(request: BusquedaGeneroRequest):
    """
    Busca canciones por género
    
    **Parámetros:**
    - genero: Género musical (ej: "House", "Techno", "Trance")
    - cantidad: Cantidad de canciones a buscar (default: 15)
    """
    try:
        logger.info(f"🎵 Búsqueda por género: {request.genero}")
        
        canciones = bot.search_agent.buscar_por_genero(
            request.genero,
            limit=request.cantidad * 2
        )
        
        if not canciones:
            raise HTTPException(status_code=404, detail=f"No se encontraron canciones del género '{request.genero}'")
        
        # Recomendar las mejores
        recomendadas = bot.recommendation_agent.generar_recomendaciones(
            request.genero,
            canciones,
            cantidad=request.cantidad
        )
        
        return {
            "exito": True,
            "mensaje": f"Encontradas {len(recomendadas)} canciones de {request.genero}",
            "datos": {"canciones": recomendadas}
        }
    
    except Exception as e:
        logger.error(f"❌ Error en búsqueda por género: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/recomendar", response_model=RespuestaGenerica, tags=["Recomendaciones"])
async def recomendar(request: BusquedaGeneroRequest):
    """
    Obtiene recomendaciones personalizadas de un género
    
    **Parámetros:**
    - genero: Género musical
    - cantidad: Cantidad de recomendaciones (default: 15)
    """
    try:
        logger.info(f"💡 Generando recomendaciones: {request.genero}")
        
        # Buscar canciones del género
        canciones = bot.search_agent.buscar_por_genero(request.genero, limit=50)
        
        if not canciones:
            raise HTTPException(status_code=404, detail=f"No hay canciones disponibles del género '{request.genero}'")
        
        # Generar recomendaciones rankeadas
        recomendadas = bot.recommendation_agent.generar_recomendaciones(
            request.genero,
            canciones,
            cantidad=request.cantidad
        )
        
        # Crear previews
        previews = [
            bot.recommendation_agent.crear_preview(cancion)
            for cancion in recomendadas
        ]
        
        return {
            "exito": True,
            "mensaje": f"{len(recomendadas)} recomendaciones generadas",
            "datos": {
                "genero": request.genero,
                "cantidad": len(recomendadas),
                "canciones": previews
            }
        }
    
    except Exception as e:
        logger.error(f"❌ Error generando recomendaciones: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/descargar", response_model=RespuestaGenerica, tags=["Descargas"])
async def descargar(request: DescargaRequest, background_tasks: BackgroundTasks):
    """
    Inicia descarga de canciones en background
    
    **Parámetros:**
    - genero: Género musical
    - cantidad: Cantidad de canciones a descargar
    - formato: Formato preferido (mp3, wav, flac, aiff)
    
    **Nota:** La descarga se ejecuta en background. Verifica /api/v1/descargas para el estado.
    """
    try:
        logger.info(f"📥 Descarga iniciada: {request.cantidad} de {request.genero} ({request.formato})")
        
        # Agregar tarea en background
        background_tasks.add_task(
            ejecutar_descarga,
            request.genero,
            request.cantidad,
            request.formato
        )
        
        return {
            "exito": True,
            "mensaje": f"Descarga de {request.cantidad} canciones de {request.genero} iniciada",
            "datos": {
                "estado": "en_progreso",
                "genero": request.genero,
                "cantidad": request.cantidad,
                "formato": request.formato
            }
        }
    
    except Exception as e:
        logger.error(f"❌ Error iniciando descarga: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/descargas", response_model=RespuestaGenerica, tags=["Descargas"])
async def listar_descargas():
    """
    Lista todos los archivos descargados
    """
    try:
        archivos = bot.download_agent.listar_descargas()
        
        return {
            "exito": True,
            "mensaje": f"{len(archivos)} archivo(s) descargado(s)",
            "datos": {
                "total": len(archivos),
                "archivos": archivos
            }
        }
    
    except Exception as e:
        logger.error(f"❌ Error listando descargas: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/formatos", tags=["Info"])
async def formatos_soportados():
    """
    Retorna los formatos de audio soportados y su prioridad
    """
    return {
        "formatos": {
            "wav": {
                "prioridad": 1,
                "descripcion": "Mejor calidad sin pérdida",
                "calidad": "⭐⭐⭐"
            },
            "aiff": {
                "prioridad": 2,
                "descripcion": "Formato profesional",
                "calidad": "⭐⭐⭐"
            },
            "flac": {
                "prioridad": 3,
                "descripcion": "Comprimido sin pérdida",
                "calidad": "⭐⭐"
            },
            "mp3": {
                "prioridad": 4,
                "descripcion": "Última opción - con pérdida",
                "calidad": "⭐"
            }
        },
        "nota": "El bot descargará automáticamente en el mejor formato disponible"
    }


@app.get("/api/v1/generos-populares", tags=["Info"])
async def generos_populares():
    """
    Retorna géneros musicales populares para búsqueda
    """
    return {
        "generos": [
            "House", "Techno", "Trance", "Ambient",
            "Deep House", "Tech House", "Progressive House",
            "Electro", "Downtempo", "Chill Out",
            "Drum & Bass", "Dubstep", "Garage",
            "Minimal", "Acid", "Industrial"
        ]
    }


# ============ FUNCIONES AUXILIARES ============

async def ejecutar_descarga(genero: str, cantidad: int, formato: str):
    """Ejecuta descarga en background"""
    try:
        logger.info(f"⬇️  Iniciando descarga: {cantidad} de {genero}")
        
        # Buscar y recomendar
        recomendadas = bot.buscar_y_recomendar(genero, cantidad)
        
        if recomendadas:
            # Descargar
            await bot.download_agent.descargar_multiples(
                recomendadas,
                formato=formato,
                limit=cantidad
            )
            
            logger.info(f"✅ Descarga completada: {genero}")
        else:
            logger.warning(f"⚠️ No hay canciones para descargar: {genero}")
    
    except Exception as e:
        logger.error(f"❌ Error en descarga background: {e}")


# ============ MANEJO DE ERRORES ============

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Manejo de excepciones HTTP"""
    return {
        "exito": False,
        "mensaje": exc.detail,
        "datos": None
    }


# ============ INICIALIZACIÓN ============

if __name__ == "__main__":
    import uvicorn
    
    print("\n" + "="*80)
    print("🎵 Music Bot API - Iniciando...")
    print("="*80)
    print("\n📍 API disponible en: http://localhost:8000")
    print("📚 Documentación en: http://localhost:8000/docs\n")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
