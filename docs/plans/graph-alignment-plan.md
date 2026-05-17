# Orchestrator Graph Alignment Plan

## Background & Motivation
The current `telegram-agent-orchestrator` utilizes a simplified LangGraph implementation consisting of a basic Supervisor <-> Tools loop and a Pydantic `BaseModel` for state. A recent review of the `biometric-ai-platform` (the Expert Agent) revealed a more sophisticated graph architecture. To ensure consistency, improve robustness, and pave the way for a scalable "Swarm Architecture", the Orchestrator's graph must be aligned with these patterns while fundamentally supporting an N-agent ecosystem.

## Scope & Impact
The modifications will focus on the `orchestrator-api/app/main.py` file, specifically overhauling the LangGraph setup.
- **Affected Components:** `AgentState`, `supervisor_node`, intent routing, tool execution logic, and the overall graph compilation.
- **Impact:** This is a structural refactor designed for scale. The core functionality remains, but internal routing will support dynamic delegation to multiple expert agents, enforce non-blocking A2A communication, and utilize robust state management.

## Proposed Solution
We will refactor the Orchestrator's graph to implement a scalable "Swarm Architecture" routing pattern:

1.  **State Structure Update (`TypedDict` with Reducers)**:
    -   Convert `AgentState` from a Pydantic `BaseModel` to a `TypedDict`.
    -   Explicitly use LangGraph's native reducers for appending data, specifically `messages: Annotated[list, add_messages]`.
    -   Add new fields: `intent` (str), `loop_count` (int), and `usage_stats` (dict).

2.  **Scalable Intent Routing (`node_router`)**:
    -   Introduce a router node at the start of the graph.
    -   The `IntentClassifier` will NOT be hardcoded to just biometrics. It will be designed to evaluate intent against an expanding registry of expert agents (e.g., `biometric_expert`), plus `general_chat` and `unknown` (fallback).

3.  **Mandatory Asynchronous Execution (A2A)**:
    -   All tool executions or nodes that involve network calls/API requests to expert agents MUST be strictly asynchronous (`async def`) to prevent blocking the main Orchestrator flow. (e.g., ensuring `call_biometric_expert` is and remains fully async).

4.  **Loop Prevention**:
    -   Update the conditional edge logic to increment and evaluate a `loop_count`.
    -   If `loop_count` exceeds a threshold, force the graph to exit (`END`) to prevent infinite tool-calling loops.

5.  **Graph Topology Update (Swarm Architecture)**:
    -   Instead of a single generic supervisor, the topology will reflect a true router:
    -   `START` -> `router` -> Conditional Edge based on `intent`.
    -   If `intent` matches an expert (e.g., `biometric_expert`), route to the specific expert tool/node.
    -   If `intent` is `general_chat` or `unknown`, route to a general conversational node.
    -   Expert nodes/tools will feed back to a synthesizer or directly to `END`.

## Phased Implementation Plan

### Phase 1: State and Scalable Models Update
- Modify `AgentState` in `main.py` to inherit from `TypedDict`, strictly using `Annotated[list, add_messages]` for messages.
- Add `intent`, `loop_count`, and `usage_stats` to the state definition.
- Create a scalable `IntentClassifier` model that supports a dynamic registry of intents (`biometric_expert`, `general_chat`, `unknown`).

### Phase 2: Node Implementation & Async Enforcement
- Create the `node_router` function to classify intent using the LLM.
- Ensure the existing `call_biometric_expert` tool and any future A2A tools are strictly `async def`.
- Implement or update the general conversational node for `general_chat`/`unknown` intents.
- Implement loop tracking and usage tracking logic within the state updates.

### Phase 3: Graph Reassembly (Swarm Topology)
- Rebuild the `StateGraph` using the new routing topology.
- Add conditional edges from the `router` node to direct traffic to the appropriate expert node/tool or the general node based on the classified `intent`.
- Ensure the loop prevention logic is integrated into the expert interaction cycles.

## Verification & Testing
1.  **Scalable Routing**: Send a biometric query, a general greeting, and an off-topic query, verifying the router directs them to `biometric_expert`, `general_chat`, and `unknown` respectively.
2.  **Async Enforcement**: Verify that A2A tool calls use `await` and do not block the event loop.
3.  **Loop Prevention**: Temporarily simulate a failing tool or circular logic and verify the graph gracefully halts without crashing.
4.  **State Reduction**: Verify that message history is correctly appended using the `add_messages` reducer across multiple turns.

## Migration & Rollback
-   **Migration**: The changes are localized to the Orchestrator API. A restart of the FastAPI service is required. Existing checkpointer memory might be invalidated due to the state schema change.
-   **Rollback**: Revert the changes in `main.py` to the previous commit if critical failures occur in routing or state handling.
