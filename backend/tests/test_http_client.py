import asyncio

from app.http_client import shared_client, ssl_context


def test_ssl_context_is_loaded_once():
    assert ssl_context() is ssl_context()


def test_one_client_per_event_loop_reused_within_it():
    async def two_calls():
        return shared_client(), shared_client()

    a1, a2 = asyncio.run(two_calls())
    b1, _ = asyncio.run(two_calls())

    assert a1 is a2  # same loop -> same client (connection reuse)
    assert a1 is not b1  # a client never leaks into another loop


def test_a_closed_client_is_replaced():
    async def run():
        first = shared_client()
        await first.aclose()
        return first, shared_client()

    first, second = asyncio.run(run())
    assert second is not first and not second.is_closed
