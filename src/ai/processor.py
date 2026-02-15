from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING

import anthropic
import openai

from src.config import settings
from src.db.models import LifeDomain, MessageType, Note, Todo, TodoPriority, TodoStatus

if TYPE_CHECKING:
    from src.db.database import Database
    from src.db.tag_registry import TagRegistry

logger = logging.getLogger(__name__)

BASE_SYSTEM_PROMPT = """You are a personal note-taking and task management assistant. You process voice and text
messages from a user who takes DIVERSE notes and gives task instructions across ALL areas of their life —
work, personal, health, creative projects, finances, learning, social, and more.

STEP 1 — CLASSIFY THE MESSAGE:
Determine the "message_type" based on the user's INTENT:
- "todo": The message is primarily about things to DO. Tasks, reminders, errands, things to follow up on.
  Examples: "I need to call Bob tomorrow", "Pick up groceries", "Remind me to send the invoice",
  "Book flights for Delhi trip", "Follow up with Kaizen Waste on the website mockups"
- "note": The message is primarily informational — observations, thoughts, meeting summaries, ideas,
  journal entries, reflections, learnings. No clear action needed.
  Examples: "Had a great meeting with the DIVINI team today, they loved the packaging concepts",
  "Thinking about the connection between Sharira Traya and modern psychology",
  "Rida's birthday is Feb 3rd" (this is informational, not a task)
- "note_with_todos": The message contains BOTH informational content AND embedded action items.
  Examples: "Met with Kaizen Waste team — they want to revamp the website. I need to send them
  the mockups by Friday and schedule a follow-up call", "Great gym session today, hit a PR on
  deadlifts. Need to buy more protein powder and book next physio appointment"

STEP 2 — RETURN JSON based on message_type:

If message_type is "note" or "note_with_todos", include:
- "summary": A concise 1-2 sentence summary of the note content
- "tags": A list of relevant hierarchical tags (2-6 tags, lowercase, no #)
- "domain": One of: work, personal, health, finance, creative, learning, social, other
- "related_keywords": 3-5 keywords for finding related notes later

If message_type is "todo" or "note_with_todos", include:
- "todos": A list of todo objects, each with:
  - "text": The actionable task, written as a clear imperative (e.g. "Send mockups to Kaizen Waste")
  - "priority": "high", "medium", or "low" — infer from urgency/importance cues
  - "due_date": A date string if mentioned or implied (e.g. "tomorrow", "Friday", "Feb 20"), or null

If message_type is "todo" (pure task, no note), ALSO include:
- "tags": Tags for the todo items (2-4 tags)
- "domain": The life domain for the tasks

ALWAYS include:
- "message_type": one of "note", "todo", "note_with_todos"

TAG FORMAT RULES:
- Tags use slash-separated hierarchy: "parent/child" (max 2 levels)
- Examples: "devalok/hiring", "health/gym", "piera/events", "work"
- Top-level tags are also valid on their own: "work", "devalok", "piera"
- PREFER existing tags from the vocabulary below when they fit
- You MAY create new sub-tags when needed (e.g. "devalok/marketing" if no existing tag fits)
- New sub-tags should be under an existing top-level parent when possible
- Keep tags concise: lowercase, alphanumeric and hyphens only

Be precise. Don't invent information not in the note. Match the user's intent."""


class AIProcessor:
    def __init__(
        self,
        db: Database | None = None,
        tag_registry: TagRegistry | None = None,
    ):
        self._openai: openai.OpenAI | None = None
        self._anthropic: anthropic.Anthropic | None = None
        self.db = db
        self.tag_registry = tag_registry

    @property
    def openai_client(self) -> openai.OpenAI:
        if self._openai is None:
            self._openai = openai.OpenAI(api_key=settings.openai_api_key)
        return self._openai

    @property
    def anthropic_client(self) -> anthropic.Anthropic:
        if self._anthropic is None:
            self._anthropic = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._anthropic

    def _build_system_prompt(self) -> str:
        """Build system prompt with user profile and tag vocabulary injected."""
        prompt = BASE_SYSTEM_PROMPT

        # Inject user profile context
        if self.db:
            profile = self.db.get_profile()
            if profile:
                profile_lines = []
                key_order = [
                    "bio", "companies", "current_projects", "interests",
                    "key_people", "devalok_vocabulary", "clients", "note_context",
                ]
                for key in key_order:
                    if key in profile:
                        label = key.replace("_", " ").title()
                        profile_lines.append(f"- {label}: {profile[key]}")
                for key, value in profile.items():
                    if key not in key_order:
                        label = key.replace("_", " ").title()
                        profile_lines.append(f"- {label}: {value}")
                if profile_lines:
                    prompt += "\n\nUSER CONTEXT:\n" + "\n".join(profile_lines)

        # Inject tag vocabulary
        if self.tag_registry:
            tag_context = self.tag_registry.format_tag_context_for_ai()
            if tag_context:
                prompt += f"\n\nEXISTING TAG VOCABULARY:\n{tag_context}"

        return prompt

    def transcribe_audio(self, audio_bytes: bytes, filename: str = "voice.ogg") -> str:
        """Transcribe audio using OpenAI Whisper API."""
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = filename
        transcript = self.openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="text",
        )
        return transcript.strip()

    def _parse_json_response(self, response_text: str) -> dict | None:
        """Extract and parse JSON from Claude's response."""
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]
        try:
            return json.loads(response_text.strip())
        except json.JSONDecodeError:
            logger.error("Failed to parse AI response: %s", response_text)
            return None

    def process_message(self, raw_text: str) -> dict:
        """Use Claude to classify, summarize, tag, and extract todos from a message."""
        message = self.anthropic_client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            system=self._build_system_prompt(),
            messages=[
                {
                    "role": "user",
                    "content": f"Process this message and return ONLY valid JSON:\n\n{raw_text}",
                }
            ],
        )
        result = self._parse_json_response(message.content[0].text)
        if result is None:
            return {
                "message_type": "note",
                "summary": raw_text[:200],
                "tags": [],
                "domain": "other",
                "todos": [],
                "related_keywords": [],
            }
        # Normalize: ensure message_type always present
        if "message_type" not in result:
            result["message_type"] = "note"
        return result

    def _validate_tags(self, raw_tags: list[str]) -> list[str]:
        """Validate and register tags, returning only valid ones."""
        from src.db.tag_registry import validate_tag

        validated = []
        for tag in raw_tags:
            try:
                validated.append(validate_tag(tag))
            except ValueError:
                logger.warning("AI produced invalid tag '%s', skipping", tag)
        if self.tag_registry and validated:
            self.tag_registry.register_tags_from_note(validated)
        return validated

    def _parse_domain(self, domain_str: str) -> LifeDomain:
        try:
            return LifeDomain(domain_str)
        except ValueError:
            return LifeDomain.OTHER

    def enrich_message(self, raw_text: str, source: str) -> dict:
        """Classify a message and return structured result with note/todos/both.

        Returns dict with keys:
            message_type: "note" | "todo" | "note_with_todos"
            note: Note | None  (populated for "note" and "note_with_todos")
            todos: list[Todo]  (populated for "todo" and "note_with_todos")
        """
        from src.db.models import NoteSource

        result = self.process_message(raw_text)
        msg_type_str = result.get("message_type", "note")
        try:
            msg_type = MessageType(msg_type_str)
        except ValueError:
            msg_type = MessageType.NOTE

        note_source = NoteSource(source) if isinstance(source, str) else source
        tags = self._validate_tags(result.get("tags", []))
        domain = self._parse_domain(result.get("domain", "other"))

        output: dict = {"message_type": msg_type, "note": None, "todos": []}

        # Build Note for "note" and "note_with_todos"
        if msg_type in (MessageType.NOTE, MessageType.NOTE_WITH_TODOS):
            # Collect todo texts as action_items on the note for backwards compatibility
            todo_texts = [t["text"] for t in result.get("todos", []) if isinstance(t, dict) and "text" in t]
            output["note"] = Note(
                source=note_source,
                raw_text=raw_text,
                summary=result.get("summary", ""),
                tags=tags,
                domain=domain,
                action_items=todo_texts,
            )

        # Build Todo objects for "todo" and "note_with_todos"
        if msg_type in (MessageType.TODO, MessageType.NOTE_WITH_TODOS):
            raw_todos = result.get("todos", [])
            for t in raw_todos:
                if not isinstance(t, dict) or "text" not in t:
                    continue
                priority_str = t.get("priority", "medium")
                try:
                    priority = TodoPriority(priority_str)
                except ValueError:
                    priority = TodoPriority.MEDIUM
                output["todos"].append(Todo(
                    text=t["text"],
                    priority=priority,
                    domain=domain,
                    tags=tags,
                    source=note_source,
                    due_date=t.get("due_date"),
                ))

        # Pure todo with no note — if AI returned a summary, ignore it (no note to save)
        if msg_type == MessageType.TODO and not output["todos"]:
            # Fallback: treat the whole message as a single todo
            output["todos"].append(Todo(
                text=raw_text,
                priority=TodoPriority.MEDIUM,
                domain=domain,
                tags=tags,
                source=note_source,
            ))

        return output

    # Keep backwards-compatible method for API/direct callers
    def enrich_note(self, note: Note) -> Note:
        """Process a note with AI and fill in summary, tags, domain, action items."""
        result = self.process_message(note.raw_text)
        note.summary = result.get("summary", "")

        # Collect action items from todos array (new format) or action_items (old format)
        todos = result.get("todos", [])
        if todos:
            note.action_items = [t["text"] for t in todos if isinstance(t, dict) and "text" in t]
        else:
            note.action_items = result.get("action_items", [])

        note.tags = self._validate_tags(result.get("tags", []))
        note.domain = self._parse_domain(result.get("domain", "other"))
        return note

    def find_related_notes(self, note: Note, existing_notes: list[Note], limit: int = 5) -> list[int]:
        """Use Claude to identify which existing notes are related to a new note."""
        if not existing_notes:
            return []

        notes_context = "\n".join(
            f"[ID:{n.id}] {n.summary or n.raw_text[:100]}" for n in existing_notes[:50]
        )

        message = self.anthropic_client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=256,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Given this new note:\n\"{note.raw_text[:500]}\"\n\n"
                        f"Which of these existing notes are most related? "
                        f"Return ONLY a JSON array of note IDs (max {limit}), e.g. [1, 5, 12]. "
                        f"Return [] if none are related.\n\n{notes_context}"
                    ),
                }
            ],
        )

        try:
            text = message.content[0].text.strip()
            if "```" in text:
                text = text.split("```")[1].split("```")[0].replace("json", "").strip()
            ids = json.loads(text)
            return [i for i in ids if isinstance(i, int)][:limit]
        except (json.JSONDecodeError, KeyError):
            return []
