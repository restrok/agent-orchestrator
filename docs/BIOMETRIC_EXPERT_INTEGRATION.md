# 🤖 Task: Expert Agent Integration - Biometric AI Coach

## 🎯 Objective
Integrate the **Biometric AI Platform** as a specialized "Expert Agent" within the `telegram-agent-orchestrator` ecosystem. Enable the Orchestrator to route physiological, training, and health-related queries to the Biometric Expert while maintaining user identity isolation.

## 📍 Expert Agent Context (Biometric AI Platform)
- **Base URL:** `http://localhost:8000/v1` (or the configured Biometric API host)
- **Primary Interface:** OpenAI-compatible Chat Completion at `/chat/completions`.
- **Identity Header:** `X-User-ID` (Required for all requests to ensure data isolation).
- **Core Capabilities:**
    - Biometric Data Retrieval (Garmin sync, BigQuery analysis).
    - Physiological Efficiency Analysis (Aerobic Decoupling, Cardiac Drift).
    - Workout Library Management (Listing, pruning, and uploading to Garmin devices).
    - Health & Medication Logging (Persisting user-reported symptoms).

## 🛠️ Implementation Requirements

### 1. Expert Registration
- Add a new Expert configuration for the **Biometric Coach**.
- Map intent keywords to this expert: `run`, `training`, `workout`, `sleep`, `HRV`, `heart rate`, `garmin`, `health`, `headache`, `medication`, `10k`.

### 2. A2A Protocol Implementation (Agent-to-Agent)
- Configure the Orchestrator to call the `/v1/chat/completions` endpoint of the Biometric Expert API when the Supervisor identifies a relevant intent.
- **Header Injection:** Ensure the user's unique identifier (derived from their Telegram ID) is passed in the `X-User-ID` header to the Expert API.
- **Streaming Support:** Utilize the SSE (Server-Sent Events) capabilities of the Biometric API to provide real-time feedback to the Telegram Gateway.

### 3. Proactive Notification Hook (Phase 4 Foundation)
- Implement a skeleton for a "Morning Briefing" service within the orchestrator.
- **Logic:** The service should trigger a non-interactive call to the Biometric Expert's `/biometric/retrieve` or `/chat/completions` (with a hidden system prompt like *"Analyze my sleep and HRV from last night and provide a 1-sentence recovery status"*) and push the result to the user's Telegram chat.

### 4. Health Context Passthrough
- Enable the orchestrator to capture unstructured health reports (e.g., *"My head still hurts, I took an Enantyum"*) and forward them to the Biometric Expert's health logging tool via the universal chat interface.

## 📝 Success Criteria
- [ ] Supervisor correctly routes "How was my sleep?" to the Biometric Expert.
- [ ] User ID is correctly forwarded via `X-User-ID` header.
- [ ] Response from the Biometric Expert (including tables/markdown) is correctly rendered in the Telegram UI.
- [ ] SSE Stream works without breaking the Telegram Gateway connection.

---
**Technical Note:** The Biometric API is running **v0.2.0** with **SDK v0.7.0** and is ready for integration.
