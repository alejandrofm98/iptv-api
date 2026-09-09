"""Hidden channel groups service — uses SQLAlchemy repositories."""

from sqlalchemy.orm import Session

from iptv_api.repositories.hidden_group_repo import HiddenGroupRepository


class HiddenGroupService:
    def __init__(self, session: Session):
        self.session = session
        self.repo = HiddenGroupRepository(session)

    @staticmethod
    def _to_dict(row) -> dict:
        return {
            "user_id": str(row.user_id),
            "country": row.country or "",
            "group_name": str(row.group_name),
            "created_at": row.created_at,
        }

    def list_hidden(self, user_id: str) -> list[dict]:
        return [self._to_dict(r) for r in self.repo.list_by_user(user_id)]

    def hide(self, user_id: str, group_name: str, country: str = "") -> dict:
        row = self.repo.hide(user_id, (country or "").strip().upper(), group_name.strip())
        return self._to_dict(row)

    def unhide(self, user_id: str, group_name: str, country: str = "") -> bool:
        return self.repo.unhide(user_id, (country or "").strip().upper(), group_name.strip())
