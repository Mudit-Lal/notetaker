"""MCP (Model Context Protocol) server for Claude tool integration.

This allows Claude to directly interact with your notes as a tool:
- Search notes by keyword or meaning
- Create new notes
- List recent notes
- Get action items
- Browse by life domain
- Browse and filter by hierarchical tags
"""

import logging

from mcp.server.fastmcp import FastMCP

from src.config import settings
from src.db.database import Database
from src.db.models import LifeDomain, Note, NoteSource
from src.db.tag_registry import TagRegistry

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "notetaker",
    instructions="Personal voice & text note-taking system with hierarchical tags",
    host=settings.mcp_host,
    port=settings.mcp_port,
)

# Module-level references — set via init functions
_db: Database | None = None
_tag_registry: TagRegistry | None = None


def init_mcp_db(db: Database) -> None:
    global _db
    _db = db


def init_mcp_tag_registry(registry: TagRegistry) -> None:
    global _tag_registry
    _tag_registry = registry


def _get_db() -> Database:
    if _db is None:
        db = Database(settings.db_path)
        db.connect()
        init_mcp_db(db)
    return _db


def _get_tag_registry() -> TagRegistry | None:
    return _tag_registry


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
        tags: Comma-separated hierarchical tags (e.g. "devalok/hiring,work,urgent")
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

    # Register tags in the vocabulary
    registry = _get_tag_registry()
    if registry and tag_list:
        registry.register_tags_from_note(tag_list)

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


@mcp.tool()
def list_tags(parent: str = "") -> str:
    """Browse the tag vocabulary. Shows all known tags organized hierarchically.

    Args:
        parent: Optional parent tag to filter by (e.g. "devalok" to see devalok sub-tags only)
    """
    registry = _get_tag_registry()
    if not registry:
        return "Tag registry not available."

    if parent:
        tags = registry.get_children(parent)
        if not tags:
            return f"No sub-tags under '{parent}'."
        output = [f"Sub-tags of {parent}:"]
        for t in tags:
            leaf = t.name.split("/")[-1]
            desc = f" — {t.description}" if t.description else ""
            output.append(f"  {leaf} ({t.usage_count} uses){desc}")
        return "\n".join(output)

    # Show full tree
    top_level = registry.get_top_level_tags()
    if not top_level:
        return "No tags registered yet."

    output = ["Tag hierarchy:"]
    for tag in top_level:
        desc = f" — {tag.description}" if tag.description else ""
        children = registry.get_children(tag.name)
        if children:
            kids = ", ".join(f"{c.name.split('/')[-1]}({c.usage_count})" for c in children)
            output.append(f"  {tag.name}/ ({tag.usage_count} uses){desc}\n    {kids}")
        else:
            output.append(f"  {tag.name} ({tag.usage_count} uses){desc}")
    return "\n".join(output)


@mcp.tool()
def list_notes_by_tag(tag: str, limit: int = 20) -> str:
    """List notes filtered by a hierarchical tag. Use a top-level tag to match all its sub-tags too.

    Args:
        tag: Tag name (e.g. "devalok/hiring" for exact, or "devalok" for all devalok/* notes)
        limit: Maximum results (default 20)
    """
    db = _get_db()
    notes = db.list_notes(tag=tag, limit=limit)
    if not notes:
        return f"No notes with tag '{tag}'."

    output = [f"Notes tagged '{tag}':"]
    for n in notes:
        tags = ", ".join(n.tags) if n.tags else ""
        output.append(f"  #{n.id} [{n.domain.value}] {n.summary or n.raw_text[:100]} [{tags}]")
    return "\n".join(output)


def run_mcp_server():
    """Run the MCP server.

    Transport is controlled by MCP_TRANSPORT env var:
      - "stdio" (default): for local / piped connections
      - "sse": for remote HTTP connections (e.g. Claude Desktop over network)
    """
    transport = settings.mcp_transport.lower()
    if transport == "sse":
        logger.info("MCP server starting with SSE transport on %s:%s", settings.mcp_host, settings.mcp_port)
        mcp.run(transport="sse")
    else:
        logger.info("MCP server starting with stdio transport")
        mcp.run()
