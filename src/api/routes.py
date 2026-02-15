from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from src.api.schemas import (
    ActionItemResponse,
    MessageResponse,
    NoteCreate,
    NoteResponse,
    NoteSearchResponse,
    NotesListResponse,
    NoteUpdate,
    RegisterRequest,
    TokenRequest,
    TokenResponse,
)
from src.db.database import Database
from src.db.models import APIUser, LifeDomain, Note

router = APIRouter()

# Database dependency — initialized at app startup
_db: Database | None = None


def get_db() -> Database:
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    return _db


def set_db(db: Database) -> None:
    global _db
    _db = db


# --- Auth ---


@router.post("/auth/register", response_model=TokenResponse, status_code=201)
def register(req: RegisterRequest, db: Database = Depends(get_db)):
    existing = db.get_user_by_username(req.username)
    if existing:
        raise HTTPException(status_code=409, detail="Username already taken")
    user = APIUser(username=req.username, hashed_password=hash_password(req.password))
    db.create_user(user)
    token = create_access_token(req.username)
    return TokenResponse(access_token=token)


@router.post("/auth/token", response_model=TokenResponse)
def login(req: TokenRequest, db: Database = Depends(get_db)):
    user = db.get_user_by_username(req.username)
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    token = create_access_token(req.username)
    return TokenResponse(access_token=token)


# --- Notes ---


@router.post("/notes", response_model=NoteResponse, status_code=201)
def create_note(
    body: NoteCreate,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    note = Note(
        source=body.source,
        raw_text=body.raw_text,
        domain=body.domain or LifeDomain.OTHER,
        tags=body.tags,
    )
    created = db.create_note(note)
    return _note_to_response(created)


@router.get("/notes", response_model=NotesListResponse)
def list_notes(
    domain: LifeDomain | None = None,
    tag: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    notes = db.list_notes(domain=domain, tag=tag, limit=limit, offset=offset)
    return NotesListResponse(notes=[_note_to_response(n) for n in notes], total=len(notes))


@router.get("/notes/search", response_model=list[NoteSearchResponse])
def search_notes(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=20, le=100),
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    results = db.search_notes(q, limit=limit)
    return [
        NoteSearchResponse(
            note=_note_to_response(r.note),
            relevance_score=r.relevance_score,
            snippet=r.snippet,
        )
        for r in results
    ]


@router.get("/notes/actions", response_model=list[ActionItemResponse])
def get_action_items(
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    items = db.get_action_items()
    return [ActionItemResponse(**item) for item in items]


@router.get("/notes/{note_id}", response_model=NoteResponse)
def get_note(
    note_id: int,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    note = db.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return _note_to_response(note)


@router.patch("/notes/{note_id}", response_model=NoteResponse)
def update_note(
    note_id: int,
    body: NoteUpdate,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    note = db.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    if body.summary is not None:
        note.summary = body.summary
    if body.tags is not None:
        note.tags = body.tags
    if body.domain is not None:
        note.domain = body.domain
    if body.action_items is not None:
        note.action_items = body.action_items
    updated = db.update_note(note)
    return _note_to_response(updated)


@router.delete("/notes/{note_id}", response_model=MessageResponse)
def delete_note(
    note_id: int,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    if not db.delete_note(note_id):
        raise HTTPException(status_code=404, detail="Note not found")
    return MessageResponse(message="Note deleted")


@router.get("/health")
def health_check():
    return {"status": "ok"}


def _note_to_response(note: Note) -> NoteResponse:
    return NoteResponse(
        id=note.id,
        source=note.source,
        raw_text=note.raw_text,
        summary=note.summary,
        tags=note.tags,
        domain=note.domain,
        action_items=note.action_items,
        related_note_ids=note.related_note_ids,
        calendar_event_id=note.calendar_event_id,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )
