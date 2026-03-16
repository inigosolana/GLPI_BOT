"""
agent.py — Punto de entrada del agente de voz LiveKit para helpdesk GLPI.

Responsabilidad: conectarse a una Room de LiveKit creada por el SIP trunk,
configurar el pipeline de voz (STT → LLM → TTS) con las tools de GLPI y
gestionar el ciclo de vida de la llamada telefónica. El LLM decide autónomamente
cuándo llamar a cada tool; no hay máquina de estados manual.

Arranque:
    python agent.py start
"""

import logging

from livekit import rtc
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli
from livekit.agents.voice_assistant import VoicePipelineAgent
from livekit.plugins import cartesia, deepgram, openai, silero

import config  # noqa: F401 — importar para validar variables de entorno al inicio
from glpi_client import GLPIClient
from glpi_tools import GLPITools

logger = logging.getLogger(__name__)

# ── Prompt del sistema ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "Eres un asistente de helpdesk de IT que atiende llamadas telefónicas "
    "de comerciales de la empresa. Tu objetivo es ayudarles a crear tickets "
    "de soporte en GLPI o consultar el estado de tickets existentes.\n\n"
    "Reglas de comportamiento:\n"
    "- Habla siempre en español, de forma clara y concisa\n"
    "- Las respuestas deben ser cortas porque el usuario está conduciendo\n"
    "- Cuando el usuario quiera crear un ticket, usa la tool crear_ticket\n"
    "- Cuando quiera consultar un ticket, usa la tool consultar_ticket\n"
    "- Si el usuario no especifica la urgencia, asume urgencia normal (3)\n"
    "- Confirma siempre el número de ticket creado al usuario\n"
    "- Al terminar di 'Hasta luego' y llama a la tool finalizar_llamada"
)

# ── Entrypoint del agente ──────────────────────────────────────────────────────

async def entrypoint(ctx: JobContext) -> None:
    """
    Función principal llamada por el WorkerOptions de LiveKit para cada Room
    nueva que se crea al recibir una llamada SIP entrante.

    Flujo:
    1. Conectarse a la Room en modo solo audio
    2. Esperar a que el participante SIP aparezca y obtener su número
    3. Buscar el usuario en GLPI por teléfono (opcional, best-effort)
    4. Configurar y arrancar el VoicePipelineAgent
    5. El agente saluda al comercial y espera instrucciones
    """
    logger.info("Nuevo job recibido: room='%s'", ctx.room.name)

    # ── 1. Conectar a la Room (solo audio, sin vídeo) ──────────────────────────
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # ── 2. Obtener el número del llamante desde los metadatos SIP ──────────────
    participant: rtc.RemoteParticipant = await ctx.wait_for_participant()
    caller_number: str = participant.attributes.get("sip.callerId", "desconocido")
    logger.info("Llamada recibida de: %s (identity='%s')", caller_number, participant.identity)

    # ── 3. Inicializar cliente GLPI y buscar usuario por teléfono ──────────────
    glpi = GLPIClient()
    requester_id = None
    if caller_number != "desconocido":
        requester_id = await glpi.find_user_by_phone(caller_number)
        if requester_id:
            logger.info("Comercial identificado en GLPI: user_id=%d", requester_id)
        else:
            logger.info("Comercial no encontrado en GLPI para %s; ticket sin asignar.", caller_number)

    # ── 4. Crear instancia de tools con referencia a la Room ──────────────────
    tools = GLPITools(glpi_client=glpi, room=ctx.room)

    # ── 5. Construir el VoicePipelineAgent ────────────────────────────────────
    agent = VoicePipelineAgent(
        # VAD: Silero detecta cuando el usuario empieza/para de hablar
        vad=silero.VAD.load(),

        # STT: Deepgram Nova-2 en español
        stt=deepgram.STT(
            model="nova-2",
            language="es",
        ),

        # LLM: Llama 3.3 70B vía Groq (compatible con la API de OpenAI)
        llm=openai.LLM.with_groq(
            model="llama-3.3-70b-versatile",
            temperature=0,
        ),

        # TTS: Cartesia Sonic Multilingual — voz y parámetros óptimos para telefonía (8 kHz)
        tts=cartesia.TTS(
            model="sonic-multilingual",
            voice="a0e99841-438c-4a64-b679-ae501e7d6091",
            encoding="pcm_s16le",
            sample_rate=8000,
        ),

        # System prompt con las instrucciones de comportamiento
        chat_ctx=_build_initial_chat_ctx(),

        # Tools disponibles para el LLM
        fnc_ctx=tools,
    )

    # ── 6. Arrancar el agente en la Room ──────────────────────────────────────
    agent.start(ctx.room, participant)

    # Saludo inicial: el agente toma la iniciativa en cuanto se conecta
    saludo = (
        f"Hola, soy el asistente de helpdesk. "
        f"¿En qué puedo ayudarte hoy?"
    )
    await agent.say(saludo, allow_interruptions=True)

    logger.info("VoicePipelineAgent activo y esperando interacción del comercial.")


def _build_initial_chat_ctx() -> "openai.llm.ChatContext":  # type: ignore[name-defined]
    """
    Construye el ChatContext inicial con el mensaje de sistema.
    Separado en función propia para mantener entrypoint limpio.
    """
    from livekit.plugins.openai import llm as openai_llm  # importación local
    ctx = openai_llm.ChatContext()
    ctx.append(role="system", text=SYSTEM_PROMPT)
    return ctx


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(entrypoint_fnc=entrypoint)
    )
