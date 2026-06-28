"""fix_watch_progress_unique_constraint

Revision ID: 309bd59b0f90
Revises: 50987149425c
Create Date: 2026-06-28 23:17:15.090413

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '309bd59b0f90'
down_revision: Union[str, Sequence[str], None] = '50987149425c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DELETE FROM watch_progress
        WHERE id NOT IN (
            SELECT DISTINCT ON (user_id, content_id, COALESCE(season_number, 0), COALESCE(episode_number, 0))
                id
            FROM watch_progress
            ORDER BY user_id, content_id, COALESCE(season_number, 0), COALESCE(episode_number, 0), last_watched_at DESC
        )
    """)
    op.drop_constraint('watch_progress_user_content_episode_unique', 'watch_progress', type_='unique')
    op.execute(
        "ALTER TABLE watch_progress "
        "ADD CONSTRAINT watch_progress_user_content_unique "
        "UNIQUE NULLS NOT DISTINCT "
        "(user_id, content_id, season_number, episode_number)"
    )


def downgrade() -> None:
    op.drop_constraint('watch_progress_user_content_unique', 'watch_progress', type_='unique')
    op.execute(
        "ALTER TABLE watch_progress "
        "ADD CONSTRAINT watch_progress_user_content_episode_unique "
        "UNIQUE (user_id, content_id, season_number, episode_number)"
    )
