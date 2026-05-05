# Changelog

All notable changes to this project will be documented in this file.

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
