"""Synthetic isolated tests: no server import, no external/production sockets."""
import asyncio
import hashlib
import io
import json
import logging
import os
import socket
import tempfile
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from webchat.auth import (COOKIE_NAME, CredentialStore, LoginRateLimiter, SafeAccessLogger,
                          SessionManager, json_object, load_credentials,
                          make_setup_handler, validate_client_message)

PASSWORD = "synthetic-test-only-password"


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class UnitTests(unittest.TestCase):
    def test_env_validation_and_safe_repr(self):
        env = {"WEB_PASSWORD": PASSWORD, "NAPCAT_ACCESS_TOKEN": "synthetic-napcat-only",
               "PUBLIC_BASE_URL": "https://chat.example.test", "TRUSTED_PROXIES": "127.0.0.1/32,::1/128"}
        config = load_credentials(env)
        self.assertNotIn(PASSWORD, repr(config))
        self.assertNotIn(env["NAPCAT_ACCESS_TOKEN"], repr(config))
        self.assertEqual(len(config.trusted_proxies), 2)
        self.assertEqual(load_credentials({**env, "WEB_PASSWORD": ""}).password, "")
        for changed in ({"NAPCAT_ACCESS_TOKEN": ""},
                        {"PUBLIC_BASE_URL": "http://chat.example.test"},
                        {"PUBLIC_BASE_URL": "https://chat.example.test/path"},
                        {"TRUSTED_PROXIES": "invalid"}, {"ADMIN_QQ_ID": "nonnumeric"},
                        {"WEB_USERNAME": "bad name"}):
            with self.assertRaises(ValueError):
                load_credentials({**env, **changed})

    def test_rate_limits_bounded_without_ip_eviction(self):
        clock = Clock()
        limiter = LoginRateLimiter(per_ip=2, global_limit=5, max_ips=2, window=10, clock=clock)
        self.assertTrue(limiter.allow("a"))
        self.assertTrue(limiter.allow("a"))
        self.assertFalse(limiter.allow("a"))
        self.assertTrue(limiter.allow("b"))
        self.assertFalse(limiter.allow("c"))
        self.assertFalse(limiter.allow("b"))
        self.assertEqual(len(limiter.ips), 2)
        self.assertEqual(len(limiter.global_attempts), 5)
        clock.value += 11
        self.assertTrue(limiter.allow("c"))
        self.assertEqual(len(limiter.ips), 1)

    def test_client_schema(self):
        self.assertEqual(validate_client_message({"type": "ping"}), {"type": "ping"})
        valid = {"type": "send_message", "target_id": "private:123", "request_id": "test-123", "content": "test"}
        self.assertIs(validate_client_message(valid), valid)
        for item in ([], None, {"type": "unknown"}, {"type": "get_history", "target_id": "123"},
                     {**valid, "request_id": []}, {**valid, "request_id": "x" * 129},
                     {**valid, "content": []}, {**valid, "quote": []},
                     {**valid, "file_is_image": "false"}, {**valid, "msg_type": "group"}):
            with self.assertRaises(web.HTTPBadRequest):
                validate_client_message(item)

    def test_proxy_identity_never_blindly_trusts_forwarding(self):
        transport = unittest.mock.Mock()
        transport.get_extra_info.side_effect = lambda name, default=None: ("127.0.0.1", 12345) if name == "peername" else default
        request = make_mocked_request("GET", "/", headers={"Host": "localhost", "X-Forwarded-For": "198.51.100.1, 192.0.2.2", "X-Forwarded-Proto": "https"}, transport=transport)
        direct = SessionManager(PASSWORD)
        self.assertEqual(direct.client_ip(request), "127.0.0.1")
        self.assertFalse(direct.secure_request(request))
        proxied = SessionManager(PASSWORD, trusted_proxies=("127.0.0.1/32",))
        self.assertEqual(proxied.client_ip(request), "192.0.2.2")
        self.assertTrue(proxied.secure_request(request))
        proxied2 = SessionManager(PASSWORD, trusted_proxies=("127.0.0.1/32", "192.0.2.0/24"))
        self.assertEqual(proxied2.client_ip(request), "198.51.100.1")
        self.assertFalse(proxied.secure_request(request.clone(headers={"X-Forwarded-Proto": "https,http"})))

    def test_origin_and_public_https(self):
        transport = unittest.mock.Mock()
        transport.get_extra_info.side_effect = lambda name, default=None: ("127.0.0.1", 12345) if name == "peername" else default
        manager = SessionManager(PASSWORD, public_base_url="https://chat.example.test", trusted_proxies=("127.0.0.1/32",))
        req = make_mocked_request("POST", "/", headers={"Host": "localhost", "Origin": "https://chat.example.test:443", "X-Forwarded-Proto": "https"}, transport=transport)
        manager.check_transport(req)
        manager.check_origin(req, required=True)
        for origin in ("null", "http://chat.example.test", "https://evil.example.test", "https://chat.example.test/path"):
            with self.assertRaises(web.HTTPForbidden):
                manager.check_origin(req.clone(headers={"Origin": origin}))
        with self.assertRaises(web.HTTPForbidden):
            manager.check_transport(req.clone(headers={"Host": "localhost"}))
        with self.assertRaises(web.HTTPForbidden):
            manager.check_origin(req.clone(headers={"Host": "localhost"}), required=True)


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = Clock()
        self.manager = SessionManager(PASSWORD, clock=self.clock, absolute_ttl=100, idle_ttl=20,
                                      global_login_limit=100, per_ip_limit=50)
        self.app = web.Application()
        self.manager.install(self.app)
        async def protected(request):
            self.manager.require(request)
            return web.json_response({"ok": True})
        self.app.router.add_get("/protected", protected)
        self.app.router.add_post("/protected", protected)
        async def schema(request):
            return web.json_response(await json_object(request, 64))
        self.app.router.add_post("/schema", schema)
        async def ws_handler(request):
            session = self.manager.require(request, websocket=True)
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            if not self.manager.bind(session, ws, lambda: ws.close(code=1008, message=b"session ended")):
                await ws.close()
                return ws
            try:
                async for message in ws:
                    if not self.manager.is_active(session, touch=True):
                        break
                    await ws.send_str("ack")
            finally:
                self.manager.unbind(session, ws)
            return ws
        self.app.router.add_get("/client_ws", ws_handler)
        self.log_output = io.StringIO()
        logger = logging.getLogger("auth-tests-access")
        logger.handlers = [logging.StreamHandler(self.log_output)]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        server = TestServer(self.app)
        await server.start_server(access_log=logger, access_log_class=SafeAccessLogger)
        self.client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
        await self.client.start_server()
        self.origin = str(self.client.make_url("/")).rstrip("/")
        allowed_port = self.client.server.port
        original_connect = socket.socket.connect
        def guarded_connect(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                if address[0] not in ("127.0.0.1", "::1") or address[1] != allowed_port or address[1] == 4001:
                    raise AssertionError("test blocked unexpected network destination")
            return original_connect(sock, address)
        self.guard = patch.object(socket.socket, "connect", guarded_connect)
        self.guard.start()

    async def asyncTearDown(self):
        await self.client.close()
        self.guard.stop()

    async def login(self):
        response = await self.client.post("/api/login", json={"password": PASSWORD}, headers={"Origin": self.origin})
        self.assertEqual(response.status, 200)
        return await response.json(), response

    async def test_login_cookie_session_and_hash_disabled(self):
        payload, response = await self.login()
        self.assertGreaterEqual(len(payload["token"]), 43)
        self.assertTrue(response.cookies[COOKIE_NAME]["httponly"])
        self.assertEqual(response.cookies[COOKIE_NAME]["samesite"], "Strict")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        status = await self.client.get("/api/session")
        body = await status.json();
        self.assertTrue(body["authenticated"]);
        self.assertTrue(isinstance(body.get("ws_token"), str) and len(body["ws_token"]) >= 20)
        response = await self.client.post("/api/login", json={"password_hash": hashlib.sha256(PASSWORD.encode()).hexdigest()})
        self.assertEqual(response.status, 401)
        second, _ = await self.login()
        self.assertNotEqual(payload["token"], second["token"])

    async def test_cookie_origin_and_bearer_compatibility(self):
        payload, _ = await self.login()
        self.assertEqual((await self.client.post("/protected", json={})).status, 403)
        self.assertEqual((await self.client.post("/protected", json={}, headers={"Origin": "https://evil.example.test"})).status, 403)
        self.assertEqual((await self.client.post("/protected", json={}, headers={"Origin": self.origin})).status, 200)
        self.assertEqual((await self.client.post("/protected", json={}, headers={"Authorization": "Bearer " + payload["token"]})).status, 200)
        self.client.session.cookie_jar.clear()
        self.assertEqual((await self.client.get("/protected?token=" + payload["token"])).status, 401)
        self.assertEqual((await self.client.get("/protected", headers={"Authorization": "Bearer " + payload["token"]})).status, 200)
        self.assertEqual((await self.client.get("/protected", headers={"Authorization": "Bearer " + payload["token"], "Origin": "https://evil.example.test"})).status, 403)

    async def test_logout_revokes_websocket_and_bridge_resource(self):
        payload, _ = await self.login()
        session = self.manager.sessions[payload["token"]]
        bridge_closed = asyncio.Event()
        self.assertTrue(self.manager.bind(session, "bridge-synthetic", bridge_closed.set))
        ws = await self.client.ws_connect("/client_ws", headers={"Origin": self.origin})
        await ws.send_str("ping")
        self.assertEqual((await ws.receive()).data, "ack")
        response = await self.client.post("/api/logout", json={}, headers={"Origin": self.origin})
        self.assertEqual(response.status, 200)
        self.assertTrue(bridge_closed.is_set())
        self.assertFalse(self.manager.is_active(session))
        self.assertEqual((await ws.receive()).type, aiohttp.WSMsgType.CLOSE)
        self.assertEqual((await self.client.get("/api/session")).status, 401)
        await ws.close()

    async def test_ws_origin_required_but_native_bearer_allowed(self):
        payload, _ = await self.login()
        for headers in ({}, {"Origin": "https://evil.example.test"}):
            with self.assertRaises(aiohttp.WSServerHandshakeError) as error:
                await self.client.ws_connect("/client_ws", headers=headers)
            self.assertEqual(error.exception.status, 403)
        self.client.session.cookie_jar.clear()
        ws = await self.client.ws_connect("/client_ws", headers={"Authorization": "Bearer " + payload["token"]})
        await ws.close()

    async def test_expiry_idle_absolute_and_resource_close(self):
        first = await self.manager.issue()
        closed = asyncio.Event()
        self.manager.bind(first, "resource", closed.set)
        self.clock.value += 20
        self.assertFalse(self.manager.is_active(first, touch=True))
        await self.manager.sweep()
        self.assertTrue(closed.is_set())
        second = await self.manager.issue()
        for _ in range(9):
            self.clock.value += 10
            self.assertTrue(self.manager.is_active(second, touch=True))
        self.clock.value += 10
        self.assertFalse(self.manager.is_active(second, touch=True))
        await self.manager.sweep()
        self.assertEqual(len(self.manager.sessions), 0)

    async def test_expiry_closes_established_ws_and_bridge(self):
        payload, _ = await self.login()
        session = self.manager.sessions[payload["token"]]
        closed = asyncio.Event()
        self.manager.bind(session, "synthetic-bridge", closed.set)
        ws = await self.client.ws_connect("/client_ws", headers={"Origin": self.origin})
        self.clock.value += 21
        await self.manager.sweep()
        self.assertTrue(closed.is_set())
        self.assertEqual((await ws.receive()).type, aiohttp.WSMsgType.CLOSE)
        response = await self.client.get("/protected")
        self.assertEqual(response.status, 401)
        await ws.close()

    async def test_public_https_cookie_requires_trusted_proxy(self):
        self.manager.public_base_url = "https://chat.example.test"
        self.manager.public_origin = ("https", "chat.example.test", 443)
        headers = {"Host": "chat.example.test", "Origin": "https://chat.example.test", "X-Forwarded-Proto": "https"}
        response = await self.client.post("/api/login", json={"password": PASSWORD}, headers=headers)
        self.assertEqual(response.status, 403)
        import ipaddress
        self.manager.trusted_proxies = (ipaddress.ip_network("127.0.0.1/32"),)
        response = await self.client.post("/api/login", json={"password": PASSWORD}, headers=headers)
        self.assertEqual(response.status, 200)
        self.assertTrue(response.cookies[COOKIE_NAME]["secure"])

    async def test_capacity_resources_and_cancelled_revocation(self):
        manager = SessionManager(PASSWORD, max_sessions=1, max_resources_per_session=1)
        session = await manager.issue()
        entered, release = asyncio.Event(), asyncio.Event()
        async def closer():
            entered.set()
            await release.wait()
        self.assertTrue(manager.bind(session, "a", closer))
        self.assertFalse(manager.bind(session, "b", closer))
        with self.assertRaises(web.HTTPServiceUnavailable):
            await manager.issue()
        revoke = asyncio.create_task(manager.revoke(session))
        await entered.wait()
        revoke.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await revoke
        self.assertFalse(manager.is_active(session))
        with self.assertRaises(web.HTTPServiceUnavailable):
            await manager.issue()
        release.set()
        await manager.close()
        self.assertEqual(len(manager.sessions), 0)

    async def test_callback_self_cancellation_does_not_release_capacity_early(self):
        manager = SessionManager(PASSWORD, max_sessions=1)
        session = await manager.issue()
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def cancelled():
            await entered.wait()
            raise asyncio.CancelledError
        async def sibling():
            entered.set()
            await release.wait()
            finished.set()
        manager.bind(session, "cancelled", cancelled)
        manager.bind(session, "sibling", sibling)
        revoke = asyncio.create_task(manager.revoke(session))
        await entered.wait()
        try:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertFalse(revoke.done())
            self.assertFalse(finished.is_set())
            self.assertEqual(len(manager.sessions), 1)
            with self.assertRaises(web.HTTPServiceUnavailable):
                await manager.issue()
        finally:
            release.set()
            await revoke
            await manager.close()
        self.assertTrue(finished.is_set())
        self.assertEqual(len(manager.sessions), 0)

    async def test_periodic_sweeper_survives_callback_self_cancellation(self):
        manager = SessionManager(PASSWORD, clock=self.clock, idle_ttl=1, sweep_interval=0.001)
        context = manager.cleanup_context(None)
        await anext(context)
        closed = asyncio.Event()
        async def cancelled():
            closed.set()
            raise asyncio.CancelledError
        first = await manager.issue()
        manager.bind(first, "cancelled", cancelled)
        self.clock.value += 2
        try:
            await asyncio.wait_for(closed.wait(), 1)
            # issue() itself sweeps, so use close completion before registering
            # the next session to ensure the periodic worker alone closes it.
            await manager.revoke(first)
            second = await manager.issue()
            second_closed = asyncio.Event()
            manager.bind(second, "second", second_closed.set)
            self.clock.value += 2
            await asyncio.wait_for(second_closed.wait(), 1)
        finally:
            await context.aclose()

    async def test_restore_cookie_from_legacy_body_but_no_cross_origin(self):
        payload, _ = await self.login()
        self.client.session.cookie_jar.clear()
        response = await self.client.post("/api/restore", json={"token": payload["token"]}, headers={"Origin": "https://evil.example.test"})
        self.assertEqual(response.status, 403)
        response = await self.client.post("/api/restore", json={"token": payload["token"]}, headers={"Origin": self.origin})
        self.assertEqual(response.status, 200)
        self.assertIn(COOKIE_NAME, response.cookies)
        self.assertNotIn("token", await response.json())

    async def test_bad_json_shapes_and_request_limits(self):
        for body in ([], None, {"password": []}, {"password_hash": []}):
            response = await self.client.post("/api/login", data=json.dumps(body), headers={"Content-Type": "application/json"})
            self.assertEqual(response.status, 400)
        for body in ("{", "[", "null"):
            response = await self.client.post("/api/login", data=body, headers={"Content-Type": "application/json"})
            self.assertEqual(response.status, 400)
        response = await self.client.post("/api/login", data='{"password":"test"}', headers={"Content-Type": "text/plain"})
        self.assertEqual(response.status, 400)
        response = await self.client.post("/schema", json={"a": "x" * 100})
        self.assertEqual(response.status, 413)
        async def chunks():
            yield b'{"a":"'
            yield b'x' * 100
            yield b'"}'
        response = await self.client.post("/schema", data=chunks(), headers={"Content-Type": "application/json"})
        self.assertEqual(response.status, 413)
        response = await self.client.post("/api/restore", json={"token": []})
        self.assertEqual(response.status, 400)

    async def test_login_rate_limit(self):
        self.manager.limiter = LoginRateLimiter(per_ip=2, global_limit=3)
        for _ in range(2):
            response = await self.client.post("/api/login", json={"password": "synthetic-wrong"})
            self.assertEqual(response.status, 401)
        response = await self.client.post("/api/login", json={"password": PASSWORD})
        self.assertEqual(response.status, 429)
        self.assertIn("Retry-After", response.headers)

    async def test_access_logger_never_records_credentials_or_unknown_paths(self):
        await self.client.get("/protected?token=synthetic-url-secret&access_token=synthetic-other-secret", headers={"Authorization": "Bearer synthetic-header-secret", "Cookie": "webchat_token=synthetic-cookie-secret", "Referer": "https://example.test/?token=synthetic-ref-secret"})
        await self.client.get("/synthetic-path-secret")
        text = self.log_output.getvalue()
        for value in ("synthetic-url-secret", "synthetic-other-secret", "synthetic-header-secret", "synthetic-cookie-secret", "synthetic-ref-secret", "synthetic-path-secret"):
            self.assertNotIn(value, text)
        self.assertIn("GET /protected 401", text)
        self.assertIn("<unmatched>", text)


class CredentialStoreTests(unittest.TestCase):
    def test_atomic_roundtrip_and_owner_only_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "credentials.json")
            store = CredentialStore(path)
            self.assertIsNone(store.load())
            stored = store.save("admin", "synthetic-pass-123")
            self.assertEqual(stored.username, "admin")
            self.assertRegex(stored.password_hash, r"^[0-9a-f]{64}$")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            loaded = store.load()
            self.assertEqual((loaded.username, loaded.password_hash),
                             (stored.username, stored.password_hash))
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("not json")
            with self.assertRaises(ValueError):
                store.load()


class SetupFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = CredentialStore(os.path.join(self.temp.name, "credentials.json"))
        self.manager = SessionManager(setup_token="synthetic-setup-token",
                                      global_login_limit=100, per_ip_limit=50)
        app = web.Application()
        self.manager.install(app)
        app.router.add_post("/api/setup", make_setup_handler(self.manager, self.store))
        self.client = TestClient(TestServer(app), cookie_jar=aiohttp.CookieJar(unsafe=True))
        await self.client.start_server()
        self.origin = str(self.client.make_url("/")).rstrip("/")

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_setup_creates_account_then_normal_login_is_username_bound(self):
        response = await self.client.get("/api/session")
        self.assertEqual(response.status, 401)
        body = await response.json()
        self.assertTrue(body["setup_required"])
        response = await self.client.post("/api/setup", json={"token": "wrong", "username": "admin",
                                                              "password": "synthetic-pass-123"},
                                          headers={"Origin": self.origin})
        self.assertEqual(response.status, 403)
        response = await self.client.post("/api/setup", json={"token": "synthetic-setup-token",
                                                              "username": "admin", "password": "short"},
                                          headers={"Origin": self.origin})
        self.assertEqual(response.status, 400)
        response = await self.client.post("/api/setup", json={"token": "synthetic-setup-token",
                                                              "username": "admin",
                                                              "password": "synthetic-pass-123"},
                                          headers={"Origin": self.origin})
        self.assertEqual(response.status, 200)
        self.assertIn(COOKIE_NAME, response.cookies)
        self.assertTrue(self.manager.initialized)
        body = await (await self.client.get("/api/session")).json()
        self.assertTrue(body["authenticated"])
        response = await self.client.post("/api/setup", json={"token": "synthetic-setup-token",
                                                              "username": "other",
                                                              "password": "synthetic-pass-456"},
                                          headers={"Origin": self.origin})
        self.assertEqual(response.status, 409)
        stored = self.store.load()
        self.assertEqual(stored.username, "admin")
        await self.client.post("/api/logout", json={}, headers={"Origin": self.origin})
        response = await self.client.post("/api/login", json={"username": "admin",
                                                              "password": "synthetic-pass-123"},
                                          headers={"Origin": self.origin})
        self.assertEqual(response.status, 200)
        response = await self.client.post("/api/login", json={"username": "other",
                                                              "password": "synthetic-pass-123"},
                                          headers={"Origin": self.origin})
        self.assertEqual(response.status, 401)
        response = await self.client.post("/api/login", json={"password": "synthetic-pass-123"},
                                          headers={"Origin": self.origin})
        self.assertEqual(response.status, 401)

    async def test_setup_without_configured_token_is_rejected(self):
        manager = SessionManager()
        store = CredentialStore(os.path.join(self.temp.name, "other.json"))
        app = web.Application()
        app.router.add_post("/api/setup", make_setup_handler(manager, store))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.post("/api/setup", json={"token": "anything", "username": "admin",
                                                             "password": "synthetic-pass-123"},
                                         headers={"Origin": str(client.make_url("/")).rstrip("/")})
            self.assertEqual(response.status, 403)
            self.assertFalse(manager.initialized)
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
