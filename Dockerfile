FROM python:3.11-slim

# Evitar preguntas interactivas durante instalación
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instalar dependencias del sistema necesarias para audio y ML
RUN apt-get update && apt-get install -y \
    gcc \
    libsndfile1 \
    ffmpeg \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código
COPY . .

# Descargar modelos necesarios (Silero VAD) en tiempo de build
# Añadimos '|| true' para que si falla la red en el build, no bloquee el despliegue
RUN python -c "from livekit.plugins import silero; silero.VAD.load()" || true

# Carpeta para transcripciones (volumen montable)
RUN mkdir -p /app/transcripciones

# Arrancar el agente
CMD ["python", "agent.py", "start"]
