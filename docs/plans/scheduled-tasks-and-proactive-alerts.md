# Sistema de Tareas Programadas y Recordatorios Inteligentes en Telegram Orchestrator

## 🎯 Objetivo General
Permitir al usuario agendar tareas y recordatorios desde Telegram en lenguaje natural, sin consumo de tokens ni timeouts durante los períodos de espera, con soporte para:
1. **Alertas climáticas y condicionales preventivas** (ej: chequear el clima de Tigre con antelación para el asado del domingo).
2. **Tareas diferidas de ejecución** (ej: "el lunes tomemos X tarea"), ya sea como recordatorio interactivo o ejecución autónoma directa del Worker.
3. **Persistencia y control interactivo** (botones inline en Telegram para cancelar, iniciar o posponer, más gestión por lenguaje natural).

---

## 🏗️ Arquitectura Técnica

- **Motor Scheduler:** `APScheduler` (AsyncIOScheduler) con `SQLAlchemyJobStore` apuntando a `sqlite:////app/data/orchestrator.db`.
- **Persistencia:** Jobs persistidos en SQLite en volumen `orchestrator_data`. Sobreviven a reinicios del contenedor sin duplicaciones.
- **Cero costo en espera:** Ningún polling ni bucle activo de tokens. La tarea se reactiva en el segundo exacto del trigger.

---

## 🧩 Tipos de Tareas y Herramientas

### Tipos de Ejecución:
- `weather_check`: Consulta Open-Meteo para Tigre/GBA (lat: -34.426, lon: -58.579), evalúa lluvia/viento y envía alerta preventiva.
- `reminder_notify`: Notificación proactiva con botones inline interactivos (`[🚀 Iniciar Tarea]`, `[⏰ Posponer 1h]`, `[❌ Descartar]`).
- `worker_execute`: Disparo autónomo directo del Antigravity Worker en el repositorio indicado con streaming de avances a Telegram.
- `agent_prompt`: Despertar del Orchestrator para análisis o consultas complejas usando sus herramientas.

### Tools para el Agente (LangGraph):
- `schedule_task(title, trigger_time, task_type, payload, reminder_lead_hours)`
- `list_scheduled_tasks()`
- `cancel_scheduled_task(job_id)`
- `get_weather_forecast(location="Tigre, Buenos Aires", days=7)` (Open-Meteo, sin costo ni API key)
- `search_web(query)` (DuckDuckGo / Search complementario)

---

## 📲 Interacción y Botones en Telegram

- **Notificaciones Proactivas:** Envío a través de Telegram Bot API con `InlineKeyboardMarkup`.
- **Callback Queries (`telegram-gateway`):**
  - `cancel:<job_id>`: Cancela la tarea, muestra toast y actualiza el mensaje en el chat a `❌ Tarea cancelada`.
  - `run_now:<job_id>`: Ejecuta la tarea inmediatamente.
  - `snooze:<job_id>:<hours>`: Pospone la tarea N horas.
- **Lenguaje Natural:** Soporte completo para consultas ("¿qué tareas tengo pendientes?", "cancelá la tarea del clima").
