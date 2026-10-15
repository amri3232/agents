import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from utils import ApiError, HttpClient, supervise
import asyncio


async def make_server(counter):
    async def flaky_429(request):
        counter["429"] = counter.get("429", 0) + 1
        if counter["429"] < 3:
            return web.json_response({"error": "rate"}, status=429, headers={"Retry-After": "0"})
        return web.json_response({"ok": True})

    async def always_500(request):
        counter["500"] = counter.get("500", 0) + 1
        return web.Response(status=503, text="down")

    async def missing(request):
        counter["404"] = counter.get("404", 0) + 1
        return web.Response(status=404, text="nope")

    async def bad_json(request):
        return web.Response(text="<html>")

    async def ok(request):
        return web.json_response([{"a": 1}])

    app = web.Application()
    app.add_routes([web.get("/429", flaky_429), web.get("/500", always_500), web.get("/404", missing),
                    web.get("/badjson", bad_json), web.get("/ok", ok)])
    server = TestServer(app)
    await server.start_server()
    return server


async def test_retries_on_429_then_succeeds_and_notifies():
    counter, events = {}, []
    server = await make_server(counter)
    async with aiohttp.ClientSession() as session:
        http = HttpClient(session, retries=3)
        http.observers.append(lambda name, ok, ms: events.append((name, ok)))
        data = await http.get_json(str(server.make_url("/429")), name="t")
    await server.close()
    assert data == {"ok": True} and counter["429"] == 3
    assert events.count(("t", False)) == 2 and events[-1] == ("t", True)


async def test_gives_up_after_retries_on_5xx():
    counter = {}
    server = await make_server(counter)
    async with aiohttp.ClientSession() as session:
        http = HttpClient(session, retries=2)
        with pytest.raises(ApiError) as ei:
            await http.get_json(str(server.make_url("/500")), name="t")
    await server.close()
    assert counter["500"] == 3 and ei.value.status == 503


async def test_4xx_is_not_retried():
    counter = {}
    server = await make_server(counter)
    async with aiohttp.ClientSession() as session:
        with pytest.raises(ApiError) as ei:
            await HttpClient(session, retries=3).get_json(str(server.make_url("/404")))
    await server.close()
    assert counter["404"] == 1 and ei.value.status == 404


async def test_invalid_json_and_connection_errors_raise_apierror():
    server = await make_server({})
    url_bad, url_ok = str(server.make_url("/badjson")), str(server.make_url("/ok"))
    async with aiohttp.ClientSession() as session:
        http = HttpClient(session, retries=1)
        with pytest.raises(ApiError):
            await http.get_json(url_bad)
        assert (await http.get_json(url_ok)) == [{"a": 1}]
    await server.close()
    async with aiohttp.ClientSession() as session:       # Server weg -> Verbindungsfehler
        with pytest.raises(ApiError):
            await HttpClient(session, retries=1).get_json(url_ok, timeout=2)


async def test_supervise_restarts_crashing_task():
    stop, runs = asyncio.Event(), []

    async def flaky():
        runs.append(1)
        if len(runs) < 3:
            raise RuntimeError("boom")
        stop.set()

    task = asyncio.create_task(supervise("flaky", flaky, stop))
    await asyncio.wait_for(task, timeout=10)
    assert len(runs) == 3
