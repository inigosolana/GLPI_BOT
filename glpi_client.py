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
from typing import Any, Optional

import httpx

import config

logger = logging.getLogger(__name__)

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
        self._client = httpx.AsyncClient(timeout=15.0)
        self._CATEGORY_MAP: dict[str, int] = {}
        self._categories_loaded: bool = False

    async def __aenter__(self):
        await self.load_categories()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session_token:
            await self.kill_session()
        await self._client.aclose()

    async def get_categories(self) -> list[str]:
        """Devuelve los nombres de las categorías cargadas"""
        await self.load_categories()
        return list(self._CATEGORY_MAP.keys())

    async def load_categories(self) -> None:
        if self._categories_loaded:
            return
        try:
            categories = await self._request("GET", "/ITILCategory", params={"range": "0-100", "is_deleted": "0"})
            if isinstance(categories, list):
                mapping = {}
                for cat in categories:
                    name = cat.get("name", "").lower()
                    cat_id = cat.get("id")
                    if name and cat_id is not None:
                        mapping[name] = cat_id
                
                # Fallback mapping options
                if "hardware" not in mapping: mapping["hardware"] = 1
                if "software" not in mapping: mapping["software"] = 2
                if "red" not in mapping: mapping["red"] = 3
                if "impresora" not in mapping: mapping["impresora"] = 4
                mapping["otro"] = 0
                self._CATEGORY_MAP = mapping
                self._categories_loaded = True
                logger.info("Categorías GLPI cargadas: %d encontradas", len(mapping))
        except Exception as exc:
            logger.warning("Error al cargar categorías ITIL, usando defaults. %s", exc)
            self._CATEGORY_MAP = {
                "hardware": 1,
                "software": 2,
                "red": 3,
                "impresora": 4,
                "otro": 0,
            }

    # ── Autenticación ──────────────────────────────────────────────────────────

    def _is_session_valid(self) -> bool:
        """Comprueba si el token de sesión actual sigue siendo válido."""
        return bool(self._session_token) and time.time() < self._session_expires_at

    async def _do_login(self) -> str:
        """
        Realiza el login HTTP de forma asíncrona nativa.
        Usa autenticación por user_token en lugar de usuario+contraseña.
        Devuelve el session_token obtenido.
        """
        url = f"{self._base_url}/initSession"
        response = await self._client.get(
            url,
            headers={
                **self._headers_base,
                "Authorization": f"user_token {config.GLPI_USER_TOKEN}",
            },
        )
        response.raise_for_status()
        token = response.json()["session_token"]
        logger.info("Sesión GLPI iniciada correctamente con user_token.")
        return token

    async def _login(self) -> str:
        """
        Obtiene o reutiliza el session_token (caché de 1 hora).
        Si la sesión ha expirado, realiza un nuevo login.
        """
        if self._is_session_valid():
            return self._session_token  # type: ignore[return-value]

        token = await self._do_login()
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
    ) -> Any:
        """
        Realiza una petición autenticada a la API de GLPI.
        Si devuelve 401 y retry=True, hace re-login y reintenta una vez.
        """
        token = await self._login()

        url = f"{self._base_url}{path}"
        response = await self._client.request(
            method,
            url,
            headers=self._auth_headers(token),
            json=json,
            params=params,
        )

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
        await self.load_categories()
        category_id = self._CATEGORY_MAP.get(category.lower(), 0)

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

    async def search_user(self, query: str) -> list[dict]:
        """
        Busca usuarios en GLPI por teléfono, firstname, o realname.
        Devuelve una lista de diccionarios con id y name.
        """
        users_found = []
        try:
            # 1. Intentar por teléfono (limpiando espacios)
            normalized_query = query.replace(" ", "")
            if normalized_query.isdigit() or (normalized_query.startswith("+") and normalized_query[1:].isdigit()):
                uid = await self.find_user_by_phone(normalized_query)
                if uid:
                    nom = await self.get_user_name(uid)
                    users_found.append({"id": uid, "name": nom})
                    return users_found
            
            # 2. Intentar buscar por texto
            for field in ["realname", "firstname", "name"]:
                res = await self._request("GET", "/User", params={f"searchText[{field}]": query, "range": "0-5"})
                if res and isinstance(res, list):
                    for u in res:
                        uid = u.get("id")
                        if uid and not any(x["id"] == uid for x in users_found):
                            firstname = u.get("firstname", "")
                            realname = u.get("realname", "")
                            nombre_completo = f"{firstname} {realname}".strip() or u.get("name", "")
                            users_found.append({"id": uid, "name": nombre_completo})
        except Exception as exc:
            logger.warning("Error buscando usuario via free-text %s: %s", query, exc)
        
        return users_found

    async def find_entity_by_user_id(self, user_id: int) -> Optional[int]:
        """Obtiene el entities_id de un usuario dado su ID interno"""
        try:
            data = await self._request("GET", f"/User/{user_id}")
            entity = data.get("entities_id")
            return entity
        except Exception as exc:
            logger.warning("Error obteniendo entities_id del usuario %d: %s", user_id, exc)
            return None

    # ── Búsqueda por entidad ──────────────────────────────────────────────────

    async def find_entity_by_phone(self, phone: str) -> Optional[int]:
        """
        Busca la entities_id de GLPI asociada al número de teléfono del llamante.
        Primero localiza el usuario por teléfono y luego devuelve su entities_id.
        """
        user_id = await self.find_user_by_phone(phone)
        if not user_id:
            return None
        try:
            data = await self._request("GET", f"/User/{user_id}")
            entity = data.get("entities_id")
            logger.info("entities_id=%s para user_id=%d", entity, user_id)
            return entity
        except Exception as exc:
            logger.warning("Error obteniendo entities_id del usuario %d: %s", user_id, exc)
            return None

    async def get_tickets_by_entity(self, entities_id: int) -> list[dict]:
        """
        Busca tickets activos de una entidad GLPI mediante la API de búsqueda.
        Replica la consulta del workflow n8n:
          - field=80 (entities_id) equals entities_id
          - field=12 (status) equals notold (exclye resueltos y cerrados)
        Devuelve lista de dicts con los campos de cada ticket.
        """
        params = (
            "is_deleted=0&as_map=0"
            "&criteria[0][field]=80&criteria[0][searchtype]=equals"
            f"&criteria[0][value]={entities_id}"
            "&criteria[1][link]=AND&criteria[1][field]=12"
            "&criteria[1][searchtype]=equals&criteria[1][value]=notold"
            "&forcedisplay[0]=2&forcedisplay[1]=1&forcedisplay[2]=7"
            "&forcedisplay[3]=4&forcedisplay[4]=80&forcedisplay[5]=5"
            "&range=0-50&is_recursive=1"
        )
        token = await self._login()

        response = await self._client.get(
            f"{self._base_url}/search/Ticket?{params}",
            headers=self._auth_headers(token),
        )

        response.raise_for_status()
        data = response.json()
        tickets = data.get("data", [])
        logger.info("%d tickets encontrados para entities_id=%d", len(tickets), entities_id)
        return tickets

    async def get_ticket_followups(self, ticket_id: int) -> list[dict]:
        """
        Obtiene los followups (comentarios) de un ticket ordenados por fecha descendente.
        """
        try:
            data = await self._request("GET", f"/Ticket/{ticket_id}/ITILFollowup")
            if isinstance(data, list):
                return sorted(data, key=lambda x: x.get("date", ""), reverse=True)
        except Exception as exc:
            logger.warning("Error obteniendo followups del ticket %d: %s", ticket_id, exc)
        return []

    async def get_user_name(self, user_id: int) -> str:
        """
        Obtiene el nombre completo de un usuario GLPI por su ID.
        Devuelve 'Sin asignar' si user_id es 0 o None.
        """
        if not user_id or user_id == 0:
            return "Sin asignar"
        try:
            data = await self._request("GET", f"/User/{user_id}")
            firstname = data.get("firstname", "")
            realname = data.get("realname", "")
            nombre = f"{firstname} {realname}".strip()
            return nombre or f"Técnico ID: {user_id}"
        except Exception:
            return f"Técnico ID: {user_id}"

    # ── Gestión de sesión ─────────────────────────────────────────────────────

    async def kill_session(self) -> None:
        """
        Cierra la sesión GLPI activa llamando a /killSession.
        Se llama al final de context manager.
        """
        if not self._session_token:
            return
        try:
            token = self._session_token

            await self._client.get(
                f"{self._base_url}/killSession",
                headers=self._auth_headers(token),
            )

            self._session_token = None
            self._session_expires_at = 0.0
            logger.info("Sesión GLPI cerrada correctamente.")
        except Exception as exc:
            logger.warning("Error al cerrar sesión GLPI: %s", exc)

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
