from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class NoteSource(str, Enum):
    VOICE = "voice"
    TEXT = "text"
    API = "api"
    MCP = "mcp"


class LifeDomain(str, Enum):
    WORK = "work"
    PERSONAL = "personal"
    HEALTH = "health"
    FINANCE = "finance"
    CREATIVE = "creative"
    LEARNING = "learning"
    SOCIAL = "social"
    OTHER = "other"


class Note(BaseModel):
    id: int | None = None
    source: NoteSource
    raw_text: str
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    domain: LifeDomain = LifeDomain.OTHER
    action_items: list[str] = Field(default_factory=list)
    related_note_ids: list[int] = Field(default_factory=list)
    calendar_event_id: str | None = None
    telegram_message_id: int | None = None
    audio_duration_seconds: float | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NoteSearchResult(BaseModel):
    note: Note
    relevance_score: float = 0.0
    snippet: str = ""


class APIUser(BaseModel):
    id: int | None = None
    username: str
    hashed_password: str
    is_active: bool = True
    created_at: datetime | None = None
