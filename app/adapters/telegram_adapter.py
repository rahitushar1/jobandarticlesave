"""
Telegram bot adapter.
Handles all incoming Telegram updates: images, URLs, text, commands.
Uses python-telegram-bot v21 in webhook or polling mode.
"""
import io
import os
import re
import structlog
from typing import Optional

from telegram import Update, Bot, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes,
)
from telegram.constants import ParseMode

from app.config import get_settings
from app.models.capture import CaptureRequest, SourceType
from app.services.pipeline import process_capture
from app import database as db

log = structlog.get_logger(__name__)
settings = get_settings()

URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def _is_allowed(user_id: int) -> bool:
    allowed = settings.allowed_telegram_ids
    if not allowed:
        return True   # Open to all if no whitelist
    return user_id in allowed


async def _send_typing(update: Update) -> None:
    await update.effective_chat.send_action("typing")


# ─── Commands ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        return
    text = (
        "👋 *Capture Assistant*\n\n"
        "Send me:\n"
        "• 📸 A screenshot of a job posting / anything\n"
        "• 🔗 A URL to capture\n"
        "• 📝 Text or a note to save\n\n"
        "*Commands:*\n"
        "/recent — last 5 captures\n"
        "/status — health check\n"
        "/help — show this message"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        return
    recent = await db.get_recent_captures(limit=1)
    last = recent[0]["created_at"] if recent else "none"
    await update.message.reply_text(
        f"✅ *Capture Assistant is running*\n"
        f"Last capture: `{last}`\n"
        f"DB: `{settings.sqlite_db_path}`\n"
        f"Sheet ID: `{settings.google_spreadsheet_id[:20]}...`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        return
    await _send_typing(update)
    captures = await db.get_recent_captures(channel="telegram", limit=5)
    if not captures:
        await update.message.reply_text("No captures yet.")
        return

    lines = ["📋 *Recent Captures:*\n"]
    for i, c in enumerate(captures, 1):
        title = c.get("title") or "(untitled)"
        tab = c.get("sheet_tab", "?")
        ts = c.get("created_at", "?")[:16]
        ctype = c.get("capture_type", "?")
        lines.append(f"{i}. `{title}` — {ctype} → *{tab}* ({ts})")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ─── Message handlers ─────────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        return
    await _send_typing(update)

    # Download the highest-res version
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)

    # Stream into memory
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    image_bytes = buf.getvalue()

    # Save to upload dir for reference
    os.makedirs(settings.upload_dir, exist_ok=True)
    file_path = os.path.join(settings.upload_dir, f"{photo.file_unique_id}.jpg")
    with open(file_path, "wb") as f:
        f.write(image_bytes)

    user_note = update.message.caption or None

    request = CaptureRequest(
        channel="telegram",
        source_type=SourceType.image,
        raw_input=file_path,
        user_note=user_note,
        telegram_user_id=update.effective_user.id,
    )

    await update.message.reply_text("🔍 Analysing screenshot…")
    result = await process_capture(request)
    await update.message.reply_text(
        result.to_telegram_message(), parse_mode=ParseMode.MARKDOWN
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle documents sent as files (PDF, images uploaded as docs)."""
    if not _is_allowed(update.effective_user.id):
        return

    doc = update.message.document
    mime = doc.mime_type or ""

    if mime.startswith("image/"):
        await _send_typing(update)
        file = await context.bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        image_bytes = buf.getvalue()

        os.makedirs(settings.upload_dir, exist_ok=True)
        ext = mime.split("/")[-1]
        file_path = os.path.join(settings.upload_dir, f"{doc.file_unique_id}.{ext}")
        with open(file_path, "wb") as f:
            f.write(image_bytes)

        request = CaptureRequest(
            channel="telegram",
            source_type=SourceType.image,
            raw_input=file_path,
            user_note=update.message.caption,
            telegram_user_id=update.effective_user.id,
        )
        await update.message.reply_text("🔍 Analysing image document…")
        result = await process_capture(request)
        await update.message.reply_text(
            result.to_telegram_message(), parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            "ℹ️ I can handle image files. PDF support coming soon. "
            "Try sending the URL instead."
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        return
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    await _send_typing(update)

    # Detect if it's a URL
    url_match = URL_PATTERN.search(text)
    if url_match:
        url = url_match.group(0).rstrip(".,;)")
        # Rest of text (if any) becomes the note
        user_note = text.replace(url_match.group(0), "").strip() or None
        request = CaptureRequest(
            channel="telegram",
            source_type=SourceType.url,
            raw_input=url,
            user_note=user_note,
            telegram_user_id=update.effective_user.id,
        )
        await update.message.reply_text(f"🌐 Fetching `{url}`…", parse_mode=ParseMode.MARKDOWN)
    else:
        request = CaptureRequest(
            channel="telegram",
            source_type=SourceType.text,
            raw_input=text,
            telegram_user_id=update.effective_user.id,
        )
        await update.message.reply_text("📝 Processing note…")

    result = await process_capture(request)
    await update.message.reply_text(
        result.to_telegram_message(), parse_mode=ParseMode.MARKDOWN
    )


# ─── App factory ─────────────────────────────────────────────────────────────

def build_telegram_app() -> Application:
    app = Application.builder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("recent", cmd_recent))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return app


async def setup_commands(bot_token: str) -> None:
    bot = Bot(bot_token)
    await bot.set_my_commands([
        BotCommand("start",  "Welcome & instructions"),
        BotCommand("recent", "Show last 5 captures"),
        BotCommand("status", "Health check"),
        BotCommand("help",   "Show help"),
    ])
