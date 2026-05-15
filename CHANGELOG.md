# Changelog

All notable changes to this project will be documented in this file.

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

### Changed
- **Routing Reliability:** Implemented `InjectedState` in the Orchestrator to prevent LLM hallucinations of user IDs. Routing now uses validated platform IDs.
- **Increased Timeouts:** Adjusted request timeouts (Orchestrator: 120s, Gateway: 150s) to accommodate slow reasoning or data retrieval from Expert Agents.
- **Agnostic Supervisor:** Refactored the Supervisor's system prompt to be task-neutral while enforcing strict Telegram-friendly formatting (no tables, double line-breaks for readability).

### Fixed
- **State Persistence:** Added `add_messages` reducer to the LangGraph state to prevent message history from being overwritten during tool-calling cycles.
- **Crash Fix:** Resolved a `ValueError: contents are required` in the Google GenAI SDK caused by broken message sequences in the state.

## [1.0.0] - 2026-05-01
- Initial release of the Hub-and-Spoke Agent Orchestrator.
- Basic LangGraph implementation with Biometric Expert integration.
- Telegram Gateway with voice and text support.
