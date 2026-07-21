from fastapi import APIRouter

router = APIRouter()


@router.get("/", tags=["Health"])
async def root():
    return {"service": "IPTV API", "version": "2.1.0", "status": "running"}


@router.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
