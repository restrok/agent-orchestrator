import os
import json
import logging
import httpx
import asyncio
from dotenv import load_dotenv
import re
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
        # Remove excessive whitespace
        text = re.sub(r'\n{3,}', '\n\n', text.strip())
        return text

    @staticmethod
    def decode(text: str) -> str:
        """
        Formats AI response for Telegram MarkdownV2.
        Converts tables to lists and ensures vertical spacing.
        """
        if not text:
            return ""

        # Handle literal \n if they come as strings
        text = text.replace("\\n", "\n")

        # 1. Table-to-List Transformation (Robust Version)
        lines = text.split('\n')
        processed_lines = []
        in_table = False
        
        emojis = {
            "distance": "📍", "distancia": "📍",
            "hr": "❤️", "bpm": "❤️", "frecuencia": "❤️",
            "pace": "⏱️", "ritmo": "⏱️",
            "power": "⚡", "potencia": "⚡",
            "time": "🕒", "tiempo": "🕒", "duración": "🕒",
            "calories": "🔥", "calorías": "🔥",
            "vo2": "📈", "sleep": "😴", "sueño": "😴",
            "hrv": "⚖️"
        }

        for line in lines:
            stripped = line.strip()
            if stripped.startswith('|') and stripped.endswith('|'):
                parts = [p.strip() for p in stripped.split('|') if p.strip()]
                if not parts or all(re.match(r'[:\-]+', p) for p in parts):
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
                    processed_lines.append(f"{icon}**{metric_name}:** {value}")
                else:
                    processed_lines.append(f"• {parts[0]}")
            else:
                if in_table:
                    in_table = False
                    processed_lines.append("")
                processed_lines.append(line)
        
        text = '\n'.join(processed_lines)

        # 2. Aggressive Spacing for Sections
        # Markers that need a double newline before them (Structural)
        structural_markers = [r'###', r'🔹', r'⚠️', r'✅', r'📅', r'🔔', r'🏃', r'🔋', r'💪', r'🧘‍♂️']
        for marker in structural_markers:
            # Only inject if preceded by a character that isn't a newline or space
            text = re.sub(r'([^\n\s])\s*(%s)' % marker, r'\1\n\n\2', text)

        # 3. MarkdownV2 Escaping
        reserved = r"\_*[]()~`>#+-=|{}.!"
        def escape_match(match):
            return '\\' + match.group(0)
        
        text = re.sub(r'([%s])' % re.escape(reserved), escape_match, text)

        # 4. Restoration of intended formatting
        # Headers: ### -> Bold
        text = re.sub(r"\\#\\#\\# (.*?)(?:\n|$)", r"*\1*\n", text)
        # Bold: \*\*text\*\* -> *text* (Telegram V2 uses single * for bold)
        text = re.sub(r'\\\*\\\*(.*?)\\\*\\\*', r'*\1*', text)
        # Italics: \_text\_ -> _text_
        text = re.sub(r'\\\_(.*?)\\\_', r'_\1_', text)

        # Final Cleanup: Max 2 newlines
        text = re.sub(r'\n{3,}', '\n\n', text)

        return text.strip()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = os.getenv("API_URL")

# Load user mapping
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
with open(CONFIG_PATH, "r") as f:
    config = json.load(f)
    USER_MAPPING = config.get("users", {})

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

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

async def process_request(update: Update, context: ContextTypes.DEFAULT_TYPE, text=None, voice_bytes=None, voice_mime=None):
    telegram_user_id = str(update.message.from_user.id)
    platform_user_id = USER_MAPPING.get(telegram_user_id)

    if not platform_user_id:
        logging.warning(f"Unauthorized access attempt from Telegram ID: {telegram_user_id}")
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return

    chat_id = update.message.chat_id
    
    # Send initial "thinking" message
    thinking_message = await update.message.reply_text(r"_Thinking\.\.\._", parse_mode=ParseMode.MARKDOWN_V2)

    try:
        async with httpx.AsyncClient() as client:
            files = None
            data = {
                "thread_id": str(chat_id)
            }
            
            if voice_bytes:
                files = {'file': ('voice.ogg', bytes(voice_bytes), voice_mime)}
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
            
            async with client.stream("POST", stream_url, data=data, files=files, headers=headers, timeout=150.0) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    logging.error(f"API Error: {response.status_code} - {error_text.decode()}")
                    await thinking_message.edit_text(f"Error communicating with the backend (Status: {response.status_code}).")
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
                            if current_time - last_update_time > 1.0: # Update every 1s
                                if full_response.strip():
                                    # DECODE the partial response to ensure valid MarkdownV2
                                    formatted_partial = MessageProcessor.decode(full_response)
                                    await thinking_message.edit_text(formatted_partial + "...", parse_mode=ParseMode.MARKDOWN_V2)
                                    last_update_time = current_time
                        except json.JSONDecodeError:
                            continue
                        except Exception as e:
                            logging.warning(f"Error during partial update: {e}")
                            # Fallback if MarkdownV2 fails during stream
                            continue

            if full_response:
                formatted_final = MessageProcessor.decode(full_response)
                try:
                    await thinking_message.edit_text(formatted_final, parse_mode=ParseMode.MARKDOWN_V2)
                except Exception:
                    logging.warning("MarkdownV2 parsing failed, falling back to plain text")
                    await thinking_message.edit_text(full_response)
            else:
                await thinking_message.edit_text("The agent returned an empty response.")

    except Exception:
        logging.exception("Error during API request")
        await thinking_message.edit_text("An error occurred while processing your request.")

if __name__ == '__main__':
    if not TELEGRAM_BOT_TOKEN or not API_URL:
        print("Error: TELEGRAM_BOT_TOKEN and API_URL must be set in .env")
        exit(1)

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    
    print("Telegram Gateway started...")
    application.run_polling()
