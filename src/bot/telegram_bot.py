import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.ai.processor import AIProcessor
from src.config import settings
from src.db.database import Database
from src.db.models import Note, NoteSource

if TYPE_CHECKING:
    from src.db.tag_registry import TagRegistry

logger = logging.getLogger(__name__)


class NoteTakerBot:
    def __init__(self, db: Database, ai: AIProcessor, tag_registry: "TagRegistry | None" = None):
        self.db = db
        self.ai = ai
        self.tag_registry = tag_registry

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Hey! I'm your note-taking assistant.\n\n"
            "Just send me a voice message or text and I'll capture, "
            "transcribe, summarize, and organize it.\n\n"
            "Commands:\n"
            "/recent - Show recent notes\n"
            "/search <query> - Search your notes\n"
            "/actions - Show pending action items\n"
            "/domains - List notes by life area\n"
            "/note <id> - View a specific note\n"
            "/tags - Browse tag hierarchy\n"
            "/tag <name> - Filter notes by tag"
        )

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming voice messages."""
        voice = update.message.voice
        status_msg = await update.message.reply_text("Transcribing your voice note...")

        try:
            # Download the voice file
            voice_file = await context.bot.get_file(voice.file_id)
            audio_bytes = await voice_file.download_as_bytearray()

            # Transcribe
            raw_text = self.ai.transcribe_audio(bytes(audio_bytes))
            if not raw_text:
                await status_msg.edit_text("Couldn't transcribe that. Try again?")
                return

            await status_msg.edit_text(f"Transcribed. Processing...\n\n\"{raw_text[:200]}\"")

            # Create and enrich note
            note = Note(
                source=NoteSource.VOICE,
                raw_text=raw_text,
                telegram_message_id=update.message.message_id,
                audio_duration_seconds=voice.duration,
            )
            note = self.ai.enrich_note(note)

            # Find related notes
            recent = self.db.get_recent_notes(50)
            note.related_note_ids = self.ai.find_related_notes(note, recent)

            # Save
            note = self.db.create_note(note)

            # Format response
            tags_str = " ".join(f"#{t}" for t in note.tags) if note.tags else "none"
            actions_str = "\n".join(f"  - {a}" for a in note.action_items) if note.action_items else "none"
            related_str = (
                ", ".join(f"#{rid}" for rid in note.related_note_ids)
                if note.related_note_ids
                else "none"
            )

            response = (
                f"Note #{note.id} saved!\n\n"
                f"Summary: {note.summary}\n"
                f"Domain: {note.domain.value}\n"
                f"Tags: {tags_str}\n"
                f"Actions: {actions_str}\n"
                f"Related: {related_str}"
            )
            await status_msg.edit_text(response)

        except Exception as e:
            logger.exception("Error processing voice note")
            await status_msg.edit_text(f"Error processing voice note: {e}")

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming text messages as notes."""
        raw_text = update.message.text
        if not raw_text or raw_text.startswith("/"):
            return

        status_msg = await update.message.reply_text("Processing your note...")

        try:
            note = Note(
                source=NoteSource.TEXT,
                raw_text=raw_text,
                telegram_message_id=update.message.message_id,
            )
            note = self.ai.enrich_note(note)

            recent = self.db.get_recent_notes(50)
            note.related_note_ids = self.ai.find_related_notes(note, recent)

            note = self.db.create_note(note)

            tags_str = " ".join(f"#{t}" for t in note.tags) if note.tags else "none"
            actions_str = "\n".join(f"  - {a}" for a in note.action_items) if note.action_items else "none"

            response = (
                f"Note #{note.id} saved!\n\n"
                f"Summary: {note.summary}\n"
                f"Domain: {note.domain.value}\n"
                f"Tags: {tags_str}\n"
                f"Actions: {actions_str}"
            )
            await status_msg.edit_text(response)

        except Exception as e:
            logger.exception("Error processing text note")
            await status_msg.edit_text(f"Error: {e}")

    async def recent(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show recent notes."""
        notes = self.db.get_recent_notes(10)
        if not notes:
            await update.message.reply_text("No notes yet. Send me a voice or text message!")
            return

        lines = []
        for n in notes:
            tags = " ".join(f"#{t}" for t in n.tags[:3])
            summary = n.summary or n.raw_text[:80]
            lines.append(f"#{n.id} [{n.domain.value}] {summary}\n   {tags}")

        await update.message.reply_text("Recent notes:\n\n" + "\n\n".join(lines))

    async def search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Search notes."""
        query = " ".join(context.args) if context.args else ""
        if not query:
            await update.message.reply_text("Usage: /search <query>")
            return

        results = self.db.search_notes(query, limit=10)
        if not results:
            await update.message.reply_text(f"No notes matching '{query}'")
            return

        lines = []
        for r in results:
            n = r.note
            summary = n.summary or n.raw_text[:80]
            lines.append(f"#{n.id} [{n.domain.value}] {summary}")

        await update.message.reply_text(f"Search results for '{query}':\n\n" + "\n\n".join(lines))

    async def actions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show pending action items."""
        items = self.db.get_action_items()
        if not items:
            await update.message.reply_text("No action items found.")
            return

        lines = [f"- {item['action']} (note #{item['note_id']})" for item in items[:20]]
        await update.message.reply_text("Action items:\n\n" + "\n".join(lines))

    async def domains(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """List notes grouped by domain."""
        from src.db.models import LifeDomain

        lines = []
        for domain in LifeDomain:
            notes = self.db.list_notes(domain=domain, limit=5)
            if notes:
                lines.append(f"\n{domain.value.upper()} ({len(notes)} notes):")
                for n in notes:
                    lines.append(f"  #{n.id} {n.summary or n.raw_text[:60]}")

        if not lines:
            await update.message.reply_text("No notes yet!")
            return

        await update.message.reply_text("Notes by domain:" + "\n".join(lines))

    async def tags_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show tag tree with usage counts."""
        if not self.tag_registry:
            await update.message.reply_text("Tag registry not available.")
            return

        top_level = self.tag_registry.get_top_level_tags()
        if not top_level:
            await update.message.reply_text("No tags yet. Send a note and tags will be created!")
            return

        lines = []
        for tag in top_level:
            children = self.tag_registry.get_children(tag.name)
            if children:
                kids_str = ", ".join(f"{c.name.split('/')[-1]}({c.usage_count})" for c in children)
                lines.append(f"#{tag.name} ({tag.usage_count} uses)\n   {kids_str}")
            else:
                lines.append(f"#{tag.name} ({tag.usage_count} uses)")

        await update.message.reply_text("Tag hierarchy:\n\n" + "\n\n".join(lines))

    async def tag_filter(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """List notes with a specific tag."""
        tag_name = " ".join(context.args) if context.args else ""
        if not tag_name:
            await update.message.reply_text("Usage: /tag <tag-name>\nExample: /tag devalok/hiring")
            return

        notes = self.db.list_notes(tag=tag_name.lower(), limit=10)
        if not notes:
            await update.message.reply_text(f"No notes with tag '{tag_name}'")
            return

        lines = []
        for n in notes:
            tags = " ".join(f"#{t}" for t in n.tags[:3])
            summary = n.summary or n.raw_text[:80]
            lines.append(f"#{n.id} [{n.domain.value}] {summary}\n   {tags}")

        await update.message.reply_text(f"Notes tagged '{tag_name}':\n\n" + "\n\n".join(lines))

    async def view_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """View a specific note by ID."""
        if not context.args:
            await update.message.reply_text("Usage: /note <id>")
            return

        try:
            note_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Invalid note ID")
            return

        note = self.db.get_note(note_id)
        if not note:
            await update.message.reply_text(f"Note #{note_id} not found")
            return

        tags = " ".join(f"#{t}" for t in note.tags) if note.tags else "none"
        actions = "\n".join(f"  - {a}" for a in note.action_items) if note.action_items else "none"
        related = ", ".join(f"#{r}" for r in note.related_note_ids) if note.related_note_ids else "none"

        response = (
            f"Note #{note.id}\n"
            f"Source: {note.source.value}\n"
            f"Domain: {note.domain.value}\n"
            f"Created: {note.created_at}\n\n"
            f"Text:\n{note.raw_text}\n\n"
            f"Summary: {note.summary}\n"
            f"Tags: {tags}\n"
            f"Actions: {actions}\n"
            f"Related: {related}"
        )
        await update.message.reply_text(response)

    def build_app(self) -> Application:
        """Build and return the Telegram application."""
        app = Application.builder().token(settings.telegram_bot_token).build()

        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("recent", self.recent))
        app.add_handler(CommandHandler("search", self.search))
        app.add_handler(CommandHandler("actions", self.actions))
        app.add_handler(CommandHandler("domains", self.domains))
        app.add_handler(CommandHandler("note", self.view_note))
        app.add_handler(CommandHandler("tags", self.tags_command))
        app.add_handler(CommandHandler("tag", self.tag_filter))
        app.add_handler(MessageHandler(filters.VOICE, self.handle_voice))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))

        return app
