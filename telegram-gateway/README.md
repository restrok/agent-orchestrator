# Telegram Gateway (Phase 1)

A lightweight microservice to forward Telegram messages to a backend API with multi-user support.

## Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configuration:**
   - Copy `.env.example` to `.env` and fill in your `TELEGRAM_BOT_TOKEN` and `API_URL`.
   - Update `config.json` with the mapping of Telegram User IDs to Platform User IDs (e.g., `fsirio`).

3. **Run the bot:**
   ```bash
   python main.py
   ```

## Features
- **Multi-User Mapping:** Authorization based on Telegram User ID.
- **Context Forwarding:** Sends `thread_id` (Telegram Chat ID) and `X-User-ID` header to the backend.
- **Logging:** Simple console logging for monitoring requests.
