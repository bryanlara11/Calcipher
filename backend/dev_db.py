"""Start a local embedded PostgreSQL 16 + pgvector (no Docker needed) and print its DATABASE_URL.

    pip install pgserver
    python dev_db.py            # starts (or reuses) the server in backend/.pgdata
    python dev_db.py --stop     # stops it

The embedded build has pgvector but not pg_trgm; CalCipher works without it (the trigram index
is simply skipped). Use docker-compose for a production-like database.
"""

import sys
from pathlib import Path

import pgserver

DATA_DIR = Path(__file__).resolve().parent / ".pgdata"
DB_NAME = "calcipher"

if __name__ == "__main__":
    server = pgserver.get_server(DATA_DIR, cleanup_mode=None)  # keep running after this script exits
    if "--stop" in sys.argv:
        server.cleanup()
        print("stopped")
        sys.exit(0)
    exists = server.psql(f"SELECT 1 FROM pg_database WHERE datname = '{DB_NAME}';")
    if "1 row" not in exists:
        server.psql(f"CREATE DATABASE {DB_NAME};")
    url = server.get_uri(DB_NAME).replace("postgresql://", "postgresql+psycopg://", 1)
    print(url)
