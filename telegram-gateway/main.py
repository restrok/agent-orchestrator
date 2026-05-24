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
    def split_message(text: str, max_length: int = 4000) -> list[str]:
        """
        Splits a message into chunks, preferably at newlines.
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

            # Find the last newline within the limit
            split_at = text.rfind("\n", 0, max_length)
            if split_at == -1:
                # If no newline, split at max_length
                split_at = max_length

            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")
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
        # Bold: *text* or **text**
        text = re.sub(r"\*(\*?)(?!\s)(.+?)(?<!\s)\1\*", r"<b>\2</b>", text, flags=re.DOTALL)
        # Italic: _text_
        text = re.sub(r"_(?!\s)(.+?)(?<!\s)_", r"<i>\1</i>", text, flags=re.DOTALL)

        # 6. Final Cleanup
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = os.getenv("API_URL")

# Global user mapping cache
USER_MAPPING = {}


async def fetch_user_mapping():
    """Fetches the latest user mapping from the orchestrator."""
    global USER_MAPPING
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_URL}/api/users/mapping", timeout=10.0)
            if response.status_code == 200:
                USER_MAPPING = response.json()
                logging.info(f"Synchronized {len(USER_MAPPING)} user mappings from orchestrator.")
            else:
                logging.error(f"Failed to fetch user mapping: {response.status_code}")
    except Exception as e:
        logging.error(f"Error fetching user mapping: {e}")


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
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)


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
        # Silent Registration
        logging.info(f"New user detected: {telegram_user_id}. Attempting silent registration.")
        username = (
            update.message.from_user.username or update.message.from_user.first_name or f"user_{telegram_user_id}"
        )
        platform_user_id = await register_new_user(telegram_user_id, username)

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
                # Split the raw response
                raw_chunks = MessageProcessor.split_message(full_response, 3800)

                for i, raw_chunk in enumerate(raw_chunks):
                    formatted_chunk = MessageProcessor.decode(raw_chunk)
                    try:
                        if i == 0:
                            await thinking_message.edit_text(formatted_chunk, parse_mode=ParseMode.HTML)
                        else:
                            await update.message.reply_text(formatted_chunk, parse_mode=ParseMode.HTML)
                    except Exception as e:
                        logging.warning(f"HTML failed for chunk {i}: {e}")
                        # Fallback to plain text
                        if i == 0:
                            await thinking_message.edit_text(raw_chunk)
                        else:
                            await update.message.reply_text(raw_chunk)
            else:
                await thinking_message.edit_text("The agent returned an empty response.")

    except Exception:
        logging.exception("Error during API request")
        await thinking_message.edit_text("An error occurred while processing your request.")


if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN or not API_URL:
        print("Error: TELEGRAM_BOT_TOKEN and API_URL must be set in .env")
        exit(1)

    # Initial sync of user mapping
    asyncio.run(fetch_user_mapping())

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Handle both regular text and commands (like /garmin-login)
    application.add_handler(MessageHandler(filters.TEXT, handle_text))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))

    print("Telegram Gateway started...")
    application.run_polling()
