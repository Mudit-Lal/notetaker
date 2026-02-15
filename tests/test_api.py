import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.db.database import Database
from src.db.seed_tags import SEED_TAGS
from src.db.tag_registry import TagRegistry
from src.main import create_api


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.connect()
        tag_registry = TagRegistry(db.conn)
        tag_registry.ensure_tables()
        tag_registry.seed_tags(SEED_TAGS)
        app = create_api(db, tag_registry)
        with TestClient(app) as c:
            yield c
        db.close()


@pytest.fixture
def auth_client(client):
    """Client with a registered and authenticated user."""
    client.post("/api/v1/auth/register", json={"username": "testuser", "password": "testpass123"})
    resp = client.post("/api/v1/auth/token", json={"username": "testuser", "password": "testpass123"})
    token = resp.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


class TestHealth:
    def test_health_check(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestAuth:
    def test_register(self, client):
        resp = client.post("/api/v1/auth/register", json={"username": "newuser", "password": "password123"})
        assert resp.status_code == 201
        assert "access_token" in resp.json()

    def test_register_duplicate(self, client):
        client.post("/api/v1/auth/register", json={"username": "dupe", "password": "password123"})
        resp = client.post("/api/v1/auth/register", json={"username": "dupe", "password": "password456"})
        assert resp.status_code == 409

    def test_login(self, client):
        client.post("/api/v1/auth/register", json={"username": "loginuser", "password": "password123"})
        resp = client.post("/api/v1/auth/token", json={"username": "loginuser", "password": "password123"})
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_login_wrong_password(self, client):
        client.post("/api/v1/auth/register", json={"username": "user1", "password": "password123"})
        resp = client.post("/api/v1/auth/token", json={"username": "user1", "password": "wrong"})
        assert resp.status_code == 401

    def test_unauthenticated_access(self, client):
        resp = client.get("/api/v1/notes")
        assert resp.status_code in (401, 403)


class TestNotes:
    def test_create_note(self, auth_client):
        resp = auth_client.post("/api/v1/notes", json={"raw_text": "My first note"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["raw_text"] == "My first note"
        assert data["id"] is not None

    def test_list_notes(self, auth_client):
        auth_client.post("/api/v1/notes", json={"raw_text": "Note 1"})
        auth_client.post("/api/v1/notes", json={"raw_text": "Note 2"})

        resp = auth_client.get("/api/v1/notes")
        assert resp.status_code == 200
        assert len(resp.json()["notes"]) == 2

    def test_get_note(self, auth_client):
        create_resp = auth_client.post("/api/v1/notes", json={"raw_text": "Specific note"})
        note_id = create_resp.json()["id"]

        resp = auth_client.get(f"/api/v1/notes/{note_id}")
        assert resp.status_code == 200
        assert resp.json()["raw_text"] == "Specific note"

    def test_get_nonexistent_note(self, auth_client):
        resp = auth_client.get("/api/v1/notes/999")
        assert resp.status_code == 404

    def test_update_note(self, auth_client):
        create_resp = auth_client.post("/api/v1/notes", json={"raw_text": "Update me"})
        note_id = create_resp.json()["id"]

        resp = auth_client.patch(
            f"/api/v1/notes/{note_id}",
            json={"summary": "Updated summary", "tags": ["work", "urgent"]},
        )
        assert resp.status_code == 200
        assert resp.json()["summary"] == "Updated summary"
        assert resp.json()["tags"] == ["work", "urgent"]

    def test_delete_note(self, auth_client):
        create_resp = auth_client.post("/api/v1/notes", json={"raw_text": "Delete me"})
        note_id = create_resp.json()["id"]

        resp = auth_client.delete(f"/api/v1/notes/{note_id}")
        assert resp.status_code == 200

        resp = auth_client.get(f"/api/v1/notes/{note_id}")
        assert resp.status_code == 404

    def test_search_notes(self, auth_client):
        auth_client.post("/api/v1/notes", json={"raw_text": "Meeting about the new product launch"})
        auth_client.post("/api/v1/notes", json={"raw_text": "Grocery shopping list for the weekend"})

        resp = auth_client.get("/api/v1/notes/search", params={"q": "product"})
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_filter_by_domain(self, auth_client):
        auth_client.post("/api/v1/notes", json={"raw_text": "Work stuff", "domain": "work"})
        auth_client.post("/api/v1/notes", json={"raw_text": "Health stuff", "domain": "health"})

        resp = auth_client.get("/api/v1/notes", params={"domain": "work"})
        assert resp.status_code == 200
        assert len(resp.json()["notes"]) == 1

    def test_action_items(self, auth_client):
        create_resp = auth_client.post("/api/v1/notes", json={"raw_text": "Need to do things"})
        note_id = create_resp.json()["id"]
        auth_client.patch(
            f"/api/v1/notes/{note_id}",
            json={"action_items": ["Call the dentist", "Buy milk"]},
        )

        resp = auth_client.get("/api/v1/notes/actions")
        assert resp.status_code == 200
        assert len(resp.json()) == 2


class TestTags:
    def test_list_all_tags(self, auth_client):
        resp = auth_client.get("/api/v1/tags")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == len(SEED_TAGS)
        tag_names = [t["name"] for t in data["tags"]]
        assert "devalok" in tag_names
        assert "piera" in tag_names
        assert "devalok/hiring" in tag_names

    def test_list_tags_by_parent(self, auth_client):
        resp = auth_client.get("/api/v1/tags", params={"parent": "devalok"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        for tag in data["tags"]:
            assert tag["parent"] == "devalok"

    def test_tag_tree(self, auth_client):
        resp = auth_client.get("/api/v1/tags/tree")
        assert resp.status_code == 200
        tree = resp.json()["tree"]
        assert len(tree) > 0

        # Find devalok in tree
        devalok = next((t for t in tree if t["name"] == "devalok"), None)
        assert devalok is not None
        assert len(devalok["children"]) > 0
        child_names = [c["name"] for c in devalok["children"]]
        assert "devalok/hiring" in child_names

    def test_tag_tree_piera(self, auth_client):
        resp = auth_client.get("/api/v1/tags/tree")
        tree = resp.json()["tree"]

        piera = next((t for t in tree if t["name"] == "piera"), None)
        assert piera is not None
        child_names = [c["name"] for c in piera["children"]]
        assert "piera/events" in child_names
        assert "piera/ops-india" in child_names
