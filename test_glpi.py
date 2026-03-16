"""
test_glpi.py — Script rápido para validar la conexión con GLPI.
"""
import asyncio
import logging
from glpi_client import GLPIClient

# Configurar logging para ver qué pasa
logging.basicConfig(level=logging.INFO)

async def test():
    client = GLPIClient()
    print("--- Probando conexión con GLPI ---")
    try:
        # El primer método que llame a _request forzará el login
        # Intentamos obtener un ticket alto que probablemente no exista 
        # para ver si al menos el login funciona.
        print("Intentando login y consulta de ticket 1...")
        ticket = await client.get_ticket(1)
        print(f"Éxito: {ticket}")
    except Exception as e:
        print(f"Error detectado: {e}")
        print("Nota: Si el error es 404, el login funcionó pero el ticket 1 no existe.")

if __name__ == "__main__":
    asyncio.run(test())
