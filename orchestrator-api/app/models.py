from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class AgentState(TypedDict):
    """
    Represents the state of the Orchestrator graph.
    Uses LangGraph's native add_messages reducer for the message history.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    user_id: str
    thread_id: str
    intent: str
    loop_count: int
    usage_stats: dict[str, Any]


class IntentClassifier(BaseModel):
    """
    Classifies the user's intent to route to the appropriate expert agent or general handler.
    Designed to be scalable by adding more expert agents to the Literal.
    """

    intent: Literal["biometric_expert", "exocortex_brain", "antigravity_worker", "general_chat", "unknown"] = Field(
        ...,
        description=(
            "The classified intent of the user. "
            "'biometric_expert' for health, training, Garmin, or physiological queries. "
            "Includes specific commands like /garmin_login and /garmin_sync. "
            "'exocortex_brain' for queries or actions involving the user's second brain, memory, notes, "
            "prior knowledge, decisions, documentation, or workflows stored in Exocortex. "
            "'antigravity_worker' for engineering tasks, coding, refactoring, modifying files, executing terminal "
            "commands or automation on the host Lenovo ThinkCentre via Antigravity (agy). "
            "'general_chat' for greetings, small talk, or general non-specialized questions. "
            "'unknown' for ambiguous queries or intents not covered by existing experts."
        ),
    )
    rationale: str = Field(..., description="A brief explanation of why this intent was selected.")
