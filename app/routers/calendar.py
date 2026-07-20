from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.services.calendar_service import CalendarServiceV2
from utils.config import get_settings
from utils.dependencies import AuthResult as AuthDep
from utils.dependencies import (
    get_calendar_service_v2,
    require_auth_with_jwt,
)
from utils.exceptions import BadRequestException, NotFoundException
from utils.models import CalendarDayResponse, CalendarEvent

router = APIRouter()

settings = get_settings()


@router.get("/api/calendar/{fecha}", response_model=CalendarDayResponse, tags=["Calendar"])
async def get_calendar_by_date(
    fecha: str,
    password: str | None = Query(None, description="Password para construir stream_url"),
    client: str | None = Query(None, description="'android' para URLs con /live/"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    calendar_svc: CalendarServiceV2 = Depends(get_calendar_service_v2),
):
    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        raise BadRequestException("Formato de fecha inválido. Use YYYY-MM-DD") from None

    eventos_raw = calendar_svc.get_events_by_date(fecha)

    if not eventos_raw:
        return CalendarDayResponse(fecha=fecha, total_eventos=0, eventos=[])

    username = auth.username or ""
    pwd = password or ""
    base_url = settings.public_domain.rstrip("/")

    all_channel_ids = []
    for evento in eventos_raw:
        for ch in evento.get("canales_resueltos", []) or []:
            cid = ch.get("channel_id")
            if cid:
                all_channel_ids.append(cid)

    provider_map = calendar_svc.get_provider_ids(all_channel_ids) if all_channel_ids else {}

    eventos = []
    for evento in eventos_raw:
        canales_resueltos = evento.get("canales_resueltos", []) or []
        if username and pwd:
            for ch in canales_resueltos:
                stream_id = ch.get("provider_id") or provider_map.get(ch.get("channel_id"))
                if stream_id:
                    ch["provider_id"] = stream_id
                    if client == "android":
                        ch["stream_url"] = f"{base_url}/live/{username}/{pwd}/{stream_id}"
                    elif not ch.get("stream_url"):
                        ch["stream_url"] = f"{base_url}/{username}/{pwd}/{stream_id}"
        eventos.append(
            CalendarEvent(
                id=str(evento["id"]),
                fecha=evento.get("fecha"),
                hora=evento.get("hora"),
                competicion=evento.get("competicion"),
                subtitulo_competicion=evento.get("subtitulo_competicion"),
                categoria=evento.get("categoria"),
                equipos=evento.get("equipos"),
                imagen_evento=evento.get("imagen_evento"),
                canales_original=evento.get("canales_original", []) or [],
                canales_resueltos=canales_resueltos,
            )
        )

    return CalendarDayResponse(fecha=fecha, total_eventos=len(eventos), eventos=eventos)


@router.get("/api/calendar/event/{event_id}", response_model=CalendarEvent, tags=["Calendar"])
async def get_calendar_event(
    event_id: str,
    password: str | None = Query(None, description="Password para construir stream_url"),
    client: str | None = Query(None, description="'android' para URLs con /live/"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    calendar_svc: CalendarServiceV2 = Depends(get_calendar_service_v2),
):
    evento = calendar_svc.get_event_by_id(event_id)

    if not evento:
        raise NotFoundException("Evento", event_id)

    canales_resueltos = evento.get("canales_resueltos", []) or []

    username = auth.username or ""
    pwd = password or ""
    base_url = settings.public_domain.rstrip("/")

    if username and pwd:
        all_channel_ids = [ch.get("channel_id") for ch in canales_resueltos if ch.get("channel_id")]
        provider_map = calendar_svc.get_provider_ids(all_channel_ids) if all_channel_ids else {}
        for ch in canales_resueltos:
            stream_id = ch.get("provider_id") or provider_map.get(ch.get("channel_id"))
            if stream_id:
                ch["provider_id"] = stream_id
                if client == "android":
                    ch["stream_url"] = f"{base_url}/live/{username}/{pwd}/{stream_id}"
                elif not ch.get("stream_url"):
                    ch["stream_url"] = f"{base_url}/{username}/{pwd}/{stream_id}"

    return CalendarEvent(
        id=str(evento["id"]),
        fecha=evento.get("fecha"),
        hora=evento.get("hora"),
        competicion=evento.get("competicion"),
        subtitulo_competicion=evento.get("subtitulo_competicion"),
        categoria=evento.get("categoria"),
        equipos=evento.get("equipos"),
        imagen_evento=evento.get("imagen_evento"),
        canales_original=evento.get("canales_original", []) or [],
        canales_resueltos=canales_resueltos,
    )
