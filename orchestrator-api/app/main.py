import html
import json
import logging
import os
import re
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
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import InjectedState, ToolNode
from llm_factory import get_chat_model
from models import AgentState, IntentClassifier
from pydantic import BaseModel

load_dotenv()

# Logging
LOG_FILE = "orchestrator.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


# Configuration
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
BIOMETRIC_API_URL = os.getenv("BIOMETRIC_API_URL", "http://localhost:8080/chat")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

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
model_name = "gemini-3.1-flash-lite"


app = FastAPI(title="Telegram Agent Orchestrator")

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
    async with httpx.AsyncClient() as client:
        # Adjusted for /v1/chat/completions "Black Box" interface
        response = await client.post(
            BIOMETRIC_API_URL,
            json={"messages": [{"role": "user", "content": query}], "user": user_id},
            headers={"X-User-ID": user_id},
            timeout=600.0,
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


tools = [call_biometric_expert]
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

    logger.info(f"Classifying intent for: {str(last_message)[:100]}...")

    llm = get_chat_model(model_name=model_name, temperature=0)
    structured_llm = llm.with_structured_output(IntentClassifier)

    try:
        classification = await structured_llm.ainvoke(state["messages"])
        logger.info(f"🔍 Intent Classified: {classification.intent.upper()}")
        logger.info(f"💡 Rationale: {classification.rationale}")
        return {"intent": classification.intent}
    except Exception as e:
        logger.error(f"Intent classification failed: {e}. Falling back to 'unknown'.")
        return {"intent": "unknown"}


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


def supervisor_node(state: AgentState):
    current_loops = state.get("loop_count", 0)
    logger.info(f"--- NODE: Supervisor (Loop: {current_loops}) ---")

    logger.info(f"Supervisor node called with {len(state['messages'])} messages")
    # Log only the last few messages to keep logs clean
    for i, msg in enumerate(state["messages"][-3:]):
        logger.info(f"Message {i}: {type(msg).__name__} - {str(msg.content)[:100]}...")

    # Add system context with formatting instructions
    system_prompt_content = (
        "You are a versatile AI Orchestrator. Your role is to coordinate between the user and specialized Expert "
        "Agents. "
        "Analyze the user's intent, call the appropriate tools, and synthesize results into a clear, friendly "
        "response.\n\n"
        "USER CONTEXT:\n"
        f"You are currently assisting user: '{state['user_id']}'. "
        "All data retrieved via tools will be specific to this user profile.\n\n"
        "SPECIALIZED COMMANDS:\n"
        "- If the user wants to connect Garmin, they should use /garmin_login.\n"
        "- If the user wants to force a data sync, they should use /garmin_sync.\n"
        "You can inform the user about these commands if they seem lost.\n\n"
        "LANGUAGE POLICY:\n"
        "Always respond in the same language the user is speaking. If the user asks in English, respond in English. "
        "If the user asks in Spanish, respond in Spanish. This is mandatory.\n\n"
        "IMPORTANT FORMATTING RULES for Telegram (Chat Interface):\n"
        "1. DO NOT use Markdown tables. Use bulleted lists instead.\n"
        "2. VERTICAL SPACING: Use double newlines (two '\\n') between list items and between different sections "
        "to ensure good readability on mobile screens.\n"
        "3. Every item in a schedule or list MUST be on its own line with a blank line between items.\n"
        "4. Use **bold** for emphasis and emojis to keep the tone friendly.\n"
        "5. If you provide a schedule, use a clear 'Day - Activity' list format with ample spacing."
    )
    system_prompt = SystemMessage(content=system_prompt_content)

    messages_to_send = [system_prompt] + state["messages"]

    try:
        response = llm_with_tools.invoke(messages_to_send)
        return {"messages": [response], "loop_count": current_loops + 1}
    except Exception as e:
        logger.error(f"Error invoking LLM: {e}")
        raise e


def should_continue(state: AgentState):
    current_loops = state.get("loop_count", 0)
    last_message = state["messages"][-1]

    if current_loops > 4:
        logger.warning(f"⚠️ Loop count ({current_loops}) exceeded. Stopping to preserve API quota.")
        return END

    if last_message.tool_calls:
        return "tools"
    return END


llm = ChatGoogleGenerativeAI(model=model_name, google_api_key=GOOGLE_API_KEY)
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
    # If registration failed but it's the same telegram_id, just return the existing mapping
    from db import get_platform_id

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
