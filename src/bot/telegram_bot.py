import logging
from typing import TYPE_CHECKING

from telegram import BotCommand, Update
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
from src.db.models import MessageType, Note, NoteSource, TodoStatus

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
            "Just send me a voice message or text — I'll figure out "
            "if it's a note, a to-do, or both, and handle it accordingly.\n\n"
            "Commands:\n"
            "/recent - Show recent notes\n"
            "/search <query> - Search your notes\n"
            "/todos - Show pending to-dos\n"
            "/done <id> - Mark a to-do as done\n"
            "/undone <id> - Reopen a to-do\n"
            "/actions - Show action items from notes\n"
            "/domains - List notes by life area\n"
            "/note <id> - View a specific note\n"
            "/tags - Browse tag hierarchy\n"
            "/tag <name> - Filter notes by tag"
        )

    def _format_note_response(self, note: Note, todos: list | None = None) -> str:
        """Format a saved note (and optional todos) into a Telegram response."""
        tags_str = " ".join(f"#{t}" for t in note.tags) if note.tags else "none"
        related_str = (
            ", ".join(f"#{rid}" for rid in note.related_note_ids)
            if note.related_note_ids
            else "none"
        )

        lines = [
            f"Note #{note.id} saved!\n",
            f"Summary: {note.summary}",
            f"Domain: {note.domain.value}",
            f"Tags: {tags_str}",
            f"Related: {related_str}",
        ]

        if todos:
            lines.append("\nTo-dos created:")
            for t in todos:
                prio = f" [{t.priority.value}]" if t.priority.value != "medium" else ""
                due = f" (due: {t.due_date})" if t.due_date else ""
                lines.append(f"  T#{t.id}{prio} {t.text}{due}")

        return "\n".join(lines)

    def _format_todo_response(self, todos: list) -> str:
        """Format saved todos (no note) into a Telegram response."""
        lines = ["To-do saved!" if len(todos) == 1 else f"{len(todos)} to-dos saved!"]
        for t in todos:
            prio = f" [{t.priority.value}]" if t.priority.value != "medium" else ""
            due = f" (due: {t.due_date})" if t.due_date else ""
            tags_str = " ".join(f"#{tg}" for tg in t.tags) if t.tags else ""
            lines.append(f"\nT#{t.id}{prio} {t.text}{due}")
            if tags_str:
                lines.append(f"  {tags_str}  [{t.domain.value}]")
        return "\n".join(lines)

    async def _process_and_save(self, raw_text: str, source: NoteSource, status_msg, telegram_message_id: int, audio_duration: float | None = None) -> None:
        """Classify message, save note/todos/both, and respond."""
        result = self.ai.enrich_message(raw_text, source.value)
        msg_type: MessageType = result["message_type"]
        note = result.get("note")
        todos = result.get("todos", [])

        saved_note = None
        saved_todos = []

        # Save note if present
        if note:
            note.telegram_message_id = telegram_message_id
            if audio_duration is not None:
                note.audio_duration_seconds = audio_duration
            # Find related notes
            recent = self.db.get_recent_notes(50)
            note.related_note_ids = self.ai.find_related_notes(note, recent)
            saved_note = self.db.create_note(note)

        # Save todos
        for todo in todos:
            todo.source = source
            if saved_note:
                todo.source_note_id = saved_note.id
            saved_todo = self.db.create_todo(todo)
            saved_todos.append(saved_todo)

        # Format response based on message type
        if msg_type == MessageType.NOTE and saved_note:
            response = self._format_note_response(saved_note)
        elif msg_type == MessageType.TODO and saved_todos:
            response = self._format_todo_response(saved_todos)
        elif msg_type == MessageType.NOTE_WITH_TODOS and saved_note:
            response = self._format_note_response(saved_note, saved_todos)
        else:
            response = "Saved!"

        await status_msg.edit_text(response)

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming voice messages."""
        voice = update.message.voice
        status_msg = await update.message.reply_text("Transcribing your voice note...")

        try:
            voice_file = await context.bot.get_file(voice.file_id)
            audio_bytes = await voice_file.download_as_bytearray()

            raw_text = self.ai.transcribe_audio(bytes(audio_bytes))
            if not raw_text:
                await status_msg.edit_text("Couldn't transcribe that. Try again?")
                return

            await status_msg.edit_text(f"Transcribed. Processing...\n\n\"{raw_text[:200]}\"")
            await self._process_and_save(
                raw_text, NoteSource.VOICE, status_msg,
                update.message.message_id, voice.duration,
            )
        except Exception as e:
            logger.exception("Error processing voice note")
            await status_msg.edit_text(f"Error processing voice note: {e}")

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming text messages — intelligently routed as note, todo, or both."""
        raw_text = update.message.text
        if not raw_text or raw_text.startswith("/"):
            return

        status_msg = await update.message.reply_text("Processing...")

        try:
            await self._process_and_save(
                raw_text, NoteSource.TEXT, status_msg,
                update.message.message_id,
            )
        except Exception as e:
            logger.exception("Error processing message")
            await status_msg.edit_text(f"Error: {e}")

    async def todos_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show pending to-dos."""
        todos = self.db.list_todos(status=TodoStatus.PENDING)
        if not todos:
            await update.message.reply_text("No pending to-dos. You're all caught up!")
            return

        lines = ["Pending to-dos:\n"]
        for t in todos:
            prio = f" [{t.priority.value}]" if t.priority.value != "medium" else ""
            due = f" (due: {t.due_date})" if t.due_date else ""
            note_ref = f" (note #{t.source_note_id})" if t.source_note_id else ""
            tags_str = " ".join(f"#{tg}" for tg in t.tags[:3]) if t.tags else ""
            line = f"T#{t.id}{prio} {t.text}{due}{note_ref}"
            if tags_str:
                line += f"\n   {tags_str}"
            lines.append(line)

        await update.message.reply_text("\n\n".join(lines))

    async def done_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Mark a to-do as done."""
        if not context.args:
            await update.message.reply_text("Usage: /done <todo-id>")
            return
        try:
            todo_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Invalid to-do ID.")
            return

        todo = self.db.complete_todo(todo_id)
        if not todo:
            await update.message.reply_text(f"To-do T#{todo_id} not found.")
            return
        await update.message.reply_text(f"Done! T#{todo.id} marked as completed.\n  {todo.text}")

    async def undone_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Reopen a completed to-do."""
        if not context.args:
            await update.message.reply_text("Usage: /undone <todo-id>")
            return
        try:
            todo_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Invalid to-do ID.")
            return

        todo = self.db.uncomplete_todo(todo_id)
        if not todo:
            await update.message.reply_text(f"To-do T#{todo_id} not found.")
            return
        await update.message.reply_text(f"Reopened T#{todo.id}.\n  {todo.text}")

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
        app = Application.builder().token(settings.telegram_bot_token).post_init(self._post_init).build()

        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("recent", self.recent))
        app.add_handler(CommandHandler("search", self.search))
        app.add_handler(CommandHandler("todos", self.todos_command))
        app.add_handler(CommandHandler("done", self.done_command))
        app.add_handler(CommandHandler("undone", self.undone_command))
        app.add_handler(CommandHandler("actions", self.actions))
        app.add_handler(CommandHandler("domains", self.domains))
        app.add_handler(CommandHandler("note", self.view_note))
        app.add_handler(CommandHandler("tags", self.tags_command))
        app.add_handler(CommandHandler("tag", self.tag_filter))
        app.add_handler(MessageHandler(filters.VOICE, self.handle_voice))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))

        return app

    @staticmethod
    async def _post_init(application: Application) -> None:
        """Register the bot command menu with Telegram on startup."""
        await application.bot.set_my_commands([
            BotCommand("recent", "Show recent notes"),
            BotCommand("search", "Search your notes"),
            BotCommand("todos", "Show pending to-dos"),
            BotCommand("done", "Mark a to-do as done"),
            BotCommand("undone", "Reopen a completed to-do"),
            BotCommand("actions", "Show action items from notes"),
            BotCommand("domains", "List notes by life area"),
            BotCommand("note", "View a specific note"),
            BotCommand("tags", "Browse tag hierarchy"),
            BotCommand("tag", "Filter notes by tag"),
        ])
