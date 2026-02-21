"""
Servicio de proxy para streams IPTV con resiliencia
"""
import hashlib
import time
import httpx
import re
import asyncio
import logging
from typing import Optional, Dict, Any, AsyncIterator, Tuple, Union
from urllib.parse import urlparse, urljoin
from supabase import Client

import utils.constants as CONSTANTS
from services.resilience_service import ResilienceService, StreamBuffer

logger = logging.getLogger("stream_service")
logger.setLevel(logging.DEBUG)


class StreamProxyService:
    """Servicio para proxificar streams IPTV"""

    # Cache de redirects compartido entre instancias: url -> (final_url, timestamp)
    _redirect_cache: Dict[str, Tuple[str, float]] = {}
    # TTL del cache de redirects en segundos (5 minutos)
    _REDIRECT_CACHE_TTL: float = 300.0
    # TTL para errores de DNS (60 segundos) - evita reintentos constantes
    _DNS_ERROR_CACHE_TTL: float = 60.0

    def __init__(self, supabase: Client):
        self.supabase = supabase
        # Cache de URLs originales: stream_id -> url original
        self._url_cache: Dict[str, str] = {}
        # Servicio de resiliencia: circuit breaker + retry + buffering
        self._resilience = ResilienceService()

    async def resolve_redirects(self, url: str) -> str:
        """
        Resuelve redirects HTTP manualmente para mantener la misma conexión TCP.

        Esto es necesario porque algunos proveedores devuelven 302 redirects
        y bloquean IPs que cambian entre redirects. Al seguir los redirects
        manualmente en la misma conexión, la IP de origen permanece constante.

        Incluye cache con TTL para evitar resolver el mismo URL repetidamente.

        Args:
            url: URL inicial que puede tener redirects

        Returns:
            URL final después de seguir todos los redirects, o URL original si hay error
        """
        import urllib.parse

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

        try:
            if not await self._resilience.circuit_breaker.can_execute(url):
                print(f"⚠️ Circuit breaker OPEN para {hostname}, usando URL original")
                return url

            async with httpx.AsyncClient(
                follow_redirects=False,
                headers={'User-Agent': CONSTANTS.DEFAULT_USER_AGENT},
                timeout=10.0
            ) as client:
                current_url = url
                max_redirects = 10
                redirect_count = 0

                while redirect_count < max_redirects:
                    response = await client.get(current_url)

                    if response.status_code in (301, 302):
                        location = response.headers.get('Location')
                        if not location:
                            break
                        if not location.startswith('http'):
                            location = urllib.parse.urljoin(current_url, location)
                        current_url = location
                        redirect_count += 1
                    else:
                        break

                final_url = current_url

            await self._resilience.circuit_breaker.record_success(url)

            elapsed = time.time() - start_time
            if final_url != url:
                print(f"Redirect resuelto: {hostname} -> {final_url[:80]}... ({elapsed:.2f}s, {redirect_count} redirects)")

            self._redirect_cache[url] = (final_url, time.time())
            return final_url

        except Exception as e:
            elapsed = time.time() - start_time
            await self._resilience.circuit_breaker.record_failure(url)
            print(f"Error resolviendo redirects para {hostname} ({elapsed:.2f}s): {e}")
            self._redirect_cache[url] = (url, time.time())
            return url

    def _hash_url(self, url: str) -> str:
        """Genera hash de URL (mismo método que playlist_service)"""
        return hashlib.md5(url.encode()).hexdigest()[:16]

    def get_original_url(self, provider_id: str, content_type: str = 'live') -> Optional[str]:
        """
        Obtiene la URL original de un stream a partir de su provider_id

        Args:
            provider_id: ID del proveedor (ej: "176861" de la URL)
            content_type: 'live', 'movie' o 'series'

        Returns:
            URL original del stream o None
        """
        # Primero buscar en cache
        cache_key = f"{content_type}:{provider_id}"
        if cache_key in self._url_cache:
            return self._url_cache[cache_key]

        # Determinar tabla según tipo
        table_map = {
            'live': 'channels',
            'movie': 'movies',
            'series': 'series'
        }

        table = table_map.get(content_type, 'channels')

        # Buscar en la base de datos por provider_id (mucho más rápido que hash)
        result = self.supabase.table(table).select('url').eq('provider_id', provider_id).limit(1).execute()

        if result.data and len(result.data) > 0:
            url = result.data[0].get('url', '')
            if url:
                # Guardar en cache
                self._url_cache[cache_key] = url
                return url

        return None

    async def proxy_stream(
        self,
        original_url: str,
        headers: Optional[Dict[str, str]] = None,
        use_buffer: bool = True
    ) -> AsyncIterator[bytes]:
        """
        Proxifica un stream IPTV con retry logic, circuit breaker y pre-buffering.

        Args:
            original_url: URL original del stream
            headers: Headers adicionales para la solicitud
            use_buffer: Si True, usa pre-buffering para estabilidad

        Yields:
            Chunks de bytes del stream
        """
        default_headers = {
            'User-Agent': CONSTANTS.DEFAULT_USER_AGENT
        }

        if headers:
            default_headers.update(headers)

        # Verificar circuit breaker
        if not await self._resilience.circuit_breaker.can_execute(original_url):
            raise Exception(f"Circuit breaker OPEN para {original_url}")

        buffer = self._resilience.create_buffer() if use_buffer else None
        last_error = None

        # Intentar con retry
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

                        # Registrar éxito del circuit breaker
                        await self._resilience.circuit_breaker.record_success(original_url)

                        if buffer:
                            # Modo con buffer: alimentar buffer primero
                            async def _buffer_task():
                                async for chunk in response.aiter_bytes(chunk_size=8192):
                                    await buffer.feed(chunk)
                                buffer.mark_complete()

                            # Iniciar buffer en background
                            buffer_task = asyncio.create_task(_buffer_task())

                            # Esperar a tener suficiente buffer
                            start_wait = time.time()
                            while not await buffer.should_start_streaming():
                                await asyncio.sleep(0.1)
                                if time.time() - start_wait > 10.0:  # Timeout de buffer
                                    break

                            # Empezar a servir desde buffer
                            while True:
                                chunk = await buffer.get_chunk()
                                if chunk is None:
                                    break
                                yield chunk

                            await buffer_task
                        else:
                            # Modo sin buffer: stream directo
                            async for chunk in response.aiter_bytes(chunk_size=8192):
                                yield chunk

                        return  # Éxito, salir

            except Exception as e:
                last_error = e
                await self._resilience.circuit_breaker.record_failure(original_url)

                if attempt < self._resilience.retry_service.config.max_attempts - 1:
                    delay = self._resilience.retry_service._calculate_delay(attempt)
                    print(f"🔄 Retry {attempt + 1} tras error: {e}. Esperando {delay:.1f}s...")
                    await asyncio.sleep(delay)
                else:
                    break

        print(f"❌ Stream falló tras {self._resilience.retry_service.config.max_attempts} intentos: {last_error}")
        raise last_error or Exception("Stream failed")

    def _rewrite_m3u8_url(self, url: str, base_url: str) -> str:
        """
        Reescribe URLs dentro de un M3U8 para evitar Mixed Content.
        Convierte HTTP a HTTPS o las pasa por el proxy si es necesario.
        """
        if not url:
            return url

        # Si ya es HTTPS, dejarlo así
        if url.startswith('https://'):
            return url

        # Si es HTTP, convertir a HTTPS si es posible
        if url.startswith('http://'):
            # Intentar convertir a HTTPS primero
            https_url = url.replace('http://', 'https://', 1)
            return https_url

        # Si es una URL relativa, convertirla a absoluta
        if url.startswith('/'):
            parsed_base = urlparse(base_url)
            return f"{parsed_base.scheme}://{parsed_base.netloc}{url}"

        if not url.startswith('http'):
            # URL relativa sin slash inicial
            return urljoin(base_url, url)

        return url

    async def get_stream_response(
        self,
        original_url: str,
        headers: Optional[Dict[str, str]] = None,
        use_resilience: bool = True
    ) -> Tuple[int, Dict[str, str], Union[AsyncIterator[bytes], str]]:
        """
        Obtiene respuesta de stream con headers.
        Si el contenido es M3U8, lo procesa y reescribe URLs HTTP a HTTPS.
        Soporta Range Requests para permitir seek en videos (VOD).
        
        Ahora con resiliencia: circuit breaker + retry logic.

        Args:
            original_url: URL del stream
            headers: Headers adicionales
            use_resilience: Si True, usa circuit breaker y retry

        Returns:
            (status_code, response_headers, body_iterator o contenido_m3u8)
        """
        default_headers = {
            'User-Agent': CONSTANTS.DEFAULT_USER_AGENT
        }

        if headers:
            default_headers.update(headers)

        # Verificar circuit breaker antes de intentar
        if use_resilience and not await self._resilience.circuit_breaker.can_execute(original_url):
            logger.warning(f"⚠️ Circuit breaker OPEN para {original_url[:80]}")
            raise Exception(f"Servicio no disponible - circuit breaker abierto")

        last_error = None
        max_attempts = self._resilience.retry_service.config.max_attempts if use_resilience else 1

        for attempt in range(max_attempts):
            client = None
            try:
                client = httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        connect=10.0,
                        read=300.0,  # 5 min timeout de lectura
                        write=10.0,
                        pool=10.0
                    ),
                    follow_redirects=True,
                    limits=httpx.Limits(
                        max_keepalive_connections=20,
                        max_connections=50,
                        keepalive_expiry=60.0
                    ),
                    headers={
                        'Connection': 'keep-alive',
                        'Keep-Alive': 'timeout=300, max=100'
                    }
                )

                response = await client.send(
                    client.build_request('GET', original_url, headers=default_headers),
                    stream=True
                )

                # Verificar status code - reintentar en errores temporales
                retryable_codes = {401, 403, 502, 503, 504, 511, 520, 521, 522, 523, 524}
                if response.status_code >= 500 or response.status_code in retryable_codes:
                    await client.aclose()
                    error_msg = f"HTTP {response.status_code} from provider"
                    logger.warning(f"⚠️ Server error {response.status_code}, will retry...")
                    if use_resilience:
                        await self._resilience.circuit_breaker.record_failure(original_url)
                    
                    if attempt < max_attempts - 1:
                        delay = self._resilience.retry_service._calculate_delay(attempt)
                        logger.warning(f"🔄 Retry {attempt + 1}/{max_attempts}: {error_msg}. Waiting {delay:.1f}s...")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        raise Exception(error_msg)

                # Registrar éxito en circuit breaker
                if use_resilience:
                    await self._resilience.circuit_breaker.record_success(original_url)
                
                logger.info(f"📺 Stream started: {original_url[:60]}...")

                # Headers relevantes para pasar al cliente
                pass_headers = {}
                important_headers = [
                    'content-type', 'content-length', 'accept-ranges', 'content-range',
                    'last-modified', 'etag', 'cache-control'
                ]
                for header in important_headers:
                    if header in response.headers:
                        pass_headers[header] = response.headers[header]

                # Siempre indicar que aceptamos Range Requests (para VOD)
                if 'accept-ranges' not in pass_headers:
                    pass_headers['accept-ranges'] = 'bytes'

                # Detectar si es M3U8 por content-type o extensión
                content_type = response.headers.get('content-type', '').lower()
                is_m3u8 = ('mpegurl' in content_type or
                           'm3u8' in content_type or
                           original_url.endswith('.m3u8') or
                           '.m3u8' in original_url.lower())

                if is_m3u8:
                    # Procesar M3U8 y reescribir URLs
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
                        print(f"Error procesando M3U8: {e}")
                        return (response.status_code, pass_headers, content)

                # Para streams de video (TS), usar el proxy con iterador
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
                
                # Registrar fallo en circuit breaker
                if use_resilience:
                    await self._resilience.circuit_breaker.record_failure(original_url)

                # Limpiar cliente si existe
                if client:
                    try:
                        await client.aclose()
                    except:
                        pass

                # Decidir si reintentar
                if attempt < max_attempts - 1:
                    delay = self._resilience.retry_service._calculate_delay(attempt)
                    logger.warning(f"🔄 Retry {attempt + 1}/{max_attempts}: {e}. Esperando {delay:.1f}s...")
                    await asyncio.sleep(delay)
                else:
                    break

        # Todos los intentos fallaron
        logger.error(f"❌ Stream falló tras {max_attempts} intentos: {last_error}")
        raise last_error or Exception("Stream failed")

    def _process_m3u8(self, content: str, base_url: str) -> str:
        """
        Procesa el contenido de un archivo M3U8 y reescribe URLs HTTP a HTTPS.
        """
        lines = content.split('\n')
        processed_lines = []

        for line in lines:
            stripped = line.strip()

            # Si la línea es un URL (no empieza con # y no está vacía)
            if stripped and not stripped.startswith('#'):
                rewritten_url = self._rewrite_m3u8_url(stripped, base_url)
                processed_lines.append(rewritten_url)
            # Si es una línea EXT-X-KEY con URI
            elif 'URI="' in stripped:
                # Reescribir URI en tags como #EXT-X-KEY
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
        """Limpia el cache de URLs"""
        self._url_cache.clear()

    def preload_cache(self):
        """Precarga el cache con todas las URLs"""
        tables = ['channels', 'movies', 'series']
        type_map = {'channels': 'live', 'movies': 'movie', 'series': 'series'}

        for table in tables:
            result = self.supabase.table(table).select('url').execute()
            content_type = type_map[table]

            for item in (result.data or []):
                url = item.get('url', '')
                if url:
                    stream_id = self._hash_url(url)
                    cache_key = f"{content_type}:{stream_id}"
                    self._url_cache[cache_key] = url

        print(f"✅ Cache precargado: {len(self._url_cache)} URLs")

    def get_resilience_status(self) -> Dict[str, Any]:
        """Obtiene el estado de resiliencia del servicio"""
        return self._resilience.get_status()
