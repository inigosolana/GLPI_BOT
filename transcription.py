"""
transcription.py — Gestión de transcripciones de llamadas telefónicas.

Responsabilidad: acumular las intervenciones de usuario y agente durante una
llamada, formatearlas como texto legible y persistirlas en fichero local y
opcionalmente como seguimiento (followup) en el ticket GLPI correspondiente.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from glpi_client import GLPIClient

logger = logging.getLogger(__name__)

# Carpeta donde se guardan las transcripciones
TRANSCRIPCIONES_DIR = "transcripciones"


class CallTranscription:
    """
    Registra y persiste la transcripción completa de una llamada telefónica.

    Uso típico en agent.py:
        transcription = CallTranscription(caller_number, room_name)
        transcription.add_entry("USUARIO", "Hola, necesito un ticket")
        transcription.add_entry("AGENTE", "Por supuesto, ¿cuál es el problema?")
        ruta = await transcription.save_to_file()
    """

    def __init__(self, caller_number: str, room_name: str) -> None:
        """
        Parámetros:
            caller_number — Número de teléfono del llamante (sip.callerId)
            room_name     — Nombre de la Room LiveKit asociada a la llamada
        """
        self.caller_number = caller_number
        self.room_name = room_name
        self.started_at = datetime.now()
        self.entries: list[dict] = []

    def add_entry(self, role: str, text: str) -> None:
        """
        Añade una intervención a la transcripción.

        Parámetros:
            role — "USUARIO" o "AGENTE"
            text — Texto transcrito de la intervención
        """
        self.entries.append({
            "role": role,
            "text": text.strip(),
            "timestamp": datetime.now().isoformat(),
        })

    def format_text(self) -> str:
        """
        Devuelve la transcripción completa formateada como texto plano legible.

        Formato:
            === TRANSCRIPCIÓN DE LLAMADA ===
            Llamante: +34612345678
            Room: room-name-xyz
            Inicio: 2026-03-16 12:00:00
            Fin:    2026-03-16 12:05:42
            ================================
            [12:00:05] USUARIO: Hola, necesito ayuda con...
            [12:00:08] AGENTE:  Por supuesto, cuéntame...
            ================================
        """
        now = datetime.now()
        lineas = [
            "=== TRANSCRIPCIÓN DE LLAMADA ===",
            f"Llamante: {self.caller_number}",
            f"Room:     {self.room_name}",
            f"Inicio:   {self.started_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Fin:      {now.strftime('%Y-%m-%d %H:%M:%S')}",
            "================================",
        ]

        for entry in self.entries:
            # Extraer hora HH:MM:SS del timestamp ISO almacenado
            hora = entry["timestamp"][11:19]
            lineas.append(f"[{hora}] {entry['role']}: {entry['text']}")

        lineas.append("================================")
        return "\n".join(lineas)

    async def save_to_file(self) -> str:
        """
        Guarda la transcripción en un fichero de texto dentro de la carpeta
        'transcripciones/'. Crea la carpeta si no existe.

        Devuelve la ruta del fichero guardado.
        """
        # Nombre de fichero: llamada_{numero}_{YYYYMMDD_HHMMSS}.txt
        timestamp_str = self.started_at.strftime("%Y%m%d_%H%M%S")
        # Limpiar el número para usarlo como nombre de fichero
        numero_limpio = self.caller_number.replace("+", "").replace(" ", "_")
        nombre_fichero = f"llamada_{numero_limpio}_{timestamp_str}.txt"
        ruta = os.path.join(TRANSCRIPCIONES_DIR, nombre_fichero)

        contenido = self.format_text()

        def _escribir():
            os.makedirs(TRANSCRIPCIONES_DIR, exist_ok=True)
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(contenido)

        await asyncio.to_thread(_escribir)
        logger.info("Transcripción guardada en: %s", ruta)
        return ruta

    async def save_to_glpi(self, glpi_client: "GLPIClient", ticket_id: int) -> None:
        """
        Adjunta la transcripción como seguimiento (followup) al ticket GLPI indicado.

        No lanza excepciones; si falla, registra un warning y continúa.

        Parámetros:
            glpi_client — Instancia de GLPIClient
            ticket_id   — ID del ticket al que adjuntar la transcripción
        """
        try:
            await glpi_client.add_followup(ticket_id, self.format_text())
            logger.info("Transcripción adjuntada al ticket GLPI %d como followup.", ticket_id)
        except Exception as exc:
            logger.warning(
                "No se pudo adjuntar la transcripción al ticket %d: %s", ticket_id, exc
            )
