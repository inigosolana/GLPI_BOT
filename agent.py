"""
agent.py — Punto de entrada del agente de voz LiveKit para helpdesk GLPI.

Responsabilidad: conectarse a una Room de LiveKit creada por el SIP trunk,
configurar el pipeline de voz (STT → LLM → TTS) con las tools de GLPI y
gestionar el ciclo de vida de la llamada telefónica. El LLM decide autónomamente
cuándo llamar a cada tool; no hay máquina de estados manual.

Arranque:
    python agent.py start
"""

import asyncio
import logging

from livekit import rtc
from livekit.agents import llm, AutoSubscribe, JobContext, WorkerOptions, cli
from livekit.agents.llm import ChatContext, ChatMessage
from livekit.agents.voice_assistant import VoicePipelineAgent
from livekit.plugins import cartesia, deepgram, openai, silero

import config  # noqa: F401 — importar para validar variables de entorno al inicio
from glpi_client import GLPIClient
from glpi_tools import GLPITools
from transcription import CallTranscription

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

# ── Construcción del ChatContext inicial ───────────────────────────────────────

def _build_initial_chat_ctx() -> ChatContext:
    """
    Construye el ChatContext inicial con el mensaje de sistema.
    Separado en función propia para mantener entrypoint limpio.
    """
    ctx = ChatContext()
    ctx.messages.append(
        ChatMessage(role="system", content=SYSTEM_PROMPT)
    )
    return ctx


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
    6. Al finalizar, guardar la transcripción completa en fichero
    """
    logger.info("Nuevo job recibido: room='%s'", ctx.room.name)

    # ── 1. Conectar a la Room (solo audio, sin vídeo) ──────────────────────────
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # ── 2. Obtener el número del llamante desde los metadatos SIP ──────────────
    participant: rtc.RemoteParticipant = await ctx.wait_for_participant()
    caller_number: str = participant.attributes.get("sip.callerId", "desconocido")
    logger.info("Llamada recibida de: %s (identity='%s')", caller_number, participant.identity)

    # ── 3. Instanciar transcripción de llamada ─────────────────────────────────
    transcription = CallTranscription(
        caller_number=caller_number,
        room_name=ctx.room.name,
    )

    # ── 4. Inicializar cliente GLPI y buscar usuario por teléfono ──────────────
    glpi = GLPIClient()
    requester_id = None
    if caller_number != "desconocido":
        requester_id = await glpi.find_user_by_phone(caller_number)
        if requester_id:
            logger.info("Comercial identificado en GLPI: user_id=%d", requester_id)
        else:
            logger.info("Comercial no encontrado en GLPI para %s; ticket sin asignar.", caller_number)

    # ── 5. Crear instancia de tools con referencia a la Room y transcripción ───
    tools = GLPITools(glpi_client=glpi, room=ctx.room, transcription=transcription)

    # ── 6. Construir el VoicePipelineAgent ────────────────────────────────────
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

    # ── 7. Listeners para transcripción en tiempo real ─────────────────────────

    @agent.on("user_speech_committed")
    def on_user_speech(msg: ChatMessage):
        if msg.content:
            logger.info("TRANSCRIPCIÓN USUARIO: %s", msg.content)
            transcription.add_entry("USUARIO", str(msg.content))

    @agent.on("agent_speech_committed")
    def on_agent_speech(msg: ChatMessage):
        if msg.content:
            logger.info("TRANSCRIPCIÓN AGENTE: %s", msg.content)
            transcription.add_entry("AGENTE", str(msg.content))

    # ── 8. Arrancar el agente y mantener vivo hasta que cuelguen ─────────────
    agent.start(ctx.room, participant)

    try:
        # Saludo inicial
        await agent.say(
            "Hola, soy el asistente de helpdesk. ¿En qué puedo ayudarte hoy?",
            allow_interruptions=True,
        )
        logger.info("VoicePipelineAgent activo y esperando interacción del comercial.")
        # Mantener el agente vivo hasta que la llamada termine (máximo 1 hora)
        await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        # Guardar transcripción completa al colgar
        ruta = await transcription.save_to_file()
        logger.info("Transcripción guardada en: %s", ruta)


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="glpi_inigo",
        )
    )
