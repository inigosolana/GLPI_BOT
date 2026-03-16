# GLPI Voice Bot

Agente de voz telefónico basado en **LiveKit Agents** que permite a los comerciales
de campo crear y consultar tickets de soporte en **GLPI** mediante una llamada de teléfono,
sin necesidad de acceder al portal web.

---

## Descripción

El bot recibe llamadas a través de un **SIP trunk conectado a LiveKit**. El audio de la
llamada se transcribe en tiempo real con **Deepgram** (STT), el **LLM Llama 3.3 70B** 
(servido por Groq) decide cuándo y cómo llamar a las herramientas de GLPI, y finalmente
**Cartesia** sintetiza la respuesta en audio (TTS) que se devuelve al llamante.

Al finalizar cada llamada, la **transcripción completa** se guarda en fichero local y,
si se creó un ticket durante la llamada, también se adjunta como seguimiento en GLPI.

### Arquitectura

```
Comercial → SIP Trunk → LiveKit Server → WebRTC Audio
                                              ↓
              agent.py (VoicePipelineAgent)
                ├── STT: Deepgram Nova-2 (español)
                ├── LLM: Groq → Llama 3.3 70B (function calling)
                │       ├── Tool: crear_ticket   → GLPI REST API
                │       ├── Tool: consultar_ticket → GLPI REST API
                │       └── Tool: finalizar_llamada
                └── TTS: Cartesia Sonic Multilingual (8 kHz)
```

---

## Requisitos

- Python 3.11 o superior
- Cuenta en [LiveKit Cloud](https://cloud.livekit.io) con SIP trunk configurado
- API Key de [Deepgram](https://console.deepgram.com)
- API Key de [Cartesia](https://play.cartesia.ai)
- API Key de [Groq](https://console.groq.com)
- GLPI con la REST API activada y un App Token generado

---

## Instalación

```bash
# 1. Clonar el repositorio
git clone https://github.com/inigosolana/GLPI_BOT.git
cd GLPI_BOT

# 2. Crear entorno virtual e instalar dependencias
python -m venv .venv
source .venv/bin/activate   # Linux/Mac
.venv\Scripts\activate      # Windows

pip install -r requirements.txt

# 3. Configurar las variables de entorno
cp .env.example .env
# Edita .env con tu editor favorito y rellena todos los valores
```

---

## Cómo rellenar el .env

| Variable | Dónde encontrarla |
|---|---|
| `LIVEKIT_URL` | LiveKit Cloud → tu proyecto → Settings → URL del proyecto |
| `LIVEKIT_API_KEY` | LiveKit Cloud → tu proyecto → Settings → Keys |
| `LIVEKIT_API_SECRET` | LiveKit Cloud → tu proyecto → Settings → Keys |
| `DEEPGRAM_API_KEY` | [console.deepgram.com](https://console.deepgram.com) → API Keys |
| `CARTESIA_API_KEY` | [play.cartesia.ai](https://play.cartesia.ai) → API Keys |
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) → API Keys |
| `GLPI_URL` | URL de tu GLPI + `/apirest.php` (ej: `https://tuglpi.com/apirest.php`) |
| `GLPI_APP_TOKEN` | GLPI → Configuración → General → API → Token de la aplicación |
| `GLPI_USER` | Usuario de GLPI con permisos para crear tickets |
| `GLPI_PASS` | Contraseña del usuario GLPI |

---

## Cómo arrancar el agente

```bash
# Descargar modelos necesarios (solo la primera vez, descarga el VAD de Silero)
python agent.py download-files

# Arrancar el agente (se conecta a LiveKit y espera llamadas)
python agent.py start
```

El agente quedará a la escucha. Cuando llegue una llamada SIP a tu trunk de LiveKit,
el agente se conectará automáticamente a la Room creada y gestionará la conversación.

---

## Cómo probar la conexión con GLPI

```bash
python test_glpi.py
```

Este script verifica que el login con la API de GLPI funciona correctamente antes de
lanzar el agente completo.

---

## Transcripciones de llamadas

Cada llamada genera automáticamente un fichero de texto en la carpeta `transcripciones/`:

```
transcripciones/
└── llamada_34612345678_20260316_120500.txt
```

El formato del fichero es:
```
=== TRANSCRIPCIÓN DE LLAMADA ===
Llamante: 34612345678
Room:     room-sip-abc123
Inicio:   2026-03-16 12:05:00
Fin:      2026-03-16 12:08:42
================================
[12:05:03] USUARIO: Hola, necesito abrir un ticket
[12:05:05] AGENTE: Hola, soy el asistente de helpdesk. ¿En qué puedo ayudarte hoy?
[12:05:12] USUARIO: Mi portátil no conecta a internet
[12:05:15] AGENTE: Ticket 1234 creado correctamente
================================
```

Además, si durante la llamada se crea un ticket, la transcripción se adjunta
automáticamente como **seguimiento** en ese ticket de GLPI.

> ⚠️ La carpeta `transcripciones/` está en el `.gitignore` para proteger los datos de los usuarios.

---

## Estructura del proyecto

```
glpi-voice-bot/
├── agent.py           # Punto de entrada del agente LiveKit
├── glpi_client.py     # Wrapper async para la REST API de GLPI
├── glpi_tools.py      # Tools decoradas con @llm.ai_callable para el LLM
├── transcription.py   # Gestión y persistencia de transcripciones de llamadas
├── config.py          # Carga y validación de variables de entorno
├── test_glpi.py       # Script para probar la conexión con GLPI
├── requirements.txt   # Dependencias Python
├── Dockerfile         # Imagen Docker para despliegue en Portainer
├── docker-compose.yml # Composición Docker para despliegue
├── .env.example       # Plantilla de variables de entorno
└── .gitignore         # Exclusiones de Git (incluye .env y transcripciones/)
```

---

## Despliegue con Docker / Portainer

```bash
# Construir la imagen
docker build -t glpi-voice-bot .

# O usar docker-compose
docker-compose up -d
```

En Portainer, crea un nuevo **Stack** y pega el contenido de `docker-compose.yml`.
Añade las variables de entorno del `.env` en la sección "Environment variables" del Stack.
