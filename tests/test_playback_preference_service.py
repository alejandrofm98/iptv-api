"""Tests de preferencias de reproduccion sincronizadas."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from iptv_api.core.exceptions import BadRequestException
from iptv_api.services.playback_preference_service import PlaybackPreferenceService
from iptv_api.services.watch_progress_service import WatchProgressServiceV2


def test_resolves_series_episode_to_catalog_uuid(db_session: MagicMock) -> None:
    service = PlaybackPreferenceService(db_session)
    catalog_id = uuid4()
    service.series_repo.get_with_metadata = MagicMock(return_value=None)
    service.series_repo.get_catalog_by_episode_provider_id = MagicMock(
        return_value={"id": catalog_id}
    )

    result = service.resolve_catalog_id("series", "episode-provider-id")

    assert result == catalog_id


def test_rejects_unsupported_content_type(db_session: MagicMock) -> None:
    service = PlaybackPreferenceService(db_session)

    with pytest.raises(BadRequestException):
        service.resolve_catalog_id("episode", "123")


def test_upsert_uses_canonical_catalog_id(db_session: MagicMock) -> None:
    service = PlaybackPreferenceService(db_session)
    catalog_id = uuid4()
    preference_id = uuid4()
    user_id = uuid4()
    service.content_repo.get_movie_with_metadata = MagicMock(return_value={"id": catalog_id})
    service.repo.upsert = MagicMock(
        return_value=SimpleNamespace(
            id=preference_id,
            user_id=user_id,
            content_type="movie",
            catalog_id=catalog_id,
            audio_language="EN",
            audio_label="English",
            subtitle_language=None,
            subtitle_label=None,
            subtitles_disabled=True,
            created_at=None,
            updated_at=None,
        )
    )

    result = service.upsert(
        str(user_id),
        "movie",
        "provider-id",
        {"audio_language": "EN", "subtitles_disabled": True},
    )

    service.repo.upsert.assert_called_once_with(
        str(user_id),
        "movie",
        catalog_id,
        {"audio_language": "EN", "subtitles_disabled": True},
    )
    assert result["catalog_id"] == str(catalog_id)


def test_series_preference_is_deleted_only_when_every_episode_is_watched(
    db_session: MagicMock,
) -> None:
    service = WatchProgressServiceV2(db_session)
    catalog_id = uuid4()
    service.series_repo._get_episode_counts = MagicMock(return_value={"total_episodes": 2})
    service.wp_repo.get_all_for_user_and_series = MagicMock(
        return_value=[
            SimpleNamespace(season_number=1, episode_number=1, is_watched=True),
            SimpleNamespace(season_number=1, episode_number=2, is_watched=False),
        ]
    )
    service.playback_preference_repo.delete_for_content = MagicMock()

    service._delete_completed_playback_preference("user", "series", str(catalog_id))

    service.playback_preference_repo.delete_for_content.assert_not_called()

    service.wp_repo.get_all_for_user_and_series.return_value[1].is_watched = True
    service._delete_completed_playback_preference("user", "series", str(catalog_id))

    service.playback_preference_repo.delete_for_content.assert_called_once_with(
        "user", "series", catalog_id
    )
