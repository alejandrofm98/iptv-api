"""Fichas de Stremio con sinopsis TMDB en español persistida por el scraper."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from iptv_api.core.dependencies import AuthResult as AuthDep
from iptv_api.core.dependencies import (
    get_cinemeta_service,
    get_db,
    require_auth_with_jwt,
)
from iptv_api.core.exceptions import BadRequestException, ServiceUnavailableException
from iptv_api.repositories.external_catalog_repo import ExternalCatalogRepository
from iptv_api.schemas.addons import AddonCatalogResponse, AddonMetaResponse
from iptv_api.services.cinemeta_service import CinemetaService
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
        repository = ExternalCatalogRepository(session)
        cached = (
            repository.search_page(content_type, catalog_id, search, skip, page_size)
            if search
            else repository.list_page(content_type, catalog_id, skip, page_size)
        )
        localized = repository.get_metadata_by_imdb_ids(
            content_type,
            [row.imdb_id for row in cached],
        )
        items = []
        for row in cached:
            detail = localized.get(row.imdb_id)
            items.append(
                {
                    "id": row.imdb_id,
                    "imdb_id": row.imdb_id,
                    "moviedb_id": row.moviedb_id,
                    "title": row.title_es or (detail.title_es if detail else None) or row.title,
                    "type": row.content_type,
                    "description": row.overview_es or (detail.overview_es if detail else None),
                    "poster": row.poster,
                    "backdrop": row.backdrop,
                    "rating": row.rating,
                    "year": row.year,
                }
            )
        has_next = bool(
            not search
            and len(cached) == page_size
            and repository.list_page(
                content_type,
                catalog_id,
                skip + len(cached),
                1,
            )
        )
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc
    except Exception as exc:
        raise ServiceUnavailableException("No se pudo leer el catálogo persistido") from exc
    return {
        "items": items,
        "content_type": content_type,
        "catalog_id": catalog_id,
        "skip": skip,
        "has_next": has_next,
    }


@router.get("/meta/{content_type}/{imdb_id}", response_model=AddonMetaResponse)
def get_addon_meta(
    content_type: Literal["movie", "series"] = Path(description="Tipo de contenido"),
    imdb_id: str = Path(description="Identificador IMDb (tt1234567)"),
    include_videos: bool = Query(False, description="Incluir el detalle de episodios de la serie"),
    include_sources: bool = Query(False, description="Consultar disponibilidad torrent"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    session: Session = Depends(get_db),
    cinemeta_svc: CinemetaService = Depends(get_cinemeta_service),
):
    """Devuelve ficha Cinemeta + sinopsis ES persistida + disponibilidad torrent.

    Para series la disponibilidad se estima con una muestra (S1E1), igual que
    hace el clasificador del scrapper. El detalle por episodio sigue en
    `/api/torrentio/series/{id}/episodes/{s}/{e}`.
    """
    del auth
    try:
        meta = cinemeta_svc.get_meta(content_type, imdb_id)
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc
    except Exception as exc:
        raise ServiceUnavailableException("Cinemeta no esta disponible") from exc

    spanish: dict[str, str | None] | None = None
    if hasattr(session, "execute"):
        try:
            repository = ExternalCatalogRepository(session)
            cached = repository.get_metadata_by_imdb_ids(content_type, [imdb_id]).get(imdb_id)
            if cached and (cached.title_es or cached.overview_es):
                spanish = {
                    "title_es": cached.title_es,
                    "overview_es": cached.overview_es,
                }
            repository.save_meta(content_type, imdb_id, meta, spanish)
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.warning("No se pudo persistir la ficha Cinemeta %s: %s", imdb_id, exc)

    # `Query(False)` is a FastAPI object when this function is called directly
    # by unit tests; checking identity also keeps that direct-call default safe.
    if include_sources is True:
        has_torrent, languages, torrent_status = _torrent_availability(
            content_type, imdb_id, meta.get("total_episodes", 0)
        )
    else:
        has_torrent, languages, torrent_status = False, [], "not_requested"
    episodes = meta.get("episodes", []) if include_videos else []
    return {
        "imdb_id": meta.get("imdb_id") or imdb_id,
        "content_type": content_type,
        "name": meta.get("name"),
        "year": meta.get("year"),
        "description_en": meta.get("description_en"),
        "overview_es": (spanish or {}).get("overview_es"),
        "title_es": (spanish or {}).get("title_es"),
        "overview_source": "tmdb" if (spanish or {}).get("overview_es") else "none",
        "poster": meta.get("poster"),
        "background": meta.get("background"),
        "logo": meta.get("logo"),
        "genres": meta.get("genres") or [],
        "cast": meta.get("cast") or [],
        "imdb_rating": meta.get("imdb_rating"),
        "moviedb_id": meta.get("moviedb_id"),
        "has_torrent_source": has_torrent,
        "torrent_languages": languages,
        "torrent_status": torrent_status,
        "total_episodes": meta.get("total_episodes", 0),
        "seasons": meta.get("seasons") or [],
        "episodes": episodes,
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
