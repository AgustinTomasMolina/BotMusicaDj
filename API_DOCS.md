"""
Documentación del API REST
"""

# 🎵 API REST - Music Bot

## Información General

- **Versión**: 1.0.0
- **Base URL**: `http://localhost:8000`
- **Documentación Interactiva**: `http://localhost:8000/docs`

---

## 📋 Endpoints

### 1️⃣ Información

#### GET `/`
Información general del API

**Respuesta:**
```json
{
  "nombre": "🎵 Music Bot API",
  "version": "1.0.0",
  "documentacion": "/docs"
}
```

#### GET `/health`
Estado de salud del API

**Respuesta:**
```json
{
  "status": "✅ OK",
  "agentes": {
    "search": "✓ Activo",
    "download": "✓ Activo",
    "recommendation": "✓ Activo"
  }
}
```

---

### 🔍 Búsqueda

#### POST `/api/v1/buscar`
Buscar canciones por término

**Body:**
```json
{
  "query": "Techno",
  "limite": 10
}
```

**Respuesta:**
```json
{
  "exito": true,
  "mensaje": "Encontradas 10 canciones",
  "datos": {
    "canciones": [
      {
        "titulo": "Nombre Canción",
        "artista": "Artista",
        "fuente": "spotify",
        "url": "https://...",
        "duracion": 180,
        "popularidad": 85
      }
    ]
  }
}
```

---

#### POST `/api/v1/buscar-genero`
Buscar canciones por género con recomendaciones

**Body:**
```json
{
  "genero": "House",
  "cantidad": 15
}
```

**Respuesta:**
```json
{
  "exito": true,
  "mensaje": "Encontradas 15 canciones de House",
  "datos": {
    "canciones": [...]
  }
}
```

---

### 💡 Recomendaciones

#### POST `/api/v1/recomendar`
Obtener recomendaciones personalizadas

**Body:**
```json
{
  "genero": "Techno Trance",
  "cantidad": 15
}
```

**Respuesta:**
```json
{
  "exito": true,
  "mensaje": "15 recomendaciones generadas",
  "datos": {
    "genero": "Techno Trance",
    "cantidad": 15,
    "canciones": [
      {
        "titulo": "...",
        "artista": "...",
        "puntuacion": 95.5,
        "popularidad": 90,
        "fuente": "spotify"
      }
    ]
  }
}
```

---

### 📥 Descargas

#### POST `/api/v1/descargar`
Iniciar descarga de canciones (background)

**Body:**
```json
{
  "genero": "Trance",
  "cantidad": 15,
  "formato": "wav"
}
```

**Respuesta:**
```json
{
  "exito": true,
  "mensaje": "Descarga de 15 canciones de Trance iniciada",
  "datos": {
    "estado": "en_progreso",
    "genero": "Trance",
    "cantidad": 15,
    "formato": "wav"
  }
}
```

---

#### GET `/api/v1/descargas`
Listar todos los archivos descargados

**Respuesta:**
```json
{
  "exito": true,
  "mensaje": "5 archivo(s) descargado(s)",
  "datos": {
    "total": 5,
    "archivos": [
      {
        "nombre": "01_Cancion.wav",
        "tamaño_mb": 45.2,
        "ruta": "/downloads/01_Cancion.wav",
        "extension": "wav"
      }
    ]
  }
}
```

---

### ℹ️ Información Adicional

#### GET `/api/v1/formatos`
Formatos soportados y prioridad

**Respuesta:**
```json
{
  "formatos": {
    "wav": {
      "prioridad": 1,
      "descripcion": "Mejor calidad sin pérdida",
      "calidad": "⭐⭐⭐"
    },
    "aiff": {...},
    "flac": {...},
    "mp3": {...}
  }
}
```

---

#### GET `/api/v1/generos-populares`
Géneros disponibles para búsqueda

**Respuesta:**
```json
{
  "generos": [
    "House", "Techno", "Trance", "Ambient",
    "Deep House", "Tech House", "Progressive House",
    ...
  ]
}
```

---

## 🔐 Códigos de Error

| Código | Mensaje | Causa |
|--------|---------|-------|
| 200 | OK | Solicitud exitosa |
| 400 | Bad Request | Datos inválidos |
| 404 | Not Found | Recurso no encontrado |
| 500 | Internal Server Error | Error en el servidor |

---

## 📚 Ejemplos con cURL

### Buscar canciones

```bash
curl -X POST "http://localhost:8000/api/v1/buscar" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Techno",
    "limite": 10
  }'
```

### Obtener recomendaciones

```bash
curl -X POST "http://localhost:8000/api/v1/recomendar" \
  -H "Content-Type: application/json" \
  -d '{
    "genero": "House",
    "cantidad": 15
  }'
```

### Iniciar descarga

```bash
curl -X POST "http://localhost:8000/api/v1/descargar" \
  -H "Content-Type: application/json" \
  -d '{
    "genero": "Trance",
    "cantidad": 15,
    "formato": "wav"
  }'
```

### Listar descargas

```bash
curl "http://localhost:8000/api/v1/descargas"
```

---

## 🚀 Iniciar el API

```bash
python api.py
```

El API estará disponible en `http://localhost:8000`

Documentación interactiva (Swagger UI): `http://localhost:8000/docs`

---

**Status**: ✅ API REST Completado - Fase 2
