import os
import json
import logging
from typing import List, Optional, Annotated
from fastapi import FastAPI, Request, Header, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

import google.generativeai as genai
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, InjectedState
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
import httpx

load_dotenv()

# Configuration
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
BIOMETRIC_API_URL = os.getenv("BIOMETRIC_API_URL", "http://localhost:8080/chat")

genai.configure(api_key=GOOGLE_API_KEY)
model_name = "gemma-4-31b-it"

# Logging
LOG_FILE = "orchestrator.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


app = FastAPI(title="Telegram Agent Orchestrator")

# --- Tools ---

@tool
async def call_biometric_expert(
    query: str, 
    user_id: Annotated[str, InjectedState("user_id")], 
    thread_id: Annotated[str, InjectedState("thread_id")]
):
    """Calls the Biometric Expert Agent to get health, Garmin, or profile data."""
    logger.info(f"--- TOOL CALL: Biometric Expert ---")
    logger.info(f"User: {user_id}")
    logger.info(f"Thread: {thread_id}")
    logger.info(f"Query sent to Expert: {query}")
    logger.info(f"------------------------------------")
    
    logger.info(f"Routing request for user {user_id} (thread: {thread_id}) to Biometric Expert")
    async with httpx.AsyncClient() as client:
        # Adjusted for /v1/chat/completions "Black Box" interface
        response = await client.post(
            BIOMETRIC_API_URL,
            json={
                "messages": [{"role": "user", "content": query}],
                "user": user_id
            },
            headers={"X-User-ID": user_id},
            timeout=120.0
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

from langgraph.checkpoint.memory import MemorySaver

# --- LangGraph Setup ---

class AgentState(BaseModel):
    messages: Annotated[List[BaseMessage], add_messages]
    user_id: str
    thread_id: str

def supervisor_node(state: AgentState):
    logger.info(f"Supervisor node called with {len(state.messages)} messages")
    for i, msg in enumerate(state.messages):
        logger.info(f"Message {i}: {type(msg).__name__} - {str(msg.content)[:100]}...")
    
    llm = ChatGoogleGenerativeAI(model=model_name, google_api_key=GOOGLE_API_KEY)
    
    # Add system context with formatting instructions
    system_prompt_content = (
        f"You are a versatile AI Orchestrator. Your role is to coordinate between the user and specialized Expert Agents. "
        f"Analyze the user's intent, call the appropriate tools, and synthesize results into a clear, friendly response.\n\n"
        "IMPORTANT FORMATTING RULES for Telegram (Chat Interface):\n"
        "1. DO NOT use Markdown tables. Use bulleted lists instead.\n"
        "2. VERTICAL SPACING: Use double newlines (two '\\n') between list items and between different sections to ensure good readability on mobile screens.\n"
        "3. Every item in a schedule or list MUST be on its own line with a blank line between items.\n"
        "4. Use **bold** for emphasis and emojis to keep the tone friendly.\n"
        "5. If you provide a schedule, use a clear 'Day - Activity' list format with ample spacing."
    )
    system_prompt = SystemMessage(content=system_prompt_content)
    
    messages_to_send = [system_prompt] + state.messages
    
    # Simple supervisor logic
    llm_with_tools = llm.bind_tools(tools)
    try:
        response = llm_with_tools.invoke(messages_to_send)
        return {"messages": [response]}
    except Exception as e:
        logger.error(f"Error invoking LLM: {e}")
        raise e

def should_continue(state: AgentState):
    last_message = state.messages[-1]
    if last_message.tool_calls:
        return "tools"
    return END

memory = MemorySaver()

workflow = StateGraph(AgentState)
workflow.add_node("supervisor", supervisor_node)
workflow.add_node("tools", tool_node)

workflow.set_entry_point("supervisor")
workflow.add_conditional_edges("supervisor", should_continue, {"tools": "tools", END: END})
workflow.add_edge("tools", "supervisor")

graph = workflow.compile(checkpointer=memory)

# --- Endpoints ---

@app.post("/stream")
async def chat_stream(
    request: Request,
    x_user_id: str = Header(..., alias="X-User-ID"),
    text: Optional[str] = Form(None),
    thread_id: str = Form(...),
    file: Optional[UploadFile] = File(None)
):
    # Handle Voice if file is provided
    if file:
        logger.info(f"Processing voice note for user {x_user_id}")
        content = await file.read()
        
        # Upload to Google Generative AI
        temp_file_path = f"/tmp/{file.filename}"
        with open(temp_file_path, "wb") as f:
            f.write(content)
        
        try:
            uploaded_file = genai.upload_file(path=temp_file_path, mime_type=file.content_type)
            model = genai.GenerativeModel(model_name)
            response = model.generate_content([
                "Transcribe this voice note and explain the user's intent. "
                "Output ONLY the transcribed text if it's a simple message, "
                "or a clear command if it's an action.",
                uploaded_file
            ])
            text = response.text
            logger.info(f"Transcribed text: {text}")
        finally:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)

    # Initialize state
    initial_messages = [HumanMessage(content=text)]
    
    async def event_generator():
        config = {"configurable": {"thread_id": thread_id}}
        
        # In a real LangGraph app, we use stream()
        # For simplicity and to match the SSE expected by Gateway:
        state = {"messages": initial_messages, "user_id": x_user_id, "thread_id": thread_id}
        
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
                    # Specifically ignore blocks like {'type': 'thinking', ...}
                else:
                    parts.append(str(c))
            final_message = " ".join(parts).strip()
        else:
            final_message = str(final_msg_obj.content)
        
        # Simulate token streaming of the final message
        for word in final_message.split():
            yield f"data: {json.dumps({'text': word + ' '})}\n\n"
        
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
