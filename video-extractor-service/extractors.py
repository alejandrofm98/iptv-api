"""
Extractores de video usando Playwright (navegador headless).
Para sitios que requieren ejecución de JavaScript.
"""

import re
import logging
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import async_playwright

logger = logging.getLogger("playwright-extractor")

DEFAULT_TIMEOUT = 15000  # 15 segundos

# Patrones de URLs falsas (ads, tracking, etc)
# NOTA: hgplaycdn.com fue eliminado — es el CDN real del player de StreamWish
FAKE_URL_PATTERNS = [
    "vast.", "vpaid", "/ad/", "-ad-", "ads.", "advertising",
    "player/jw", "jwplayer.", "vast.js", "tracking.",
    "ssp.yahoo", "doubleclick", "googlesyndication",
    "playnixes.com/player", "rtmark.net",
    "tiktokcdn.com/ad", "medixiru.com",
    "ad-site", "huntrexus.com",
]

# Dominios de players legítimos que nunca deben filtrarse aunque parezcan CDN de ads
LEGITIMATE_PLAYER_DOMAINS = {
    "hgplaycdn.com",  # Player real de StreamWish
}

# Headers requeridos por provider
PROVIDER_HEADERS = {
    "streamwish": {
        "Origin": "https://streamwish.to",
        "Referer": "https://streamwish.to/",
    },
    "filemoon": {
        "Origin": "https://filemoon.sx",
        "Referer": "https://filemoon.sx/",
    },
}

# Dominios conocidos de StreamWish (para detectar redirects)
STREAMWISH_DOMAINS = {
    "streamwish.com", "streamwish.to", "awish.one",
    "strwish.com", "sfastwish.com", "niramirus.com",
}


def _is_fake_url(url: str) -> bool:
    """Detecta si una URL es de advertising/fake y no es un stream real."""
    url_lower = url.lower()
    # Si el dominio es un player legítimo, nunca es fake
    try:
        domain = urlparse(url).netloc.lower()
        if any(d in domain for d in LEGITIMATE_PLAYER_DOMAINS):
            return False
    except Exception:
        pass
    return any(pattern in url_lower for pattern in FAKE_URL_PATTERNS)


def _unpack_js(packed: str) -> str:
    """
    Desempaquetador para eval(function(p,a,c,k,e,d){...}).
    Cubre: k como .split('|') o array literal.
    Necesario porque StreamWish packed JS no contiene /stream/ literalmente.
    """
    try:
        # Variante 1: k como string con .split('|')
        m = re.search(
            r"""eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*[df]\s*\)"""
            r"""\s*\{.+?\}\s*\(\s*['"](.+?)['"]\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*"""
            r"""['"](.+?)['"]\s*\.split\s*\(\s*['"]\|['"]\s*\)""",
            packed,
            re.DOTALL,
        )

        # Variante 2: k como array literal
        if not m:
            m = re.search(
                r"""eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*[df]\s*\)"""
                r"""\s*\{.+?\}\s*\(\s*['"](.+?)['"]\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*"""
                r"""\[(.+?)\]\s*[,\)]""",
                packed,
                re.DOTALL,
            )
            if m:
                p_val, a_str, k_raw = m.group(1), m.group(2), m.group(4)
                a_val = int(a_str)
                k_val = re.findall(r"""['"]([^'"]*?)['"]""", k_raw)
            else:
                return packed
        else:
            p_val = m.group(1)
            a_val = int(m.group(2))
            k_val = m.group(4).split("|")

        def replace_match(mo, k=k_val, a=a_val):
            try:
                idx = int(mo.group(0), a) if a != 10 else int(mo.group(0))
                word = k[idx] if idx < len(k) else ""
                return word if word else mo.group(0)
            except Exception:
                return mo.group(0)

        return re.sub(r"\b\w+\b", replace_match, p_val)

    except Exception:
        return packed


def _select_best_video_url(urls: list[str]) -> str:
    """Selecciona la mejor URL de video de una lista, descartando falsas."""
    real_urls = [u for u in urls if not _is_fake_url(u)]
    m3u8_urls = [u for u in real_urls if ".m3u8" in u]
    mp4_urls = [u for u in real_urls if ".mp4" in u]

    if m3u8_urls:
        # Priorizar master.m3u8 sobre index-*.m3u8 (variantes de calidad)
        master_urls = [u for u in m3u8_urls if "master.m3u8" in u]
        if master_urls:
            return master_urls[0]
        return m3u8_urls[0]
    if mp4_urls:
        return mp4_urls[0]
    if real_urls:
        return real_urls[0]
    return urls[0] if urls else ""


async def _try_click_play(page) -> bool:
    """
    Intenta hacer click en el botón de play usando múltiples estrategias.
    Devuelve True si se hizo click en algo.
    """
    play_selectors = [
        # JWPlayer
        ".jw-icon-display",
        ".jw-display-icon-display",
        ".jw-display",
        # Video.js
        ".vjs-big-play-button",
        # Plyr
        ".plyr__control--overlaid",
        # HTML5 genérico
        "video",
        # Botones genéricos con aria/title
        "[aria-label='Play']",
        "[aria-label='play']",
        "[aria-label='Reproducir']",
        "[title='Play']",
        # Overlays de play frecuentes en embeds
        ".play-button",
        ".playButton",
        ".play_button",
        "#play-button",
        ".btn-play",
        ".video-overlay",
        ".player-overlay",
    ]

    for selector in play_selectors:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=1000):
                await el.click(timeout=3000)
                logger.info(f"Click en play con selector: {selector}")
                return True
        except Exception:
            continue

    # Fallback: click en el centro de la página
    try:
        await page.mouse.click(960, 540)
        logger.info("Click en centro de pantalla como fallback")
        return True
    except Exception:
        pass

    return False


async def _try_click_play_in_frames(page, provider: str) -> None:
    """
    Intenta hacer click en el botón de play dentro de iframes legítimos
    (ej: hgplaycdn.com) para forzar la carga del stream.
    """
    for frame in page.frames:
        try:
            frame_url = frame.url
            if not frame_url or frame_url == "about:blank":
                continue
            frame_domain = urlparse(frame_url).netloc.lower()
            if not any(d in frame_domain for d in LEGITIMATE_PLAYER_DOMAINS):
                continue

            logger.info(f"[{provider}] Click en play dentro de frame legítimo: {frame_url[:60]}")
            play_selectors = [
                ".jw-icon-display",
                ".jw-display-icon-display",
                ".jw-display",
                ".vjs-big-play-button",
                ".plyr__control--overlaid",
                "video",
                "[aria-label='Play']",
                "[aria-label='play']",
                ".play-button",
                ".playButton",
            ]
            clicked = False
            for sel in play_selectors:
                try:
                    el = frame.locator(sel).first
                    if await el.is_visible(timeout=800):
                        await el.click(timeout=2000)
                        logger.info(f"[{provider}] Click en frame con selector: {sel}")
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                # Fallback: forzar play via JS dentro del frame
                try:
                    await frame.evaluate("() => { const v = document.querySelector('video'); if(v) v.play(); }")
                    logger.info(f"[{provider}] video.play() ejecutado en frame")
                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"[{provider}] Error haciendo click en frame: {e}")
            continue


async def _extract_from_frames(page, provider: str) -> Optional[dict]:
    """
    Busca URLs de video dentro de iframes, incluyendo players CDN legítimos
    como hgplaycdn.com (player real de StreamWish).
    """
    for frame in page.frames:
        try:
            frame_url = frame.url
            if not frame_url or frame_url == "about:blank":
                continue

            frame_domain = urlparse(frame_url).netloc.lower()
            is_legitimate = any(d in frame_domain for d in LEGITIMATE_PLAYER_DOMAINS)

            # Saltar iframes fake EXCEPTO si son players legítimos
            if _is_fake_url(frame_url) and not is_legitimate:
                logger.info(f"[{provider}] Saltando iframe fake: {frame_url[:80]}")
                continue

            logger.info(f"[{provider}] Inspeccionando frame: {frame_url[:80]}")

            # Intentar extracción JS dentro del frame primero
            try:
                video_url = await frame.evaluate(r"""
                    () => {
                        const fakePatterns = [
                            'vast.', 'vpaid', '/ad/', '-ad-', 'ads.', 'advertising',
                            'player/jw', 'jwplayer.', 'vast.js', 'tracking.',
                            'ssp.yahoo', 'doubleclick', 'googlesyndication',
                            'playnixes.com/player', 'rtmark.net', 'tiktokcdn.com/ad',
                            'medixiru.com', 'ad-site', 'huntrexus.com',
                        ];
                        function isFake(u) {
                            if (!u) return true;
                            const lower = u.toLowerCase();
                            return fakePatterns.some(p => lower.includes(p));
                        }
                        function isVideo(u) {
                            return u && typeof u === 'string'
                                && (u.includes('.m3u8') || u.includes('.mp4'))
                                && !isFake(u);
                        }

                        // <video> y <source>
                        const video = document.querySelector('video');
                        if (video && isVideo(video.src)) return video.src;
                        for (const s of document.querySelectorAll('source')) {
                            if (isVideo(s.src)) return s.src;
                        }

                        // jwplayer runtime
                        if (window.jwplayer) {
                            try {
                                const jw = window.jwplayer();
                                if (jw && jw.getPlaylist) {
                                    const pl = jw.getPlaylist();
                                    if (pl && pl[0]) {
                                        const src = pl[0].file || pl[0].sources?.[0]?.file;
                                        if (isVideo(src)) return src;
                                    }
                                }
                            } catch(e) {}
                        }

                        // Scripts inline
                        for (const s of document.querySelectorAll('script')) {
                            const text = s.textContent || '';

                            // StreamWish: URLs con /stream/ son el video real
                            const streamUrl = text.match(/https?:\/\/[^"'\s\\]+\/stream\/[^"'\s\\]+\/master\.m3u8[^"'\s\\]*/);
                            if (streamUrl && !isFake(streamUrl[0])) return streamUrl[0];

                            // jwplayer setup
                            const jwSetup = text.match(/jwplayer\s*\([^)]*\)\s*\.setup\s*\(\s*(\{[\s\S]*?\})\s*\)/);
                            if (jwSetup) {
                                const fileMatch = jwSetup[1].match(/["']?file["']?\s*:\s*["']([^"']+\.m3u8[^"']*)["']/);
                                if (fileMatch && !isFake(fileMatch[1])) return fileMatch[1];
                            }

                            // sources:[{file:"..."}]
                            const srcMatch = text.match(/sources\s*:\s*\[\s*\{[^}]*?file\s*:\s*["']([^"']+\.m3u8[^"']*)["']/);
                            if (srcMatch && !isFake(srcMatch[1])) return srcMatch[1];

                            // master.m3u8
                            const masterM3u8 = text.match(/https?:\/\/[^\s"'\\]+\/master\.m3u8[^\s"'\\]*/);
                            if (masterM3u8 && !isFake(masterM3u8[0])) return masterM3u8[0];

                            const m3u8 = text.match(/https?:\/\/[^\s"'\\]+\.m3u8[^\s"'\\]*/);
                            if (m3u8 && !isFake(m3u8[0])) return m3u8[0];

                            const mp4 = text.match(/https?:\/\/[^\s"'\\]+\.mp4[^\s"'\\]*/);
                            if (mp4 && !isFake(mp4[0])) return mp4[0];
                        }

                        return null;
                    }
                """)
                if video_url and not _is_fake_url(video_url):
                    logger.info(f"[{provider}] URL encontrada en frame via JS: {video_url[:80]}")
                    return {
                        "url": video_url,
                        "provider": provider,
                        "type": "hls" if ".m3u8" in video_url else "mp4",
                    }
            except Exception as e:
                logger.debug(f"[{provider}] JS en frame falló: {e}")

            # Fallback: regex sobre el HTML del frame
            content = await frame.content()
            patterns = [
                r'sources\s*:\s*\[\s*\{[^}]*?file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'"(https?://[^"]+/master\.m3u8[^"]*)"',
                r"'(https?://[^']+/master\.m3u8[^']*)'",
                r'"(https?://[^"]+\.m3u8[^"]*)"',
                r"'(https?://[^']+\.m3u8[^']*)'",
                r'"(https?://[^"]+\.mp4[^"]*)"',
            ]
            for pattern in patterns:
                for m in re.findall(pattern, content):
                    if (
                        any(ext in m for ext in [".m3u8", ".mp4"])
                        and not m.startswith("data:")
                        and not _is_fake_url(m)
                    ):
                        logger.info(f"[{provider}] URL encontrada en frame via regex: {m[:80]}")
                        return {
                            "url": m,
                            "provider": provider,
                            "type": "hls" if ".m3u8" in m else "mp4",
                        }

        except Exception as e:
            logger.debug(f"[{provider}] Error inspeccionando frame: {e}")
            continue

    return None


async def _extract_from_page(page, provider: str, label: str = "") -> Optional[dict]:
    """
    Extrae URL de video del DOM/JS.
    Orden: packed JS unpack → jwplayer() runtime → regex HTML crudo.
    """
    tag = f"[{provider}][{label}]" if label else f"[{provider}]"
    base_url = page.url  # dominio real tras redirect

    # ── 1. Desempaquetar packed JS y buscar /stream/ pattern
    #    Esto funciona AUNQUE bot detection bloquee jwplayer() runtime
    try:
        html = await page.content()
        for m_eval in re.finditer(r'eval\s*\(function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k', html):
            start = m_eval.start()
            packed = html[start:]
            unpacked = _unpack_js(packed)
            if unpacked != packed:
                stream_match = re.search(
                    r'/stream/[^\s"\'\\<>]+/master\.m3u8[^\s"\'\\<>]*', unpacked
                )
                if stream_match:
                    path = stream_match.group(0)
                    if path.startswith("http"):
                        full_url = path
                    else:
                        parsed_base = urlparse(base_url)
                        full_url = f"{parsed_base.scheme}://{parsed_base.netloc}{path}"
                    if not _is_fake_url(full_url):
                        logger.info(f"{tag} URL desde packed JS: {full_url[:80]}")
                        return {"url": full_url, "provider": provider, "type": "hls"}
    except Exception as e:
        logger.debug(f"{tag} packed JS unpack falló: {e}")

    # ── 2. JS evaluate (jwplayer runtime + DOM search)
    try:
        video_url = await page.evaluate(r"""
            () => {
                const fakePatterns = [
                    'vast.', 'vpaid', '/ad/', '-ad-', 'ads.', 'advertising',
                    'player/jw', 'jwplayer.', 'vast.js', 'tracking.',
                    'ssp.yahoo', 'doubleclick', 'googlesyndication',
                    'playnixes.com/player', 'rtmark.net', 'tiktokcdn.com/ad',
                    'medixiru.com', 'ad-site', 'huntrexus.com',
                ];

                function isFake(u) {
                    if (!u) return true;
                    const lower = u.toLowerCase();
                    return fakePatterns.some(p => lower.includes(p));
                }

                function isVideo(u) {
                    return u && typeof u === 'string'
                        && (u.includes('.m3u8') || u.includes('.mp4'))
                        && !isFake(u);
                }

                // <video> y <source>
                const video = document.querySelector('video');
                if (video && isVideo(video.src)) return video.src;
                for (const s of document.querySelectorAll('source')) {
                    if (isVideo(s.src)) return s.src;
                }

                // jwplayer() runtime
                if (typeof window.jwplayer === 'function') {
                    try {
                        var pl = window.jwplayer().getPlaylist();
                        if (pl && pl[0]) {
                            var f = pl[0].file || (pl[0].sources && pl[0].sources[0] && pl[0].sources[0].file);
                            if (f && typeof f === 'string' && (f.includes('.m3u8') || f.includes('/stream/'))) return f;
                        }
                    } catch(e) {}
                }

                // Scripts inline (packed JS ya evaluados por browser)
                for (const s of document.querySelectorAll('script')) {
                    const text = s.textContent || '';

                    const streamUrl = text.match(/\/stream\/[^\s"'\\<>]+\/master\.m3u8[^\s"'\\<>]*/);
                    if (streamUrl) return streamUrl[0];

                    const masterM3u8 = text.match(/https?:\/\/[^\s"'\\]+\/master\.m3u8[^\s"'\\]*/);
                    if (masterM3u8 && !isFake(masterM3u8[0])) return masterM3u8[0];

                    const m3u8 = text.match(/https?:\/\/[^\s"'\\]+\.m3u8[^\s"'\\]*/);
                    if (m3u8 && !isFake(m3u8[0])) return m3u8[0];

                    const mp4 = text.match(/https?:\/\/[^\s"'\\]+\.mp4[^\s"'\\]*/);
                    if (mp4 && !isFake(mp4[0])) return mp4[0];
                }

                return null;
            }
        """)

        if video_url and not _is_fake_url(video_url):
            if not video_url.startswith("http") and (".m3u8" in video_url or "/stream/" in video_url):
                parsed_base = urlparse(base_url)
                video_url = f"{parsed_base.scheme}://{parsed_base.netloc}{video_url}"
            logger.info(f"{tag} URL encontrada via JS: {video_url[:80]}")
            return {"url": video_url, "provider": provider, "type": "hls" if ".m3u8" in video_url else "mp4"}

    except Exception as e:
        logger.debug(f"{tag} JS evaluate falló: {e}")

    # ── 3. Regex sobre HTML crudo (último recurso)
    try:
        html = await page.content()
        patterns = [
            r'/stream/[^\s"\'\\<>]+/master\.m3u8[^\s"\'\\<>]*',
            r'sources\s*:\s*\[\s*\{[^}]*?file\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
            r"sources\s*:\s*\[\s*\{[^}]*?file\s*:\s*'(https?://[^']+\.m3u8[^']*)'",
            r'file\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
            r"file\s*:\s*'(https?://[^']+\.m3u8[^']*)'",
            r'"(https?://[^"]+/master\.m3u8[^"]*)"',
            r"'(https?://[^']+/master\.m3u8[^']*)'",
            r'"(https?://[^"]+\.m3u8[^"]*)"',
            r"'(https?://[^']+\.m3u8[^']*)'",
            r'file\s*:\s*"(https?://[^"]+\.mp4[^"]*)"',
        ]
        for pattern in patterns:
            m = re.search(pattern, html)
            if m:
                url_found = m.group(1) if m.lastindex else m.group(0)
                if not _is_fake_url(url_found):
                    if not url_found.startswith("http") and ("/stream/" in url_found or ".m3u8" in url_found):
                        parsed_base = urlparse(base_url)
                        url_found = f"{parsed_base.scheme}://{parsed_base.netloc}{url_found}"
                    logger.info(f"{tag} URL encontrada via regex HTML: {url_found[:80]}")
                    return {"url": url_found, "provider": provider, "type": "hls" if ".m3u8" in url_found else "mp4"}
    except Exception as e:
        logger.debug(f"{tag} regex HTML falló: {e}")

    return None


async def _extract_with_playwright(
    url: str,
    provider: str,
    wait_ms: int = 8000,
    extra_wait_for: Optional[str] = None,
) -> dict:
    """
    Extrae URL de video usando Playwright.
    Separa URLs de video real de URLs de anuncios (VAST, tracking).
    Prioriza master.m3u8 sobre variantes de calidad.
    Entra dentro de iframes de players legítimos (hgplaycdn.com, etc).
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

        # Headers correctos según el provider (dominio real tras redirect)
        provider_headers = PROVIDER_HEADERS.get(provider, {})
        extra_http_headers = {}
        if provider_headers:
            extra_http_headers = dict(provider_headers)

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            extra_http_headers=extra_http_headers,
        )
        page = await context.new_page()

        try:
            # URLs de video real separadas de ads
            master_urls: list[str] = []  # master.m3u8 — prioridad máxima
            segment_urls: list[str] = []  # index-*.m3u8, *.ts, *.mp4
            all_captured: list[str] = []  # todo lo capturado (debug)

            def _on_response(response):
                resp_url = response.url
                if not any(ext in resp_url for ext in [".m3u8", ".mp4", ".webm", ".ts"]):
                    return
                if _is_fake_url(resp_url):
                    return

                all_captured.append(resp_url)

                # Separar master.m3u8 de variantes/index
                if "master.m3u8" in resp_url:
                    master_urls.append(resp_url)
                    logger.info(f"[{provider}] Red master.m3u8: {resp_url[:100]}")
                elif ".m3u8" in resp_url or ".mp4" in resp_url:
                    segment_urls.append(resp_url)
                    logger.info(f"[{provider}] Red segmento: {resp_url[:100]}")

            page.on("response", _on_response)

            logger.info(f"[{provider}] Cargando {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)

            # Esperar a que el JS inline y VAST se inicialicen
            await page.wait_for_timeout(3000)

            # ── Fase 1: red pre-click (priorizar master.m3u8)
            if master_urls:
                best = master_urls[0]
                logger.info(f"[{provider}] master.m3u8 pre-click: {best[:80]}")
                return {"url": best, "provider": provider, "type": "hls"}

            if segment_urls:
                best = _select_best_video_url(segment_urls)
                logger.info(f"[{provider}] URL pre-click via network: {best[:80]}")
                return {"url": best, "provider": provider, "type": "hls" if ".m3u8" in best else "mp4"}

            # ── Fase 2: DOM/JS pre-click
            result = await _extract_from_page(page, provider, label="pre-click")
            if result:
                return result

            # ── Fase 2.5: iframes pre-click (hgplaycdn.com puede ya tener el m3u8)
            result = await _extract_from_frames(page, provider)
            if result:
                return result

            # ── Fase 3: click en play en la página principal
            await _try_click_play(page)

            # Click también dentro de iframes legítimos (hgplaycdn.com)
            await _try_click_play_in_frames(page, provider)

            # Esperar en bloques cortos, verificando si master.m3u8 aparece
            remaining = wait_ms
            check_interval = 2000
            while remaining > 0:
                wait_chunk = min(check_interval, remaining)
                await page.wait_for_timeout(wait_chunk)
                remaining -= wait_chunk

                if master_urls:
                    best = master_urls[0]
                    logger.info(f"[{provider}] master.m3u8 post-click: {best[:80]}")
                    return {"url": best, "provider": provider, "type": "hls"}

                if segment_urls:
                    best = _select_best_video_url(segment_urls)
                    logger.info(f"[{provider}] URL post-click network: {best[:80]}")
                    return {"url": best, "provider": provider, "type": "hls" if ".m3u8" in best else "mp4"}

            # ── Fase 4: DOM/JS post-click
            result = await _extract_from_page(page, provider, label="post-click")
            if result:
                return result

            # ── Fase 5: iframes post-click
            result = await _extract_from_frames(page, provider)
            if result:
                return result

            # Log de URLs capturadas para debugging
            if all_captured:
                logger.warning(f"[{provider}] URLs capturadas pero no válidas: {all_captured[:5]}")
            raise ValueError(f"{provider}: no se encontró fuente de video en la página")

        finally:
            await browser.close()


# ─────────────────────────── extractores específicos ────────────────────────

async def extract_streamwish(url: str) -> dict:
    """Extrae video de StreamWish (SPA, requiere JS)."""
    from urllib.parse import urlparse as _urlparse

    parsed = _urlparse(url)
    real_domain = parsed.netloc.lower()

    # Headers correctos para el dominio real
    headers = {
        "Origin": f"https://{real_domain}",
        "Referer": f"https://{real_domain}/",
    }

    # Actualizar PROVIDER_HEADERS temporalmente para este request
    PROVIDER_HEADERS["streamwish"] = headers

    result = await _extract_with_playwright(url, provider="streamwish", wait_ms=12000)
    result["required_headers"] = headers
    return result


async def extract_filemoon(url: str) -> dict:
    """Extrae video de Filemoon (packed JS)."""
    result = await _extract_with_playwright(url, provider="filemoon", wait_ms=6000)
    result["required_headers"] = PROVIDER_HEADERS.get("filemoon", {})
    return result


async def extract_generic(url: str) -> dict:
    """Extractor genérico con Playwright para cualquier sitio con JS."""
    parsed = urlparse(url)
    provider = parsed.netloc.split(".")[0]
    return await _extract_with_playwright(url, provider=provider, wait_ms=6000)


# ─────────────────────────── router ─────────────────────────────────────────

EXTRACTORS = {
    "streamwish": extract_streamwish,
    "filemoon": extract_filemoon,
}

PLAYWRIGHT_PROVIDERS = {"streamwish", "filemoon"}


def detect_provider(url: str) -> Optional[str]:
    """Detecta el proveedor a partir de la URL."""
    domain = urlparse(url).netloc.lower()
    rules = [
        (["streamwish.com", "streamwish.to", "awish.one", "strwish.com", "sfastwish.com", "niramirus.com"], "streamwish"),
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
        return await extract_generic(url)
    extractor = EXTRACTORS.get(provider)
    if not extractor:
        return await extract_generic(url)
    return await extractor(url)