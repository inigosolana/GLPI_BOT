"""
config.py — Carga y gestión de variables de entorno para glpi-voice-bot.
"""

import logging
import os
from typing import List

from dotenv import load_dotenv

# Cargar el fichero .env si existe (en desarrollo)
load_dotenv()

logger = logging.getLogger(__name__)

def _get_env(name: str, default: str = "") -> str:
    """Obtiene una variable de entorno de forma segura."""
    return os.getenv(name, default)

# ── LiveKit ────────────────────────────────────────────────────────────────────
LIVEKIT_URL: str = _get_env("LIVEKIT_URL")
LIVEKIT_API_KEY: str = _get_env("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET: str = _get_env("LIVEKIT_API_SECRET")

# ── STT: Deepgram ──────────────────────────────────────────────────────────────
DEEPGRAM_API_KEY: str = _get_env("DEEPGRAM_API_KEY")

# ── TTS: Cartesia ──────────────────────────────────────────────────────────────
CARTESIA_API_KEY: str = _get_env("CARTESIA_API_KEY")
CARTESIA_VOICE_ID: str = _get_env("CARTESIA_VOICE_ID", "02aeee94-c02b-456e-be7a-659672acf82d")

# ── LLM: Groq (compatible OpenAI) ─────────────────────────────────────────────
GROQ_API_KEY: str = _get_env("GROQ_API_KEY")

# ── GLPI REST API ──────────────────────────────────────────────────────────────
GLPI_URL: str = _get_env("GLPI_URL")
GLPI_APP_TOKEN: str = _get_env("GLPI_APP_TOKEN")
GLPI_USER_TOKEN: str = _get_env("GLPI_USER_TOKEN")

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

def validate_config():
    """
    Valida que todas las variables de entorno obligatorias estén presentes.
    Se llama al arrancar el agente, pero no durante el build de Docker.
    """
    required = [
        "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
        "DEEPGRAM_API_KEY", "CARTESIA_API_KEY", "GROQ_API_KEY",
        "GLPI_URL", "GLPI_APP_TOKEN", "GLPI_USER_TOKEN"
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        error_msg = f"Faltan variables de entorno obligatorias: {', '.join(missing)}"
        logger.error(error_msg)
        raise EnvironmentError(error_msg)
    logger.info("Configuración validada correctamente.")
