FROM python:3.11-slim

# Evitar preguntas interactivas durante instalación
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instalar dependencias del sistema necesarias para audio
RUN apt-get update && apt-get install -y \
    gcc \
    libsndfile1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Descargar modelos necesarios (Silero VAD) en tiempo de build
COPY . .
RUN python agent.py download-files

# Carpeta para transcripciones (volumen montable)
RUN mkdir -p /app/transcripciones

# Arrancar el agente
CMD ["python", "agent.py", "start"]
