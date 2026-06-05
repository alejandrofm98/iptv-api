# AGENTS.md - IPTV API

> Guia para agentes de codigo que trabajen en iptv-api y en su ecosistema
> de proyectos hermanos (scrapper, Android, web). Secciones 0-3 son
> contexto obligatorio antes de tocar nada. Secciones 4-10 son referencia
> operativa.

## 0. Ecosistema y posicion del proyecto

iptv-api es el nodo central de un ecosistema de 3 proyectos hermanos del mismo owner (`alejandrofm98`).

```
   +-------------+        +-------------+
   |  walactvWeb |        |   WalacTV   |
   |  Angular 20 |        |  Android TV |
   |  :4200      |        |  Kotlin     |
   +------+------+        +-----+-------+
          |   HTTP + JWT       |
          |  (REST + HLS web)  |  (REST + HLS android)
          v                    v
   +--------------------------------------+
   |          iptv-api (este)             |
   |     FastAPI @ localhost:3010         |
   |  - REST + HLS proxy                  |
   |  - Postgres / Supabase               |
   +---+----------+-----------+-----------+
       |          |           |
       | lee JSON | escribe   | escribe scraper_failures
       v          v           v
   +--------+ +----------+ +-----------+
   |walactv-| | iptv-data| | Postgres  |
   |scrapper| | volumen  | | tabla     |
   | (Ofelia| | compartido| | scraper_  |
   |  cron) | |  (JSONs) | | failures  |
   +--------+ +----------+ +-----------+
```

### Tabla de proyectos hermanos

| Proyecto           | Rol                  | Stack                                | Repo                                                 | Relacion con iptv-api                                              |
| ------------------ | -------------------- | ------------------------------------ | ---------------------------------------------------- | ----------------------------------------------------------------- |
| walactv-scrapper   | Productor catalogos  | Python 3.12, asyncpg, Ofelia, Ansible| `github.com/alejandrofm98/walactv-scrapper`         | Escribe JSONs en `../walactv-scrapper/data/json/` y en `scraper_failures` (ver 4.3) |
| WalacTV (Android)  | Cliente TV           | Kotlin, Android TV                   | `github.com/alejandrofm98/WalacTV`                   | Consume endpoints REST de 4.1 + HLS con perfil `chromecast`       |
| walactvWeb         | Cliente web          | Angular 20                           | `github.com/alejandrofm98/walactvWeb`                | Consume endpoints REST + HLS con perfil `web`                     |

### Proyectos NO relacionados

`pistas-deportivas-frontend` (otro owner `JLAC008`, reserva de pistas deportivas) convive en este workspace pero no comparte codigo, datos ni redes con iptv-api. 0 referencias cruzadas. Tratar como proyecto ajeno.

## 1. Contexto rapido

- **Stack**: FastAPI 0.128, Pydantic 2.12, SQLAlchemy 2.0, alembic,
  psycopg2-binary, bcrypt, httpx, yt-dlp, curl_cffi.
- **Puerto local**: `3010`. **Entry point**: `scripts/api.py` (2256
  lineas, monolito legacy que monta routers y lifespan).
- **Lifespan**: `scripts/api.py:138-175` — pre-carga cache de streams
  y arranca background tasks de cleanup.
- **BD**: Postgres directo (psycopg2) o Supabase (cliente HTTP). Modelos
  en `app/models/`, esquema en `app/schemas/`, migraciones en
  `alembic/versions/`.
- **Auth**: JWT propio (`API_SECRET_KEY`, `JWT_SECRET`) + bcrypt para
  passwords. Dispositivos y sesiones viven en `app/services/`.

```bash
python -m uvicorn scripts.api:app --reload --host 0.0.0.0 --port 3010
curl http://localhost:3010/health
docker compose -f docker/docker-compose.yml up -d --build
```

## 2. Arquitectura

### 2.1 Capas (`app/`)

```
app/
  models/        # ORM SQLAlchemy 2.0
  repositories/  # Acceso a datos por entidad
  services/      # Logica de negocio
  schemas/       # DTOs Pydantic (request/response)
  database.py    # Engine + session factory
  core/          # (en construccion) config, security, exceptions
```

Regla: router importa `services` (no repos directo). Service compone repos. Repo solo toca SQLAlchemy.

### 2.2 `services/` legacy (primer nivel)

Convive con `app/services/` por migracion gradual. Contiene piezas anteriores al corte a capas:

- `services/bulk_insert.py` — carga masiva de catalogos desde JSONs del scrapper.
- `services/video_extractor_service.py` — extraccion de streams via yt-dlp / curl_cffi.
- `services/transcode_service.py` — transcoding HLS.
- `services/resilience_service.py` — reintentos y circuit breakers.

Regla: NO anadir logica nueva aqui. Si necesitas tocar estas piezas, migralas a `app/services/` primero.

### 2.3 Wiring de servicios

`utils/dependencies.py` expone factories `Depends(...)`:

| Factory                       | Devuelve                  | Uso                                  |
| ----------------------------- | ------------------------- | ------------------------------------ |
| `get_content_service`         | `ContentService`          | Endpoints de catalogo y busqueda     |
| `get_user_service`            | `UserService`             | Auth, registro, perfil               |
| `get_device_service`          | `DeviceService`           | Gestion de dispositivos              |
| `get_stream_service`          | `StreamService`           | `/live`, `/movie`, `/series`         |
| `get_watch_progress_service`  | `WatchProgressService`    | Continuidad de visionado             |
| `get_calendar_service`        | `CalendarService`         | EPG y programacion                   |
| `get_postgres_service`        | `PostgresService`         | Queries complejas de catalogo        |
| `get_playlist_service`        | `PlaylistService`         | Generacion M3U                       |

Cadena (3 niveles de indireccion, intencional):

```
ruta -> utils/dependencies.get_xxx_service
       -> app/services/xxx_service
       -> app/repositories/xxx_repo
       -> app/database.py (Session)
```

### 2.4 Convenciones de codigo

- Clases: `PascalCase`. Funciones / variables / modulos: `snake_case`. Constantes: `UPPER_CASE`.
- Tipado: obligatorio en funciones nuevas y cambios significativos.
- Docstrings: en espanol, breves y claras (1-2 lineas).
- Imports: stdlib -> terceros -> locales.
- Sin emojis en codigo, comentarios ni docs.

## 3. Patrones obligatorios

1. Inyectar servicios via `Depends(...)` desde `utils/dependencies.py`.
2. Para errores de negocio, usar excepciones de `utils/exceptions.py`
   (no levantar `HTTPException` directo). Disponibles:
   `BadRequestException` (400), `UnauthorizedException` (401),
   `ForbiddenException` (403), `NotFoundException` (404),
   `ConflictException` (409), `TooManyRequestsException` (429),
   `ServiceUnavailableException` (503).
3. En endpoints administrativos, exigir `require_admin`.
4. En endpoints de catalogo, usar `require_auth_with_jwt`.
5. En endpoints de stream (`/live`, `/movie`, `/series`), usar
   validacion por credenciales en path + registro de sesion.
6. Paginacion: `page` / `page_size` (nunca `skip` / `limit`).
7. Para consultas complejas de contenido, preferir `PostgresService`
   (en `app/services/`) antes que N+1 en repos.
8. **Endpoints consumidos por Android**: aceptar parametro
   `client=android` y aplicar helpers `_to_android_*` de
   `app/services/content_service.py` para normalizar respuesta.
9. **Listados grandes**: usar repos + paginacion obligatoria (sin
   `all()` ni `.to_list()` sin limite explicito).
10. **Ruta nueva expuesta a Android/Web**: registrarla en 4.1 o 4.2 en
    el mismo PR. Si la rompes, rompes clientes.

## 4. Contratos publicos (cross-project)

Estos endpoints son contratos con proyectos hermanos. Cambiarlos sin
coordinar rompe clientes en produccion.

### 4.1 Endpoints consumidos por WalacTV Android

Lista exhaustiva (verificada contra el cliente Kotlin):

- `POST /api/auth/login`
- `GET /api/watch-progress`
- `PUT /api/watch-progress/{id}`
- `GET /api/content/stats?content_type={channels|movies|series}`
- `GET /api/full/{channels|movies|series}`
- `GET /api/content/channels/all`
- `GET /api/channel-favorites`
- `POST /api/channel-favorites`
- `DELETE /api/channel-favorites`
- `GET /api/content/countries?content_type=...`
- `GET /api/search?q=...&page=1&page_size=60`
- `GET /api/content/{kind}/{id}`
- `GET /api/series/{name}/episodes?page=...&page_size=100`
- `GET /api/content?...&country=...&page=...&page_size=...`
- `GET /api/home?country=...`
- `GET /api/calendar/{today}?client=android`
- `GET /live/{username}/{password}/{channelId}`
- `GET /movie/{username}/{password}/{providerId}`

Reglas: paginacion fija, param `client=android` donde aplique, y shape
de respuesta estable (no anadir campos requeridos sin versionar).

### 4.2 Endpoints consumidos por walactvWeb

Reutiliza el grueso de 4.1 (mismo backend). Ademas:

- HLS con perfil `web` (vs `chromecast` en Android). El campo
  `hls_profile` en sesion distingue uno de otro.
- `IPTV_API_URL=http://localhost:3010` en `docker/dockerfile` del
  cliente web.
- Mismas reglas de paginacion y versionado que 4.1.

### 4.3 Datos producidos por walactv-scrapper

El scrapper es upstream. iptv-api consume su output.

- **JSONs de catalogo**: `../walactv-scrapper/data/json/`. Lectura en
  `scripts/api.py:710`, `scripts/api.py:744`, `scripts/api.py:778` y
  `app/services/content_service.py:1251`.
- **Tabla `scraper_failures`**: modelo en `app/models/scraper.py:11`.
  El scrapper escribe filas; `app/repositories/scraper_repo.py` las
  lee para alertas.
- **Volumen compartido**: `iptv-data` (Docker volume / NFS). Mismo
  path desde ambos contenedores.
- **Red compartida**: `dokploy-network`. Reservada para futuros
  health-checks mutuos.
- **Variables de entorno comunes**: `IPTV_API_URL=http://localhost:3010`,
  `API_SECRET_KEY` (mismo valor en ambos lados).

### 4.4 Checklist de breaking change

Si tocas un endpoint listado en 4.1, 4.2 o un path de 4.3:

- [ ] Avisar en el canal del owner antes de mergear.
- [ ] Versionar el endpoint (`/api/v2/...`) si el cambio es de shape, no solo anadir campos opcionales.
- [ ] Actualizar `README.md` y la coleccion Postman (`postman/`).
- [ ] Verificar build del cliente (Android: `./gradlew assembleDebug`; Web: `ng build`).
- [ ] Coordinar deploy: scrapper, API y cliente suelen ir en orden scrapper -> API -> cliente.

## 5. Configuracion y secretos

`utils/config.py:18-22` carga `.env` en este orden (el primero que
existe gana):

1. `utils/.env`
2. `docker/.env`
3. `.env` (raiz)

**Variables minimas (obligatorias)**: `SUPABASE_URL`, `SUPABASE_KEY`,
`API_SECRET_KEY`, `JWT_SECRET`.

**Postgres opcionales** (si no, se usa Supabase HTTP): `PG_HOST`,
`PG_PORT`, `PG_DATABASE`, `PG_USER`, `PG_PASSWORD`.

**Runtime**: `PUBLIC_DOMAIN` (base de URLs de streams servidas al
cliente), `M3U_DIR` (directorio de salida de M3Us generadas).

Reglas:

- Nunca commitear `.env` ni credenciales.
- `.env.example` (si existe) es la unica referencia commiteable.
- Redactar secrets en logs.

## 6. Lint, formato, tipos y calidad

Toda la config vive en `pyproject.toml`.

### 6.1 Comandos

```bash
ruff format scripts services utils app tests
ruff check scripts services utils app tests --fix
mypy scripts services utils app
vulture scripts services utils app --min-confidence 80
pytest tests/
```

### 6.2 Reglas activas (resumen de `pyproject.toml`)

- Line length: 100 (verificar en `[tool.ruff]`).
- Target Python: 3.12.
- Selectors ruff: `E`, `F`, `W`, `I`, `N`, `UP`, `B`, `C4`, `SIM`.
- mypy: `strict = true` en modulos nuevos; resto en modo progresivo.
- vulture: umbral 80% para evitar falsos positivos.

### 6.3 Pre-commit y CI

NO estan configurados todavia. Roadmap en 9. Mientras tanto: correr
los comandos de 6.1 manualmente antes de cada PR.

### 6.4 Como correr TODO

```bash
ruff format scripts services utils app tests && \
  ruff check scripts services utils app tests --fix && \
  mypy scripts services utils app && \
  vulture scripts services utils app --min-confidence 80 && \
  pytest tests/
```

## 7. Testing

- Framework: `pytest` (ver `requirements-test.txt`).
- Tests viven en `tests/`:
  - `test_android_catalog_api.py` — contrato con cliente Android.
  - `test_content_service_normalized.py` — normalizacion de catalogos.
  - `test_watch_progress_service.py` — continuidad de visionado.
- `conftest.py` deberia proveer fixtures de `Session` y clientes
  Supabase/Postgres mockeados (verificar si existe; si no, anadir).
- Patrones:
  - Mockear I/O externo (httpx, yt-dlp) — nada de red en tests.
  - Postgres real solo en tests marcados `@pytest.mark.integration`.
  - Cubrir helpers `_to_android_*` con tests dedicados (son contrato
    publico).

## 8. Criterios para cambios

1. No romper flujos de sesiones/dispositivos al tocar streams. Cada cambio en `/live`, `/movie`, `/series` o `/hls/*` requiere verificar registro y limpieza de sesion.
2. Si modificas rutas o contratos, actualizar `README.md`, la coleccion `postman/`, y 4.1/4.2 en este archivo en el mismo PR.
3. Evitar cambios de estilo no relacionados en archivos no tocados. Un PR = un motivo.

## 9. Roadmap (no en esta iteracion)

1. Activar pre-commit (`pre-commit install`) con ruff + mypy + vulture.
2. CI en GitHub Actions corriendo 6.4 en cada PR.
3. Migrar los 4 modulos de `services/` legacy a `app/services/`.
4. Crear `app/core/` y mover `utils/config.py`, `utils/exceptions.py`
   y un futuro `utils/security.py`.
5. Versionado explicito de API (`/api/v1/...`, `/api/v2/...`) antes
   del siguiente breaking change.

## 10. Checklist antes de cerrar una tarea

1. Codigo formateado: `ruff format` sin diffs.
2. Lint limpio: `ruff check` sin warnings.
3. Tipos: `mypy` sin errores en modulos tocados.
4. Tests: `pytest tests/` pasa, y se anadieron tests si hay logica nueva.
5. Si tocaste un endpoint de 4.1/4.2 o path de 4.3: actualizaste `README.md`, `postman/`, y este archivo.
6. Probaste el endpoint afectado con `curl` o Postman (minimo: 200/401/403/404 segun aplique).
7. Sin secretos en el diff (`git diff --staged | grep -iE 'key|secret|token|password'`).
8. Sin emojis en codigo, comentarios ni docs.

## 11. Acceso a base de datos

Para verificar datos en Postgres, usar `pgcli` (instalado en el sistema).

Las credenciales estan en `.env` en la raiz del proyecto (nunca commiteado).
Si no existe, crear uno con las variables `PG_HOST`, `PG_PORT`, `PG_DATABASE`,
`PG_USER`, `PG_PASSWORD` que aparecen en `utils/config.py`.

```bash
source .env 2>/dev/null || true
PGPASSWORD="$PG_PASSWORD" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DATABASE" -c "SELECT ...;"
```

O connectarse directamente con pgcli:
```bash
pgcli -h $PG_HOST -p $PG_PORT -U $PG_USER -d $PG_DATABASE
```

### Tablas principales

| Tabla | Descripcion |
|-------|-------------|
| `movies_catalog` | Catalogo de peliculas (UUID PK, provider_id, tmdb_id) |
| `movie_streams` | Streams de peliculas (FK a movies_catalog) |
| `series_catalog` | Catalogo de series (UUID PK, provider_id, tmdb_id) |
| `series_episodes` | Episodios de series (FK a series_catalog) |
| `series_streams` | Streams de episodios (FK a series_episodes, tiene provider_id) |
| `channels` | Canales en vivo |
| `watch_progress` | Progreso de visionado por usuario |
| `scraper_failures` | Fallos del scraper |

### Cuidado con IDs

- `movies_catalog.provider_id` y `series_catalog.provider_id` son strings numericos (ej: "1394135")
- `series_streams.provider_id` es el ID del stream de un **episodio** individual (ej: "1418278"), NO de la serie
- Para buscar una serie por episode provider_id: JOIN series_streams -> series_episodes -> series_catalog
