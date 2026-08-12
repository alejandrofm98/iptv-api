"""Endpoints de busqueda Torrentio para clientes autorizados."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from iptv_api.core.dependencies import AuthResult as AuthDep
from iptv_api.core.dependencies import get_db, require_auth_with_jwt
from iptv_api.core.exceptions import BadRequestException, ServiceUnavailableException
from iptv_api.repositories.content_repo import ContentRepository
from iptv_api.repositories.series_repo import SeriesRepository
from iptv_api.services.torrentio_service import TorrentioService

router = APIRouter(prefix="/api/torrentio", tags=["Torrentio"])


@router.get("/movies/{movie_id}")
def get_movie_streams(
    movie_id: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    db: Session = Depends(get_db),
):
    """Busca torrents de una pelicula sin guardarlos en PostgreSQL."""
    del auth
    row = ContentRepository(db).get_movie_with_metadata(movie_id)
    imdb_id = row.get("imdb_id") if row else None
    if not imdb_id:
        raise BadRequestException("La pelicula no tiene imdb_id para consultar Torrentio")
    return _query_movie(imdb_id)


@router.get("/series/{series_id}/episodes/{season}/{episode}")
def get_episode_streams(
    series_id: str,
    season: int,
    episode: int,
    auth: AuthDep = Depends(require_auth_with_jwt),
    db: Session = Depends(get_db),
):
    """Busca torrents de un episodio por el identificador de la serie."""
    del auth
    row = SeriesRepository(db).get_with_metadata(series_id)
    imdb_id = row.get("imdb_id") if row else None
    if not imdb_id:
        raise BadRequestException("La serie no tiene imdb_id para consultar Torrentio")
    try:
        items = TorrentioService().get_episode_streams(imdb_id, season, episode)
    except (ValueError, OSError) as exc:
        raise BadRequestException(str(exc)) from exc
    except Exception as exc:
        raise ServiceUnavailableException("Torrentio no esta disponible") from exc
    return {"items": items, "total": len(items), "source": "torrentio"}


def _query_movie(imdb_id: str) -> dict:
    try:
        items = TorrentioService().get_movie_streams(imdb_id)
    except (ValueError, OSError) as exc:
        raise BadRequestException(str(exc)) from exc
    except Exception as exc:
        raise ServiceUnavailableException("Torrentio no esta disponible") from exc
    return {"items": items, "total": len(items), "source": "torrentio"}
