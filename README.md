# IPTV API

API FastAPI para gestión de usuarios IPTV, catálogo de contenido y proxy de streams.

## Qué incluye

- Autenticación JWT para endpoints `/api/*`
- Gestión de usuarios y sesiones/dispositivos por cuenta
- Catálogo unificado de contenido (`channels`, `movies`, `series`) con paginación `page/page_size`
- Endpoints de calendario deportivo con canales resueltos
- Playlist vía `/get.php`
- Proxy de streams (`/live`, `/movie`, `/series`) con control de conexiones
- Perfil HLS específico para Chromecast en directo
- Transcodificación HLS bajo demanda para clientes web permitidos
- Proxy de logos con fallback automático a placeholders

## Stack

- Python + FastAPI
- Supabase (PostgreSQL)
- psycopg2 para consultas SQL complejas (DISTINCT/GROUP BY)
- Nginx + Traefik (en Docker)

## Estructura

```text
iptv-api/
├── scripts/           # API principal (api.py)
├── services/          # Lógica de negocio (usuarios, streams, contenido, calendario, etc.)
├── utils/             # Configuración, modelos, constantes, excepciones, dependencias
├── docker/            # Dockerfile, compose, env de Docker
├── nginx/             # Config de Nginx
├── resources/images/  # Placeholders de logos
└── data/m3u/          # Plantillas y archivos M3U
```

## Configuración

La app carga variables en este orden:

1. `utils/.env`
2. `docker/.env`
3. `.env` (raíz)

Variables mínimas:

- `SUPABASE_URL`
- `SUPABASE_KEY`
- `API_SECRET_KEY`
- `JWT_SECRET`

Variables opcionales PostgreSQL (si no se definen, se infieren desde Supabase):

- `PG_HOST`, `PG_PORT`, `PG_DATABASE`, `PG_USER`, `PG_PASSWORD`

Variables importantes de runtime:

- `PUBLIC_DOMAIN` (usado para construir URLs de streams)
- `M3U_DIR` (directorio de salida M3U)

## Ejecutar local

```bash
pip install -r requirements.txt
python -m uvicorn scripts.api:app --reload --host 0.0.0.0 --port 3010
```

Health check:

```bash
curl http://localhost:3010/health
```

## Ejecutar con Docker

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

## Flujos de autenticación

### 1) JWT (API administrativa y catálogo)

Login:

```bash
curl -X POST http://localhost:3010/api/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=admin123"
```

Usar token:

```bash
curl http://localhost:3010/api/admin/users \
  -H "Authorization: Bearer <TOKEN>"
```

### 2) Credenciales en URL (players)

- Playlist: `/get.php?username=...&password=...`
- Streams: `/live/{user}/{pass}/{id}`, `/movie/{user}/{pass}/{id}`, `/series/{user}/{pass}/{id}`
- Chromecast live: `/cast/live/{user}/{pass}/{id}/playlist.m3u8`

## Endpoints principales

### Salud

- `GET /`
- `GET /health`

### Auth

- `POST /api/auth/login`

### Admin (Bearer + rol admin)

- `POST /api/admin/users`
- `GET /api/admin/users`
- `GET /api/admin/users/{user_id}`
- `PUT /api/admin/users/{user_id}`
- `DELETE /api/admin/users/{user_id}`
- `GET /api/admin/users/{user_id}/devices`
- `DELETE /api/admin/users/{user_id}/devices/{device_id}`
- `DELETE /api/admin/users/{user_id}/devices`
- `GET /api/admin/sessions`
- `GET /api/admin/stats`
- `GET /api/admin/resilience`
- `POST /api/admin/content/reload`

### Content (Bearer)

- `GET /api/content/groups`
- `GET /api/content/countries`
- `GET /api/content`
- `GET /api/content/{content_type}/{item_id}`
- `GET /api/content/stats`
- `GET /api/series/{serie_name}/episodes`

### Calendario (Bearer)

- `GET /api/calendar/{fecha}`
- `GET /api/calendar/event/{event_id}`

### Streaming

- `GET /get.php`
- `GET /{live|movie|series}/{username}/{password}/{stream_id}`
- `GET /{username}/{password}/{stream_id}` (atajo para `live`)
- `GET /cast/live/{username}/{password}/{stream_id}/playlist.m3u8`
- `GET /auth/validate-stream/{content_type}/{username}/{password}/{provider_id}`
- `GET /internal/stream-url`

### HLS y logos

- `GET /hls/{session_id}/playlist.m3u8`
- `GET /hls/{session_id}/{segment}`
- `GET /logo?url=<encoded_url>&type=channel|movie|series`

## Ejemplos rápidos

Grupos de canales:

```bash
curl "http://localhost:3010/api/content/groups?content_type=channels" \
  -H "Authorization: Bearer <TOKEN>"
```

Playlist estándar:

```bash
curl "http://localhost:3010/get.php?username=<USER>&password=<PASS>&type=m3u_plus&output=ts"
```

Stream en vivo:

```bash
curl -I "http://localhost:3010/live/<USER>/<PASS>/<STREAM_ID>"
```

## Respuestas de error

Formato estándar:

```json
{
  "error": "NotFound",
  "message": "Usuario no encontrado",
  "details": {"id": "123"},
  "status_code": 404
}
```

Excepciones principales en `utils/exceptions.py`:

- `BadRequestException` (400)
- `UnauthorizedException` (401)
- `ForbiddenException` (403)
- `NotFoundException` (404)
- `ConflictException` (409)
- `TooManyRequestsException` (429)

## Desarrollo

Lint/format recomendado:

```bash
pip install ruff mypy black isort
black scripts services utils
isort scripts services utils
ruff check scripts services utils
mypy scripts services utils
```

Docs interactivas:

- `http://localhost:3010/docs`
- `http://localhost:3010/redoc`

## Seguridad

- No subas `.env` al repositorio
- Cambia `API_SECRET_KEY` y `JWT_SECRET` en producción
- Mantén `PUBLIC_DOMAIN` correcto para URLs de stream

## Licencia

MIT
