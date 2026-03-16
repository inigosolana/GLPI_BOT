# Dockerfile para glpi-voice-bot

FROM python:3.11-slim

# Evitar que Python genere archivos .pyc y habilitar logs en tiempo real
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Instalar dependencias del sistema necesarias (si las hubiera)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código de la aplicación
COPY . .

# Descargar archivos necesarios (VAD, etc.) para evitar descargas en runtime si es posible
# Nota: silero-vad se descarga la primera vez que se usa, pero podemos forzarlo aquí
RUN python -c "from livekit.plugins import silero; silero.VAD.load()"

# Comando por defecto para arrancar el agente
CMD ["python", "agent.py", "start"]
