# AGENTS.md - IPTV API Development Guide

Guia para agentes de codigo que trabajen en este proyecto FastAPI.

## Contexto rapido

- Proyecto: API IPTV con JWT, gestion de usuarios/dispositivos, proxy de streams y HLS.
- App principal: `scripts/api.py`
- Puerto local por defecto: `3010`

## Comandos clave

```bash
# Instalar dependencias
pip install -r requirements.txt

# Ejecutar API local
python -m uvicorn scripts.api:app --reload --host 0.0.0.0 --port 3010

# Health check
curl http://localhost:3010/health

# Docker (desde la raiz)
docker compose -f docker/docker-compose.yml up -d --build
```

## Lint y formato

```bash
pip install ruff mypy black isort
black scripts services utils
isort scripts services utils
ruff check scripts services utils
mypy scripts services utils
```

## Convenciones de codigo

- Clases: `PascalCase`
- Funciones/variables/modulos: `snake_case`
- Constantes: `UPPER_CASE`
- Tipado: obligatorio en nuevas funciones y cambios significativos
- Docstrings: en espanol, breves y claras
- Imports: stdlib -> terceros -> locales

## Arquitectura del proyecto

```text
scripts/          # Entrypoints y rutas (api.py)
services/         # Logica de negocio
utils/            # Config, modelos, dependencias, excepciones, constantes
docker/           # Dockerfile y compose
nginx/            # Config de reverse proxy
resources/images/ # Placeholders para /logo
data/m3u/         # Plantillas y cache M3U
```

## Patrones obligatorios

1. Inyectar servicios via `Depends(...)` desde `utils/dependencies.py`.
2. Para errores de negocio, usar excepciones de `utils/exceptions.py`.
3. En endpoints administrativos, exigir `require_admin`.
4. En endpoints de catalogo, usar `require_auth_with_jwt`.
5. En endpoints de stream, usar validacion por credenciales + registro de sesion.
6. Mantener paginacion `page/page_size` (evitar `skip/limit`).
7. Para consultas complejas de contenido, preferir `PostgresService`.

## Endpoints importantes

- Auth: `POST /api/auth/login`
- Admin: `/api/admin/users*`, `/api/admin/sessions`, `/api/admin/stats`, `/api/admin/resilience`, `/api/admin/content/reload`
- Contenido: `/api/content*`, `/api/series/{serie_name}/episodes`
- Calendario: `/api/calendar/{fecha}`, `/api/calendar/event/{event_id}`
- Streams: `/live/{u}/{p}/{id}`, `/movie/{u}/{p}/{id}`, `/series/{u}/{p}/{id}`
- HLS: `/hls/{session_id}/playlist.m3u8`, `/hls/{session_id}/{segment}`
- Logo proxy: `/logo`

## Configuracion y secretos

- Variables minimas: `SUPABASE_URL`, `SUPABASE_KEY`, `API_SECRET_KEY`, `JWT_SECRET`.
- Variables PG opcionales: `PG_HOST`, `PG_PORT`, `PG_DATABASE`, `PG_USER`, `PG_PASSWORD`.
- Nunca commitear `.env` o credenciales.

## Criterios para cambios

- No romper flujos de sesiones/dispositivos al tocar streams.
- Si se modifican rutas o contratos, actualizar `README.md` y coleccion Postman.
- Evitar cambios de estilo no relacionados en archivos no tocados.

## Checklist antes de cerrar una tarea

1. Codigo formateado/lint sin errores criticos.
2. Endpoints afectados probados de forma minima (curl/manual).
3. README actualizado si hay cambios funcionales.
4. Sin secretos en diffs.
