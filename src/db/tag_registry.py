"""Tag registry — tracks hierarchical tag vocabulary and usage."""

import re
import sqlite3
from datetime import datetime, timezone

from src.db.models import Tag


def validate_tag(tag: str) -> str:
    """Normalize and validate a hierarchical tag.

    Rules: lowercase, max 2 levels (one slash), alphanumeric + hyphens per segment.
    Returns normalized tag or raises ValueError.
    """
    tag = tag.strip().lower()
    parts = tag.split("/")
    if len(parts) > 2:
        raise ValueError(f"Tag '{tag}' exceeds max 2 levels")
    for part in parts:
        if not re.match(r"^[a-z0-9][a-z0-9-]*$", part):
            raise ValueError(f"Invalid tag segment: '{part}'")
    return "/".join(parts)


def parse_tag_parent(tag: str) -> str | None:
    """Return parent portion or None. 'devalok/hiring' -> 'devalok'."""
    parts = tag.split("/")
    return parts[0] if len(parts) == 2 else None


class TagRegistry:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def ensure_tables(self) -> None:
        """Create tags table if it doesn't exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                parent TEXT,
                description TEXT DEFAULT '',
                auto_created INTEGER DEFAULT 0,
                usage_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tags_parent ON tags(parent);
            CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name);
        """)

    def seed_tags(self, tags: list[dict]) -> None:
        """Insert seed tags idempotently (INSERT OR IGNORE)."""
        now = datetime.now(timezone.utc).isoformat()
        for tag_def in tags:
            name = tag_def["name"]
            parent = parse_tag_parent(name)
            description = tag_def.get("description", "")
            self.conn.execute(
                """INSERT OR IGNORE INTO tags (name, parent, description, auto_created, usage_count, created_at, updated_at)
                   VALUES (?, ?, ?, 0, 0, ?, ?)""",
                (name, parent, description, now, now),
            )
        self.conn.commit()

    def get_all_tags(self) -> list[Tag]:
        """Return all registered tags ordered by usage count descending."""
        rows = self.conn.execute(
            "SELECT * FROM tags ORDER BY usage_count DESC, name"
        ).fetchall()
        return [self._row_to_tag(row) for row in rows]

    def get_tag(self, name: str) -> Tag | None:
        """Look up a single tag by full name."""
        row = self.conn.execute("SELECT * FROM tags WHERE name = ?", (name,)).fetchone()
        return self._row_to_tag(row) if row else None

    def get_children(self, parent: str) -> list[Tag]:
        """Return all sub-tags of a given parent."""
        rows = self.conn.execute(
            "SELECT * FROM tags WHERE parent = ? ORDER BY usage_count DESC", (parent,)
        ).fetchall()
        return [self._row_to_tag(row) for row in rows]

    def get_top_level_tags(self) -> list[Tag]:
        """Return all top-level tags (no parent)."""
        rows = self.conn.execute(
            "SELECT * FROM tags WHERE parent IS NULL ORDER BY usage_count DESC, name"
        ).fetchall()
        return [self._row_to_tag(row) for row in rows]

    def register_tag(self, name: str, description: str = "", auto_created: bool = True) -> Tag:
        """Register a new tag. Auto-creates parent if needed. Returns existing tag if present."""
        now = datetime.now(timezone.utc).isoformat()
        parent = parse_tag_parent(name)

        # Ensure parent exists first
        if parent:
            self.conn.execute(
                """INSERT OR IGNORE INTO tags (name, parent, description, auto_created, usage_count, created_at, updated_at)
                   VALUES (?, NULL, '', ?, 0, ?, ?)""",
                (parent, int(auto_created), now, now),
            )

        # Upsert the tag itself
        self.conn.execute(
            """INSERT INTO tags (name, parent, description, auto_created, usage_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, 0, ?, ?)
               ON CONFLICT(name) DO NOTHING""",
            (name, parent, description, int(auto_created), now, now),
        )
        self.conn.commit()
        return self.get_tag(name)

    def register_tags_from_note(self, tags: list[str]) -> None:
        """Register tags from a processed note. Creates new tags and increments usage counts."""
        now = datetime.now(timezone.utc).isoformat()
        for tag in tags:
            parent = parse_tag_parent(tag)

            # Ensure parent exists
            if parent:
                self.conn.execute(
                    """INSERT OR IGNORE INTO tags (name, parent, description, auto_created, usage_count, created_at, updated_at)
                       VALUES (?, NULL, '', 0, 0, ?, ?)""",
                    (parent, now, now),
                )

            # Upsert: create if new, increment usage_count always
            self.conn.execute(
                """INSERT INTO tags (name, parent, description, auto_created, usage_count, created_at, updated_at)
                   VALUES (?, ?, '', 1, 1, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       usage_count = usage_count + 1,
                       updated_at = ?""",
                (tag, parent, now, now, now),
            )

        self.conn.commit()

    def format_tag_context_for_ai(self) -> str:
        """Build compact tag tree string for inclusion in the AI system prompt."""
        rows = self.conn.execute(
            "SELECT name, parent, usage_count FROM tags ORDER BY usage_count DESC"
        ).fetchall()

        if not rows:
            return ""

        # Group children under parents
        top_level = []
        children: dict[str, list[tuple[str, int]]] = {}

        for row in rows:
            name, parent, count = row["name"], row["parent"], row["usage_count"]
            if parent is None:
                top_level.append((name, count))
            else:
                leaf = name.split("/")[-1]
                children.setdefault(parent, []).append((leaf, count))

        lines = []
        for name, count in top_level:
            kids = children.get(name, [])
            if kids:
                kid_str = ", ".join(k[0] for k in kids)
                lines.append(f"{name}/ ({count} uses): {kid_str}")
            else:
                lines.append(f"{name} ({count} uses)")

        return "\n".join(lines)

    def _row_to_tag(self, row: sqlite3.Row) -> Tag:
        return Tag(
            id=row["id"],
            name=row["name"],
            parent=row["parent"],
            description=row["description"],
            auto_created=bool(row["auto_created"]),
            usage_count=row["usage_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
