"""Start a local embedded PostgreSQL 16 + pgvector (no Docker needed) and point backend/.env at it.

    pip install pgserver
    python dev_db.py            # starts (or reuses) the server in backend/.pgdata, updates .env
    python dev_db.py --no-env   # same, but only prints the URL
    python dev_db.py --stop     # stops it

The server can come back on a different port after a restart, so the DATABASE_URL line in
backend/.env is rewritten each time. A DATABASE_URL pointing anywhere else (e.g. the
docker-compose database) is left untouched.

The embedded build has pgvector but not pg_trgm; CalCipher works without it (the trigram index
is simply skipped). Use docker-compose for a production-like database.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = BACKEND_DIR / ".pgdata"
ENV_PATH = BACKEND_DIR / ".env"
DB_NAME = "calcipher"

_ENV_LINE = re.compile(r"^\s*DATABASE_URL\s*=(.*)$")
# The shape of URLs this script itself writes: local host, user postgres, database calcipher.
_EMBEDDED_URL = re.compile(
    rf"^postgresql(\+psycopg)?://postgres:[^@]*@(127\.0\.0\.1|localhost):\d+/{DB_NAME}$"
)


def sync_env(url: str, env_path: Path = ENV_PATH) -> str:
    """Write `url` as DATABASE_URL in env_path. Returns what happened, for printing."""
    if not env_path.exists():
        env_path.write_text(
            "# Local dev settings (git-ignored). DATABASE_URL is maintained by dev_db.py.\n"
            f"DATABASE_URL={url}\n",
            encoding="utf-8",
        )
        return f"created {env_path.name} with DATABASE_URL"

    raw = env_path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        m = _ENV_LINE.match(line)
        if not m:
            continue
        current = m.group(1).strip().strip("'\"")
        if current == url:
            return f"{env_path.name} already up to date"
        if current and not _EMBEDDED_URL.match(current):
            return (f"left {env_path.name} unchanged: its DATABASE_URL points at another database. "
                    f"Use {url} there to switch to the embedded one.")
        lines[i] = f"DATABASE_URL={url}"
        break
    else:
        lines.append(f"DATABASE_URL={url}")

    tmp = env_path.with_name(env_path.name + ".tmp")
    tmp.write_bytes((newline.join(lines) + newline).encode("utf-8"))
    os.replace(tmp, env_path)  # atomic: never leaves a half-written .env
    return f"updated DATABASE_URL in {env_path.name}"


def main(argv: list[str]) -> None:
    import pgserver

    server = pgserver.get_server(DATA_DIR, cleanup_mode=None)  # keep running after this script exits
    if "--stop" in argv:
        server.cleanup()
        print("stopped")
        return
    exists = server.psql(f"SELECT 1 FROM pg_database WHERE datname = '{DB_NAME}';")
    if "1 row" not in exists:
        server.psql(f"CREATE DATABASE {DB_NAME};")
    url = server.get_uri(DB_NAME).replace("postgresql://", "postgresql+psycopg://", 1)
    print(url)
    if "--no-env" not in argv:
        print(sync_env(url))


if __name__ == "__main__":
    main(sys.argv[1:])
