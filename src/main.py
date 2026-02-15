"""Main entry point — runs the FastAPI server and Telegram bot together."""

import logging
import sys
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.ai.processor import AIProcessor
from src.api.routes import router, set_db
from src.bot.telegram_bot import NoteTakerBot
from src.config import settings
from src.db.database import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_api(db: Database) -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Notetaker API started on %s:%s", settings.api_host, settings.api_port)
        yield
        db.close()

    app = FastAPI(
        title="Notetaker API",
        description="Voice-first note-taking REST API with AI-powered organization",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Lock this down in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    set_db(db)
    app.include_router(router, prefix="/api/v1")

    return app


def run_api(db: Database) -> None:
    """Run the FastAPI server."""
    app = create_api(db)
    uvicorn.run(app, host=settings.api_host, port=settings.api_port, log_level="info")


def run_bot(db: Database, ai: AIProcessor) -> None:
    """Run the Telegram bot in a separate thread."""
    bot = NoteTakerBot(db=db, ai=ai)
    app = bot.build_app()
    logger.info("Starting Telegram bot...")
    app.run_polling(drop_pending_updates=True)


def main():
    """Main entry point — starts both API and bot."""
    db = Database(settings.db_path)
    db.connect()
    ai = AIProcessor()

    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode == "api":
        logger.info("Starting API server only...")
        run_api(db)
    elif mode == "bot":
        logger.info("Starting Telegram bot only...")
        run_bot(db, ai)
    elif mode == "mcp":
        logger.info("Starting MCP server...")
        from src.mcp.server import init_mcp_db, run_mcp_server
        init_mcp_db(db)
        run_mcp_server()
    else:
        logger.info("Starting API server + Telegram bot...")
        # Run API in a thread, bot on main thread
        api_thread = threading.Thread(target=run_api, args=(db,), daemon=True)
        api_thread.start()
        run_bot(db, ai)


if __name__ == "__main__":
    main()
