# Changelog

All notable changes to this project will be documented in this file.

## [1.5.0] - 2026-09-12

### Added
- **Dedicated Local Whisper ASR Container:** Integrated `onerahmet/openai-whisper-asr-webservice:latest` running `faster-whisper` (`small` model) in `docker-compose.yml`. Transcribes voice notes locally with sub-second response times and zero cloud egress.
- **Multimodal Gateway & Orchestrator:** Full support in `telegram-gateway` for photos (`filters.PHOTO`), voice notes & audio files (`filters.VOICE | filters.AUDIO`), and documents/code/logs (`filters.Document.ALL`).
- **Persistent Task Scheduler (APScheduler):** Implemented `AsyncIOScheduler` with SQLite job store (`orchestrator.db`). Allows scheduling future reminders, proactive checks, and deferred worker tasks with 0 token consumption during wait periods.
- **Deterministic Weather Tool:** Added `get_weather_forecast` via Open-Meteo API for Tigre/Buenos Aires without requiring external API keys.
- **Exocortex Brain Proactive Intents:** Integrated tools `register_intent_in_brain`, `get_intent_from_brain`, and `update_intent_in_brain` to persist proactive tasks into the Exocortex long-term memory system.
- **Multi-Worker Orchestration (Sync & Async):** Support for fast synchronous tools and long-running asynchronous subagents with automated 45-second progress updates sent directly to Telegram.
- **Human-in-the-Loop (HITL) Security:** Non-admin requests (e.g. Mercedes) that trigger system actions generate an approval plan (`plan_<id>`) and send interactive Telegram inline buttons (`Aprobar` / `Rechazar`) to the administrator (`fsirio`).

### Changed
- **Primary Orchestrator LLM:** Switched model to `deepseek-v4.1-flash` via Ollama Cloud API.
- **Audio Processing Pipeline:** Routed all incoming Telegram voice notes to local Whisper ASR at `http://whisper:9000/asr`, with graceful fallback to Gemini if local Whisper is unreachable.

## [1.3.0] - 2026-05-15

### Added
- **Swarm Architecture Integration:** Migrated the Orchestrator from a centralized Supervisor model to a decentralized "Swarm" routing topology.
- **Intent Router Node:** Implemented a new asynchronous `node_router` that uses structured LLM output to classify and delegate requests.
- **Direct Expert Handoffs:** Added a dedicated `biometric_expert_node` for direct handoffs, bypassing the supervisor for specialized biometric queries.
- **Loop Prevention System:** Integrated a state-based loop counter that automatically halts execution if an agent enters an infinite tool-calling cycle (threshold: 4).
- **TypedDict State Management:** Refactored `AgentState` to use `TypedDict` and native LangGraph reducers for more robust message history and performance.
- **Modular Models:** Extracted state and classifier definitions into a dedicated `models.py` for better project modularity.

### Changed
- **Graph Topology:** Updated the LangGraph flow to START -> Router -> {Expert Node | Supervisor Node}.
- **Logging Traceability:** Added detailed logs for Intent Classification and Rationale to improve observability during agent reasoning.

### Fixed
- **State Consistency:** Ensured all nodes use asynchronous execution (A2A) to prevent blocking the main event loop during long-running expert calls.

## [1.2.0] - 2026-05-10

### Added
- **Proactive Notification Infrastructure:** Implemented a new `POST /api/notify` endpoint in the Orchestrator API, allowing external agents to push alerts to Telegram users.
- **CI/CD Pipeline:** Added GitHub Actions workflow for automated linting, formatting checks, and multi-architecture Docker image builds (amd64/arm64).
- **Docker-Compose Production Example:** Updated the root `docker-compose.yml` with a production-ready configuration including internal networking and image references.
- **User Mapping Utility:** The Orchestrator now correctly loads and inverses user mappings from `config.json` to route proactive alerts by platform ID.

### Changed
- **Code Organization:** Reordered initialization logic in `main.py` to ensure logging is available during configuration loading.
- **Dependency Management:** Integrated `httpx` for outbound communication with Telegram and Expert Agents.

## [1.1.0] - 2026-05-05

### Added
- **Detailed Tool Logging:** The Orchestrator now logs the exact query sent to Expert Agents for better transparency and debugging.
- **MarkdownV2 Support:** Enhanced Telegram Gateway to support MarkdownV2, enabling rich text formatting (bold, italics, lists).
- **Graceful Fallbacks:** Added error handling for malformed Markdown to ensure messages are still delivered as plain text.
