import asyncio
import html
import json
import logging
import os
import re
import shlex
from pathlib import Path
from typing import Annotated

import google.generativeai as genai
import httpx
from db import get_telegram_id, get_user_mapping, init_db, register_user
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, Request, UploadFile
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import InjectedState, ToolNode
from llm_factory import get_chat_model
from models import AgentState, IntentClassifier
from pydantic import BaseModel

load_dotenv()


# Logging
class JsonFormatter(logging.Formatter):
    """Custom formatter to output logs in JSON format for machine analysis."""

    def format(self, record):
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


LOG_FILE = "orchestrator.log"
log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_name, logging.INFO)

# Console Handler (Human-readable)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))

# Unified File Handler (Machine-readable JSON)
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(JsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S%z"))

# Root configuration
logging.basicConfig(level=log_level, force=True, handlers=[stream_handler, file_handler])
logger = logging.getLogger(__name__)


# Configuration
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
BIOMETRIC_API_URL = os.getenv("BIOMETRIC_API_URL", "http://localhost:8080/chat")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
EXOCORTEX_MCP_URL = os.getenv("EXOCORTEX_MCP_URL", "http://localhost:8765/mcp")
HOST_SSH_IP = os.getenv("HOST_SSH_IP", "127.0.0.1")
HOST_SSH_USER = os.getenv("HOST_SSH_USER", "worker")
SSH_KEY_PATH = os.getenv("SSH_KEY_PATH", "/root/.ssh/id_ed25519")
ALLOWED_WORKER_USERS = [u.strip() for u in os.getenv("ALLOWED_WORKER_USERS", "").split(",") if u.strip()]

# Initialize Database and migrate if needed
init_db()
CONFIG_PATH = Path(__file__).parent / "config.json"
if CONFIG_PATH.exists():
    try:
        with CONFIG_PATH.open() as f:
            config = json.load(f)
            users = config.get("users", {})
            for tid, pid in users.items():
                register_user(tid, pid)
        logger.info(f"Migrated users from {CONFIG_PATH} to SQLite")
        # Rename to avoid re-migration
        CONFIG_PATH.rename(f"{CONFIG_PATH}.bak")
    except Exception as e:
        logger.error(f"Failed to migrate from {CONFIG_PATH}: {e}")

genai.configure(api_key=GOOGLE_API_KEY)
model_name = os.getenv("LLM_MODEL", "gemini-1.5-flash")


app = FastAPI(title="Telegram Agent Orchestrator")


async def heartbeat_loop():
    while True:
        logging.info("💓 Heartbeat: Orchestrator API is active and listening")
        await asyncio.sleep(600)  # Every 10 mins


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(heartbeat_loop())


# --- Tools ---


@tool
async def call_biometric_expert(
    query: str,
    user_id: Annotated[str, InjectedState("user_id")],
    thread_id: Annotated[str, InjectedState("thread_id")],
):
    """Calls the Biometric Expert Agent to get health, Garmin, or profile data."""
    logger.info("--- TOOL CALL: Biometric Expert ---")
    logger.info(f"User: {user_id}")
    logger.info(f"Thread: {thread_id}")
    logger.info(f"Query sent to Expert: {query}")
    logger.info("------------------------------------")

    logger.info(f"Routing request for user {user_id} (thread: {thread_id}) to Biometric Expert")
    try:
        async with httpx.AsyncClient() as client:
            # Increased timeout to 300s (5 mins) to accommodate complex data retrieval
            response = await client.post(
                BIOMETRIC_API_URL,
                json={"messages": [{"role": "user", "content": query}], "user": user_id},
                headers={"X-User-ID": user_id},
                timeout=300.0,
            )
            if response.status_code == 200:
                data = response.json()
                # Standard OpenAI-like response parsing
                try:
                    return data["choices"][0]["message"]["content"]
                except (KeyError, IndexError):
                    return data.get("response", str(data))
            else:
                return f"Error from Biometric Expert: {response.status_code} - {response.text}"
    except httpx.TimeoutException:
        logger.error(f"Timeout calling Biometric Expert for user {user_id}")
        return "The Biometric Expert is taking too long to respond. Please try a simpler question or wait a moment."
    except Exception as e:
        logger.error(f"Error calling Biometric Expert: {e}")
        return f"An error occurred while reaching the Biometric Expert: {str(e)}"


# --- Exocortex MCP Client Helper & Tools ---


async def _invoke_mcp_tool(tool_name: str, arguments: dict) -> str:
    """Invoca una herramienta en el servidor MCP Streamable HTTP de Exocortex."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(EXOCORTEX_MCP_URL, json=payload, headers=headers)
            if response.status_code != 200:
                return f"Error connecting to Exocortex MCP: HTTP {response.status_code} - {response.text}"

            result_data = None
            for line in response.text.splitlines():
                if line.startswith("data: "):
                    json_str = line[6:].strip()
                    try:
                        data = json.loads(json_str)
                        if "result" in data:
                            result_data = data["result"]
                            break
                        if "error" in data:
                            return f"Exocortex MCP Error: {data['error']}"
                    except json.JSONDecodeError:
                        continue

            if result_data:
                if "structuredContent" in result_data:
                    return json.dumps(result_data["structuredContent"], ensure_ascii=False, indent=2)
                if "content" in result_data and len(result_data["content"]) > 0:
                    return result_data["content"][0].get("text", str(result_data["content"]))
                return json.dumps(result_data, ensure_ascii=False)
            return response.text
    except httpx.TimeoutException:
        return "Exocortex MCP request timed out."
    except Exception as e:
        logger.error(f"Error calling Exocortex MCP tool '{tool_name}': {e}")
        return f"Error contacting Exocortex: {str(e)}"


@tool
async def search_brain(query: str, limit: int = 5) -> str:
    """Busca conocimiento previo, decisiones históricas, notas de arquitectura o workflows en el Exocortex Brain."""
    logger.info(f"--- TOOL CALL: search_brain ('{query}', limit={limit}) ---")
    return await _invoke_mcp_tool("brain_search", {"query": query, "limit": limit})


@tool
async def remember_in_brain(content: str, title: str) -> str:
    """Almacena conocimiento duradero, notas importantes o decisiones en el Vault del Exocortex Brain."""
    logger.info(f"--- TOOL CALL: remember_in_brain ('{title}') ---")
    return await _invoke_mcp_tool("brain_remember", {"content": content, "title": title})


@tool
async def get_brain_health() -> str:
    """Consulta el estado de salud de los componentes de Exocortex (Vault, Gateway y Neo4j)."""
    logger.info("--- TOOL CALL: get_brain_health ---")
    return await _invoke_mcp_tool("brain_health", {})


@tool
async def get_workflow(workflow_id: str) -> str:
    """Obtiene los detalles y referencias de un workflow específico del Exocortex Brain por su ID."""
    logger.info(f"--- TOOL CALL: get_workflow ('{workflow_id}') ---")
    return await _invoke_mcp_tool("brain_get_workflow", {"workflow_id": workflow_id})


# --- Antigravity Worker Tool (Exclusive for authorized users) ---


@tool
async def call_antigravity_worker(
    task: str,
    target_project: str,
    user_id: Annotated[str, InjectedState("user_id")],
    _thread_id: Annotated[str, InjectedState("thread_id")],
) -> str:
    """Invoca al Agente Worker (Antigravity) para ejecutar tareas autónomas en el host."""
    if ALLOWED_WORKER_USERS and user_id not in ALLOWED_WORKER_USERS:
        logger.warning(f"Unauthorized access attempt to Antigravity Worker by user: {user_id}")
        return "⛔ Acceso denegado: El Agente Worker (Antigravity) está restringido a usuarios autorizados."

    logger.info(f"🚀 Lanzando Antigravity Worker para {user_id}: {task} en {target_project}")

    clean_project = target_project.strip()
    workspace_dir = clean_project if clean_project.startswith("/") else f"/home/{HOST_SSH_USER}/{clean_project}"

    key_path = SSH_KEY_PATH
    if not Path(key_path).exists() and Path("/root/.ssh/id_rsa").exists():
        key_path = "/root/.ssh/id_rsa"

    cmd_flags = "--dangerously-skip-permissions --print-timeout 15m"
    remote_cmd = f"cd {shlex.quote(workspace_dir)} && agy {cmd_flags} -p {shlex.quote(task)}"
    ssh_cmd = [
        "ssh",
        "-i",
        key_path,
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=10",
        f"{HOST_SSH_USER}@{HOST_SSH_IP}",
        remote_cmd,
    ]

    logger.info(f"Ejecutando Worker SSH en {HOST_SSH_IP} directorio {workspace_dir}...")

    try:
        proc = await asyncio.create_subprocess_exec(
            *ssh_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=960.0)
        except asyncio.TimeoutError:
            import contextlib

            with contextlib.suppress(Exception):
                proc.kill()
            return "⚠️ La tarea del Agente Worker (Antigravity) superó el tiempo límite de espera (16 minutos)."

        stdout_str = stdout.decode("utf-8", errors="replace").strip()
        stderr_str = stderr.decode("utf-8", errors="replace").strip()

        # Protect against runaway terminal output (e.g. infinite dumps) while allowing full reports
        max_output_len = 50000
        if len(stdout_str) > max_output_len:
            omitted = len(stdout_str) - max_output_len
            stdout_str = (
                stdout_str[:35000]
                + f"\n\n... [Truncado: {omitted} caracteres omitidos por tamaño] ...\n\n"
                + stdout_str[-15000:]
            )

        if proc.returncode == 0:
            return f"✅ Tarea de Antigravity completada con éxito:\n\n{stdout_str}"
        return (
            f"⚠️ Antigravity Worker finalizó con código {proc.returncode}.\n"
            f"Salida:\n{stdout_str}\n"
            f"Errores:\n{stderr_str}"
        )
    except Exception as e:
        logger.error(f"Error executing Antigravity Worker via SSH: {e}")
        return f"Error ejecutando Antigravity Worker: {str(e)}"


tools = [
    call_biometric_expert,
    search_brain,
    remember_in_brain,
    get_brain_health,
    get_workflow,
    call_antigravity_worker,
]
tool_node = ToolNode(tools)


# --- LangGraph Setup ---


async def node_router(state: AgentState):
    """
    Classifies the user's intent to route the conversation to the correct expert or handler.
    """
    logger.info("--- NODE: Router ---")

    # Get the last human message for logging
    last_message = ""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            last_message = msg.content
            break

    if not last_message:
        logger.warning("No human message found in state. Defaulting to 'unknown'.")
        return {"intent": "unknown"}

    # HARDCODED OVERRIDES: Ensure critical commands are routed instantly
    msg_lower = last_message.strip().lower()
    sync_commands = ["/garmin_sync", "/garmin_sync_full", "/garmin_login", "sync garmin"]
    if any(msg_lower.startswith(cmd) for cmd in sync_commands):
        logger.info(f"🎯 Hardcoded Override: Routing '{msg_lower}' to Biometric Expert.")
        return {"intent": "biometric_expert", "loop_count": 0}

    logger.info(f"Classifying intent for: {str(last_message)[:100]}...")

    llm = get_chat_model(model_name=model_name, temperature=0)
    provider = os.getenv("LLM_PROVIDER", "google").lower()
    if provider in ["ollama", "openai", "lmstudio"]:
        structured_llm = llm.with_structured_output(IntentClassifier, method="function_calling")
    else:
        structured_llm = llm.with_structured_output(IntentClassifier)

    try:
        classification = await structured_llm.ainvoke(state["messages"])
        logger.info(f"🔍 Intent Classified: {classification.intent.upper()}")
        logger.info(f"💡 Rationale: {classification.rationale}")
        return {"intent": classification.intent, "loop_count": 0}
    except Exception as e:
        logger.error(f"Intent classification failed: {e}. Falling back to 'unknown'.")
        return {"intent": "unknown", "loop_count": 0}


async def biometric_expert_node(state: AgentState):
    """
    Expert node that handles biometric queries by calling the Biometric API directly.
    """
    logger.info("--- NODE: Biometric Expert Handoff ---")

    # Get the last message
    query = state["messages"][-1].content
    user_id = state["user_id"]
    thread_id = state["thread_id"]

    # Call the tool logic directly
    result = await call_biometric_expert.ainvoke({"query": query, "user_id": user_id, "thread_id": thread_id})

    return {"messages": [AIMessage(content=result)]}


def route_to_agent(state: AgentState):
    """
    Router logic to determine which expert agent to hand off to.
    """
    intent = state.get("intent", "unknown")
    if intent == "biometric_expert":
        return "biometric_expert"
    return "supervisor"


async def supervisor_node(state: AgentState):
    current_loops = state.get("loop_count", 0)
    logger.info(f"--- NODE: Supervisor (Loop: {current_loops}) ---")

    logger.info(f"Supervisor node called with {len(state['messages'])} messages")
    # Log only the last few messages to keep logs clean
    for i, msg in enumerate(state["messages"][-3:]):
        logger.info(f"Message {i}: {type(msg).__name__} - {str(msg.content)[:100]}...")

    # Add system context with formatting instructions
    system_prompt_content = (
        "You are a versatile AI Orchestrator and Supervisor. Your role is to coordinate between "
        "the user and specialized Expert Agents and Tools. Analyze the user's intent, call the "
        "appropriate tools, and synthesize results into a clear, friendly response.\n\n"
        "USER CONTEXT:\n"
        f"You are currently assisting user: '{state['user_id']}'. "
        "All data retrieved via tools will be specific to this user profile.\n\n"
        "CAPABILITIES & TOOLS:\n"
        "1. Biometric Expert (`call_biometric_expert`): Consult physiological data, Garmin "
        "activities, sleep, HRV, and running training advice.\n"
        "2. Exocortex Brain:\n"
        "   - `search_brain`: Search the user's second brain for past decisions, project notes, "
        "architecture docs, or general knowledge.\n"
        "   - `remember_in_brain`: Save valuable notes, decisions, or durable context into vault.\n"
        "   - `get_brain_health`: Check Exocortex system health (Vault, Gateway, Neo4j).\n"
        "   - `get_workflow`: Inspect specific saved workflows or operational procedures by ID.\n"
        "3. Antigravity Worker (`call_antigravity_worker`):\n"
        "   - Executes autonomous engineering tasks, coding, refactors, script executions, or "
        "workspace modifications on host via `agy` (Antigravity CLI).\n"
        "   - CRITICAL SECURITY / ACCESS CONTROL: This tool is STRICTLY RESERVED for authorized "
        "users. Never deny an authorized user when they ask to execute or implement code; "
        "immediately call `call_antigravity_worker` with specified task and target_project. "
        "For unauthorized users, access is strictly forbidden.\n\n"
        "SPECIALIZED COMMANDS:\n"
        "- If the user wants to connect Garmin, they should use /garmin_login.\n"
        "- If the user wants to force a data sync, they should use /garmin_sync.\n"
        "You can inform the user about these commands if they seem lost.\n\n"
        "LANGUAGE POLICY:\n"
        "Always respond in the same language the user is speaking. If the user asks in English, "
        "respond in English. If the user asks in Spanish, respond in Spanish. This is mandatory.\n\n"
        "IMPORTANT FORMATTING RULES for Telegram (Chat Interface):\n"
        "1. DO NOT use Markdown tables. Use bulleted lists instead.\n"
        "2. VERTICAL SPACING: Use double newlines (two '\\n') between list items and sections "
        "to ensure good readability on mobile screens.\n"
        "3. Every item in a schedule or list MUST be on its own line with a blank line between items.\n"
        "4. Use **bold** for emphasis and emojis to keep the tone friendly.\n"
        "5. If you provide a schedule, use a clear 'Day - Activity' list format with ample spacing."
    )
    system_prompt = SystemMessage(content=system_prompt_content)

    llm = get_chat_model(model_name=model_name, temperature=0.1, max_tokens=4096)
    llm_with_tools = llm.bind_tools(tools)

    messages_to_send = [system_prompt] + state["messages"]

    try:
        response = await llm_with_tools.ainvoke(messages_to_send)
        return {"messages": [response], "loop_count": current_loops + 1}
    except Exception as e:
        logger.error(f"Error invoking LLM: {e}")
        raise e


def should_continue(state: AgentState):
    current_loops = state.get("loop_count", 0)
    last_message = state["messages"][-1]

    if current_loops > 6:
        logger.warning(f"⚠️ Loop count ({current_loops}) exceeded. Stopping to preserve API quota.")
        return END

    if last_message.tool_calls:
        return "tools"
    return END


llm = get_chat_model(model_name=model_name)
llm_with_tools = llm.bind_tools(tools)

memory = MemorySaver()

workflow = StateGraph(AgentState)
workflow.add_node("router", node_router)
workflow.add_node("biometric_expert", biometric_expert_node)
workflow.add_node("supervisor", supervisor_node)
workflow.add_node("tools", tool_node)

workflow.add_edge(START, "router")
workflow.add_conditional_edges(
    "router", route_to_agent, {"biometric_expert": "biometric_expert", "supervisor": "supervisor"}
)

# Expert nodes go to END or can go to supervisor for synthesis
# For now, let's have them go to END as they are specialized experts
workflow.add_edge("biometric_expert", END)

workflow.add_conditional_edges("supervisor", should_continue, {"tools": "tools", END: END})
workflow.add_edge("tools", "supervisor")

graph = workflow.compile(checkpointer=memory)

# --- Endpoints ---


class HealthCheck(BaseModel):
    status: str


class NotificationPayload(BaseModel):
    user_id: str
    agent_id: str
    message: str


class MessageProcessor:
    @staticmethod
    def decode(text: str) -> str:
        """
        Robustly formats text for Telegram's HTML mode.
        """
        if not text:
            return ""

        # 1. Initial cleanup and Handle literal \n
        text = text.replace("\\n", "\n")

        # 2. Table-to-List Transformation
        lines = text.split("\n")
        processed_lines = []
        in_table = False

        # Mapping for biometric emojis
        emojis = {
            "heart": "❤️",
            "hr": "❤️",
            "bpm": "❤️",
            "frecuencia": "❤️",
            "distance": "📍",
            "distancia": "📍",
            "pace": "⏱️",
            "ritmo": "⏱️",
            "power": "⚡",
            "potencia": "⚡",
            "time": "🕒",
            "tiempo": "🕒",
            "duración": "🕒",
            "calories": "🔥",
            "calorías": "🔥",
            "vo2": "📈",
            "sleep": "😴",
            "sueño": "😴",
            "hrv": "⚖️",
        }

        for line in lines:
            stripped = line.strip()
            # Detect table line
            if stripped.startswith("|") and stripped.endswith("|"):
                # Split and clean parts
                parts = [p.strip() for p in stripped.split("|") if p.strip()]

                # Skip separators like |:---|
                if not parts or all(re.match(r"[:\-]+", p) for p in parts):
                    continue

                if not in_table:
                    # First real line is the header, we skip it but mark we are in a table
                    in_table = True
                    processed_lines.append("")  # Initial air
                    continue

                if len(parts) >= 2:
                    metric_name = parts[0]
                    value = " | ".join(parts[1:])

                    # Find emoji
                    icon = ""
                    for e_key, emoji in emojis.items():
                        if e_key in metric_name.lower():
                            icon = emoji + " "
                            break

                    processed_lines.append(f"{icon}<b>{metric_name}:</b> {value}")
                else:
                    processed_lines.append(f"• {parts[0]}")
            else:
                if in_table:
                    in_table = False
                    processed_lines.append("")  # End of table air
                processed_lines.append(line)

        text = "\n".join(processed_lines)

        # 3. Structural Headers & Spacing
        # Convert ### to Bold and ensure vertical spacing
        text = re.sub(r"^###\s+(.*)$", r"\n\n<b>\1</b>\n", text, flags=re.MULTILINE)

        # Ensure structural emojis have proper spacing
        structural_markers = [r"🔹", r"⚠️", r"✅", r"📅", r"🔔", r"🏃", r"🔋", r"💪", r"🧘‍♂️", r"🎯"]
        for marker in structural_markers:
            # Only inject if preceded by a character that isn't a newline or space
            text = re.sub(rf"([^\n])\s*({marker})", r"\1\n\n\2", text)
            text = re.sub(rf"({marker})([^\s])", r"\1 \2", text)

        # 4. HTML Escaping
        text = html.escape(text, quote=False)

        # 5. Restoration of Formatting Entities (using HTML tags)
        # Bold: *text* or **text**
        text = re.sub(r"\*(\*?)(?!\s)(.+?)(?<!\s)\1\*", r"<b>\2</b>", text, flags=re.DOTALL)
        # Italic: _text_
        text = re.sub(r"_(?!\s)(.+?)(?<!\s)_", r"<i>\1</i>", text, flags=re.DOTALL)

        # 6. Final Cleanup
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()


@app.get("/")
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/users/mapping")
async def get_mapping():
    """Returns the current {telegram_id: platform_id} mapping."""
    return get_user_mapping()


class RegisterPayload(BaseModel):
    telegram_id: str
    username: str


@app.post("/api/users/register")
async def register(payload: RegisterPayload):
    """Registers a new user."""
    from db import CANONICAL_ALIASES, CANONICAL_USERS, get_platform_id

    # Check canonical mappings first
    if payload.telegram_id in CANONICAL_USERS:
        platform_id = CANONICAL_USERS[payload.telegram_id]
        register_user(payload.telegram_id, platform_id)
        return {"status": "success", "platform_user_id": platform_id}

    cleaned_username = payload.username.lower()
    if cleaned_username in CANONICAL_ALIASES:
        platform_id = CANONICAL_ALIASES[cleaned_username]
        register_user(payload.telegram_id, platform_id)
        return {"status": "success", "platform_user_id": platform_id}

    # If user already registered, return existing
    existing_pid = get_platform_id(payload.telegram_id)
    if existing_pid:
        return {"status": "success", "platform_user_id": existing_pid}

    # Simple username cleaning
    platform_id = re.sub(r"\W+", "_", payload.username.lower()).strip("_")

    # Ensure uniqueness (naive approach for now)
    base_id = platform_id
    counter = 1
    while True:
        # Check if this platform_id already exists for a DIFFERENT telegram_id
        existing_tid = get_telegram_id(platform_id)
        if existing_tid and existing_tid != payload.telegram_id:
            platform_id = f"{base_id}_{counter}"
            counter += 1
        else:
            break

    success = register_user(payload.telegram_id, platform_id)
    if success:
        return {"status": "success", "platform_user_id": platform_id}

    existing_pid = get_platform_id(payload.telegram_id)
    if existing_pid:
        return {"status": "success", "platform_user_id": existing_pid}
    return {"status": "error", "message": "Failed to register user"}


@app.post("/api/notify")
async def notify(payload: NotificationPayload):
    """
    Sends a proactive notification to a user via Telegram.
    """
    logger.info(f"Notification request for user: {payload.user_id} from agent: {payload.agent_id}")

    chat_id = get_telegram_id(payload.user_id)
    if not chat_id:
        logger.error(f"User {payload.user_id} not found in database")
        return {"status": "error", "message": f"User {payload.user_id} not found"}

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not configured")
        return {"status": "error", "message": "Telegram token not configured"}

    telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # Use the unified formatter
    clean_message = MessageProcessor.decode(payload.message)

    # Escape agent_id separately for the header
    safe_agent_id = html.escape(payload.agent_id, quote=False)
    header = f"🔔 <b>Notification from {safe_agent_id}</b>"
    formatted_message = f"{header}\n\n{clean_message}"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                telegram_url,
                json={
                    "chat_id": chat_id,
                    "text": formatted_message,
                    "parse_mode": "HTML",
                },
            )
            if response.status_code == 200:
                logger.info(f"Notification sent successfully to {payload.user_id}")
                return {"status": "success"}
            logger.error(f"Failed to send Telegram message: {response.text}")
            return {"status": "error", "message": response.text}
        except Exception as e:
            logger.exception(f"Exception while sending notification: {e}")
            return {"status": "error", "message": str(e)}


@app.post("/stream")
async def chat_stream(
    _request: Request,
    x_user_id: str = Header(..., alias="X-User-ID"),
    text: str | None = Form(None),
    thread_id: str = Form(...),
    file: UploadFile | None = File(None),
):
    # Handle Voice if file is provided
    if file:
        logger.info(f"Processing voice note for user {x_user_id}")
        content = await file.read()

        # Upload to Google Generative AI
        temp_file = Path(f"/tmp/{file.filename}")
        with temp_file.open("wb") as f:
            f.write(content)

        try:
            uploaded_file = genai.upload_file(path=str(temp_file), mime_type=file.content_type)
            # Use gemini-1.5-flash for transcription as it supports audio input
            transcription_model = genai.GenerativeModel("gemini-1.5-flash")
            response = transcription_model.generate_content(
                [
                    "Transcribe this voice note and explain the user's intent. "
                    "Output ONLY the transcribed text if it's a simple message, "
                    "or a clear command if it's an action.",
                    uploaded_file,
                ]
            )
            text = response.text
            logger.info(f"Transcribed text: {text}")
        finally:
            if temp_file.exists():
                temp_file.unlink()

    # Initialize state
    initial_messages = [HumanMessage(content=text)]

    async def event_generator():
        config = {"configurable": {"thread_id": thread_id}}

        # In a real LangGraph app, we use stream()
        # For simplicity and to match the SSE expected by Gateway:
        state = {
            "messages": initial_messages,
            "user_id": x_user_id,
            "thread_id": thread_id,
            "loop_count": 0,
        }

        # Run with checkpointer
        async for event in graph.astream(state, config=config, stream_mode="values"):
            if "messages" in event:
                last_msg = event["messages"][-1]
                if isinstance(last_msg, AIMessage) and last_msg.content:
                    # This is a bit naive for token-by-token streaming,
                    # but good for Phase 2/3 structure.
                    # To get real token streaming, we'd need to hook into the LLM
                    pass

        # Get final result
        final_state = await graph.aget_state(config)
        final_msg_obj = final_state.values["messages"][-1]

        # Ensure final_message is a string
        if isinstance(final_msg_obj.content, list):
            parts = []
            for c in final_msg_obj.content:
                if isinstance(c, dict):
                    if "text" in c:
                        parts.append(c["text"])
                else:
                    parts.append(str(c))
            final_message = "".join(parts).strip()
        else:
            final_message = str(final_msg_obj.content)

        # Simulate token streaming of the final message preserving structure
        # We split by characters or small chunks to simulate streaming without losing newlines
        chunk_size = 100
        for i in range(0, len(final_message), chunk_size):
            yield f"data: {json.dumps({'text': final_message[i : i + chunk_size]})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
