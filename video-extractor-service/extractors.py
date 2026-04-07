"""
Extractores de video usando Playwright (navegador headless).
Para sitios que requieren ejecución de JavaScript.
"""

import re
import logging
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Page, Browser

logger = logging.getLogger("playwright-extractor")

DEFAULT_TIMEOUT = 15000  # 15 segundos


# ─────────────────────────── helpers ────────────────────────────────────────

def _first(pattern: str, text: str, group: int = 1, flags: int = 0) -> Optional[str]:
    m = re.search(pattern, text, flags)
    return m.group(group) if m else None


# ─────────────────────────── Playwright extractors ──────────────────────────

async def _extract_with_playwright(
    url: str,
    provider: str,
    wait_ms: int = 15000,
    extra_wait_for: Optional[str] = None,
) -> dict:
    """
    Extrae URL de video usando Playwright.
    Carga la página, espera a que el JS renderice el contenido,
    y busca URLs de video en el DOM.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        try:
            # Interceptar requests de red para capturar URLs de video
            video_urls = []

            def _on_response(response):
                resp_url = response.url
                if any(ext in resp_url for ext in [".m3u8", ".mp4", ".webm", ".ts"]):
                    video_urls.append(resp_url)

            page.on("response", _on_response)

            # Navegar a la página
            logger.info(f"[{provider}] Cargando {url}")
            await page.goto(url, wait_until="networkidle", timeout=DEFAULT_TIMEOUT)

            # Esperar a que el contenido se renderice
            if extra_wait_for:
                try:
                    await page.wait_for_selector(extra_wait_for, timeout=wait_ms)
                except Exception:
                    pass  # Timeout, continuar igual

            await page.wait_for_timeout(wait_ms)

            # Estrategia 0: Buscar iframes con el player
            frames = page.frames
            for frame in frames:
                try:
                    frame_url = frame.url
                    if "embed" in frame_url or "player" in frame_url:
                        logger.info(f"[{provider}] Procesando frame: {frame_url[:60]}")
                        # Intentar extraer del frame
                        frame_content = await frame.content()
                        for pattern in [
                            r'''['"]([^'"]+\.m3u8[^'"]*)['"]''',
                            r'''['"]([^'"]+\.mp4[^'"]*)['"]''',
                        ]:
                            matches = re.findall(pattern, frame_content)
                            for m in matches:
                                if any(ext in m for ext in [".m3u8", ".mp4"]) and not m.startswith("data:"):
                                    logger.info(f"[{provider}] URL encontrada en iframe: {m[:80]}")
                                    return {
                                        "url": m,
                                        "provider": provider,
                                        "type": "hls" if ".m3u8" in m else "mp4",
                                    }
                except Exception:
                    continue

            # Estrategia 1: Buscar URLs capturadas en respuestas de red
            if video_urls:
                best_url = _select_best_video_url(video_urls)
                logger.info(f"[{provider}] URL encontrada via network: {best_url[:80]}")
                return {
                    "url": best_url,
                    "provider": provider,
                    "type": "hls" if ".m3u8" in best_url else "mp4",
                }

            # Estrategia 2: Buscar en el DOM
            page_content = await page.content()

            for pattern in [
                r'''['"]([^'"]+\.m3u8[^'"]*)['"]''',
                r'''['"]([^'"]+\.mp4[^'"]*)['"]''',
                r'''src\s*[:=]\s*['"]([^'"]+)['"]''',
                r'''file\s*[:=]\s*['"]([^'"]+)['"]''',
                r'''source\s+src\s*=\s*['"]([^'"]+)['"]''',
                r'''iframe[^>]+src=["']([^"']+)["']''',
            ]:
                matches = re.findall(pattern, page_content)
                video_matches = [
                    m for m in matches
                    if any(ext in m for ext in [".m3u8", ".mp4", ".webm", "embed", "player"])
                    and not m.startswith("data:")
                ]
                if video_matches:
                    best_url = video_matches[0]
                    logger.info(f"[{provider}] URL encontrada en DOM: {best_url[:80]}")
                    return {
                        "url": best_url,
                        "provider": provider,
                        "type": "hls" if ".m3u8" in best_url else "mp4",
                    }

            # Estrategia 3: Ejecutar JS para buscar el video
            video_url = await page.evaluate(r"""
                () => {
                    // Buscar en elementos <video> y <source>
                    const video = document.querySelector('video');
                    if (video && video.src) return video.src;
                    const sources = document.querySelectorAll('source');
                    for (const s of sources) {
                        if (s.src && (s.src.includes('.m3u8') || s.src.includes('.mp4'))) {
                            return s.src;
                        }
                    }

                    // Buscar en iframes
                    const iframes = document.querySelectorAll('iframe');
                    for (const iframe of iframes) {
                        if (iframe.src && (iframe.src.includes('.m3u8') || iframe.src.includes('.mp4'))) {
                            return iframe.src;
                        }
                    }

                    // Buscar en variables globales comunes
                    const vars = ['file', 'source', 'videoUrl', 'src', 'url', 'video_src', 'stream_url'];
                    for (const v of vars) {
                        if (window[v] && typeof window[v] === 'string' &&
                            (window[v].includes('.m3u8') || window[v].includes('.mp4'))) {
                            return window[v];
                        }
                    }

                    // Buscar en configuraciones de players (jwplayer, plyr, etc)
                    const players = ['jwplayer', 'plyr', 'videojs', 'flowplayer'];
                    for (const p of players) {
                        if (window[p] && window[p].config) {
                            const config = window[p].config;
                            if (config.file) return config.file;
                            if (config.sources) {
                                for (const s of config.sources) {
                                    if (s.src) return s.src;
                                }
                            }
                        }
                    }

                    // Buscar en scripts inline
                    const scripts = document.querySelectorAll('script');
                    for (const s of scripts) {
                        const text = s.textContent || '';
                        const m3u8 = text.match(/https?:\/\/[^'"\\s]+\.m3u8[^'"\\s]*/);
                        if (m3u8) return m3u8[0];
                        const mp4 = text.match(/https?:\/\/[^'"\\s]+\.mp4[^'"\\s]*/);
                        if (mp4) return mp4[0];
                    }

                    return null;
                }
            """)

            if video_url:
                logger.info(f"[{provider}] URL encontrada via JS: {video_url[:80]}")
                return {
                    "url": video_url,
                    "provider": provider,
                    "type": "hls" if ".m3u8" in video_url else "mp4",
                }

            raise ValueError(f"{provider}: no se encontró fuente de video en la página")

        finally:
            await browser.close()


def _select_best_video_url(urls: list[str]) -> str:
    """Selecciona la mejor URL de video de una lista."""
    # Preferir m3u8 sobre mp4 sobre ts
    m3u8_urls = [u for u in urls if ".m3u8" in u]
    mp4_urls = [u for u in urls if ".mp4" in u]

    if m3u8_urls:
        return m3u8_urls[0]
    if mp4_urls:
        return mp4_urls[0]
    return urls[0]


# ─────────────────────────── extractores específicos ────────────────────────

async def extract_streamwish(url: str) -> dict:
    """Extrae video de StreamWish (SPA, requiere JS)."""
    return await _extract_with_playwright(
        url,
        provider="streamwish",
        wait_ms=15000,
    )


async def extract_filemoon(url: str) -> dict:
    """Extrae video de Filemoon (packed JS)."""
    return await _extract_with_playwright(
        url,
        provider="filemoon",
        wait_ms=6000,
    )


async def extract_generic(url: str) -> dict:
    """Extractor genérico con Playwright para cualquier sitio con JS."""
    parsed = urlparse(url)
    provider = parsed.netloc.split(".")[0]
    return await _extract_with_playwright(
        url,
        provider=provider,
        wait_ms=6000,
    )


# ─────────────────────────── router ─────────────────────────────────────────

EXTRACTORS = {
    "streamwish": extract_streamwish,
    "filemoon": extract_filemoon,
}

# Providers que requieren Playwright (no se pueden extraer con HTTP)
PLAYWRIGHT_PROVIDERS = {"streamwish", "filemoon"}


def detect_provider(url: str) -> Optional[str]:
    """Detecta el proveedor a partir de la URL."""
    domain = urlparse(url).netloc.lower()

    rules = [
        (["streamwish.com", "streamwish.to", "awish.one", "strwish.com", "sfastwish.com"], "streamwish"),
        (["filemoon.sx", "filemoon.to", "filemoon.in", "moonplayer.to"], "filemoon"),
    ]

    for domains, provider in rules:
        if any(d in domain for d in domains):
            return provider

    return None


def supported_providers() -> list[str]:
    """Lista de providers soportados por Playwright."""
    return list(EXTRACTORS.keys())


async def extract(url: str) -> dict:
    """
    Extrae video de una URL usando Playwright.
    Detecta el provider automáticamente.
    """
    provider = detect_provider(url)
    if not provider:
        # Fallback: intentar extractor genérico
        return await extract_generic(url)

    extractor = EXTRACTORS.get(provider)
    if not extractor:
        return await extract_generic(url)

    return await extractor(url)
