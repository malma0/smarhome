"""With a VPN's system proxy on, local services must still be reached directly."""

import asyncio

from app.http_client import is_local, shared_client, trust_env


def test_local_addresses_are_recognized():
    for url in ("http://localhost:8123", "http://127.0.0.1:5000", "http://192.168.0.234:11434",
                "http://10.0.0.5", "http://172.20.1.1", "http://jarvis.local"):
        assert is_local(url) and not trust_env(url), url
    for url in ("https://api.groq.com/openai/v1", "https://api.open-meteo.com", "http://8.8.8.8"):
        assert not is_local(url) and trust_env(url), url


def test_a_local_url_gets_a_client_that_ignores_proxies():
    async def clients():
        return shared_client("http://localhost:8123"), shared_client(), shared_client("https://api.groq.com")

    local, default, remote = asyncio.run(clients())
    assert local is not default and default is remote
    assert local._trust_env is False and default._trust_env is True
