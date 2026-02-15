import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.db.models import APIUser, LifeDomain, Note, NoteSearchResult, NoteSource

# Use synchronous sqlite3 for simplicity and reliability on a single-user VPS.
# aiosqlite adds complexity without meaningful benefit at this scale.


class Database:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.connect()
        return self._conn

    def _create_tables(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                summary TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                domain TEXT DEFAULT 'other',
                action_items TEXT DEFAULT '[]',
                related_note_ids TEXT DEFAULT '[]',
                calendar_event_id TEXT,
                telegram_message_id INTEGER,
                audio_duration_seconds REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                hashed_password TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                raw_text, summary, tags, content='notes', content_rowid='id'
            );

            CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
                INSERT INTO notes_fts(rowid, raw_text, summary, tags)
                VALUES (new.id, new.raw_text, new.summary, new.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
                INSERT INTO notes_fts(notes_fts, rowid, raw_text, summary, tags)
                VALUES ('delete', old.id, old.raw_text, old.summary, old.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
                INSERT INTO notes_fts(notes_fts, rowid, raw_text, summary, tags)
                VALUES ('delete', old.id, old.raw_text, old.summary, old.tags);
                INSERT INTO notes_fts(rowid, raw_text, summary, tags)
                VALUES (new.id, new.raw_text, new.summary, new.tags);
            END;

            CREATE INDEX IF NOT EXISTS idx_notes_domain ON notes(domain);
            CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes(created_at);
        """)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _row_to_note(self, row: sqlite3.Row) -> Note:
        return Note(
            id=row["id"],
            source=NoteSource(row["source"]),
            raw_text=row["raw_text"],
            summary=row["summary"],
            tags=json.loads(row["tags"]),
            domain=LifeDomain(row["domain"]),
            action_items=json.loads(row["action_items"]),
            related_note_ids=json.loads(row["related_note_ids"]),
            calendar_event_id=row["calendar_event_id"],
            telegram_message_id=row["telegram_message_id"],
            audio_duration_seconds=row["audio_duration_seconds"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # --- Notes ---

    def create_note(self, note: Note) -> Note:
        now = self._now()
        cursor = self.conn.execute(
            """INSERT INTO notes
               (source, raw_text, summary, tags, domain, action_items,
                related_note_ids, calendar_event_id, telegram_message_id,
                audio_duration_seconds, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                note.source.value,
                note.raw_text,
                note.summary,
                json.dumps(note.tags),
                note.domain.value,
                json.dumps(note.action_items),
                json.dumps(note.related_note_ids),
                note.calendar_event_id,
                note.telegram_message_id,
                note.audio_duration_seconds,
                now,
                now,
            ),
        )
        self.conn.commit()
        note.id = cursor.lastrowid
        note.created_at = datetime.fromisoformat(now)
        note.updated_at = datetime.fromisoformat(now)
        return note

    def get_note(self, note_id: int) -> Note | None:
        row = self.conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return self._row_to_note(row) if row else None

    def update_note(self, note: Note) -> Note:
        now = self._now()
        self.conn.execute(
            """UPDATE notes SET
               summary = ?, tags = ?, domain = ?, action_items = ?,
               related_note_ids = ?, calendar_event_id = ?, updated_at = ?
               WHERE id = ?""",
            (
                note.summary,
                json.dumps(note.tags),
                note.domain.value,
                json.dumps(note.action_items),
                json.dumps(note.related_note_ids),
                note.calendar_event_id,
                now,
                note.id,
            ),
        )
        self.conn.commit()
        note.updated_at = datetime.fromisoformat(now)
        return note

    def delete_note(self, note_id: int) -> bool:
        cursor = self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def list_notes(
        self,
        domain: LifeDomain | None = None,
        tag: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Note]:
        query = "SELECT * FROM notes WHERE 1=1"
        params: list = []

        if domain:
            query += " AND domain = ?"
            params.append(domain.value)
        if tag:
            query += " AND tags LIKE ?"
            params.append(f'%"{tag}"%')

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_note(row) for row in rows]

    def search_notes(self, query: str, limit: int = 20) -> list[NoteSearchResult]:
        rows = self.conn.execute(
            """SELECT notes.*, rank
               FROM notes_fts
               JOIN notes ON notes.id = notes_fts.rowid
               WHERE notes_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit),
        ).fetchall()

        results = []
        for row in rows:
            note = self._row_to_note(row)
            snippet = note.raw_text[:200] + "..." if len(note.raw_text) > 200 else note.raw_text
            results.append(
                NoteSearchResult(note=note, relevance_score=abs(row["rank"]), snippet=snippet)
            )
        return results

    def get_recent_notes(self, limit: int = 10) -> list[Note]:
        rows = self.conn.execute(
            "SELECT * FROM notes ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_note(row) for row in rows]

    def get_notes_by_domain(self, domain: LifeDomain) -> list[Note]:
        return self.list_notes(domain=domain)

    def get_action_items(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, action_items, created_at FROM notes WHERE action_items != '[]' ORDER BY created_at DESC"
        ).fetchall()
        items = []
        for row in rows:
            for action in json.loads(row["action_items"]):
                items.append({"note_id": row["id"], "action": action, "created_at": row["created_at"]})
        return items

    # --- Users ---

    def create_user(self, user: APIUser) -> APIUser:
        now = self._now()
        cursor = self.conn.execute(
            "INSERT INTO users (username, hashed_password, is_active, created_at) VALUES (?, ?, ?, ?)",
            (user.username, user.hashed_password, user.is_active, now),
        )
        self.conn.commit()
        user.id = cursor.lastrowid
        user.created_at = datetime.fromisoformat(now)
        return user

    def get_user_by_username(self, username: str) -> APIUser | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not row:
            return None
        return APIUser(
            id=row["id"],
            username=row["username"],
            hashed_password=row["hashed_password"],
            is_active=bool(row["is_active"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
