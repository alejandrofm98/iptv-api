"""Stream Proxy Service v2 — standalone, no inheritance from old service."""
import hashlib
import time
import asyncio
import logging
import re
from typing import Optional, Dict, Any, AsyncIterator, Tuple, Union
from urllib.parse import urlparse, urljoin, quote

import httpx
from sqlalchemy import select

import utils.constants as CONSTANTS
from app.repositories.config_repo import ConfigRepository
from app.repositories.content_repo import ContentRepository
from app.repositories.channel_repo import ChannelRepository
from app.repositories.series_repo import SeriesRepository
from services.resilience_service import ResilienceService

logger = logging.getLogger("stream_service_v2")


class StreamProxyServiceV2:
    _redirect_cache: Dict[str, Tuple[str, float]] = {}
    _REDIRECT_CACHE_TTL: float = 300.0
    _DNS_ERROR_CACHE_TTL: float = 60.0
    _proxy_bootstrap_cache: Optional[Tuple[Optional[str], float]] = None
    _PROXY_BOOTSTRAP_CACHE_TTL: float = 60.0

    def __init__(self, config_repo: ConfigRepository, channel_repo: ChannelRepository,
                 content_repo: ContentRepository, series_repo: SeriesRepository):
        self.config_repo = config_repo
        self.channel_repo = channel_repo
        self.content_repo = content_repo
        self.series_repo = series_repo
        self._url_cache: Dict[str, str] = {}
        self._resilience = ResilienceService()

    @staticmethod
    def _mask_proxy_url(proxy_url: Optional[str]) -> str:
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

    def _get_bootstrap_proxy_url(self, use_cache: bool = True) -> Optional[str]:
        cached = StreamProxyServiceV2._proxy_bootstrap_cache
        if use_cache and cached:
            cached_value, cached_at = cached
            if (time.time() - cached_at) < self._PROXY_BOOTSTRAP_CACHE_TTL:
                logger.info(f"Proxy bootstrap desde cache: enabled={bool(cached_value)}, proxy={self._mask_proxy_url(cached_value)}")
                return cached_value
        try:
            config = self.config_repo.get_all()
            proxy_ip = (config.get('PROXY_IP') or '').strip()
            proxy_port = (config.get('PROXY_PORT') or '').strip()
            proxy_user = (config.get('PROXY_USER') or '').strip()
            proxy_pass = (config.get('PROXY_PASS') or '').strip()
            proxy_url = None
            if proxy_ip:
                if proxy_ip.startswith('http://') or proxy_ip.startswith('https://'):
                    proxy_url = proxy_ip
                elif proxy_port:
                    if proxy_user and proxy_pass:
                        proxy_url = f"http://{quote(proxy_user, safe='')}:{quote(proxy_pass, safe='')}@{proxy_ip}:{proxy_port}"
                    elif proxy_user:
                        proxy_url = f"http://{quote(proxy_user, safe='')}@{proxy_ip}:{proxy_port}"
                    else:
                        proxy_url = f"http://{proxy_ip}:{proxy_port}"
            StreamProxyServiceV2._proxy_bootstrap_cache = (proxy_url, time.time())
            logger.info(f"Proxy bootstrap desde config: enabled={bool(proxy_url)}, proxy={self._mask_proxy_url(proxy_url)}")
            return proxy_url
        except Exception as e:
            logger.warning(f"No se pudo leer proxy de bootstrap: {e}")
            StreamProxyServiceV2._proxy_bootstrap_cache = (None, time.time())
            return None

    async def resolve_redirects(self, url: str, use_cache: bool = True, use_proxy: bool = False) -> str:
        import urllib.parse
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
        logger.info(f"Resolviendo redirects: host={hostname}, use_proxy={use_proxy}, proxy={self._mask_proxy_url(proxy_url)}, use_cache={use_cache}")
        try:
            if not await self._resilience.circuit_breaker.can_execute(url):
                logger.warning(f"Circuit breaker OPEN para {hostname}, usando URL original")
                return url
            async with httpx.AsyncClient(
                follow_redirects=False,
                headers={'User-Agent': CONSTANTS.DEFAULT_USER_AGENT},
                timeout=10.0,
                proxy=proxy_url
            ) as client:
                current_url = url
                max_redirects = 10
                redirect_count = 0
                while redirect_count < max_redirects:
                    try:
                        async with client.stream("GET", current_url) as response:
                            status = response.status_code
                            location = response.headers.get('Location')
                            logger.info(f"Redirect check #{redirect_count + 1}: status={status}, has_location={bool(location)}")
                    except Exception as e:
                        logger.warning(f"Error evaluando redirect en {current_url[:100]}...: {e}")
                        break
                    if status in (301, 302, 307, 308):
                        if not location:
                            logger.warning(f"Redirect sin Location para {current_url[:100]}... (status={status})")
                            break
                        if not location.startswith('http'):
                            location = urllib.parse.urljoin(current_url, location)
                        logger.debug(f"  Redirect {redirect_count + 1}: {current_url[:60]} -> {location[:60]}")
                        current_url = location
                        redirect_count += 1
                    elif status == 511:
                        logger.warning(f"Provider devolvió 511 en bootstrap: {current_url[:100]}... (use_proxy={use_proxy}, proxy={self._mask_proxy_url(proxy_url)})")
                        break
                    else:
                        break
                final_url = current_url
            await self._resilience.circuit_breaker.record_success(url)
            elapsed = time.time() - start_time
            if final_url != url:
                logger.info(f"Redirect resuelto: {hostname} -> {final_url[:80]}... ({elapsed:.2f}s, {redirect_count} redirects, proxy={self._mask_proxy_url(proxy_url)})")
            else:
                logger.info(f"Sin redirects para {hostname} ({elapsed:.2f}s, proxy={self._mask_proxy_url(proxy_url)})")
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

    def _get_content_item(self, table: str, provider_id: str) -> Optional[dict]:
        if table == 'channels':
            c = self.channel_repo.get_by_provider_id(provider_id)
            if c:
                return {"stream_url": c.url, "url": c.url}
        elif table == 'movie_streams':
            m = self.content_repo.search_by_provider_id(provider_id)
            if m:
                streams = self.content_repo._get_movie_streams(m.id)
                if streams:
                    return {"stream_url": streams[0].get('url'), "url": streams[0].get('url')}
        elif table == 'series_streams':
            s = self.series_repo.search_by_provider_id(provider_id)
            if s:
                from app.models.series import SeriesStream
                stmt = select(SeriesStream.stream_url).where(SeriesStream.provider_id == provider_id).limit(1)
                row = self.series_repo.session.execute(stmt).scalar()
                if row:
                    return {"stream_url": row, "url": row}
        return None

    def get_original_url(self, provider_id: str, content_type: str = 'live') -> Optional[str]:
        cache_key = f"{content_type}:{provider_id}"
        if cache_key in self._url_cache:
            return self._url_cache[cache_key]
        table_map = {'live': 'channels', 'movie': 'movie_streams', 'series': 'series_streams'}
        table = table_map.get(content_type, 'channels')
        row = self._get_content_item(table, provider_id)
        if row:
            url_value = row.get('stream_url') or row.get('url')
            if url_value:
                self._url_cache[cache_key] = url_value
                return url_value
        return None

    async def proxy_stream(
        self,
        original_url: str,
        headers: Optional[Dict[str, str]] = None,
        use_buffer: bool = True
    ) -> AsyncIterator[bytes]:
        default_headers = {'User-Agent': CONSTANTS.DEFAULT_USER_AGENT}
        if headers:
            default_headers.update(headers)
        if not await self._resilience.circuit_breaker.can_execute(original_url):
            raise Exception(f"Circuit breaker OPEN para {original_url}")
        buffer = self._resilience.create_buffer() if use_buffer else None
        last_error = None
        for attempt in range(self._resilience.retry_service.config.max_attempts):
            try:
                async with httpx.AsyncClient(
                    timeout=30.0,
                    follow_redirects=True,
                    limits=httpx.Limits(
                        max_keepalive_connections=20,
                        max_connections=50,
                        keepalive_expiry=30.0
                    )
                ) as client:
                    async with client.stream('GET', original_url, headers=default_headers) as response:
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
                    logger.warning(f"Retry {attempt + 1} tras error: {e}. Esperando {delay:.1f}s...")
                    await asyncio.sleep(delay)
                else:
                    break
        logger.error(f"Stream falló tras {self._resilience.retry_service.config.max_attempts} intentos: {last_error}")
        raise last_error or Exception("Stream failed")

    def _rewrite_m3u8_url(self, url: str, base_url: str) -> str:
        if not url:
            return url
        if url.startswith('https://'):
            return url
        if url.startswith('http://'):
            return url.replace('http://', 'https://', 1)
        if url.startswith('/'):
            parsed_base = urlparse(base_url)
            return f"{parsed_base.scheme}://{parsed_base.netloc}{url}"
        if not url.startswith('http'):
            return urljoin(base_url, url)
        return url

    async def get_stream_response(
        self,
        original_url: str,
        headers: Optional[Dict[str, str]] = None,
        use_resilience: bool = True
    ) -> Tuple[int, Dict[str, str], Union[AsyncIterator[bytes], str]]:
        default_headers = {'User-Agent': CONSTANTS.DEFAULT_USER_AGENT}
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
                    limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=60.0),
                    headers={'Connection': 'keep-alive', 'Keep-Alive': 'timeout=300, max=100'}
                )
                response = await client.send(
                    client.build_request('GET', original_url, headers=default_headers),
                    stream=True
                )
                retryable_codes = {401, 403, 502, 503, 504, 511, 520, 521, 522, 523, 524}
                if response.status_code >= 500 or response.status_code in retryable_codes:
                    await client.aclose()
                    error_msg = f"HTTP {response.status_code} from provider"
                    logger.warning(f"Server error {response.status_code}, will retry...")
                    if use_resilience:
                        await self._resilience.circuit_breaker.record_failure(original_url)
                    if attempt < max_attempts - 1:
                        delay = self._resilience.retry_service._calculate_delay(attempt)
                        logger.warning(f"Retry {attempt + 1}/{max_attempts}: {error_msg}. Waiting {delay:.1f}s...")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        raise Exception(error_msg)
                if use_resilience:
                    await self._resilience.circuit_breaker.record_success(original_url)
                logger.info(f"Stream started: {original_url[:60]}...")
                pass_headers = {}
                important_headers = [
                    'content-type', 'content-length', 'accept-ranges', 'content-range',
                    'last-modified', 'etag', 'cache-control'
                ]
                for header in important_headers:
                    if header in response.headers:
                        pass_headers[header] = response.headers[header]
                if 'accept-ranges' not in pass_headers:
                    pass_headers['accept-ranges'] = 'bytes'
                content_type = response.headers.get('content-type', '').lower()
                is_m3u8 = ('mpegurl' in content_type or 'm3u8' in content_type or
                           original_url.endswith('.m3u8') or '.m3u8' in original_url.lower())
                if is_m3u8:
                    content = await response.aread()
                    await client.aclose()
                    try:
                        m3u8_text = content.decode('utf-8')
                        rewritten = self._process_m3u8(m3u8_text, original_url)
                        pass_headers['content-type'] = 'application/vnd.apple.mpegurl'
                        pass_headers.pop('content-length', None)
                        pass_headers.pop('content-range', None)
                        return (response.status_code, pass_headers, rewritten)
                    except Exception as e:
                        logger.error(f"Error procesando M3U8: {e}")
                        return (response.status_code, pass_headers, content.decode('utf-8', errors='ignore'))
                async def body_iterator():
                    try:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            yield chunk
                    finally:
                        await response.aclose()
                        if client:
                            await client.aclose()
                return (response.status_code, pass_headers, body_iterator())
            except Exception as e:
                last_error = e
                if use_resilience:
                    await self._resilience.circuit_breaker.record_failure(original_url)
                if client:
                    try:
                        await client.aclose()
                    except Exception:
                        pass
                if attempt < max_attempts - 1:
                    delay = self._resilience.retry_service._calculate_delay(attempt)
                    logger.warning(f"Retry {attempt + 1}/{max_attempts}: {e}. Esperando {delay:.1f}s...")
                    await asyncio.sleep(delay)
                else:
                    break
        logger.error(f"Stream falló tras {max_attempts} intentos: {last_error}")
        raise last_error or Exception("Stream failed")

    def _process_m3u8(self, content: str, base_url: str) -> str:
        lines = content.split('\n')
        processed_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
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
        return '\n'.join(processed_lines)

    def clear_cache(self):
        self._url_cache.clear()

    def preload_cache(self):
        channels = self.channel_repo.get_all()
        for c in channels:
            if c.url:
                stream_id = self._hash_url(c.url)
                self._url_cache[f"live:{stream_id}"] = c.url
        logger.info(f"Cache precargado: {len(self._url_cache)} URLs")

    def get_resilience_status(self) -> Dict[str, Any]:
        return self._resilience.get_status()
