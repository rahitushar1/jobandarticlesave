"""
FastAPI application entry point.
Mounts Telegram webhook (or starts polling) and optional web adapter.
"""
import asyncio
import os
import structlog
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from telegram import Update

from app.config import get_settings
from app.adapters.telegram_adapter import build_telegram_app, setup_commands
from app.adapters.web_adapter import router as web_router
from app import database as db
from app.services.sheets_writer import ensure_sheet_tabs

settings = get_settings()
log = structlog.get_logger(__name__)

# Configure structlog
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# ── Telegram app singleton ────────────────────────────────────────────────────
_tg_app = None


def get_telegram_app():
    global _tg_app
    if _tg_app is None:
        _tg_app = build_telegram_app()
    return _tg_app


# ── FastAPI lifespan ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup_begin")

    # SQLite
    await db.init_db()
    log.info("sqlite_ready", path=settings.sqlite_db_path)

    # Google Sheets
    try:
        await ensure_sheet_tabs()
        log.info("sheets_ready")
    except Exception as e:
        log.error("sheets_init_error", error=str(e))

    # Telegram setup
    tg = get_telegram_app()
    await tg.initialize()

    if settings.telegram_webhook_url:
        webhook_url = f"{settings.telegram_webhook_url.rstrip('/')}/telegram/webhook"
        await tg.bot.set_webhook(webhook_url)
        log.info("telegram_webhook_set", url=webhook_url)
    else:
        # Polling mode (local dev)
        log.info("telegram_starting_polling")
        asyncio.create_task(_run_polling(tg))

    try:
        await setup_commands(settings.telegram_bot_token)
    except Exception as e:
        log.warning("commands_setup_failed", error=str(e))

    log.info("startup_complete")
    yield

    # Shutdown
    await tg.shutdown()
    log.info("shutdown_complete")


async def _run_polling(tg_app):
    """Run polling in background (dev mode only)."""
    try:
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        log.info("telegram_polling_active")
        # Keep alive until shutdown
        while True:
            await asyncio.sleep(3600)
    except Exception as e:
        log.error("polling_error", error=str(e))


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Capture Assistant",
    description="AI-powered capture assistant for saving content to Google Sheets",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Telegram webhook endpoint ─────────────────────────────────────────────────

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> Response:
    data = await request.json()
    tg = get_telegram_app()
    update = Update.de_json(data, tg.bot)
    await tg.process_update(update)
    return Response(status_code=200)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.app_env}


# ── Web adapter (optional) ────────────────────────────────────────────────────

if settings.web_enabled:
    app.include_router(web_router)
    log.info("web_adapter_enabled")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=settings.app_env == "development",
        log_level=settings.log_level.lower(),
    )
