import tempfile
from pathlib import Path

import pytest

from src.db.database import Database
from src.db.models import APIUser, LifeDomain, Note, NoteSource


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        database = Database(Path(tmpdir) / "test.db")
        database.connect()
        yield database
        database.close()


class TestNotes:
    def test_create_and_get_note(self, db):
        note = Note(source=NoteSource.TEXT, raw_text="Test note about a meeting")
        created = db.create_note(note)
        assert created.id is not None
        assert created.created_at is not None

        fetched = db.get_note(created.id)
        assert fetched is not None
        assert fetched.raw_text == "Test note about a meeting"
        assert fetched.source == NoteSource.TEXT

    def test_update_note(self, db):
        note = db.create_note(Note(source=NoteSource.TEXT, raw_text="Raw note"))
        note.summary = "A brief summary"
        note.tags = ["meeting", "work"]
        note.domain = LifeDomain.WORK
        note.action_items = ["Follow up with team"]

        updated = db.update_note(note)
        assert updated.summary == "A brief summary"
        assert updated.tags == ["meeting", "work"]
        assert updated.domain == LifeDomain.WORK

        fetched = db.get_note(note.id)
        assert fetched.tags == ["meeting", "work"]
        assert fetched.action_items == ["Follow up with team"]

    def test_delete_note(self, db):
        note = db.create_note(Note(source=NoteSource.TEXT, raw_text="Delete me"))
        assert db.delete_note(note.id) is True
        assert db.get_note(note.id) is None
        assert db.delete_note(999) is False

    def test_list_notes_with_filters(self, db):
        db.create_note(Note(source=NoteSource.TEXT, raw_text="Work note", domain=LifeDomain.WORK, tags=["project"]))
        db.create_note(Note(source=NoteSource.TEXT, raw_text="Health note", domain=LifeDomain.HEALTH, tags=["gym"]))
        db.create_note(Note(source=NoteSource.TEXT, raw_text="Another work note", domain=LifeDomain.WORK, tags=["meeting"]))

        # Update notes with proper domains since create_note uses what's passed
        work_notes = db.list_notes(domain=LifeDomain.WORK)
        assert len(work_notes) == 2

        health_notes = db.list_notes(domain=LifeDomain.HEALTH)
        assert len(health_notes) == 1

        tag_notes = db.list_notes(tag="project")
        assert len(tag_notes) == 1

    def test_search_notes(self, db):
        db.create_note(Note(source=NoteSource.TEXT, raw_text="Meeting with the design team about new UI"))
        db.create_note(Note(source=NoteSource.TEXT, raw_text="Grocery list: milk, eggs, bread"))
        db.create_note(Note(source=NoteSource.TEXT, raw_text="Design review for the mobile app"))

        results = db.search_notes("design")
        assert len(results) == 2

        results = db.search_notes("grocery")
        assert len(results) == 1
        assert "milk" in results[0].snippet

    def test_get_recent_notes(self, db):
        for i in range(15):
            db.create_note(Note(source=NoteSource.TEXT, raw_text=f"Note number {i}"))

        recent = db.get_recent_notes(10)
        assert len(recent) == 10
        # Most recent first
        assert "14" in recent[0].raw_text

    def test_get_action_items(self, db):
        note1 = db.create_note(Note(source=NoteSource.TEXT, raw_text="Note with actions"))
        note1.action_items = ["Call dentist", "Buy groceries"]
        db.update_note(note1)

        note2 = db.create_note(Note(source=NoteSource.TEXT, raw_text="No actions here"))

        items = db.get_action_items()
        assert len(items) == 2
        assert items[0]["action"] == "Call dentist"

    def test_voice_note_metadata(self, db):
        note = Note(
            source=NoteSource.VOICE,
            raw_text="Transcribed voice note",
            telegram_message_id=12345,
            audio_duration_seconds=15.5,
        )
        created = db.create_note(note)
        fetched = db.get_note(created.id)
        assert fetched.source == NoteSource.VOICE
        assert fetched.telegram_message_id == 12345
        assert fetched.audio_duration_seconds == 15.5


class TestUsers:
    def test_create_and_get_user(self, db):
        user = APIUser(username="testuser", hashed_password="hashed123")
        created = db.create_user(user)
        assert created.id is not None

        fetched = db.get_user_by_username("testuser")
        assert fetched is not None
        assert fetched.username == "testuser"

    def test_get_nonexistent_user(self, db):
        assert db.get_user_by_username("nobody") is None

    def test_duplicate_username(self, db):
        db.create_user(APIUser(username="dupe", hashed_password="hash1"))
        with pytest.raises(Exception):
            db.create_user(APIUser(username="dupe", hashed_password="hash2"))
