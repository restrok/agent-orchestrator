import asyncio
import contextlib
import html
import json
import logging
import os
import re
import shlex
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import google.generativeai as genai
import httpx
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from db import (
    complete_background_worker,
    create_approval_plan,
    get_approval_plan,
    get_telegram_id,
    get_user_mapping,
    init_db,
    list_active_scheduled_tasks,
    register_background_worker,
    register_scheduled_task,
    register_user,
    update_approval_plan_status,
    update_background_worker_progress,
    update_scheduled_task_status,
)
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import InjectedState, ToolNode
from llm_factory import get_chat_model
from models import AgentState, IntentClassifier
from pydantic import BaseModel

load_dotenv()

# Active Status Queues for Streaming Progress
ACTIVE_STATUS_QUEUES: dict[str, asyncio.Queue] = {}


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

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))

file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(JsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S%z"))

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
ALLOWED_WORKER_USERS = [u.strip().lower() for u in os.getenv("ALLOWED_WORKER_USERS", "fsirio").split(",") if u.strip()]

# Initialize Database
init_db()

genai.configure(api_key=GOOGLE_API_KEY)
model_name = os.getenv("LLM_MODEL", "gemini-1.5-flash")

# --- Scheduler Setup ---
DB_PATH = Path(__file__).parent / "data" / "orchestrator.db"
jobstores = {
    "default": SQLAlchemyJobStore(url=f"sqlite:///{DB_PATH.resolve()}")
}
scheduler = AsyncIOScheduler(jobstores=jobstores, timezone="America/Argentina/Buenos_Aires")

app = FastAPI(title="Telegram Agent Orchestrator")


async def heartbeat_loop():
    while True:
        logging.info("💓 Heartbeat: Orchestrator API is active and listening")
        await asyncio.sleep(600)


@app.on_event("startup")
async def startup_event():
    scheduler.start()
    logger.info("⏰ APScheduler started with SQLite persistent jobstore.")
    asyncio.create_task(heartbeat_loop())


@app.on_event("shutdown")
async def shutdown_event():
    scheduler.shutdown()
    logger.info("⏰ APScheduler shutdown cleanly.")


# --- Notification Helper ---
async def send_telegram_notification(target_user: str, message: str, inline_keyboard: list[list[dict]] | None = None) -> bool:
    """Envía notificación directa por Telegram a un usuario."""
    chat_id = get_telegram_id(target_user)
    if not chat_id:
        # Check if target_user is already a numeric telegram chat_id
        if str(target_user).isdigit():
            chat_id = str(target_user)
        else:
            logger.error(f"Cannot send notification: User {target_user} has no telegram_id mapped.")
            return False

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not configured")
        return False

    telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }
    if inline_keyboard:
        payload["reply_markup"] = {"inline_keyboard": inline_keyboard}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(telegram_url, json=payload)
            if resp.status_code == 200:
                logger.info(f"Notification sent successfully to {target_user} ({chat_id})")
                return True
            logger.error(f"Failed to send Telegram notification: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Error sending Telegram notification: {e}")
        return False


# --- Exocortex MCP Client Helper ---
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


# --- Tools ---

@tool
async def call_biometric_expert(
    query: str,
    user_id: Annotated[str, InjectedState("user_id")],
    thread_id: Annotated[str, InjectedState("thread_id")],
):
    """Calls the Biometric Expert Agent to get health, Garmin, or profile data."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                BIOMETRIC_API_URL,
                json={"messages": [{"role": "user", "content": query}], "user": user_id},
                headers={"X-User-ID": user_id},
                timeout=300.0,
            )
            if response.status_code == 200:
                data = response.json()
                try:
                    return data["choices"][0]["message"]["content"]
                except (KeyError, IndexError):
                    return data.get("response", str(data))
            else:
                return f"Error from Biometric Expert: {response.status_code} - {response.text}"
    except httpx.TimeoutException:
        return "The Biometric Expert is taking too long to respond. Please wait a moment."
    except Exception as e:
        return f"An error occurred while reaching the Biometric Expert: {str(e)}"


# --- Exocortex Brain Tools ---

@tool
async def search_brain(query: str, space_id: str = "work", limit: int = 5) -> str:
    """Busca conocimiento previo, decisiones históricas, notas de arquitectura o workflows en el Exocortex Brain."""
    return await _invoke_mcp_tool("brain_search", {"query": query, "space_id": space_id, "limit": limit})


@tool
async def remember_in_brain(content: str, title: str, space_id: str = "work") -> str:
    """Almacena conocimiento duradero, notas importantes o decisiones en el Vault del Exocortex Brain."""
    return await _invoke_mcp_tool("brain_remember", {"content": content, "title": title, "space_id": space_id})


@tool
async def get_brain_health() -> str:
    """Consulta el estado de salud de los componentes de Exocortex (Vault, Gateway y Neo4j)."""
    return await _invoke_mcp_tool("brain_health", {})


@tool
async def get_workflow(workflow_id: str) -> str:
    """Obtiene los detalles y referencias de un workflow específico del Exocortex Brain por su ID."""
    return await _invoke_mcp_tool("brain_get_workflow", {"workflow_id": workflow_id})


@tool
async def register_intent_in_brain(
    title: str,
    goal_description: str,
    target_event_timestamp: str,
    decision_horizon_hours: int = 24,
    eval_tool_target: str = "weather_check",
    eval_params: dict | None = None,
    space_id: str = "personal",
) -> str:
    """Registra una intención condicional o alerta preventiva en el Vault de Exocortex Brain para ejecución futura sin consumo de tokens."""
    args = {
        "title": title,
        "goal_description": goal_description,
        "target_event_timestamp": target_event_timestamp,
        "decision_horizon_hours": decision_horizon_hours,
        "eval_tool_target": eval_tool_target,
        "eval_params": eval_params or {},
        "space_id": space_id,
    }
    return await _invoke_mcp_tool("brain_register_intent", args)


@tool
async def get_intent_from_brain(intent_id: str) -> str:
    """Recupera los detalles de una intención registrada en el Exocortex Brain mediante su ID."""
    return await _invoke_mcp_tool("brain_get_intent", {"intent_id": intent_id})


@tool
async def update_intent_in_brain(intent_id: str, status: str, context_data: dict | None = None) -> str:
    """Actualiza el estado de una intención en el Brain ('active', 'notified', 'dismissed', 'expired')."""
    return await _invoke_mcp_tool("brain_update_intent_status", {
        "intent_id": intent_id,
        "status": status,
        "context_data": context_data or {},
    })


# --- Weather Tool (Open-Meteo) ---

@tool
async def get_weather_forecast(location: str = "Tigre, Buenos Aires", days: int = 3) -> str:
    """Obtiene el pronóstico del clima determinista sin costo ni API key usando Open-Meteo. Ideal para evaluar asados, actividades al aire libre y deportes."""
    # Coordenadas Tigre / Rincón de Milberg por defecto
    lat, lon = -34.426, -58.579
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,windspeed_10m_max&timezone=America/Argentina/Buenos_Aires&forecast_days={min(days, 7)}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return f"Error consultando el clima: HTTP {resp.status_code}"
            data = resp.json()
            daily = data.get("daily", {})
            dates = daily.get("time", [])
            t_max = daily.get("temperature_2m_max", [])
            t_min = daily.get("temperature_2m_min", [])
            precip = daily.get("precipitation_sum", [])
            prob = daily.get("precipitation_probability_max", [])
            wind = daily.get("windspeed_10m_max", [])

            lines = [f"🌤️ **Pronóstico Meteorológico para {location}**:"]
            for i, d in enumerate(dates):
                cond = "☀️ Despejado/Bueno"
                if prob[i] > 60 or precip[i] > 5:
                    cond = "🌧️ Lluvia probable"
                elif prob[i] > 30 or precip[i] > 1:
                    cond = "⛅ Inestable / Posibles lloviznas"

                lines.append(
                    f"📅 **{d}**: {cond} | Temp: {t_min[i]}°C a {t_max[i]}°C | Lluvia: {precip[i]}mm ({prob[i]}% prob) | Viento: {wind[i]} km/h"
                )
            return "\n\n".join(lines)
    except Exception as e:
        return f"Error obteniendo pronóstico del clima: {e}"


# --- Scheduled Tasks Engine Handler ---

async def execute_scheduled_job(job_id: str, task_type: str, user_id: str, chat_id: str, title: str, payload: dict, intent_id: str | None = None):
    """Ejecutor disparado por APScheduler cuando llega el trigger_time."""
    logger.info(f"⏰ Ejecutando tarea programada [{job_id}] para {user_id}: {title} ({task_type})")

    if task_type == "weather_check":
        forecast = await get_weather_forecast.ainvoke({"location": payload.get("location", "Tigre, Buenos Aires"), "days": 3})
        msg = f"🔔 <b>Alerta Programada: {html.escape(title)}</b>\n\n{forecast}\n\n<i>Evaluación automática completada con cero tokens en espera.</i>"
        inline_kb = [
            [
                {"text": "🥩 Mantener Plan", "callback_data": f"keep_plan:{job_id}"},
                {"text": "❌ Cancelar/Cambiar", "callback_data": f"change_plan:{job_id}"},
            ]
        ]
        await send_telegram_notification(user_id, msg, inline_keyboard=inline_kb)
        if intent_id:
            await update_intent_in_brain.ainvoke({"intent_id": intent_id, "status": "notified", "context_data": {"forecast_snippet": forecast[:200]}})

    elif task_type == "reminder":
        rem_msg = payload.get("message", title)
        msg = f"⏰ <b>Recordatorio: {html.escape(title)}</b>\n\n{html.escape(rem_msg)}"
        await send_telegram_notification(user_id, msg)
        if intent_id:
            await update_intent_in_brain.ainvoke({"intent_id": intent_id, "status": "notified"})

    elif task_type == "worker_execute":
        task_prompt = payload.get("task", title)
        target_project = payload.get("target_project", "")
        msg = f"🚀 <b>Ejecutando Tarea Programada de Worker</b>:\n\n<b>Tarea:</b> {html.escape(task_prompt)}\n<b>Proyecto:</b> {html.escape(target_project or 'default')}"
        await send_telegram_notification(user_id, msg)
        # Launch async worker
        asyncio.create_task(run_background_worker_task(user_id=user_id, chat_id=chat_id, task=task_prompt, target_project=target_project))

    update_scheduled_task_status(job_id, "triggered")


# --- Scheduling Tools ---

@tool
async def schedule_task(
    title: str,
    trigger_time_iso: str,
    task_type: str,
    payload: dict,
    user_id: Annotated[str, InjectedState("user_id")],
    thread_id: Annotated[str, InjectedState("thread_id")],
    reminder_lead_hours: int = 24,
) -> str:
    """Programa una tarea o recordatorio condicional para el futuro (formato ISO 8601, ej: '2026-09-13T10:00:00-03:00').
    Tipos soportados: 'weather_check', 'reminder', 'worker_execute'."""
    try:
        trigger_dt = datetime.fromisoformat(trigger_time_iso)
    except Exception as e:
        return f"Error en formato de fecha/hora: {e}. Debe ser ISO 8601 con zona horaria (ej: 2026-09-13T10:00:00-03:00)."

    job_id = f"job_{uuid.uuid4().hex[:10]}"
    task_id = f"task_{uuid.uuid4().hex[:10]}"

    intent_id = None
    # If it's a weather check or preventive alert, register intent in Exocortex Brain
    if task_type in ("weather_check", "reminder"):
        try:
            intent_res = await register_intent_in_brain.ainvoke({
                "title": title,
                "goal_description": json.dumps(payload),
                "target_event_timestamp": trigger_time_iso,
                "decision_horizon_hours": reminder_lead_hours,
                "eval_tool_target": task_type,
                "eval_params": payload,
                "space_id": "personal",
            })
            # Parse intent ID if available
            with contextlib.suppress(Exception):
                intent_data = json.loads(intent_res)
                intent_id = intent_data.get("id") or intent_data.get("intent_id")
        except Exception as e:
            logger.warning(f"Could not register intent in Brain: {e}")

    # Register in SQLite
    register_scheduled_task(task_id, job_id, intent_id, user_id, str(thread_id), title, task_type, payload, trigger_time_iso)

    # Schedule in APScheduler
    scheduler.add_job(
        execute_scheduled_job,
        "date",
        run_date=trigger_dt,
        id=job_id,
        args=[job_id, task_type, user_id, str(thread_id), title, payload, intent_id],
        replace_existing=True,
    )

    logger.info(f"✅ Tarea [{title}] programada con éxito para {trigger_time_iso} (Job ID: {job_id})")
    return f"✅ Tarea '{title}' agendada exitosamente para el {trigger_time_iso}.\n• Job ID: `{job_id}`\n• Tipo: {task_type}\n• Espera activa: 0 tokens (reactivación automática)."


@tool
async def list_scheduled_tasks() -> str:
    """Lista las tareas y recordatorios actualmente agendados y pendientes de ejecución."""
    tasks = list_active_scheduled_tasks()
    if not tasks:
        return "📅 No hay tareas programadas pendientes."
    lines = ["📅 **Tareas Programadas Activas**:"]
    for t in tasks:
        lines.append(f"• **{t['title']}** (ID: `{t['job_id']}`)\n  ⏰ Fecha: {t['trigger_time']}\n  📌 Tipo: {t['task_type']}")
    return "\n\n".join(lines)


@tool
async def cancel_scheduled_task(job_id: str) -> str:
    """Cancela una tarea programada mediante su job_id."""
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    update_scheduled_task_status(job_id, "cancelled")
    return f"❌ Tarea `{job_id}` cancelada correctamente."


# --- Multi-Worker Execution Logic ---

async def run_ssh_worker_command(task: str, target_project: str, q: asyncio.Queue | None = None) -> tuple[int, str, str]:
    """Helper base para ejecutar el Antigravity Worker mediante SSH en el host."""
    clean_project = target_project.strip().lstrip("~").lstrip("/")
    if clean_project and clean_project not in (".", "home", "root"):
        workspace_dir = f"/home/{HOST_SSH_USER}/{clean_project}"
    else:
        workspace_dir = f"/home/{HOST_SSH_USER}"

    key_path = SSH_KEY_PATH
    if not Path(key_path).exists() and Path("/root/.ssh/id_rsa").exists():
        key_path = "/root/.ssh/id_rsa"

    cmd_flags = (
        "--dangerously-skip-permissions "
        "--model gemini-3.8-flash-medium "
        "--effort medium "
        "--print-timeout 15m "
        "--output-format stream-json"
    )
    remote_cmd = (
        f'export PATH="/home/{HOST_SSH_USER}/.local/bin:/usr/local/bin:$PATH" && '
        f"if [ -d {shlex.quote(workspace_dir)} ]; then cd {shlex.quote(workspace_dir)}; "
        f"else cd /home/{HOST_SSH_USER}; fi && agy {cmd_flags} -p {shlex.quote(task)}"
    )
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

    proc = await asyncio.create_subprocess_exec(
        *ssh_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout_lines = []
    final_result = None

    async def read_stdout_stream():
        nonlocal final_result
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue
            stdout_lines.append(line_str)
            try:
                data = json.loads(line_str)
                ev = data.get("event")
                if ev == "step_update":
                    su = data.get("step_update", {})
                    idx = su.get("step_index", 0)
                    stype = su.get("step_type", "")
                    state = su.get("state", "")
                    tool_name = su.get("tool_name") or ""
                    delta = su.get("text_delta", "")
                    if tool_name:
                        msg = f"⚙️ Worker (Paso {idx}): ejecutando {tool_name}..."
                    elif delta:
                        snippet = delta.strip()[:60]
                        msg = f"🧠 Worker (Paso {idx}): {snippet}..."
                    elif stype == "agent_response":
                        msg = f"🧠 Worker (Paso {idx}): analizando código..."
                    else:
                        msg = f"🔍 Worker (Paso {idx}): {stype or state}..."
                    if q:
                        await q.put(msg)
                elif ev == "result":
                    res = data.get("result", {})
                    if "response" in res:
                        final_result = res["response"]
            except json.JSONDecodeError:
                pass

    try:
        await asyncio.wait_for(
            asyncio.gather(read_stdout_stream(), proc.wait()),
            timeout=900.0,
        )
    except asyncio.TimeoutError:
        with contextlib.suppress(Exception):
            proc.kill()
        return -1, "", "Timeout: El Worker superó los 15 minutos de ejecución."

    stderr = await proc.stderr.read()
    stderr_str = stderr.decode("utf-8", errors="replace").strip()
    stdout_str = final_result if final_result else "\n".join(stdout_lines)

    return proc.returncode, stdout_str, stderr_str


async def run_background_worker_task(user_id: str, chat_id: str, task: str, target_project: str):
    """Tarea en segundo plano para Workers asíncronos con reportes periódicos a Telegram."""
    worker_id = f"worker_{uuid.uuid4().hex[:8]}"
    register_background_worker(worker_id, user_id, chat_id, task, target_project)

    status_q = asyncio.Queue()
    last_reported_time = asyncio.get_event_loop().time()
    last_status = "Iniciando worker..."

    async def periodic_reporter():
        nonlocal last_reported_time, last_status
        while True:
            await asyncio.sleep(45)  # Reporte de progreso cada 45 segundos
            now = asyncio.get_event_loop().time()
            if last_status and (now - last_reported_time >= 40):
                msg = f"⏳ <b>Avance Worker (`{worker_id}`)</b>:\n<i>{html.escape(last_status)}</i>"
                await send_telegram_notification(user_id, msg)
                update_background_worker_progress(worker_id, last_status)
                last_reported_time = now

    reporter_task = asyncio.create_task(periodic_reporter())

    async def drain_queue():
        nonlocal last_status
        while True:
            item = await status_q.get()
            if item is None:
                break
            last_status = item

    drain_task = asyncio.create_task(drain_queue())

    returncode, stdout_str, stderr_str = await run_ssh_worker_command(task, target_project, q=status_q)
    await status_q.put(None)
    reporter_task.cancel()
    with contextlib.suppress(Exception):
        await drain_task

    if returncode == 0:
        complete_background_worker(worker_id, "completed", stdout_str)
        snippet = stdout_str[:2500] + ("\n\n...[Respuesta recortada por tamaño]" if len(stdout_str) > 2500 else "")
        final_msg = f"✅ <b>Worker Completado con Éxito (`{worker_id}`)</b>:\n\n<b>Tarea:</b> {html.escape(task)}\n\n<b>Resultado:</b>\n{snippet}"
    else:
        complete_background_worker(worker_id, "failed", stderr_str or stdout_str)
        final_msg = f"❌ <b>Worker Falló (`{worker_id}`)</b>:\n\n<b>Error:</b>\n{html.escape(stderr_str or stdout_str[:500])}"

    await send_telegram_notification(user_id, final_msg)


@tool
async def call_antigravity_worker(
    task: str,
    target_project: str,
    mode: str = "sync",
    user_id: Annotated[str, InjectedState("user_id")] = "fsirio",
    thread_id: Annotated[str, InjectedState("thread_id")] = "",
) -> str:
    """Invoca al Agente Worker (Antigravity) para ejecutar tareas autónomas en el host homelab.
    - Modo 'sync': Para tareas cortas o consultas rápidas con respuesta inmediata en el chat.
    - Modo 'async': Para tareas largas, refactors o pipelines en segundo plano con reportes periódicos de estado a Telegram.
    - SEGURIDAD: Exclusivo para 'fsirio'. Si un usuario no autorizado (ej: Mercedes) lo solicita, se genera un plan para aprobación humana."""
    user_lower = str(user_id).lower()

    # CONTROL DE ACCESO HITL: Si es Mercedes u otro no autorizado -> Generar Plan de Aprobación
    if user_lower not in ALLOWED_WORKER_USERS:
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"
        plan_title = f"Ejecución en {target_project or 'host'}: {task[:50]}"
        plan_details = f"El usuario '{user_id}' solicitó ejecutar la siguiente tarea en el host:\n\n• Proyecto: {target_project or 'workspace raíz'}\n• Tarea: {task}"

        create_approval_plan(plan_id, user_id, str(thread_id), plan_title, plan_details, task, target_project)

        # Enviar solicitud interactiva a Federico (fsirio)
        approval_kb = [
            [
                {"text": "✅ Aprobar y Ejecutar", "callback_data": f"approve_plan:{plan_id}"},
                {"text": "❌ Rechazar", "callback_data": f"reject_plan:{plan_id}"},
            ]
        ]
        fsirio_msg = (
            f"🛡️ <b>Solicitud de Ejecución Pendiente de Aprobación</b>:\n\n"
            f"👤 <b>Solicitante:</b> {user_id}\n"
            f"📁 <b>Proyecto:</b> <code>{target_project or 'default'}</code>\n"
            f"📝 <b>Tarea:</b> {html.escape(task)}\n\n"
            f"<i>¿Deseas autorizar la ejecución del Worker Antigravity?</i>"
        )
        await send_telegram_notification("fsirio", fsirio_msg, inline_keyboard=approval_kb)

        return (
            f"📝 <b>Plan de Trabajo Generado (ID: `{plan_id}`)</b>\n\n"
            f"Como esta solicitud requiere ejecución de comandos en el servidor homelab, "
            f"he generado un plan técnico y le acabo de enviar una solicitud de aprobación interactiva a Federico.\n\n"
            f"Te avisaré en cuanto sea aprobado y ejecutado."
        )

    # Usuario Autorizado (fsirio)
    if mode == "async":
        asyncio.create_task(run_background_worker_task(user_id=user_id, chat_id=str(thread_id), task=task, target_project=target_project))
        return (
            f"🚀 **Antigravity Worker lanzado en segundo plano (modo async)**.\n\n"
            f"• **Tarea:** {task}\n"
            f"• **Proyecto:** `{target_project or 'workspace'}`\n"
            f"• **Seguimiento:** Te iré enviando reportes de progreso a este chat de Telegram cada 45 segundos hasta su finalización."
        )

    # Modo Síncrono (sync)
    q = ACTIVE_STATUS_QUEUES.get(str(thread_id))
    if q:
        await q.put(f"🚀 Worker Antigravity iniciado en {target_project or 'workspace'}...")

    code, stdout_str, stderr_str = await run_ssh_worker_command(task, target_project, q=q)

    if code == 0:
        if q:
            await q.put("✅ Worker completado con éxito.")
        return f"✅ **Tarea de Antigravity completada con éxito**:\n\n{stdout_str}"
    return f"⚠️ **Antigravity Worker finalizó con error** (código {code}):\n{stdout_str}\n{stderr_str}"


tools = [
    call_biometric_expert,
    search_brain,
    remember_in_brain,
    get_brain_health,
    get_workflow,
    register_intent_in_brain,
    get_intent_from_brain,
    update_intent_in_brain,
    get_weather_forecast,
    schedule_task,
    list_scheduled_tasks,
    cancel_scheduled_task,
    call_antigravity_worker,
]
tool_node = ToolNode(tools)


# --- LangGraph Setup ---

async def node_router(state: AgentState):
    """Router determinista y clasificador de intenciones."""
    logger.info("--- NODE: Router ---")
    last_message = ""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            last_message = msg.content
            break

    if not last_message:
        return {"intent": "unknown"}

    msg_lower = str(last_message).strip().lower()
    sync_commands = ["/garmin_sync", "/garmin_sync_full", "/garmin_login", "sync garmin"]
    if any(msg_lower.startswith(cmd) for cmd in sync_commands):
        return {"intent": "biometric_expert", "loop_count": 0}

    llm = get_chat_model(model_name=model_name, temperature=0)
    provider = os.getenv("LLM_PROVIDER", "google").lower()
    if provider in ["ollama", "openai", "lmstudio"]:
        structured_llm = llm.with_structured_output(IntentClassifier, method="function_calling")
    else:
        structured_llm = llm.with_structured_output(IntentClassifier)

    try:
        classification = await structured_llm.ainvoke(state["messages"])
        return {"intent": classification.intent, "loop_count": 0}
    except Exception as e:
        logger.error(f"Intent classification failed: {e}. Falling back to supervisor.")
        return {"intent": "general_chat", "loop_count": 0}


async def biometric_expert_node(state: AgentState):
    query = state["messages"][-1].content
    user_id = state["user_id"]
    thread_id = state["thread_id"]
    result = await call_biometric_expert.ainvoke({"query": query, "user_id": user_id, "thread_id": thread_id})
    return {"messages": [AIMessage(content=result)]}


def route_to_agent(state: AgentState):
    intent = state.get("intent", "unknown")
    if intent == "biometric_expert":
        return "biometric_expert"
    return "supervisor"


async def supervisor_node(state: AgentState):
    current_loops = state.get("loop_count", 0)
    user_id = state["user_id"]

    system_prompt_content = (
        "You are an AI Orchestrator and Supervisor for a powerful Homelab and Biometric platform.\n\n"
        f"USER CONTEXT:\n"
        f"You are currently assisting user: '{user_id}'.\n\n"
        "CAPABILITIES & TOOLS:\n"
        "1. Biometric Expert (`call_biometric_expert`): Consult physiological data, Garmin activities, sleep, HRV, running.\n"
        "2. Exocortex Brain:\n"
        "   - `search_brain`: Search the user's second brain for past decisions, project notes, architecture docs, or general knowledge (space_id='work' or 'personal').\n"
        "   - `remember_in_brain`: Save valuable notes, decisions, or durable context into vault.\n"
        "   - `register_intent_in_brain`, `get_intent_from_brain`, `update_intent_in_brain`: Manage conditional intents and proactive reminders.\n"
        "3. Weather & Proactive Scheduler:\n"
        "   - `get_weather_forecast`: Check weather forecast for Tigre, Buenos Aires or other locations.\n"
        "   - `schedule_task`: Schedule delayed tasks, weather alerts, or reminders (zero tokens while waiting).\n"
        "   - `list_scheduled_tasks`, `cancel_scheduled_task`: Manage scheduled jobs.\n"
        "4. Antigravity Worker (`call_antigravity_worker`):\n"
        "   - Executes engineering tasks, refactors, script executions, or terminal commands on host.\n"
        "   - Supports mode='sync' (fast tasks) and mode='async' (long-running background jobs with Telegram progress updates).\n"
        "   - SECURITY / HITL POLICY: Only 'fsirio' can directly execute workers. If any other user (like 'mercedes') asks for engineering or host execution, call `call_antigravity_worker` and it will automatically generate a Plan for Fsirio's approval.\n\n"
        "LANGUAGE POLICY:\n"
        "Always respond in the same language the user is speaking (Spanish if Spanish, English if English).\n\n"
        "FORMATTING RULES for Telegram:\n"
        "1. DO NOT use Markdown tables. Use bulleted lists instead.\n"
        "2. VERTICAL SPACING: Use double newlines between list items.\n"
        "3. Use bold and emojis to keep the tone friendly and structured."
    )
    system_prompt = SystemMessage(content=system_prompt_content)

    llm = get_chat_model(model_name=model_name, temperature=0.1, max_tokens=4096)
    llm_with_tools = llm.bind_tools(tools)
    messages_to_send = [system_prompt] + state["messages"]

    response = await llm_with_tools.ainvoke(messages_to_send)
    return {"messages": [response], "loop_count": current_loops + 1}


def should_continue(state: AgentState):
    current_loops = state.get("loop_count", 0)
    last_message = state["messages"][-1]
    if current_loops > 7:
        return END
    if last_message.tool_calls:
        return "tools"
    return END


memory = MemorySaver()

workflow = StateGraph(AgentState)
workflow.add_node("router", node_router)
workflow.add_node("biometric_expert", biometric_expert_node)
workflow.add_node("supervisor", supervisor_node)
workflow.add_node("tools", tool_node)

workflow.add_edge(START, "router")
workflow.add_conditional_edges("router", route_to_agent, {"biometric_expert": "biometric_expert", "supervisor": "supervisor"})
workflow.add_edge("biometric_expert", END)
workflow.add_conditional_edges("supervisor", should_continue, {"tools": "tools", END: END})
workflow.add_edge("tools", "supervisor")

graph = workflow.compile(checkpointer=memory)


# --- Message Processor for HTML ---
class MessageProcessor:
    @staticmethod
    def decode(text: str) -> str:
        if not text:
            return ""
        text = text.replace("\\n", "\n")
        lines = text.split("\n")
        processed_lines = []
        in_table = False

        emojis = {
            "heart": "❤️", "hr": "❤️", "bpm": "❤️", "frecuencia": "❤️",
            "distance": "📍", "distancia": "📍", "pace": "⏱️", "ritmo": "⏱️",
            "power": "⚡", "potencia": "⚡", "time": "🕒", "tiempo": "🕒",
            "duración": "🕒", "calories": "🔥", "calorías": "🔥", "vo2": "📈",
            "sleep": "😴", "sueño": "😴", "hrv": "⚖️",
        }

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                parts = [p.strip() for p in stripped.split("|") if p.strip()]
                if not parts or all(re.match(r"[:\-]+", p) for p in parts):
                    continue
                if not in_table:
                    in_table = True
                    processed_lines.append("")
                    continue
                if len(parts) >= 2:
                    metric_name = parts[0]
                    value = " | ".join(parts[1:])
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
                    processed_lines.append("")
                processed_lines.append(line)

        text = "\n".join(processed_lines)
        text = re.sub(r"^###\s+(.*)$", r"\n\n<b>\1</b>\n", text, flags=re.MULTILINE)
        structural_markers = [r"🔹", r"⚠️", r"✅", r"📅", r"🔔", r"🏃", r"🔋", r"💪", r"🧘‍♂️", r"🎯"]
        for marker in structural_markers:
            text = re.sub(rf"([^\n])\s*({marker})", r"\1\n\n\2", text)
            text = re.sub(rf"({marker})([^\s])", r"\1 \2", text)

        text = html.escape(text, quote=False)
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"<i>\1</i>", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


# --- Endpoints ---

@app.get("/")
@app.get("/health")
async def health():
    return {"status": "ok", "scheduler": scheduler.running}


@app.get("/api/users/mapping")
async def get_mapping():
    return get_user_mapping()


class RegisterPayload(BaseModel):
    telegram_id: str
    username: str


@app.post("/api/users/register")
async def register(payload: RegisterPayload):
    from db import CANONICAL_ALIASES, CANONICAL_USERS, get_platform_id
    if payload.telegram_id in CANONICAL_USERS:
        platform_id = CANONICAL_USERS[payload.telegram_id]
        register_user(payload.telegram_id, platform_id)
        return {"status": "success", "platform_user_id": platform_id}

    cleaned_username = payload.username.lower()
    if cleaned_username in CANONICAL_ALIASES:
        platform_id = CANONICAL_ALIASES[cleaned_username]
        register_user(payload.telegram_id, platform_id)
        return {"status": "success", "platform_user_id": platform_id}

    existing_pid = get_platform_id(payload.telegram_id)
    if existing_pid:
        return {"status": "success", "platform_user_id": existing_pid}

    platform_id = re.sub(r"\W+", "_", payload.username.lower()).strip("_")
    success = register_user(payload.telegram_id, platform_id)
    if success:
        return {"status": "success", "platform_user_id": platform_id}
    return {"status": "error", "message": "Failed to register user"}


class NotificationPayload(BaseModel):
    user_id: str
    agent_id: str
    message: str


@app.post("/api/notify")
async def notify(payload: NotificationPayload):
    success = await send_telegram_notification(payload.user_id, f"🔔 <b>Notificación ({html.escape(payload.agent_id)})</b>:\n\n{MessageProcessor.decode(payload.message)}")
    return {"status": "success" if success else "error"}


class ApprovalActionPayload(BaseModel):
    plan_id: str
    action: str  # approve, reject
    reviewer_user_id: str


@app.post("/api/plans/action")
async def handle_plan_action(payload: ApprovalActionPayload):
    """Endpoint llamado por el gateway cuando Federico presiona Aprobar o Rechazar en Telegram."""
    plan = get_approval_plan(payload.plan_id)
    if not plan:
        return JSONResponse({"status": "error", "message": "Plan no encontrado"}, status_code=404)

    if plan["status"] != "pending":
        return JSONResponse({"status": "error", "message": f"El plan ya fue procesado ({plan['status']})"}, status_code=400)

    if payload.action == "reject":
        update_approval_plan_status(payload.plan_id, "rejected")
        await send_telegram_notification(plan["requester_id"], f"❌ Tu plan <b>'{html.escape(plan['title'])}'</b> fue rechazado por Federico.")
        return {"status": "success", "action": "rejected"}

    if payload.action == "approve":
        update_approval_plan_status(payload.plan_id, "approved")
        await send_telegram_notification(plan["requester_id"], f"✅ Tu plan <b>'{html.escape(plan['title'])}'</b> fue aprobado por Federico. Iniciando ejecución...")

        # Disparar ejecución en background
        asyncio.create_task(run_background_worker_task(
            user_id=plan["requester_id"],
            chat_id=plan["requester_telegram_id"],
            task=plan["task"],
            target_project=plan["target_project"],
        ))
        return {"status": "success", "action": "approved_and_launched"}

    return JSONResponse({"status": "error", "message": "Acción inválida"}, status_code=400)


@app.post("/stream")
async def chat_stream(
    _request: Request,
    x_user_id: str = Header(..., alias="X-User-ID"),
    text: str | None = Form(None),
    thread_id: str = Form(...),
    file: UploadFile | None = File(None),
):
    media_context = ""

    # Soporte Multimodal: Audio, Imágenes, Archivos/Documentos
    if file:
        filename = file.filename or "file"
        mime_type = file.content_type or "application/octet-stream"
        logger.info(f"Processing attached file '{filename}' ({mime_type}) for user {x_user_id}")
        content = await file.read()

        temp_file = Path(f"/tmp/{filename}")
        with temp_file.open("wb") as f:
            f.write(content)

        try:
            # 1. Audios / Notas de voz
            if "audio" in mime_type or filename.endswith((".ogg", ".mp3", ".m4a", ".wav")):
                uploaded_file = genai.upload_file(path=str(temp_file), mime_type=mime_type)
                transcription_model = genai.GenerativeModel("gemini-1.5-flash")
                response = transcription_model.generate_content([
                    "Transcribe this voice note and explain user intent. Output ONLY the transcribed message or action.",
                    uploaded_file,
                ])
                transcribed = response.text.strip()
                logger.info(f"Transcribed audio: {transcribed}")
                text = f"{text or ''}\n{transcribed}".strip()

            # 2. Imágenes / Fotos
            elif "image" in mime_type or filename.endswith((".jpg", ".jpeg", ".png", ".webp")):
                uploaded_file = genai.upload_file(path=str(temp_file), mime_type=mime_type)
                vision_model = genai.GenerativeModel("gemini-1.5-flash")
                response = vision_model.generate_content([
                    "Analyze this image thoroughly. Describe what is shown, extract any visible text, errors, diagrams, or details relevant to homelab, programming, workouts, or general tasks.",
                    uploaded_file,
                ])
                media_context = f"\n\n[CONTEXTO VISUAL DE LA IMAGEN ADJUNTA '{filename}']:\n{response.text}\n"

            # 3. Documentos y Código (PDF, TXT, PY, LOG, CSV, JSON, MD)
            elif filename.endswith((".txt", ".py", ".log", ".json", ".csv", ".md", ".sh", ".yml", ".yaml")):
                text_content = content.decode("utf-8", errors="replace")
                snippet = text_content[:15000]
                media_context = f"\n\n[CONTENIDO DEL ARCHIVO ADJUNTO '{filename}']:\n```\n{snippet}\n```\n"

            elif filename.endswith(".pdf"):
                uploaded_file = genai.upload_file(path=str(temp_file), mime_type="application/pdf")
                doc_model = genai.GenerativeModel("gemini-1.5-flash")
                response = doc_model.generate_content([
                    "Extract and summarize the essential text and structure from this PDF document.",
                    uploaded_file,
                ])
                media_context = f"\n\n[RESUMEN DEL DOCUMENTO PDF '{filename}']:\n{response.text}\n"

        except Exception as e:
            logger.error(f"Error analyzing multimodal file {filename}: {e}")
            media_context = f"\n\n[Aviso: El archivo adjunto '{filename}' no pudo ser completamente analizado: {e}]\n"
        finally:
            if temp_file.exists():
                temp_file.unlink()

    full_user_prompt = f"{text or ''}{media_context}".strip()
    if not full_user_prompt:
        full_user_prompt = "¿En qué puedo ayudarte?"

    initial_messages = [HumanMessage(content=full_user_prompt)]

    async def event_generator():
        config = {"configurable": {"thread_id": thread_id}}
        status_queue = asyncio.Queue()
        ACTIVE_STATUS_QUEUES[str(thread_id)] = status_queue

        state = {
            "messages": initial_messages,
            "user_id": x_user_id,
            "thread_id": thread_id,
            "loop_count": 0,
        }

        async def run_graph():
            try:
                await graph.ainvoke(state, config=config)
            except Exception as e:
                logger.error(f"Error executing LangGraph: {e}")
            finally:
                await status_queue.put(None)

        graph_task = asyncio.create_task(run_graph())

        while True:
            try:
                status_item = await asyncio.wait_for(status_queue.get(), timeout=2.5)
                if status_item is None:
                    break
                yield f"data: {json.dumps({'status': status_item})}\n\n"
            except asyncio.TimeoutError:
                if graph_task.done():
                    break
                yield f"data: {json.dumps({'status': '⚙️ Analizando solicitud...'})}\n\n"

        await graph_task
        ACTIVE_STATUS_QUEUES.pop(str(thread_id), None)

        final_state = await graph.aget_state(config)
        final_msg_obj = final_state.values["messages"][-1]

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

        chunk_size = 100
        for i in range(0, len(final_message), chunk_size):
            yield f"data: {json.dumps({'text': final_message[i : i + chunk_size]})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
