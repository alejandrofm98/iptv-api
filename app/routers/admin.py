from fastapi import APIRouter, Depends, Query

from app.services.device_service import DeviceServiceV2
from app.services.playlist_service import PlaylistServiceV2
from app.services.stream_service import StreamProxyServiceV2
from app.services.user_service import UserServiceV2
from utils.dependencies import (
    get_device_service_v2,
    get_playlist_service_v2,
    get_stream_service_v2,
    get_user_service_v2,
    require_admin,
)
from utils.exceptions import BadRequestException, NotFoundException
from utils.models import SystemStats, UserCreate, UserUpdate

router = APIRouter()

# ============================================
# API: Usuarios (Admin)
# ============================================


@router.post("/api/admin/users", response_model=dict, tags=["Admin - Users"])
async def create_user(
    user_data: UserCreate,
    _: dict = Depends(require_admin),
    svc: UserServiceV2 = Depends(get_user_service_v2),
):
    """Crear nuevo usuario (Solo Admin)"""
    try:
        return svc.create_user_from_model(user_data)
    except ValueError as e:
        raise BadRequestException(str(e)) from e


@router.get("/api/admin/users", response_model=dict, tags=["Admin - Users"])
async def list_users(
    page: int = Query(1, ge=1, description="Número de página"),
    page_size: int = Query(100, ge=1, le=1000, description="Items por página"),
    _: dict = Depends(require_admin),
    svc: UserServiceV2 = Depends(get_user_service_v2),
):
    """Listar usuarios paginados (Solo Admin)"""
    items, total = svc.list_users(page, page_size)
    pages = (total + page_size - 1) // page_size

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "has_next": page < pages,
        "has_prev": page > 1,
    }


@router.get("/api/admin/users/{user_id}", response_model=dict, tags=["Admin - Users"])
async def get_user(
    user_id: str,
    _: dict = Depends(require_admin),
    svc: UserServiceV2 = Depends(get_user_service_v2),
):
    """Obtener usuario por ID (Solo Admin)"""
    user = svc.get_user(user_id)
    if not user:
        raise NotFoundException("Usuario", user_id)
    return user


@router.put("/api/admin/users/{user_id}", response_model=dict, tags=["Admin - Users"])
async def update_user(
    user_id: str,
    user_data: UserUpdate,
    _: dict = Depends(require_admin),
    svc: UserServiceV2 = Depends(get_user_service_v2),
):
    """Actualizar usuario (Solo Admin)"""
    user = svc.update_user(user_id, user_data)
    if not user:
        raise NotFoundException("Usuario", user_id)
    return user


@router.delete("/api/admin/users/{user_id}", tags=["Admin - Users"])
async def delete_user(
    user_id: str,
    _: dict = Depends(require_admin),
    svc: UserServiceV2 = Depends(get_user_service_v2),
):
    """Eliminar usuario (Solo Admin)"""
    success = svc.delete_user(user_id)
    if not success:
        raise NotFoundException("Usuario", user_id)
    return {"message": "Usuario eliminado"}


# ============================================
# API: Dispositivos (Admin)
# ============================================


@router.get("/api/admin/users/{user_id}/devices", response_model=list, tags=["Admin - Devices"])
async def get_user_devices(
    user_id: str,
    _: dict = Depends(require_admin),
    svc: DeviceServiceV2 = Depends(get_device_service_v2),
):
    """Obtiene dispositivos de un usuario"""
    return svc.get_user_devices(user_id)


@router.delete("/api/admin/users/{user_id}/devices/{device_id}", tags=["Admin - Devices"])
async def disconnect_device(
    user_id: str,
    device_id: str,
    _: dict = Depends(require_admin),
    svc: DeviceServiceV2 = Depends(get_device_service_v2),
):
    """Desconecta un dispositivo específico"""
    success = svc.disconnect_device(user_id, device_id)
    if not success:
        raise NotFoundException("Dispositivo", device_id)
    return {"message": "Dispositivo desconectado"}


@router.delete("/api/admin/users/{user_id}/devices", tags=["Admin - Devices"])
async def disconnect_all_devices(
    user_id: str,
    _: dict = Depends(require_admin),
    svc: DeviceServiceV2 = Depends(get_device_service_v2),
):
    """Desconecta todos los dispositivos de un usuario"""
    count = svc.disconnect_all_devices(user_id)
    return {"message": f"{count} dispositivos desconectados"}


@router.get("/api/admin/sessions", tags=["Admin - Devices"])
async def get_all_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    _: dict = Depends(require_admin),
    svc: DeviceServiceV2 = Depends(get_device_service_v2),
):
    """Obtiene todas las sesiones activas paginadas"""
    sessions = svc.get_all_sessions(page_size * page)
    total = len(sessions)
    start = (page - 1) * page_size
    end = start + page_size
    pages = (total + page_size - 1) // page_size

    return {
        "items": sessions[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


# ============================================
# API: Stats (Admin)
# ============================================


@router.get("/api/admin/stats", response_model=SystemStats, tags=["Admin - Stats"])
async def get_system_stats(
    _: dict = Depends(require_admin),
    user_svc: UserServiceV2 = Depends(get_user_service_v2),
    device_svc: DeviceServiceV2 = Depends(get_device_service_v2),
    playlist_svc: PlaylistServiceV2 = Depends(get_playlist_service_v2),
):
    """Obtiene estadísticas del sistema"""
    _, total_users = user_svc.list_users(page=1, page_size=1)
    sessions = device_svc.get_all_sessions(limit=10000)
    playlist_stats = playlist_svc.get_playlist_stats()

    return SystemStats(
        total_users=total_users,
        active_users=total_users,
        total_sessions=len(sessions),
        total_channels=playlist_stats["total_channels"],
        total_movies=playlist_stats["total_movies"],
        total_series=playlist_stats["total_series"],
    )


@router.get("/api/admin/resilience", tags=["Admin - Stats"])
async def get_resilience_status(
    _: dict = Depends(require_admin),
    stream_svc: StreamProxyServiceV2 = Depends(get_stream_service_v2),
):
    """Obtiene el estado de resiliencia de streams (circuit breaker, retry, buffer)"""
    return stream_svc.get_resilience_status()


# ============================================
# API: Admin Content
# ============================================


@router.post("/api/admin/content/reload", tags=["Admin - Content"])
async def reload_template(
    _: dict = Depends(require_admin),
    playlist_svc: PlaylistServiceV2 = Depends(get_playlist_service_v2),
):
    """Recarga los templates M3U en memoria"""
    playlist_svc.reload_template()
    templates = playlist_svc._templates
    if templates.get("full"):
        return {
            "status": "success",
            "message": "Templates recargados correctamente",
            "templates": {k: len(v) if v else 0 for k, v in templates.items()},
        }
    else:
        raise BadRequestException(
            "No se pudo recargar el template. Verifica que playlist_template.m3u exista."
        )
