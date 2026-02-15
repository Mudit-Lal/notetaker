"""Main entry point — runs the FastAPI server and Telegram bot together."""

import logging
import sys
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.ai.processor import AIProcessor
from src.api.routes import router, set_db, set_tag_registry
from src.bot.telegram_bot import NoteTakerBot
from src.config import settings
from src.db.database import Database
from src.db.seed_tags import SEED_TAGS
from src.db.tag_registry import TagRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_api(db: Database, tag_registry: TagRegistry) -> FastAPI:
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
        allow_origins=[
            "https://notes.muditlal.com",
            "http://localhost:3000",
            "http://localhost:8000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    set_db(db)
    set_tag_registry(tag_registry)
    app.include_router(router, prefix="/api/v1")

    return app


def run_api(db: Database, tag_registry: TagRegistry) -> None:
    """Run the FastAPI server."""
    app = create_api(db, tag_registry)
    uvicorn.run(app, host=settings.api_host, port=settings.api_port, log_level="info")


def run_bot(db: Database, ai: AIProcessor, tag_registry: TagRegistry) -> None:
    """Run the Telegram bot."""
    bot = NoteTakerBot(db=db, ai=ai, tag_registry=tag_registry)
    app = bot.build_app()
    logger.info("Starting Telegram bot...")
    app.run_polling(drop_pending_updates=True)


def main():
    """Main entry point — starts both API and bot."""
    db = Database(settings.db_path)
    db.connect()

    # Initialize tag registry and seed default tags
    tag_registry = TagRegistry(db.conn)
    tag_registry.ensure_tables()
    tag_registry.seed_tags(SEED_TAGS)
    logger.info("Tag registry initialized with %d seed tags", len(SEED_TAGS))

    # AI processor with tag awareness and user profile context
    ai = AIProcessor(db=db, tag_registry=tag_registry)

    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode == "api":
        logger.info("Starting API server only...")
        run_api(db, tag_registry)
    elif mode == "bot":
        logger.info("Starting Telegram bot only...")
        run_bot(db, ai, tag_registry)
    elif mode == "mcp":
        logger.info("Starting MCP server...")
        from src.mcp.server import init_mcp_db, init_mcp_tag_registry, run_mcp_server
        init_mcp_db(db)
        init_mcp_tag_registry(tag_registry)
        run_mcp_server()
    else:
        logger.info("Starting API server + Telegram bot...")
        api_thread = threading.Thread(target=run_api, args=(db, tag_registry), daemon=True)
        api_thread.start()
        run_bot(db, ai, tag_registry)


if __name__ == "__main__":
    main()
