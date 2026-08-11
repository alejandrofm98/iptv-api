"""Stream Proxy Service v2 — standalone, no inheritance from old service."""

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx
from sqlalchemy import select

import iptv_api.core.constants as CONSTANTS
from iptv_api.repositories.channel_repo import ChannelRepository
from iptv_api.repositories.config_repo import ConfigRepository
from iptv_api.repositories.content_repo import ContentRepository
from iptv_api.repositories.series_repo import SeriesRepository
from iptv_api.services.resilience_service import ResilienceService

logger = logging.getLogger("stream_service_v2")

_active_clients: set[httpx.AsyncClient] = set()


class StreamProxyServiceV2:
    _redirect_cache: dict[str, tuple[str, float]] = {}
    _REDIRECT_CACHE_TTL: float = 300.0
    _DNS_ERROR_CACHE_TTL: float = 60.0
    _proxy_bootstrap_cache: tuple[str | None, float] | None = None
    _PROXY_BOOTSTRAP_CACHE_TTL: float = 60.0
    _subtitle_track_cache: dict[str, tuple[list[dict[str, Any]], float]] = {}
    _SUBTITLE_PROBE_TTL: float = 600.0
    _cache_eviction_counter: int = 0
    _CACHE_EVICTION_INTERVAL: int = 100

    def __init__(
        self,
        config_repo: ConfigRepository,
        channel_repo: ChannelRepository,
        content_repo: ContentRepository,
        series_repo: SeriesRepository,
    ):
        self.config_repo = config_repo
        self.channel_repo = channel_repo
        self.content_repo = content_repo
        self.series_repo = series_repo
        self._url_cache: dict[str, str] = {}
        self._resilience = ResilienceService()

    @staticmethod
    def _mask_proxy_url(proxy_url: str | None) -> str:
        if not proxy_url:
            return "none"
        try:
            parsed = urlparse(proxy_url)
            if parsed.username:
                host = parsed.hostname or ""
                port = f":{parsed.port}" if parsed.port else ""
                return f"{parsed.scheme}://***:***@{host}{port}"
            return proxy_url
        except Exception:
            return "invalid-proxy-url"

    @classmethod
    def _evict_expired_redirects(cls) -> None:
        now = time.time()
        expired = [
            k
            for k, (_, cached_at) in cls._redirect_cache.items()
            if (now - cached_at) > cls._REDIRECT_CACHE_TTL
        ]
        for k in expired:
            del cls._redirect_cache[k]

    def _maybe_evict_caches(self) -> None:
        StreamProxyServiceV2._cache_eviction_counter += 1
        if StreamProxyServiceV2._cache_eviction_counter >= self._CACHE_EVICTION_INTERVAL:
            StreamProxyServiceV2._cache_eviction_counter = 0
            before = len(StreamProxyServiceV2._redirect_cache)
            self._evict_expired_redirects()
            evicted = before - len(StreamProxyServiceV2._redirect_cache)
            if evicted > 0:
                logger.info(f"Cache eviction: {evicted} expired redirect entries removed")

    def _get_bootstrap_proxy_url(self, use_cache: bool = True) -> str | None:
        cached = StreamProxyServiceV2._proxy_bootstrap_cache
        if use_cache and cached:
            cached_value, cached_at = cached
            if (time.time() - cached_at) < self._PROXY_BOOTSTRAP_CACHE_TTL:
                logger.info(
                    f"Proxy bootstrap desde cache: enabled={bool(cached_value)}, proxy={self._mask_proxy_url(cached_value)}"
                )
                return cached_value
        try:
            config = self.config_repo.get_all()
            proxy_ip = (config.get("PROXY_IP") or "").strip()
            proxy_port = (config.get("PROXY_PORT") or "").strip()
            proxy_user = (config.get("PROXY_USER") or "").strip()
            proxy_pass = (config.get("PROXY_PASS") or "").strip()
            proxy_url = None
            if proxy_ip:
                if proxy_ip.startswith("http://") or proxy_ip.startswith("https://"):
                    proxy_url = proxy_ip
                elif proxy_port:
                    if proxy_user and proxy_pass:
                        proxy_url = f"http://{quote(proxy_user, safe='')}:{quote(proxy_pass, safe='')}@{proxy_ip}:{proxy_port}"
                    elif proxy_user:
                        proxy_url = f"http://{quote(proxy_user, safe='')}@{proxy_ip}:{proxy_port}"
                    else:
                        proxy_url = f"http://{proxy_ip}:{proxy_port}"
            StreamProxyServiceV2._proxy_bootstrap_cache = (proxy_url, time.time())
            logger.info(
                f"Proxy bootstrap desde config: enabled={bool(proxy_url)}, proxy={self._mask_proxy_url(proxy_url)}"
            )
            return proxy_url
        except Exception as e:
            logger.warning(f"No se pudo leer proxy de bootstrap: {e}")
            StreamProxyServiceV2._proxy_bootstrap_cache = (None, time.time())
            return None

    async def resolve_redirects(
        self, url: str, use_cache: bool = True, use_proxy: bool = False
    ) -> str:
        import urllib.parse

        self._maybe_evict_caches()

        if use_cache:
            cached = self._redirect_cache.get(url)
            if cached:
                final_url, cached_at = cached
                ttl = self._REDIRECT_CACHE_TTL
                if final_url == url:
                    ttl = self._DNS_ERROR_CACHE_TTL
                if (time.time() - cached_at) < ttl:
                    return final_url
        start_time = time.time()
        hostname = urlparse(url).hostname or "unknown"
        proxy_url = self._get_bootstrap_proxy_url() if use_proxy else None
        logger.info(
            f"Resolviendo redirects: host={hostname}, use_proxy={use_proxy}, proxy={self._mask_proxy_url(proxy_url)}, use_cache={use_cache}"
        )
        try:
            if not await self._resilience.circuit_breaker.can_execute(url):
                logger.warning(f"Circuit breaker OPEN para {hostname}, usando URL original")
                return url
            async with httpx.AsyncClient(
                follow_redirects=False,
                headers={"User-Agent": CONSTANTS.DEFAULT_USER_AGENT},
                timeout=10.0,
                proxy=proxy_url,
            ) as client:
                current_url = url
                max_redirects = 10
                redirect_count = 0
                while redirect_count < max_redirects:
                    try:
                        async with client.stream("GET", current_url) as response:
                            status = response.status_code
                            location = response.headers.get("Location")
                            logger.info(
                                f"Redirect check #{redirect_count + 1}: status={status}, has_location={bool(location)}"
                            )
                    except Exception as e:
                        logger.warning(f"Error evaluando redirect en {current_url[:100]}...: {e}")
                        break
                    if status in (301, 302, 307, 308):
                        if not location:
                            logger.warning(
                                f"Redirect sin Location para {current_url[:100]}... (status={status})"
                            )
                            break
                        if not location.startswith("http"):
                            location = urllib.parse.urljoin(current_url, location)
                        logger.debug(
                            f"  Redirect {redirect_count + 1}: {current_url[:60]} -> {location[:60]}"
                        )
                        current_url = location
                        redirect_count += 1
                    elif status == 511:
                        logger.warning(
                            f"Provider devolvió 511 en bootstrap: {current_url[:100]}... (use_proxy={use_proxy}, proxy={self._mask_proxy_url(proxy_url)})"
                        )
                        break
                    else:
                        break
                final_url = current_url
            await self._resilience.circuit_breaker.record_success(url)
            elapsed = time.time() - start_time
            if final_url != url:
                logger.info(
                    f"Redirect resuelto: {hostname} -> {final_url[:80]}... ({elapsed:.2f}s, {redirect_count} redirects, proxy={self._mask_proxy_url(proxy_url)})"
                )
            else:
                logger.info(
                    f"Sin redirects para {hostname} ({elapsed:.2f}s, proxy={self._mask_proxy_url(proxy_url)})"
                )
            if use_cache:
                self._redirect_cache[url] = (final_url, time.time())
            return final_url
        except Exception as e:
            elapsed = time.time() - start_time
            await self._resilience.circuit_breaker.record_failure(url)
            logger.error(f"Error resolviendo redirects para {hostname} ({elapsed:.2f}s): {e}")
            if use_cache:
                self._redirect_cache[url] = (url, time.time())
            return url

    def _hash_url(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()[:16]

    def _get_content_item(self, table: str, provider_id: str) -> dict | None:
        pid = provider_id.rsplit(".", 1)[0] if "." in provider_id else provider_id
        if table == "channels":
            c = self.channel_repo.get_by_provider_id(pid)
            if c:
                return {"stream_url": c.url, "url": c.url}
        elif table == "movie_streams":
            from iptv_api.models.content import MovieStream

            stmt = (
                select(MovieStream.stream_url, MovieStream.url)
                .where(MovieStream.provider_id == pid)
                .limit(1)
            )
            row_ms = self.content_repo.session.execute(stmt).mappings().first()
            if row_ms:
                return {
                    "stream_url": row_ms.get("stream_url"),
                    "url": row_ms.get("url") or row_ms.get("stream_url"),
                }
        elif table == "series_streams":
            from iptv_api.models.series import SeriesStream

            stmt_series = (
                select(SeriesStream.stream_url, SeriesStream.url)
                .where(SeriesStream.provider_id == pid)
                .limit(1)
            )
            row = self.series_repo.session.execute(stmt_series).mappings().first()
            if row:
                return {
                    "stream_url": row.get("stream_url"),
                    "url": row.get("url") or row.get("stream_url"),
                }
        return None

    def get_original_url(self, provider_id: str, content_type: str = "live") -> str | None:
        cache_key = f"{content_type}:{provider_id}"
        self._maybe_evict_caches()
        if cache_key in self._url_cache:
            return self._url_cache[cache_key]
        table_map = {
            "live": "channels",
            "movie": "movie_streams",
            "series": "series_streams",
        }
        table = table_map.get(content_type, "channels")
        row = self._get_content_item(table, provider_id)
        if row:
            url_value = row.get("url") or row.get("stream_url")
            if url_value:
                self._url_cache[cache_key] = url_value
                return url_value
        return None

    async def proxy_stream(
        self,
        original_url: str,
        headers: dict[str, str] | None = None,
        use_buffer: bool = True,
    ) -> AsyncIterator[bytes]:
        default_headers = {"User-Agent": CONSTANTS.DEFAULT_USER_AGENT}
        if headers:
            default_headers.update(headers)
        if not await self._resilience.circuit_breaker.can_execute(original_url):
            raise Exception(f"Circuit breaker OPEN para {original_url}")
        buffer = self._resilience.create_buffer() if use_buffer else None
        last_error = None
        for attempt in range(self._resilience.retry_service.config.max_attempts):
            try:
                async with (
                    httpx.AsyncClient(
                        timeout=30.0,
                        follow_redirects=True,
                        limits=httpx.Limits(
                            max_keepalive_connections=20,
                            max_connections=50,
                            keepalive_expiry=30.0,
                        ),
                    ) as client,
                    client.stream("GET", original_url, headers=default_headers) as response,
                ):
                    response.raise_for_status()
                    await self._resilience.circuit_breaker.record_success(original_url)
                    if buffer:

                        async def _buffer_task():
                            async for chunk in response.aiter_bytes(chunk_size=8192):
                                await buffer.feed(chunk)
                            buffer.mark_complete()

                        buffer_task = asyncio.create_task(_buffer_task())
                        start_wait = time.time()
                        while not await buffer.should_start_streaming():
                            await asyncio.sleep(0.1)
                            if time.time() - start_wait > 10.0:
                                break
                        while True:
                            chunk = await buffer.get_chunk()
                            if chunk is None:
                                break
                            yield chunk
                        await buffer_task
                    else:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            yield chunk
                    return
            except Exception as e:
                last_error = e
                await self._resilience.circuit_breaker.record_failure(original_url)
                if attempt < self._resilience.retry_service.config.max_attempts - 1:
                    delay = self._resilience.retry_service._calculate_delay(attempt)
                    logger.warning(
                        f"Retry {attempt + 1} tras error: {e}. Esperando {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    break
        logger.error(
            f"Stream falló tras {self._resilience.retry_service.config.max_attempts} intentos: {last_error}"
        )
        raise last_error or Exception("Stream failed")

    @staticmethod
    def _rewrite_m3u8_url(url: str, base_url: str) -> str:
        if not url:
            return url
        if url.startswith("https://"):
            return url
        if url.startswith("http://"):
            return url.replace("http://", "https://", 1)
        if url.startswith("/"):
            parsed_base = urlparse(base_url)
            return f"{parsed_base.scheme}://{parsed_base.netloc}{url}"
        if not url.startswith("http"):
            return urljoin(base_url, url)
        return url

    async def get_stream_response(
        self,
        original_url: str,
        headers: dict[str, str] | None = None,
        use_resilience: bool = True,
        content_type: str | None = None,
        username: str | None = None,
        password: str | None = None,
        stream_id: str | None = None,
        subtitle_base_url: str | None = None,
    ) -> tuple[int, dict[str, str], AsyncIterator[bytes] | str]:
        default_headers = {"User-Agent": CONSTANTS.DEFAULT_USER_AGENT}
        if headers:
            default_headers.update(headers)
        if use_resilience and not await self._resilience.circuit_breaker.can_execute(original_url):
            logger.warning(f"Circuit breaker OPEN para {original_url[:80]}")
            raise Exception("Servicio no disponible - circuit breaker abierto")
        last_error = None
        max_attempts = self._resilience.retry_service.config.max_attempts if use_resilience else 1
        for attempt in range(max_attempts):
            client = None
            try:
                client = httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
                    follow_redirects=True,
                    limits=httpx.Limits(
                        max_keepalive_connections=20,
                        max_connections=50,
                        keepalive_expiry=60.0,
                    ),
                    headers={
                        "Connection": "keep-alive",
                        "Keep-Alive": "timeout=300, max=100",
                    },
                )
                _active_clients.add(client)
                response = await client.send(
                    client.build_request("GET", original_url, headers=default_headers),
                    stream=True,
                )
                retryable_codes = {
                    401,
                    403,
                    502,
                    503,
                    504,
                    511,
                    520,
                    521,
                    522,
                    523,
                    524,
                }
                if response.status_code >= 500 or response.status_code in retryable_codes:
                    _active_clients.discard(client)
                    await client.aclose()
                    error_msg = f"HTTP {response.status_code} from provider"
                    logger.warning(f"Server error {response.status_code}, will retry...")
                    if use_resilience:
                        await self._resilience.circuit_breaker.record_failure(original_url)
                    if attempt < max_attempts - 1:
                        delay = self._resilience.retry_service._calculate_delay(attempt)
                        logger.warning(
                            f"Retry {attempt + 1}/{max_attempts}: {error_msg}. Waiting {delay:.1f}s..."
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        raise Exception(error_msg)
                if use_resilience:
                    await self._resilience.circuit_breaker.record_success(original_url)
                logger.info(f"Stream started: {original_url[:60]}...")
                pass_headers = {}
                important_headers = [
                    "content-type",
                    "content-length",
                    "accept-ranges",
                    "content-range",
                    "last-modified",
                    "etag",
                    "cache-control",
                ]
                for header in important_headers:
                    if header in response.headers:
                        pass_headers[header] = response.headers[header]
                if "accept-ranges" not in pass_headers:
                    pass_headers["accept-ranges"] = "bytes"
                content_type = response.headers.get("content-type", "").lower()
                is_m3u8 = (
                    "mpegurl" in content_type
                    or "m3u8" in content_type
                    or original_url.endswith(".m3u8")
                    or ".m3u8" in original_url.lower()
                )
                if is_m3u8:
                    content = await response.aread()
                    _active_clients.discard(client)
                    await client.aclose()
                    try:
                        m3u8_text = content.decode("utf-8")
                        rewritten = self._process_m3u8(m3u8_text, original_url)
                        if (
                            content_type in ("movie", "series")
                            and username
                            and password
                            and stream_id
                        ):
                            try:
                                rewritten = await self.inject_borrowed_subtitles(
                                    rewritten,
                                    subtitle_base_url or "",
                                    content_type,
                                    username,
                                    password,
                                    stream_id,
                                )
                            except Exception as e:
                                logger.warning(f"Error inyectando subtítulos prestados: {e}")
                        pass_headers["content-type"] = "application/vnd.apple.mpegurl"
                        pass_headers.pop("content-length", None)
                        pass_headers.pop("content-range", None)
                        return (response.status_code, pass_headers, rewritten)
                    except Exception as e:
                        logger.error(f"Error procesando M3U8: {e}")
                        return (
                            response.status_code,
                            pass_headers,
                            content.decode("utf-8", errors="ignore"),
                        )

                async def body_iterator():
                    try:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            yield chunk
                    finally:
                        await response.aclose()
                        if client:
                            _active_clients.discard(client)
                            await client.aclose()

                return (response.status_code, pass_headers, body_iterator())
            except Exception as e:
                last_error = e
                if use_resilience:
                    await self._resilience.circuit_breaker.record_failure(original_url)
                if client:
                    _active_clients.discard(client)
                    try:
                        await client.aclose()
                    except Exception:
                        pass
                if attempt < max_attempts - 1:
                    delay = self._resilience.retry_service._calculate_delay(attempt)
                    logger.warning(
                        f"Retry {attempt + 1}/{max_attempts}: {e}. Esperando {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    break
        logger.error(f"Stream falló tras {max_attempts} intentos: {last_error}")
        raise last_error or Exception("Stream failed")

    def _process_m3u8(self, content: str, base_url: str) -> str:
        lines = content.split("\n")
        processed_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                rewritten_url = self._rewrite_m3u8_url(stripped, base_url)
                processed_lines.append(rewritten_url)
            elif 'URI="' in stripped:

                def rewrite_uri(match):
                    uri = match.group(1)
                    new_uri = self._rewrite_m3u8_url(uri, base_url)
                    return f'URI="{new_uri}"'

                processed_line = re.sub(r'URI="([^"]+)"', rewrite_uri, line)
                processed_lines.append(processed_line)
            else:
                processed_lines.append(line)
        return "\n".join(processed_lines)

    # ------------------------------------------------------------------
    # Subtítulos prestados: extraer pistas de enlaces hermanos (VOD)
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_credentials(url: str, username: str, password: str) -> str:
        if not url:
            return url
        if username:
            url = url.replace("{{USERNAME}}", username)
        if password:
            url = url.replace("{{PASSWORD}}", password)
        return url

    def _get_sibling_stream_urls(self, content_type: str, provider_id: str) -> list[str]:
        """Devuelve URLs crudas de los demás enlaces del mismo contenido (movie/episodio).

        Usa la columna ``url`` (URL directa del proveedor) y no ``stream_url``
        (forma proxied con placeholders) para evitar auto-llamadas al proxy.
        """
        pid = provider_id.rsplit(".", 1)[0] if "." in provider_id else provider_id
        if content_type == "movie":
            from iptv_api.models.content import MovieStream

            row = (
                self.content_repo.session.execute(
                    select(MovieStream.movie_id).where(MovieStream.provider_id == pid).limit(1)
                )
                .mappings()
                .first()
            )
            if not row or not row.get("movie_id"):
                return []
            rows = (
                self.content_repo.session.execute(
                    select(MovieStream.url, MovieStream.provider_id).where(
                        MovieStream.movie_id == row["movie_id"]
                    )
                )
                .mappings()
                .all()
            )
            return [
                r.get("url")
                for r in rows
                if r.get("url") and (r.get("provider_id") or "").rsplit(".", 1)[0] != pid
            ]
        if content_type == "series":
            from iptv_api.models.series import SeriesStream

            row = (
                self.content_repo.session.execute(
                    select(SeriesStream.episode_id).where(SeriesStream.provider_id == pid).limit(1)
                )
                .mappings()
                .first()
            )
            if not row or not row.get("episode_id"):
                return []
            rows = (
                self.content_repo.session.execute(
                    select(SeriesStream.url, SeriesStream.provider_id).where(
                        SeriesStream.episode_id == row["episode_id"]
                    )
                )
                .mappings()
                .all()
            )
            return [
                r.get("url")
                for r in rows
                if r.get("url") and (r.get("provider_id") or "").rsplit(".", 1)[0] != pid
            ]
        return []

    async def _fetch_manifest_text(self, url: str) -> str | None:
        try:
            proxy_url = self._get_bootstrap_proxy_url()
            async with httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True,
                proxy=proxy_url,
                headers={"User-Agent": CONSTANTS.DEFAULT_USER_AGENT},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "").lower()
                if "mpegurl" in content_type or ".m3u8" in url.lower():
                    return resp.text
                return None
        except Exception as e:
            logger.debug(f"No se pudo sondear manifest hermano {url[:80]}: {e}")
            return None

    @staticmethod
    def _split_media_attrs(attr_str: str) -> list[str]:
        parts = []
        current: list[str] = []
        in_quotes = False
        for ch in attr_str:
            if ch == '"':
                in_quotes = not in_quotes
                current.append(ch)
            elif ch == "," and not in_quotes:
                parts.append("".join(current))
                current = []
            else:
                current.append(ch)
        if current:
            parts.append("".join(current))
        return parts

    @classmethod
    def _parse_subtitle_renditions(cls, m3u8_text: str, base_url: str) -> list[dict[str, Any]]:
        tracks = []
        for line in m3u8_text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("#EXT-X-MEDIA"):
                continue
            if ":TYPE=SUBTITLES" not in stripped.replace(" ", "").upper():
                continue
            _, _, attr_str = stripped.partition(":")
            attrs = {}
            for part in cls._split_media_attrs(attr_str):
                if "=" in part:
                    key, _, value = part.partition("=")
                    attrs[key.strip().upper()] = value.strip().strip('"')
            uri = attrs.get("URI")
            if not uri:
                continue
            tracks.append(
                {
                    "name": attrs.get("NAME") or attrs.get("LANGUAGE") or "Subtítulos",
                    "lang": attrs.get("LANGUAGE", ""),
                    "uri": cls._rewrite_m3u8_url(uri, base_url),
                    "forced": attrs.get("FORCED", "").upper() == "YES",
                }
            )
        return tracks

    async def get_borrowed_subtitle_tracks(
        self, content_type: str, username: str, password: str, provider_id: str
    ) -> list[dict[str, Any]]:
        """Extrae (con cache) las pistas de subtítulos de los enlaces hermanos."""
        pid = provider_id.rsplit(".", 1)[0] if "." in provider_id else provider_id
        cache_key = f"{content_type}:{pid}"
        now = time.time()
        cached = StreamProxyServiceV2._subtitle_track_cache.get(cache_key)
        if cached and (now - cached[1]) < StreamProxyServiceV2._SUBTITLE_PROBE_TTL:
            return cached[0]

        tracks: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for raw_url in self._get_sibling_stream_urls(content_type, pid):
            url = self._resolve_credentials(raw_url, username, password)
            if not url:
                continue
            try:
                resolved = await self.resolve_redirects(url, use_cache=True, use_proxy=True)
            except Exception:
                continue
            text = await self._fetch_manifest_text(resolved)
            if not text:
                continue
            for track in self._parse_subtitle_renditions(text, resolved):
                key = (track["lang"], track["name"])
                if key in seen or track["forced"]:
                    continue
                seen.add(key)
                tracks.append(track)

        StreamProxyServiceV2._subtitle_track_cache[cache_key] = (tracks, now)
        return tracks

    @staticmethod
    def _escape_media_attr(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _build_subtitle_proxy_uri(
        base_url: str, content_type: str, username: str, password: str, provider_id: str, index: int
    ) -> str:
        return (
            f"{base_url}/api/subtitle/{content_type}/"
            f"{quote(username, safe='')}/{quote(password, safe='')}/"
            f"{quote(provider_id, safe='')}/{index}"
        )

    @classmethod
    def _wire_subtitle_group(cls, m3u8_text: str, group_id: str) -> str:
        out = []
        for line in m3u8_text.splitlines():
            stripped = line.strip()
            if (
                stripped.startswith("#EXT-X-MEDIA:TYPE=AUDIO")
                or stripped.startswith("#EXT-X-STREAM-INF")
            ) and 'SUBTITLES="' not in stripped:
                out.append(f'{line},SUBTITLES="{group_id}"')
            else:
                out.append(line)
        return "\n".join(out)

    async def inject_borrowed_subtitles(
        self,
        m3u8_text: str,
        base_url: str,
        content_type: str,
        username: str,
        password: str,
        provider_id: str,
    ) -> str:
        """Inyecta subtítulos de enlaces hermanos si el manifest no trae pistas propias."""
        has_own_subtitles = any(
            line.strip().startswith("#EXT-X-MEDIA")
            and ":TYPE=SUBTITLES" in line.replace(" ", "").upper()
            for line in m3u8_text.splitlines()
        )
        if has_own_subtitles:
            return m3u8_text

        tracks = await self.get_borrowed_subtitle_tracks(
            content_type, username, password, provider_id
        )
        if not tracks:
            return m3u8_text

        pid = provider_id.rsplit(".", 1)[0] if "." in provider_id else provider_id
        group_id = "walactv-borrowed"
        media_lines = []
        for idx, track in enumerate(tracks):
            uri = self._build_subtitle_proxy_uri(
                base_url, content_type, username, password, pid, idx
            )
            attrs = [
                "TYPE=SUBTITLES",
                f'GROUP-ID="{group_id}"',
                f'NAME="{self._escape_media_attr(track.get("name") or "Subtítulos")}"',
                f"DEFAULT={'YES' if idx == 0 else 'NO'}",
                "AUTOSELECT=YES",
                f'LANGUAGE="{self._escape_media_attr(track.get("lang") or "")}"',
                f'URI="{self._escape_media_attr(uri)}"',
            ]
            media_lines.append("#EXT-X-MEDIA:" + ",".join(attrs))

        lines = m3u8_text.split("\n")
        output: list[str] = []
        injected = False
        for line in lines:
            output.append(line)
            if not injected and line.strip() == "#EXTM3U":
                output.extend(media_lines)
                injected = True
        if not injected:
            output.extend(media_lines)
        rewritten = "\n".join(output)

        if 'SUBTITLES="' not in rewritten:
            rewritten = self._wire_subtitle_group(rewritten, group_id)
        return rewritten

    async def fetch_subtitle_file(self, url: str) -> tuple[int, dict[str, str], bytes]:
        resolved = await self.resolve_redirects(url, use_cache=True, use_proxy=True)
        proxy_url = self._get_bootstrap_proxy_url()
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            proxy=proxy_url,
            headers={"User-Agent": CONSTANTS.DEFAULT_USER_AGENT},
        ) as client:
            resp = await client.get(resolved)
            resp.raise_for_status()
        headers = {
            "Content-Type": resp.headers.get("content-type", "text/vtt"),
            "Cache-Control": "no-cache",
        }
        return resp.status_code, headers, resp.content

    def clear_cache(self):
        self._url_cache.clear()

    def preload_cache(self):
        channels = self.channel_repo.get_all()
        for c in channels:
            if c.url:
                stream_id = self._hash_url(c.url)
                self._url_cache[f"live:{stream_id}"] = c.url
        logger.info(f"Cache precargado: {len(self._url_cache)} URLs")

    def get_resilience_status(self) -> dict[str, Any]:
        return self._resilience.get_status()

    @staticmethod
    async def close_all_clients() -> None:
        for client in list(_active_clients):
            try:
                await client.aclose()
            except Exception:
                pass
        _active_clients.clear()
