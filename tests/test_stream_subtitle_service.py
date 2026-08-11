"""Tests del préstamo de subtítulos entre enlaces hermanos (VOD)."""

from unittest.mock import AsyncMock, MagicMock, patch

from iptv_api.services.stream_service import StreamProxyServiceV2

SUB_MEDIA_LINE = (
    '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="Español",'
    'DEFAULT=NO,AUTOSELECT=YES,FORCED=NO,LANGUAGE="es",URI="subs/es.vtt"'
)


def _make_service() -> StreamProxyServiceV2:
    return StreamProxyServiceV2(
        config_repo=MagicMock(),
        channel_repo=MagicMock(),
        content_repo=MagicMock(),
        series_repo=MagicMock(),
    )


def test_split_media_attrs_respects_quotes() -> None:
    parts = StreamProxyServiceV2._split_media_attrs(
        'TYPE=SUBTITLES,NAME="Esp, Añol",URI="subs/es.vtt"'
    )
    assert parts == ["TYPE=SUBTITLES", 'NAME="Esp, Añol"', 'URI="subs/es.vtt"']


def test_parse_subtitle_renditions_extracts_tracks() -> None:
    playlist = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        f"{SUB_MEDIA_LINE}\n"
        '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",'
        'DEFAULT=YES,AUTOSELECT=YES,LANGUAGE="en",URI="https://cdn.provider.com/en.vtt"\n'
        "#EXT-X-STREAM-INF:BANDWIDTH=2000000\n"
        "https://cdn.provider.com/master.m3u8\n"
    )
    tracks = StreamProxyServiceV2._parse_subtitle_renditions(playlist, "https://host/a.m3u8")
    assert len(tracks) == 2
    assert tracks[0]["lang"] == "es"
    assert tracks[0]["name"] == "Español"
    assert tracks[0]["uri"] == "https://host/subs/es.vtt"
    assert tracks[0]["forced"] is False
    assert tracks[1]["uri"] == "https://cdn.provider.com/en.vtt"


def test_parse_subtitle_renditions_skips_non_subtitle_media() -> None:
    playlist = (
        '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="ES",LANGUAGE="es",URI="es.m3u8"\n'
    )
    assert StreamProxyServiceV2._parse_subtitle_renditions(playlist, "https://host/a.m3u8") == []


def test_wire_subtitle_group_adds_reference_to_audio_and_variants() -> None:
    playlist = (
        "#EXTM3U\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="ES",LANGUAGE="es",URI="es.m3u8"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=2000000,CODECS="avc1"\n'
        "https://cdn.provider.com/master.m3u8\n"
    )
    out = StreamProxyServiceV2._wire_subtitle_group(playlist, "walactv-borrowed")
    assert (
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="ES",LANGUAGE="es",URI="es.m3u8",SUBTITLES="walactv-borrowed"'
        in out
    )
    assert '#EXT-X-STREAM-INF:BANDWIDTH=2000000,CODECS="avc1",SUBTITLES="walactv-borrowed"' in out


def test_build_subtitle_proxy_uri_encodes_credentials() -> None:
    uri = StreamProxyServiceV2._build_subtitle_proxy_uri(
        "https://iptv.walerike.com", "movie", "us/er", "p@ss", "abc", 0
    )
    assert uri == "https://iptv.walerike.com/api/subtitle/movie/us%2Fer/p%40ss/abc/0"


async def test_inject_borrowed_subtitles_returns_unchanged_when_manifest_has_subs() -> None:
    service = _make_service()
    service.get_borrowed_subtitle_tracks = AsyncMock(
        return_value=[{"name": "ES", "lang": "es", "uri": "x", "forced": False}]
    )
    playlist = f"#EXTM3U\n{SUB_MEDIA_LINE}\n"
    out = await service.inject_borrowed_subtitles(
        playlist, "https://iptv.walerike.com", "movie", "user", "pass", "abc"
    )
    assert out == playlist
    service.get_borrowed_subtitle_tracks.assert_not_awaited()


async def test_inject_borrowed_subtitles_returns_unchanged_without_borrowed_tracks() -> None:
    service = _make_service()
    service.get_borrowed_subtitle_tracks = AsyncMock(return_value=[])
    playlist = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\nhttps://cdn/v.m3u8\n"
    out = await service.inject_borrowed_subtitles(
        playlist, "https://iptv.walerike.com", "movie", "user", "pass", "abc"
    )
    assert out == playlist


async def test_inject_borrowed_subtitles_adds_media_lines_and_wires_group() -> None:
    service = _make_service()
    service.get_borrowed_subtitle_tracks = AsyncMock(
        return_value=[
            {"name": "Español", "lang": "es", "uri": "https://cdn/es.vtt", "forced": False},
            {"name": "English", "lang": "en", "uri": "https://cdn/en.vtt", "forced": False},
        ]
    )
    playlist = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="EN",LANGUAGE="en",URI="en.m3u8"\n'
        "#EXT-X-STREAM-INF:BANDWIDTH=2000000\n"
        "https://cdn/provider/master.m3u8\n"
    )
    out = await service.inject_borrowed_subtitles(
        playlist, "https://iptv.walerike.com", "movie", "us er", "p@ss", "abc"
    )
    assert 'TYPE=SUBTITLES,GROUP-ID="walactv-borrowed",NAME="Español"' in out
    assert "DEFAULT=YES" in out
    assert 'LANGUAGE="es"' in out
    assert 'URI="https://iptv.walerike.com/api/subtitle/movie/us%20er/p%40ss/abc/0"' in out
    assert 'TYPE=SUBTITLES,GROUP-ID="walactv-borrowed",NAME="English",DEFAULT=NO' in out
    assert (
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="EN",LANGUAGE="en",URI="en.m3u8",SUBTITLES="walactv-borrowed"'
        in out
    )
    assert '#EXT-X-STREAM-INF:BANDWIDTH=2000000,SUBTITLES="walactv-borrowed"' in out


def test_get_sibling_stream_urls_movie_excludes_active() -> None:
    service = _make_service()
    first = MagicMock()
    first.mappings.return_value.first.return_value = {"movie_id": "m1"}
    second = MagicMock()
    second.mappings.return_value.all.return_value = [
        {"url": "https://p1/a.m3u8", "provider_id": "abc"},
        {"url": "https://p1/b.m3u8", "provider_id": "other"},
        {"url": "", "provider_id": "no-url"},
        {"url": "https://p1/c.m3u8", "provider_id": "abc2"},
    ]
    service.content_repo.session.execute = MagicMock(side_effect=[first, second])
    urls = service._get_sibling_stream_urls("movie", "abc")
    assert urls == ["https://p1/b.m3u8", "https://p1/c.m3u8"]


async def test_fetch_manifest_text_uses_bootstrap_proxy() -> None:
    service = _make_service()
    service._get_bootstrap_proxy_url = MagicMock(return_value="http://proxy:3128")
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.headers = {"content-type": "application/vnd.apple.mpegurl"}
    fake_response.text = "#EXTM3U\n"
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.get = AsyncMock(return_value=fake_response)
    with patch("iptv_api.services.stream_service.httpx.AsyncClient", return_value=fake_client):
        text = await service._fetch_manifest_text("https://p1/a.m3u8")
    assert text == "#EXTM3U\n"
    assert fake_client.get.await_args.args[0] == "https://p1/a.m3u8"


async def test_fetch_subtitle_file_resolves_redirects_with_proxy_and_fetches() -> None:
    service = _make_service()
    service.resolve_redirects = AsyncMock(return_value="https://p1/cdn/es.vtt")
    service._get_bootstrap_proxy_url = MagicMock(return_value="http://proxy:3128")
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.headers = {"content-type": "text/vtt"}
    fake_response.status_code = 200
    fake_response.content = b"WEBVTT\n"
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.get = AsyncMock(return_value=fake_response)
    with patch("iptv_api.services.stream_service.httpx.AsyncClient", return_value=fake_client):
        status, headers, content = await service.fetch_subtitle_file("https://p1/cdn/es.vtt")
    assert status == 200
    assert content == b"WEBVTT\n"
    assert headers["Content-Type"] == "text/vtt"
    service.resolve_redirects.assert_awaited_once_with(
        "https://p1/cdn/es.vtt", use_cache=True, use_proxy=True
    )
    assert fake_client.get.await_args.args[0] == "https://p1/cdn/es.vtt"
