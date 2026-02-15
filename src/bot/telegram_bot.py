import logging
from html import escape
from typing import TYPE_CHECKING

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.ai.processor import AIProcessor
from src.config import settings
from src.db.database import Database
from src.db.models import MessageType, Note, NoteSource, Todo, TodoStatus

if TYPE_CHECKING:
    from src.db.tag_registry import TagRegistry

logger = logging.getLogger(__name__)

# Priority display helpers
_PRIORITY_LABEL = {"high": "‼️ High", "medium": "", "low": "Low"}
_PRIORITY_ICON = {"high": "🔴", "medium": "🔵", "low": "⚪"}


def _prio_bullet(priority_value: str) -> str:
    return _PRIORITY_ICON.get(priority_value, "🔵")


class NoteTakerBot:
    def __init__(self, db: Database, ai: AIProcessor, tag_registry: "TagRegistry | None" = None):
        self.db = db
        self.ai = ai
        self.tag_registry = tag_registry

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _todo_line(t: Todo, *, show_note_ref: bool = False) -> str:
        """Render a single todo as an HTML line."""
        bullet = _prio_bullet(t.priority.value)
        text = escape(t.text)
        parts = [f"{bullet} {text}"]

        meta: list[str] = []
        if t.priority.value == "high":
            meta.append("‼️ High")
        if t.due_date:
            meta.append(f"📅 {escape(t.due_date)}")
        if show_note_ref and t.source_note_id:
            meta.append(f"from note #{t.source_note_id}")

        if meta:
            parts.append(f"     <i>{' · '.join(meta)}</i>")

        return "\n".join(parts)

    @staticmethod
    def _todos_keyboard(todos: list[Todo]) -> InlineKeyboardMarkup | None:
        """Build inline keyboard with a 'Done' button for each todo."""
        if not todos:
            return None
        buttons = []
        for t in todos:
            label = t.text if len(t.text) <= 30 else t.text[:28] + "…"
            buttons.append(
                [InlineKeyboardButton(f"✓  {label}", callback_data=f"todo_done:{t.id}")]
            )
        return InlineKeyboardMarkup(buttons)

    # ── formatters ────────────────────────────────────────────────────

    def _format_note_response(self, note: Note, todos: list[Todo] | None = None) -> str:
        """Format a saved note (and optional todos) into an HTML Telegram response."""
        summary = escape(note.summary) if note.summary else ""
        domain = escape(note.domain.value.capitalize())

        lines = [f"<b>📝 Note #{note.id} saved</b>"]

        if summary:
            lines.append(f"\n{summary}")

        lines.append(f"\n<i>{domain}</i>")

        if note.related_note_ids:
            refs = ", ".join(f"#{rid}" for rid in note.related_note_ids)
            lines.append(f"Related: {refs}")

        if todos:
            lines.append("\n<b>To-dos created:</b>")
            for t in todos:
                lines.append(self._todo_line(t))

        return "\n".join(lines)

    def _format_todo_response(self, todos: list[Todo]) -> str:
        """Format saved todos (no note) into an HTML Telegram response."""
        header = "<b>✅ To-do saved!</b>" if len(todos) == 1 else f"<b>✅ {len(todos)} to-dos saved!</b>"
        lines = [header, ""]
        for t in todos:
            lines.append(self._todo_line(t))
        return "\n".join(lines)

    # ── commands ──────────────────────────────────────────────────────

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "<b>Hey! I'm your note-taking assistant.</b>\n\n"
            "Send me a voice message or text — I'll figure out "
            "if it's a note, a to-do, or both.\n\n"
            "<b>Commands</b>\n"
            "/recent – Recent notes\n"
            "/search – Search your notes\n"
            "/todos – Pending to-dos\n"
            "/done – Mark a to-do done\n"
            "/undone – Reopen a to-do\n"
            "/actions – Action items\n"
            "/domains – Notes by life area\n"
            "/note – View a specific note\n"
            "/tags – Tag hierarchy\n"
            "/tag – Filter by tag"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def _process_and_save(self, raw_text: str, source: NoteSource, status_msg, telegram_message_id: int, audio_duration: float | None = None) -> None:
        """Classify message, save note/todos/both, and respond."""
        result = self.ai.enrich_message(raw_text, source.value)
        msg_type: MessageType = result["message_type"]
        note = result.get("note")
        todos = result.get("todos", [])

        saved_note = None
        saved_todos: list[Todo] = []

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

        keyboard = self._todos_keyboard(saved_todos) if saved_todos else None
        await status_msg.edit_text(response, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming voice messages."""
        voice = update.message.voice
        status_msg = await update.message.reply_text("🎙 Transcribing…")

        try:
            voice_file = await context.bot.get_file(voice.file_id)
            audio_bytes = await voice_file.download_as_bytearray()

            raw_text = self.ai.transcribe_audio(bytes(audio_bytes))
            if not raw_text:
                await status_msg.edit_text("Couldn't transcribe that. Try again?")
                return

            preview = escape(raw_text[:200])
            await status_msg.edit_text(
                f"✅ Transcribed — processing…\n\n<i>\"{preview}\"</i>",
                parse_mode=ParseMode.HTML,
            )

            # Classify intent — voice message could be a question too
            intent = self.ai.classify_intent(raw_text)

            if intent["intent"] == "query":
                await status_msg.edit_text(
                    f"<i>\"{preview}\"</i>\n\nThinking…",
                    parse_mode=ParseMode.HTML,
                )
                answer = self.ai.answer_query(intent["query_text"], raw_text)
                await status_msg.edit_text(answer)
            else:
                await self._process_and_save(
                    raw_text, NoteSource.VOICE, status_msg,
                    update.message.message_id, voice.duration,
                )
        except Exception as e:
            logger.exception("Error processing voice note")
            await status_msg.edit_text(f"Error processing voice note: {e}")

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming text messages — classifies intent first, then routes."""
        raw_text = update.message.text
        if not raw_text or raw_text.startswith("/"):
            return

        status_msg = await update.message.reply_text("Processing…")

        try:
            # Step 1: Classify intent — is the user saving content or asking a question?
            intent = self.ai.classify_intent(raw_text)

            if intent["intent"] == "query":
                # Answer the question using existing notes/todos as context
                await status_msg.edit_text("Thinking…")
                answer = self.ai.answer_query(intent["query_text"], raw_text)
                await status_msg.edit_text(answer)
            else:
                # Save as note/todo (existing flow)
                await self._process_and_save(
                    raw_text, NoteSource.TEXT, status_msg,
                    update.message.message_id,
                )
        except Exception as e:
            logger.exception("Error processing message")
            await status_msg.edit_text(f"Error: {e}")

    async def todos_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show pending to-dos with inline buttons to mark them done."""
        todos = self.db.list_todos(status=TodoStatus.PENDING)
        if not todos:
            await update.message.reply_text("No pending to-dos — you're all caught up! 🎉")
            return

        lines = ["<b>📋 Pending to-dos</b>\n"]
        for t in todos:
            lines.append(self._todo_line(t, show_note_ref=True))

        keyboard = self._todos_keyboard(todos)
        await update.message.reply_text(
            "\n\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )

    async def done_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Mark a to-do as done via /done <id>."""
        if not context.args:
            await update.message.reply_text("Usage: /done &lt;todo-id&gt;", parse_mode=ParseMode.HTML)
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
        await update.message.reply_text(
            f"<b>✅ Done!</b>  <s>{escape(todo.text)}</s>",
            parse_mode=ParseMode.HTML,
        )

    async def undone_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Reopen a completed to-do."""
        if not context.args:
            await update.message.reply_text("Usage: /undone &lt;todo-id&gt;", parse_mode=ParseMode.HTML)
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
        await update.message.reply_text(
            f"<b>🔄 Reopened</b>  {escape(todo.text)}",
            parse_mode=ParseMode.HTML,
        )

    async def callback_todo_done(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline keyboard tap to mark a todo as done."""
        query = update.callback_query
        await query.answer()  # acknowledge the tap immediately

        data = query.data or ""
        if not data.startswith("todo_done:"):
            return

        try:
            todo_id = int(data.split(":")[1])
        except (IndexError, ValueError):
            return

        todo = self.db.complete_todo(todo_id)
        if not todo:
            await query.answer("To-do not found.", show_alert=True)
            return

        # Re-fetch the pending list and rebuild the message
        remaining = self.db.list_todos(status=TodoStatus.PENDING)

        if remaining:
            lines = ["<b>📋 Pending to-dos</b>\n"]
            for t in remaining:
                lines.append(self._todo_line(t, show_note_ref=True))
            lines.append(f"\n<i>✅ \"{escape(todo.text)}\" — done!</i>")
            keyboard = self._todos_keyboard(remaining)
            await query.edit_message_text(
                "\n\n".join(lines),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        else:
            await query.edit_message_text(
                f"<i>✅ \"{escape(todo.text)}\" — done!</i>\n\nAll to-dos completed! 🎉",
                parse_mode=ParseMode.HTML,
            )

    async def recent(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show recent notes."""
        notes = self.db.get_recent_notes(10)
        if not notes:
            await update.message.reply_text("No notes yet. Send me a voice or text message!")
            return

        lines = ["<b>📒 Recent notes</b>\n"]
        for n in notes:
            summary = escape(n.summary or n.raw_text[:80])
            domain = escape(n.domain.value.capitalize())
            lines.append(f"<b>#{n.id}</b>  {summary}\n     <i>{domain}</i>")

        await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.HTML)

    async def search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Search notes."""
        query = " ".join(context.args) if context.args else ""
        if not query:
            await update.message.reply_text("Usage: /search &lt;query&gt;", parse_mode=ParseMode.HTML)
            return

        results = self.db.search_notes(query, limit=10)
        if not results:
            await update.message.reply_text(f"No notes matching <i>{escape(query)}</i>", parse_mode=ParseMode.HTML)
            return

        lines = [f"<b>🔍 Results for \"{escape(query)}\"</b>\n"]
        for r in results:
            n = r.note
            summary = escape(n.summary or n.raw_text[:80])
            domain = escape(n.domain.value.capitalize())
            lines.append(f"<b>#{n.id}</b>  {summary}\n     <i>{domain}</i>")

        await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.HTML)

    async def actions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show pending action items."""
        items = self.db.get_action_items()
        if not items:
            await update.message.reply_text("No action items found.")
            return

        lines = ["<b>⚡ Action items</b>\n"]
        for item in items[:20]:
            action = escape(item["action"])
            lines.append(f"• {action}  <i>(note #{item['note_id']})</i>")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def domains(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """List notes grouped by domain."""
        from src.db.models import LifeDomain

        lines = ["<b>🗂 Notes by domain</b>"]
        for domain in LifeDomain:
            notes = self.db.list_notes(domain=domain, limit=5)
            if notes:
                lines.append(f"\n<b>{escape(domain.value.upper())}</b> ({len(notes)})")
                for n in notes:
                    summary = escape(n.summary or n.raw_text[:60])
                    lines.append(f"  #{n.id}  {summary}")

        if len(lines) == 1:
            await update.message.reply_text("No notes yet!")
            return

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def tags_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show tag tree with usage counts."""
        if not self.tag_registry:
            await update.message.reply_text("Tag registry not available.")
            return

        top_level = self.tag_registry.get_top_level_tags()
        if not top_level:
            await update.message.reply_text("No tags yet. Send a note and tags will be created!")
            return

        lines = ["<b>🏷 Tag hierarchy</b>\n"]
        for tag in top_level:
            children = self.tag_registry.get_children(tag.name)
            tag_name = escape(tag.name)
            if children:
                kids_str = ", ".join(
                    f"{escape(c.name.split('/')[-1])} ({c.usage_count})" for c in children
                )
                lines.append(f"<b>#{tag_name}</b> ({tag.usage_count})\n     {kids_str}")
            else:
                lines.append(f"<b>#{tag_name}</b> ({tag.usage_count})")

        await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.HTML)

    async def tag_filter(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """List notes with a specific tag."""
        tag_name = " ".join(context.args) if context.args else ""
        if not tag_name:
            await update.message.reply_text(
                "Usage: /tag &lt;tag-name&gt;\nExample: /tag devalok/hiring",
                parse_mode=ParseMode.HTML,
            )
            return

        notes = self.db.list_notes(tag=tag_name.lower(), limit=10)
        if not notes:
            await update.message.reply_text(
                f"No notes with tag <i>{escape(tag_name)}</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        lines = [f"<b>🏷 Notes tagged \"{escape(tag_name)}\"</b>\n"]
        for n in notes:
            summary = escape(n.summary or n.raw_text[:80])
            domain = escape(n.domain.value.capitalize())
            lines.append(f"<b>#{n.id}</b>  {summary}\n     <i>{domain}</i>")

        await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.HTML)

    async def view_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """View a specific note by ID."""
        if not context.args:
            await update.message.reply_text("Usage: /note &lt;id&gt;", parse_mode=ParseMode.HTML)
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

        domain = escape(note.domain.value.capitalize())
        source = escape(note.source.value.capitalize())
        raw = escape(note.raw_text)
        summary = escape(note.summary) if note.summary else ""

        lines = [f"<b>📝 Note #{note.id}</b>"]
        lines.append(f"<i>{source} · {domain}</i>")

        if note.created_at:
            lines.append(f"<i>{note.created_at:%b %d, %Y %H:%M}</i>")

        lines.append(f"\n{raw}")

        if summary:
            lines.append(f"\n<b>Summary:</b> {summary}")

        if note.action_items:
            lines.append("\n<b>Action items:</b>")
            for a in note.action_items:
                lines.append(f"  • {escape(a)}")

        if note.related_note_ids:
            refs = ", ".join(f"#{r}" for r in note.related_note_ids)
            lines.append(f"\n<b>Related:</b> {refs}")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    # ── app builder ───────────────────────────────────────────────────

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
        app.add_handler(CallbackQueryHandler(self.callback_todo_done, pattern=r"^todo_done:"))
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
