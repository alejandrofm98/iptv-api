"""initial_schema_v2

Revision ID: b6608c246678
Revises:
Create Date: 2026-06-01 10:42:58.030055

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b6608c246678'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create initial schema for v2 (SQLAlchemy ORM models)."""
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('username', sa.String(50), unique=True, nullable=False),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('max_connections', sa.Integer(), server_default='2'),
        sa.Column('is_active', sa.Boolean(), server_default=sa.true()),
        sa.Column('role', sa.String(20), server_default='user'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        'active_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('device_id', sa.String(64), nullable=False),
        sa.Column('device_name', sa.String(100), nullable=True),
        sa.Column('device_type', sa.String(20), server_default='unknown'),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.String(), nullable=True),
        sa.Column('last_activity', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'device_id', name='uq_active_sessions_user_device'),
    )
    op.create_index('ix_active_sessions_user_id', 'active_sessions', ['user_id'])

    op.create_table(
        'channels',
        sa.Column('id', sa.String(50), primary_key=True),
        sa.Column('provider_id', sa.String(50), nullable=True),
        sa.Column('nombre', sa.String(255), nullable=False),
        sa.Column('nombre_normalizado', sa.String(255), nullable=True),
        sa.Column('logo', sa.Text(), nullable=True),
        sa.Column('grupo', sa.String(255), nullable=True),
        sa.Column('grupo_normalizado', sa.String(255), nullable=True),
        sa.Column('country', sa.String(10), nullable=True),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('numero', sa.Integer(), nullable=True),
        sa.Column('tvg_id', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_channels_country', 'channels', ['country'])
    op.create_index('ix_channels_grupo', 'channels', ['grupo'])
    op.create_index('ix_channels_grupo_normalizado', 'channels', ['grupo_normalizado'])
    op.create_index('ix_channels_provider_id', 'channels', ['provider_id'])

    op.create_table(
        'channel_favorites',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel_provider_id', sa.String(100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('user_id', 'channel_provider_id', name='pk_channel_favorites'),
    )
    op.create_index('ix_channel_favorites_channel_provider_id', 'channel_favorites', ['channel_provider_id'])

    op.create_table(
        'movies_metadata',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tmdb_id', sa.String(20), unique=True, nullable=True),
        sa.Column('title', sa.String(255), nullable=True),
        sa.Column('original_title', sa.String(255), nullable=True),
        sa.Column('overview_es', sa.Text(), nullable=True),
        sa.Column('overview_en', sa.Text(), nullable=True),
        sa.Column('genres', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('vote_average', sa.Float(), nullable=True),
        sa.Column('vote_count', sa.Integer(), nullable=True),
        sa.Column('poster_path', sa.String(255), nullable=True),
        sa.Column('backdrop_path', sa.String(255), nullable=True),
        sa.Column('release_date', sa.Date(), nullable=True),
        sa.Column('year', sa.Integer(), nullable=True),
        sa.Column('runtime_minutes', sa.Integer(), nullable=True),
        sa.Column('tagline', sa.String(500), nullable=True),
        sa.Column('popularity', sa.Float(), nullable=True),
        sa.Column('status', sa.String(50), nullable=True),
        sa.Column('tmdb_data', postgresql.JSONB(), nullable=True),
        sa.Column('scraped_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        'movies_catalog',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('provider_id', sa.String(50), nullable=True),
        sa.Column('tmdb_id', sa.String(20), sa.ForeignKey('movies_metadata.tmdb_id', ondelete='SET NULL'), nullable=True),
        sa.Column('nombre_dedup_key', sa.Text(), nullable=True),
        sa.Column('canonical_key', sa.String(), nullable=True),
        sa.Column('year', sa.Integer(), nullable=True),
        sa.Column('country', sa.String(10), nullable=True),
        sa.Column('group_normalizado', sa.Text(), nullable=True),
        sa.Column('logo', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_movies_catalog_provider_id', 'movies_catalog', ['provider_id'], unique=True)
    op.create_index('ix_movies_catalog_tmdb_id', 'movies_catalog', ['tmdb_id'])
    op.create_index('ix_movies_catalog_canonical_key', 'movies_catalog', ['canonical_key'])
    op.create_index('ix_movies_catalog_country', 'movies_catalog', ['country'])
    op.create_index('ix_movies_catalog_group_normalizado', 'movies_catalog', ['group_normalizado'])
    op.create_index('ix_movies_catalog_year', 'movies_catalog', ['year'])

    op.create_table(
        'movie_streams',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('movie_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('movies_catalog.id', ondelete='CASCADE'), nullable=False),
        sa.Column('country', sa.String(10), nullable=False),
        sa.Column('quality', sa.String(10), nullable=True),
        sa.Column('provider_id', sa.String(50), nullable=True),
        sa.Column('stream_url', sa.Text(), nullable=False),
        sa.Column('url', sa.Text(), nullable=True),
        sa.Column('label', sa.Text(), nullable=True),
        sa.Column('numero', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_movie_streams_movie_id', 'movie_streams', ['movie_id'])
    op.create_index('ix_movie_streams_provider_id', 'movie_streams', ['provider_id'])
    op.create_index('ix_movie_streams_country', 'movie_streams', ['country'])

    op.create_table(
        'series_metadata',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tmdb_id', sa.String(20), unique=True, nullable=True),
        sa.Column('title', sa.String(255), nullable=True),
        sa.Column('original_title', sa.String(255), nullable=True),
        sa.Column('overview_es', sa.Text(), nullable=True),
        sa.Column('overview_en', sa.Text(), nullable=True),
        sa.Column('genres', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('vote_average', sa.Float(), nullable=True),
        sa.Column('vote_count', sa.Integer(), nullable=True),
        sa.Column('poster_path', sa.String(255), nullable=True),
        sa.Column('backdrop_path', sa.String(255), nullable=True),
        sa.Column('release_date', sa.Date(), nullable=True),
        sa.Column('year', sa.Integer(), nullable=True),
        sa.Column('tagline', sa.String(500), nullable=True),
        sa.Column('popularity', sa.Float(), nullable=True),
        sa.Column('status', sa.String(50), nullable=True),
        sa.Column('tmdb_data', postgresql.JSONB(), nullable=True),
        sa.Column('scraped_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        'series_catalog',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('series_key', sa.Text(), nullable=False),
        sa.Column('canonical_key', sa.String(), nullable=True),
        sa.Column('provider_id', sa.String(50), nullable=True),
        sa.Column('tmdb_id', sa.String(20), sa.ForeignKey('series_metadata.tmdb_id', ondelete='SET NULL'), nullable=True),
        sa.Column('nombre_dedup_key', sa.Text(), nullable=True),
        sa.Column('year', sa.Integer(), nullable=True),
        sa.Column('country', sa.String(10), nullable=True),
        sa.Column('group_normalizado', sa.Text(), nullable=True),
        sa.Column('logo', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_series_catalog_provider_id', 'series_catalog', ['provider_id'], unique=True)
    op.create_index('ix_series_catalog_tmdb_id', 'series_catalog', ['tmdb_id'])
    op.create_index('ix_series_catalog_canonical_key', 'series_catalog', ['canonical_key'])
    op.create_index('ix_series_catalog_country', 'series_catalog', ['country'])
    op.create_index('ix_series_catalog_group_normalizado', 'series_catalog', ['group_normalizado'])
    op.create_index('ix_series_catalog_year', 'series_catalog', ['year'])

    op.create_table(
        'series_episodes',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('catalog_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('series_catalog.id', ondelete='CASCADE'), nullable=False),
        sa.Column('season_number', sa.Integer(), nullable=False),
        sa.Column('episode_number', sa.Integer(), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('overview', sa.Text(), nullable=True),
        sa.Column('air_date', sa.Date(), nullable=True),
        sa.Column('still_path', sa.String(255), nullable=True),
        sa.Column('numero', sa.Integer(), nullable=True),
        sa.UniqueConstraint('catalog_id', 'season_number', 'episode_number', name='uq_series_episodes_catalog_season_episode'),
    )
    op.create_index('ix_series_episodes_catalog_id', 'series_episodes', ['catalog_id'])
    op.create_index('ix_series_episodes_season_number', 'series_episodes', ['season_number'])

    op.create_table(
        'series_streams',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('episode_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('series_episodes.id', ondelete='CASCADE'), nullable=False),
        sa.Column('country', sa.String(10), nullable=False),
        sa.Column('quality', sa.String(10), nullable=True),
        sa.Column('provider_id', sa.String(50), nullable=True),
        sa.Column('stream_url', sa.Text(), nullable=False),
        sa.Column('url', sa.Text(), nullable=True),
        sa.Column('label', sa.Text(), nullable=True),
        sa.Column('numero', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_series_streams_episode_id', 'series_streams', ['episode_id'])
    op.create_index('ix_series_streams_provider_id', 'series_streams', ['provider_id'])
    op.create_index('ix_series_streams_country', 'series_streams', ['country'])

    op.create_table(
        'replays',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('slug', sa.Text(), nullable=False, unique=True),
        sa.Column('source_site', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('event_name', sa.Text(), nullable=True),
        sa.Column('event_type', sa.Text(), nullable=True),
        sa.Column('event_date', sa.Date(), nullable=True),
        sa.Column('post_url', sa.Text(), nullable=False),
        sa.Column('featured_image_url', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('video_sources', postgresql.JSONB(), nullable=False),
        sa.Column('match_card', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_replays_event_date', 'replays', ['event_date'])
    op.create_index('ix_replays_event_name', 'replays', ['event_name'])
    op.create_index('ix_replays_event_type', 'replays', ['event_type'])
    op.create_index('ix_replays_slug', 'replays', ['slug'], unique=True)

    op.create_table(
        'watch_progress',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('content_id', sa.String(100), nullable=False),
        sa.Column('content_type', sa.String(20), nullable=False),
        sa.Column('position_ms', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('duration_ms', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('series_name', sa.String(255), nullable=True),
        sa.Column('season_number', sa.Integer(), nullable=True),
        sa.Column('episode_number', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(255), nullable=False, server_default=''),
        sa.Column('image_url', sa.String(), nullable=False, server_default=''),
        sa.Column('last_watched_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('is_watched', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint('user_id', 'content_id', name='uq_watch_progress_user_content'),
    )
    op.create_index('ix_watch_progress_user_id', 'watch_progress', ['user_id'])
    op.create_index('ix_watch_progress_content_id', 'watch_progress', ['content_id'])
    op.create_index('ix_watch_progress_last_watched_at', 'watch_progress', ['last_watched_at'])

    op.create_table(
        'config',
        sa.Column('key', sa.String(), primary_key=True),
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        'sync_metadata',
        sa.Column('id', sa.String(50), primary_key=True),
        sa.Column('ultima_actualizacion', postgresql.TIMESTAMP(), nullable=True),
        sa.Column('total_canales', sa.Integer(), nullable=True),
        sa.Column('total_movies', sa.Integer(), nullable=True),
        sa.Column('total_series', sa.Integer(), nullable=True),
        sa.Column('m3u_template_path', sa.Text(), nullable=True),
        sa.Column('m3u_template_filename', sa.Text(), nullable=True),
        sa.Column('m3u_size_mb', sa.Numeric(10, 2), nullable=True),
        sa.Column('channels_con_logo', sa.Integer(), nullable=True),
        sa.Column('channels_sin_logo', sa.Integer(), nullable=True),
        sa.Column('movies_con_logo', sa.Integer(), nullable=True),
        sa.Column('movies_sin_logo', sa.Integer(), nullable=True),
        sa.Column('series_con_logo', sa.Integer(), nullable=True),
        sa.Column('series_sin_logo', sa.Integer(), nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(), nullable=True),
        sa.Column('updated_at', postgresql.TIMESTAMP(), nullable=True),
        sa.Column('channels_generated_at', postgresql.TIMESTAMP(), nullable=True),
        sa.Column('channels_json_size_mb', sa.Numeric(10, 2), nullable=True),
        sa.Column('movies_generated_at', postgresql.TIMESTAMP(), nullable=True),
        sa.Column('movies_json_size_mb', sa.Numeric(10, 2), nullable=True),
        sa.Column('series_generated_at', postgresql.TIMESTAMP(), nullable=True),
        sa.Column('series_json_size_mb', sa.Numeric(10, 2), nullable=True),
    )

    op.create_table(
        'scraper_failures',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('provider_id', sa.String(50), nullable=True),
        sa.Column('series_key', sa.String(255), nullable=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('failed_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('retry_count', sa.Integer(), server_default='1'),
        sa.Column('last_retry_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_scraper_failures_provider_id', 'scraper_failures', ['provider_id'])
    op.create_index('ix_scraper_failures_series_key', 'scraper_failures', ['series_key'])
    op.create_index('ix_scraper_failures_failed_at', 'scraper_failures', ['failed_at'])


def downgrade() -> None:
    """Drop all tables in reverse order."""
    op.drop_table('scraper_failures')
    op.drop_table('sync_metadata')
    op.drop_table('config')
    op.drop_table('watch_progress')
    op.drop_table('replays')
    op.drop_table('series_streams')
    op.drop_table('series_episodes')
    op.drop_table('series_catalog')
    op.drop_table('series_metadata')
    op.drop_table('movie_streams')
    op.drop_table('movies_catalog')
    op.drop_table('movies_metadata')
    op.drop_table('channel_favorites')
    op.drop_table('channels')
    op.drop_table('active_sessions')
    op.drop_table('users')
