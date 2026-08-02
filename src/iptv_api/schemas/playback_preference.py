"""DTOs de preferencias de audio y subtitulos."""

from datetime import datetime

from pydantic import BaseModel


class PlaybackPreferenceUpdate(BaseModel):
    audio_language: str | None = None
    audio_label: str | None = None
    subtitle_language: str | None = None
    subtitle_label: str | None = None
    subtitles_disabled: bool | None = None


class PlaybackPreferenceResponse(PlaybackPreferenceUpdate):
    id: str
    user_id: str
    content_type: str
    catalog_id: str
    created_at: datetime
    updated_at: datetime
