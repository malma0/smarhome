"""Shared HTTPS setup for Jarvis's own HTTP calls (Whisper, Groq, Voicebox).

Measured on this laptop: creating an httpx.AsyncClient with default
settings takes ~1.8-2.1 s, because each one loads the whole certificate
bundle again. voice_app.transcribe used to create a client per phrase, so a
Whisper call cost 2.0-2.4 s end to end - vs 0.5-1.0 s with the certificates
loaded once, and 0.23-0.31 s reusing one connection (keep-alive skips the
TLS handshake too).

ssl_context(): the certificate context, loaded once for the whole process.
shared_client(): one client per event loop - an httpx.AsyncClient belongs
to the loop it first ran on, and Jarvis runs more than one (the voice loop,
the Voicebox warm-up thread, each test).
"""

import asyncio
import ssl
import threading
import weakref

import certifi
import httpx

_lock = threading.Lock()
_ssl_context: ssl.SSLContext | None = None
_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = weakref.WeakKeyDictionary()


def ssl_context() -> ssl.SSLContext:
    global _ssl_context
    with _lock:
        if _ssl_context is None:
            _ssl_context = ssl.create_default_context(cafile=certifi.where())
        return _ssl_context


def shared_client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=60, verify=ssl_context())
        _clients[loop] = client
    return client
