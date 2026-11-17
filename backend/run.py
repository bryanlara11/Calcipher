"""Dev launcher: `python run.py [--reload]`.

psycopg's async driver needs a selector event loop, which Windows does not use by default, so
the policy is set here and uvicorn is told not to install its own loop.
"""

import sys

import uvicorn

from app.db.session import ensure_selector_event_loop_policy

if __name__ == "__main__":
    ensure_selector_event_loop_policy()
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload="--reload" in sys.argv,
        loop="none" if sys.platform == "win32" else "auto",
    )
