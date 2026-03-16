"""
glpi_client.py — Wrapper async para la REST API de GLPI.

Responsabilidad: gestionar la autenticación (session token), crear tickets,
consultarlos y buscar usuarios por número de teléfono. Todas las llamadas HTTP
se ejecutan en un hilo separado mediante asyncio.to_thread para no bloquear
el event loop de LiveKit Agents.
"""

import asyncio
import logging
import time
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

# Mapeo de nombres de categoría (legible) a itilcategories_id de GLPI.
# Ajusta los IDs según tu instancia de GLPI.
CATEGORY_MAP: dict[str, int] = {
    "hardware": 1,
    "software": 2,
    "red": 3,
    "impresora": 4,
    "otro": 0,   # 0 = sin categoría
}

# Mapeo de estado numérico GLPI a texto legible en español
ESTADO_MAP: dict[int, str] = {
    1: "nuevo",
    2: "en proceso (asignado)",
    3: "en proceso (planificado)",
    4: "pendiente",
    5: "resuelto",
    6: "cerrado",
}

# Tiempo de vida de la sesión en segundos (1 hora)
SESSION_TTL = 3600


class GLPIClient:
    """
    Cliente asíncrono para interactuar con la REST API de GLPI.

    Ejemplo de uso:
        client = GLPIClient()
        ticket_id = await client.create_ticket("Sin internet", "El portátil no conecta", 3, "red")
        ticket = await client.get_ticket(ticket_id)
    """

    def __init__(self) -> None:
        self._session_token: Optional[str] = None
        self._session_expires_at: float = 0.0
        self._base_url: str = config.GLPI_URL.rstrip("/")
        self._headers_base: dict[str, str] = {
            "App-Token": config.GLPI_APP_TOKEN,
            "Content-Type": "application/json",
        }

    # ── Autenticación ──────────────────────────────────────────────────────────

    def _is_session_valid(self) -> bool:
        """Comprueba si el token de sesión actual sigue siendo válido."""
        return bool(self._session_token) and time.time() < self._session_expires_at

    def _do_login(self) -> str:
        """
        Realiza el login HTTP de forma síncrona (se llama desde asyncio.to_thread).
        Devuelve el session_token obtenido.
        """
        url = f"{self._base_url}/initSession"
        response = requests.get(
            url,
            headers=self._headers_base,
            auth=(config.GLPI_USER, config.GLPI_PASS),
            timeout=10,
        )
        response.raise_for_status()
        token = response.json()["session_token"]
        logger.info("Sesión GLPI iniciada correctamente.")
        return token

    async def _login(self) -> str:
        """
        Obtiene o reutiliza el session_token (caché de 1 hora).
        Si la sesión ha expirado, realiza un nuevo login.
        """
        if self._is_session_valid():
            return self._session_token  # type: ignore[return-value]

        token = await asyncio.to_thread(self._do_login)
        self._session_token = token
        self._session_expires_at = time.time() + SESSION_TTL
        return token

    def _auth_headers(self, token: str) -> dict[str, str]:
        """Construye las cabeceras HTTP con el session token."""
        return {**self._headers_base, "Session-Token": token}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        retry: bool = True,
    ) -> dict:
        """
        Realiza una petición autenticada a la API de GLPI.
        Si devuelve 401 y retry=True, hace re-login y reintenta una vez.
        """
        token = await self._login()

        def _sync_request() -> requests.Response:
            return requests.request(
                method,
                f"{self._base_url}{path}",
                headers=self._auth_headers(token),
                json=json,
                params=params,
                timeout=15,
            )

        response = await asyncio.to_thread(_sync_request)

        # Re-login automático en caso de sesión expirada en el servidor
        if response.status_code == 401 and retry:
            logger.warning("Token GLPI expirado; iniciando nueva sesión…")
            self._session_token = None
            self._session_expires_at = 0.0
            return await self._request(method, path, json=json, params=params, retry=False)

        response.raise_for_status()
        return response.json() if response.content else {}

    # ── Operaciones de tickets ─────────────────────────────────────────────────

    async def create_ticket(
        self,
        title: str,
        description: str,
        urgency: int = 3,
        category: str = "otro",
        requester_id: Optional[int] = None,
    ) -> int:
        """
        Crea un ticket en GLPI y devuelve su ID numérico.

        Parámetros:
            title         — Título del ticket (máx. 80 caracteres recomendado)
            description   — Descripción detallada del problema
            urgency       — 1 (muy urgente) … 5 (muy baja)
            category      — Nombre de categoría: hardware, software, red, impresora, otro
            requester_id  — ID interno GLPI del usuario solicitante (opcional)
        """
        category_id = CATEGORY_MAP.get(category.lower(), 0)

        payload: dict = {
            "input": {
                "name": title[:80],
                "content": description,
                "urgency": urgency,
                "type": 1,           # 1 = Incident, 2 = Request
                "status": 1,         # 1 = New
            }
        }

        if category_id:
            payload["input"]["itilcategories_id"] = category_id

        if requester_id:
            payload["input"]["_users_id_requester"] = requester_id

        data = await self._request("POST", "/Ticket", json=payload)
        ticket_id: int = data["id"]
        logger.info("Ticket GLPI creado con ID=%d", ticket_id)
        return ticket_id

    async def get_ticket(self, ticket_id: int) -> dict:
        """
        Consulta un ticket por su ID.

        Devuelve un dict con las claves: id, titulo, estado, fecha.
        Lanza KeyError si el ticket no existe.
        """
        data = await self._request("GET", f"/Ticket/{ticket_id}")

        estado_num: int = data.get("status", 0)
        estado_texto: str = ESTADO_MAP.get(estado_num, f"desconocido ({estado_num})")

        return {
            "id": data["id"],
            "titulo": data.get("name", "Sin título"),
            "estado": estado_texto,
            "fecha": data.get("date_creation", "desconocida"),
        }

    # ── Búsqueda de usuarios ───────────────────────────────────────────────────

    async def find_user_by_phone(self, phone: str) -> Optional[int]:
        """
        Busca un usuario GLPI cuyo campo 'mobile' o 'phone' coincide con el número.
        Devuelve el ID interno de GLPI o None si no se encuentra.
        """
        # Normalizar: quitar espacios y el prefijo internacional +34
        normalized = phone.strip().lstrip("+").lstrip("34")

        try:
            users = await self._request(
                "GET",
                "/User",
                params={
                    "searchText[mobile]": normalized,
                    "range": "0-1",
                    "forcedisplay[0]": "1",   # id
                    "forcedisplay[1]": "3",   # nombre
                    "forcedisplay[2]": "11",  # móvil
                },
            )
            if users and isinstance(users, list):
                user_id: int = users[0]["id"]
                logger.info("Usuario GLPI encontrado: id=%d para teléfono %s", user_id, phone)
                return user_id
        except Exception as exc:
            logger.warning("No se pudo buscar usuario por teléfono %s: %s", phone, exc)

        return None

    # ── Seguimientos (followups) ───────────────────────────────────────────────

    async def add_followup(self, ticket_id: int, content: str) -> None:
        """
        Añade un seguimiento (ITILFollowup) a un ticket existente.

        Se usa para adjuntar la transcripción completa de la llamada al ticket
        una vez que este ha sido creado durante la conversación.

        Parámetros:
            ticket_id — ID del ticket al que añadir el followup
            content   — Texto del seguimiento (la transcripción formateada)
        """
        payload = {
            "input": {
                "itemtype": "Ticket",
                "items_id": ticket_id,
                "content": content,
                "is_private": 0,  # 0 = público (visible al solicitante)
            }
        }
        try:
            await self._request("POST", "/ITILFollowup", json=payload)
            logger.info("Followup añadido correctamente al ticket GLPI %d.", ticket_id)
        except Exception as exc:
            logger.warning(
                "No se pudo añadir followup al ticket %d: %s", ticket_id, exc
            )
