"""
config.py — Carga y validación de variables de entorno para glpi-voice-bot.

Responsabilidad: centralizar toda la configuración de la aplicación usando
python-dotenv, exponiendo constantes tipadas para el resto de módulos.
"""

import logging
import os

from dotenv import load_dotenv

# Cargar el fichero .env si existe (en producción las vars ya están en el entorno)
load_dotenv()


def _require(name: str) -> str:
    """Devuelve el valor de una variable de entorno obligatoria o lanza un error."""
    value = os.getenv(name)
    if not value:
        raise EnvironmentError(
            f"Variable de entorno obligatoria no definida: {name}. "
            "Revisa el fichero .env o las variables del entorno."
        )
    return value


# ── LiveKit ────────────────────────────────────────────────────────────────────
LIVEKIT_URL: str = _require("LIVEKIT_URL")
LIVEKIT_API_KEY: str = _require("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET: str = _require("LIVEKIT_API_SECRET")

# ── STT: Deepgram ──────────────────────────────────────────────────────────────
DEEPGRAM_API_KEY: str = _require("DEEPGRAM_API_KEY")

# ── TTS: Cartesia ──────────────────────────────────────────────────────────────
CARTESIA_API_KEY: str = _require("CARTESIA_API_KEY")

# ── LLM: Groq (compatible OpenAI) ─────────────────────────────────────────────
GROQ_API_KEY: str = _require("GROQ_API_KEY")

# ── GLPI REST API ──────────────────────────────────────────────────────────────
GLPI_URL: str = _require("GLPI_URL")           # https://tuglpi.empresa.com/apirest.php
GLPI_APP_TOKEN: str = _require("GLPI_APP_TOKEN")
GLPI_USER: str = _require("GLPI_USER")
GLPI_PASS: str = _require("GLPI_PASS")

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
