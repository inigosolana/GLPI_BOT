
import asyncio
import os
import logging
import sys
from dotenv import load_dotenv
from glpi_client import GLPIClient

# Force utf-8 output if possible, or just avoid emojis
# sys.stdout.reconfigure(encoding='utf-8') 

# Configure logging to see what's happening
logging.basicConfig(level=logging.INFO)

load_dotenv()

async def test_client_search():
    client = GLPIClient()
    
    # Test queries
    queries = [
        "621151394",      # Correct phone
        "Iñigo Solana",   # Correct name
        "Iñogo Solana",   # Misspelled (common STT error)
        "Inigo",           # No accent
        "Javier Bilbao"    # Existing user that works
    ]

    print("\n--- TEST DE BUSQUEDA ROBUSTA ---")
    
    for q in queries:
        print(f"\n[PROBANDO QUERY]: '{q}'")
        try:
            users = await client.search_user(q)
            if users:
                for u in users:
                    # Avoid non-ascii in print to be safe with windows cmd
                    print(f"  FOUND: ID={u['id']}, Name={u['name']}")
            else:
                print(f"  NOT FOUND")
        except Exception as e:
            print(f"  ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_client_search())
