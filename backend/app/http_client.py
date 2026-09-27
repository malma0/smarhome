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
import ipaddress
import ssl
import threading
import weakref

import certifi
import httpx

_lock = threading.Lock()
_ssl_context: ssl.SSLContext | None = None
_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[bool, httpx.AsyncClient]]" = (
    weakref.WeakKeyDictionary()
)


def ssl_context() -> ssl.SSLContext:
    global _ssl_context
    with _lock:
        if _ssl_context is None:
            _ssl_context = ssl.create_default_context(cafile=certifi.where())
        return _ssl_context


def is_local(url: str) -> bool:
    """localhost, 127.x, 192.168.x, 10.x, 172.16-31.x, *.local - this machine or the home network."""
    host = httpx.URL(url).host
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def trust_env(url: str) -> bool:
    """Whether a client for url may take proxy settings from the system. Not
    for local addresses: with a VPN on, httpx takes the Windows system proxy
    from the registry but not its bypass list, so Home Assistant on localhost
    went through the VPN and got 503."""
    return not is_local(url)


def shared_client(url: str | None = None) -> httpx.AsyncClient:
    """url: where the requests go - a local one gets a client that never uses a proxy."""
    loop = asyncio.get_running_loop()
    direct = url is not None and is_local(url)
    clients = _clients.setdefault(loop, {})
    client = clients.get(direct)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=60, verify=ssl_context(), trust_env=not direct)
        clients[direct] = client
    return client
