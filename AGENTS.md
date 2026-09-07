# 🤖 Agent Orchestrator (Infrastructure & Tools)

## 🏗️ Production Environment
- **Host:** Raspberry Pi 5
- **Deployment Path:** `~/homelab/agent-orchestrator/`
- **Docker Command:** Use `docker-compose` (verified working).
- **Restart Command:** `cd ~/homelab/agent-orchestrator && docker-compose up -d`

## 🌐 Network & IPs
- **LM Studio Host:** `http://<lm-studio-host>:1234/v1` (Primary LLM Provider)
- **Orchestrator API (Static):** `http://orchestrator:8001`
- **Telegram Gateway (Static):** `http://gateway:8002`

## 📂 Configuration Files
- **Orchestrator Env:** `~/homelab/agent-orchestrator/.env.orchestrator`
- **Gateway Env:** `~/homelab/agent-orchestrator/.env.gateway`

## 🛠️ Maintenance Workflows
- **Update LLM IP:**
  1. Modify `orchestrator-api/app/llm_factory.py` default.
  2. Modify `orchestrator-api/.env.example`.
  3. Modify `~/homelab/agent-orchestrator/.env.orchestrator`.
  4. Run `docker-compose up -d` in the homelab directory.
