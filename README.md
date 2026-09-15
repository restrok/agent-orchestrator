# Agent Orchestrator

A modular, scalable **Swarm Architecture** agentic system designed to coordinate multiple specialized Expert Agents through decentralized handoffs. Built with **LangGraph**, **FastAPI**, **python-telegram-bot**, **Faster-Whisper ASR**, and **APScheduler**.

## 🚀 Overview

The **Agent Orchestrator** acts as the high-speed router of a multi-agent ecosystem in the homelab. Instead of a single monolithic bot or a strictly centralized supervisor, this project implements an **Agent-to-Agent (A2A)** protocol where an Intent Router analyzes user requests and immediately hands them off to specialized "Expert Agents" (e.g., Biometric Coach, Finance Expert) or orchestrates local Autonomous Workers.

### Key Features
- **Swarm Architecture:** Decentralized handoffs to specialized expert nodes and autonomous background workers.
- **Local Speech-to-Text (Whisper ASR):** 100% private, on-premise voice note and audio transcription using a dedicated `faster-whisper` container (`whisper-small` engine) with multi-language auto-detection (Spanish Rioplatense & English). Zero cloud egress for voice notes.
- **Full Multimodal Intake:** Native Telegram support for voice notes (`.ogg`, `.mp3`), high-res photos (`.jpg`, `.png`), documents, logs, and code files (`.py`, `.pdf`, `.sh`, `.yml`).
- **Persistent Task Scheduler:** Powered by `APScheduler` with SQLite job store (`orchestrator.db`). Supports scheduled weather forecasts, reminders, and proactive evaluation without idle token burn (0 tokens while waiting).
- **Exocortex Brain MCP Integration:** Durable intent persistence (`register_intent_in_brain`, `get_intent_from_brain`, `update_intent_in_brain`) connected directly to the Exocortex long-term memory system.
- **Multi-Worker Orchestration (Sync & Async):** Fast synchronous tool-calls or long-running asynchronous subagents with periodic 45-second progress heartbeats sent to Telegram.
- **Human-in-the-Loop (HITL) Security:** Strict role-based execution (RBAC). Work requests from unprivileged users automatically generate an interactive approval plan with inline Telegram buttons for the admin (`fsirio`).
- **Intent-Based Routing:** Automated delegation powered by `deepseek-v4.1-flash` via Ollama Cloud.
- **Proactive Notifications:** Support for asynchronous, agent-initiated alerts pushed via the Orchestrator to Telegram (`POST /api/notify`).
- **SSE Streaming:** Real-time response delivery to the Telegram Gateway.

## 🏗️ Architecture & Services

The system runs as a multi-container Docker Compose stack connected to `shared_internal_network`:

```
                           +------------------------+
                           |    Telegram Gateway    |
                           |  (python-telegram-bot) |
                           +-----------+------------+
                                       |
                   HTTP / SSE Streaming| (Photos, Audio, Docs, Commands)
                                       v
                           +------------------------+
                           |    Orchestrator API    |
                           | (FastAPI + LangGraph)  |
                           +-----+------------+-----+
                                 |            |
         +-----------------------+            +-----------------------+
         | Internal HTTP                      | Internal HTTP         |
         v                                    v                       v
+------------------+                 +------------------+    +------------------+
| Local Whisper ASR|                 |  External Agents |    | Exocortex Brain  |
| (faster-whisper) |                 | (Biometric Coach)|    |   (MCP Tools)    |
+------------------+                 +------------------+    +------------------+
```

1. **Telegram Gateway (`agent-orchestrator-gateway`):** Lightweight proxy handling Telegram polling, photo/document/voice downloads, user mapping, and interactive inline keyboard callbacks.
2. **Orchestrator API (`agent-orchestrator-api`):** Core FastAPI application running the LangGraph state machine, APScheduler, worker dispatchers, and tool registry.
3. **Whisper ASR (`agent-orchestrator-whisper`):** Dedicated microservice exposing standard transcription endpoints for voice notes, running optimized CTranslate2 / faster-whisper.

## 🛠️ Tech Stack
- **Language:** Python 3.10+
- **Orchestration:** [LangGraph](https://github.com/langchain-ai/langgraph)
- **Primary LLM:** DeepSeek V4.1 Flash (via Ollama Cloud API)
- **Speech-to-Text:** Faster-Whisper (`onerahmet/openai-whisper-asr-webservice`)
- **Scheduler:** APScheduler (AsyncIO + SQLite SQLAlchemyJobStore)
- **API Framework:** FastAPI & Uvicorn
- **Telegram Interface:** [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) (v21+)
- **Storage:** SQLite (`orchestrator.db`) for schedules, plans, and chat history

## 🚦 Getting Started

### Prerequisites
- Docker & Docker Compose.
- Ollama Cloud / API access (`OLLAMA_API_KEY`).
- Telegram Bot Token (from `@BotFather`).

### Deployment (Docker Compose)

```bash
docker compose up -d
```

### Environment Configuration

#### `orchestrator-api/.env`
```env
OLLAMA_BASE_URL=https://ollama.com
OLLAMA_API_KEY=your_ollama_key
ORCHESTRATOR_MODEL=deepseek-v4.1-flash
WHISPER_API_URL=http://whisper:9000
DATABASE_URL=sqlite:////app/data/orchestrator.db
```

#### `telegram-gateway/.env`
```env
TELEGRAM_BOT_TOKEN=your_bot_token
API_URL=http://orchestrator:8001
ADMIN_USER_ID=963420066
```

## 📄 License
This project is open-source and available under the MIT License.
