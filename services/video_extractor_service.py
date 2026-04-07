"""
Video Extractor Service
=======================
Extrae URLs directas de video desde proveedores externos de anime/series.

Proveedores soportados:
  - Streamtape
  - Stape
  - Netu / StreamHide / VidHide / Fembed
  - StreamWish / FileLions / VidMoly / Vidhide variantes
  - Doodstream
  - Filemoon / Moonplayer
  - Mp4upload
  - Okru (ok.ru)
  - Uqload
  - Upstream
  - Voe.sx
  - Lulustream / Luluvdo

Uso:
    svc = VideoExtractorService()
    result = await svc.extract("https://streamtape.com/e/XYZ123")
    # result = {"url": "https://...", "provider": "streamtape", "type": "mp4"|"hls"}
"""

import re
import logging
import asyncio
from typing import Optional
from urllib.parse import urlparse, urljoin, unquote
import json
import base64

import httpx

logger = logging.getLogger("video-extractor")

# ─────────────────────────── helpers ────────────────────────────────────────

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

TIMEOUT = httpx.Timeout(20.0)


def _first(pattern: str, text: str, group: int = 1, flags: int = 0) -> Optional[str]:
    """Devuelve el primer match de un patrón o None."""
    m = re.search(pattern, text, flags)
    return m.group(group) if m else None


def _all(pattern: str, text: str, group: int = 1, flags: int = 0) -> list[str]:
    return re.findall(pattern, text, flags)


async def _fetch(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    headers = {**DEFAULT_HEADERS, **kwargs.pop("headers", {})}
    return await client.get(url, headers=headers, timeout=TIMEOUT, follow_redirects=True, **kwargs)


async def _post(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    headers = {**DEFAULT_HEADERS, **kwargs.pop("headers", {})}
    return await client.post(url, headers=headers, timeout=TIMEOUT, follow_redirects=True, **kwargs)


# ─────────────────────────── extractores individuales ───────────────────────

async def _extract_streamtape(client: httpx.AsyncClient, url: str) -> dict:
    """
    Streamtape: la URL directa está en <div id="robotlink"> (display:none).
    """
    r = await _fetch(client, url, headers={"Referer": "https://streamtape.com"})
    html = r.text

    match = _first(r'<div id="robotlink"[^>]*>(/streamtape\.com/get_video\?[^<]+)', html)
    if match:
        direct = f"https:{match}" if match.startswith("//") else f"https://{match.lstrip('/')}"
        return {"url": direct, "provider": "streamtape", "type": "mp4"}

    # Fallback: buscar get_video en cualquier parte del HTML
    match = _first(r"(https?://streamtape\.com/get_video\?[^\"' >]+)", html)
    if match:
        return {"url": match, "provider": "streamtape", "type": "mp4"}

    raise ValueError("Streamtape: no se encontró la URL de descarga")


async def _extract_stape(client: httpx.AsyncClient, url: str) -> dict:
    """Stape es una variante de Streamtape con dominio propio."""
    r = await _fetch(client, url, headers={"Referer": "https://stape.io"})
    html = r.text

    part1 = _first(r"innerHTML = '(/[^']+)'", html)
    part2 = _first(r"innerHTML \+ '([^']+)'", html)

    if part1 and part2:
        return {"url": f"https://stape.io{part1}{part2}", "provider": "stape", "type": "mp4"}

    # Alternativa con get_video
    match = _first(
        r"get_video\?id=([^&\"']+)&expires=([^&\"']+)&ip=([^&\"']+)&token=([^\"'&\s]+)",
        html
    )
    if match:
        # match es el primer grupo; necesitamos todos
        m = re.search(
            r"get_video\?id=([^&\"']+)&expires=([^&\"']+)&ip=([^&\"']+)&token=([^\"'&\s]+)",
            html
        )
        direct = (
            f"https://stape.io/get_video"
            f"?id={m.group(1)}&expires={m.group(2)}&ip={m.group(3)}&token={m.group(4)}"
        )
        return {"url": direct, "provider": "stape", "type": "mp4"}

    raise ValueError("Stape: no se encontró la URL de descarga")


async def _extract_fembed_like(
    client: httpx.AsyncClient,
    url: str,
    provider: str,
    api_domain: Optional[str] = None
) -> dict:
    """
    Extracteur genérico para proveedores con API estilo Fembed:
    POST a /api/source/{id} → JSON con array 'data' de fuentes.
    Usado por: Netu, StreamHide, VidHide, Fembed, StreamWish, FileLions, etc.
    """
    parsed = urlparse(url)
    domain = api_domain or f"{parsed.scheme}://{parsed.netloc}"
    video_id = parsed.path.rstrip("/").split("/")[-1]
    api_url = f"{domain}/api/source/{video_id}"

    r = await _post(
        client,
        api_url,
        data={"r": "", "d": parsed.netloc},
        headers={
            "Referer": url,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
        }
    )

    try:
        data = r.json()
    except Exception:
        raise ValueError(f"{provider}: respuesta API no es JSON — {r.text[:200]}")

    if not data.get("success"):
        raise ValueError(f"{provider}: API devolvió success=false — {data}")

    sources = data.get("data", [])
    if not sources:
        raise ValueError(f"{provider}: sin fuentes en la respuesta")

    # Seleccionar la mayor calidad disponible
    best = max(
        sources,
        key=lambda s: int(re.sub(r"\D", "", s.get("label", "0")) or 0)
    )
    file_url = best.get("file") or best.get("src") or ""
    if not file_url:
        raise ValueError(f"{provider}: fuente sin URL")

    stream_type = "hls" if ".m3u8" in file_url else "mp4"
    return {"url": file_url, "provider": provider, "type": stream_type, "sources": sources}


async def _extract_netu(client: httpx.AsyncClient, url: str) -> dict:
    """
    Netu/HQQ: busca el src m3u8 directamente en el HTML.
    El player videojs tiene la URL en: src: 'https://...m3u8'
    """
    r = await _fetch(client, url, headers={"Referer": "https://hqq.tv"})
    html = r.text

    # Buscar src con m3u8 en el HTML del player
    match = _first(r'''src\s*:\s*['"]([^'"]+\.m3u8[^'"]*)['"]''', html)
    if match:
        return {"url": match, "provider": "netu", "type": "hls"}

    # Fallback: intentar API Fembed-like
    try:
        return await _extract_fembed_like(client, url, "netu")
    except Exception:
        pass

    raise ValueError("Netu: no se encontró fuente de video")


async def _extract_streamhide(client: httpx.AsyncClient, url: str) -> dict:
    return await _extract_fembed_like(client, url, "streamhide")


async def _extract_vidhide(client: httpx.AsyncClient, url: str) -> dict:
    return await _extract_fembed_like(client, url, "vidhide")


async def _extract_streamwish(client: httpx.AsyncClient, url: str) -> dict:
    """
    StreamWish a veces embebe directamente la URL m3u8 en el JS,
    y otras veces usa la API Fembed-like.
    """
    r = await _fetch(client, url, headers={"Referer": "https://streamwish.com"})
    html = r.text

    # Intento 1: m3u8 directo en el JS (más común)
    for pattern in [
        r'file\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r"file\s*:\s*'(https?://[^']+\.m3u8[^']*)'",
        r'src\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r"jwplayer[^)]+\)\s*\.setup\([^}]+file\s*:\s*['\"]([^'\"]+)['\"]",
    ]:
        match = _first(pattern, html)
        if match:
            return {"url": match, "provider": "streamwish", "type": "hls"}

    # Intento 2: API Fembed-like
    try:
        return await _extract_fembed_like(client, url, "streamwish")
    except Exception:
        pass

    raise ValueError("StreamWish: no se encontró fuente de video")


async def _extract_filelions(client: httpx.AsyncClient, url: str) -> dict:
    return await _extract_fembed_like(client, url, "filelions")


async def _extract_vidmoly(client: httpx.AsyncClient, url: str) -> dict:
    return await _extract_fembed_like(client, url, "vidmoly")


async def _extract_doodstream(client: httpx.AsyncClient, url: str) -> dict:
    """
    Doodstream usa un token temporal que se construye combinando
    un path del JS con un hash aleatorio y la marca de tiempo.
    """
    # Normalizar URL a formato /e/ (embed)
    url = re.sub(r"/d/", "/e/", url)

    r = await _fetch(client, url, headers={"Referer": "https://dood.to"})
    html = r.text

    # Extraer el pass_md5 del JS
    pass_md5 = _first(r"\$\.get\s*\(\s*['\"](/pass_md5/[^'\"]+)['\"]", html)
    if not pass_md5:
        pass_md5 = _first(r"'/pass_md5/([^'\"]+)'", html)
        if pass_md5:
            pass_md5 = f"/pass_md5/{pass_md5}"

    if not pass_md5:
        raise ValueError("Doodstream: no se encontró pass_md5")

    parsed = urlparse(url)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"
    token_url = f"{base_domain}{pass_md5}"

    r2 = await _fetch(client, token_url, headers={"Referer": url})
    token_base = r2.text.strip()

    import time
    import random
    import string
    rand_str = "".join(random.choices(string.ascii_letters + string.digits, k=10))
    timestamp = int(time.time() * 1000)
    direct = f"{token_base}{rand_str}?token={rand_str}&expiry={timestamp}"

    return {"url": direct, "provider": "doodstream", "type": "mp4"}


async def _extract_filemoon(client: httpx.AsyncClient, url: str) -> dict:
    """
    Filemoon usa packed JS (p,a,c,k,e,d). Hay que desempaquetar
    o buscar el m3u8 directamente en el HTML.
    """
    r = await _fetch(client, url, headers={"Referer": "https://filemoon.sx"})
    html = r.text

    # Buscar m3u8 directo (a veces aparece sin obfuscación)
    match = _first(r'["\']?(https?://[^"\']+\.m3u8[^"\']*)["\']?', html)
    if match:
        return {"url": match, "provider": "filemoon", "type": "hls"}

    # Buscar en el bloque eval(function(p,a,c,k,e,d)
    packed = _first(r"(eval\(function\(p,a,c,k,e,d\).*?)</script>", html, flags=re.DOTALL)
    if packed:
        unpacked = _unpack_js(packed)
        match = _first(r'["\']?(https?://[^"\']+\.m3u8[^"\']*)["\']?', unpacked)
        if match:
            return {"url": match, "provider": "filemoon", "type": "hls"}

    raise ValueError("Filemoon: no se encontró fuente de video")


async def _extract_mp4upload(client: httpx.AsyncClient, url: str) -> dict:
    """Mp4upload tiene el src directo en el HTML del player."""
    r = await _fetch(client, url, headers={"Referer": "https://www.mp4upload.com"})
    html = r.text

    for pattern in [
        r'src\s*:\s*"(https?://[^"]+\.mp4[^"]*)"',
        r"src\s*:\s*'(https?://[^']+\.mp4[^']*)'",
        r'file\s*:\s*"(https?://[^"]+\.mp4[^"]*)"',
    ]:
        match = _first(pattern, html)
        if match:
            return {"url": match, "provider": "mp4upload", "type": "mp4"}

    raise ValueError("Mp4upload: no se encontró fuente de video")


async def _extract_okru(client: httpx.AsyncClient, url: str) -> dict:
    """
    Ok.ru (Odnoklassniki) expone las fuentes en un JSON embebido
    dentro del atributo data-options del player.
    """
    r = await _fetch(client, url, headers={"Referer": "https://ok.ru"})
    html = r.text

    # Extraer el JSON del atributo data-options
    raw = _first(r'data-options="([^"]+)"', html)
    if not raw:
        raise ValueError("Okru: no se encontró data-options")

    raw = raw.replace("&quot;", '"')
    try:
        opts = json.loads(raw)
    except Exception:
        raise ValueError("Okru: JSON inválido en data-options")

    # Las fuentes están anidadas en flashvars → metadata → videos
    flash_vars = opts.get("flashvars", {})
    metadata_str = flash_vars.get("metadata", "")
    if not metadata_str:
        raise ValueError("Okru: sin metadata en flashvars")

    try:
        metadata = json.loads(metadata_str)
    except Exception:
        raise ValueError("Okru: metadata no es JSON válido")

    videos = metadata.get("videos", [])
    if not videos:
        raise ValueError("Okru: sin videos en metadata")

    # Calidades típicas: mobile, lowest, low, sd, hd, full
    quality_order = ["full", "hd", "sd", "low", "lowest", "mobile"]
    best = None
    for q in quality_order:
        best = next((v for v in videos if v.get("name") == q), None)
        if best:
            break
    if not best:
        best = videos[-1]

    return {"url": best["url"], "provider": "okru", "type": "mp4"}


async def _extract_uqload(client: httpx.AsyncClient, url: str) -> dict:
    """Uqload tiene sources en array JS: sources: ["url"]"""
    r = await _fetch(client, url, headers={"Referer": "https://uqload.to"})
    html = r.text

    match = _first(r'sources\s*:\s*\["(https?://[^"]+)"', html)
    if match:
        stream_type = "hls" if ".m3u8" in match else "mp4"
        return {"url": match, "provider": "uqload", "type": stream_type}

    raise ValueError("Uqload: no se encontró fuente de video")


async def _extract_upstream(client: httpx.AsyncClient, url: str) -> dict:
    """Upstream (upstream.to) usa API similar a Fembed."""
    try:
        return await _extract_fembed_like(client, url, "upstream")
    except Exception:
        pass

    r = await _fetch(client, url, headers={"Referer": "https://upstream.to"})
    html = r.text
    match = _first(r'file\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"', html)
    if match:
        return {"url": match, "provider": "upstream", "type": "hls"}

    raise ValueError("Upstream: no se encontró fuente de video")


async def _extract_voe(client: httpx.AsyncClient, url: str) -> dict:
    """
    Voe.sx guarda la URL en una variable JS:
    'hls': 'https://...'  o  var sources = {...}
    """
    r = await _fetch(client, url, headers={"Referer": "https://voe.sx"})
    html = r.text

    for pattern in [
        r"'hls'\s*:\s*'(https?://[^']+)'",
        r'"hls"\s*:\s*"(https?://[^"]+)"',
        r"hls\s*=\s*['\"]([^'\"]+\.m3u8[^'\"]*)['\"]",
    ]:
        match = _first(pattern, html)
        if match:
            return {"url": match, "provider": "voe", "type": "hls"}

    # Fallback: mp4
    match = _first(r"'mp4'\s*:\s*'(https?://[^']+)'", html)
    if match:
        return {"url": match, "provider": "voe", "type": "mp4"}

    raise ValueError("Voe: no se encontró fuente de video")


async def _extract_lulustream(client: httpx.AsyncClient, url: str) -> dict:
    """Lulustream / Luluvdo — API POST similar a Fembed."""
    try:
        return await _extract_fembed_like(client, url, "lulustream")
    except Exception:
        pass

    r = await _fetch(client, url, headers={"Referer": url})
    html = r.text
    match = _first(r'file\s*:\s*["\']?(https?://[^"\']+\.m3u8[^"\']*)["\']?', html)
    if match:
        return {"url": match, "provider": "lulustream", "type": "hls"}

    raise ValueError("Lulustream: no se encontró fuente de video")


# ─────────────────────────── desempaquetador JS básico ─────────────────────

def _unpack_js(packed: str) -> str:
    """
    Desempaquetador mínimo para eval(function(p,a,c,k,e,d){...}).
    No cubre todos los casos pero sí los más comunes en proveedores de video.
    """
    try:
        # Extraer los parámetros: p, a, c, k
        m = re.search(
            r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\('(.+?)',(\d+),(\d+),'(.+?)'\.split",
            packed,
            re.DOTALL
        )
        if not m:
            return packed

        p_val = m.group(1)
        a_val = int(m.group(2))
        # c_val = int(m.group(3))  # no necesario para el decode básico
        k_val = m.group(4).split("|")

        def base_decode(n: int, base: int) -> str:
            chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
            result = ""
            while n > 0:
                result = chars[n % base] + result
                n //= base
            return result or "0"

        def replace_match(mo):
            idx = int(mo.group(0), a_val) if a_val != 10 else int(mo.group(0))
            word = k_val[idx] if idx < len(k_val) else ""
            return word if word else mo.group(0)

        result = re.sub(r"\b\w+\b", replace_match, p_val)
        return result
    except Exception:
        return packed


# ─────────────────────────── router principal ────────────────────────────────

def _detect_provider(url: str) -> Optional[str]:
    """Detecta el proveedor a partir del dominio de la URL."""
    domain = urlparse(url).netloc.lower()

    rules = [
        (["streamtape.com", "streamtape.to", "streamtape.net"], "streamtape"),
        (["stape.io", "stape.to"], "stape"),
        (["netu.ac", "netu.io", "hqq.tv", "hqq.ac"], "netu"),
        (["streamhide.to", "streamhide.com", "guccihide.com", "ridehide.com"], "streamhide"),
        (["vidhide.com", "vidhide.to", "vid-guard.com"], "vidhide"),
        (["streamwish.com", "streamwish.to", "awish.one", "strwish.com", "sfastwish.com"], "streamwish"),
        (["filelions.com", "filelions.to", "filelions.live", "alions.pro"], "filelions"),
        (["vidmoly.to", "vidmoly.com"], "vidmoly"),
        (["dood.to", "dood.la", "doodstream.com", "dooood.com", "ds2play.com", "doods.pro"], "doodstream"),
        (["filemoon.sx", "filemoon.to", "filemoon.in", "moonplayer.to"], "filemoon"),
        (["mp4upload.com", "www.mp4upload.com"], "mp4upload"),
        (["ok.ru", "odnoklassniki.ru"], "okru"),
        (["uqload.to", "uqload.co", "uqload.com"], "uqload"),
        (["upstream.to"], "upstream"),
        (["voe.sx", "voe.to"], "voe"),
        (["lulustream.com", "luluvdo.com", "bestlulustream.com"], "lulustream"),
    ]

    for domains, provider in rules:
        if any(d in domain for d in domains):
            return provider

    return None


_EXTRACTORS = {
    "streamtape":  _extract_streamtape,
    "stape":       _extract_stape,
    "netu":        _extract_netu,
    "streamhide":  _extract_streamhide,
    "vidhide":     _extract_vidhide,
    "streamwish":  _extract_streamwish,
    "filelions":   _extract_filelions,
    "vidmoly":     _extract_vidmoly,
    "doodstream":  _extract_doodstream,
    "filemoon":    _extract_filemoon,
    "mp4upload":   _extract_mp4upload,
    "okru":        _extract_okru,
    "uqload":      _extract_uqload,
    "upstream":    _extract_upstream,
    "voe":         _extract_voe,
    "lulustream":  _extract_lulustream,
}


# ─────────────────────────── servicio principal ──────────────────────────────

class VideoExtractorService:
    """
    Servicio principal de extracción de URLs de video.

    Uso básico:
        svc = VideoExtractorService()
        result = await svc.extract("https://streamtape.com/e/abc123")

    Resultado:
        {
            "url": "https://cdn.streamtape.com/...",
            "provider": "streamtape",
            "type": "mp4",          # o "hls"
            "sources": [...]         # opcional, lista completa de calidades
        }
    """

    def __init__(self, timeout: float = 20.0):
        self._timeout = timeout

    async def extract(self, url: str) -> dict:
        """
        Extrae la URL directa de reproducción a partir de una URL de embed.

        Args:
            url: URL de la página del proveedor (embed o página de video).

        Returns:
            dict con keys: url, provider, type (mp4|hls), sources (opcional)

        Raises:
            ValueError: si el proveedor no está soportado o la extracción falla.
        """
        provider = _detect_provider(url)
        if not provider:
            raise ValueError(f"Proveedor no soportado para URL: {url}")

        extractor = _EXTRACTORS.get(provider)
        if not extractor:
            raise ValueError(f"No hay extractor implementado para: {provider}")

        logger.info(f"[VideoExtractor] Extrayendo {provider}: {url[:80]}...")

        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers=DEFAULT_HEADERS,
        ) as client:
            try:
                result = await extractor(client, url)
                logger.info(
                    f"[VideoExtractor] OK {provider} → {result['type']}: "
                    f"{result['url'][:80]}..."
                )
                return result
            except Exception as e:
                logger.error(f"[VideoExtractor] ERROR {provider}: {e}")
                raise

    async def extract_multi(self, urls: list[str]) -> list[dict]:
        """
        Extrae múltiples URLs en paralelo.

        Returns:
            Lista de dicts. Si una URL falla, incluye {"error": "...", "url": "..."}
        """
        async def _safe(url: str) -> dict:
            try:
                return await self.extract(url)
            except Exception as e:
                return {"url": url, "error": str(e), "provider": None, "type": None}

        return await asyncio.gather(*[_safe(u) for u in urls])

    def supported_providers(self) -> list[str]:
        """Devuelve la lista de proveedores soportados."""
        return list(_EXTRACTORS.keys())

    def detect_provider(self, url: str) -> Optional[str]:
        """Devuelve el nombre del proveedor detectado o None."""
        return _detect_provider(url)