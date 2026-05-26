"""Channel Favorites Service v2 — uses SQLAlchemy repositories."""
from typing import List, Optional

from sqlalchemy.orm import Session

from app.repositories.channel_favorite_repo import ChannelFavoriteRepository
from app.repositories.channel_repo import ChannelRepository


class ChannelFavoritesServiceV2:
    def __init__(self, session: Session):
        self.session = session
        self.repo = ChannelFavoriteRepository(session)
        self.channel_repo = ChannelRepository(session)

    def list_favorites(self, user_id: str) -> List[dict]:
        items = self.repo.list_by_user(user_id)
        return [
            {
                "user_id": str(f.user_id),
                "channel_provider_id": str(f.channel_provider_id),
                "provider_id": str(f.channel_provider_id),
                "created_at": f.created_at,
            }
            for f in items
        ]

    def add_favorite(self, user_id: str, channel_provider_id: str) -> dict:
        fav = self.repo.add(user_id, str(channel_provider_id))
        return {
            "user_id": str(fav.user_id),
            "channel_provider_id": str(fav.channel_provider_id),
            "provider_id": str(fav.channel_provider_id),
            "created_at": fav.created_at,
        }

    def remove_favorite(self, user_id: str, channel_provider_id: str) -> bool:
        return self.repo.remove(user_id, str(channel_provider_id))

    def get_favorite_channels(
        self,
        user_id: str,
        content_svc,
        page: int = 1,
        page_size: int = 50,
        country: Optional[str] = None,
        search: Optional[str] = None,
        username: str = "",
        password: str = "",
    ) -> dict:
        favorites = self.list_favorites(user_id)
        provider_ids = [
            item["channel_provider_id"]
            for item in favorites if item.get("channel_provider_id")
        ]
        if not provider_ids:
            return content_svc.build_paginated_payload([], 0, page, page_size)

        channels, _ = self.channel_repo.get_paginated(
            page=1, page_size=999999, country=country, search=search,
        )
        filtered = [c for c in channels if str(c.provider_id) in provider_ids]
        favorite_order = {pid: i for i, pid in enumerate(provider_ids)}
        filtered.sort(key=lambda c: favorite_order.get(str(c.provider_id), len(provider_ids)))

        total = len(filtered)
        offset = (page - 1) * page_size
        page_channels = filtered[offset:offset + page_size]

        items = [
            content_svc.to_android_catalog_item(
                {
                    "id": str(c.id),
                    "provider_id": str(c.provider_id) if c.provider_id else "",
                    "nombre": c.nombre or "",
                    "nombre_normalizado": c.nombre_normalizado or "",
                    "logo": c.logo or "",
                    "grupo": c.grupo or "",
                    "grupo_normalizado": c.grupo_normalizado or "",
                    "country": c.country or "",
                    "url": c.url or "",
                    "numero": c.numero,
                    "tvg_id": c.tvg_id or "",
                    "tvg_name": c.tvg_name or "",
                    "tvg_logo": c.tvg_logo or "",
                },
                "channels",
                username=username,
                password=password,
            )
            for c in page_channels
        ]
        return content_svc.build_paginated_payload(items, total, page, page_size)
