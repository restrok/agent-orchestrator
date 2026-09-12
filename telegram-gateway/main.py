import asyncio
import contextlib
import html
import json
import logging
import os
import re

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Load environment variables
load_dotenv()


class MessageProcessor:
    @staticmethod
    def encode(text: str) -> str:
        """Sanitizes and prepares user input for the AI."""
        if not text:
            return ""
        lines = [line.strip() for line in text.strip().split("\n")]
        text = "\n".join(lines)
        return re.sub(r"\n{3,}", "\n\n", text)

    @staticmethod
    def split_message(text: str, max_length: int = 3000) -> list[str]:
        """Splits a message into chunks, preferably at paragraphs or newlines."""
        if not text:
            return []
        if len(text) <= max_length:
            return [text]

        chunks = []
        while text:
            if len(text) <= max_length:
                chunks.append(text)
                break

            split_at = text.rfind("\n\n", 0, max_length)
            if split_at != -1 and split_at > max_length // 3:
                split_len = 2
            else:
                split_at = text.rfind("\n", 0, max_length)
                if split_at != -1 and split_at > max_length // 3:
                    split_len = 1
                else:
                    split_at = text.rfind(" ", 0, max_length)
                    if split_at != -1 and split_at > max_length // 3:
                        split_len = 1
                    else:
                        split_at = max_length
                        split_len = 0

            chunks.append(text[:split_at].strip())
            text = text[split_at + split_len:].strip()

        return chunks

    @staticmethod
    def decode(text: str) -> str:
        """Robustly formats text for Telegram's HTML mode."""
        if not text:
            return ""

        text = text.replace("\\n", "\n")
        lines = text.split("\n")
        processed_lines = []
        in_table = False

        emojis = {
            "heart": "❤️", "hr": "❤️", "bpm": "❤️", "frecuencia": "❤️",
            "distance": "📍", "distancia": "📍", "pace": "⏱️", "ritmo": "⏱️",
            "power": "⚡", "potencia": "⚡", "time": "🕒", "tiempo": "🕒",
            "duración": "🕒", "calories": "🔥", "calorías": "🔥", "vo2": "📈",
            "sleep": "😴", "sueño": "😴", "hrv": "⚖️",
        }

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                parts = [p.strip() for p in stripped.split("|") if p.strip()]
                if not parts or all(re.match(r"[:\-]+", p) for p in parts):
                    continue
                if not in_table:
                    in_table = True
                    processed_lines.append("")
                    continue
                if len(parts) >= 2:
                    metric_name = parts[0]
                    value = " | ".join(parts[1:])
                    icon = ""
                    for e_key, emoji in emojis.items():
                        if e_key in metric_name.lower():
                            icon = emoji + " "
                            break
                    processed_lines.append(f"{icon}<b>{metric_name}:</b> {value}")
                else:
                    processed_lines.append(f"• {parts[0]}")
            else:
                if in_table:
                    in_table = False
                    processed_lines.append("")
                processed_lines.append(line)

        text = "\n".join(processed_lines)
        text = re.sub(r"^###\s+(.*)$", r"\n\n<b>\1</b>\n", text, flags=re.MULTILINE)
        structural_markers = [r"🔹", r"⚠️", r"✅", r"📅", r"🔔", r"🏃", r"🔋", r"💪", r"🧘‍♂️", r"🎯"]
        for marker in structural_markers:
            text = re.sub(rf"([^\n])\s*({marker})", r"\1\n\n\2", text)
            text = re.sub(rf"({marker})([^\s])", r"\1 \2", text)

        text = html.escape(text, quote=False)
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"<i>\1</i>", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = os.getenv("API_URL", "http://agent-orchestrator-api:8001")


def _load_env_json(var_name: str) -> dict[str, str]:
    val = os.getenv(var_name, "").strip()
    if not val:
        return {}
    try:
        data = json.loads(val)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception as e:
        logging.warning(f"Failed to parse {var_name} from env: {e}")
    return {}


DEFAULT_USER_MAPPING = _load_env_json("DEFAULT_USER_MAPPING")
USER_MAPPING = dict(DEFAULT_USER_MAPPING)


async def fetch_user_mapping(retries: int = 5, delay: float = 2.0):
    global USER_MAPPING
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{API_URL}/api/users/mapping", timeout=10.0)
                if response.status_code == 200:
                    fetched = response.json()
                    USER_MAPPING.update(fetched)
                    logging.info(f"Synchronized {len(USER_MAPPING)} user mappings from orchestrator.")
                    return True
        except Exception as e:
            logging.warning(f"Attempt {attempt + 1}/{retries} error fetching user mapping: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(delay)
    return False


async def register_new_user(telegram_id: str, username: str):
    global USER_MAPPING
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{API_URL}/api/users/register", json={"telegram_id": telegram_id, "username": username}, timeout=10.0
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    platform_id = data.get("platform_user_id")
                    USER_MAPPING[telegram_id] = platform_id
                    logging.info(f"Successfully registered user: {username} ({telegram_id}) -> {platform_id}")
                    return platform_id
    except Exception as e:
        logging.error(f"Error registering user: {e}")
    return None


# Logging setup
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


LOG_FILE = "gateway.log"
log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_name, logging.INFO)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))

file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(JsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S%z"))

logging.basicConfig(level=log_level, force=True, handlers=[stream_handler, file_handler])


async def handle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    await handle_text(update, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    encoded_text = MessageProcessor.encode(update.message.text)
    await process_request(update, context, text=encoded_text)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not (update.message.voice or update.message.audio):
        return

    voice_obj = update.message.voice or update.message.audio
    file_obj = await voice_obj.get_file()
    file_bytes = await file_obj.download_as_bytearray()
    mime = getattr(voice_obj, "mime_type", "audio/ogg") or "audio/ogg"
    filename = getattr(voice_obj, "file_name", "voice.ogg") or "voice.ogg"

    caption = update.message.caption or ""
    await process_request(update, context, text=caption, file_bytes=file_bytes, file_name=filename, file_mime=mime)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja fotos e imágenes enviadas por el usuario."""
    if not update.message or not update.message.photo:
        return

    # Descargar la versión con mayor resolución (última en la lista)
    photo_obj = update.message.photo[-1]
    file_obj = await photo_obj.get_file()
    file_bytes = await file_obj.download_as_bytearray()

    caption = update.message.caption or ""
    await process_request(update, context, text=caption, file_bytes=file_bytes, file_name="photo.jpg", file_mime="image/jpeg")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja documentos, código, PDFs o logs adjuntos."""
    if not update.message or not update.message.document:
        return

    doc_obj = update.message.document
    file_obj = await doc_obj.get_file()
    file_bytes = await file_obj.download_as_bytearray()

    filename = doc_obj.file_name or "document.bin"
    mime = doc_obj.mime_type or "application/octet-stream"
    caption = update.message.caption or ""

    await process_request(update, context, text=caption, file_bytes=file_bytes, file_name=filename, file_mime=mime)


async def handle_callback_query(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    """Maneja las pulsaciones de botones inline (Aprobar/Rechazar planes, etc.)."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = str(query.from_user.id)
    platform_user_id = USER_MAPPING.get(user_id, "unknown")

    logging.info(f"Callback query received: {data} from user {platform_user_id} ({user_id})")

    # Botones de Aprobación HITL
    if data.startswith("approve_plan:") or data.startswith("reject_plan:"):
        action, plan_id = data.split(":")
        act_name = "approve" if "approve" in action else "reject"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(
                    f"{API_URL}/api/plans/action",
                    json={"plan_id": plan_id, "action": act_name, "reviewer_user_id": platform_user_id},
                )
                if res.status_code == 200:
                    if act_name == "approve":
                        await query.edit_message_text(f"✅ <b>Plan {plan_id} Aprobado</b>. La ejecución ha comenzado.", parse_mode=ParseMode.HTML)
                    else:
                        await query.edit_message_text(f"❌ <b>Plan {plan_id} Rechazado</b>.", parse_mode=ParseMode.HTML)
                else:
                    await query.edit_message_text(f"⚠️ Error procesando el plan: {res.text}")
        except Exception as e:
            await query.edit_message_text(f"⚠️ Error conectando al orquestador: {e}")

    elif data.startswith("keep_plan:") or data.startswith("change_plan:"):
        act, job_id = data.split(":")
        if act == "keep_plan":
            await query.edit_message_text("🥩 <b>Plan confirmado:</b> Asado en marcha.", parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text("❌ <b>Plan modificado/cancelado.</b>", parse_mode=ParseMode.HTML)


async def process_request(
    update: Update, _context: ContextTypes.DEFAULT_TYPE, text=None, file_bytes=None, file_name=None, file_mime=None
):
    telegram_user_id = str(update.message.from_user.id)
    platform_user_id = USER_MAPPING.get(telegram_user_id)

    if not platform_user_id:
        if telegram_user_id in DEFAULT_USER_MAPPING:
            platform_user_id = DEFAULT_USER_MAPPING[telegram_user_id]
            USER_MAPPING[telegram_user_id] = platform_user_id
        else:
            await fetch_user_mapping(retries=2, delay=1.0)
            platform_user_id = USER_MAPPING.get(telegram_user_id)

    if not platform_user_id:
        raw_username = update.message.from_user.username or update.message.from_user.first_name or f"user_{telegram_user_id}"
        platform_user_id = await register_new_user(telegram_user_id, raw_username)
        if not platform_user_id:
            await update.message.reply_text("Error registrando usuario. Intenta más tarde.")
            return

    chat_id = update.message.chat_id
    thinking_message = await update.message.reply_text("<i>Thinking...</i>", parse_mode=ParseMode.HTML)

    try:
        async with httpx.AsyncClient() as client:
            files = None
            data = {"thread_id": str(chat_id)}

            if file_bytes:
                files = {"file": (file_name or "file.bin", bytes(file_bytes), file_mime or "application/octet-stream")}
            if text:
                data["text"] = text

            headers = {"X-User-ID": platform_user_id}
            stream_url = f"{API_URL}/stream"

            full_response = ""
            last_update_time = 0

            async with client.stream("POST", stream_url, data=data, files=files, headers=headers, timeout=900.0) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    logging.error(f"API Error: {response.status_code} - {error_text.decode()}")
                    await thinking_message.edit_text(f"Error en backend (Status: {response.status_code}).")
                    return

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        content = line[6:]
                        if content == "[DONE]":
                            break

                        try:
                            chunk = json.loads(content)
                            if "status" in chunk:
                                status_msg = chunk["status"]
                                current_time = asyncio.get_event_loop().time()
                                if current_time - last_update_time > 1.5:
                                    with contextlib.suppress(Exception):
                                        await thinking_message.edit_text(f"⏳ <i>{status_msg}</i>", parse_mode=ParseMode.HTML)
                                    last_update_time = current_time
                                continue

                            token = chunk.get("text", "")
                            full_response += token

                            current_time = asyncio.get_event_loop().time()
                            if current_time - last_update_time > 1.0 and full_response.strip():
                                formatted_partial = MessageProcessor.decode(full_response)
                                with contextlib.suppress(Exception):
                                    await thinking_message.edit_text(formatted_partial + "...", parse_mode=ParseMode.HTML)
                                last_update_time = current_time
                        except json.JSONDecodeError:
                            continue
                        except Exception as e:
                            logging.warning(f"Error during partial update: {e}")
                            continue

            if full_response:
                raw_chunks = MessageProcessor.split_message(full_response, 3000)
                for i, raw_chunk in enumerate(raw_chunks):
                    formatted_chunk = MessageProcessor.decode(raw_chunk)
                    if len(formatted_chunk) > 4000:
                        send_text = raw_chunk[:4000]
                        parse_mode = None
                    else:
                        send_text = formatted_chunk
                        parse_mode = ParseMode.HTML

                    try:
                        if i == 0:
                            await thinking_message.edit_text(send_text, parse_mode=parse_mode)
                        else:
                            await asyncio.sleep(0.35)
                            await _context.bot.send_message(chat_id=chat_id, text=send_text, parse_mode=parse_mode)
                    except Exception as e:
                        logging.warning(f"Failed sending chunk {i}: {e}")
                        try:
                            if i == 0:
                                await thinking_message.edit_text(raw_chunk[:4000])
                            else:
                                await asyncio.sleep(0.35)
                                await _context.bot.send_message(chat_id=chat_id, text=raw_chunk[:4000])
                        except Exception as ex:
                            logging.error(f"Fallback plain text failed: {ex}")
            else:
                await thinking_message.edit_text("El agente retornó una respuesta vacía.")

    except Exception:
        logging.exception("Error during API request")
        await thinking_message.edit_text("Ocurrió un error al procesar la solicitud.")


async def heartbeat_loop():
    while True:
        logging.info("💓 Heartbeat: Telegram Gateway is active and listening")
        await asyncio.sleep(600)


async def post_init(_application):
    asyncio.create_task(heartbeat_loop())
    asyncio.create_task(fetch_user_mapping(retries=5, delay=2.0))


if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN or not API_URL:
        print("Error: TELEGRAM_BOT_TOKEN and API_URL must be set in .env")
        exit(1)

    asyncio.run(fetch_user_mapping())

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # Handlers Multimodales y Comandos
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(CommandHandler(["start", "help", "garmin_login", "garmin_sync"], handle_command))

    # Handler de Botones Interactivos (HITL Approvals)
    application.add_handler(CallbackQueryHandler(handle_callback_query))

    print("Telegram Gateway started with Multimodal & Approval Callback handlers...")
    application.run_polling()
