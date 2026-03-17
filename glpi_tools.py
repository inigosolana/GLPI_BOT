"""
glpi_tools.py — Tools que el LLM puede invocar durante la conversación telefónica.

Responsabilidad: envolver las operaciones de GLPIClient en métodos decorados con
@llm.ai_callable para que el VoicePipelineAgent los exponga al modelo de lenguaje
via function calling. Cada parámetro lleva una descripción @llm.TypeInfo para que
el LLM sepa exactamente qué valor enviar.
"""

import logging
import re
from typing import TYPE_CHECKING, Annotated, Optional

import httpx
from livekit.agents import llm
from livekit.agents.llm import FunctionContext

from glpi_client import GLPIClient

if TYPE_CHECKING:
    from transcription import CallTranscription

logger = logging.getLogger(__name__)


class GLPITools(FunctionContext):
    """
    Conjunto de herramientas GLPI accesibles por el LLM durante la llamada.

    El VoicePipelineAgent recibe esta instancia como `fnc_ctx` y la pasa al
    modelo de lenguaje, que decide autónomamente cuándo y cómo invocar cada tool.
    """

    def __init__(
        self,
        glpi_client: GLPIClient,
        room=None,
        transcription: Optional["CallTranscription"] = None,
        caller_number: str = "desconocido",
    ) -> None:
        """
        Parámetros:
            glpi_client   — Instancia de GLPIClient ya configurada
            room          — Referencia a la Room de LiveKit para poder desconectarse
            transcription — Instancia de CallTranscription para adjuntar al ticket
            caller_number — Número de teléfono del llamante (sip.callerId)
        """
        super().__init__()
        self._glpi = glpi_client
        self._room = room
        self._transcription = transcription
        self._caller_number = caller_number
        self.ticket_creado_id: Optional[int] = None

    # ── Tool: crear ticket ─────────────────────────────────────────────────────

    @llm.ai_callable(description="Crea un ticket de soporte en GLPI")
    async def crear_ticket(
        self,
        titulo: Annotated[
            str,
            llm.TypeInfo(description="Título breve del problema, máximo 80 caracteres"),
        ],
        descripcion: Annotated[
            str,
            llm.TypeInfo(description="Descripción detallada del problema"),
        ],
        urgencia: Annotated[
            int,
            llm.TypeInfo(description="Nivel de urgencia: 1=muy urgente, 3=normal, 5=baja"),
        ] = 3,
        categoria: Annotated[
            str,
            llm.TypeInfo(
                description="Categoría del problema: hardware, software, red, impresora u otro"
            ),
        ] = "otro",
    ) -> str:
        """
        Crea un ticket en GLPI con los datos proporcionados por el usuario y
        devuelve una confirmación con el número de ticket generado.
        """
        logger.info(
            "LLM solicita crear_ticket: titulo='%s' urgencia=%d categoria='%s'",
            titulo,
            urgencia,
            categoria,
        )
        try:
            ticket_id = await self._glpi.create_ticket(
                title=titulo,
                description=descripcion,
                urgency=urgencia,
                category=categoria,
            )
            self.ticket_creado_id = ticket_id
            resultado = f"Ticket {ticket_id} creado correctamente"
            logger.info(resultado)
            return resultado
        except httpx.HTTPStatusError as exc:
            logger.error("Error del servidor GLPI al crear ticket: %s", exc, exc_info=True)
            return "Error del servidor al crear el ticket. Es posible que el servicio esté interrumpido."
        except httpx.RequestError as exc:
            logger.error("Error de red al crear ticket: %s", exc, exc_info=True)
            return "No se pudo conectar con el servidor para crear el ticket."
        except Exception as exc:
            logger.error("Error inesperado al crear ticket: %s", exc, exc_info=True)
            return "Hubo un error inesperado al intentar crear el ticket. Inténtelo más tarde."

    # ── Tool: consultar ticket por ID ──────────────────────────────────────────

    @llm.ai_callable(description="Consulta el estado de un ticket existente en GLPI")
    async def consultar_ticket(
        self,
        ticket_id: Annotated[
            int,
            llm.TypeInfo(description="Número del ticket a consultar"),
        ],
    ) -> str:
        """
        Consulta un ticket GLPI por su ID y devuelve un resumen con el título
        y el estado actual en español.
        """
        logger.info("LLM solicita consultar_ticket: id=%d", ticket_id)
        try:
            ticket = await self._glpi.get_ticket(ticket_id)
            resultado = (
                f"El ticket {ticket['id']} '{ticket['titulo']}' "
                f"está en estado: {ticket['estado']}"
            )
            logger.info(resultado)
            return resultado
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return f"El ticket número {ticket_id} no existe en el sistema."
            logger.warning("Error del servidor al consultar ticket %d: %s", ticket_id, exc)
            return f"Hubo un fallo del servidor al consultar el ticket {ticket_id}."
        except httpx.RequestError as exc:
            logger.warning("Error de conectividad al consultar ticket %d: %s", ticket_id, exc)
            return "Fallo de comunicación con GLPI. No pude consultar el ticket."
        except Exception as exc:
            logger.warning("Ticket %d no encontrado o fallo impredecible: %s", ticket_id, exc)
            return f"No encontré el ticket número {ticket_id}"

    # ── Tool: consultar mis tickets abiertos ───────────────────────────────────

    @llm.ai_callable(
        description="Lista todos los tickets abiertos del comercial que llama"
    )
    async def consultar_mis_tickets(self) -> str:
        """
        Busca los tickets activos asociados a la entidad del comercial.
        Para cada ticket obtiene: número, título, estado, técnico asignado
        y último comentario. Igual que el bot de Telegram.
        """
        logger.info("LLM solicita consultar_mis_tickets para %s", self._caller_number)
        try:
            # Obtener la entidad GLPI asociada al número del llamante
            entities_id = await self._glpi.find_entity_by_phone(self._caller_number)
            if not entities_id:
                return (
                    "No encontré su empresa en el sistema. "
                    "Por favor contacte con soporte para registrarse."
                )

            tickets = await self._glpi.get_tickets_by_entity(entities_id)

            if not tickets:
                return "No hay tickets abiertos para su empresa en este momento."

            # Mapeo de estados igual que el bot de Telegram
            estados = {
                1: "Nuevo",
                2: "En curso",
                3: "Planificado",
                4: "En espera",
                5: "Resuelto",
                6: "Cerrado",
            }

            resumen = f"Tiene {len(tickets)} tickets abiertos. "

            # Limitar a 5 para no saturar la llamada
            for t in tickets[:5]:
                ticket_id = t.get("2")
                titulo = t.get("1", "Sin título")
                estado_num = int(t.get("12", 1))
                tecnico_id = t.get("5", 0)

                estado_texto = estados.get(estado_num, "Desconocido")
                tecnico_nombre = await self._glpi.get_user_name(
                    int(tecnico_id) if tecnico_id else 0
                )

                # Obtener y limpiar el último comentario
                followups = await self._glpi.get_ticket_followups(int(ticket_id))
                ultimo_comentario = ""
                if followups:
                    raw = followups[0].get("content", "")
                    # Eliminar etiquetas HTML del comentario
                    ultimo_comentario = re.sub(r"<[^>]+>", "", raw).strip()
                    if len(ultimo_comentario) > 100:
                        ultimo_comentario = ultimo_comentario[:100] + "..."

                resumen += (
                    f"Ticket {ticket_id}: {titulo}. "
                    f"Estado: {estado_texto}. "
                    f"Técnico: {tecnico_nombre}. "
                )
                if ultimo_comentario:
                    resumen += f"Último comentario: {ultimo_comentario}. "

            if len(tickets) > 5:
                resumen += f"Y {len(tickets) - 5} tickets más."

            return resumen

        except httpx.HTTPStatusError as exc:
            logger.error("Error de servidor en consultar_mis_tickets: %s", exc, exc_info=True)
            return "El servidor de GLPI ha rechazado la consulta. Inténtelo más tarde."
        except httpx.RequestError as exc:
            logger.error("Error de red en consultar_mis_tickets: %s", exc, exc_info=True)
            return "Fallo de conexión con GLPI al intentar consultar sus tickets abiertos."
        except Exception as exc:
            logger.error("Error en consultar_mis_tickets: %s", exc, exc_info=True)
            return "Error al consultar los tickets. Inténtelo de nuevo."

    # ── Tool: finalizar llamada ────────────────────────────────────────────────

    @llm.ai_callable(description="Finaliza la llamada cuando el usuario se despide")
    async def finalizar_llamada(self) -> str:
        """
        Desconecta el agente de la Room de LiveKit, lo que provoca que LiveKit
        Server cierre la sesión SIP con el participante externo.
        """
        logger.info("LLM solicita finalizar_llamada; desconectando de la Room…")
        try:
            if self._room is not None:
                await self._room.disconnect()
        except Exception as exc:
            logger.warning("Error al desconectar la Room: %s", exc)
        return "Llamada finalizada"
