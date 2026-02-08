# AGENTS.md - IPTV API Development Guide

This file provides guidelines for AI coding agents working on this Python FastAPI IPTV API project.

## Build & Run Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the API locally
python -m uvicorn scripts.api:app --reload --host 0.0.0.0 --port 3000

# Run with Docker
cd docker && docker-compose up -d

# Check health
curl http://localhost/health
```

## Lint & Format (Recommended)

```bash
# Setup (one time)
pip install ruff mypy black isort

# Format code
black scripts/ services/ utils/
isort scripts/ services/ utils/

# Lint
ruff check scripts/ services/ utils/
mypy scripts/ services/ utils/

# Run single test (when tests exist)
pytest tests/test_specific.py::test_function -v
```

## Code Style Guidelines

### Naming Conventions
- **Classes**: `PascalCase` (e.g., `UserService`, `PlaylistService`)
- **Functions/Variables**: `snake_case` (e.g., `create_user`, `max_connections`)
- **Constants**: `UPPER_CASE` (e.g., `JWT_SECRET`, `DEFAULT_TIMEOUT`)
- **Private methods**: `_leading_underscore` (e.g., `_hash_password`)
- **Modules**: `snake_case` with underscores (e.g., `user_service.py`)

### Import Style
```python
# Standard library first
import os
import re
from datetime import datetime
from typing import Optional, Dict, Any

# Third-party packages
from fastapi import FastAPI, HTTPException
from supabase import Client
import bcrypt

# Local imports - absolute from project root
from utils.config import get_settings
from utils.models import UserCreate, UserResponse
from utils.constants import JWT_SECRET
from utils.exceptions import NotFoundException, BadRequestException
from utils.dependencies import get_user_service, require_admin

# Within-package relative imports (for services/)
from .user_service import UserService
```

### Type Hints
- Always use type hints for function parameters and return types
- Use `Optional[Type]` for nullable values
- Use `Dict[str, Any]` and `List[Type]` for collections
- Import types from `typing` module

### Docstrings
- Use triple quotes `"""` for module, class, and function docstrings
- Keep docstrings in Spanish (existing convention)
- First line: brief description
- Use Args/Returns sections for complex functions

### Error Handling
```python
# Use custom exceptions from utils.exceptions
from utils.exceptions import NotFoundException, BadRequestException, UnauthorizedException

# Instead of generic HTTPException
raise NotFoundException("Usuario", user_id)
raise BadRequestException("Datos inválidos", {"field": "username"})
raise UnauthorizedException("Token expirado")

# Old way (avoid)
raise HTTPException(status_code=404, detail="Usuario no encontrado")
```

### FastAPI Patterns
```python
# Dependency injection for services (reusable dependencies)
from utils.dependencies import get_user_service, require_admin, require_auth_with_credentials

# Route with dependencies
@app.get("/api/admin/users/{user_id}")
async def get_user(
    user_id: str,
    _: dict = Depends(require_admin),
    svc: UserService = Depends(get_user_service)
):
    return svc.get_user(user_id)

# Public endpoint with credentials
@app.get("/api/content")
async def get_content(
    auth: AuthDep = Depends(require_auth_with_credentials),
    content_svc: ContentService = Depends(get_content_service)
):
    return content_svc.get_content_list(...)

# Pydantic models for request/response
class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    max_connections: int = Field(default=2, ge=1, le=10)
```

### Pagination Pattern
```python
# Use page/page_size instead of skip/limit
@app.get("/api/content")
async def get_content(
    page: int = Query(1, ge=1, description="Número de página"),
    page_size: int = Query(50, ge=1, le=100, description="Items por página"),
):
    # Response includes pagination metadata
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "has_next": has_next,
        "has_prev": has_prev
    }
```

## Project Structure

```
scripts/        # Main applications (api.py, sync_iptv.py)
services/       # Business logic (UserService, DeviceService, etc.)
utils/          # Shared utilities (config, models, constants, exceptions, dependencies)
database/       # SQL schema
nginx/          # Reverse proxy config
docker/         # Docker configuration
postman/        # API collections
```

## Database & Supabase
- Use Supabase client for all database operations
- Use `utils.constants` for table names
- Handle timezone-aware datetimes carefully
- Use `.execute()` pattern for queries

## Key Patterns to Follow
1. Services are stateless classes receiving Supabase client in `__init__`
2. Configuration goes in `utils.config.Settings`
3. Constants go in `utils.constants`
4. Pydantic models go in `utils.models`
5. **Custom exceptions go in `utils.exceptions`**
6. **Reusable dependencies go in `utils.dependencies`**
7. Use dependency injection in FastAPI routes
8. Always validate user permissions in admin endpoints
9. Handle JWT authentication using `get_current_user` dependency
10. **Use `page/page_size` for pagination (not skip/limit)**
11. **Return consistent error responses using custom exceptions**

## API Structure

### Endpoint Organization
```
/api/auth/*              # Authentication (login)
/api/admin/users/*       # User management (admin only)
/api/admin/devices/*     # Device management (admin only)
/api/admin/stats         # System stats (admin only)
/api/admin/content/*     # Content management (admin only)
/api/content/*           # Public content (requires auth)
/playlist/*              # Playlist M3U (public with credentials)
/stream/*                # Stream proxy (public with credentials)
/logo                    # Logo proxy (public)
/auth/validate-stream/*  # Nginx validation (internal)
```

### Content Endpoints (Unified)
```
GET /api/content/groups?content_type=channels|movies|series&countries=US,MX
GET /api/content/countries?content_type=channels|movies|series
GET /api/content?content_type=channels|movies|series&page=1&page_size=50
GET /api/content/{type}/{item_id}
```

### Stream Endpoints (Unified)
```
GET /stream/{live|movie|series}/{username}/{password}/{stream_id}
```

## Security Reminders
- Never commit `.env` files
- Use `getattr()` with defaults for optional model fields
- Validate all user inputs with Pydantic
- Check user roles before admin operations
- Hash passwords with bcrypt (never store plain text)
- Use `require_auth_with_credentials` for public endpoints
- Use `require_auth_with_session` for stream endpoints (tracks devices)

## New Files (v2.1)

### utils/exceptions.py
Custom exceptions for consistent error handling:
- `NotFoundException` - Recurso no encontrado (404)
- `BadRequestException` - Petición inválida (400)
- `UnauthorizedException` - No autorizado (401)
- `ForbiddenException` - Acceso prohibido (403)
- `ConflictException` - Conflicto/duplicado (409)
- `TooManyRequestsException` - Rate limiting (429)

### utils/dependencies.py
Reusable FastAPI dependencies:
- `get_user_service()` - Obtener UserService
- `get_content_service()` - Obtener ContentService
- `get_current_user()` - Validar JWT token
- `require_admin()` - Requerir rol admin
- `require_auth_with_credentials()` - Validar user/pass
- `require_auth_with_session()` - Validar + registrar sesión

## Migration Notes (v2.0 → v2.1)

### Removed Endpoints (Legacy)
These endpoints were removed. Use the new unified endpoints:
- `/api/channels` → `GET /api/content?content_type=channels`
- `/api/movies` → `GET /api/content?content_type=movies`
- `/api/series` → `GET /api/content?content_type=series`
- `/live/{user}/{pass}/{id}` → `GET /stream/live/{user}/{pass}/{id}`
- `/movie/{user}/{pass}/{id}` → `GET /stream/movie/{user}/{pass}/{id}`
- `/series/{user}/{pass}/{id}` → `GET /stream/series/{user}/{pass}/{id}`

### Pagination Changes
- Old: `skip=0&limit=50`
- New: `page=1&page_size=50`
- Response now includes: `total`, `pages`, `has_next`, `has_prev`

### Error Response Format
All errors now return consistent JSON:
```json
{
  "error": "NotFound",
  "message": "Usuario no encontrado",
  "details": {"id": "123"},
  "status_code": 404
}
```
