"""Bounded in-memory administrator sessions and aiohttp authentication helpers.

Integration: install() owns the four /api auth routes and a cleanup context.
Every WS/bridge must bind a close callback and check is_active() before actions;
bridge poll/send must additionally compare the authenticated session identity.
No query-string credential is accepted. Browser clients should use only cookies.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
import hashlib
import inspect
import ipaddress
import json
import logging
import os
import re
import secrets
import tempfile
import time
from typing import Mapping
from urllib.parse import urlsplit

from aiohttp import web
from aiohttp.abc import AbstractAccessLogger

COOKIE_NAME = "webchat_token"
SAFE_METHODS = frozenset(("GET", "HEAD", "OPTIONS"))


def _error(status, message):
    return {
        400: web.HTTPBadRequest, 401: web.HTTPUnauthorized,
        403: web.HTTPForbidden,
        429: web.HTTPTooManyRequests, 503: web.HTTPServiceUnavailable,
    }[status](text=json.dumps({"error": message}), content_type="application/json",
              headers={"Cache-Control": "no-store"})


def bounded_string(value, name, *, maximum=4096, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or len(value) > maximum or (required and not value):
        raise _error(400, "invalid " + name)
    return value


async def json_object(request, max_bytes=65536, read_timeout=10):
    """Read a small JSON object, including chunked bodies, without large buffering."""
    if request.content_length is not None and request.content_length > max_bytes:
        raise web.HTTPRequestEntityTooLarge(max_size=max_bytes, actual_size=request.content_length)
    if request.content_type != "application/json":
        raise _error(400, "application/json required")
    body = bytearray()
    try:
        async with asyncio.timeout(read_timeout):
            async for chunk in request.content.iter_chunked(min(8192, max_bytes + 1)):
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise web.HTTPRequestEntityTooLarge(max_size=max_bytes, actual_size=len(body))
    except TimeoutError:
        response = web.HTTPRequestTimeout(text='JSON body read timed out')
        response.force_close()
        raise response from None
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise _error(400, "invalid JSON") from None
    if not isinstance(data, dict):
        raise _error(400, "JSON object required")
    return data


def validate_client_message(data):
    """Basic shape checks; routing/ACL and attachment ownership remain server duties."""
    if not isinstance(data, dict):
        raise _error(400, "JSON object required")
    action = bounded_string(data.get("type"), "type", maximum=64, required=True)
    if action == "ping":
        return data
    if action not in ("get_history", "send_message", "recall_message"):
        raise _error(400, "unknown action")
    target = bounded_string(data.get("target_id"), "target_id", maximum=128, required=True)
    if not re.fullmatch(r"(?:private|group):[1-9][0-9]{0,19}", target):
        raise _error(400, "typed target_id required")
    for key, limit in (("content", 65536), ("request_id", 128), ("message_id", 128),
                       ("file_local_path", 4096), ("file_orig_name", 512), ("msg_type", 16)):
        if key in data:
            bounded_string(data[key], key, maximum=limit)
    if action == "send_message":
        request_id = bounded_string(data.get("request_id"), "request_id", maximum=128, required=True)
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", request_id):
            raise _error(400, "invalid request_id")
        if data.get("msg_type") not in (None, target.split(":", 1)[0]):
            raise _error(400, "conflicting msg_type")
    if action == "recall_message":
        bounded_string(data.get("message_id"), "message_id", maximum=128, required=True)
    if "file_is_image" in data and not isinstance(data["file_is_image"], bool):
        raise _error(400, "invalid file_is_image")
    quote = data.get("quote")
    if quote is not None:
        if not isinstance(quote, dict):
            raise _error(400, "invalid quote")
        for key in ("id", "napcat_id", "sender_name", "content"):
            if key in quote:
                bounded_string(quote[key], "quote." + key, maximum=65536 if key == "content" else 256)
    return data


def _origin(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                parsed.query or parsed.fragment or parsed.path not in ("", "/") or
                any(c.isspace() for c in value) or "\\" in value):
            raise ValueError
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except (ValueError, TypeError):
        raise ValueError("invalid origin") from None


def _loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]{3,32}")


@dataclass(frozen=True)
class Credentials:
    password: str = field(repr=False)
    napcat_token: str = field(repr=False)
    admin_qq_id: str = ""
    public_base_url: str = ""
    trusted_proxies: tuple = ()
    username: str = ""


def validate_new_password(password):
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise ValueError("password must be 8-128 characters")


def load_credentials(env: Mapping | None = None):
    """Load environment only; never load a repository .env or disclose values.

    An empty WEB_PASSWORD is allowed for a fresh install: the web UI then asks
    the first visitor to create an account, guarded by SETUP_TOKEN.
    """
    env = os.environ if env is None else env
    password, token = env.get("WEB_PASSWORD", ""), env.get("NAPCAT_ACCESS_TOKEN", "")
    if not isinstance(password, str) or len(password) > 4096 or (password and not password.strip()):
        raise ValueError("WEB_PASSWORD must be empty or a bounded string")
    if not isinstance(token, str) or not token.strip() or len(token) > 4096:
        raise ValueError("NAPCAT_ACCESS_TOKEN must be a nonempty bounded string")
    username = env.get("WEB_USERNAME", "")
    if username and not USERNAME_PATTERN.fullmatch(username):
        raise ValueError("WEB_USERNAME must be 3-32 letters, digits, dot, dash or underscore")
    admin = env.get("ADMIN_QQ_ID", "")
    if admin and not re.fullmatch(r"[1-9][0-9]{0,19}", admin):
        raise ValueError("ADMIN_QQ_ID must be a numeric identifier")
    public = env.get("PUBLIC_BASE_URL", "").rstrip("/")
    if public:
        scheme, host, _ = _origin(public)
        if scheme != "https" and not _loopback(host):
            raise ValueError("PUBLIC_BASE_URL requires HTTPS except on localhost")
    proxies = tuple(x.strip() for x in env.get("TRUSTED_PROXIES", "").split(",") if x.strip())
    for proxy in proxies:
        ipaddress.ip_network(proxy, strict=False)
    return Credentials(password, token, admin, public, proxies, username)


@dataclass(frozen=True)
class StoredCredentials:
    username: str
    password_hash: str
    created_at: float
    must_change: bool = False


class CredentialStore:
    """Atomic owner-only credential file; plaintext passwords are never stored."""

    def __init__(self, path):
        self.path = os.path.abspath(path)

    def load(self):
        try:
            with open(self.path, "rb") as handle:
                raw = handle.read(8192)
        except FileNotFoundError:
            return None
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise ValueError("credential file is invalid") from None
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("credential file is invalid")
        username, digest, created = data.get("username"), data.get("password_hash"), data.get("created_at")
        must_change = data.get("must_change", False)
        if not isinstance(username, str) or not USERNAME_PATTERN.fullmatch(username):
            raise ValueError("credential file is invalid")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("credential file is invalid")
        if not isinstance(created, (int, float)) or isinstance(created, bool) or created <= 0:
            raise ValueError("credential file is invalid")
        if not isinstance(must_change, bool):
            raise ValueError("credential file is invalid")
        return StoredCredentials(username, digest, float(created), must_change)

    def save(self, username, password, *, must_change=False):
        if not USERNAME_PATTERN.fullmatch(username or ""):
            raise ValueError("invalid username")
        digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
        payload = json.dumps({"version": 1, "username": username, "password_hash": digest,
                              "created_at": time.time(), "must_change": bool(must_change)}).encode("utf-8")
        directory = os.path.dirname(self.path)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".credentials-", dir=directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            dir_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        return StoredCredentials(username, digest, time.time(), bool(must_change))


@dataclass(eq=False)
class Session:
    token: str = field(repr=False)
    created_at: float
    last_seen: float
    resources: dict = field(default_factory=dict, repr=False)
    revoked: bool = False
    close_task: object = field(default=None, repr=False)


class LoginRateLimiter:
    """Sliding-window attempt limits; saturation rejects new IPs, never evicts protection."""
    def __init__(self, per_ip=10, global_limit=60, window=60, max_ips=1024, clock=time.monotonic):
        if min(per_ip, global_limit, window, max_ips) <= 0:
            raise ValueError("rate limits must be positive")
        self.per_ip, self.global_limit, self.window, self.max_ips = per_ip, global_limit, window, max_ips
        self.clock = clock
        self.ips = OrderedDict()
        self.global_attempts = deque()

    def allow(self, ip):
        now = self.clock()
        cutoff = now - self.window
        while self.global_attempts and self.global_attempts[0] <= cutoff:
            self.global_attempts.popleft()
        for key, attempts in list(self.ips.items()):
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if not attempts:
                del self.ips[key]
        if len(self.global_attempts) >= self.global_limit:
            return False
        # Count IP-limited requests globally too, bounding distributed work.
        self.global_attempts.append(now)
        attempts = self.ips.get(ip)
        if attempts is None:
            if len(self.ips) >= self.max_ips:
                return False
            attempts = self.ips[ip] = deque()
        if len(attempts) >= self.per_ip:
            return False
        attempts.append(now)
        return True


class SessionManager:
    def __init__(self, password="", *, username="", password_hash=None, setup_token="",
                 must_change_password=False, public_base_url="", trusted_proxies=(),
                 absolute_ttl=86400, idle_ttl=3600, max_sessions=128,
                 max_resources_per_session=16, per_ip_limit=10, global_login_limit=60,
                 login_window=60, max_login_ips=1024, sweep_interval=1,
                 close_timeout=2, allow_password_hash=False, clock=time.monotonic):
        if password and (not isinstance(password, str) or not password.strip() or len(password) > 4096):
            raise ValueError("bounded password required")
        if username and not USERNAME_PATTERN.fullmatch(username):
            raise ValueError("invalid username")
        if password_hash is not None and (not isinstance(password_hash, str) or
                                          not re.fullmatch(r"[0-9a-f]{64}", password_hash)):
            raise ValueError("invalid password hash")
        if min(absolute_ttl, idle_ttl, max_sessions, max_resources_per_session, sweep_interval, close_timeout) <= 0:
            raise ValueError("session limits must be positive")
        self._password_hash = password_hash or (hashlib.sha256(password.encode()).hexdigest() if password else None)
        self.username = username or ""
        self.setup_token = setup_token or ""
        self.must_change_password = bool(must_change_password) and self._password_hash is not None
        self.public_base_url = public_base_url.rstrip("/")
        self.public_origin = _origin(public_base_url) if public_base_url else None
        if self.public_origin and self.public_origin[0] != "https" and not _loopback(self.public_origin[1]):
            raise ValueError("PUBLIC_BASE_URL requires HTTPS except on localhost")
        self.trusted_proxies = tuple(ipaddress.ip_network(x, strict=False) for x in trusted_proxies)
        self.absolute_ttl, self.idle_ttl, self.max_sessions = absolute_ttl, idle_ttl, max_sessions
        self.max_resources_per_session = max_resources_per_session
        self.sweep_interval, self.close_timeout = sweep_interval, close_timeout
        self.allow_password_hash, self.clock = allow_password_hash, clock
        self.sessions = {}
        self.limiter = LoginRateLimiter(per_ip_limit, global_login_limit, login_window, max_login_ips, clock)

    @property
    def initialized(self):
        return self._password_hash is not None

    def verify_setup_token(self, token):
        return bool(self.setup_token and isinstance(token, str) and
                    secrets.compare_digest(token, self.setup_token))

    def set_credentials(self, username, *, password=None, password_hash=None, must_change_password=False):
        if not USERNAME_PATTERN.fullmatch(username or ""):
            raise ValueError("invalid username")
        if password_hash is None:
            validate_new_password(password)
            password_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        elif not re.fullmatch(r"[0-9a-f]{64}", password_hash or ""):
            raise ValueError("invalid password hash")
        self.username = username
        self._password_hash = password_hash
        self.must_change_password = bool(must_change_password)

    def check_password(self, password):
        if not self.initialized or not isinstance(password, str) or not password:
            return False
        return secrets.compare_digest(hashlib.sha256(password.encode()).hexdigest(), self._password_hash)

    def _trusted(self, value):
        try:
            ip = ipaddress.ip_address(value)
            return any(ip in network for network in self.trusted_proxies)
        except (ValueError, TypeError):
            return False

    def client_ip(self, request):
        peer = request.remote or "unknown"
        if not self._trusted(peer):
            return peer
        forwarded = request.headers.get("X-Forwarded-For", "")
        if not forwarded or len(forwarded) > 2048:
            return peer
        parts = [x.strip() for x in forwarded.split(",")]
        if len(parts) > 32:
            return peer
        try:
            for value in parts:
                ipaddress.ip_address(value)
        except ValueError:
            return peer
        # Strip only known proxy hops from the right, never trust leftmost blindly.
        current = peer
        for value in reversed(parts):
            if not self._trusted(current):
                break
            current = value
        return current

    def secure_request(self, request):
        return request.secure or (self._trusted(request.remote) and
                                  request.headers.get("X-Forwarded-Proto", "").lower() == "https")

    def expected_origin(self, request):
        if self.public_origin:
            return self.public_origin
        scheme = "https" if self.secure_request(request) else "http"
        try:
            return _origin(scheme + "://" + request.host)
        except ValueError:
            raise _error(403, "invalid request origin") from None

    def check_origin(self, request, *, required=False):
        origin = request.headers.get("Origin")
        if origin is not None:
            try:
                valid = _origin(origin) == self.expected_origin(request)
            except ValueError:
                valid = False
            if not valid:
                raise _error(403, "origin denied")
        elif required:
            raise _error(403, "origin required")
        if request.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
            raise _error(403, "cross-origin request denied")

    def check_transport(self, request):
        if self.secure_request(request):
            return
        expected = self.expected_origin(request)
        if expected[0] == "http" and _loopback(expected[1]) and _loopback(request.remote or ""):
            return
        raise _error(403, "HTTPS required")

    def is_active(self, session, touch=False):
        if not isinstance(session, Session) or session.revoked or self.sessions.get(session.token) is not session:
            return False
        now = self.clock()
        if now - session.created_at >= self.absolute_ttl or now - session.last_seen >= self.idle_ttl:
            return False
        if touch:
            session.last_seen = now
        return True

    def authenticate(self, request, *, websocket=False, touch=True):
        self.check_transport(request)
        authorization = request.headers.get("Authorization", "")
        cookie = request.cookies.get(COOKIE_NAME)
        if authorization:
            pieces = authorization.split(" ")
            if len(pieces) != 2 or pieces[0].lower() != "bearer":
                return None
            token, cookie_auth = pieces[1], False
        else:
            token, cookie_auth = cookie, bool(cookie)
        self.check_origin(request, required=cookie_auth and (websocket or request.method not in SAFE_METHODS))
        if not isinstance(token, str) or not 20 <= len(token) <= 256:
            return None
        session = self.sessions.get(token)
        return session if self.is_active(session, touch=touch) else None

    def require(self, request, **kwargs):
        session = self.authenticate(request, **kwargs)
        if session is None:
            raise _error(401, "unauthorized")
        return session

    def bind(self, session, resource_id, close_callback):
        if (not self.is_active(session) or not callable(close_callback) or
                resource_id in session.resources or len(session.resources) >= self.max_resources_per_session):
            return False
        session.resources[resource_id] = close_callback
        return True

    def unbind(self, session, resource_id):
        session.resources.pop(resource_id, None)

    async def revoke(self, session):
        if isinstance(session, str):
            session = self.sessions.get(session)
        if session is None:
            return
        if session.close_task is None:
            session.revoked = True
            # Keep capacity occupied until callbacks finish, even if logout is cancelled.
            callbacks = list(session.resources.values())
            session.resources.clear()
            async def close_one(callback):
                try:
                    result = callback()
                    if inspect.isawaitable(result):
                        await asyncio.wait_for(result, self.close_timeout)
                except (Exception, asyncio.CancelledError):
                    # A resource may cancel itself. That is not cancellation of
                    # revoke(), and must not abandon siblings/release capacity.
                    logging.getLogger(__name__).warning("session resource close failed")
            async def close_all():
                try:
                    await asyncio.gather(*(close_one(callback) for callback in callbacks))
                finally:
                    if self.sessions.get(session.token) is session:
                        self.sessions.pop(session.token, None)
            session.close_task = asyncio.create_task(close_all())
        await asyncio.shield(session.close_task)

    async def sweep(self):
        await asyncio.gather(*(self.revoke(session) for session in list(self.sessions.values())
                               if not session.revoked and not self.is_active(session)))

    async def close(self):
        await asyncio.gather(*(self.revoke(session) for session in list(self.sessions.values())))

    async def issue(self):
        await self.sweep()
        if len(self.sessions) >= self.max_sessions:
            raise _error(503, "session capacity")
        now = self.clock()
        token = secrets.token_urlsafe(32)
        session = Session(token, now, now)
        self.sessions[token] = session
        return session

    def _cookie(self, response, request, session):
        secure = self.public_origin[0] == "https" if self.public_origin else self.secure_request(request)
        remaining = max(0, int(self.absolute_ttl - (self.clock() - session.created_at)))
        response.set_cookie(COOKIE_NAME, session.token, path="/", httponly=True,
                            samesite="Strict", secure=secure, max_age=remaining)
        response.headers["Cache-Control"] = "no-store"
        return response

    async def login_handler(self, request):
        self.check_transport(request)
        self.check_origin(request)
        if not self.limiter.allow(self.client_ip(request)):
            response = web.json_response({"error": "too many login attempts"}, status=429)
            response.headers.update({"Retry-After": str(int(self.limiter.window) + 1), "Cache-Control": "no-store"})
            return response
        if not self.initialized:
            return web.json_response({"status": "error", "error": "setup required",
                                      "setup_required": True}, status=409,
                                     headers={"Cache-Control": "no-store"})
        data = await json_object(request, 8192)
        username = bounded_string(data.get("username"), "username", maximum=64)
        password = bounded_string(data.get("password"), "password", maximum=4096)
        password_hash = bounded_string(data.get("password_hash"), "password_hash", maximum=64)
        digest = hashlib.sha256((password or "").encode()).hexdigest()
        allowed = not self.username or (bool(username) and secrets.compare_digest(username, self.username))
        valid = allowed and bool(password) and secrets.compare_digest(digest, self._password_hash)
        if self.allow_password_hash and password_hash and re.fullmatch(r"[0-9a-f]{64}", password_hash):
            valid = allowed and (valid or secrets.compare_digest(password_hash, self._password_hash))
        if not valid:
            return web.json_response({"status": "error", "error": "invalid credentials"}, status=401,
                                     headers={"Cache-Control": "no-store"})
        session = await self.issue()
        return self._cookie(web.json_response({"status": "ok", "token": session.token,
                                               "username": self.username,
                                               "must_change_password": self.must_change_password}), request, session)

    async def logout_handler(self, request):
        session = self.require(request, touch=False)
        if request.can_read_body:
            await json_object(request, 8192)
        await self.revoke(session)
        response = web.json_response({"ok": True}, headers={"Cache-Control": "no-store"})
        secure = self.public_origin[0] == "https" if self.public_origin else self.secure_request(request)
        response.del_cookie(COOKIE_NAME, path="/", secure=secure,
                            httponly=True, samesite="Strict")
        return response

    async def session_handler(self, request):
        session = self.authenticate(request)
        if session is None:
            return web.json_response({"authenticated": False, "setup_required": not self.initialized,
                                      "username_required": bool(self.username)}, status=401,
                                     headers={"Cache-Control": "no-store"})
        # Old Android WebViews omit cookies on WebSocket upgrades, so an already
        # authenticated same-origin page may use this token for the WS handshake.
        return web.json_response({"authenticated": True, "ws_token": session.token,
                                  "must_change_password": self.must_change_password},
                                 headers={"Cache-Control": "no-store"})

    async def restore_handler(self, request):
        self.check_transport(request)
        self.check_origin(request)
        data = await json_object(request, 8192)
        token = bounded_string(data.get("token"), "token", maximum=256)
        # Body token is migration only; never allow a cross-origin cookie upgrade.
        session = self.authenticate(request)
        if session is None and token:
            session = self.sessions.get(token)
            if not self.is_active(session, touch=True):
                session = None
        if session is None:
            raise _error(401, "unauthorized")
        return self._cookie(web.json_response({"ok": True, "authenticated": True}), request, session)

    async def cleanup_context(self, app):
        async def periodic():
            while True:
                await asyncio.sleep(self.sweep_interval)
                await self.sweep()
        task = asyncio.create_task(periodic(), name="auth-session-expiry")
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await self.close()

    def install(self, app):
        app.router.add_post("/api/login", self.login_handler)
        app.router.add_post("/api/logout", self.logout_handler)
        app.router.add_get("/api/session", self.session_handler)
        app.router.add_post("/api/restore", self.restore_handler)
        app.cleanup_ctx.append(self.cleanup_context)


def make_setup_handler(manager, store):
    """One-time web setup, authorised by the deployment's SETUP_TOKEN.

    The handler is only reachable while no credential file exists. It creates
    the account, persists only a password hash, issues the first session and
    never exposes the token or the password in responses or logs.
    """
    async def setup_handler(request):
        manager.check_transport(request)
        manager.check_origin(request, required=True)
        if not manager.limiter.allow(manager.client_ip(request)):
            response = web.json_response({"error": "too many setup attempts"}, status=429,
                                         headers={"Cache-Control": "no-store"})
            response.headers["Retry-After"] = str(int(manager.limiter.window) + 1)
            return response
        if manager.initialized:
            return web.json_response({"error": "already initialized"}, status=409,
                                     headers={"Cache-Control": "no-store"})
        data = await json_object(request, 8192)
        token = bounded_string(data.get("token"), "token", maximum=256)
        username = bounded_string(data.get("username"), "username", maximum=64) or "admin"
        password = bounded_string(data.get("password"), "password", maximum=4096, required=True)
        if not manager.verify_setup_token(token):
            return web.json_response({"error": "invalid setup token"}, status=403,
                                     headers={"Cache-Control": "no-store"})
        try:
            validate_new_password(password)
            stored = store.save(username, password)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400,
                                     headers={"Cache-Control": "no-store"})
        except OSError:
            logging.getLogger(__name__).error("credential storage unavailable")
            return web.json_response({"error": "credential storage unavailable"}, status=503,
                                     headers={"Cache-Control": "no-store"})
        manager.set_credentials(stored.username, password_hash=stored.password_hash)
        session = await manager.issue()
        return manager._cookie(web.json_response({"ok": True, "username": stored.username,
                                                  "token": session.token}), request, session)
    return setup_handler


def make_change_password_handler(manager, store):
    """Change the web password after a default-token or forced-change login."""
    async def change_handler(request):
        manager.check_transport(request)
        manager.check_origin(request, required=True)
        if not manager.limiter.allow(manager.client_ip(request)):
            response = web.json_response({"error": "too many password attempts"}, status=429,
                                         headers={"Cache-Control": "no-store"})
            response.headers["Retry-After"] = str(int(manager.limiter.window) + 1)
            return response
        manager.require(request, touch=True)
        data = await json_object(request, 8192)
        new_password = bounded_string(data.get("new_password"), "new_password", maximum=4096, required=True)
        current = bounded_string(data.get("current_password"), "current_password", maximum=4096)
        if not manager.must_change_password and not manager.check_password(current):
            return web.json_response({"error": "current password is incorrect"}, status=403,
                                     headers={"Cache-Control": "no-store"})
        try:
            validate_new_password(new_password)
            stored = store.save(manager.username or "admin", new_password, must_change=False)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400,
                                     headers={"Cache-Control": "no-store"})
        except OSError:
            logging.getLogger(__name__).error("credential storage unavailable")
            return web.json_response({"error": "credential storage unavailable"}, status=503,
                                     headers={"Cache-Control": "no-store"})
        manager.set_credentials(stored.username, password_hash=stored.password_hash,
                                must_change_password=False)
        return web.json_response({"ok": True, "username": stored.username},
                                 headers={"Cache-Control": "no-store"})
    return change_handler


class SafeAccessLogger(AbstractAccessLogger):
    """Log route templates only: no URLs, queries, cookies, headers or user agents.

    This intentionally ignores the caller's access_log_format, so adding a header
    formatter cannot accidentally reintroduce bearer credentials to the log.
    """
    def log(self, request, response, time):
        resource = getattr(request.match_info.route, "resource", None)
        canonical = getattr(resource, "canonical", None)
        route = canonical if isinstance(canonical, str) else "<unmatched>"
        # Canonical dynamic routes contain variable names, never actual capabilities.
        route = re.sub(r"[\r\n\x00-\x1f\x7f]", "?", route)[:256]
        method = request.method if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} else "OTHER"
        self.logger.info("%s %s %s %.3fs", method, route, response.status, time)
