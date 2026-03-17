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
from livekit.agents.pipeline import VoicePipelineAgent
from livekit.plugins import cartesia, deepgram, openai, silero

import config  # noqa: F401 — importar para validar variables de entorno al inicio
from glpi_client import GLPIClient
from glpi_tools import GLPITools
from transcription import CallTranscription

logger = logging.getLogger(__name__)

# ── Prompt del sistema ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "Eres el asistente de tickets de la empresa Ausarta, que atiende llamadas telefónicas "
    "de comerciales y empleados. Tu objetivo es ayudarles a crear tickets "
    "de soporte en GLPI o consultar el estado de tickets existentes.\n\n"
    "Reglas de comportamiento:\n"
    "- Habla SIEMPRE en español, con acento y pronunciación en idioma español.\n"
    "- Forma de hablar natural, sin acento robótico o extranjero. Nada de inglés.\n"
    "- Las respuestas deben ser claras y concisas porque el usuario probablemente esté ocupado o conduciendo.\n"
    "- SIEMPRE CON CONFIRMACIÓN PARA IDENTIFICARSE: Al inicio, si no sabes quién es, pídeselo. Cuando te diga su nombre o número, usa OBLIGATORIAMENTE la tool identificar_usuario para validarlo.\n"
    "- IMPORTANTE: Si le pides el nombre y el usuario responde dictando un número de teléfono, usa la tool identificar_usuario enviándole ese número. Pero asegúrate SIEMPRE de que, si es un teléfono, recabes los 9 dígitos completos.\n"
    "- Si el usuario está dictando un número y parece detenerse a medias (ej: solo ha dicho 2 o 3 dígitos), NO intentes buscarlo aún, pídele: 'Por favor, indícame tu número de teléfono completo'.\n"
    "- Solo cuando el usuario haya sido identificado puedes proceder a preguntar: ¿desea crear un nuevo ticket o consultar uno existente?\n"
    "- Cuando el usuario quiera crear un ticket, usa la tool crear_ticket.\n"
    "- Cuando quiera consultar un ticket concreto, usa la tool consultar_ticket.\n"
    "- Cuando quiera ver todos sus tickets abiertos o consultar los suyos propios, usa la tool consultar_mis_tickets.\n"
    "- Si el usuario no especifica la urgencia, asume urgencia normal (3).\n"
    "- Confirma siempre el número del ticket. Al terminar di 'Hasta luego' y llama a la tool finalizar_llamada."
)

# ── Construcción del ChatContext inicial ───────────────────────────────────────

def _build_initial_chat_ctx(caller_number: str, requester_name: str | None = None) -> ChatContext:
    """
    Construye el ChatContext inicial con el mensaje de sistema.
    Inyecta información sobre el llamante para que el LLM pueda usarla.
    """
    ctx = ChatContext()
    
    prompt_dinamico = SYSTEM_PROMPT + "\n\nInformación de esta llamada:\n"
    prompt_dinamico += f"- Número llamante: {caller_number}\n"
    
    if requester_name:
        prompt_dinamico += f"- Nombre identificado en GLPI: {requester_name}\n"
    else:
        prompt_dinamico += "- Nombre identificado en GLPI: Desconocido (no se ha encontrado en el sistema)\n"

    ctx.messages.append(
        ChatMessage(role="system", content=prompt_dinamico)
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

    # Validar configuración antes de intentar conectar
    config.validate_config()

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

    # ── 4. Inicializar cliente GLPI y bloque de contexto ───────────────────────
    async with GLPIClient() as glpi:
        requester_id = None
        requester_name: str | None = None
        if caller_number != "desconocido":
            requester_id = await glpi.find_user_by_phone(caller_number)
            if requester_id:
                requester_name = await glpi.get_user_name(requester_id)
                logger.info("Comercial identificado en GLPI: user_id=%d, nombre=%s", requester_id, requester_name)
            else:
                logger.info("Comercial no encontrado en GLPI para %s; ticket sin asignar.", caller_number)

        # ── 5. Crear instancia de tools con referencia a la Room, transcripción y caller ───
        tools = GLPITools(
            glpi_client=glpi,
            room=ctx.room,
            transcription=transcription,
            caller_number=caller_number,
        )

        # ── 6. Construir el VoicePipelineAgent ────────────────────────────────────
        agent = VoicePipelineAgent(
            # VAD: Silero detecta cuando el usuario empieza/para de hablar
            # Umbral alto (0.85) para telefonía SIP — evita que eco/ruido corte al agente
            vad=silero.VAD.load(
                activation_threshold=0.85,
            ),

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
                language="es",
                voice=config.CARTESIA_VOICE_ID,
                encoding="pcm_s16le",
                sample_rate=8000,
            ),

            # System prompt con las instrucciones de comportamiento y datos de la llamada
            chat_ctx=_build_initial_chat_ctx(caller_number, requester_name),

            # Tools disponibles para el LLM
            fnc_ctx=tools,

            # Parámetros anti-interrupción para telefonía SIP
            interrupt_min_words=2,          # mínimo 2 palabras del usuario para interrumpir
            min_endpointing_delay=1.5,      # dar más tiempo de silencio (1.5s) para que el usuario pueda pensar y dictar los números sin que se corte

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
            # Saludo inicial nulo. El LLM empezará hablando en el primer turno según el sistema
            await agent.say(
                "Hola. Soy el asistente de tickets de Ausarta. Permíteme un segundo para identificar tu número.",
                allow_interruptions=False,
            )
            logger.info("VoicePipelineAgent activo y esperando interacción del comercial.")
            # Mantener el agente vivo hasta que la llamada termine (máximo 1 hora)
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            # Dentro del finally guardamos la transcripción y lo adjuntamos al GLPI si hay ticket creado.
            ruta = await transcription.save_to_file()
            
            # Verificamos si en la clase tools se guardó un ticket creado
            if getattr(tools, "ticket_creado_id", None):
                await transcription.save_to_glpi(glpi, tools.ticket_creado_id)
            
            logger.info("Transcripción guardada en: %s", ruta)
            # El context manager se encierra aquí y llamará a glpi.kill_session() a continuación


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="glpi_inigo",
        )
    )
