import os
import json
import logging
import httpx
import asyncio
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# Load environment variables
load_dotenv()

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
    
    await process_request(update, context, text=update.message.text)

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
    thinking_message = await update.message.reply_text("_Thinking..._", parse_mode=ParseMode.MARKDOWN_V2)

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
                                    await thinking_message.edit_text(full_response + "...")
                                    last_update_time = current_time
                        except json.JSONDecodeError:
                            continue

            if full_response:
                try:
                    await thinking_message.edit_text(full_response, parse_mode=ParseMode.MARKDOWN_V2)
                except Exception as e:
                    logging.warning(f"MarkdownV2 parsing failed, falling back to plain text: {e}")
                    await thinking_message.edit_text(full_response)
            else:
                await thinking_message.edit_text("The agent returned an empty response.")

    except Exception as e:
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
