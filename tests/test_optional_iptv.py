import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from iptv_api.core.exceptions import ForbiddenException
from iptv_api.core.models import AuthResult
from iptv_api.routers.auth import validate_stream
from iptv_api.routers.content import get_channels_full, get_content, get_home
from iptv_api.routers.streams import _proxy_stream_handler
from iptv_api.services.user_service import UserServiceV2


def no_iptv_auth() -> AuthResult:
    return AuthResult(
        valid=True,
        user_id="user-no-iptv",
        username="catalog-only",
        message="OK",
        can_connect=True,
        max_devices=1,
        iptv_enabled=False,
    )


def test_home_is_provider_empty_for_account_without_iptv() -> None:
    content_service = Mock()
    favorites_service = Mock()

    payload = asyncio.run(
        get_home(
            page_size=24,
            country=None,
            password=None,
            auth=no_iptv_auth(),
            content_svc=content_service,
            favorites_svc=favorites_service,
        )
    )

    assert payload["movie_sections"] == []
    assert payload["series_sections"] == []
    assert payload["favorites"] == []
    content_service.get_home_catalog_new.assert_not_called()
    favorites_service.get_favorite_channels.assert_not_called()


def test_content_and_full_channels_are_empty_without_iptv() -> None:
    content_service = Mock()
    favorites_service = Mock()

    page = asyncio.run(
        get_content(
            content_type="movies",
            page=1,
            page_size=24,
            group=None,
            country=None,
            search=None,
            year=None,
            genre=None,
            password=None,
            section_title=None,
            auth=no_iptv_auth(),
            content_svc=content_service,
            favorites_svc=favorites_service,
        )
    )
    channels = asyncio.run(get_channels_full(auth=no_iptv_auth(), content_svc=content_service))

    assert page["items"] == []
    assert page["total"] == 0
    assert channels == {"items": [], "total": 0}
    content_service.get_android_content_list.assert_not_called()


def test_user_service_exposes_iptv_capability() -> None:
    service = UserServiceV2.__new__(UserServiceV2)
    service.user_repo = Mock()
    service.session_repo = Mock()
    service.user_repo.get_by_id.return_value = SimpleNamespace(
        id="user-no-iptv",
        username="catalog-only",
        max_connections=2,
        is_active=True,
        iptv_enabled=False,
        role="user",
        expires_at=None,
        created_at=None,
    )
    service.session_repo.count_by_user.return_value = 0

    result = service.get_user("user-no-iptv")

    assert result is not None
    assert result["iptv_enabled"] is False


def test_direct_iptv_validation_rejects_account_without_provider() -> None:
    user_service = Mock()
    user_service.validate_credentials.return_value = no_iptv_auth()
    device_service = Mock()
    stream_service = Mock()

    with pytest.raises(ForbiddenException):
        asyncio.run(
            validate_stream(
                content_type="movie",
                username="catalog-only",
                password="password",
                provider_id="123",
                request=Mock(),
                user_svc=user_service,
                device_svc=device_service,
                stream_svc=stream_service,
            )
        )

    device_service.register_or_update_session.assert_not_called()
    stream_service.get_original_url.assert_not_called()


def test_stream_proxy_rejects_account_without_provider() -> None:
    user_service = Mock()
    user_service.validate_credentials.return_value = no_iptv_auth()
    device_service = Mock()
    stream_service = Mock()

    with pytest.raises(ForbiddenException):
        asyncio.run(
            _proxy_stream_handler(
                content_type="live",
                username="catalog-only",
                password="password",
                stream_id="123",
                request=Mock(),
                user_svc=user_service,
                device_svc=device_service,
                stream_svc=stream_service,
                transcode_svc=Mock(),
            )
        )

    device_service.register_or_update_session.assert_not_called()
    stream_service.get_original_url.assert_not_called()
