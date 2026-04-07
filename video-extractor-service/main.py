"""
Video Extractor Service
=======================
Microservicio para extraer URLs de video desde proveedores externos
usando Playwright (navegador headless).

Usado como fallback para sitios que requieren ejecución de JavaScript
(StreamWish, Filemoon, Mega.nz, etc.)
"""

import logging
import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl

from extractors import extract, detect_provider, supported_providers

# ─────────────────────────── logging ────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("video-extractor-service")

# ─────────────────────────── FastAPI app ─────────────────────────────────────

app = FastAPI(
    title="Video Extractor Service",
    description="Microservicio para extraer URLs de video usando Playwright",
    version="1.0.0",
)

# ─────────────────────────── modelos ────────────────────────────────────────


class ExtractRequest(BaseModel):
    url: str
    """URL del embed del proveedor (streamwish, filemoon, mega.nz, etc.)"""


class ExtractResponse(BaseModel):
    success: bool
    url: Optional[str] = None
    provider: Optional[str] = None
    type: Optional[str] = None  # "hls" o "mp4"
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    providers: list[str]


# ─────────────────────────── endpoints ──────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check del servicio."""
    return HealthResponse(
        status="ok",
        providers=supported_providers(),
    )


@app.get("/supported-providers")
async def get_supported_providers():
    """Lista de providers soportados por Playwright."""
    return {"providers": supported_providers()}


@app.post("/extract", response_model=ExtractResponse)
async def extract_video(request: ExtractRequest):
    """
    Extrae la URL directa de video a partir de una URL de embed.

    Usa Playwright (navegador headless) para renderizar la página
    y extraer la URL del video, incluso si la página requiere JavaScript.

    Providers soportados: streamwish, filemoon, mega.nz
    Para otros sitios se usa el extractor genérico de Playwright.
    """
    url = request.url
    provider = detect_provider(url)

    logger.info(f"Extrayendo video de {provider or 'genérico'}: {url[:100]}")

    try:
        result = await extract(url)
        return ExtractResponse(
            success=True,
            url=result["url"],
            provider=result.get("provider"),
            type=result.get("type"),
        )
    except ValueError as e:
        logger.error(f"Error de extracción: {e}")
        return ExtractResponse(
            success=False,
            error=str(e),
        )
    except Exception as e:
        logger.error(f"Error inesperado: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error al extraer video: {str(e)}",
        )
