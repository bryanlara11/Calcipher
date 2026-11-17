from dev_db import sync_env

NEW = "postgresql+psycopg://postgres:@127.0.0.1:58448/calcipher"


def test_creates_env_when_missing(tmp_path):
    env = tmp_path / ".env"
    assert sync_env(NEW, env).startswith("created")
    assert f"DATABASE_URL={NEW}" in env.read_text(encoding="utf-8").splitlines()


def test_updates_embedded_port_and_keeps_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(
        b"# comment\r\nDATABASE_URL=postgresql+psycopg://postgres:@127.0.0.1:49488/calcipher\r\n"
        b"EMBEDDER_BACKEND=hash\r\nGOOGLE_PLACES_API_KEY=secret\r\n"
    )
    assert sync_env(NEW, env).startswith("updated")
    assert env.read_bytes() == (
        b"# comment\r\nDATABASE_URL=" + NEW.encode() + b"\r\n"
        b"EMBEDDER_BACKEND=hash\r\nGOOGLE_PLACES_API_KEY=secret\r\n"  # CRLF and other values kept
    )


def test_leaves_other_databases_alone(tmp_path):
    env = tmp_path / ".env"
    docker = "DATABASE_URL=postgresql+psycopg://calcipher:calcipher@localhost:5432/calcipher\n"
    env.write_text(docker, encoding="utf-8")
    assert sync_env(NEW, env).startswith("left")
    assert env.read_text(encoding="utf-8") == docker


def test_adds_line_when_missing_and_is_idempotent(tmp_path):
    env = tmp_path / ".env"
    env.write_text("EMBEDDER_BACKEND=hash\n", encoding="utf-8")
    assert sync_env(NEW, env).startswith("updated")
    assert sync_env(NEW, env).endswith("already up to date")
    assert env.read_text(encoding="utf-8") == f"EMBEDDER_BACKEND=hash\nDATABASE_URL={NEW}\n"
