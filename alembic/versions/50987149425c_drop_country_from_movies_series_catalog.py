"""drop_country_from_movies_series_catalog

Revision ID: 50987149425c
Revises: b6608c246678
Create Date: 2026-06-13 11:55:50.465636

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '50987149425c'
down_revision: Union[str, Sequence[str], None] = 'b6608c246678'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop country column from movies_catalog and series_catalog."""
    op.drop_column('movies_catalog', 'country')
    op.drop_column('series_catalog', 'country')


def downgrade() -> None:
    """Re-add country column to movies_catalog and series_catalog."""
    op.add_column('movies_catalog', sa.Column('country', sa.VARCHAR(length=10), autoincrement=False, nullable=True))
    op.add_column('series_catalog', sa.Column('country', sa.VARCHAR(length=10), autoincrement=False, nullable=True))
