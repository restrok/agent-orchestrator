import logging
from typing import Any

from langchain_core.messages import AIMessage, RemoveMessage, ToolMessage

logger = logging.getLogger(__name__)

HIGH_WATER_MARK = 24
LOW_WATER_MARK = 10


async def prune_state_if_needed(graph: Any, config: dict[str, Any]) -> int:
    """Prunes the LangGraph conversation state if total messages exceed HIGH_WATER_MARK (24),

    reducing the message history to LOW_WATER_MARK (10).

    Selection safety:
    Prevents orphaned ToolMessages by ensuring we never cut in the middle of
    an AIMessage(tool_calls) -> ToolMessage pair. If the cut point lands on a
    ToolMessage or splits a tool call pair, the entire pair is removed together.

    Returns the number of removed messages.
    """
    if not graph:
        return 0

    try:
        state = await graph.aget_state(config)
        if not state or not getattr(state, "values", None) or "messages" not in state.values:
            return 0

        messages = state.values["messages"]
        total_msgs = len(messages)
        if total_msgs <= HIGH_WATER_MARK:
            return 0

        # We want to keep LOW_WATER_MARK messages from the end
        target_remove_count = total_msgs - LOW_WATER_MARK
        cut_idx = target_remove_count

        # Safe boundary selection:
        # 1. If cut_idx lands on a ToolMessage, advance past all ToolMessages so the
        #    entire pair (AIMessage with tool_calls + ToolMessage(s)) is removed together.
        while cut_idx < total_msgs and isinstance(messages[cut_idx], ToolMessage):
            cut_idx += 1

        # 2. If the message immediately preceding the cut is an AIMessage with tool_calls,
        #    its corresponding ToolMessage(s) start at cut_idx. Advance past them to avoid
        #    leaving a dangling tool response.
        if 0 < cut_idx < total_msgs:
            prev_msg = messages[cut_idx - 1]
            if isinstance(prev_msg, AIMessage) and getattr(prev_msg, "tool_calls", None):
                while cut_idx < total_msgs and isinstance(messages[cut_idx], ToolMessage):
                    cut_idx += 1

        removals = [RemoveMessage(id=m.id) for m in messages[:cut_idx] if getattr(m, "id", None)]

        if removals:
            await graph.aupdate_state(config, {"messages": removals})
            remaining = total_msgs - len(removals)
            logger.info(
                f"✂️ State pruned for thread '{config.get('configurable', {}).get('thread_id')}': "
                f"removed {len(removals)} messages ({total_msgs} -> {remaining})."
            )
            return len(removals)

        return 0
    except Exception as e:
        logger.error(f"Error during state pruning: {e}", exc_info=True)
        return 0
