"""The control panel on its own - for trying it out. Jarvis runs it itself
when APP_PIN is set (app/panel.py, started with the voice app).

    python panel_server.py --port 8765            # this PC only
    python panel_server.py --host 0.0.0.0         # the home network
"""

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.chdir(HERE)  # relative paths (the database, the dataset) as Jarvis itself has them

import uvicorn  # noqa: E402

from app.config import settings  # noqa: E402
from app.panel import create_app  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=settings.panel_port)
    parser.add_argument("--pin", default=None, help="instead of APP_PIN, e.g. a test one")
    args = parser.parse_args()
    if not (args.pin or settings.app_pin):
        raise SystemExit("Set APP_PIN in .env (or pass --pin) - the panel controls the house.")
    uvicorn.run(create_app(pin=args.pin), host=args.host, port=args.port, log_level="warning")
