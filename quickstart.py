"""
Quick Start - Ejemplo simple para comenzar
"""

from main import MusicBot
import asyncio


def ejemplo_1_busqueda():
    """Ejemplo 1: Buscar canciones de un género"""
    print("\n" + "="*80)
    print("EJEMPLO 1: Buscar Canciones de un Género")
    print("="*80 + "\n")
    
    bot = MusicBot()
    
    # Buscar 5 canciones de Techno
    print("🔍 Buscando 5 canciones de 'Techno'...\n")
    recomendadas = bot.buscar_y_recomendar("Techno", cantidad=5)
    
    if recomendadas:
        print(f"\n✅ Encontradas {len(recomendadas)} canciones!\n")
    else:
        print("\n⚠️ No se encontraron canciones. Verifica las credenciales de Spotify.\n")


def ejemplo_2_listar_descargas():
    """Ejemplo 2: Listar descargas existentes"""
    print("\n" + "="*80)
    print("EJEMPLO 2: Listar Descargas")
    print("="*80 + "\n")
    
    bot = MusicBot()
    bot.listar_descargas()


def ejemplo_3_buscar_genero():
    """Ejemplo 3: Búsqueda por diferentes géneros"""
    print("\n" + "="*80)
    print("EJEMPLO 3: Búsqueda por Género")
    print("="*80 + "\n")
    
    bot = MusicBot()
    
    generos = ["House", "Trance", "Ambient"]
    
    for genero in generos:
        print(f"\n🎵 Buscando: {genero}")
        print("-" * 40)
        resultados = bot.search_agent.buscar_con_fallback(genero, limit=3)
        
        if resultados:
            for cancion in resultados:
                print(f"  • {cancion['titulo']} - {cancion['artista']}")
        else:
            print("  (No encontrado)")


def main():
    """Menú principal"""
    print("\n" + "🎵 "*40)
    print("BOT DE MÚSICA INTELIGENTE - QUICK START")
    print("🎵 "*40 + "\n")
    
    print("Selecciona un ejemplo para ejecutar:\n")
    print("1. Buscar canciones de un género")
    print("2. Listar descargas existentes")
    print("3. Búsqueda por múltiples géneros")
    print("0. Salir\n")
    
    opcion = input("Tu opción (0-3): ").strip()
    
    if opcion == "1":
        ejemplo_1_busqueda()
    elif opcion == "2":
        ejemplo_2_listar_descargas()
    elif opcion == "3":
        ejemplo_3_buscar_genero()
    elif opcion == "0":
        print("\n¡Hasta luego! 👋\n")
        return
    else:
        print("\n❌ Opción no válida\n")
        return
    
    print("\n✅ Ejemplo completado!\n")


if __name__ == "__main__":
    main()
