import io
import json
import logging
from typing import TYPE_CHECKING

import anthropic
import openai

from src.config import settings
from src.db.models import LifeDomain, Note

if TYPE_CHECKING:
    from src.db.tag_registry import TagRegistry

logger = logging.getLogger(__name__)

BASE_SYSTEM_PROMPT = """You are a personal note-taking assistant. You process voice and text notes
from a user who takes DIVERSE notes across ALL areas of their life — work, personal, health,
creative projects, finances, learning, social, and more.

The user runs a company called Devalok and is the Founding First Officer (now advisory) of PIERA —
the India Policy & Economy Research Club at Arizona State University, also helping ops in India.

Your job is to analyze each note and return a JSON object with:
- "summary": A concise 1-2 sentence summary of the note
- "tags": A list of relevant hierarchical tags (2-6 tags, lowercase, no #)
- "domain": One of: work, personal, health, finance, creative, learning, social, other
- "action_items": A list of any action items or todos mentioned (empty list if none)
- "related_keywords": 3-5 keywords for finding related notes later

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
    def __init__(self, tag_registry: "TagRegistry | None" = None):
        self._openai: openai.OpenAI | None = None
        self._anthropic: anthropic.Anthropic | None = None
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
        """Build system prompt with current tag vocabulary injected."""
        prompt = BASE_SYSTEM_PROMPT
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

    def process_note(self, raw_text: str) -> dict:
        """Use Claude to summarize, tag, and extract action items from a note."""
        message = self.anthropic_client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            system=self._build_system_prompt(),
            messages=[
                {
                    "role": "user",
                    "content": f"Process this note and return ONLY valid JSON:\n\n{raw_text}",
                }
            ],
        )
        response_text = message.content[0].text

        # Extract JSON from response (handle markdown code blocks)
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]

        try:
            return json.loads(response_text.strip())
        except json.JSONDecodeError:
            logger.error("Failed to parse AI response: %s", response_text)
            return {
                "summary": raw_text[:200],
                "tags": [],
                "domain": "other",
                "action_items": [],
                "related_keywords": [],
            }

    def enrich_note(self, note: Note) -> Note:
        """Process a note with AI and fill in summary, tags, domain, action items."""
        from src.db.tag_registry import validate_tag

        result = self.process_note(note.raw_text)
        note.summary = result.get("summary", "")
        note.action_items = result.get("action_items", [])

        # Validate and normalize tags
        raw_tags = result.get("tags", [])
        validated_tags = []
        for tag in raw_tags:
            try:
                validated_tags.append(validate_tag(tag))
            except ValueError:
                logger.warning("AI produced invalid tag '%s', skipping", tag)
        note.tags = validated_tags

        # Register new tags and update usage counts
        if self.tag_registry and validated_tags:
            self.tag_registry.register_tags_from_note(validated_tags)

        domain_str = result.get("domain", "other")
        try:
            note.domain = LifeDomain(domain_str)
        except ValueError:
            note.domain = LifeDomain.OTHER

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
