"""
Servicio de proxy para streams IPTV
"""
import hashlib
import time
import httpx
import re
from typing import Optional, Dict, Any, AsyncIterator, Tuple, Union
from urllib.parse import urlparse, urljoin
from supabase import Client

import utils.constants as CONSTANTS


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

    async def resolve_redirects(self, url: str) -> str:
        """
        Resuelve redirects HTTP y devuelve la URL final (async).

        Esto es necesario porque algunos proveedores devuelven 302 redirects
        a URLs HTTP, lo que causa problemas de Mixed Content en clientes HTTPS.

        Incluye cache con TTL para evitar resolver el mismo URL repetidamente,
        especialmente cuando hay fallos de DNS.

        Args:
            url: URL inicial que puede tener redirects

        Returns:
            URL final después de seguir todos los redirects, o URL original si hay error
        """
        # Revisar cache primero
        cached = self._redirect_cache.get(url)
        if cached:
            final_url, cached_at = cached
            ttl = self._REDIRECT_CACHE_TTL
            # Si el cache devolvió la misma URL (error), usar TTL corto
            if final_url == url:
                ttl = self._DNS_ERROR_CACHE_TTL
            if (time.time() - cached_at) < ttl:
                return final_url

        start_time = time.time()
        hostname = urlparse(url).hostname or "unknown"

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                headers={'User-Agent': CONSTANTS.DEFAULT_USER_AGENT},
                timeout=5.0
            ) as client:
                response = await client.send(
                    client.build_request('GET', url),
                    stream=True
                )
                final_url = str(response.url)
                elapsed = time.time() - start_time
                await response.aclose()

                if final_url != url:
                    print(f"Redirect resuelto: {hostname} ({elapsed:.2f}s)")

                # Guardar en cache
                self._redirect_cache[url] = (final_url, time.time())
                return final_url

        except httpx.TimeoutException:
            elapsed = time.time() - start_time
            print(f"Timeout resolviendo redirects para {hostname} ({elapsed:.2f}s)")
            self._redirect_cache[url] = (url, time.time())
            return url
        except OSError as e:
            # Errores de DNS/red: [Errno -2] Name or service not known, etc.
            elapsed = time.time() - start_time
            print(f"Error DNS/red resolviendo {hostname} ({elapsed:.2f}s): {e}")
            self._redirect_cache[url] = (url, time.time())
            return url
        except Exception as e:
            elapsed = time.time() - start_time
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
        headers: Optional[Dict[str, str]] = None
    ) -> AsyncIterator[bytes]:
        """
        Proxifica un stream IPTV

        Args:
            original_url: URL original del stream
            headers: Headers adicionales para la solicitud

        Yields:
            Chunks de bytes del stream
        """
        default_headers = {
            'User-Agent': CONSTANTS.DEFAULT_USER_AGENT
        }

        if headers:
            default_headers.update(headers)

        async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
            async with client.stream('GET', original_url, headers=default_headers) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    yield chunk

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
        headers: Optional[Dict[str, str]] = None
    ) -> Tuple[int, Dict[str, str], Union[AsyncIterator[bytes], str]]:
        """
        Obtiene respuesta de stream con headers.
        Si el contenido es M3U8, lo procesa y reescribe URLs HTTP a HTTPS.
        Soporta Range Requests para permitir seek en videos (VOD).

        Returns:
            (status_code, response_headers, body_iterator o contenido_m3u8)
        """
        default_headers = {
            'User-Agent': CONSTANTS.DEFAULT_USER_AGENT
        }

        if headers:
            default_headers.update(headers)

        client = httpx.AsyncClient(timeout=None, follow_redirects=True)

        response = await client.send(
            client.build_request('GET', original_url, headers=default_headers),
            stream=True
        )

        # Headers relevantes para pasar al cliente
        # Incluimos headers necesarios para Range Requests (seek)
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
                # Eliminar content-length ya que el tamaño cambió
                pass_headers.pop('content-length', None)
                # Eliminar content-range si existe, ya que cambió el contenido
                pass_headers.pop('content-range', None)
                return (response.status_code, pass_headers, rewritten)
            except Exception as e:
                print(f"Error procesando M3U8: {e}")
                # Si falla el procesamiento, devolver el contenido original
                return (response.status_code, pass_headers, content)

        async def body_iterator():
            try:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return (response.status_code, pass_headers, body_iterator())

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
