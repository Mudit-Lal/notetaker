import tempfile
from pathlib import Path

import pytest

from src.db.database import Database
from src.db.models import LifeDomain, Note, NoteSource
from src.db.seed_tags import SEED_TAGS
from src.db.tag_registry import TagRegistry, parse_tag_parent, validate_tag


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        database = Database(Path(tmpdir) / "test.db")
        database.connect()
        yield database
        database.close()


@pytest.fixture
def registry(db):
    reg = TagRegistry(db.conn)
    reg.ensure_tables()
    return reg


class TestValidateTag:
    def test_simple_tag(self):
        assert validate_tag("work") == "work"

    def test_hierarchical_tag(self):
        assert validate_tag("devalok/hiring") == "devalok/hiring"

    def test_normalizes_case(self):
        assert validate_tag("Devalok/Hiring") == "devalok/hiring"

    def test_strips_whitespace(self):
        assert validate_tag("  work  ") == "work"
        assert validate_tag(" devalok/hiring ") == "devalok/hiring"

    def test_rejects_three_levels(self):
        with pytest.raises(ValueError, match="exceeds max 2 levels"):
            validate_tag("a/b/c")

    def test_rejects_special_chars(self):
        with pytest.raises(ValueError):
            validate_tag("work!")
        with pytest.raises(ValueError):
            validate_tag("work/my tag")

    def test_allows_hyphens(self):
        assert validate_tag("piera/ops-india") == "piera/ops-india"

    def test_rejects_leading_hyphen(self):
        with pytest.raises(ValueError):
            validate_tag("-work")

    def test_rejects_empty_segment(self):
        with pytest.raises(ValueError):
            validate_tag("/work")


class TestParseTagParent:
    def test_top_level_returns_none(self):
        assert parse_tag_parent("work") is None

    def test_child_returns_parent(self):
        assert parse_tag_parent("devalok/hiring") == "devalok"


class TestTagRegistry:
    def test_ensure_tables(self, registry):
        # Should not raise
        registry.ensure_tables()

    def test_seed_tags(self, registry):
        registry.seed_tags(SEED_TAGS)
        all_tags = registry.get_all_tags()
        assert len(all_tags) == len(SEED_TAGS)

    def test_seed_tags_idempotent(self, registry):
        registry.seed_tags(SEED_TAGS)
        registry.seed_tags(SEED_TAGS)
        all_tags = registry.get_all_tags()
        assert len(all_tags) == len(SEED_TAGS)

    def test_get_tag(self, registry):
        registry.seed_tags(SEED_TAGS)
        tag = registry.get_tag("devalok")
        assert tag is not None
        assert tag.name == "devalok"
        assert tag.parent is None

        child = registry.get_tag("devalok/hiring")
        assert child is not None
        assert child.parent == "devalok"

    def test_get_children(self, registry):
        registry.seed_tags(SEED_TAGS)
        children = registry.get_children("devalok")
        child_names = [c.name for c in children]
        assert "devalok/product" in child_names
        assert "devalok/hiring" in child_names
        assert "devalok/finance" in child_names

    def test_get_top_level_tags(self, registry):
        registry.seed_tags(SEED_TAGS)
        top = registry.get_top_level_tags()
        top_names = [t.name for t in top]
        assert "devalok" in top_names
        assert "piera" in top_names
        assert "work" in top_names
        # Sub-tags should NOT be in top-level
        assert "devalok/hiring" not in top_names

    def test_register_new_tag(self, registry):
        tag = registry.register_tag("newtag")
        assert tag is not None
        assert tag.name == "newtag"
        assert tag.auto_created is True

    def test_register_child_auto_creates_parent(self, registry):
        tag = registry.register_tag("project/backend")
        assert tag is not None
        assert tag.parent == "project"

        parent = registry.get_tag("project")
        assert parent is not None
        assert parent.parent is None

    def test_register_existing_tag_no_duplicate(self, registry):
        registry.register_tag("work")
        registry.register_tag("work")
        all_tags = registry.get_all_tags()
        work_tags = [t for t in all_tags if t.name == "work"]
        assert len(work_tags) == 1

    def test_register_tags_from_note(self, registry):
        registry.seed_tags(SEED_TAGS)

        # Simulate AI tagging a note with mix of existing and new tags
        tags = ["devalok/hiring", "devalok/marketing", "work"]
        registry.register_tags_from_note(tags)

        # Existing tag should have incremented usage
        hiring = registry.get_tag("devalok/hiring")
        assert hiring.usage_count == 1

        # New tag should be created
        marketing = registry.get_tag("devalok/marketing")
        assert marketing is not None
        assert marketing.auto_created is True
        assert marketing.parent == "devalok"
        assert marketing.usage_count == 1

        # Register same tags again
        registry.register_tags_from_note(tags)
        hiring = registry.get_tag("devalok/hiring")
        assert hiring.usage_count == 2

    def test_format_tag_context_for_ai(self, registry):
        registry.seed_tags(SEED_TAGS)

        # Simulate some usage
        registry.register_tags_from_note(["devalok/hiring", "work"])
        registry.register_tags_from_note(["devalok/hiring", "piera/events"])

        context = registry.format_tag_context_for_ai()
        assert "devalok/" in context
        assert "hiring" in context
        assert "piera/" in context
        assert "work" in context
        assert len(context) > 0

    def test_get_nonexistent_tag(self, registry):
        assert registry.get_tag("nonexistent") is None


class TestHierarchicalNoteQueries:
    """Test that list_notes supports hierarchical tag prefix matching."""

    def test_exact_subtag_match(self, db, registry):
        registry.seed_tags(SEED_TAGS)

        # Create notes with hierarchical tags
        db.create_note(Note(source=NoteSource.TEXT, raw_text="Hiring discussion", tags=["devalok/hiring"]))
        db.create_note(Note(source=NoteSource.TEXT, raw_text="Product roadmap", tags=["devalok/product"]))
        db.create_note(Note(source=NoteSource.TEXT, raw_text="Gym workout", tags=["health/gym"]))

        # Exact sub-tag match
        notes = db.list_notes(tag="devalok/hiring")
        assert len(notes) == 1
        assert "Hiring" in notes[0].raw_text

    def test_parent_tag_matches_children(self, db, registry):
        registry.seed_tags(SEED_TAGS)

        db.create_note(Note(source=NoteSource.TEXT, raw_text="Hiring discussion", tags=["devalok/hiring"]))
        db.create_note(Note(source=NoteSource.TEXT, raw_text="Product roadmap", tags=["devalok/product"]))
        db.create_note(Note(source=NoteSource.TEXT, raw_text="Gym workout", tags=["health/gym"]))

        # Top-level tag matches all children
        notes = db.list_notes(tag="devalok")
        assert len(notes) == 2

    def test_parent_tag_also_matches_exact(self, db, registry):
        registry.seed_tags(SEED_TAGS)

        db.create_note(Note(source=NoteSource.TEXT, raw_text="General devalok note", tags=["devalok"]))
        db.create_note(Note(source=NoteSource.TEXT, raw_text="Hiring discussion", tags=["devalok/hiring"]))

        notes = db.list_notes(tag="devalok")
        assert len(notes) == 2

    def test_no_false_positives(self, db, registry):
        registry.seed_tags(SEED_TAGS)

        db.create_note(Note(source=NoteSource.TEXT, raw_text="Note about dev", tags=["dev"]))
        db.create_note(Note(source=NoteSource.TEXT, raw_text="Devalok note", tags=["devalok/ops"]))

        # "dev" should NOT match "devalok/ops"
        notes = db.list_notes(tag="dev")
        assert len(notes) == 1
        assert "dev" in notes[0].tags
