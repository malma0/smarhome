"""python -m model_service [--port 8090] [--host 127.0.0.1] - see model_service/app.py."""

import argparse
import os
import sys
from pathlib import Path

import uvicorn

LOG = Path(__file__).resolve().parents[1] / "model_service.log"


def main() -> None:
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")  # mlflow (the registry lookup) prints one otherwise
    if sys.stdout is None or sys.stderr is None:  # pythonw (the launcher): no console - a log instead
        sys.stdout = sys.stderr = LOG.open("a", encoding="utf-8", buffering=1)
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("MODEL_SERVICE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MODEL_SERVICE_PORT", "8090")))
    args = parser.parse_args()
    uvicorn.run("model_service.app:create_app", factory=True, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
