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
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# Load environment variables
load_dotenv()


class MessageProcessor:
    @staticmethod
    def encode(text: str) -> str:
        """
        Sanitizes and prepares user input for the AI.
        """
        if not text:
            return ""
        # Remove excessive whitespace and strip each line
        lines = [line.strip() for line in text.strip().split("\n")]
        text = "\n".join(lines)
        return re.sub(r"\n{3,}", "\n\n", text)

    @staticmethod
    def split_message(text: str, max_length: int = 3000) -> list[str]:
        """
        Splits a message into chunks, preferably at paragraphs or newlines.
        """
        if not text:
            return []
        if len(text) <= max_length:
            return [text]

        chunks = []
        while text:
            if len(text) <= max_length:
                chunks.append(text)
                break

            # 1. Prefer paragraph break (\n\n)
            split_at = text.rfind("\n\n", 0, max_length)
            if split_at != -1 and split_at > max_length // 3:
                split_len = 2
            else:
                # 2. Prefer single newline (\n)
                split_at = text.rfind("\n", 0, max_length)
                if split_at != -1 and split_at > max_length // 3:
                    split_len = 1
                else:
                    # 3. Prefer space
                    split_at = text.rfind(" ", 0, max_length)
                    if split_at != -1 and split_at > max_length // 3:
                        split_len = 1
                    else:
                        # 4. Hard cut
                        split_at = max_length
                        split_len = 0

            chunk = text[:split_at].strip()
            if chunk:
                chunks.append(chunk)
            text = text[split_at + split_len:].strip()
        return chunks

    @staticmethod
    def decode(text: str) -> str:
        """
        Robustly formats text for Telegram's HTML mode.
        """
        if not text:
            return ""

        # 1. Initial cleanup and Handle literal \n
        text = text.replace("\\n", "\n")

        # 2. Table-to-List Transformation
        lines = text.split("\n")
        processed_lines = []
        in_table = False

        emojis = {
            "heart": "❤️",
            "hr": "❤️",
            "bpm": "❤️",
            "frecuencia": "❤️",
            "distance": "📍",
            "distancia": "📍",
            "pace": "⏱️",
            "ritmo": "⏱️",
            "power": "⚡",
            "potencia": "⚡",
            "time": "🕒",
            "tiempo": "🕒",
            "duración": "🕒",
            "calories": "🔥",
            "calorías": "🔥",
            "vo2": "📈",
            "sleep": "😴",
            "sueño": "😴",
            "hrv": "⚖️",
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

        # 3. Structural Headers & Spacing
        # Convert ### to Bold and ensure vertical spacing
        text = re.sub(r"^###\s+(.*)$", r"\n\n<b>\1</b>\n", text, flags=re.MULTILINE)

        # Ensure structural emojis have proper spacing
        structural_markers = [r"🔹", r"⚠️", r"✅", r"📅", r"🔔", r"🏃", r"🔋", r"💪", r"🧘‍♂️", r"🎯"]
        for marker in structural_markers:
            text = re.sub(rf"([^\n])\s*({marker})", r"\1\n\n\2", text)
            text = re.sub(rf"({marker})([^\s])", r"\1 \2", text)

        # 4. HTML Escaping
        text = html.escape(text, quote=False)

        # 5. Restoration of Formatting Entities (using HTML tags)
        # Inline code: `code`
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        # Bold: **text**
        text = re.sub(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
        # Single asterisk bold/italic: *text*
        text = re.sub(r"(?<!\*)\*(?!\s|\*)(.+?)(?<!\s|\*)\*(?!\*)", r"<b>\1</b>", text, flags=re.DOTALL)
        # Italic: _text_ (only when surrounded by non-word characters or space)
        text = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"<i>\1</i>", text, flags=re.DOTALL)

        # 6. Final Cleanup
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = os.getenv("API_URL")

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

# Global user mapping cache loaded from environment defaults
DEFAULT_USER_MAPPING = _load_env_json("DEFAULT_USER_MAPPING")
USER_MAPPING = dict(DEFAULT_USER_MAPPING)


async def fetch_user_mapping(retries: int = 5, delay: float = 2.0):
    """Fetches the latest user mapping from the orchestrator with retry."""
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
                    logging.error(f"Failed to fetch user mapping: {response.status_code}")
        except Exception as e:
            logging.warning(f"Attempt {attempt + 1}/{retries} error fetching user mapping: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(delay)
    return False


async def register_new_user(telegram_id: str, username: str):
    """Registers a new user with the orchestrator."""
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
                    logging.info(f"Successfully registered new user: {username} ({telegram_id}) -> {platform_id}")
                    return platform_id
            logging.error(f"Failed to register user: {response.text}")
    except Exception as e:
        logging.error(f"Error registering user: {e}")
    return None


# Logging setup
class JsonFormatter(logging.Formatter):
    """Custom formatter to output logs in JSON format for machine analysis."""

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

# Console Handler (Human-readable)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))

# Unified File Handler (Machine-readable JSON)
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(JsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S%z"))

# Root configuration
logging.basicConfig(level=log_level, force=True, handlers=[stream_handler, file_handler])


async def handle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles slash commands by treating them as text requests.
    """
    if not update.message or not update.message.text:
        return

    await handle_text(update, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    encoded_text = MessageProcessor.encode(update.message.text)
    await process_request(update, context, text=encoded_text)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.voice:
        return

    voice_file = await update.message.voice.get_file()
    voice_bytes = await voice_file.download_as_bytearray()

    await process_request(update, context, voice_bytes=voice_bytes, voice_mime=update.message.voice.mime_type)


async def process_request(
    update: Update, _context: ContextTypes.DEFAULT_TYPE, text=None, voice_bytes=None, voice_mime=None
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
        # Silent Registration
        logging.info(f"New user detected: {telegram_user_id}. Attempting silent registration.")
        raw_username = (
            update.message.from_user.username or update.message.from_user.first_name or f"user_{telegram_user_id}"
        )
        platform_user_id = await register_new_user(telegram_user_id, raw_username)

        if not platform_user_id:
            logging.error(f"Could not register user {telegram_user_id}")
            await update.message.reply_text(
                "Sorry, an error occurred during your registration. Please try again later."
            )
            return

    chat_id = update.message.chat_id

    # Send initial "thinking" message
    thinking_message = await update.message.reply_text("<i>Thinking...</i>", parse_mode=ParseMode.HTML)

    try:
        async with httpx.AsyncClient() as client:
            files = None
            data = {"thread_id": str(chat_id)}

            if voice_bytes:
                files = {"file": ("voice.ogg", bytes(voice_bytes), voice_mime)}
            else:
                data["text"] = text

            # Phase 2: SSE Streaming
            headers = {
                "X-User-ID": platform_user_id,
            }

            # Using /stream endpoint for SSE
            stream_url = f"{API_URL}/stream"

            full_response = ""
            last_update_time = 0

            async with client.stream(
                "POST", stream_url, data=data, files=files, headers=headers, timeout=610.0
            ) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    logging.error(f"API Error: {response.status_code} - {error_text.decode()}")
                    await thinking_message.edit_text(
                        f"Error communicating with the backend (Status: {response.status_code})."
                    )
                    return

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        content = line[6:]
                        if content == "[DONE]":
                            break

                        try:
                            chunk = json.loads(content)
                            token = chunk.get("text", "")
                            full_response += token

                            # Throttle updates to Telegram to avoid rate limits
                            current_time = asyncio.get_event_loop().time()
                            if current_time - last_update_time > 1.0 and full_response.strip():
                                # DECODE the partial response to ensure valid HTML
                                formatted_partial = MessageProcessor.decode(full_response)
                                with contextlib.suppress(Exception):
                                    await thinking_message.edit_text(
                                        formatted_partial + "...", parse_mode=ParseMode.HTML
                                    )
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
                        logging.warning(f"Failed sending chunk {i} with parse_mode={parse_mode}: {e}")
                        try:
                            if i == 0:
                                await thinking_message.edit_text(raw_chunk[:4000])
                            else:
                                await asyncio.sleep(0.35)
                                await _context.bot.send_message(chat_id=chat_id, text=raw_chunk[:4000])
                        except Exception as ex:
                            logging.error(f"Fallback plain text failed for chunk {i}: {ex}")
            else:
                await thinking_message.edit_text("The agent returned an empty response.")

    except Exception:
        logging.exception("Error during API request")
        await thinking_message.edit_text("An error occurred while processing your request.")


async def heartbeat_loop():
    while True:
        logging.info("💓 Heartbeat: Telegram Gateway is active and listening")
        await asyncio.sleep(600)  # Every 10 mins


async def post_init(_application):
    """Start background tasks."""
    asyncio.create_task(heartbeat_loop())
    asyncio.create_task(fetch_user_mapping(retries=5, delay=2.0))


if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN or not API_URL:
        print("Error: TELEGRAM_BOT_TOKEN and API_URL must be set in .env")
        exit(1)

    # Initial sync of user mapping
    asyncio.run(fetch_user_mapping())

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # Handle both regular text and commands (like /garmin-login)
    application.add_handler(MessageHandler(filters.TEXT, handle_text))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))

    print("Telegram Gateway started...")
    application.run_polling()
