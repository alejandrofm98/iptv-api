"""
Excepciones y manejadores de error consistentes para la API
"""

from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    field: str | None = None
    message: str
    value: Any | None = None


class ErrorResponse(BaseModel):
    error: str
    message: str
    details: dict[str, Any] | None = None
    status_code: int


class APIException(HTTPException):
    """Excepción base para errores de API"""

    def __init__(
        self,
        status_code: int,
        error: str,
        message: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            status_code=status_code,
            detail=ErrorResponse(
                error=error, message=message, details=details, status_code=status_code
            ).model_dump(),
        )


class NotFoundException(APIException):
    """Recurso no encontrado"""

    def __init__(self, resource: str, id: str | None = None):
        details = {"id": id} if id else None
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error="NotFound",
            message=f"{resource} no encontrado",
            details=details,
        )


class BadRequestException(APIException):
    """Petición inválida"""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error="BadRequest",
            message=message,
            details=details,
        )


class UnauthorizedException(APIException):
    """No autorizado"""

    def __init__(self, message: str = "Credenciales inválidas"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error="Unauthorized",
            message=message,
            details=None,
        )


class ForbiddenException(APIException):
    """Acceso prohibido"""

    def __init__(self, message: str = "No tienes permiso para realizar esta acción"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error="Forbidden",
            message=message,
            details=None,
        )


class ConflictException(APIException):
    """Conflicto (recurso ya existe)"""

    def __init__(self, resource: str, field: str, value: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            error="Conflict",
            message=f"{resource} con {field}='{value}' ya existe",
            details={"field": field, "value": value},
        )


class ValidationException(APIException):
    """Error de validación"""

    def __init__(self, errors: list[ErrorDetail]):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error="ValidationError",
            message="Error de validación",
            details={"errors": [e.model_dump() for e in errors]},
        )


class TooManyRequestsException(APIException):
    """Demasiadas peticiones (rate limiting)"""

    def __init__(self, message: str = "Demasiadas peticiones. Intenta más tarde."):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            error="TooManyRequests",
            message=message,
            details=None,
        )


class ServiceUnavailableException(APIException):
    """Servicio no disponible"""

    def __init__(self, message: str = "Servicio temporalmente no disponible"):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error="ServiceUnavailable",
            message=message,
            details=None,
        )
