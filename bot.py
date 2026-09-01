import logging
import os
import tempfile
import uuid

from pydub import AudioSegment
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

user_queues: dict[int, list[str]] = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send me two or more audio files (as audio or voice/document), one at a time.\n"
        "When you're done, send /merge and I'll stitch them together in the order you sent them.\n"
        "Use /clear to empty your queue and start over."
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    for path in user_queues.pop(user_id, []):
        _safe_remove(path)
    await update.message.reply_text("Queue cleared.")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    message = update.message

    tg_file = message.audio or message.voice or message.document
    if tg_file is None:
        return

    if message.document and not (message.document.mime_type or "").startswith("audio"):
        await message.reply_text("That doesn't look like an audio file — please send audio.")
        return

    file = await tg_file.get_file()

    tmp_dir = tempfile.gettempdir()
    ext = _guess_extension(tg_file)
    local_path = os.path.join(tmp_dir, f"{uuid.uuid4().hex}{ext}")
    await file.download_to_drive(local_path)

    user_queues.setdefault(user_id, []).append(local_path)
    count = len(user_queues[user_id])
    await message.reply_text(f"Got it ({count} queued). Send more, or /merge when ready.")


async def merge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    message = update.message
    paths = user_queues.get(user_id, [])

    if len(paths) < 2:
        await message.reply_text("I need at least two audio files first. Send them, then /merge.")
        return

    await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.RECORD_VOICE)
    status = await message.reply_text("Merging your audio files…")

    output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_merged.mp3")
    try:
        combined = AudioSegment.empty()
        for path in paths:
            combined += AudioSegment.from_file(path)
        combined.export(output_path, format="mp3")

        with open(output_path, "rb") as f:
            await message.reply_audio(audio=f, filename="merged.mp3", caption="Here's your merged audio!")
    except Exception:
        logger.exception("Failed to merge audio for user %s", user_id)
        await message.reply_text(
            "Something went wrong while merging. Make sure all files are valid audio and try again."
        )
    finally:
        await status.delete()
        for path in paths:
            _safe_remove(path)
        _safe_remove(output_path)
        user_queues.pop(user_id, None)


def _guess_extension(tg_file) -> str:
    file_name = getattr(tg_file, "file_name", None)
    if file_name and "." in file_name:
        return os.path.splitext(file_name)[1]
    mime = getattr(tg_file, "mime_type", "") or ""
    mime_map = {
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
    }
    return mime_map.get(mime, ".ogg")


def _safe_remove(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        logger.warning("Could not remove temp file %s", path)


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "Set the TELEGRAM_BOT_TOKEN environment variable before running the bot."
        )

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clear", clear))
    application.add_handler(CommandHandler("merge", merge))
    application.add_handler(
        MessageHandler(filters.AUDIO | filters.VOICE | filters.Document.AUDIO, handle_audio)
    )

    logger.info("Bot starting…")
    application.run_polling()


if __name__ == "__main__":
    main()
