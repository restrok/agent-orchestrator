"""Memory Janitor module for deterministic background transcript ingestion.

Transfers pending conversation and notification batches from `chat_messages`
(ingested_to_brain = 0) to Exocortex Brain via MCP tool `brain_ingest_session`.
Operates 100% deterministically without LLM calls on the orchestrator.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from db import get_pending_for_ingest, mark_ingested, mark_skipped
except ImportError:
    from app.db import get_pending_for_ingest, mark_ingested, mark_skipped

logger = logging.getLogger(__name__)


def _get_default_mcp_invoker():
    try:
        from main import _invoke_mcp_tool

        return _invoke_mcp_tool
    except ImportError:
        from app.main import _invoke_mcp_tool

        return _invoke_mcp_tool


def parse_iso_utc(timestamp_str: str) -> datetime:
    """Parses an ISO 8601 string into a timezone-aware UTC datetime.

    Handles ISO formats like '2026-10-26T03:48:37.592475+00:00' and '...Z'.
    Ensures safe comparisons against aware datetimes without naive tz issues.
    """
    normalized = timestamp_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def get_pending_batches(
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Groups pending messages by `thread_id` maintaining ascending id order.

    If `messages` is None, queries `get_pending_for_ingest` from the database.
    """
    if messages is None:
        messages = get_pending_for_ingest(limit=1000)

    batches: dict[str, list[dict[str, Any]]] = {}
    for msg in messages:
        t_id = str(msg["thread_id"])
        batches.setdefault(t_id, []).append(msg)

    # Ensure strictly sorted by id
    for t_id in batches:
        batches[t_id].sort(key=lambda m: m["id"])

    return batches


def should_process(
    batch: list[dict[str, Any]],
    now: datetime | None = None,
    idle_minutes: int = 30,
    volume_threshold: int = 20,
    force: bool = False,
) -> bool:
    """Evaluates whether a thread's pending batch satisfies flush triggers.

    Conditions:
    - force=True: Always processes non-empty batches (used by manual flush endpoint).
    - VOLUME: Pending count >= volume_threshold.
    - IDLE: Time elapsed since the LAST pending message's created_at > idle_minutes.

    CRITICAL — TIMEZONE AWARENESS:
    Compares timezone-aware UTC datetimes to prevent naive/aware offset bugs.
    """
    if not batch:
        return False
    if force:
        return True
    if len(batch) >= volume_threshold:
        return True

    now_utc = (
        now.astimezone(timezone.utc)
        if (now and now.tzinfo)
        else (now.replace(tzinfo=timezone.utc) if now else datetime.now(timezone.utc))
    )

    last_msg = batch[-1]
    last_created = parse_iso_utc(str(last_msg["created_at"]))

    elapsed = now_utc - last_created
    return elapsed > timedelta(minutes=idle_minutes)


def is_notification_only(batch: list[dict[str, Any]]) -> bool:
    """Anti-spam filter: checks if batch lacks any user message.

    If a batch contains ZERO rows with role='user' (e.g. only proactive notifications
    or agent responses without human interaction), it is considered notification-only.
    """
    return not any(msg.get("role") == "user" for msg in batch)


def derive_space_id(batch: list[dict[str, Any]]) -> str:
    """Deterministically derives the destination space_id for the batch.

    Design & Criteria (Hybrid Spaces alignment with commit c8dad18):
    1. User context:
       - If user is 'mercedes', space is 'personal' (mercedes has no 'work' space access).
    2. Intent context:
       - Messages tagged with 'biometric_expert' (physiological metrics, Garmin sync, sleep)
         pertain to personal biological state -> 'personal'.
       - Messages tagged with 'antigravity_worker' or 'exocortex_brain' -> 'work'.
       - If personal intents outnumber work intents -> 'personal'.
    3. Default:
       - Fallback to 'work', the canonical default space configured in Exocortex.
    """
    if not batch:
        return "work"

    user_id = str(batch[0].get("user_id") or "").lower()
    if user_id == "mercedes":
        return "personal"

    intents = [msg.get("intent") for msg in batch if msg.get("intent")]
    if intents:
        personal_count = sum(1 for i in intents if i in ("biometric_expert", "personal"))
        work_count = sum(
            1 for i in intents if i in ("antigravity_worker", "exocortex_brain", "work")
        )
        if personal_count > work_count:
            return "personal"

    return "work"


def build_payload(batch: list[dict[str, Any]]) -> dict[str, Any]:
    """Assembles the deterministic and idempotent payload for `brain_ingest_session`.

    - conversation_id: deterministic `tg_{thread_id}_{min_id}_{max_id}`.
    - transcript_jsonl: format required by Exocortex `_read_antigravity_lines`.
      Exocortex looks for `event.get("type")` ('USER_INPUT' for user,
      'PLANNER_RESPONSE' for assistant/system/notification) to build turns,
      plus 'role', 'content', and 'created_at'.
    - space_id: derived deterministically.
    """
    if not batch:
        raise ValueError("Cannot build payload for empty batch")

    ids = [msg["id"] for msg in batch]
    min_id = min(ids)
    max_id = max(ids)
    thread_id = str(batch[0]["thread_id"])
    conversation_id = f"tg_{thread_id}_{min_id}_{max_id}"
    space_id = derive_space_id(batch)

    lines: list[str] = []
    for msg in batch:
        role = str(msg.get("role") or "user")
        event_type = "USER_INPUT" if role == "user" else "PLANNER_RESPONSE"
        event = {
            "type": event_type,
            "role": role,
            "content": str(msg.get("content") or ""),
            "created_at": str(msg.get("created_at") or ""),
        }
        lines.append(json.dumps(event, ensure_ascii=False))

    transcript_jsonl = "\n".join(lines)

    return {
        "conversation_id": conversation_id,
        "transcript_jsonl": transcript_jsonl,
        "space_id": space_id,
    }


def is_mcp_success(response: Any) -> tuple[bool, str]:
    """Validates whether an MCP tool response indicates success.

    Returns (is_success, error_description).
    """
    if response is None:
        return False, "Empty response from MCP"

    if isinstance(response, dict):
        if response.get("status") == "ok":
            return True, ""
        if "error" in response:
            return False, str(response.get("error"))
        return False, f"Unexpected dict response: {response}"

    if isinstance(response, str):
        trimmed = response.strip()
        # Handle string error messages from _invoke_mcp_tool (e.g. timeouts, HTTP errors)
        if (
            trimmed.startswith("Error")
            or trimmed.startswith("Exocortex MCP")
            or "timed out" in trimmed.lower()
        ):
            # Check if it happens to be valid JSON with status: ok
            try:
                parsed = json.loads(trimmed)
                if isinstance(parsed, dict) and parsed.get("status") == "ok":
                    return True, ""
            except json.JSONDecodeError:
                pass
            return False, trimmed

        try:
            parsed = json.loads(trimmed)
            if isinstance(parsed, dict):
                if parsed.get("status") == "ok":
                    return True, ""
                if "error" in parsed:
                    return False, str(parsed.get("error"))
                return False, f"Response status not ok: {parsed}"
        except json.JSONDecodeError:
            if "error" in trimmed.lower():
                return False, trimmed
            return False, f"Non-JSON string response: {trimmed}"

    return False, f"Unexpected response format: {response}"


async def process_batch(
    thread_id: str,
    batch: list[dict[str, Any]],
    mcp_invoker: Callable[..., Any] | None = None,
    mark_ingested_fn: Callable[[list[int]], int] | None = None,
    mark_skipped_fn: Callable[[list[int]], int] | None = None,
) -> dict[str, Any]:
    """Processes a single thread's batch with atomic state update guarantees.

    1. If notification-only (0 user messages):
       Marks ingested_to_brain = 2 (skipped) without invoking MCP.
    2. If contains user messages:
       Builds payload and invokes `brain_ingest_session`.
       Only marks ingested_to_brain = 1 AFTER successful MCP response.
       If MCP fails, rows remain at 0 for subsequent retries.
    """
    if mark_ingested_fn is None:
        mark_ingested_fn = mark_ingested

    if mark_skipped_fn is None:
        mark_skipped_fn = mark_skipped

    batch_ids = [m["id"] for m in batch]

    # Anti-spam filter
    if is_notification_only(batch):
        logger.info(
            f"🧹 Thread '{thread_id}' batch has 0 user messages ({len(batch_ids)} notif/system). "
            f"Marking as skipped (ingested_to_brain = 2)."
        )
        updated = mark_skipped_fn(batch_ids)
        return {
            "thread_id": thread_id,
            "status": "skipped",
            "message_count": updated,
            "reason": "notification_only",
        }

    # User messages present: invoke Exocortex MCP
    if mcp_invoker is None:
        mcp_invoker = _get_default_mcp_invoker()

    payload = build_payload(batch)
    logger.info(
        f"🧹 Invoking MCP 'brain_ingest_session' for thread '{thread_id}' "
        f"(conversation_id={payload['conversation_id']}, space_id={payload['space_id']}, msgs={len(batch_ids)})"
    )

    try:
        res = await mcp_invoker("brain_ingest_session", payload)
        success, err_msg = is_mcp_success(res)
        if not success:
            logger.error(
                f"❌ MCP brain_ingest_session failed for thread '{thread_id}': {err_msg}. "
                f"Batch rows remain at ingested_to_brain = 0."
            )
            return {
                "thread_id": thread_id,
                "status": "error",
                "message_count": 0,
                "error": err_msg,
            }

        # Successful MCP ingestion -> atomically mark rows
        updated = mark_ingested_fn(batch_ids)
        logger.info(
            f"✅ Ingestion confirmed for thread '{thread_id}'. Marked {updated} rows as ingested (ingested_to_brain = 1)."
        )
        return {
            "thread_id": thread_id,
            "status": "ingested",
            "message_count": updated,
            "conversation_id": payload["conversation_id"],
            "space_id": payload["space_id"],
            "mcp_response": res,
        }

    except Exception as e:
        logger.error(
            f"❌ Exception calling brain_ingest_session for thread '{thread_id}': {e}. "
            f"Batch rows remain at ingested_to_brain = 0.",
            exc_info=True,
        )
        return {
            "thread_id": thread_id,
            "status": "error",
            "message_count": 0,
            "error": str(e),
        }


async def run_janitor_cycle(
    force: bool = False,
    now: datetime | None = None,
    idle_minutes: int = 30,
    volume_threshold: int = 20,
    mcp_invoker: Callable[..., Any] | None = None,
    get_pending_fn: Callable[[], list[dict[str, Any]]] | None = None,
    mark_ingested_fn: Callable[[list[int]], int] | None = None,
    mark_skipped_fn: Callable[[list[int]], int] | None = None,
    get_lock_fn: Callable[[str], asyncio.Lock] | None = None,
) -> dict[str, Any]:
    """Executes a full cycle of the memory janitor across all pending threads.

    - Groups pending rows by thread_id.
    - Evaluates `should_process` (or forces if `force=True`).
    - Uses per-thread asyncio.Lock if provided to prevent race conditions
      with concurrent chat turns on the same thread.
    - Collects cycle statistics and errors without interrupting execution.
    """
    if get_pending_fn is None:
        def _default_get_pending() -> list[dict[str, Any]]:
            return get_pending_for_ingest(limit=1000)

        get_pending_fn = _default_get_pending

    pending_messages = get_pending_fn()
    batches = get_pending_batches(pending_messages)

    summary: dict[str, Any] = {
        "status": "ok",
        "total_pending": len(pending_messages),
        "total_threads_pending": len(batches),
        "processed_threads": 0,
        "ingested_messages": 0,
        "skipped_messages": 0,
        "details": [],
        "errors": [],
    }

    if not batches:
        return summary

    for thread_id, batch in batches.items():
        if not should_process(
            batch,
            now=now,
            idle_minutes=idle_minutes,
            volume_threshold=volume_threshold,
            force=force,
        ):
            continue

        try:
            if get_lock_fn is not None:
                async with get_lock_fn(str(thread_id)):
                    res = await process_batch(
                        thread_id=thread_id,
                        batch=batch,
                        mcp_invoker=mcp_invoker,
                        mark_ingested_fn=mark_ingested_fn,
                        mark_skipped_fn=mark_skipped_fn,
                    )
            else:
                res = await process_batch(
                    thread_id=thread_id,
                    batch=batch,
                    mcp_invoker=mcp_invoker,
                    mark_ingested_fn=mark_ingested_fn,
                    mark_skipped_fn=mark_skipped_fn,
                )

            summary["details"].append(res)
            if res["status"] == "ingested":
                summary["processed_threads"] += 1
                summary["ingested_messages"] += res["message_count"]
            elif res["status"] == "skipped":
                summary["processed_threads"] += 1
                summary["skipped_messages"] += res["message_count"]
            elif res["status"] == "error":
                summary["errors"].append({"thread_id": thread_id, "error": res.get("error")})

        except Exception as e:
            logger.error(f"Unexpected error processing batch for thread '{thread_id}': {e}", exc_info=True)
            summary["errors"].append({"thread_id": thread_id, "error": str(e)})

    if summary["errors"] and summary["processed_threads"] == 0:
        summary["status"] = "partial_or_error"

    return summary
