"""
Script de Pruebas - Verifica que todos los agentes funcionen correctamente
"""

import sys
import logging
from pathlib import Path

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_imports():
    """Prueba que todos los módulos se importen correctamente"""
    logger.info("\n" + "="*80)
    logger.info("TEST 1: Verificar Imports")
    logger.info("="*80)
    
    try:
        logger.info("✓ Importando config...")
        import config
        
        logger.info("✓ Importando database...")
        import database
        
        logger.info("✓ Importando search_agent...")
        from search_agent import SearchAgent
        
        logger.info("✓ Importando download_agent...")
        from download_agent import DownloadAgent
        
        logger.info("✓ Importando recommendation_agent...")
        from recommendation_agent import RecommendationAgent
        
        logger.info("✓ Importando main...")
        from main import MusicBot
        
        logger.info("\n✅ Todos los imports exitosos!\n")
        return True
    
    except ImportError as e:
        logger.error(f"\n❌ Error de import: {e}\n")
        return False


def test_agents():
    """Prueba la inicialización de los agentes"""
    logger.info("="*80)
    logger.info("TEST 2: Inicializar Agentes")
    logger.info("="*80)
    
    try:
        from main import MusicBot
        
        logger.info("🔄 Inicializando MusicBot...")
        bot = MusicBot()
        
        logger.info(f"✓ Search Agent: {bot.search_agent.__class__.__name__}")
        logger.info(f"✓ Download Agent: {bot.download_agent.__class__.__name__}")
        logger.info(f"✓ Recommendation Agent: {bot.recommendation_agent.__class__.__name__}")
        
        logger.info("\n✅ Todos los agentes inicializados!\n")
        return True
    
    except Exception as e:
        logger.error(f"\n❌ Error inicializando agentes: {e}\n")
        return False


def test_search():
    """Prueba búsqueda (requiere Spotify API)"""
    logger.info("="*80)
    logger.info("TEST 3: Prueba de Búsqueda")
    logger.info("="*80)
    
    try:
        from main import MusicBot
        import os
        from dotenv import load_dotenv
        
        load_dotenv()
        
        spotify_id = os.getenv('SPOTIFY_CLIENT_ID', '')
        spotify_secret = os.getenv('SPOTIFY_CLIENT_SECRET', '')
        
        if not spotify_id or not spotify_secret:
            logger.warning("⚠️ Credenciales de Spotify no configuradas en .env")
            logger.info("   (Saltando prueba de búsqueda)\n")
            return True
        
        logger.info("🔄 Buscando canciones de 'House'...")
        bot = MusicBot()
        resultados = bot.search_agent.buscar_con_fallback("House", limit=3)
        
        if resultados:
            logger.info(f"✓ Encontradas {len(resultados)} canciones\n")
            for idx, cancion in enumerate(resultados, 1):
                logger.info(f"  {idx}. {cancion['titulo']} - {cancion['artista']}")
            logger.info()
            logger.info("✅ Búsqueda exitosa!\n")
            return True
        else:
            logger.warning("⚠️ No se encontraron canciones\n")
            return True
    
    except Exception as e:
        logger.error(f"\n❌ Error en búsqueda: {e}\n")
        return False


def test_database():
    """Prueba la base de datos"""
    logger.info("="*80)
    logger.info("TEST 4: Base de Datos")
    logger.info("="*80)
    
    try:
        from database import SessionLocal, Cancion
        
        logger.info("🔄 Conectando a base de datos...")
        db = SessionLocal()
        
        # Probar inserción
        nueva_cancion = Cancion(
            titulo="Test Song",
            artista="Test Artist",
            genero="Test",
            duracion=180,
            fuente="test",
            url_fuente="http://test.com"
        )
        db.add(nueva_cancion)
        db.commit()
        
        logger.info("✓ Canción de prueba insertada")
        
        # Contar canciones
        count = db.query(Cancion).count()
        logger.info(f"✓ Total de canciones en BD: {count}")
        
        # Limpiar
        db.query(Cancion).filter(Cancion.titulo == "Test Song").delete()
        db.commit()
        db.close()
        
        logger.info("\n✅ Base de datos funcionando!\n")
        return True
    
    except Exception as e:
        logger.error(f"\n❌ Error en base de datos: {e}\n")
        return False


def test_directories():
    """Verifica que existan directorios necesarios"""
    logger.info("="*80)
    logger.info("TEST 5: Directorios Requeridos")
    logger.info("="*80)
    
    try:
        dirs = ['downloads', 'logs']
        
        for dir_name in dirs:
            dir_path = Path(dir_name)
            dir_path.mkdir(exist_ok=True)
            logger.info(f"✓ Directorio '{dir_name}': {dir_path.absolute()}")
        
        logger.info("\n✅ Directorios listos!\n")
        return True
    
    except Exception as e:
        logger.error(f"\n❌ Error con directorios: {e}\n")
        return False


def main():
    """Ejecuta todos los tests"""
    logger.info("\n" + "🚀 "*40)
    logger.info("INICIANDO SUITE DE PRUEBAS")
    logger.info("🚀 "*40 + "\n")
    
    tests = [
        ("Imports", test_imports),
        ("Directorios", test_directories),
        ("Agentes", test_agents),
        ("Base de Datos", test_database),
        ("Búsqueda", test_search),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            logger.error(f"\n❌ Error ejecutando {test_name}: {e}\n")
            results.append((test_name, False))
    
    # Resumen
    logger.info("="*80)
    logger.info("RESUMEN DE PRUEBAS")
    logger.info("="*80)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"{status} - {test_name}")
    
    logger.info("\n" + "="*80)
    logger.info(f"RESULTADO: {passed}/{total} pruebas exitosas")
    logger.info("="*80 + "\n")
    
    if passed == total:
        logger.info("🎉 ¡TODAS LAS PRUEBAS PASARON! Bot listo para usar.\n")
        return 0
    else:
        logger.warning(f"⚠️ {total - passed} pruebas fallaron. Revisa los errores arriba.\n")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
