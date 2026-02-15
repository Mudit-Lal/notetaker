from datetime import datetime

from pydantic import BaseModel, Field

from src.db.models import LifeDomain, NoteSource, TodoPriority, TodoStatus


class NoteCreate(BaseModel):
    raw_text: str = Field(..., min_length=1, max_length=50000)
    source: NoteSource = NoteSource.API
    domain: LifeDomain | None = None
    tags: list[str] = Field(default_factory=list)


class NoteUpdate(BaseModel):
    summary: str | None = None
    tags: list[str] | None = None
    domain: LifeDomain | None = None
    action_items: list[str] | None = None


class NoteResponse(BaseModel):
    id: int
    source: NoteSource
    raw_text: str
    summary: str
    tags: list[str]
    domain: LifeDomain
    action_items: list[str]
    related_note_ids: list[int]
    calendar_event_id: str | None
    created_at: datetime
    updated_at: datetime


class NoteSearchResponse(BaseModel):
    note: NoteResponse
    relevance_score: float
    snippet: str


class NotesListResponse(BaseModel):
    notes: list[NoteResponse]
    total: int


class ActionItemResponse(BaseModel):
    note_id: int
    action: str
    created_at: str


class TokenRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=128)


class TagResponse(BaseModel):
    name: str
    parent: str | None
    description: str
    auto_created: bool
    usage_count: int


class TagsListResponse(BaseModel):
    tags: list[TagResponse]
    total: int


class TagTreeNode(BaseModel):
    name: str
    description: str
    usage_count: int
    children: list["TagTreeNode"] = []


class TagTreeResponse(BaseModel):
    tree: list[TagTreeNode]


class MessageResponse(BaseModel):
    message: str


# --- Todos ---


class TodoCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    priority: TodoPriority = TodoPriority.MEDIUM
    domain: LifeDomain | None = None
    tags: list[str] = Field(default_factory=list)
    due_date: str | None = None


class TodoUpdate(BaseModel):
    text: str | None = None
    priority: TodoPriority | None = None
    domain: LifeDomain | None = None
    tags: list[str] | None = None
    due_date: str | None = None


class TodoResponse(BaseModel):
    id: int
    text: str
    status: TodoStatus
    priority: TodoPriority
    domain: LifeDomain
    tags: list[str]
    source_note_id: int | None
    source: NoteSource
    due_date: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class TodosListResponse(BaseModel):
    todos: list[TodoResponse]
    total: int
