"""The home model as a service: one HTTP API in front of it for Jarvis and
anything else, with its speed and behavior measured.

    python -m model_service                       (http://localhost:8090, docs at /docs)

    POST /v1/generate   one model turn: system prompt, history, tools -> reply and tool calls
                        (what Jarvis's agent sends - app/llm/home_service.py)
    POST /v1/command    a phrase -> the model's first step (the tool calls it makes), with
                        the prompt and tools Jarvis uses - for a quick try, curl, the load test
    GET  /v1/model      what is served: Ollama model, digest, quantization, registry version
    GET  /v1/stats      latency percentiles and counts since start, as JSON
    GET  /metrics       the same for Prometheus
    GET  /health        200 when the model answers through Ollama, 503 otherwise

Settings (environment, falling back to .env's HOME_LLM_*): MODEL_SERVICE_MODEL,
MODEL_SERVICE_OLLAMA_URL, MODEL_SERVICE_KEEP_ALIVE (how long Ollama keeps the
model loaded - "-1" for always; unset is Ollama's 5 minutes).
"""

from __future__ import annotations

import os
import statistics
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, Info, generate_latest
from pydantic import BaseModel, Field

from app.llm.base import ToolDef
from model_service.inference import OllamaBackend, Turn

BUCKETS = (0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 30.0, 60.0)


class Metrics:
    """Prometheus metrics in a registry of their own (tests make several apps)."""

    def __init__(self):
        self.registry = CollectorRegistry()
        r = self.registry
        self.requests = Counter("home_model_requests_total", "Requests by endpoint and outcome",
                                ["endpoint", "outcome"], registry=r)
        self.latency = Histogram("home_model_request_seconds", "Whole request, as the caller waited",
                                 ["endpoint"], buckets=BUCKETS, registry=r)
        self.inference = Histogram("home_model_inference_seconds", "Ollama's prompt + generation time",
                                   buckets=BUCKETS, registry=r)
        self.load = Counter("home_model_cold_starts_total", "Calls that first had to load the model", registry=r)
        self.tokens = Counter("home_model_tokens_total", "Tokens in and out", ["kind"], registry=r)
        self.tokens_per_second = Gauge("home_model_generation_tokens_per_second", "Of the last call", registry=r)
        self.tool_calls = Counter("home_model_tool_calls_total", "Tool calls the model made", ["tool"], registry=r)
        self.malformed = Counter("home_model_malformed_tool_calls_total",
                                 "Tool calls that weren't valid JSON (dropped)", registry=r)
        self.turns = Counter("home_model_turns_total", "Model turns: tool calls or a plain reply", ["kind"],
                             registry=r)
        self.in_flight = Gauge("home_model_requests_in_flight", "Being answered now", registry=r)
        self.info = Info("home_model", "What is served", registry=r)


class ToolSpec(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class GenerateRequest(BaseModel):
    system: str
    messages: list[dict[str, Any]]
    tools: list[ToolSpec] = Field(default_factory=list)


class CommandRequest(BaseModel):
    text: str = Field(..., min_length=1, examples=["Джарвис, выключи свет на кухне"])
    spoken: bool = True  # said aloud (Jarvis's prompt then asks for replies fit for speech)
    now: datetime | None = None  # the model's clock - "напомни в 8", "что было ночью"


def _blocks(turn: Turn) -> list[dict[str, Any]]:
    return [b.to_dict() for b in turn.response.content]


def _timing(turn: Turn) -> dict[str, Any]:
    return {"total_ms": round(turn.seconds * 1000, 1), "inference_ms": round(turn.inference_seconds * 1000, 1),
            "load_ms": round(turn.load_seconds * 1000, 1), "prompt_tokens": turn.prompt_tokens,
            "completion_tokens": turn.completion_tokens, "tokens_per_second": round(turn.tokens_per_second, 1)}


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)

    def at(p: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(p * len(ordered)))] * 1000, 1)

    return {"p50_ms": at(0.50), "p90_ms": at(0.90), "p95_ms": at(0.95), "p99_ms": at(0.99),
            "mean_ms": round(statistics.fmean(ordered) * 1000, 1), "max_ms": round(ordered[-1] * 1000, 1)}


def registry_version(model: str) -> dict[str, Any]:
    """The served model's version in the MLflow registry (training/tracking.py), if MLflow is here."""
    try:
        from training import tracking

        if not tracking.available():
            return {}
        mlflow = tracking.setup()
        client = mlflow.MlflowClient()
        aliases = {v: k for k, v in client.get_registered_model(tracking.REGISTERED_MODEL).aliases.items()}
        for version in client.search_model_versions(f"name='{tracking.REGISTERED_MODEL}'"):
            if version.tags.get("ollama_model") == model:
                return {"registered_model": tracking.REGISTERED_MODEL, "version": int(version.version),
                        "alias": aliases.get(version.version, "")}
    except Exception:  # noqa: BLE001 - the registry is a nice-to-have here, never a reason to fail
        return {}
    return {}


def default_settings() -> dict[str, str | None]:
    try:
        from app.config import settings

        model, url = settings.home_llm_model, settings.home_llm_url or "http://localhost:11434"
    except Exception:  # noqa: BLE001 - without a .env (a container) the environment says it all
        model, url = "jarvis-home", "http://localhost:11434"
    return {"model": os.environ.get("MODEL_SERVICE_MODEL", model),
            "ollama_url": os.environ.get("MODEL_SERVICE_OLLAMA_URL", url),
            "keep_alive": os.environ.get("MODEL_SERVICE_KEEP_ALIVE") or None}


def create_app(backend: OllamaBackend | None = None) -> FastAPI:
    config = default_settings()
    backend = backend or OllamaBackend(config["model"], config["ollama_url"], config["keep_alive"])
    metrics = Metrics()
    recent: deque[tuple[str, float, bool]] = deque(maxlen=5000)  # (endpoint, seconds, ok) for /v1/stats
    started_at = time.time()
    command_prompt: dict[bool, str] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        info = await backend.model_info()
        metrics.info.info({"model": backend.model, "ollama_url": backend.base_url,
                           "digest": str(info.get("digest", "")), "quantization": str(info.get("quantization", "")),
                           "registry_version": str(registry_version(backend.model).get("version", ""))})
        yield
        await backend.close()

    app = FastAPI(title="Jarvis home model", version="1.0",
                  description="The fine-tuned Qwen2.5-1.5B that runs Jarvis's house, as a service.",
                  lifespan=lifespan)

    async def run_turn(endpoint: str, system: str, messages: list[dict], tools: list[ToolDef]) -> Turn:
        metrics.in_flight.inc()
        started = time.perf_counter()
        try:
            turn = await backend.turn(system, messages, tools)
        except (httpx.HTTPError, OSError) as exc:
            metrics.requests.labels(endpoint, "upstream_error").inc()
            recent.append((endpoint, time.perf_counter() - started, False))
            raise HTTPException(503, f"the model didn't answer: {exc!r}") from exc
        finally:
            metrics.in_flight.dec()
        metrics.requests.labels(endpoint, "ok").inc()
        metrics.latency.labels(endpoint).observe(turn.seconds)
        metrics.inference.observe(turn.inference_seconds)
        if turn.load_seconds > 0.5:
            metrics.load.inc()
        metrics.tokens.labels("prompt").inc(turn.prompt_tokens)
        metrics.tokens.labels("completion").inc(turn.completion_tokens)
        metrics.tokens_per_second.set(turn.tokens_per_second)
        metrics.malformed.inc(turn.malformed_calls)
        calls = [b for b in turn.response.content if b.type == "tool_use"]
        metrics.turns.labels("tool_calls" if calls else "reply").inc()
        for call in calls:
            metrics.tool_calls.labels(call.name or "?").inc()
        recent.append((endpoint, turn.seconds, True))
        return turn

    @app.post("/v1/generate")
    async def generate(request: GenerateRequest) -> dict[str, Any]:
        tools = [ToolDef(name=t.name, description=t.description, parameters=t.parameters) for t in request.tools]
        turn = await run_turn("generate", request.system, request.messages, tools)
        return {"content": _blocks(turn), "stop_reason": turn.response.stop_reason, "model": backend.model,
                "malformed_calls": turn.malformed_calls, "timing": _timing(turn)}

    @app.post("/v1/command")
    async def command(request: CommandRequest) -> dict[str, Any]:
        from app.agent import _HOME_MODEL_TOOLS, current_time_note

        if request.spoken not in command_prompt:
            command_prompt[request.spoken] = _jarvis_prompt(request.spoken)
        now = request.now.astimezone() if request.now else None
        message = {"role": "user", "content": f"{request.text}\n\n[{current_time_note(now)}]"}
        turn = await run_turn("command", command_prompt[request.spoken], [message], _HOME_MODEL_TOOLS)
        calls = [{"name": b.name, "arguments": b.input} for b in turn.response.content if b.type == "tool_use"]
        reply = "".join(b.text or "" for b in turn.response.content if b.type == "text")
        return {"text": request.text, "tool_calls": calls, "reply": reply, "model": backend.model,
                "malformed_calls": turn.malformed_calls, "timing": _timing(turn)}

    @app.get("/v1/model")
    async def model() -> dict[str, Any]:
        return {"model": backend.model, "ollama_url": backend.base_url, "keep_alive": backend.keep_alive,
                **await backend.model_info(), **registry_version(backend.model)}

    @app.get("/v1/stats")
    async def stats() -> dict[str, Any]:
        out: dict[str, Any] = {"model": backend.model, "uptime_s": round(time.time() - started_at)}
        for endpoint in ("generate", "command"):
            rows = [r for r in recent if r[0] == endpoint]
            if rows:
                ok = [s for _, s, good in rows if good]
                out[endpoint] = {"requests": len(rows), "errors": len(rows) - len(ok), **_percentiles(ok)}
        return out

    @app.get("/metrics")
    async def prometheus() -> Response:
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    @app.get("/health")
    async def health() -> JSONResponse:
        info = await backend.model_info()
        if not info.get("available"):
            return JSONResponse({"status": "down", "error": info.get("error", "")}, status_code=503)
        return JSONResponse({"status": "ok", "model": backend.model})

    app.state.metrics = metrics
    app.state.backend = backend
    return app


def _jarvis_prompt(spoken: bool) -> str:
    """The system prompt Jarvis sends the home model (and the model was trained on) - see app.agent."""
    from app.agent import JarvisAgent
    from app.db import connect
    from app.memory import MemoryStore
    from app.tools.registry import ToolRegistry

    agent = JarvisAgent(llm=None, tools=ToolRegistry(), memory=MemoryStore(connect(":memory:")))
    return agent._build_system_prompt("default", spoken=spoken)
