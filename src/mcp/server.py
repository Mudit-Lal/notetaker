"""MCP (Model Context Protocol) server for Claude tool integration.

This allows Claude to directly interact with your notes as a tool:
- Search notes by keyword or meaning
- Create new notes
- List recent notes
- Get action items
- Browse by life domain
"""

import json
import logging

from mcp.server.fastmcp import FastMCP

from src.config import settings
from src.db.database import Database
from src.db.models import LifeDomain, Note, NoteSource

logger = logging.getLogger(__name__)

mcp = FastMCP("notetaker", instructions="Personal voice & text note-taking system")

# Module-level database reference — set via init_mcp_db()
_db: Database | None = None


def init_mcp_db(db: Database) -> None:
    global _db
    _db = db


def _get_db() -> Database:
    if _db is None:
        db = Database(settings.db_path)
        db.connect()
        init_mcp_db(db)
    return _db


@mcp.tool()
def search_notes(query: str, limit: int = 10) -> str:
    """Search notes by keyword. Uses full-text search across note content, summaries, and tags.

    Args:
        query: Search keywords or phrase
        limit: Maximum results to return (default 10)
    """
    db = _get_db()
    results = db.search_notes(query, limit=limit)
    if not results:
        return f"No notes found matching '{query}'"

    output = []
    for r in results:
        n = r.note
        tags = ", ".join(n.tags) if n.tags else "none"
        output.append(
            f"Note #{n.id} [{n.domain.value}]\n"
            f"  Summary: {n.summary}\n"
            f"  Tags: {tags}\n"
            f"  Created: {n.created_at}\n"
            f"  Text: {n.raw_text[:300]}"
        )
    return "\n\n".join(output)


@mcp.tool()
def get_recent_notes(limit: int = 10) -> str:
    """Get the most recent notes.

    Args:
        limit: Number of recent notes to return (default 10)
    """
    db = _get_db()
    notes = db.get_recent_notes(limit)
    if not notes:
        return "No notes found."

    output = []
    for n in notes:
        tags = ", ".join(n.tags) if n.tags else "none"
        output.append(
            f"Note #{n.id} [{n.domain.value}] ({n.source.value})\n"
            f"  Summary: {n.summary}\n"
            f"  Tags: {tags}\n"
            f"  Created: {n.created_at}\n"
            f"  Text: {n.raw_text[:300]}"
        )
    return "\n\n".join(output)


@mcp.tool()
def get_note(note_id: int) -> str:
    """Get full details of a specific note by its ID.

    Args:
        note_id: The note ID number
    """
    db = _get_db()
    note = db.get_note(note_id)
    if not note:
        return f"Note #{note_id} not found."

    tags = ", ".join(note.tags) if note.tags else "none"
    actions = "\n  ".join(f"- {a}" for a in note.action_items) if note.action_items else "none"
    related = ", ".join(f"#{r}" for r in note.related_note_ids) if note.related_note_ids else "none"

    return (
        f"Note #{note.id}\n"
        f"Source: {note.source.value}\n"
        f"Domain: {note.domain.value}\n"
        f"Created: {note.created_at}\n"
        f"Updated: {note.updated_at}\n\n"
        f"Full text:\n{note.raw_text}\n\n"
        f"Summary: {note.summary}\n"
        f"Tags: {tags}\n"
        f"Action items:\n  {actions}\n"
        f"Related notes: {related}"
    )


@mcp.tool()
def create_note(text: str, domain: str = "other", tags: str = "") -> str:
    """Create a new note from text.

    Args:
        text: The note content
        domain: Life area — one of: work, personal, health, finance, creative, learning, social, other
        tags: Comma-separated tags (e.g. "meeting,project-x,urgent")
    """
    db = _get_db()

    try:
        life_domain = LifeDomain(domain)
    except ValueError:
        life_domain = LifeDomain.OTHER

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    note = Note(
        source=NoteSource.MCP,
        raw_text=text,
        domain=life_domain,
        tags=tag_list,
    )
    note = db.create_note(note)
    return f"Note #{note.id} created in {note.domain.value} domain with tags: {tag_list}"


@mcp.tool()
def get_action_items() -> str:
    """Get all pending action items extracted from notes."""
    db = _get_db()
    items = db.get_action_items()
    if not items:
        return "No action items found."

    output = []
    for item in items:
        output.append(f"- {item['action']} (from note #{item['note_id']}, {item['created_at']})")
    return "\n".join(output)


@mcp.tool()
def list_notes_by_domain(domain: str, limit: int = 20) -> str:
    """List notes filtered by life domain.

    Args:
        domain: One of: work, personal, health, finance, creative, learning, social, other
        limit: Maximum results (default 20)
    """
    db = _get_db()
    try:
        life_domain = LifeDomain(domain)
    except ValueError:
        return f"Invalid domain '{domain}'. Use: work, personal, health, finance, creative, learning, social, other"

    notes = db.list_notes(domain=life_domain, limit=limit)
    if not notes:
        return f"No notes in the {domain} domain."

    output = []
    for n in notes:
        tags = ", ".join(n.tags) if n.tags else ""
        output.append(f"#{n.id}: {n.summary or n.raw_text[:100]} [{tags}]")
    return f"Notes in {domain}:\n" + "\n".join(output)


@mcp.tool()
def get_notes_summary() -> str:
    """Get an overview of all notes — counts by domain, recent activity, and pending actions."""
    db = _get_db()

    summary_parts = []
    total = 0
    for domain in LifeDomain:
        notes = db.list_notes(domain=domain, limit=1000)
        count = len(notes)
        total += count
        if count > 0:
            summary_parts.append(f"  {domain.value}: {count} notes")

    recent = db.get_recent_notes(3)
    recent_str = "\n".join(
        f"  #{n.id} [{n.domain.value}] {n.summary or n.raw_text[:60]}" for n in recent
    )

    actions = db.get_action_items()

    return (
        f"Total notes: {total}\n\n"
        f"By domain:\n{''.join(summary_parts) or '  (none)'}\n\n"
        f"Most recent:\n{recent_str or '  (none)'}\n\n"
        f"Pending action items: {len(actions)}"
    )


def run_mcp_server():
    """Run the MCP server (stdio transport for Claude Desktop/CLI)."""
    mcp.run()
