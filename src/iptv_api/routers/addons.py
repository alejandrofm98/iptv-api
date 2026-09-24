"""Fichas de Stremio con sinopsis TMDB en español persistida por el scraper."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from iptv_api.core.dependencies import AuthResult as AuthDep
from iptv_api.core.dependencies import (
    get_db,
    require_auth_with_jwt,
)
from iptv_api.core.exceptions import (
    BadRequestException,
    NotFoundException,
    ServiceUnavailableException,
)
from iptv_api.schemas.addons import AddonCatalogResponse, AddonMetaResponse
from iptv_api.services.addon_catalog_service import AddonCatalogService
from iptv_api.services.torrentio_service import TorrentioService

logger = logging.getLogger("iptv-api.addons")

router = APIRouter(prefix="/api/addons", tags=["Addons"])


@router.get("/catalog/{content_type}/{catalog_id}", response_model=AddonCatalogResponse)
def get_addon_catalog(
    content_type: Literal["movie", "series"] = Path(description="Tipo de contenido"),
    catalog_id: str = Path(description="Identificador de catálogo Cinemeta"),
    skip: int = Query(0, ge=0, description="Offset de paginación"),
    page_size: int = Query(50, ge=1, le=100, description="Tamaño de página"),
    search: str | None = Query(None, max_length=120, description="Texto de búsqueda Cinemeta"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    session: Session = Depends(get_db),
):
    """Devuelve exclusivamente el catálogo externo persistido por el scraper."""
    del auth
    try:
        return AddonCatalogService(session).catalog(
            content_type, catalog_id, skip, page_size, search
        )
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc
    except Exception as exc:
        raise ServiceUnavailableException("No se pudo leer el catálogo persistido") from exc


@router.get("/meta/{content_type}/{imdb_id}", response_model=AddonMetaResponse)
def get_addon_meta(
    content_type: Literal["movie", "series"] = Path(description="Tipo de contenido"),
    imdb_id: str = Path(description="Identificador IMDb (tt1234567)"),
    include_videos: bool = Query(False, description="Incluir el detalle de episodios de la serie"),
    include_sources: bool = Query(False, description="Consultar disponibilidad torrent"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    session: Session = Depends(get_db),
):
    """Devuelve ficha y episodios persistidos + disponibilidad torrent.

    Para series la disponibilidad se estima con una muestra (S1E1), igual que
    hace el clasificador del scrapper. El detalle por episodio sigue en
    `/api/torrentio/series/{id}/episodes/{s}/{e}`.
    """
    del auth
    try:
        if not imdb_id.startswith("tt") or not imdb_id[2:].isdigit():
            raise BadRequestException("imdb_id debe tener formato tt1234567")
        meta = AddonCatalogService(session).meta(content_type, imdb_id, include_videos is True)
        if meta is None:
            raise NotFoundException("Ficha", imdb_id)
    except (BadRequestException, NotFoundException):
        raise
    except Exception as exc:
        raise ServiceUnavailableException("No se pudo leer la ficha persistida") from exc

    # `Query(False)` is a FastAPI object when this function is called directly
    # by unit tests; checking identity also keeps that direct-call default safe.
    if include_sources is True:
        has_torrent, languages, torrent_status = _torrent_availability(
            content_type, imdb_id, meta.get("total_episodes", 0)
        )
    else:
        has_torrent, languages, torrent_status = False, [], "not_requested"
    return {
        **meta,
        "has_torrent_source": has_torrent,
        "torrent_languages": languages,
        "torrent_status": torrent_status,
    }


def _torrent_availability(
    content_type: str, imdb_id: str, total_episodes: int
) -> tuple[bool, list[str], str]:
    """Consulta Torrentio en modo best-effort sin romper la ficha."""
    try:
        if content_type == "movie":
            items = TorrentioService().get_movie_streams(imdb_id)
        else:
            # Muestra S1E1 como clasificacion del titulo completo.
            items = TorrentioService().get_episode_streams(imdb_id, 1, 1)
    except (ValueError, OSError) as exc:
        raise BadRequestException(str(exc)) from exc
    except Exception as exc:
        logger.warning("Torrentio degradado para %s: %s", imdb_id, exc)
        return False, [], "unavailable"
    languages = sorted({item.get("language") for item in items if item.get("language")})
    _ = total_episodes
    return bool(items), languages, "ok"
