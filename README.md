# IPTV API v2.1 - Sistema de Gestión de Usuarios con Control de Dispositivos

API para gestión de usuarios IPTV con autenticación JWT, control de conexiones simultáneas y generación dinámica de playlists M3U.

## Características

- **Autenticación JWT**: Sistema completo de autenticación con tokens JWT y OAuth2
- **Roles de usuario**: Soporte para roles `admin` y `user`
- **Gestión de usuarios**: Crear, editar, eliminar usuarios con límite de conexiones
- **Control de dispositivos**: Tracking de dispositivos conectados por usuario
- **Límite de conexiones**: Máximo de dispositivos simultáneos por cuenta
- **Playlists dinámicas**: Generación de M3U con URLs proxificadas y filtros
- **Proxy de streams**: Autenticación en cada solicitud de stream
- **Endpoints unificados**: API REST consistente para canales, películas y series
- **Consultas SQL directas**: Uso de psycopg2 para consultas complejas con DISTINCT y GROUP BY
- **Paginación estándar**: Soporte para paginación con page/page_size
- **Búsqueda por nombre**: Filtrado de contenido por búsqueda de texto
- **Detección de dispositivos**: Identificación automática del tipo de dispositivo
- **Limpieza automática**: Sesiones inactivas se eliminan automáticamente
- **Estadísticas del sistema**: Endpoint para monitorear uso del sistema
- **Sincronización automática**: Scripts para sincronizar contenido desde fuente IPTV
- **Manejo de errores consistente**: Respuestas de error estandarizadas

## Arquitectura

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Cliente   │────▶│    Nginx    │────▶│  FastAPI    │
│  (App IPTV) │     │   (Proxy)   │     │    API      │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                                │
                                         ┌──────▼──────┐
                                         │  Supabase   │
                                         │ (PostgreSQL)│
                                         └─────────────┘
```

## Estructura del Proyecto

```
iptv-api/
├── scripts/
│   ├── api.py              # Aplicación FastAPI principal (v2.1)
│   └── sync_iptv.py        # Script de sincronización de contenido
├── services/
│   ├── __init__.py         # Exporta todos los servicios
│   ├── user_service.py     # Gestión de usuarios
│   ├── device_service.py   # Control de dispositivos
│   ├── playlist_service.py # Generación de M3U
│   ├── stream_service.py   # Proxy de streams
│   ├── content_service.py  # Gestión de contenido (canales, películas, series)
│   └── bulk_insert.py      # Inserción masiva de datos
├── utils/
│   ├── config.py           # Configuración centralizada
│   ├── models.py           # Modelos Pydantic
│   ├── constants.py        # Constantes del sistema
│   ├── exceptions.py       # Excepciones personalizadas (nuevo en v2.1)
│   └── dependencies.py     # Dependencias reutilizables (nuevo en v2.1)
├── database/
│   └── schema.sql          # Script SQL para Supabase
├── docker/
│   ├── Dockerfile          # Imagen Docker de la API
│   ├── docker-compose.yml  # Orquestación de servicios
│   └── .env                # Variables de entorno Docker
├── nginx/
│   └── nginx.conf          # Configuración Nginx
├── postman/
│   ├── postman.json        # Colección de Postman
│   └── environment.json    # Variables de entorno Postman
├── requirements.txt        # Dependencias Python
└── .env.example            # Ejemplo de variables de entorno
```

## Instalación

### 1. Configurar Supabase

Ejecuta el script SQL en tu proyecto de Supabase:

```bash
# Abre el SQL Editor en Supabase y ejecuta:
database/schema.sql
```

### 2. Configurar variables de entorno

```bash
cp .env.example .env
# Editar .env con tus valores de Supabase y configuración
```

Variables importantes:
- `SUPABASE_URL` y `SUPABASE_KEY`: Credenciales de Supabase
- `API_SECRET_KEY`: Clave secreta para JWT
- `IPTV_SOURCE_URL`: URL de la playlist fuente para sincronización
- `PUBLIC_DOMAIN`: Dominio público para generar URLs

**Configuración PostgreSQL opcional** (para consultas SQL directas):
- `PG_HOST`: Host de PostgreSQL (si no se especifica, se extrae de SUPABASE_URL)
- `PG_PORT`: Puerto de PostgreSQL (default: 5432)
- `PG_DATABASE`: Nombre de la base de datos (default: postgres)
- `PG_USER`: Usuario de PostgreSQL
- `PG_PASSWORD`: Contraseña de PostgreSQL

Si no se configuran las variables PG_*, el sistema extrae automáticamente la conexión de SUPABASE_URL.

### 3. Iniciar con Docker

```bash
cd docker
docker-compose up -d
```

### 4. Verificar funcionamiento

```bash
curl http://localhost/health
```

## Autenticación

La API utiliza JWT (JSON Web Tokens) para autenticación. Los endpoints administrativos requieren un token válido.

### Login

```bash
curl -X POST http://localhost/api/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=admin123"
```

Respuesta:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "role": "admin"
}
```

### Uso del Token

Incluir el token en el header `Authorization`:

```bash
curl http://localhost/api/admin/users \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
```

## Endpoints API

### Autenticación

| Método | Endpoint | Descripción | Auth |
|--------|----------|-------------|------|
| POST | `/api/auth/login` | Login con JWT | Público |

### Usuarios (Requiere Admin)

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| POST | `/api/admin/users` | Crear usuario |
| GET | `/api/admin/users` | Listar usuarios (paginado) |
| GET | `/api/admin/users/{id}` | Obtener usuario |
| PUT | `/api/admin/users/{id}` | Actualizar usuario |
| DELETE | `/api/admin/users/{id}` | Eliminar usuario |

**Paginación**: `GET /api/admin/users?page=1&page_size=50`

Respuesta paginada:
```json
{
  "items": [...],
  "total": 150,
  "page": 1,
  "page_size": 50,
  "pages": 3,
  "has_next": true,
  "has_prev": false
}
```

### Dispositivos (Requiere Admin)

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/api/admin/users/{id}/devices` | Dispositivos del usuario |
| DELETE | `/api/admin/users/{id}/devices/{device_id}` | Desconectar dispositivo |
| DELETE | `/api/admin/users/{id}/devices` | Desconectar todos |
| GET | `/api/admin/sessions` | Todas las sesiones activas |

### Estadísticas (Requiere Admin)

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/api/admin/stats` | Estadísticas del sistema |

Respuesta de `/api/admin/stats`:
```json
{
  "total_users": 100,
  "active_users": 85,
  "total_sessions": 45,
  "total_channels": 2500,
  "total_movies": 500,
  "total_series": 300
}
```

### Contenido (Público - Bearer Token)

| Método | Endpoint | Descripción | Auth |
|--------|----------|-------------|------|
| GET | `/api/content/groups` | Lista de grupos disponibles | Bearer Token |
| GET | `/api/content/countries` | Lista de países disponibles | Bearer Token |
| GET | `/api/content` | Lista paginada de contenido | Bearer Token |
| GET | `/api/content/{type}/{id}` | Item específico | Bearer Token |

**Nota**: Los endpoints de contenido utilizan **psycopg2** para consultas SQL directas con DISTINCT y GROUP BY, eliminando el límite de 1000 registros de Supabase.

**Parámetros de `/api/content/groups`**:
- `content_type`: Tipo de contenido (`channels`, `movies`, `series`)
- `countries`: Filtrar por países (separados por coma: `US,MX,ES`)

**Ejemplos**:

```bash
# Obtener grupos de canales
curl "http://localhost/api/content/groups?content_type=channels" \
  -H "Authorization: Bearer TOKEN"

# Obtener grupos filtrados por países
curl "http://localhost/api/content/groups?content_type=channels&countries=US,MX" \
  -H "Authorization: Bearer TOKEN"

# Obtener lista de países
curl "http://localhost/api/content/countries?content_type=channels" \
  -H "Authorization: Bearer TOKEN"
```

Respuesta de `/api/content/groups`:
```json
{
  "groups": ["Deportes", "Películas", "Series", "Noticias"]
}
```

Respuesta de `/api/content/countries`:
```json
{
  "countries": [
    {"code": "AR", "name": "Argentina"},
    {"code": "ES", "name": "España"},
    {"code": "MX", "name": "México"},
    {"code": "US", "name": "Estados Unidos"}
  ]
}
```

**Parámetros de `/api/content`**:
- `content_type`: Tipo de contenido (`channels`, `movies`, `series`)
- `page`: Número de página (default: 1)
- `page_size`: Items por página (default: 50, max: 100)
- `group`: Filtrar por grupo
- `country`: Filtrar por país
- `search`: Buscar por nombre (búsqueda parcial, case-insensitive)

**Ejemplos**:

```bash
# Obtener canales paginados
curl "http://localhost/api/content?content_type=channels&page=1&page_size=50" \
  -H "Authorization: Bearer TOKEN"

# Buscar películas por nombre
curl "http://localhost/api/content?content_type=movies&search=Inception" \
  -H "Authorization: Bearer TOKEN"

# Filtrar series por grupo y país
curl "http://localhost/api/content?content_type=series&group=Action&country=US" \
  -H "Authorization: Bearer TOKEN"

# Obtener item específico
curl "http://localhost/api/content/movies/12345" \
  -H "Authorization: Bearer TOKEN"
```

Respuesta de lista paginada:
```json
{
  "items": [
    {
      "id": "12345",
      "num": 1,
      "nombre": "Canal 1",
      "logo": "http://...",
      "grupo": "Sports",
      "country": "ES",
      "stream_url": "https://domain.com/..."
    }
  ],
  "total": 2500,
  "page": 1,
  "page_size": 50,
  "pages": 50,
  "has_next": true,
  "has_prev": false
}
```

### Playlist y Streams (Público - Autenticado por URL)

| Endpoint | Descripción |
|----------|-------------|
| `/playlist/{user}/{pass}.m3u` | Playlist M3U personalizada |
| `/stream/{type}/{user}/{pass}/{stream_id}` | Stream proxy unificado |

**Parámetros de playlist**:
- `content_type`: Filtrar por tipo (`channels`, `movies`, `series`)
- `group`: Filtrar por grupo
- `country`: Filtrar por país

**Ejemplo**: `/playlist/usuario/pass.m3u?content_type=channels&country=ES`

**Tipos de stream**:
- `live`: Canales en vivo
- `movie`: Películas
- `series`: Series

**Ejemplo**: `/stream/live/usuario/pass/123456`

## Uso

### Crear usuario (como Admin)

```bash
# Primero obtener token
curl -X POST http://localhost/api/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=admin123"

# Crear usuario con el token
curl -X POST http://localhost/api/admin/users \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer TOKEN_AQUI" \
  -d '{
    "username": "usuario1",
    "password": "password123",
    "max_connections": 2,
    "role": "user"
  }'
```

### Obtener playlist

```
# En tu app IPTV, usar la URL:
http://tu-dominio.com/playlist/usuario1/password123.m3u

# Con filtros:
http://tu-dominio.com/playlist/usuario1/password123.m3u?content_type=channels&country=ES
```

### Ver dispositivos conectados

```bash
curl http://localhost/api/admin/users/{user_id}/devices \
  -H "Authorization: Bearer TOKEN_AQUI"
```

### Ver estadísticas del sistema

```bash
curl http://localhost/api/admin/stats \
  -H "Authorization: Bearer TOKEN_AQUI"
```

## Sincronización de Contenido

Para sincronizar el contenido desde la fuente IPTV:

```bash
# Desde el contenedor Docker
docker exec -it iptv-api python scripts/sync_iptv.py

# O manualmente con Python
python scripts/sync_iptv.py
```

Este script descarga la playlist fuente y actualiza la base de datos con canales, películas y series.

## Detección de Dispositivos

El sistema detecta automáticamente el tipo de dispositivo basándose en el User-Agent:

| Tipo | Ejemplos |
|------|----------|
| `tv` | TiviMate, Perfect Player, Kodi, Smart TVs |
| `mobile` | IPTV Smarters, GSE Smart IPTV, Android/iPhone |
| `desktop` | VLC, Chrome, Firefox |
| `iptv_app` | Apps IPTV específicas |
| `unknown` | Dispositivo no identificado |

## Respuestas de Error

Todas las respuestas de error siguen el mismo formato:

```json
{
  "error": "NotFound",
  "message": "Usuario no encontrado",
  "details": {"id": "123"},
  "status_code": 404
}
```

| Código | Error | Significado |
|--------|-------|-------------|
| 400 | BadRequest | Petición inválida |
| 401 | Unauthorized | Credenciales inválidas o token expirado |
| 403 | Forbidden | Cuenta desactivada, expirada o sin permisos |
| 404 | NotFound | Recurso no encontrado |
| 409 | Conflict | Recurso ya existe (duplicado) |
| 429 | TooManyRequests | Límite de dispositivos alcanzado |
| 500 | InternalError | Error interno del servidor |
| 502 | BadGateway | Error al obtener stream |
| 503 | ServiceUnavailable | Servicio no disponible |

## Configuración Avanzada

### Límites de conexiones

Cada usuario tiene un `max_connections` que define cuántos dispositivos pueden usar la cuenta simultáneamente. Cuando se alcanza el límite, nuevos dispositivos reciben error 429.

### Sesiones inactivas

Las sesiones se marcan como activas con cada solicitud de stream. Las sesiones que no tienen actividad durante `SESSION_TIMEOUT_MINUTES` (default: 30) se eliminan automáticamente.

### Forzar desconexión

Los administradores pueden:
1. Desconectar un dispositivo específico
2. Desconectar todos los dispositivos de un usuario
3. Ejecutar limpieza manual de sesiones

## Documentación API

- **Swagger UI**: `http://tu-dominio.com/docs`
- **ReDoc**: `http://tu-dominio.com/redoc`

## Colección Postman

En el directorio `postman/` encontrarás:
- `postman.json`: Colección completa de endpoints
- `environment.json`: Variables de entorno

Importa ambos archivos en Postman para probar todos los endpoints.

## Desarrollo

### Estructura de servicios

Los servicios principales están en `services/`:

- **UserService**: Gestión de usuarios, autenticación, validación de credenciales
- **DeviceService**: Control de sesiones y dispositivos conectados
- **PlaylistService**: Generación de playlists M3U, filtros, estadísticas
- **StreamProxyService**: Proxy de streams, cache de URLs
- **ContentService**: Gestión unificada de canales, películas y series
- **PostgresService**: Consultas SQL directas con psycopg2 para operaciones complejas (DISTINCT, GROUP BY) sin límite de 1000 registros
- **PostgresService**: Consultas SQL directas con psycopg2 (DISTINCT, GROUP BY)

### Dependencias reutilizables

En `utils/dependencies.py`:
- `get_user_service()`, `get_content_service()`: Obtener servicios
- `require_admin()`: Verificar rol de administrador
- `require_auth_with_credentials()`: Validar credenciales user/pass
- `require_auth_with_session()`: Validar credenciales + registrar sesión

### Excepciones personalizadas

En `utils/exceptions.py`:
- `NotFoundException`: Recurso no encontrado (404)
- `BadRequestException`: Petición inválida (400)
- `UnauthorizedException`: No autorizado (401)
- `ForbiddenException`: Acceso prohibido (403)
- `ConflictException`: Conflicto (409)
- `TooManyRequestsException`: Rate limiting (429)

### Modelos de datos

Los modelos Pydantic están en `utils/models.py`:
- `UserCreate`, `UserUpdate`, `UserResponse`: Gestión de usuarios
- `Token`: Respuesta de autenticación JWT
- `DeviceResponse`, `SessionInfo`: Información de dispositivos
- `SystemStats`: Estadísticas del sistema
- `PaginationParams`, `PaginatedResponse`: Paginación

### Configuración

La configuración centralizada está en `utils/config.py` usando Pydantic Settings.

## Changelog

### v2.1 (2024)
- **Nuevo**: Endpoints unificados para contenido (`/api/content`)
- **Nuevo**: Consultas SQL directas con psycopg2 (sin límite de 1000 registros)
- **Nuevo**: Soporte para DISTINCT y GROUP BY en PostgreSQL
- **Nuevo**: Paginación estándar con `page`/`page_size`
- **Nuevo**: Búsqueda por nombre con parámetro `search`
- **Nuevo**: Excepciones personalizadas para manejo de errores consistente
- **Nuevo**: Dependencias reutilizables para autenticación
- **Nuevo**: Endpoint unificado para streams (`/stream/{type}/...`)
- **Nuevo**: Autenticación Bearer Token para endpoints de contenido
- **Mejorado**: Estructura de endpoints con prefijos `/api/admin/`
- **Eliminado**: Endpoints legacy (`/api/channels`, `/api/movies`, `/api/series`, `/live/`, `/movie/`, `/series/`)

### v2.0
- **Nuevo**: Autenticación JWT completa
- **Nuevo**: Control de dispositivos y sesiones
- **Nuevo**: Generación dinámica de playlists M3U
- **Nuevo**: Proxy de streams con autenticación
- **Nuevo**: Soporte para canales, películas y series

## Licencia

MIT
