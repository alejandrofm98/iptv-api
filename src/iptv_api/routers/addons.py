"""Fichas read-only desde addons Stremio (Cinemeta + sinopsis ES de TMDB)."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Path, Query

from iptv_api.core.dependencies import AuthResult as AuthDep
from iptv_api.core.dependencies import (
    get_cinemeta_service,
    get_tmdb_es_service,
    require_auth_with_jwt,
)
from iptv_api.core.exceptions import BadRequestException, ServiceUnavailableException
from iptv_api.schemas.addons import AddonMetaResponse
from iptv_api.services.cinemeta_service import CinemetaService
from iptv_api.services.tmdb_es_service import TmdbEsService
from iptv_api.services.torrentio_service import TorrentioService

logger = logging.getLogger("iptv-api.addons")

router = APIRouter(prefix="/api/addons", tags=["Addons"])


@router.get("/meta/{content_type}/{imdb_id}", response_model=AddonMetaResponse)
def get_addon_meta(
    content_type: Literal["movie", "series"] = Path(description="Tipo de contenido"),
    imdb_id: str = Path(description="Identificador IMDb (tt1234567)"),
    include_videos: bool = Query(False, description="Incluir el detalle de episodios de la serie"),
    include_sources: bool = Query(False, description="Consultar disponibilidad torrent"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    cinemeta_svc: CinemetaService = Depends(get_cinemeta_service),
    tmdb_es_svc: TmdbEsService = Depends(get_tmdb_es_service),
):
    """Devuelve ficha Cinemeta + sinopsis ES (TMDB lazy) + disponibilidad torrent.

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

    spanish: dict | None = None
    if meta.get("moviedb_id"):
        try:
            spanish = tmdb_es_svc.get_spanish(meta["moviedb_id"], content_type)
        except Exception as exc:
            logger.warning("TMDB ES degradado para %s: %s", imdb_id, exc)

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
