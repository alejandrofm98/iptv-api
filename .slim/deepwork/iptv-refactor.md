# Deepwork: Refactor iptv-api + walactv-scrapper -> paquete compartido iptv-db

## Goal
Refactorizar iptv-api y walactv-scrapper para:
1. Eliminar duplicacion cross-project (modelos ORM, schemas, conexiones BD, JSONs, configuracion, errores).
2. Mejorar estructura y mantenibilidad: capas claras, sin monolito legacy, dependencias explicitas.
3. Extraer todo lo de BD a un proyecto aparte `iptv-db` (paquete Python), consumido por ambos.
4. Usar Alembic como unica fuente de verdad para migraciones (sustituyendo SQL manual y supabase/migrations mixto).
5. DTOs Pydantic claros, uno-a-uno con tablas (read models / write models / API models separados).
6. Tests ampliados: contratos publicos (4.1/4.2), helpers `_to_android_*`, scraper de outputs, migraciones Alembic.
7. Lint limpio: ruff + mypy + vulture sin warnings, pre-commit y CI activos.

## Understanding
- iptv-api: FastAPI monolito (`scripts/api.py` ~2256 lineas). BD via SQLAlchemy 2.0 (Postgres o Supabase HTTP).
- walactv-scrapper: ingiere catalogos, escribe JSONs + tabla `scraper_failures` en misma BD.
- Ambos comparten: modelos ORM (parcialmente), configuracion de BD, secrets, error handling.
- Estado actual de BD: schema disperso (alembic + supabase/migrations + database/insert.sql + scripts). Migraciones inconsistentes.

## Phases (resumen; se detalla despues de discovery)
- P0 Discovery: mapear ambos proyectos, duplicados, contratos compartidos, estado Alembic/Supabase.
- P1 Arquitectura: decidir estructura de `iptv-db` (modelos, migraciones, DTOs base), contratos de interfaces.
- P2 Extraccion: crear `iptv-db` con modelos + Alembic + DTOs, sin romper iptv-api.
- P3 Migracion iptv-api: importar iptv-db, eliminar modelos locales, ajustar imports, tests.
- P4 Migracion walactv-scrapper: importar iptv-db, eliminar duplicados, usar mismas migraciones.
- P5 Tests: ampliar cobertura (contratos publicos, scraper, migraciones), configurar CI.
- P6 Lint/CI: ruff estricto, mypy strict, pre-commit, GitHub Actions.

## Estado
- P0: en curso (scrapper mapeado, iptv-api pendiente)

## Hallazgos P0 — walactv-scrapper (confirmado)

### Estructura
- ~9.366 lineas de Python en `scripts/` (monolitos).
- 5 requirements files fragmentados por servicio Docker.
- Sin ORM (sin SQLAlchemy). Todo SQL embebido.
- Sin Pydantic. Solo dataclasses sueltas para resultados TMDB y bulk insert.
- `database/schema.sql` (678 lineas) como DDL de referencia, versionado en git. NO usa Alembic.
- 0 tests de integracion. Solo 2 archivos: `test_database.py` (singleton), `test_sync_iptv.py` (normalizacion basica).

### Drivers BD
- 2 drivers coexistiendo: `asyncpg` (con pool singleton en `database.py`/`config.py`) y `psycopg2` (sin pool, conexion directa en scripts TMDB/IMDb: `scrape_tmdb_metadata.py`, `backfill_imdb_ids.py`, `import_imdb_ratings.py`, `populate_episode_imdb_ids.py`).
- Logica de conexion duplicada en 4 archivos.

### Code smells
- `scripts/sync_iptv.py` 1.679 lineas: descarga M3U, parseo, normalizacion, upsert 6 tablas, JSON, metricas, todo en un archivo.
- `scripts/sync_replays.py` 1.449 lineas: una clase con 30+ metodos, resolvers Node.js.
- `scripts/database.py` 640 lineas: 8 clases mezcladas (pool, channel mappings, calendario, replays, manager legacy).
- 344 ocurrencias de `print()` con emojis como logging.
- `except Exception: print(...)` patron uniforme (no hay jerarquia).
- SQL string concat en `sync_iptv.py:582`.

### Duplicacion con iptv-api (mismas tablas, ambas escriben)
- `channels`, `movies_catalog`, `movie_streams`, `series_catalog`, `series_episodes`, `series_streams`
- `scraper_failures` (scrapper escribe, iptv-api lee)
- `movies_metadata`, `series_metadata` (scrapper escribe, iptv-api lee)
- `calendario` (scrapper escribe, iptv-api lee)
- `config`, `sync_metadata`

### Env vars compartidas
- `PG_HOST/PORT/DATABASE/USER/PASSWORD`, `API_SECRET_KEY`, `PUBLIC_DOMAIN`
- Variables propias: `TMDB_API_KEY`, `TMDB_READ_TOKEN`, `EPG_USER/PASS`, `M3U_DIR`, `IMAGES_DIR`, `DOCKER_CONTAINER`, `DATABASE_URL`, `EVENT_IMAGES_RETENTION_DAYS`

### Secret leak
- `docker/.env` **commiteado con credenciales reales**. Hay `.env-example` separado. Hay que mover `.env` a `.gitignore` y rotar.

### Cron (Ofelia)
- 6 jobs: `futbol-daily`, `iptv-sync` (6h), `sync-replays` (12h), `tmdb-metadata-sync` (4 veces al dia), `imdb-ratings` (diario 6h), `imdb-episode-ids` (6:30 diario)

### 3 archivos mas problematicos
1. `scripts/sync_iptv.py` (1.679 LOC)
2. `scripts/sync_replays.py` (1.449 LOC)
3. `scripts/database.py` (640 LOC, 8 clases mezcladas)

---

## Hallazgos P0 — iptv-api (confirmado)

### Estructura
- ~10.919 lineas de Python total.
- 2 archivos concentran 40% del codigo: `scripts/api.py` (2.310) + `app/services/content_service.py` (2.064).
- ORM SQLAlchemy 2.0 con 11 modelos en `app/models/` (canales, contenido, series, usuarios, watch progress, replays, scraper failures, config, sync metadata).
- Alembic con 3 migraciones lineales (`b6608c246678` -> `50987149425c` -> `309bd59b0f90`).
- DTOs en `app/schemas/`. **Duplicados legacy en `utils/models.py` (348 lineas)**.
- Tests: 3 archivos, ~997 lineas. Cobertura muy desigual (watch_progress + android catalog + content normalizacion).

### Modelos ORM
- `MovieMetadata` <-> `SeriesMetadata` identicos estructuralmente (TMDB).
- `MovieCatalog` <-> `SeriesCatalog` casi identicos (solo difiere `series_key` en series).
- `MovieStream` <-> `SeriesStream` identicos salvo FK padre.
- `ChannelFavorite` PK compuesta `(user_id, channel_provider_id)`.
- `WatchProgress` unique `(user_id, content_id, season_number, episode_number)`.

### Duplicacion interna
- `content_repo._flatten_row()` y `series_repo._flatten_row()` identicos.
- `content_repo.search_by_provider_id()` y `series_repo.search_by_provider_id()` identicos.
- `content_repo.get_movies_catalog_page()` y `series_repo.get_distinct_series_groups_catalog()` mismo patron SQL.
- 3 versiones de `get_distinct_groups` (content_repo, series_repo, channel_repo) con logica condicional.
- `COUNTRY_NAMES` definido DOS veces en `content_service.py` (linea 74 y 1888) con tamaños distintos.
- Schemas duplicados entre `app/schemas/` y `utils/models.py`.

### Code smells
- `scripts/api.py` 2.310 lineas, 63 endpoints inline sin routers, 11 `print()` debug, mypy ignorado en `pyproject.toml`.
- `app/services/content_service.py` 2.064 lineas, 6 helpers `_to_android_*`, parseo streams, home, search, replays, DailyMotion, TMDB.
- `utils/config.py:114` `except Exception: pass` silencioso al cargar config desde BD.
- Schema drift: `schema.sql` tiene columnas (`series_episodes.title_en`, `overview_en`, `runtime`, etc.) que NO estan en el ORM `SeriesEpisode`. Esquema desincronizado del modelo.

### Lint
- 26 errores ruff (I001, UP017 x20, SIM118, B033, SIM108). 22 fixables auto.
- 8 errores mypy (Result.rowcount, Column[bool], TextClause vs Select, etc.).
- Vulture: 0 detecciones (no encuentra codigo muerto).
- `scripts/api.py` excluido de mypy y de coverage.

### 5 archivos mas problematicos
1. `scripts/api.py` (2.310 LOC, monolito routers)
2. `app/services/content_service.py` (2.064 LOC, `COUNTRY_NAMES` x2, cache mutable class-level)
3. `services/video_extractor_service.py` (783 LOC, duplica `video-extractor-service/`)
4. `app/repositories/series_repo.py` (460 LOC, SQL concat)
5. `utils/models.py` (348 LOC, schemas duplicados)

### Tests huecos
- `series_repo`, `content_repo`, `stream_service`, `user_service`, `device_service`, `calendar_service`, `channel_favorites_service`, `playlist_service`: 0 tests.
- Helpers `_to_android_series_from_catalog`, `_to_android_movie_from_catalog`, `_to_android_series_group_item`, `_to_android_event`: sin tests unitarios.

---

## Hallazgos cross-project (consolidado)

### Drivers BD divergentes
- **iptv-api**: SQLAlchemy 2.0 + psycopg2-binary + Alembic (3 migraciones).
- **scrapper**: asyncpg (pool) + psycopg2-binary (sin pool, 4 scripts).
- **Ninguna comparticion** de modelos, conexiones, migraciones.

### Migraciones dispersas
- iptv-api: `alembic/versions/` (3 archivos) + `database/schema.sql` (408) + `database/insert.sql` (19).
- scrapper: `database/schema.sql` (678) + `database/insert.sql` (15). Sin Alembic, sin `supabase/migrations/`.
- **Dos `schema.sql` diferentes, ambos referenciando la misma BD. Drift garantizado.**

### Configuracion / secrets
- Ambos leen mismas env vars (`PG_*`, `API_SECRET_KEY`, `PUBLIC_DOMAIN`).
- Carga de `.env` en iptv-api: `utils/.env` -> `docker/.env` -> `.env` (raiz).
- Carga de `.env` en scrapper: `scripts/.env` -> `docker/.env` -> `.env` (raiz).
- Orden similar pero ubicaciones distintas. No hay validacion comun.

### Logging
- iptv-api: 11 `print()` en `api.py` (deberia ser `logger`).
- scrapper: 344 `print()` con emojis (no usa `logging` salvo TMDB e IMDb scripts).

### Excepciones
- iptv-api: jerarquia propia en `utils/exceptions.py` (8 clases HTTP).
- scrapper: `try/except Exception: print(...)` patron, sin jerarquia.

### DTOs / Schemas
- iptv-api: Pydantic v2 en `app/schemas/`. Bien tipados.
- scrapper: 0 Pydantic. Solo 4 dataclasses sueltas.

### Tests
- iptv-api: 3 archivos, ~997 lineas, concentrados en contratos publicos.
- scrapper: 2 archivos, 222 lineas, concentrados en singleton BD + normalizacion M3U.

### Code smells comunes
- `try/except Exception: pass` o swallow.
- SQL string concatenation (no parametros).
- Modulos > 1.000 lineas sin dividir.
- Sin tests de integracion reales (ambos).
- Sin CI (AGENTS.md confirma).

### Secret leak confirmado
- `walactv-scrapper/docker/.env` commiteado con credenciales reales.
- `iptv-api/docker/.env` no commiteado (esta en `.gitignore`).
- **Accion inmediata**: rotar credenciales y purgar `walactv-scrapper` history.

---

## Decisiones de arquitectura a tomar (con Oracle)
- [x] **Empaquetado iptv-db**: monorepo con `iptv-db/` como sub-paquete instalable (`pip install -e ../iptv-db`). Pyproject.toml PEP 621 portable a repo separado en el futuro.
- [x] **Driver BD**: `psycopg` (psycopg3) + SQLAlchemy 2.0 async como target final. Migracion incremental: iptv-db expone `get_async_engine()` y `get_sync_engine()`. Primero scripts one-shot del scrapper, luego bulk ops, luego iptv-api.
- [x] **Migraciones**: Alembic como unica fuente. Borrar `schema.sql` y los 2 existentes. Pre-auditoria: `pg_dump --schema-only` de BD productiva, diff contra ORM actual, documentar cada diferencia en `iptv-db/MIGRATION_NOTES.md` antes de generar migracion inicial.
- [x] **Monolito scripts/api.py**: F0 = routers modulares (mecanico, sin logica), F2 = shims a iptv-db, F5 = limpieza final.
- [x] **DTOs**: DB DTO (en iptv-db, 1:1 con tabla) + API DTO (en iptv-api o iptv-db/schemas/api/). ORM nunca se expone fuera de repos. Documentar en `iptv-db/CONVENTIONS.md`.
- [x] **Duplicacion Movie/Series**: clase base abstracta SQLAlchemy con herencia concrete. `MetadataBase`, `CatalogBase`, `StreamBase`. Mismas tablas, menos codigo. Validar con `alembic revision --autogenerate` que es no-op.
- [x] **Secret leak**: FALSO POSITIVO del explorador. Verificado manualmente: `git ls-files docker/.env` vacio, `git log --all --full-history --oneline -- docker/.env` vacio, `.gitignore:6` ya excluye `/docker/.env`. No hay leak. Descartado.
- [x] **Tests**: mix = mocks para todo + testcontainers Postgres en CI solo para endpoints publicos 4.1/4.2. Marker `@pytest.mark.integration`. Unitarios < 10s, integracion < 30s.

## Decisiones del usuario (sobre dictamen Oracle)
- **Empaquetado**: repo git separado (NO monorepo sub-paquete). iptv-db vive en su propio repo, instalable via `pip install git+https://github.com/alejandrofm98/iptv-db` o editable tras clone.
- **Driver**: psycopg3 + SQLAlchemy 2.0 async. OK.
- **Movie/Series**: herencia concrete con clases base. OK.
- **Tests**: mix mocks + testcontainers. OK.
- **Alcance**: plan completo F0-F5. OK.
- **F0**: se elimina rotacion de credenciales y purga de history (falso positivo confirmado).

## Fases revisadas (post-feedback usuario)

| Fase | Objetivo | Scope | Gate Oracle | Esfuerzo |
|------|----------|-------|-------------|----------|
| **F0** | Routers modulares iptv-api | `iptv-api/scripts/api.py` → `iptv-api/app/routers/*.py` (mecanico, sin logica) | SI (estructura) | 3-4 dias |
| **F1** | Crear iptv-db (repo git separado) con modelos consolidados | `alejandrofm98/iptv-db` (nuevo repo), herencia concrete, DB DTOs, Alembic. Pre-auditoria: `pg_dump --schema-only` y resolver drift antes de autogenerate | SI (base de todo) | 1 sem |
| **F2** | Migrar iptv-api a iptv-db con shims | iptv-api importa iptv-db como dep pip, shims backward compat, `alembic upgrade head` no-op | SI (valida extraccion) | 1 sem |
| **F3** | Migrar scrapper a iptv-db + psycopg3 unico | scrapper importa iptv-db, eliminar SQL embebido, asyncpg→psycopg3, validar perf bulk ops | SI (consolida cross-project) | 1 sem |
| **F4** | Tests + CI | testcontainers Postgres en CI para endpoints 4.1/4.2, pre-commit (ruff+mypy+vulture+gitleaks), GitHub Actions | SI (red de seguridad) | 1 sem |
| **F5** | Limpieza + docs | Eliminar `utils/models.py`, `services/` legacy, shims; docs `iptv-db/CONVENTIONS.md`, `MIGRATION_GUIDE.md`, `DEPLOY_ORDER.md` | SI (solo tras F4 OK) | 1 sem |

| Fase | Objetivo | Scope | Criterio de Done | Riesgo | Gate Oracle |
|------|----------|-------|------------------|--------|-------------|
| **F0** | Remediar secret leak + routers modulares iptv-api | `walactv-scrapper/docker/.env` (rotar, purgar), `iptv-api/scripts/api.py` -> `app/routers/*.py` | Creds rotadas, history purgado, routers funcionando, tests 100% | Ruptura de clones por force push | SI (seguridad + estructura) |
| **F1** | Crear iptv-db con modelos consolidados (sin cambiar schema) | `iptv-db/` (nuevo paquete), modelos SQLAlchemy con herencia concrete, DB DTOs | Instalable, modelos mapean tablas existentes, Alembic config, `upgrade head` no-op | Drift schema no detectado | SI (base para todo) |
| **F2** | Migrar iptv-api a iptv-db (con shims) | `iptv-api/app/models/`, `app/repositories/`, shims backward compat | iptv-api usa iptv-db, tests 100%, endpoints 4.1/4.2 identicos, `upgrade head` no-op | Breaking changes en endpoints publicos | SI (valida extraccion) |
| **F3** | Migrar walactv-scrapper a iptv-db | `walactv-scrapper/scripts/`, eliminar SQL embebido, migrar a psycopg3 | Scrapper usa iptv-db, driver unico, tests 100%, cron jobs sin errores | Bulk ops mas lentos con ORM | SI (consolida cross-project) |
| **F4** | Tests + CI | `*/tests/`, GitHub Actions | Cobertura > 80% en iptv-db, testcontainers en CI, pre-commit | CI lento/flaky | SI (red de seguridad) |
| **F5** | Limpieza final + docs | Eliminar shims legacy, `utils/models.py`, `services/` legacy, docs iptv-db | Sin duplicados, sin shims, docs completas, lint limpio | Eliminacion prematura rompe consumers | SI (solo cuando F4 confirma) |

Estimacion: 4-6 semanas part-time (10-15h/semana). F0=1sem, F1=1sem, F2-F3=2sem, F4-F5=1-2sem.

## Riesgos no anticipados (Oracle)
1. **Dependencia circular**: iptv-db debe tener su propia jerarquia `DatabaseError/NotFoundError/ConstraintViolationError`. iptv-api las mapea a HTTP en service layer.
2. **Performance bulk ops**: migrar asyncpg -> SQLAlchemy ORM puede ralentizar inserts. Mitigacion: `insert().values([...])` con `synchronize_session=False` o `execute_values()` raw.
3. **Resistencia del owner a friccion de setup**: documentar `setup.sh` para `pip install -e ../iptv-db`.

## Veredicto
APROBADO para ejecucion con gates por fase.

---

## ESTADO FINAL — Sesion cerrada por decision del usuario

**Usuario decidio pausar** despues de F3d4b aprobado. Retomar en sesion futura.

### Fases completadas (todas con Oracle gate aprobado)

- **F0**: Routers modulares iptv-api (commit `89e7db9`)
- **F1**: iptv-db repo separado (commit `c8f5291`)
- **F2**: iptv-api migrado a iptv-db con shims (commit `3763924`)
- **F3a-F3d4b**: scrapper migrado a iptv-db (10 commits, `9f81325` → `f326674`)

### Fases pendientes

- **F4**: Tests + CI (pre-commit, GitHub Actions, smoke tests de crons). 1 sem estimada. Subdividido:
  - **F4a**: pre-commit + GitHub Actions CI (3 repos). Bajo-medio riesgo. ~2-3 horas estimada.
  - **F4b**: smoke tests crons + Android contract tests con testcontainers. Medio-alto riesgo. ~3-5 horas estimada.
- **F5**: Limpieza final (eliminar asyncpg legacy, shims iptv-api, services/ legacy, docs). 1-2 sem estimada.

### Commits locales sin push (12)

**iptv-api** (2):
- `89e7db9` F0 (routers modulares)
- `3763924` F2 (shims a iptv-db)

**iptv-db** (1, repo nuevo):
- `c8f5291` F1 (modelos, DTOs, Alembic config)

**walactv-scrapper** (9):
- `9f81325` F3a (database.py infra)
- `a71597d` F3b (4 scripts psycopg2)
- `7dff679` F3c1 (sync_iptv.py SELECTs)
- `49dbbaf` F3c2a (sync_iptv.py writes simples)
- `a5f0da5` F3c2b (sync_iptv.py bulk UPSERT)
- `52fd53f` F3d1 (sync_iptv.py cierre, generate_content_json)
- `6ba615a` F3d2 (poblar_mapeo, scrapper, main)
- `3b24507` F3d3 (sync_replays.py)
- `e0fabbb` F3d4a (database.py, ReplayManager eliminado)
- `f326674` F3d4b (config.py, bulk_insert.py, tests)

### Tests passing

- iptv-api: 21/25 (4 pre-existentes: StubContentService)
- walactv-scrapper: 48/48 (4 nuevos en test_database.py para iptv-db API)
- iptv-db: 4/4 (en repo separado)

### Lint

- 0 nuevos issues introducidos en F3
- 7 RUF013 pre-existentes en database.py (nit de tipado)
- 9 issues pre-existentes en iptv-api (F6 los limpia)
- 3 issues pre-existentes en sync_iptv.py (nit)

### Decisiones de arquitectura (recordatorio)

- **Empaquetado iptv-db**: repo git separado (NO monorepo)
- **Driver**: psycopg3 + SQLAlchemy 2.0 async
- **Movie/Series**: herencia concrete con clases base (MetadataBase, CatalogBase, StreamBase)
- **SeriesCatalog**: NO hereda de CatalogBase por `series_key` (decision Oracle, F1)
- **Tests**: mix mocks + testcontainers en CI (F4 los anade)
- **Secret leak**: FALSO POSITIVO confirmado (F0, descartado)
- **F3d2 DatabasePG.initialize()**: se mantiene en main.py y poblar_mapeo_canales.py para inicializar el engine iptv-db (F5 lo limpia)

### Al retomar sesion

1. **Decidir push**: 12 commits esperando. Orden recomendado: iptv-db → iptv-api → scrapper. Dokploy auto-deploya cada push.
2. **F4**: Tests + CI. 1 sem. Pre-commit hooks + GitHub Actions + smoke tests.
3. **F5**: Limpieza final + docs. 1-2 sem. Eliminar asyncpg legacy, shims, services/ legacy, generar docs iptv-db.
4. **Risk note**: scrapper es el mas riesgoso de pushear (6 crons Ofelia). Considerar validar con dry-run de `sync_iptv` antes de push.

### Archivos clave para referencia

- `/home/alejandro/PycharmProjects/iptv-api/.slim/deepwork/iptv-refactor.md` — este archivo (estado persistente)
- `/home/alejandro/PycharmProjects/iptv-db/MIGRATION_NOTES.md` — drift ORM vs BD documentado para F1.5
- `/home/alejandro/PycharmProjects/iptv-db/alembic/` — config de migraciones (sin primera migracion generada)
- `/home/alejandro/PycharmProjects/iptv-api/AGENTS.md` — contexto del proyecto iptv-api
- `/home/alejandro/PycharmProjects/walactv-scrapper/AGENTS.md` — contexto del proyecto scrapper

---

## Estado de fases

- **F0**: ✅ Completada por fix-1 (commit 89e7db9 local, sin push)
  - 12 routers creados en `iptv-api/app/routers/` (health, auth, admin, content, channel_favorites, watch_progress, series, calendar, replays, streams, video_extractor, logo)
  - `scripts/api.py` reducido de 2310 → 209 LOC
  - 66 APIRoutes preservados (verificado con `len(app.routes) = 71` = 66 APIRoutes + StaticFiles)
  - Tests: 21/25 pass. **4 fallos pre-existentes** a verificar en gate Oracle.
  - Ruff: limpio
  - **Oracle gate**: ✅ APROBADO. Cero issues bloqueantes. Verificaciones 1-7 todas OK. 4 tests fallidos son deuda pre-existente (StubContentService desactualizado), no introducida por F0.
  - **Push**: pendiente decision del usuario. Dokploy desplegara automaticamente cuando se pushee.
- **F1**: ✅ Completada por fix-2 (commit c8f5291 local, sin push)
  - Repo nuevo: `/home/alejandro/PycharmProjects/iptv-db/`
  - 16 modelos ORM con herencia concrete (MetadataBase, CatalogBase, StreamBase)
  - 15 DTOs Pydantic 1:1 con tablas
  - Alembic async configurado (sin migracion inicial, F1.5)
  - MIGRATION_NOTES.md: 240 lineas, drift documentado
  - Tests: 4/4 pass, ruff+mypy limpios
  - 13 columnas legacy en BD no en ORM, UNIQUE constraints faltantes, 3 tablas del scrapper no modeladas → issues para F1.5
  - **Oracle gate**: ✅ APROBADO. 12/12 checks OK. 2 nits (orden de columnas, docstring SeriesCatalog). Decisión `SeriesCatalog` sin herencia aceptada.
  - **Push**: pendiente decision del usuario.
- **F2**: ✅ Completada por fix-3 (commit 3763924 local, sin push)
  - 8 shims en `app/models/*.py` (solo re-exports desde iptv_db)
  - `app/database.py` ahora usa `from iptv_db.models.base import Base`
  - `app/models/__init__.py` importa directo de iptv_db
  - `requirements.txt` agrega `-e /home/alejandro/PycharmProjects/iptv-db`
  - 16 modelos en iptv-api = mismos objetos Python que en iptv-db
  - Tests: 21 passed, 4 failed pre-existentes. App carga 66 APIRoutes.
  - **Oracle gate**: ✅ APROBADO. 11/11 checks OK. Cero cambios fuera de scope. 9 ruff issues pre-existentes (F6 los limpia).
  - **Push**: pendiente decision del usuario.
- **F3a**: ✅ Completada por fix-4 (commit 9f81325 local, sin push)
  - iptv-db en `docker/config/requirements.txt`
  - `scripts/database.py`: `DatabasePG` ahora crea engine iptv-db ADEMAS del pool asyncpg. API legacy preservada (initialize, get_pool, close, reset). NUEVO: get_session_factory()
  - `scripts/config.py`: nuevo `Settings.database_url` property
  - 4 otras clases en `database.py` NO tocadas (F3b-F3d)
  - asyncpg NO eliminado
  - Tests: 44/44 pass
  - **Oracle gate**: ✅ APROBADO. 10/10 checks OK. Enfoque "agregar engine sin migrar" correcto, sin riesgo de engine huerfano.
  - **Push**: pendiente.
- **F3b**: ✅ Completada por fix-5 (commit a71597d local, sin push)
  - 4 scripts migrados de psycopg2 a iptv-db sync engine
  - `backfill_imdb_ids.py` (248→179): 75% ORM
  - `populate_episode_imdb_ids.py` (283→213): 100% text() (JOIN no-ORM)
  - `import_imdb_ratings.py` (320→248): 50/50 mix
  - `scrape_tmdb_metadata.py` (1342→1345): ~95% text() (columnas no-ORM, UPSERT, JSONB)
  - psycopg2-binary eliminado de `docker/config/requirements-tmdb.txt`
  - Tests: 44/44 pass. Lint limpio.
  - **Oracle gate**: ✅ APROBADO. 10/10 checks OK. text() vs ORM aceptable. Commit por query apropiado para one-shots.
  - **F3c subdividido por Oracle** en F3c1 (infra+reads) y F3c2 (writes+bulk).
- **F3c1**: ✅ Completada por fix-6 (commit 7dff679 local, sin push)
  - 4 SELECTs de sync_iptv.py migrados a iptv-db
  - `obtener_config_desde_postgres` (ORM)
  - `contar_registros_tabla` (text())
  - `_cargar_tmdb_map_movies` (helper nuevo, ORM)
  - `_cargar_tmdb_map_series` (helper nuevo, ORM)
  - 2 funciones mixtas partidas en helpers read + write
  - asyncpg NO eliminado (todavia usado para writes)
  - Tests: 44/44 pass. Lint: 9 issues pre-existentes (0 nuevos).
  - **Oracle gate**: ✅ APROBADO. 10/10 checks OK. Coexistencia temporal correcta. Helpers read/write bien particionados.
  - **F3c2 subdividido por Oracle** en F3c2a (writes simples) y F3c2b (bulk + UPSERT).
- **F3c2a**: ✅ Completada por fix-1 (commit 49dbbaf local, sin push)
  - 7 queries simples migradas de asyncpg a iptv-db
  - 1x TRUNCATE: `limpiar_tabla_optimizada(tabla)` — text() con f-string
  - 3x DELETE: en `insert_channels_upsert`, `insert_movies_catalog`, `insert_series_catalog` (cleanup) — text() con `:named` params
  - 1x INSERT sync_metadata: `sync_to_postgres` — pg_insert con `on_conflict_do_update`
  - Pool params eliminados de 3 funciones (12 callers actualizados)
  - asyncpg: 18 → 11 calls (todos en 3 funciones bulk)
  - iptv-db: 8 → 18 session_factory uses
  - Tests: 44/44 pass. Lint: 9 issues pre-existentes (0 nuevos).
  - **Oracle gate**: ✅ APROBADO. 10/10 checks OK. Patron coexistencia correcto. result.rowcount aplicado correctamente. F3c2b como una sola fase.
- **F3c2b**: ✅ Completada por fix-1 (commit a5f0da5 local, sin push)
  - 3 funciones bulk migradas: insert_channels_upsert, insert_movies_catalog, insert_series_catalog
  - 0 asyncpg calls restantes en sync_iptv.py (era 11)
  - 18 iptv-db session uses
  - 0 pool params
  - INSERT sync_metadata: pg_insert + on_conflict_do_update
  - 11 columnas COALESCE en series_episodes via func.coalesce(excluded.col, SeriesEpisode.col)
  - 3 RETURNING id via .scalar()
  - Tests: 44/44 pass. Lint: 9 issues pre-existentes (0 nuevos).
  - **Oracle gate**: ✅ APROBADO_CON_CONDICIONES. 1 nit: docstrings en L956 y L1113 dicen "las escrituras siguen con asyncpg" — ya no es cierto. Se corrige en F3d1.
  - **F3d subdividido por Oracle** en 4 sub-fases:
    - F3d1: generate_content_json.py + cierre de sync_iptv.py (init_postgres, import asyncpg, caller de pool, docstrings nit)
    - F3d2: poblar_mapeo_canales.py + scrapper.py + main.py
    - F3d3: sync_replays.py (ThreadPoolExecutor, alta complejidad)
    - F3d4: database.py (al final, cuando todos los consumidores esten migrados)
- **F3d1**: ✅ Completada por fix-1 (commit 52fd53f local, sin push)
  - sync_iptv.py cerrado: 0 asyncpg refs (eliminado import, init_postgres, llamada, caller de pool)
  - 2 docstrings corregidos (F3c2b nit): dicen "escrituras migradas a iptv-db (F3c2b)"
  - generate_content_json.py: 4 funciones migradas (generar_channels/movies/series/todos)
  - patron: result.mappings().all() para preservar row["col"] de asyncpg.Record
  - DateTimeEncoder, str(UUID), .isoformat(), gzip — todo preservado
  - Tests: 44/44 pass. Lint: sync_iptv 9 pre-existentes (0 nuevos), generate_content_json 0 issues.
  - **Oracle gate**: ✅ APROBADO. 10/10 checks OK. Cierre completo de sync_iptv.py. generate_content_json equivalente semanticamente.
- **F3d2**: ✅ Completada por fix-1 (commit 6ba615a local, sin push)
  - 3 scripts migrados de asyncpg a iptv-db
  - `poblar_mapeo_canales.py` (266 LOC, 2 pool.acquire): channel_mappings + channel_variants con session_factory + text() + mappings()
  - `scrapper.py` (716 LOC, 1 pool.acquire): guarda_partidos_async con DDL en sesion dedicada + transacciones per-fecha
  - `main.py` (128 LOC): sin cambios — DatabasePG.initialize() mantenido
  - 0 asyncpg en los 3 archivos. 0 pool.acquire en los 3 archivos.
  - Tests: 44/44 pass. Lint: 7 issues pre-existentes (0 nuevos).
  - **Oracle gate**: ✅ APROBADO_CON_CONDICIONES. 12/12 checks tecnicos OK. Condicion: confirmar tests 44/44 (consistente en F3a-F3d1, OK). Cadena main -> scrapper -> poblar preservada. DatabasePG.initialize() mantenido donde se necesita.
- **F3d3**: ✅ Completada por fix-1 (commit 3b24507 local, sin push)
  - sync_replays.py migrado: 0 asyncpg, 0 pool.acquire
  - `_guardar_replays` migrado a pg_insert(Replay).on_conflict_do_update con 10 columnas SET
  - JSONB columns (video_sources, match_card) via SQLAlchemy (eliminado json.dumps manual)
  - 12 funciones scraping/parseo/validacion preservadas
  - 6 refs ThreadPoolExecutor/subprocess.run preservadas (NO TOCADAS)
  - Tests: 44/44 pass. Lint: 1 nuevo I001 (imports desordenados L24) + 5 pre-existentes.
  - **Oracle gate**: ✅ APROBADO_CON_CONDICIONES. Condicion: fix I001 (ruff check --fix) en primer commit de F3d4.
  - **F3d4 subdividido por Oracle** en F3d4a (database.py + callers) y F3d4b (config.py + bulk_insert.py + tests).
- **F3d4a**: ✅ Completada por fix-1 (commit e0fabbb local, sin push)
  - database.py: 0 pool.acquire (era 25), 30 iptv-db session uses
  - 5 clases migradas (DatabasePG ampliado, ConfigManager, ChannelMappingManager, CalendarioAcestreamManager, DataManagerSupabase simplificado), ReplayManager eliminado (dead code)
  - main.py y poblar_mapeo_canales.py: comments actualizados, initialize() mantenido
  - sync_replays.py: I001 fix (1 linea, F3d3 condition)
  - Tests: 44/44 pass. Lint: 2 nuevos (I001 + F401), 7 pre-existentes. Oracle gate APROBAR_CON_CONDICIONES.
- **F3d4b**: 🟡 En curso (preparando dispatch). Ultima fase de F3. Migrar scripts/config.py (228 LOC, Settings._pool_cache), scripts/services/bulk_insert.py (260 LOC), reescribir tests/test_database.py. Incluir 2 lint fixes de F3d4a (I001+F401) en primer commit.
  - F3a: Instalar iptv-db + refactorizar database.py y config.py del scrapper
  - F3b: Migrar scripts one-shot (psycopg2) - backfill, imdb, tmdb
  - F3c: Migrar sync_iptv.py (asyncpg, 1679 LOC, bulk ops)
  - F3d: Migrar sync_replays.py + scrapper.py + scripts menores

