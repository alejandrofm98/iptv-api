"""Shim: re-exports models from iptv-db. Source of truth is iptv_db.models."""

from iptv_db.models.channel import Channel, ChannelFavorite

__all__ = ["Channel", "ChannelFavorite"]
