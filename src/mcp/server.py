"""MCP (Model Context Protocol) server for Claude tool integration.

This allows Claude to directly interact with your notes as a tool:
- Search notes by keyword or meaning
- Create new notes
- List recent notes
- Get action items
- Browse by life domain
- Browse and filter by hierarchical tags
- View and update user profile context for smarter AI tagging
"""

import logging

from mcp.server.fastmcp import FastMCP

from src.ai.processor import AIProcessor
from src.config import settings
from src.db.database import Database
from src.db.models import LifeDomain, MessageType, Note, NoteSource, Todo, TodoPriority, TodoStatus
from src.db.tag_registry import TagRegistry

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "notetaker",
    instructions=(
        "This is a personal knowledge repository. Claude is the PRIMARY interface.\n\n"
        "TOOL SELECTION GUIDE:\n"
        "- save_message: Use for any new content (notes, tasks, or both). AI auto-classifies.\n"
        "- create_note / create_todo: Use when you want explicit control over domain/tags (skips AI).\n"
        "- search_notes: Use for keyword search. Try this before get_recent_notes for specific queries.\n"
        "- get_note: Use to read a full note including its linked to-dos.\n"
        "- get_notes_since: Use for time-based queries ('last week', 'today', 'past 3 days').\n"
        "- update_note: Use to correct tags, domain, or summary on an existing note.\n"
        "- delete_note: Permanent. Confirm with user before deleting.\n"
        "- update_todo: Use to change text, priority, due date, domain, or tags of a to-do.\n"
        "- list_todos: Default to status='pending'. Use domain filter to narrow scope.\n"
        "- batch_complete_todos: Prefer over multiple complete_todo calls when completing several at once.\n"
        "- get_todos_for_note: Use when you need a note's full context including its tasks.\n"
        "- list_tags / list_notes_by_tag: Use for tag-based browsing and tag vocabulary exploration.\n"
        "- get_notes_summary: Use for dashboard overviews. Avoid calling repeatedly.\n"
        "- get_user_profile / update_profile_field: Read or enrich the user's profile context.\n\n"
        "DATA MODEL:\n"
        "- Notes: immutable raw text with AI-generated summary/tags/domain. IDs prefixed #.\n"
        "- Todos: mutable tasks with priority (high/medium/low), optional due_date, optional source_note_id. IDs prefixed T#.\n"
        "- Domains: work, personal, health, finance, creative, learning, social, other.\n"
        "- Tags: hierarchical, max 2 levels (e.g. 'devalok/hiring'). Use list_tags to see vocabulary."
    ),
    host=settings.mcp_host,
    port=settings.mcp_port,
)

# Module-level references — set via init functions
_db: Database | None = None
_tag_registry: TagRegistry | None = None
_ai: AIProcessor | None = None


def init_mcp_db(db: Database) -> None:
    global _db
    _db = db


def init_mcp_tag_registry(registry: TagRegistry) -> None:
    global _tag_registry
    _tag_registry = registry


def init_mcp_ai(ai: AIProcessor) -> None:
    global _ai
    _ai = ai


def _get_ai() -> AIProcessor | None:
    return _ai


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

    todos = db.get_todos_for_note(note_id)
    if todos:
        todo_lines = []
        for t in todos:
            prio = f" [{t.priority.value}]" if t.priority.value != "medium" else ""
            due = f" (due: {t.due_date})" if t.due_date else ""
            check = "x" if t.status == TodoStatus.COMPLETED else " "
            todo_lines.append(f"  [{check}] T#{t.id}{prio} {t.text}{due}")
        todos_str = "\n".join(todo_lines)
    else:
        todos_str = "none"

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
        f"Related notes: {related}\n"
        f"Linked to-dos:\n{todos_str}"
    )


@mcp.tool()
def get_todos_for_note(note_id: int) -> str:
    """Get all to-dos that were extracted from or linked to a specific note.

    Args:
        note_id: The note ID to look up linked to-dos for
    """
    db = _get_db()
    if not db.get_note(note_id):
        return f"Note #{note_id} not found."
    todos = db.get_todos_for_note(note_id)
    if not todos:
        return f"No to-dos linked to Note #{note_id}."
    output = [f"To-dos for Note #{note_id}:"]
    for t in todos:
        prio = f" [{t.priority.value}]" if t.priority.value != "medium" else ""
        due = f" (due: {t.due_date})" if t.due_date else ""
        check = "x" if t.status == TodoStatus.COMPLETED else " "
        output.append(f"  [{check}] T#{t.id}{prio} {t.text}{due}")
    return "\n".join(output)


@mcp.tool()
def get_notes_since(days_ago: int, limit: int = 50) -> str:
    """Get notes created in the last N days. Useful for daily and weekly reviews.

    Args:
        days_ago: Number of days to look back (e.g. 1 for today, 7 for last week)
        limit: Maximum results to return (default 50)
    """
    if days_ago < 1:
        return "days_ago must be at least 1."
    db = _get_db()
    notes = db.list_notes_since(days_ago, limit=limit)
    if not notes:
        return f"No notes in the last {days_ago} day(s)."
    output = [f"Notes from the last {days_ago} day(s) ({len(notes)} found):"]
    for n in notes:
        tags = f" [{', '.join(n.tags)}]" if n.tags else ""
        date_str = n.created_at.strftime("%b %d %H:%M") if n.created_at else ""
        output.append(
            f"  #{n.id} [{n.domain.value}] {date_str}\n"
            f"    {n.summary or n.raw_text[:100]}{tags}"
        )
    return "\n\n".join(output)


@mcp.tool()
def save_message(text: str) -> str:
    """Intelligently save a message as a note, to-do(s), or both.
    The AI will automatically classify the message and route it.
    Use this when the user gives you something to save/remember/do.

    Args:
        text: The message content (note, task, or mix)
    """
    db = _get_db()
    ai = _get_ai()

    if not ai:
        # Fallback: save as a plain note if AI processor not available
        note = Note(source=NoteSource.MCP, raw_text=text)
        note = db.create_note(note)
        return f"Note #{note.id} saved (AI not available for classification)."

    result = ai.enrich_message(text, "mcp")
    msg_type: MessageType = result["message_type"]
    note = result.get("note")
    todos = result.get("todos", [])

    parts = []
    saved_note = None

    # Save note
    if note:
        recent = db.get_recent_notes(50)
        note.related_note_ids = ai.find_related_notes(note, recent)
        saved_note = db.create_note(note)
        registry = _get_tag_registry()
        if registry and saved_note.tags:
            registry.register_tags_from_note(saved_note.tags)
        tags_str = ", ".join(saved_note.tags) if saved_note.tags else "none"
        parts.append(
            f"Note #{saved_note.id} saved [{saved_note.domain.value}]\n"
            f"  Summary: {saved_note.summary}\n"
            f"  Tags: {tags_str}"
        )

    # Save todos
    saved_todos = []
    for todo in todos:
        if saved_note:
            todo.source_note_id = saved_note.id
        t = db.create_todo(todo)
        saved_todos.append(t)
        prio = f" [{t.priority.value}]" if t.priority.value != "medium" else ""
        due = f" (due: {t.due_date})" if t.due_date else ""
        parts.append(f"To-do T#{t.id}{prio}: {t.text}{due}")

    if not parts:
        return "Nothing to save."

    type_label = {"note": "Note", "todo": "To-do", "note_with_todos": "Note + To-dos"}
    header = f"Classified as: {type_label.get(msg_type.value, msg_type.value)}\n\n"
    return header + "\n\n".join(parts)


@mcp.tool()
def create_note(text: str, domain: str = "other", tags: str = "") -> str:
    """Create a new note directly (skips AI classification).
    For intelligent routing use save_message instead.

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

    registry = _get_tag_registry()
    if registry and tag_list:
        registry.register_tags_from_note(tag_list)

    return f"Note #{note.id} created in {note.domain.value} domain with tags: {tag_list}"


@mcp.tool()
def delete_note(note_id: int) -> str:
    """Permanently delete a note by ID. This cannot be undone.
    Associated to-dos are NOT deleted — they remain as orphaned tasks.

    Args:
        note_id: The note ID number to delete
    """
    db = _get_db()
    note = db.get_note(note_id)
    if not note:
        return f"Note #{note_id} not found."
    preview = note.summary or note.raw_text[:80]
    if db.delete_note(note_id):
        return f'Note #{note_id} deleted: "{preview}"'
    return f"Failed to delete Note #{note_id}."


@mcp.tool()
def update_note(note_id: int, summary: str = "", tags: str = "", domain: str = "") -> str:
    """Edit a note's summary, tags, or domain. Only provided fields are changed.
    The raw text is not editable (it's the source of truth — delete and recreate if needed).
    Providing tags replaces all existing tags. To clear all tags, pass tags=",".

    Args:
        note_id: The note ID to update
        summary: New summary text (leave empty to keep existing)
        tags: Comma-separated tags, replaces all existing (leave empty to keep existing)
        domain: New life domain (leave empty to keep existing)
    """
    db = _get_db()
    note = db.get_note(note_id)
    if not note:
        return f"Note #{note_id} not found."

    changed = []

    if summary:
        note.summary = summary
        changed.append("summary")

    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        note.tags = tag_list
        changed.append(f"tags={tag_list}")
        registry = _get_tag_registry()
        if registry and tag_list:
            registry.register_tags_from_note(tag_list)

    if domain:
        try:
            note.domain = LifeDomain(domain)
            changed.append(f"domain={domain}")
        except ValueError:
            return f"Invalid domain '{domain}'. Use: work, personal, health, finance, creative, learning, social, other"

    if not changed:
        return "No changes provided. Pass at least one of: summary, tags, domain."

    db.update_note(note)
    return f"Note #{note_id} updated ({', '.join(changed)})."


@mcp.tool()
def get_action_items() -> str:
    """Get all pending action items extracted from notes (legacy).
    For the newer to-do system, use list_todos instead."""
    db = _get_db()
    items = db.get_action_items()
    if not items:
        return "No action items found."

    output = []
    for item in items:
        output.append(f"- {item['action']} (from note #{item['note_id']}, {item['created_at']})")
    return "\n".join(output)


@mcp.tool()
def create_todo(text: str, priority: str = "medium", domain: str = "other", tags: str = "", due_date: str = "") -> str:
    """Create a new to-do item.

    Args:
        text: The task description (clear, actionable)
        priority: Priority level — "high", "medium", or "low" (default "medium")
        domain: Life area — one of: work, personal, health, finance, creative, learning, social, other
        tags: Comma-separated hierarchical tags (e.g. "devalok/clients,work")
        due_date: Optional due date (e.g. "tomorrow", "Feb 20", "2026-02-18")
    """
    db = _get_db()
    try:
        prio = TodoPriority(priority)
    except ValueError:
        prio = TodoPriority.MEDIUM
    try:
        life_domain = LifeDomain(domain)
    except ValueError:
        life_domain = LifeDomain.OTHER
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    todo = Todo(
        text=text,
        priority=prio,
        domain=life_domain,
        tags=tag_list,
        source=NoteSource.MCP,
        due_date=due_date or None,
    )
    todo = db.create_todo(todo)

    registry = _get_tag_registry()
    if registry and tag_list:
        registry.register_tags_from_note(tag_list)

    return f"To-do T#{todo.id} created: {todo.text} [{todo.priority.value}]"


@mcp.tool()
def list_todos(status: str = "pending", domain: str = "", limit: int = 30) -> str:
    """List to-do items, optionally filtered by status and domain.

    Args:
        status: Filter by status — "pending", "completed", or "" for all (default "pending")
        domain: Filter by life domain (optional)
        limit: Maximum results (default 30)
    """
    db = _get_db()
    todo_status = None
    if status:
        try:
            todo_status = TodoStatus(status)
        except ValueError:
            return f"Invalid status '{status}'. Use: pending, completed"

    todo_domain = None
    if domain:
        try:
            todo_domain = LifeDomain(domain)
        except ValueError:
            return f"Invalid domain '{domain}'."

    todos = db.list_todos(status=todo_status, domain=todo_domain, limit=limit)
    if not todos:
        label = f" ({status})" if status else ""
        return f"No to-dos found{label}."

    output = []
    for t in todos:
        prio = f" [{t.priority.value}]" if t.priority.value != "medium" else ""
        due = f" (due: {t.due_date})" if t.due_date else ""
        note_ref = f" (from note #{t.source_note_id})" if t.source_note_id else ""
        check = "x" if t.status == TodoStatus.COMPLETED else " "
        tags_str = f" [{', '.join(t.tags)}]" if t.tags else ""
        date_str = f" (created: {t.created_at.strftime('%b %d')})" if t.created_at else ""
        output.append(f"[{check}] T#{t.id}{prio} {t.text}{due}{date_str}{note_ref}{tags_str}")
    return "\n".join(output)


@mcp.tool()
def complete_todo(todo_id: int) -> str:
    """Mark a to-do as completed.

    Args:
        todo_id: The to-do ID number (e.g. 5)
    """
    db = _get_db()
    todo = db.complete_todo(todo_id)
    if not todo:
        return f"To-do T#{todo_id} not found."
    return f"Done! T#{todo.id} marked as completed: {todo.text}"


@mcp.tool()
def uncomplete_todo(todo_id: int) -> str:
    """Reopen a completed to-do (mark it as pending again).

    Args:
        todo_id: The to-do ID number
    """
    db = _get_db()
    todo = db.uncomplete_todo(todo_id)
    if not todo:
        return f"To-do T#{todo_id} not found."
    return f"Reopened T#{todo.id}: {todo.text}"


@mcp.tool()
def delete_todo(todo_id: int) -> str:
    """Delete a to-do item permanently.

    Args:
        todo_id: The to-do ID number
    """
    db = _get_db()
    if db.delete_todo(todo_id):
        return f"To-do T#{todo_id} deleted."
    return f"To-do T#{todo_id} not found."


@mcp.tool()
def update_todo(
    todo_id: int,
    text: str = "",
    priority: str = "",
    domain: str = "",
    tags: str = "",
    due_date: str = "",
) -> str:
    """Edit a to-do's text, priority, domain, tags, or due date.
    Only provided fields are changed. Pass due_date='none' to clear an existing due date.

    Args:
        todo_id: The to-do ID to update
        text: New task description (leave empty to keep existing)
        priority: New priority — "high", "medium", or "low" (leave empty to keep existing)
        domain: New life domain (leave empty to keep existing)
        tags: Comma-separated tags, replaces all existing (leave empty to keep existing)
        due_date: New due date string, or "none" to clear (leave empty to keep existing)
    """
    db = _get_db()
    todo = db.get_todo(todo_id)
    if not todo:
        return f"To-do T#{todo_id} not found."

    changed = []

    if text:
        todo.text = text
        changed.append("text")

    if priority:
        try:
            todo.priority = TodoPriority(priority)
            changed.append(f"priority={priority}")
        except ValueError:
            return f"Invalid priority '{priority}'. Use: high, medium, low"

    if domain:
        try:
            todo.domain = LifeDomain(domain)
            changed.append(f"domain={domain}")
        except ValueError:
            return f"Invalid domain '{domain}'. Use: work, personal, health, finance, creative, learning, social, other"

    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        todo.tags = tag_list
        changed.append(f"tags={tag_list}")
        registry = _get_tag_registry()
        if registry and tag_list:
            registry.register_tags_from_note(tag_list)

    if due_date:
        todo.due_date = None if due_date.lower() == "none" else due_date
        changed.append("due_date")

    if not changed:
        return "No changes provided. Pass at least one of: text, priority, domain, tags, due_date."

    updated = db.update_todo(todo)
    if not updated:
        return f"Failed to update T#{todo_id}."
    return f"T#{todo_id} updated ({', '.join(changed)}): {updated.text}"


@mcp.tool()
def batch_complete_todos(todo_ids: str) -> str:
    """Mark multiple to-dos as completed in one call.

    Args:
        todo_ids: Comma-separated list of to-do ID numbers (e.g. "3,7,12")
    """
    db = _get_db()
    id_strings = [s.strip() for s in todo_ids.split(",") if s.strip()]
    if not id_strings:
        return "No to-do IDs provided."

    results = []
    errors = []
    for id_str in id_strings:
        try:
            tid = int(id_str)
        except ValueError:
            errors.append(f"'{id_str}' is not a valid ID")
            continue
        todo = db.complete_todo(tid)
        if todo:
            results.append(f"  T#{todo.id}: {todo.text}")
        else:
            errors.append(f"T#{tid} not found")

    output = []
    if results:
        output.append(f"Completed {len(results)} to-do(s):\n" + "\n".join(results))
    if errors:
        output.append("Errors:\n" + "\n".join(f"  {e}" for e in errors))
    return "\n\n".join(output)


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
    """Get an overview of all notes and to-dos — counts by domain, recent activity, and pending tasks."""
    db = _get_db()

    domain_counts = db.count_notes_by_domain()
    total = sum(domain_counts.values())
    domain_str = "\n".join(
        f"  {d}: {c} notes" for d, c in sorted(domain_counts.items())
    ) or "  (none)"

    recent = db.get_recent_notes(3)
    recent_str = "\n".join(
        f"  #{n.id} [{n.domain.value}] {n.summary or n.raw_text[:60]}" for n in recent
    ) or "  (none)"

    pending_count = db.conn.execute(
        "SELECT COUNT(*) FROM todos WHERE status = 'pending'"
    ).fetchone()[0]
    completed_count = db.conn.execute(
        "SELECT COUNT(*) FROM todos WHERE status = 'completed'"
    ).fetchone()[0]

    return (
        f"Total notes: {total}\n\n"
        f"By domain:\n{domain_str}\n\n"
        f"Most recent:\n{recent_str}\n\n"
        f"To-dos: {pending_count} pending, {completed_count} completed"
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


@mcp.tool()
def get_user_profile() -> str:
    """View the user's profile context that is used to personalize note tagging.
    Shows all profile fields like bio, companies, projects, interests, etc.
    This context is injected into the AI system prompt when processing notes.
    """
    db = _get_db()
    profile = db.get_profile()
    if not profile:
        return (
            "No user profile set yet. Use update_profile_field to add context.\n"
            "Recommended keys:\n"
            "- bio: A short personal/professional summary\n"
            "- companies: Current companies/orgs and your roles\n"
            "- current_projects: What you're actively working on\n"
            "- interests: Topics and areas you care about\n"
            "- key_people: Family, partner, key contacts the AI should recognize\n"
            "- devalok_vocabulary: Sanskrit/internal terms used in notes\n"
            "- clients: Client names so the AI can tag notes about them\n"
            "- note_context: How you take notes, common topics, languages used\n"
            "\nYou can also create any custom key."
        )

    output = ["User Profile:"]
    for key, value in profile.items():
        label = key.replace("_", " ").title()
        output.append(f"\n{label}:\n  {value}")
    return "\n".join(output)


@mcp.tool()
def update_profile_field(key: str, value: str) -> str:
    """Update a field in the user's profile context. This context is injected into the AI
    system prompt when processing notes, so richer context leads to better tagging.

    Common keys: bio, companies, current_projects, interests, key_people,
    devalok_vocabulary, clients, note_context. You can also use any custom key.

    Args:
        key: The profile field name (lowercase, underscores for spaces, e.g. "current_projects")
        value: The content for this field
    """
    key = key.strip().lower().replace(" ", "_")
    if not key:
        return "Error: key cannot be empty."
    if len(value) > 2000:
        return "Error: value too long (max 2000 characters). Be concise."

    db = _get_db()
    db.set_profile_field(key, value)
    return f"Profile field '{key}' updated successfully."


@mcp.tool()
def delete_profile_field(key: str) -> str:
    """Remove a field from the user's profile context.

    Args:
        key: The profile field name to remove
    """
    db = _get_db()
    if db.delete_profile_field(key.strip().lower()):
        return f"Profile field '{key}' deleted."
    return f"Profile field '{key}' not found."


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
