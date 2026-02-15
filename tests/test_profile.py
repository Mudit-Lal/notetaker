import tempfile
from pathlib import Path

import pytest

from src.db.database import Database


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        database = Database(Path(tmpdir) / "test.db")
        database.connect()
        yield database
        database.close()


class TestUserProfile:
    def test_get_empty_profile(self, db):
        profile = db.get_profile()
        assert profile == {}

    def test_get_missing_field(self, db):
        assert db.get_profile_field("bio") is None

    def test_set_and_get_profile_field(self, db):
        db.set_profile_field("bio", "Test bio")
        assert db.get_profile_field("bio") == "Test bio"

    def test_upsert_overwrites(self, db):
        db.set_profile_field("bio", "Old bio")
        db.set_profile_field("bio", "New bio")
        assert db.get_profile_field("bio") == "New bio"

    def test_delete_profile_field(self, db):
        db.set_profile_field("bio", "Test bio")
        assert db.delete_profile_field("bio") is True
        assert db.get_profile_field("bio") is None

    def test_delete_nonexistent_field(self, db):
        assert db.delete_profile_field("nope") is False

    def test_get_full_profile(self, db):
        db.set_profile_field("bio", "Test bio")
        db.set_profile_field("interests", "AI, fitness")
        db.set_profile_field("companies", "Devalok")

        profile = db.get_profile()
        assert len(profile) == 3
        assert profile["bio"] == "Test bio"
        assert profile["interests"] == "AI, fitness"
        assert profile["companies"] == "Devalok"

    def test_profile_keys_sorted(self, db):
        db.set_profile_field("interests", "AI")
        db.set_profile_field("bio", "Hello")
        db.set_profile_field("companies", "Devalok")

        profile = db.get_profile()
        keys = list(profile.keys())
        assert keys == sorted(keys)
