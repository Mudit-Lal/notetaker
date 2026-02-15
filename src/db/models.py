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


class MessageType(str, Enum):
    """What the AI classifies the incoming message as."""
    NOTE = "note"           # Pure informational note
    TODO = "todo"           # Pure actionable task(s)
    NOTE_WITH_TODOS = "note_with_todos"  # Note that also contains actionable items


class TodoStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"


class TodoPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


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


class Todo(BaseModel):
    id: int | None = None
    text: str
    status: TodoStatus = TodoStatus.PENDING
    priority: TodoPriority = TodoPriority.MEDIUM
    domain: LifeDomain = LifeDomain.OTHER
    tags: list[str] = Field(default_factory=list)
    source_note_id: int | None = None  # links back to a note if extracted from one
    source: NoteSource = NoteSource.TEXT
    due_date: str | None = None  # free-form, e.g. "tomorrow", "Feb 20", "2026-02-18"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class NoteSearchResult(BaseModel):
    note: Note
    relevance_score: float = 0.0
    snippet: str = ""


class Tag(BaseModel):
    id: int | None = None
    name: str  # full slash path: "devalok/hiring"
    parent: str | None = None  # "devalok" or None for top-level
    description: str = ""
    auto_created: bool = False
    usage_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserProfileEntry(BaseModel):
    key: str
    value: str
    updated_at: datetime | None = None


class APIUser(BaseModel):
    id: int | None = None
    username: str
    hashed_password: str
    is_active: bool = True
    created_at: datetime | None = None
