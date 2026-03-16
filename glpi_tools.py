"""
glpi_tools.py — Tools que el LLM puede invocar durante la conversación telefónica.

Responsabilidad: envolver las operaciones de GLPIClient en métodos decorados con
@llm.ai_callable para que el VoicePipelineAgent los exponga al modelo de lenguaje
via function calling. Cada parámetro lleva una descripción @llm.TypeInfo para que
el LLM sepa exactamente qué valor enviar.
"""

import logging
from typing import Annotated, Optional

from livekit.agents import llm
from livekit.agents.llm import FunctionContext

from glpi_client import GLPIClient

logger = logging.getLogger(__name__)


class GLPITools(FunctionContext):
    """
    Conjunto de herramientas GLPI accesibles por el LLM durante la llamada.

    El VoicePipelineAgent recibe esta instancia como `fnc_ctx` y la pasa al
    modelo de lenguaje, que decide autónomamente cuándo y cómo invocar cada tool.
    """

    def __init__(self, glpi_client: GLPIClient, room=None) -> None:
        """
        Parámetros:
            glpi_client — Instancia de GLPIClient ya configurada
            room        — Referencia a la Room de LiveKit para poder desconectarse
        """
        super().__init__()
        self._glpi = glpi_client
        self._room = room

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
            resultado = f"Ticket {ticket_id} creado correctamente"
            logger.info(resultado)
            return resultado
        except Exception as exc:
            logger.error("Error al crear ticket: %s", exc, exc_info=True)
            return "Error al crear el ticket, inténtelo de nuevo"

    # ── Tool: consultar ticket ─────────────────────────────────────────────────

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
        except Exception as exc:
            logger.warning("Ticket %d no encontrado: %s", ticket_id, exc)
            return f"No encontré el ticket número {ticket_id}"

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
