"""
Configuración de base de datos
"""

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Table, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from config import DATABASE_URL

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ============ MODELOS ============

class Cancion(Base):
    """Tabla de canciones descubiertasDiscovered songs table"""
    __tablename__ = "canciones"

    id = Column(Integer, primary_key=True, index=True)
    titulo = Column(String, index=True)
    artista = Column(String, index=True)
    genero = Column(String, index=True)
    duracion = Column(Integer)  # en segundos
    likes = Column(Integer, default=0)
    puntuacion = Column(Float, default=0.0)
    fuente = Column(String)  # Spotify, SoundCloud, YouTube, etc
    url_fuente = Column(String, unique=True)
    url_descarga = Column(String)
    comentarios = Column(Integer, default=0)
    creado_en = Column(DateTime, default=datetime.utcnow)


class Descarga(Base):
    """Tabla de descargas realizadas"""
    __tablename__ = "descargas"

    id = Column(Integer, primary_key=True, index=True)
    cancion_id = Column(Integer, ForeignKey("canciones.id"))
    fecha_descarga = Column(DateTime, default=datetime.utcnow)
    formato = Column(String)  # wav, aiff, flac, mp3
    ruta_local = Column(String)
    tamaño_bytes = Column(Integer)
    estado = Column(String, default="completada")  # completada, fallida, en_progreso


class Preferencia(Base):
    """Tabla de preferencias del usuario"""
    __tablename__ = "preferencias"

    id = Column(Integer, primary_key=True, index=True)
    generos_favoritos = Column(Text)  # JSON serializado
    artistas_favoritos = Column(Text)  # JSON serializado
    formato_preferido = Column(String, default="wav")
    limite_descargas = Column(Integer, default=15)
    actualizado_en = Column(DateTime, default=datetime.utcnow)


# Crear tablas
Base.metadata.create_all(bind=engine)

print("✅ Base de datos configurada")
