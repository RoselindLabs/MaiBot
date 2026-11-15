"""N19: original Host origin boundary, real cookies and in-process ASGI only."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import httpx
import pytest

from fastapi import HTTPException, Request, WebSocket

from src.host.authentication import HostCredentials
from src.host.config import HostConfig
from src.host.layout import Workspace
from src.host.maintenance import HostMaintenance
from src.host.proxy import BotProxy, _request_headers, same_webui_origin
from src.host.registry import Registry
from src.host.sessions import HostSessions
from src.host.supervisor import BotSupervisor
from src.host.webui import create_host_app


@pytest.mark.parametrize(
    "origin,site,mode,login,expected",
    [
        (None, "cross-site", "cors", None, True),
        ("http://localhost:7999", "cross-site", "cors", None, True),
        ("https://pet.example", None, None, None, True),
        ("https://other.example", None, None, None, False),
        ("https://pet.example/path", None, None, None, False),
        ("https://user@pet.example", None, None, None, False),
        ("https://pet.example:invalid", None, None, None, False),
        ("https://pet.example", "same-site", "cors", None, False),
        ("https://pet.example:8446", "same-origin", "cors", None, True),
        ("https://pet.example:8446", None, "websocket", "https://pet.example:8446", True),
        ("https://pet.example:8447", None, "websocket", "https://pet.example:8446", False),
        ("https://pet.example", None, "websocket", "https://pet.example:8446", False),
    ],
)
def test_original_origin_rules(origin, site, mode, login, expected):
    headers = {"host": "pet.example"}
    for key, value in [("origin", origin), ("sec-fetch-site", site), ("sec-fetch-mode", mode)]:
        if value is not None:
            headers[key] = value
    assert same_webui_origin(headers, login) is expected


@pytest.fixture
def host(tmp_path):
    registry = Registry(tmp_path)
    registry.create()
    config = HostConfig()
    workspace = Workspace(root=tmp_path, registry=registry, config=config)
    supervisor = BotSupervisor(workspace, public_base_url="https://pet.example:8446")
    credentials = HostCredentials(tmp_path / "host" / "webui.json")
    sessions = HostSessions(tmp_path, credentials=credentials)
    app = create_host_app(
        supervisor,
        HostMaintenance(supervisor),
        sessions,
        credentials,
        asyncio.Event(),
        config=config,
    )
    return SimpleNamespace(app=app, credentials=credentials, sessions=sessions, supervisor=supervisor)


async def test_host_login_write_header_and_reverse_proxy_origin_contract(host):
    origin = "https://pet.example:8446"
    transport = httpx.ASGITransport(app=host.app)
    async with httpx.AsyncClient(transport=transport, base_url="https://pet.example") as client:
        body = {"token": host.credentials.token}
        for site in ["same-site", "cross-site"]:
            response = await client.post(
                "/api/webui/auth/verify",
                json=body,
                headers={"Origin": origin, "Sec-Fetch-Site": site},
            )
            assert response.status_code == 403
        response = await client.post(
            "/api/webui/auth/verify",
            json=body,
            headers={"Origin": origin, "Sec-Fetch-Site": "same-origin"},
        )
        assert response.status_code == 200 and response.json()["valid"] is True
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=28800" in cookie
        assert host.sessions.valid(client.cookies.get(host.sessions.cookie_name))
        assert (await client.get("/api/host/session")).status_code == 200
        response = await client.get("/api/host/desktop-pet/session")
        assert "wss://pet.example:8446" in response.headers["content-security-policy"]
        # A write must retain BOTH the origin boundary and explicit Host marker.
        assert (await client.delete("/api/host/session", headers={"Origin": origin})).status_code == 403
        for other in ["https://other.example", "https://pet.example:8447", "https://pet.example"]:
            headers = {"Origin": other, "X-MaiBot-Host": "1"}
            assert (await client.delete("/api/host/session", headers=headers)).status_code == 403
        response = await client.delete("/api/host/session", headers={"Origin": origin, "X-MaiBot-Host": "1"})
        assert response.status_code == 200
        assert (await client.get("/api/host/session")).status_code == 401


@pytest.mark.parametrize("kind", ["http", "websocket"])
def test_proxy_checks_cookie_and_origin_before_worker_access(host, kind):
    session = host.sessions.create_session("https://pet.example:8446")
    proxy = BotProxy(host.supervisor, host.sessions)
    headers = {"host": "pet.example", "cookie": f"{host.sessions.cookie_name}={session}"}

    def connection(origin):
        raw = {**headers, "origin": origin}
        scope = {"type": kind, "headers": [(key.encode(), value.encode()) for key, value in raw.items()]}
        return Request(scope) if kind == "http" else WebSocket(scope, AsyncMock(), AsyncMock())

    proxy._require_session(connection("https://pet.example:8446"))
    with pytest.raises(HTTPException) as error:
        proxy._require_session(connection("https://other.example"))
    assert error.value.status_code == 403
    host.sessions.revoke(session)
    with pytest.raises(HTTPException) as error:
        proxy._require_session(connection("https://pet.example:8446"))
    assert error.value.status_code == 401
    assert _request_headers({**headers, "authorization": "synthetic", "origin": "https://pet.example"}) == {}


async def test_unavailable_management_keeps_origin_boundary(host):
    app = create_host_app(
        host.supervisor,
        HostMaintenance(host.supervisor),
        None,
        None,
        asyncio.Event(),
        config=HostConfig(),
        management_error="synthetic unavailable",
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://pet.example") as client:
        assert (await client.get("/api/host/mode")).status_code == 503
        response = await client.post("/api/webui/auth/verify", headers={"Origin": "https://other.example"})
        assert response.status_code == 403
