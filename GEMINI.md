# 💎 Project Instructions

## 🏗️ Architecture & Infrastructure
- **Production Host:** Services are deployed on a Raspberry Pi 5.
- **Homelab Path:** Deployment files (Docker Compose) are located in `~/homelab/agent-orchestrator/`.
- **Environment Files:** 
  - Orchestrator uses `.env.orchestrator`.
  - Gateway uses `.env.gateway`.
- **Command Preference:** Use `docker-compose` for existing stacks in this repo if `docker compose` (v2 plugin) is not responding as expected, though `docker-compose` is the verified working command for the `agent-orchestrator` stack.

## 🤖 LLM Configuration
- **Primary Local Provider:** LM Studio.
- **Current Host:** `http://<lm-studio-host>:1234/v1` (Updated 2026-06-06).
- **Default Fallback:** `llm_factory.py` contains hardcoded fallbacks to the current network IP.

## 🛠️ Workflows
- **IP Updates:** When the LM Studio IP changes, update:
  1. `orchestrator-api/app/llm_factory.py` (Default fallback).
  2. `orchestrator-api/.env.example`.
  3. `~/homelab/agent-orchestrator/.env.orchestrator` (Production).
  4. Restart: `cd ~/homelab/agent-orchestrator && docker-compose up -d`.

## 📂 File Locations
- **Orchestrator Code:** `orchestrator-api/`
- **Gateway Code:** `telegram-gateway/`
- **Database:** SQLite DB located at `orchestrator-api/app/data/orchestrator.db`.
