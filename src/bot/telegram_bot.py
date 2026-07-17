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

    # ── authorization ─────────────────────────────────────────────────

    @staticmethod
    def _is_authorized(update: Update) -> bool:
        """Return True if the sender is in the allowed user ID list (or no list is configured)."""
        allowed = settings.allowed_telegram_user_ids
        if not allowed:
            return True
        user = update.effective_user
        return user is not None and user.id in allowed

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
        if not self._is_authorized(update):
            return
        text = (
            "<b>Hey! I'm your note-taking assistant.</b>\n\n"
            "Send me a voice message or text and I'll save it as a note or to-do.\n\n"
            "<b>Commands</b>\n"
            "/todos – Show pending to-dos\n"
            "/done – Mark a to-do done\n\n"
            "<i>For browsing, search, and editing — use Claude with the notetaker MCP.</i>"
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
        """Handle incoming voice messages — transcribe and save."""
        if not self._is_authorized(update):
            return
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

            await self._process_and_save(
                raw_text, NoteSource.VOICE, status_msg,
                update.message.message_id, voice.duration,
            )
        except Exception as e:
            logger.exception("Error processing voice note")
            await status_msg.edit_text(f"Error processing voice note: {e}")

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming text messages — saves as note/todo."""
        if not self._is_authorized(update):
            return
        raw_text = update.message.text
        if not raw_text or raw_text.startswith("/"):
            return

        status_msg = await update.message.reply_text("Processing…")

        try:
            await self._process_and_save(
                raw_text, NoteSource.TEXT, status_msg,
                update.message.message_id,
            )
        except Exception as e:
            logger.exception("Error processing message")
            await status_msg.edit_text(f"Error: {e}")

    async def todos_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show pending to-dos with inline buttons to mark them done."""
        if not self._is_authorized(update):
            return
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
        if not self._is_authorized(update):
            return
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

    async def callback_todo_done(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline keyboard tap to mark a todo as done."""
        if not self._is_authorized(update):
            return
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

    # ── app builder ───────────────────────────────────────────────────

    def build_app(self) -> Application:
        """Build and return the Telegram application."""
        app = Application.builder().token(settings.telegram_bot_token).post_init(self._post_init).build()

        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("todos", self.todos_command))
        app.add_handler(CommandHandler("done", self.done_command))
        app.add_handler(CallbackQueryHandler(self.callback_todo_done, pattern=r"^todo_done:"))
        app.add_handler(MessageHandler(filters.VOICE, self.handle_voice))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))

        return app

    @staticmethod
    async def _post_init(application: Application) -> None:
        """Register the bot command menu with Telegram on startup."""
        await application.bot.set_my_commands([
            BotCommand("todos", "Show pending to-dos"),
            BotCommand("done", "Mark a to-do as done"),
        ])
