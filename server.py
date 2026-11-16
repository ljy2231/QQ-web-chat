import asyncio
import copy
import sqlite3
from contextlib import closing
import json
import logging
import os
import uuid
import base64
import secrets
import time
from urllib.parse import urlparse, quote
import ipaddress
import hashlib
import glob
import mimetypes
import socket
import re
import stat
from datetime import datetime
import aiohttp
from aiohttp import web, WSMsgType

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("NapCat-WebChat")

# ====================================================
# ⚠️ 配置区
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")
WEB_PASSWORD_HASH = hashlib.sha256(WEB_PASSWORD.encode("utf-8")).hexdigest()
DATA_DIR = os.path.abspath(os.environ.get("CHAT_DATA_DIR", os.path.dirname(os.path.abspath(__file__))))
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
DEFAULT_SETTINGS = {
    "whitelist": [],
    "blacklist": [],
    "use_image_host": True,
    "theme": "light",
    "theme_auto": False,
    "dark_start": "20:00",
    "dark_end": "07:00",
    "chat_background": False,
    "sound_enabled": True
}
settings = dict(DEFAULT_SETTINGS)
ADMIN_QQ_ID = os.environ.get("ADMIN_QQ_ID", "")
EXPECTED_TOKEN = os.environ.get("NAPCAT_ACCESS_TOKEN", "")
# ====================================================

DATA_FILE = os.path.join(DATA_DIR, "chat_data.json")
DB_FILE = os.path.join(DATA_DIR, "chat_storage.sqlite3")
UPLOAD_DIR = os.path.join(DATA_DIR, "files")

# 保存有效的登录 Token
active_tokens = set()

HTML_404 = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>页面发生错误或不存在</title>
<style>
  :root { --bg: #0f1115; --card: #171a21; --text: #e8eaed; --muted: #9aa0a6; --danger: #ff5c5c; --border: #262a33; }
  @media (prefers-color-scheme: light) { :root { --bg: #f5f6f8; --card: #ffffff; --text: #1f2328; --muted: #6b7280; --danger: #e5484d; --border: #e5e7eb; } }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Roboto, sans-serif; background: var(--bg); color: var(--text); display: flex; align-items: center; justify-content: center; padding: 24px; line-height: 1.6; overflow: hidden; }
  body::before { content: ""; position: fixed; inset: -20%; background: radial-gradient(circle at 50% 50%, rgba(79, 140, 255, 0.10), transparent 60%); animation: bgPulse 3s ease-in-out infinite; pointer-events: none; z-index: 0; }
  @keyframes bgPulse { 0%, 100% { opacity: 0.5; transform: scale(1); } 50% { opacity: 1; transform: scale(1.06); } }
  .card { position: relative; z-index: 1; width: 100%; max-width: 480px; background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 48px 32px; text-align: center; box-shadow: 0 10px 40px rgba(0, 0, 0, 0.12); opacity: 0; transform: translateY(10px) scale(0.98); animation: cardIn 0.35s cubic-bezier(0.22, 1, 0.36, 1) forwards; }
  @keyframes cardIn { to { opacity: 1; transform: translateY(0) scale(1); } }
  .icon { position: relative; width: 72px; height: 72px; margin: 0 auto 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; background: rgba(255, 92, 92, 0.12); color: var(--danger); animation: iconPop 0.35s cubic-bezier(0.34, 1.56, 0.64, 1) both; }
  .icon::after { content: ""; position: absolute; inset: -6px; border-radius: 50%; border: 2px solid currentColor; opacity: 0; animation: halo 1.4s ease-out 0.4s infinite; }
  @keyframes iconPop { 0% { transform: scale(0.4); opacity: 0; } 70% { transform: scale(1.08); opacity: 1; } 100% { transform: scale(1); opacity: 1; } }
  @keyframes halo { 0% { transform: scale(0.9); opacity: 0.6; } 70% { transform: scale(1.5); opacity: 0; } 100% { transform: scale(1.5); opacity: 0; } }
  .icon svg { width: 36px; height: 36px; stroke: currentColor; stroke-width: 2; fill: none; stroke-linecap: round; stroke-linejoin: round; }
  .icon svg line:first-of-type { animation: nudge 1.4s ease-in-out 0.5s infinite; transform-origin: center; }
  @keyframes nudge { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-1px); } }
  h1 { font-size: 22px; font-weight: 600; margin: 0 0 12px; letter-spacing: 0.2px; }
  p { font-size: 14px; color: var(--muted); margin: 0; }
  .footer { margin-top: 28px; font-size: 12px; color: var(--muted); opacity: 0.7; }
  .ln { display: block; overflow: hidden; padding-bottom: 0.14em; margin-bottom: -0.14em; }
  .ln > span { display: block; transform: translateY(120%); opacity: 0; animation: lineUp 0.62s cubic-bezier(0.22, 1, 0.36, 1) forwards; animation-delay: calc(var(--d, 0ms) + 260ms); }
  @keyframes lineUp { from { transform: translateY(120%); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
  @media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } .card, h1, p, .icon, .ln > span { opacity: 1; transform: none; } }
</style>
</head>
<body>
  <div class="card">
    <div class="icon" aria-hidden="true">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
    </div>
    <h1><span class="ln" style="--d:0ms"><span>页面发生错误或不存在</span></span></h1>
    <p>
      <span class="ln" style="--d:90ms"><span>服务暂时不可用，或你访问的页面已被移除。</span></span>
      <span class="ln" style="--d:180ms"><span>请稍后重试。</span></span>
    </p>
    <div class="footer">
      <span class="ln" style="--d:270ms"><span>若问题持续存在，请联系站点管理员</span></span>
    </div>
  </div>
</body>
</html>"""

@web.middleware
async def error_middleware(request, handler):
    try:
        response = await handler(request)
        if response.status == 404:
            return web.Response(text=HTML_404, content_type='text/html', charset='utf-8', status=404)
        return response
    except web.HTTPException as ex:
        if ex.status in [400, 403, 404, 405]:
            return web.Response(text=HTML_404, content_type='text/html', charset='utf-8', status=404)
        raise

class ServerState:
    def __init__(self):
        self.napcat_ws = None
        self.clients = set()
        self.napcat_connected = False
        self.self_id = None
        self.contacts = {}
        self.messages = {}
        self.lock = asyncio.Lock()
        self.settings_lock = asyncio.Lock()
        self.is_all_mode = False

state = ServerState()
pending_requests = {}
napcat_transition_lock = asyncio.Lock()
NAPCAT_QUEUE_LIMIT = 128
BROADCAST_TIMEOUT = 2.0
BROADCAST_QUEUE_LIMIT = 64
NAPCAT_FRAME_BYTES = 8 * 1024 * 1024
NAPCAT_QUEUE_BYTES = 16 * 1024 * 1024
CLIENT_FRAME_BYTES = 8 * 1024 * 1024
CLIENT_QUEUE_BYTES = 16 * 1024 * 1024
DRAIN_TIMEOUT = 10.0
client_senders = {}

def _open_storage():
    # Restrict newly-created database and SQLite sidecars to the service user.
    fd = os.open(DB_FILE, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError('invalid database file')
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    db = sqlite3.connect(DB_FILE, timeout=30)
    try:
        db.execute("PRAGMA busy_timeout = 30000")
        return db
    except BaseException:
        db.close()
        raise


def init_storage():
    # One transaction imports both legacy files; malformed input aborts startup.
    with closing(_open_storage()) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS storage (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM storage WHERE key = 'initialized'").fetchone():
            return
        data = {"contacts": {}, "messages": {}}
        imported_settings = {}
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("contacts", {}), dict) or not isinstance(data.get("messages", {}), dict):
                raise ValueError("invalid legacy chat data")
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                imported_settings = json.load(f)
            if not isinstance(imported_settings, dict):
                raise ValueError("invalid legacy settings")
        for key, value in (("data", {"contacts": data.get("contacts", {}), "messages": data.get("messages", {})}),
                           ("settings", imported_settings), ("initialized", True)):
            db.execute("INSERT INTO storage(key, value) VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))


def _read_storage(key):
    with closing(_open_storage()) as db, db:
        row = db.execute("SELECT value FROM storage WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise RuntimeError("storage is not initialized")
    return json.loads(row[0])


def _write_storage(key, value):
    encoded = json.dumps(value, ensure_ascii=False)
    with closing(_open_storage()) as db, db:
        db.execute("INSERT INTO storage(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, encoded))


def load_settings():
    settings.update(_read_storage("settings"))


def save_settings(value):
    _write_storage("settings", value)

def clean_ids(value):
    if isinstance(value, str):
        items = value.replace("；", ";").split(";")
    elif isinstance(value, list):
        items = value
    else:
        items = []
    return [str(x).strip() for x in items if str(x).strip()]

def public_settings():
    return {
        "use_image_host": bool(settings.get("use_image_host", True)),
        "theme": settings.get("theme", "light"),
        "theme_auto": bool(settings.get("theme_auto", False)),
        "dark_start": settings.get("dark_start", "20:00"),
        "dark_end": settings.get("dark_end", "07:00"),
        "chat_background": bool(settings.get("chat_background", False)),
        "sound_enabled": bool(settings.get("sound_enabled", True))
    }

async def ui_settings_handler(request):
    return web.json_response(public_settings())

async def settings_handler(request):
    if request.method == "GET":
        if request.query.get("token") not in active_tokens:
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response(settings)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "bad request"}, status=400)
    if data.get("token") not in active_tokens:
        return web.json_response({"error": "unauthorized"}, status=401)
    async with state.settings_lock:
        updated = dict(settings)
        updated["whitelist"] = clean_ids(data.get("whitelist", updated.get("whitelist", [])))
        updated["blacklist"] = clean_ids(data.get("blacklist", updated.get("blacklist", [])))
        updated["use_image_host"] = bool(data.get("use_image_host", updated.get("use_image_host", True)))
        updated["theme"] = "dark" if data.get("theme") == "dark" else "light"
        updated["theme_auto"] = bool(data.get("theme_auto", False))
        updated["dark_start"] = str(data.get("dark_start") or "20:00")
        updated["dark_end"] = str(data.get("dark_end") or "07:00")
        updated["chat_background"] = bool(data.get("chat_background", updated.get("chat_background", False)))
        updated["sound_enabled"] = bool(data.get("sound_enabled", updated.get("sound_enabled", True)))
        # Do not let cancellation release the lock while a worker can still commit.
        task = asyncio.create_task(asyncio.to_thread(save_settings, updated))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        settings.clear()
        settings.update(updated)
        if cancelled:
            raise asyncio.CancelledError
        return web.json_response({"ok": True, "settings": settings})

def load_data():
    data = _read_storage("data")
    state.contacts = data.get("contacts", {})
    state.messages = data.get("messages", {})

async def update_chat(mutator):
    async with state.lock:
        candidate = copy.deepcopy({"contacts": state.contacts, "messages": state.messages})
        events = mutator(candidate)
        if not events:
            return
        data = json.dumps(candidate, ensure_ascii=False)
        task = asyncio.create_task(asyncio.to_thread(_write_data_encoded, data))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        state.contacts = candidate["contacts"]
        state.messages = candidate["messages"]
        for event in events:
            await broadcast_to_clients(event)
        if cancelled:
            raise asyncio.CancelledError

def _write_data_encoded(encoded):
    with closing(_open_storage()) as db, db:
        cursor = db.execute("UPDATE storage SET value = ? WHERE key = 'data'", (encoded,))
        if cursor.rowcount != 1:
            raise RuntimeError("chat storage is not initialized")

async def client_sender(ws, queue):
    try:
        while True:
            payload, size = await queue.get()
            try:
                await asyncio.wait_for(ws.send_str(payload), BROADCAST_TIMEOUT)
            finally:
                queue.bytes_used -= size
                queue.task_done()
    except asyncio.CancelledError:
        raise
    except Exception:
        state.clients.discard(ws)
        try:
            await asyncio.wait_for(ws.close(), BROADCAST_TIMEOUT)
        except Exception:
            pass
    finally:
        client_senders.pop(ws, None)


def remove_client_sender(ws):
    sender = client_senders.pop(ws, None)
    if sender:
        sender[1].cancel()


async def queue_client_message(ws, message):
    sender = client_senders.get(ws)
    if sender is None or ws.closed:
        return False
    payload = json.dumps(message, ensure_ascii=False)
    size = len(payload.encode("utf-8"))
    if size > CLIENT_FRAME_BYTES or sender[0].bytes_used + size > CLIENT_QUEUE_BYTES:
        logger.warning("oversized client message; disconnecting")
    else:
        try:
            sender[0].put_nowait((payload, size))
            sender[0].bytes_used += size
            return True
        except asyncio.QueueFull:
            logger.warning("client queue full; disconnecting")
    state.clients.discard(ws)
    remove_client_sender(ws)
    try:
        await asyncio.wait_for(ws.close(code=1013, message=b"client queue overflow"), BROADCAST_TIMEOUT)
    except Exception:
        pass
    return False


async def broadcast_to_clients(message: dict):
    # Queue insertion is synchronous before the first await in the normal case.
    await asyncio.gather(*(queue_client_message(ws, message) for ws in list(state.clients)))

async def send_history(ws, target_id):
    message = {"type": "history_data", "target_id": target_id,
               "messages": state.messages.get(target_id, [])}
    # Do not truncate silently or disconnect in a reconnect loop.
    if len(json.dumps(message, ensure_ascii=False).encode("utf-8")) > CLIENT_FRAME_BYTES:
        await queue_client_message(ws, {"type": "toast", "content": "聊天记录过大，无法显示，请联系管理员"})
        return
    await queue_client_message(ws, message)

async def save_message(target_id, msg_data, contact=None, quote_id=None):
    def mutate(data):
        events = []
        if contact is not None and target_id not in data["contacts"]:
            data["contacts"][target_id] = copy.deepcopy(contact)
            events.append({"type": "contact_add", "contact": data["contacts"][target_id]})
        message = copy.deepcopy(msg_data)
        if quote_id:
            original = next((m for m in reversed(data["messages"].get(target_id, []))
                             if str(m.get("message_id")) == str(quote_id)), None)
            message["quote"] = ({"id": original.get("message_id"), "sender_name": original.get("sender_name"), "content": original.get("content")}
                                if original else {"id": quote_id, "sender_name": "未知", "content": "[找不到原消息]"})
        messages = data["messages"].setdefault(target_id, [])
        messages.append(message)
        del messages[:-200]
        events.append({"type": "message", **message})
        return events
    await update_chat(mutate)

async def clear_history(target_id):
    def mutate(data):
        data["messages"][target_id] = []
        return [{"type": "clear_history", "target_id": target_id}]
    await update_chat(mutate)

async def send_qq_api_and_wait(action, params, timeout=120.0):
    ws = state.napcat_ws
    if not ws or ws.closed:
        return None
    echo = secrets.token_urlsafe(24)
    future = asyncio.get_running_loop().create_future()
    pending_requests[echo] = (ws, future)
    try:
        await ws.send_json({"action": action, "params": params, "echo": echo})
        return await asyncio.wait_for(future, timeout=timeout)
    except (asyncio.TimeoutError, ConnectionError, RuntimeError):
        return None
    finally:
        pending_requests.pop(echo, None)
        if not future.done():
            future.cancel()


def fail_pending_for(ws):
    for echo, (owner, future) in list(pending_requests.items()):
        if owner is ws:
            pending_requests.pop(echo, None)
            if not future.done():
                future.set_result(None)

async def send_qq_api(action, params):
    if state.napcat_ws and not state.napcat_ws.closed:
        payload = {"action": action, "params": params, "echo": f"api_{datetime.now().timestamp()}"}
        await state.napcat_ws.send_json(payload)

MAX_TRANSFER = 50 * 1024 * 1024
remote_slots = asyncio.Semaphore(4)
wallpaper_slots = asyncio.Semaphore(2)
upload_slots = asyncio.Semaphore(4)

def public_ip(value):
    ip = ipaddress.ip_address(value)
    return ip.is_global and (not isinstance(ip, ipaddress.IPv6Address) or ip.ipv4_mapped is None or ip.ipv4_mapped.is_global)

class PublicResolver(aiohttp.abc.AbstractResolver):
    def __init__(self):
        self.delegate = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await self.delegate.resolve(host, port, family)
        if not records or any(not public_ip(r['host']) for r in records):
            raise OSError('non-public address')
        return records

    async def close(self):
        await self.delegate.close()

async def safe_fetch(url, limit=MAX_TRANSFER, slots=None):
    slots = slots or remote_slots
    await asyncio.wait_for(slots.acquire(), timeout=2)
    try:
        return await _safe_fetch(url, limit)
    finally:
        slots.release()

async def _safe_fetch(url, limit=MAX_TRANSFER):
    from urllib.parse import urljoin
    timeout = aiohttp.ClientTimeout(total=60, connect=8)
    async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False), trust_env=False) as session:
        for _ in range(5):
            parsed = urlparse(str(url))
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None:
                raise ValueError('invalid URL')
            try:
                ip = ipaddress.ip_address(parsed.hostname)
            except ValueError:
                pass
            else:
                if not public_ip(parsed.hostname):
                    raise ValueError('non-public address')
            async with session.get(url, allow_redirects=False, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://qzone.qq.com/'}) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get('Location')
                    if not location:
                        raise ValueError('missing redirect')
                    url = urljoin(str(url), location)
                    continue
                if resp.status != 200 or (resp.content_length is not None and resp.content_length > limit):
                    raise ValueError('upstream error or size limit')
                body = bytearray()
                async for chunk in resp.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > limit:
                        raise ValueError('size limit')
                return bytes(body), resp.content_type
        raise ValueError('too many redirects')

def authenticated(request):
    token = request.cookies.get('webchat_token') or request.headers.get('Authorization', '').removeprefix('Bearer ')
    return token in active_tokens

def safe_upload_path(path):
    if not isinstance(path, str):
        return None
    root = os.path.realpath(UPLOAD_DIR)
    candidate = os.path.abspath(path)
    if os.path.dirname(candidate) != root or not re.fullmatch(r'[0-9a-f]{32}(?:\.[a-zA-Z0-9]{1,10})?', os.path.basename(candidate)):
        return None
    if os.path.islink(candidate) or os.path.realpath(candidate) != candidate or not os.path.isfile(candidate):
        return None
    return candidate

def open_upload(path):
    root = os.path.realpath(UPLOAD_DIR)
    candidate = os.path.abspath(path)
    name = os.path.basename(candidate)
    if os.path.dirname(candidate) != root or not re.fullmatch(r'[0-9a-f]{32}(?:\.[a-zA-Z0-9]{1,10})?', name):
        raise OSError('invalid upload path')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    finally:
        os.close(directory)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError('not regular')
    return os.fdopen(fd, 'rb')

def encode_upload_image(path):
    with open_upload(path) as f:
        data = f.read(MAX_TRANSFER + 1)
    if len(data) > MAX_TRANSFER:
        raise ValueError('file too large')
    return base64.b64encode(data).decode('utf-8')

async def proxy_image_handler(request):
    if not authenticated(request):
        return web.Response(status=401)
    try:
        body, ctype = await safe_fetch(request.query.get('url', ''), 20 * 1024 * 1024)
        if ctype not in ('image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/avif'):
            raise ValueError('invalid image')
        return web.Response(body=body, content_type=ctype, headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store'})
    except (ValueError, OSError, aiohttp.ClientError, asyncio.TimeoutError):
        return web.Response(status=502)

async def wallpaper_handler(request):
    variant = request.query.get('variant', 'desktop')
    if variant not in ('desktop', 'mobile'):
        return web.json_response({'error': 'Invalid wallpaper variant'}, status=400)
    source = 'https://t.alcy.cc/mp' if variant == 'mobile' else 'https://t.alcy.cc/ycy'
    try:
        body, ctype = await safe_fetch(source + '?r=' + secrets.token_hex(8), 20 * 1024 * 1024, slots=wallpaper_slots)
        if not body or ctype not in ('image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/avif'):
            raise ValueError('invalid image')
        return web.Response(body=body, content_type=ctype, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    except (ValueError, OSError, aiohttp.ClientError, asyncio.TimeoutError):
        return web.json_response({'error': 'Wallpaper download failed'}, status=502)

async def logout_handler(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    active_tokens.discard(data.get("token"))
    response = web.json_response({"ok": True})
    response.del_cookie("webchat_token")
    return response

async def login_handler(request):
    data = await request.json()
    pw = str(data.get("password") or "")
    pw_hash = str(data.get("password_hash") or "")
    if pw == WEB_PASSWORD or pw_hash == WEB_PASSWORD_HASH:
        token = secrets.token_urlsafe(32)
        active_tokens.add(token)
        response = web.json_response({"status": "ok", "token": token})
        response.set_cookie("webchat_token", token, httponly=True, samesite="Strict", secure=request.secure or request.headers.get("X-Forwarded-Proto") == "https")
        return response
    return web.json_response({"status": "error", "msg": "密码错误"}, status=401)

async def restore_handler(request):
    try:
        data = await request.json()
        token = data.get('token')
    except Exception:
        return web.Response(status=400)
    if token not in active_tokens:
        return web.Response(status=401)
    response = web.json_response({'ok': True})
    response.set_cookie('webchat_token', token, httponly=True, samesite='Strict', secure=request.secure or request.headers.get('X-Forwarded-Proto') == 'https')
    return response

bridge_sessions = {}
BRIDGE_SESSION_LIMIT = 64
BRIDGE_QUEUE_LIMIT = 128
BRIDGE_QUEUE_BYTES = 16 * 1024 * 1024
BRIDGE_TOTAL_BYTES = 64 * 1024 * 1024
BRIDGE_FRAME_BYTES = 8 * 1024 * 1024
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
bridge_connecting = 0

async def bridge_connect_handler(request):
    data = await request.json()
    token = data.get("token")
    if token not in active_tokens:
        return web.json_response({"error": "unauthorized"}, status=401)
    global bridge_connecting
    if len(bridge_sessions) + bridge_connecting >= BRIDGE_SESSION_LIMIT:
        return web.json_response({"error": "bridge capacity"}, status=503)
    bridge_connecting += 1
    sid = secrets.token_urlsafe(16)
    session = aiohttp.ClientSession()
    try:
        ws = await session.ws_connect("http://127.0.0.1:4001/client_ws?token=" + token)
    except BaseException:
        await session.close()
        raise
    finally:
        bridge_connecting -= 1
    queue = asyncio.Queue(maxsize=BRIDGE_QUEUE_LIMIT)
    queue.bytes_used = 0
    available = asyncio.Event()
    async def reader():
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    size = len(msg.data.encode('utf-8'))
                    if (size > BRIDGE_FRAME_BYTES or queue.bytes_used + size > BRIDGE_QUEUE_BYTES or
                            sum(s['queue'].bytes_used for s in bridge_sessions.values()) + size > BRIDGE_TOTAL_BYTES):
                        logger.warning("bridge queue byte limit exceeded; disconnecting")
                        break
                    try:
                        queue.put_nowait((msg.data, size))
                        queue.bytes_used += size
                        available.set()
                    except asyncio.QueueFull:
                        logger.warning("bridge queue full; disconnecting")
                        break
        except Exception:
            pass
        finally:
            # Keep buffered messages available until the poller drains them.
            s = bridge_sessions.get(sid)
            if s is not None:
                s["closed"] = True
            available.set()
            await ws.close()
            await session.close()
    task = asyncio.create_task(reader())
    bridge_sessions[sid] = {"ws": ws, "queue": queue, "available": available, "task": task, "session": session, "last": time.monotonic()}
    return web.json_response({"session": sid})

async def bridge_poll_handler(request):
    sid = request.query.get("session")
    s = bridge_sessions.get(sid)
    if not s:
        return web.json_response({"error": "no session"}, status=404)
    if s.get("polling"):
        return web.json_response({"error": "poll already active"}, status=409)
    s["polling"] = True
    s["last"] = time.monotonic()
    messages = []
    try:
        # Wait without removing data: cancellation leaves the queue unchanged.
        if s['queue'].empty() and not s.get('closed'):
            s['available'].clear()
            try:
                await asyncio.wait_for(s['available'].wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
        # One frame per response bounds the batch without reordering or loss.
        if not messages and not s['queue'].empty():
            item, size = s['queue'].get_nowait()
            s['queue'].bytes_used -= size
            s['queue'].task_done()
            messages.append(item)
    finally:
        s["polling"] = False
        s["last"] = time.monotonic()
    closed = bool(s.get("closed") and s["queue"].empty())
    if closed and bridge_sessions.get(sid) is s:
        bridge_sessions.pop(sid, None)
    return web.json_response({"messages": messages, "closed": closed})

async def bridge_send_handler(request):
    data = await request.json()
    s = bridge_sessions.get(data.get("session"))
    if not s or s["ws"].closed:
        return web.json_response({"error": "no session"}, status=404)
    s["last"] = time.monotonic()
    try:
        await s["ws"].send_json(data.get("payload"))
    except Exception:
        return web.json_response({"error": "send failed"}, status=502)
    return web.json_response({"ok": True})

async def bridge_cleanup():
    while True:
        now = time.monotonic()
        for sid, s in list(bridge_sessions.items()):
            if not s.get("polling") and now - s["last"] > 300:
                bridge_sessions.pop(sid, None)
                try:
                    await s["ws"].close()
                except Exception:
                    pass
                s["task"].cancel()
                try:
                    await s["session"].close()
                except Exception:
                    pass
        await asyncio.sleep(60)

async def upload_handler(request):
    if not authenticated(request):
        return web.json_response({'error': 'unauthorized'}, status=401)
    try:
        await asyncio.wait_for(upload_slots.acquire(), timeout=2)
    except asyncio.TimeoutError:
        return web.json_response({'error': 'busy'}, status=503)
    try:
        return await asyncio.wait_for(_upload_handler(request), timeout=120)
    except asyncio.TimeoutError:
        return web.json_response({'error': 'upload timed out'}, status=408)
    finally:
        upload_slots.release()

async def _upload_handler(request):
    if not authenticated(request):
        return web.json_response({'error': 'unauthorized'}, status=401)
    filepath = None
    try:
        reader = await request.multipart()
        field = await reader.next()
        if not field or field.name not in ('image', 'file'):
            return web.json_response({'error': 'Invalid field'}, status=400)
        orig_name = os.path.basename((field.filename or 'file').replace('\\', '/'))
        ext = os.path.splitext(orig_name)[1]
        if not re.fullmatch(r'\.[a-zA-Z0-9]{1,10}', ext):
            ext = ''
        filename = uuid.uuid4().hex + ext
        filepath = os.path.join(UPLOAD_DIR, filename)
        size = 0
        with open(filepath, 'xb') as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_TRANSFER:
                    raise ValueError('file too large')
                f.write(chunk)
        return web.json_response({'url': '/files/' + filename, 'local_path': filepath, 'orig_name': orig_name})
    except BaseException as exc:
        if filepath and os.path.isfile(filepath):
            os.unlink(filepath)
        if isinstance(exc, asyncio.CancelledError):
            raise
        return web.json_response({'error': 'upload failed'}, status=400)

async def cache_remote_image(url):
    try:
        body, ctype = await safe_fetch(url, 20 * 1024 * 1024)
        extensions = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif', 'image/webp': '.webp', 'image/avif': '.avif'}
        if not body or ctype not in extensions:
            return None
        filename = uuid.uuid4().hex + extensions[ctype]
        with open(os.path.join(UPLOAD_DIR, filename), 'xb') as f:
            f.write(body)
        return '/files/' + filename
    except (ValueError, OSError, aiohttp.ClientError, asyncio.TimeoutError):
        return None

async def download_handler(request):
    if not authenticated(request):
        return web.Response(status=401)
    try:
        body, _ = await safe_fetch(request.query.get('url', ''))
        name = os.path.basename(request.query.get('name') or 'file')
        return web.Response(body=body, content_type='application/octet-stream', headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(name, safe=''), 'X-Content-Type-Options': 'nosniff'})
    except (ValueError, OSError, aiohttp.ClientError, asyncio.TimeoutError):
        return web.Response(status=502)

async def cleanup_old_files():
    while True:
        cutoff = time.time() - 7 * 86400
        try:
            for entry in os.scandir(UPLOAD_DIR):
                try:
                    if entry.is_file() and entry.stat().st_mtime < cutoff:
                        os.remove(entry.path)
                        logger.info(f"已清理过期文件: {entry.name}")
                except OSError:
                    pass
        except OSError:
            pass
        await asyncio.sleep(3600)

async def refresh_contacts_meta():
    snapshot = copy.deepcopy(state.contacts)
    async def fetch(cid, contact):
        if contact.get("type") != "group":
            return None
        try:
            resp = await send_qq_api_and_wait("get_group_info", {"group_id": int(cid)})
        except (ValueError, TypeError):
            return None
        if resp and resp.get("status") == "ok" and isinstance(resp.get("data"), dict):
            return resp["data"].get("group_name")
        return None
    names = await asyncio.gather(*(fetch(cid, contact) for cid, contact in snapshot.items()))
    def mutate(data):
        events = []
        for (cid, old), name in zip(snapshot.items(), names):
            contact = data["contacts"].get(cid)
            if not contact or contact.get("type") != old.get("type"):
                continue
            avatar = f"https://p.qlogo.cn/gh/{cid}/{cid}/100" if old.get("type") == "group" else f"https://q.qlogo.cn/headimg_dl?dst_uin={cid}&spec=100"
            changed = False
            if contact.get("avatar") == old.get("avatar") and contact.get("avatar") != avatar:
                contact["avatar"] = avatar
                changed = True
            if name and contact.get("name") == old.get("name") and contact.get("name") != name:
                contact["name"] = name
                changed = True
            if changed:
                events.append({"type": "contact_add", "contact": contact})
        return events
    await update_chat(mutate)

async def napcat_ws_handler(request):
    if request.headers.get('Upgrade', '').lower() != 'websocket':
        return web.Response(text=HTML_404, content_type='text/html', charset='utf-8', status=404)

    ws = web.WebSocketResponse(max_msg_size=NAPCAT_FRAME_BYTES)
    await ws.prepare(request)

    if EXPECTED_TOKEN:
        token = request.query.get("access_token") or request.headers.get("Authorization", "").replace("Bearer ", "")
        if token != EXPECTED_TOKEN:
            await ws.close(code=1008, message=b"Invalid token")
            return ws

    # Close the currently active connection before waiting for its drain.
    old_ws = state.napcat_ws
    if old_ws is not None and old_ws is not ws:
        fail_pending_for(old_ws)
        await old_ws.close()
    try:
        await asyncio.wait_for(napcat_transition_lock.acquire(), DRAIN_TIMEOUT + 2)
    except asyncio.TimeoutError:
        await ws.close(code=1013, message=b"connection busy")
        return ws
    state.napcat_ws = ws
    state.napcat_connected = True
    state.self_id = None

    queue = asyncio.Queue(maxsize=NAPCAT_QUEUE_LIMIT)
    queue.bytes_used = 0

    async def worker():
        while True:
            data, event_size = await queue.get()
            try:
                post_type = data.get("post_type")

                if post_type == "meta_event" and data.get("meta_event_type") == "heartbeat":
                    status = data.get("status", {})
                    if not status.get("online", True):
                        await broadcast_to_clients({"type": "status", "connected": False, "error": "QQ掉线"})
                        continue

                if post_type == "notice" and data.get("notice_type") == "bot_offline":
                    await broadcast_to_clients({"type": "status", "connected": False, "error": "QQ掉线"})
                    continue

                if post_type == "notice" and data.get("notice_type") in ["friend_recall", "group_recall"]:
                    target_id = str(data.get("user_id")) if data.get("notice_type") == "friend_recall" else str(data.get("group_id"))
                    await handle_recall(target_id, data.get("message_id"))
                    continue

                if post_type == "message" and data.get("message_type") in ["private", "group"]:
                    msg_type = data.get("message_type")
                    sender = data.get("sender", {})
                    sender_uin = str(sender.get("user_id") or "")
                    message_id = data.get("message_id")

                    if msg_type == "private":
                        target_id = str(sender.get("user_id"))
                        name = sender.get("nickname", "未知用户")
                        reply_action = "send_private_msg"
                        reply_params = {"user_id": int(target_id)}
                    else:
                        target_id = str(data.get("group_id"))
                        name = sender.get("card") or sender.get("nickname") or "群成员"
                        reply_action = "send_group_msg"
                        reply_params = {"group_id": int(target_id)}

                    raw_msg = data.get("message", "")
                    text_content = ""
                    media_url = None
                    file_name = None
                    file_size = 0
                    file_url = None
                    file_id = None
                    quote_id = None

                    if isinstance(raw_msg, list):
                        for m in raw_msg:
                            m_type = m.get("type")
                            m_data = m.get("data", {})
                            if m_type == "text": text_content += m_data.get("text", "")
                            elif m_type == "reply": quote_id = m_data.get("id")
                            elif m_type in ["image", "mface"]:
                                text_content += "[图片]"
                                if not media_url:
                                    raw_media = m_data.get("url") or m_data.get("file")
                                    media_url = raw_media
                            elif m_type == "file":
                                file_name = os.path.basename(str(m_data.get("name") or m_data.get("file") or "文件"))
                                try:
                                    file_size = int(m_data.get("file_size") or 0)
                                except (TypeError, ValueError):
                                    file_size = 0
                                file_url = m_data.get("url") or None
                                file_id = m_data.get("file_id") or None
                                text_content += f"[文件] {file_name}"
                            elif m_type == "face": text_content += "[表情]"
                    else:
                        text_content = str(raw_msg)

                    text_content = text_content.strip()
                    if not text_content: continue

                    blacklist = set(str(x) for x in settings.get("blacklist", []))
                    whitelist = set(str(x) for x in settings.get("whitelist", []))
                    if target_id in blacklist or sender_uin in blacklist:
                        continue
                    if target_id not in whitelist:
                        if msg_type == "private" and text_content.startswith("="):
                            await send_qq_api(reply_action, {**reply_params, "message": f"你的ID:{sender_uin} 当前未授权，请联系管理员授权"})
                        continue

                    upper_content = text_content.upper()

                    if not quote_id:
                        if text_content == "=":
                            if msg_type == "group":
                                continue
                            if sender_uin == ADMIN_QQ_ID:
                                help_text = "【指令菜单】\n= ALL 开启全量转发\n= CLEAR 清空聊天记录\n= REBOOT 重启服务器"
                            else:
                                help_text = "【指令菜单】\n= ALL 开启全量转发"
                            await send_qq_api(reply_action, {**reply_params, "message": help_text})
                            continue
                        elif upper_content == "= ALL":
                            state.is_all_mode = not state.is_all_mode
                            tip = f"私聊全量转发已{'开启' if state.is_all_mode else '关闭'}"
                            await send_qq_api(reply_action, {**reply_params, "message": tip})
                            continue
                        elif upper_content == "= CLEAR":
                            if not ADMIN_QQ_ID or sender_uin != ADMIN_QQ_ID:
                                continue
                            await clear_history(target_id)
                            await send_qq_api(reply_action, {**reply_params, "message": "聊天记录已清空"})
                            continue
                        elif upper_content == "= REBOOT":
                            if not ADMIN_QQ_ID or sender_uin != ADMIN_QQ_ID:
                                continue
                            await send_qq_api(reply_action, {**reply_params, "message": "服务正在重启..."})
                            await asyncio.sleep(1)
                            os._exit(0)

                        if not state.is_all_mode and msg_type != "group":
                            if not text_content.startswith("="): continue
                            else: text_content = text_content[1:].strip()

                        if not text_content: continue

                    if media_url:
                        media_url = (await cache_remote_image(media_url)) or media_url

                    contact_name = name
                    if msg_type == "group" and target_id not in state.contacts:
                        info = await send_qq_api_and_wait("get_group_info", {"group_id": int(target_id)})
                        gname = info.get("data", {}).get("group_name") if (info and isinstance(info.get("data"), dict)) else None
                        contact_name = gname or target_id
                    avatar = f"https://p.qlogo.cn/gh/{target_id}/{target_id}/100" if msg_type == "group" else f"https://q.qlogo.cn/headimg_dl?dst_uin={target_id}&spec=100"
                    contact = {"id": target_id, "name": contact_name, "type": msg_type, "avatar": avatar}

                    msg_obj = {
                        "message_id": message_id, "target_id": target_id, "sender_name": name,
                        "content": text_content, "media_url": media_url, "quote": None,
                        "file_name": file_name, "file_size": file_size, "file_url": file_url, "file_id": file_id,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    await save_message(target_id, msg_obj, contact, quote_id)

                    if msg_type == "private":
                        await send_qq_api(reply_action, {**reply_params, "message": "[Web-QQ] 消息已转发到Web端"})
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NapCat event processing failed")
            finally:
                queue.bytes_used -= event_size
                queue.task_done()

    async def fetch_login_info():
        resp = await send_qq_api_and_wait("get_login_info", {})
        if state.napcat_ws is ws and resp and resp.get("status") == "ok":
            state.self_id = resp.get("data", {}).get("user_id")
            await broadcast_to_clients({"type": "status", "connected": True, "self_id": state.self_id})

    worker_task = asyncio.create_task(worker())
    startup_tasks = [asyncio.create_task(fetch_login_info()), asyncio.create_task(refresh_contacts_meta())]
    try:
        await broadcast_to_clients({"type": "status", "connected": True, "self_id": state.self_id})
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
                if not isinstance(data, dict):
                    continue
                echo = data.get("echo")
                pending = pending_requests.get(echo) if isinstance(echo, str) else None
                if pending and pending[0] is ws:
                    future = pending[1]
                    if not future.done():
                        future.set_result(data)
                    continue
                if echo is not None and not data.get("post_type"):
                    continue
                if (data.get("post_type") == "meta_event" and
                        data.get("meta_event_type") == "heartbeat" and
                        isinstance(data.get("status", {}), dict) and
                        data.get("status", {}).get("online", True)):
                    continue
                if state.napcat_ws is not ws:
                    break
                try:
                    event_size = len(msg.data.encode("utf-8"))
                    if queue.bytes_used + event_size > NAPCAT_QUEUE_BYTES:
                        raise asyncio.QueueFull
                    queue.put_nowait((data, event_size))
                    queue.bytes_used += event_size
                except asyncio.QueueFull:
                    logger.warning("NapCat event queue full; disconnecting to avoid unbounded memory")
                    await ws.close(code=1013, message=b"event queue full")
                    break
            except json.JSONDecodeError:
                continue
    finally:
        fail_pending_for(ws)
        for task in startup_tasks:
            task.cancel()
        try:
            # Keep the old connection as the owner until its queued events drain.
            # Its socket is closed, so drained events cannot issue new API calls.
            await asyncio.wait_for(queue.join(), DRAIN_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("NapCat drain timed out with %d queued events", queue.qsize())
        finally:
            worker_task.cancel()
            await asyncio.gather(worker_task, *startup_tasks, return_exceptions=True)
            if state.napcat_ws is ws:
                state.napcat_ws = None
                state.napcat_connected = False
                state.self_id = None
                await broadcast_to_clients({"type": "status", "connected": False, "error": "NapCat已断开"})
            napcat_transition_lock.release()

    return ws

async def handle_recall(target_id, message_id):
    def mutate(data):
        for message in data["messages"].get(target_id, []):
            if str(message.get("message_id")) == str(message_id) and not message.get("is_recalled"):
                sender_name = message.get("sender_name")
                message["content"] = f"「{sender_name}」撤回了一条消息"
                message["is_recalled"] = True
                message["media_url"] = None
                message["quote"] = None
                return [{"type": "message_recalled", "target_id": target_id, "message_id": message_id, "sender_name": sender_name}]
        return []
    await update_chat(mutate)

async def client_ws_handler(request):
    token = request.query.get("token")
    if token not in active_tokens:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.close(code=1008, message=b"Unauthorized")
        return ws

    ws = web.WebSocketResponse()
    await ws.prepare(request)
    state.clients.add(ws)
    send_queue = asyncio.Queue(maxsize=BROADCAST_QUEUE_LIMIT)
    send_queue.bytes_used = 0
    client_senders[ws] = (send_queue, asyncio.create_task(client_sender(ws, send_queue)))

    client_ip = request.headers.get("X-Real-IP") or \
                request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or \
                request.remote

    try:
        await queue_client_message(ws, {"type": "status", "connected": state.napcat_connected, "self_id": state.self_id})
        for contact in list(state.contacts.values()):
            await queue_client_message(ws, {"type": "contact_add", "contact": contact})
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)

                if data.get("type") == "get_history":
                    target_id = data.get("target_id")
                    if not isinstance(target_id, str) or len(target_id) > 256:
                        continue
                    await send_history(ws, target_id)

                elif data.get("type") == "send_message":
                    target_id, content, msg_type = data.get("target_id"), data.get("content"), data.get("msg_type")
                    quote_info = data.get("quote")
                    file_local_path = data.get("file_local_path")
                    if file_local_path:
                        file_local_path = safe_upload_path(file_local_path)
                        if not file_local_path:
                            await queue_client_message(ws, {"type": "toast", "content": "文件路径无效"})
                            continue
                    file_orig_name = data.get("file_orig_name", "file")
                    file_is_image = data.get("file_is_image", False)

                    if not state.napcat_connected:
                        await queue_client_message(ws, {"type": "toast", "content": "NapCat 未连接，请检查配置"})
                        continue

                    if state.napcat_ws and not state.napcat_ws.closed:
                        action = "send_private_msg" if msg_type == "private" else "send_group_msg"
                        time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                        message_payload = []
                        reply_target_id = None
                        if quote_info:
                            reply_target_id = quote_info.get("napcat_id")
                            if not reply_target_id and quote_info.get("id") and not str(quote_info.get("id")).startswith("local_"):
                                reply_target_id = quote_info.get("id")
                        if reply_target_id:
                            message_payload.append({"type": "reply", "data": {"id": str(reply_target_id)}})

                        local_url = None
                        send_file_name = None
                        send_file_size = 0
                        send_file_url = None
                        file_public_url = None
                        if file_local_path and os.path.exists(file_local_path):
                            if file_is_image:
                                try:
                                    img_b64 = await asyncio.to_thread(encode_upload_image, file_local_path)
                                    message_payload.append({"type": "image", "data": {"file": f"base64://{img_b64}"}})
                                    local_url = f"/files/{os.path.basename(file_local_path)}"
                                except Exception as e:
                                    await queue_client_message(ws, {"type": "toast", "content": "图片读取失败"})
                                    continue
                            else:
                                send_file_name = os.path.basename(file_orig_name or "文件")
                                try:
                                    with open_upload(file_local_path) as f:
                                        send_file_size = os.fstat(f.fileno()).st_size
                                except OSError:
                                    send_file_size = 0
                                send_file_url = f"/files/{os.path.basename(file_local_path)}"
                                file_public_url = PUBLIC_BASE_URL + send_file_url

                        if content:
                            formatted_text = f"正文:{content}\nIP:{client_ip}\n{time_str}"
                            message_payload.append({"type": "text", "data": {"text": formatted_text}})

                        file_sent = False
                        file_message_id = None
                        if file_public_url:
                            file_params = {
                                "message": [{"type": "file", "data": {"file": file_public_url, "name": send_file_name}}],
                                "auto_escape": False
                            }
                            if msg_type == "private": file_params["user_id"] = int(target_id)
                            else: file_params["group_id"] = int(target_id)
                            file_resp = await send_qq_api_and_wait(action, file_params, timeout=600.0)
                            file_sent = bool(file_resp and file_resp.get("status") == "ok" and file_resp.get("retcode") == 0)
                            if file_sent:
                                file_message_id = (file_resp.get("data") or {}).get("message_id")
                            elif file_resp is None:
                                await queue_client_message(ws, {"type": "toast", "content": "文件发送结果未知，请先确认对方是否收到，勿重复发送"})
                                continue
                            else:
                                await queue_client_message(ws, {"type": "toast", "content": "文件直发失败，已改为链接发送"})

                        sent_resp = None
                        if message_payload or (file_public_url and not file_sent):
                            final_payload = list(message_payload)
                            if file_public_url and not file_sent:
                                final_payload.append({"type": "text", "data": {"text": f"对方向你发送了一个文件 {send_file_name}\n{file_public_url}\n{time_str}\nIP:{client_ip}"}})
                            params = {"message": final_payload, "auto_escape": False}
                            if msg_type == "private": params["user_id"] = int(target_id)
                            else: params["group_id"] = int(target_id)
                            sent_resp = await send_qq_api_and_wait(action, params)
                            if sent_resp is None:
                                await queue_client_message(ws, {"type": "toast", "content": "消息发送结果未知，请先确认对方是否收到，勿重复发送"})
                                continue
                            if not isinstance(sent_resp, dict) or sent_resp.get("status") != "ok" or sent_resp.get("retcode") != 0:
                                await queue_client_message(ws, {"type": "toast", "content": "消息发送失败；文件可能已单独发送，请先核实" if file_sent else "消息发送失败"})
                                continue
                        elif not file_sent:
                            continue

                        text_message_id = None
                        if isinstance(sent_resp, dict) and isinstance(sent_resp.get("data"), dict):
                            text_message_id = sent_resp["data"].get("message_id")
                        napcat_message_ids = [x for x in (file_message_id, text_message_id) if x]

                        # Retain sent files for normal cleanup; unlinking by name here could race a replacement.

                        display_content = content if content else (f"[文件] {send_file_name}" if send_file_name else "[图片]")

                        msg_obj = {
                            "message_id": f"local_{datetime.now().timestamp()}", "target_id": target_id, "sender_name": (str(state.self_id) if state.self_id else "我"),
                            "content": display_content, "media_url": local_url, "quote": quote_info,
                            "file_name": send_file_name, "file_size": send_file_size, "file_url": send_file_url,
                            "napcat_message_id": napcat_message_ids[0] if napcat_message_ids else None,
                            "napcat_message_ids": napcat_message_ids,
                            "time": time_str
                        }
                        await save_message(target_id, msg_obj)

                elif data.get("type") == "recall_message":
                    target_id, message_id = data.get("target_id"), data.get("message_id")
                    if not state.napcat_connected:
                        await queue_client_message(ws, {"type": "toast", "content": "NapCat 未连接"})
                        continue

                    target_msg = next((m for m in state.messages.get(target_id, []) if str(m.get("message_id")) == str(message_id)), None)
                    is_mine = bool(target_msg) and (target_msg.get("sender_name") == "我" or (state.self_id and str(target_msg.get("sender_name")) == str(state.self_id)))
                    if target_msg and is_mine:
                        real_ids = target_msg.get("napcat_message_ids") or []
                        if not real_ids and target_msg.get("napcat_message_id"):
                            real_ids = [target_msg.get("napcat_message_id")]
                        if not real_ids:
                            await queue_client_message(ws, {"type": "toast", "content": "该消息不支持撤回"})
                            continue
                        failed = False
                        for real_id in real_ids:
                            resp = await send_qq_api_and_wait("delete_msg", {"message_id": real_id})
                            if not isinstance(resp, dict) or resp.get("status") != "ok" or resp.get("retcode") != 0:
                                failed = True
                                wording = "撤回结果未知，请先确认消息状态；部分消息可能已撤回" if resp is None else "撤回失败，部分消息可能已撤回"
                                await queue_client_message(ws, {"type": "toast", "content": wording})
                                break
                        if not failed:
                            await handle_recall(target_id, message_id)
                    else:
                        await queue_client_message(ws, {"type": "toast", "content": "你只能撤回自己发送的消息"})
    finally:
        state.clients.discard(ws)
        remove_client_sender(ws)
    return ws

async def web_handler(request):
    html = """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>QQ Web Chat</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            ::-webkit-scrollbar { width: 6px; height: 6px; }
            ::-webkit-scrollbar-track { background: transparent; }
            ::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 4px; }
            .context-menu { display: none; position: fixed; background: white; border: 1px solid #e5e7eb; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); z-index: 1000; min-width: 90px; white-space: nowrap; overflow: hidden;}
            .context-menu.show { display: block; }
            .ctx-item { padding: 10px 20px; font-size: 14px; cursor: pointer; color: #374151; transition: background 0.2s; text-align: center;}
            .ctx-item:hover { background: #f3f4f6; }
            .toast { position: fixed; top: 20px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.8); color: white; padding: 10px 20px; border-radius: 8px; font-size: 14px; z-index: 9999; transition: opacity 0.3s; }

            #drop-overlay { position: fixed; inset: 0; background: rgba(59,130,246,0.2); border: 4px dashed #3b82f6; z-index: 9998; display: none; align-items: center; justify-content: center; pointer-events: none; }
            #drop-overlay.show { display: flex; }
            /* 登录标题手写笔画动画 + 写完后发光变粗 */
            #login-title-svg { transition: filter 0.7s ease; opacity: 0; }
            #login-title-svg path { fill: none; stroke: #1f2937; stroke-width: 0.7; stroke-linecap: round; stroke-linejoin: round; transition: stroke-width 0.8s ease; }
            /* 写完变粗：直接上粉光，无蓝光阶段 */
            #login-title-svg.title-pink path,
            #login-title-svg.title-bold path { stroke-width: 1.35; }
            #login-title-svg.title-pink { filter: drop-shadow(0 0 5px rgba(244,114,182,0.40)) drop-shadow(0 0 12px rgba(244,114,182,0.16)); }
            /* 加粗完成后：光完全消失 */
            #login-title-svg.title-bold { filter: none; }
            /* 标题写完后，登录框从下方渐变浮出 */
            #login-card { pointer-events: none; opacity: 0; transform: translateY(34px); animation: cardIn 0.9s cubic-bezier(0.22, 0.61, 0.36, 1) 3s forwards; background-color: rgba(255,255,255,.55); -webkit-backdrop-filter: blur(14px); backdrop-filter: blur(14px); border-color: rgba(255,255,255,.65); }
            @keyframes cardIn { to { opacity: 1; transform: translateY(0); } }

            /* 登录页背景柔光 */
            #login-bg { position: absolute; inset: 0; overflow: hidden; pointer-events: none; z-index: 0; }
            #login-bg .blob { position: absolute; border-radius: 9999px; filter: blur(70px); opacity: .28; animation: blobFloat 16s ease-in-out infinite alternate; }
            #login-bg-img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity 1.8s ease; }
            #login-bg-mask { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(243,244,246,.12), rgba(243,244,246,.26)); }
            #bg-error { position: absolute; top: 14px; left: 16px; z-index: 20; font-size: 12px; color: #ef4444; opacity: 0; transition: opacity .3s ease; pointer-events: none; }
            #bg-error.show { opacity: 1; }
            #login-footer { position: absolute; bottom: 14px; left: 0; right: 0; text-align: center; font-size: 11px; letter-spacing: .02em; color: rgba(31,41,55,.35); pointer-events: none; z-index: 20; }
            #login-canvas { position: absolute; inset: 0; display: block; }
            #login-bg .blob-a { width: 380px; height: 380px; background: rgba(59,130,246,.20); top: -70px; left: -60px; }
            #login-bg .blob-b { width: 420px; height: 420px; background: rgba(244,114,182,.15); bottom: -110px; right: -70px; animation-delay: -8s; }
            @keyframes blobFloat { from { transform: translate3d(0,0,0) scale(1); } to { transform: translate3d(50px,36px,0) scale(1.18); } }

            /* 登录框内容依次浮现 */
            #login-card > * { opacity: 0; transform: translateY(8px); animation: cardItemIn .55s ease forwards; }
            #login-card > h2 { animation-delay: 3.2s; }
            #login-card > input { animation-delay: 3.34s; }
            #login-card > button { animation-delay: 3.46s; }
            #login-card > #login-error { animation-delay: 3.5s; }
            @keyframes cardItemIn { to { opacity: 1; transform: translateY(0); } }

            /* 按钮质感 */
            #login-btn { background-color: rgba(244,114,182,.78); box-shadow: 0 6px 18px -6px rgba(244,114,182,.50); transition: transform .15s ease, box-shadow .25s ease, background-color .25s ease; }
            #login-btn:hover { background-color: rgba(244,114,182,.92); box-shadow: 0 10px 24px -8px rgba(244,114,182,.60); }
            #login-btn:active { transform: scale(.985); }
            /* 深色主题 */
            body.theme-dark { background:#0f1115 !important; color:#e5e7eb !important; }
            body.theme-dark .bg-white { background-color:#171a21 !important; }
            body.theme-dark .bg-gray-50 { background-color:#1b1f27 !important; }
            body.theme-dark .bg-gray-100 { background-color:#0f1115 !important; }
            body.theme-dark .bg-gray-200 { background-color:#252a33 !important; }
            body.theme-dark .text-gray-800 { color:#e5e7eb !important; }
            body.theme-dark .text-gray-700 { color:#dfe3e8 !important; }
            body.theme-dark .text-gray-600 { color:#b6bcc6 !important; }
            body.theme-dark .text-gray-500 { color:#9aa1ac !important; }
            body.theme-dark .text-gray-400 { color:#8a919c !important; }
            body.theme-dark .border-gray-200 { border-color:#2a2f3a !important; }
            body.theme-dark .border-gray-300 { border-color:#363c48 !important; }
            body.theme-dark .hover\\:bg-gray-100:hover { background-color:#20242c !important; }
            body.theme-dark input, body.theme-dark textarea { background-color:#11151b !important; color:#e5e7eb !important; border-color:#2a2f3a !important; }
            body.theme-dark .settings-panel { background-color:#171a21 !important; border-color:#2a2f3a !important; }
            body.chat-bg-on { background-size: cover !important; background-position: center !important; background-attachment: fixed !important; }
            body.chat-bg-on #sidebar .bg-white, body.chat-bg-on #chat-area .bg-white { background-color: rgba(255,255,255,.28) !important; -webkit-backdrop-filter: blur(6px); backdrop-filter: blur(6px); }
            body.chat-bg-on #sidebar .bg-gray-50, body.chat-bg-on #chat-area .bg-gray-50 { background-color: rgba(255,255,255,.08) !important; }
            body.chat-bg-on #sidebar .bg-gray-100, body.chat-bg-on #chat-area .bg-gray-100 { background-color: transparent !important; }
            body.chat-bg-on #sidebar { background-color: rgba(255,255,255,.10) !important; -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px); }
            body.chat-bg-on #sidebar .bg-gray-50 { background-color: transparent !important; }
            body.chat-bg-on #chat-area { background-color: transparent !important; }
            body.chat-bg-on #messages { background: transparent !important; }
            body.theme-dark.chat-bg-on #sidebar .bg-white, body.theme-dark.chat-bg-on #chat-area .bg-white { background-color: rgba(17,21,27,.42) !important; }
            body.theme-dark.chat-bg-on #sidebar .bg-gray-50, body.theme-dark.chat-bg-on #chat-area .bg-gray-50 { background-color: rgba(17,21,27,.18) !important; }
            body.theme-dark.chat-bg-on #sidebar .bg-gray-100, body.theme-dark.chat-bg-on #chat-area .bg-gray-100 { background-color: transparent !important; }
            body.theme-dark.chat-bg-on #sidebar { background-color: rgba(17,21,27,.28) !important; }
            body.chat-bg-on { background-image: var(--chat-wallpaper) !important; }
            #save-wallpaper-btn { position: fixed; top: 12px; right: 12px; z-index: 30; height: 32px; padding: 0 12px; border: 1px solid rgba(156,163,175,.55); border-radius: 8px; background: rgba(255,255,255,.80); color: #374151; font-size: 12px; cursor: pointer; }
            #save-wallpaper-btn[hidden] { display: none !important; }
            #save-wallpaper-btn:disabled { opacity: .55; cursor: default; }
            body.theme-dark #save-wallpaper-btn { background: rgba(23,26,33,.85); color: #e5e7eb; }
            body.wallpaper-save-visible #chat-area > div:first-child { padding-right: 112px; }
            @media (max-width: 767px) { body.wallpaper-save-visible #sidebar > div:first-child { padding-right: 112px; } }
        </style>
    </head>
    <body class="bg-gray-100 h-screen w-screen overflow-hidden flex font-sans text-gray-800">

        <div id="drop-overlay">
            <div class="bg-white p-6 rounded-xl shadow-2xl text-blue-500 font-bold text-xl flex items-center gap-3">
                <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"></path></svg>
                松开鼠标上传文件
            </div>
        </div>

        <div id="login-overlay" class="fixed inset-0 bg-gray-100 z-[9999] flex items-center justify-center px-4">
            <div id="login-bg" aria-hidden="true">
                <img id="login-bg-img" alt="">
                <div id="login-bg-mask"></div>
                <canvas id="login-canvas"></canvas>
                <span class="blob blob-a"></span><span class="blob blob-b"></span>
            </div>
            <div id="bg-error">壁纸加载失败</div>
            <div id="login-footer">@LJY2231 · 保留所有权</div>
            <h1 id="login-title" class="absolute z-10 top-24 md:top-32 left-1/2 -translate-x-1/2 w-[88vw] max-w-[560px] flex justify-center">
    <svg id="login-title-svg" viewBox="0 0 205 26" class="w-auto h-[52px] md:h-[58px]" role="img" aria-label="QQ Web Chat">
          <g>
            <path d="M9,1 L7,2 5,4 4,6 3,9 3,14 4,17 5,19 7,21 9,22 13,22 15,21 17,19 18,17 19,14 19,9 18,6 17,4 15,2 13,1 9,1" transform="translate(4,2)"/>
            <path d="M12,18 L18,24" transform="translate(4,2)"/>
            <path d="M9,1 L7,2 5,4 4,6 3,9 3,14 4,17 5,19 7,21 9,22 13,22 15,21 17,19 18,17 19,14 19,9 18,6 17,4 15,2 13,1 9,1" transform="translate(26,2)"/>
            <path d="M12,18 L18,24" transform="translate(26,2)"/>
            <path d="M2,1 L7,22" transform="translate(60,2)"/>
            <path d="M12,1 L7,22" transform="translate(60,2)"/>
            <path d="M12,1 L17,22" transform="translate(60,2)"/>
            <path d="M22,1 L17,22" transform="translate(60,2)"/>
            <path d="M3,14 L15,14 15,12 14,10 13,9 11,8 8,8 6,9 4,11 3,14 3,16 4,19 6,21 8,22 11,22 13,21 15,19" transform="translate(85,2)"/>
            <path d="M4,1 L4,22" transform="translate(103,2)"/>
            <path d="M4,11 L6,9 8,8 11,8 13,9 15,11 16,14 16,16 15,19 13,21 11,22 8,22 6,21 4,19" transform="translate(103,2)"/>
            <path d="M18,6 L17,4 15,2 13,1 9,1 7,2 5,4 4,6 3,9 3,14 4,17 5,19 7,21 9,22 13,22 15,21 17,19 18,17" transform="translate(134,2)"/>
            <path d="M4,1 L4,22" transform="translate(155,2)"/>
            <path d="M4,12 L7,9 9,8 12,8 14,9 15,12 15,22" transform="translate(155,2)"/>
            <path d="M15,8 L15,22" transform="translate(173,2)"/>
            <path d="M15,11 L13,9 11,8 8,8 6,9 4,11 3,14 3,16 4,19 6,21 8,22 11,22 13,21 15,19" transform="translate(173,2)"/>
            <path d="M5,1 L5,18 6,21 8,22 10,22" transform="translate(191,2)"/>
            <path d="M2,8 L9,8" transform="translate(191,2)"/>
          </g>
        </svg>
            </h1>
            <div id="login-card" class="relative z-10 bg-white p-10 rounded-2xl shadow-xl w-full max-w-md border border-gray-200">
                <h2 class="text-2xl font-bold text-center text-gray-800 mb-6">用户登录</h2>
                <input type="password" id="login-pwd" class="w-full border border-gray-300 rounded-lg px-4 py-3 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-200 transition-all mb-4" placeholder="请输入密码">
                <button id="login-btn" class="w-full bg-blue-500 hover:bg-blue-600 text-white font-bold py-3 rounded-lg transition-colors">登录</button>
                <p id="login-error" class="text-red-500 text-sm mt-3 text-center hidden">密码错误，请重试</p>
            </div>
        </div>

        <div id="context-menu" class="context-menu">
            <div id="ctx-reply" class="ctx-item">引用</div>
            <div id="ctx-recall" class="ctx-item text-red-500 border-t border-gray-100">撤回</div>
        </div>

        <button id="save-wallpaper-btn" type="button" hidden disabled title="下载当前显示的壁纸">保存壁纸</button>

        <!-- 左侧联系人列表 -->
        <div id="sidebar" class="w-full md:w-72 bg-white border-r border-gray-200 flex flex-col shrink-0 h-full absolute md:relative z-20">
            <div class="h-14 border-b border-gray-200 flex items-center px-4 bg-gray-50 shrink-0">
                <div id="status-dot" class="w-3 h-3 rounded-full bg-red-500 mr-2 shadow-sm shrink-0"></div>
                <span id="status-text" class="text-sm font-semibold text-gray-600 truncate">等待连接...</span>
            </div>
            <div id="contact-list" class="flex-1 overflow-y-auto p-2 space-y-1">
                <div class="text-center text-gray-400 text-sm mt-10" id="empty-contact">暂无会话</div>
            </div>
            <div class="border-t border-gray-200 bg-gray-50 px-3 py-2 shrink-0 flex items-center justify-between">
                <button id="logout-btn" type="button" class="px-3 py-1.5 text-xs rounded-md border border-gray-300 text-gray-600 bg-white hover:text-red-500 hover:border-red-300 transition-colors">退出登录</button>
                <button id="refresh-bg-btn" type="button" class="text-xs text-gray-400 hover:text-gray-600 transition-colors">随机壁纸</button>
                <button id="settings-btn" type="button" class="text-xs text-gray-400 hover:text-gray-600 transition-colors">设置</button>
            </div>
        </div>

        <!-- 右侧聊天区域 -->
        <div id="chat-area" class="hidden md:flex flex-1 flex-col h-full bg-gray-50 relative overflow-hidden w-full">
            <div class="h-14 border-b border-gray-200 flex items-center px-4 md:px-6 bg-white shrink-0 shadow-sm z-10">
                <button id="back-btn" class="md:hidden mr-3 text-gray-500 hover:text-gray-700 focus:outline-none">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path></svg>
                </button>
                <span class="text-lg font-bold text-gray-700 truncate" id="current-chat-name">请选择联系人</span>
            </div>

            <div id="message-area" class="flex-1 h-0 overflow-y-auto p-4 md:p-6 space-y-4" onscroll="hideContextMenu()"></div>

            <div id="quote-preview" class="hidden w-full bg-gray-100 border-t border-gray-200 px-4 py-2 flex justify-between items-center shrink-0">
                <div class="text-xs text-gray-500 truncate" id="quote-text">正在引用...</div>
                <button id="cancel-quote" class="text-gray-400 hover:text-gray-600 text-lg ml-2">&times;</button>
            </div>

            <!-- 附件预览区 -->
            <div id="file-preview-area" class="hidden w-full bg-gray-100 border-t border-gray-200 px-4 py-2 flex items-center shrink-0">
                <div class="relative inline-flex items-center bg-white p-2 rounded-lg border border-gray-300 shadow-sm max-w-full pb-3">
                    <img id="preview-img" class="h-12 w-12 rounded object-contain mr-2 hidden">
                    <div id="preview-file-icon" class="w-10 h-10 flex items-center justify-center text-blue-500 mr-2 hidden">
                        <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"></path></svg>
                    </div>
                    <div class="flex flex-col overflow-hidden max-w-[200px] mr-4">
                        <span id="preview-filename" class="text-sm truncate font-medium text-gray-700">filename.ext</span>
                        <span id="preview-filesize" class="text-xs text-gray-400">0 KB</span>
                    </div>
                    <span id="preview-percent" class="text-xs text-blue-500 font-bold absolute right-2 bottom-3 hidden">0%</span>
                    <button id="cancel-file" class="absolute -top-2 -right-2 bg-red-500 text-white rounded-full w-5 h-5 flex items-center justify-center text-xs shadow-md hover:bg-red-600">&times;</button>
                    <!-- 加粗进度条 -->
                    <div class="absolute bottom-0 left-0 w-full h-1.5 bg-gray-200 rounded-b-lg overflow-hidden">
                        <div id="upload-progress-bar" class="h-full bg-blue-500 transition-all duration-300" style="width: 0%;"></div>
                    </div>
                </div>
            </div>

            <!-- 底部工具栏 + 输入框 -->
            <div class="w-full bg-white border-t border-gray-200 px-2 md:px-4 py-2 shrink-0 z-10 flex flex-col gap-1">

                <!-- 工具栏 -->
                <div class="flex items-center space-x-4 px-2 text-gray-500 select-none">
                    <button id="btn-image" class="hover:text-gray-800 transition-colors flex items-center" title="上传图片">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>
                    </button>
                    <button id="btn-file" class="hover:text-gray-800 transition-colors flex items-center" title="上传文件">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"></path></svg>
                    </button>
                    <input type="file" id="image-input" class="hidden" accept="image/*">
                    <input type="file" id="file-input" class="hidden">
                </div>

                <!-- 输入与发送 -->
                <div class="flex items-end bg-gray-50 border border-gray-200 rounded-xl p-1.5">
                    <textarea id="input-box" rows="1" class="flex-1 w-0 bg-transparent outline-none resize-none px-3 py-2 text-sm max-h-32 min-h-[38px]" placeholder="输入消息..." disabled></textarea>
                    <button id="send-btn" class="shrink-0 w-16 h-10 ml-2 bg-blue-500 hover:bg-blue-600 text-white rounded-lg text-sm font-semibold disabled:opacity-50 whitespace-nowrap" disabled>发送</button>
                </div>
            </div>
        </div>

        <script>
            let ws = null;
            let currentTargetId = null;
            let currentMsgType = null;
            let quoteMsg = null;
            let isNapcatOnline = false;
            let myQQ = null;
            const contacts = {};
            let contextMenuMsg = null;

            let authToken = localStorage.getItem('webchat_token');

            let pendingFile = null;
            let currentXHR = null;
            var unreadCounts = {};
            (function loadUnread() {
                try {
                    var raw = localStorage.getItem('webchat_unread');
                    if (raw) {
                        var d = JSON.parse(raw);
                        if (d && typeof d === 'object') { unreadCounts = d; }
                    }
                } catch (e) {}
            })();
            function saveUnread() {
                try { localStorage.setItem('webchat_unread', JSON.stringify(unreadCounts)); } catch (e) {}
            }


            let wsStatus = 'connecting';
            let reconnectTimeout = null;
            let countdownInterval = null;

            const loginOverlay = document.getElementById('login-overlay');
            const loginPwd = document.getElementById('login-pwd');
            const loginBtn = document.getElementById('login-btn');
            const loginError = document.getElementById('login-error');
            const logoutBtn = document.getElementById('logout-btn');
            const settingsBtn = document.getElementById('settings-btn');
            const refreshBgBtn = document.getElementById('refresh-bg-btn');
            const saveWallpaperBtn = document.getElementById('save-wallpaper-btn');
            (function enableCardAfterIntro() {
                const c = document.getElementById('login-card');
                if (!c) return;
                c.addEventListener('animationend', function() { c.style.pointerEvents = 'auto'; });
            })();

            // 手写笔画动画：按笔画顺序逐笔画出标题（总时长 3 秒）
            (function animateTitleStrokes() {
                const svg = document.getElementById('login-title-svg');
                if (!svg) return;
                svg.style.opacity = '1';
                const TOTAL_MS = 3000, GAP = 25;
                const items = [...svg.querySelectorAll('path')]
                    .map((p) => {
                        let len = 0;
                        try { len = p.getTotalLength(); } catch (e) {}
                        return { p, len };
                    })
                    .filter((i) => i.len > 0);
                if (!items.length) return;
                const sum = items.reduce((a, i) => a + i.len, 0);
                const drawBudget = TOTAL_MS - GAP * (items.length - 1);
                setTimeout(() => svg.classList.add('title-pink'), TOTAL_MS + 120);
                setTimeout(() => { svg.classList.remove('title-pink'); svg.classList.add('title-bold'); }, TOTAL_MS + 1500);
                let t = 0;
                items.forEach((i) => {
                    i.p.style.strokeDasharray = i.len;
                    i.p.style.strokeDashoffset = i.len;
                    const dur = Math.max(50, drawBudget * i.len / sum);
                    i.p.animate(
                        [{ strokeDashoffset: i.len }, { strokeDashoffset: 0 }],
                        { duration: dur, delay: t, easing: 'ease-in-out', fill: 'forwards' }
                    );
                    t += dur + GAP;
                });
            })();


            const sidebar = document.getElementById('sidebar');
            const chatArea = document.getElementById('chat-area');
            const backBtn = document.getElementById('back-btn');

            const statusDot = document.getElementById('status-dot');
            const statusText = document.getElementById('status-text');
            const contactList = document.getElementById('contact-list');
            const emptyContact = document.getElementById('empty-contact');
            const currentChatName = document.getElementById('current-chat-name');
            const messageArea = document.getElementById('message-area');
            const inputBox = document.getElementById('input-box');
            const sendBtn = document.getElementById('send-btn');
            const contextMenu = document.getElementById('context-menu');
            const ctxRecall = document.getElementById('ctx-recall');
            const quotePreview = document.getElementById('quote-preview');
            const quoteText = document.getElementById('quote-text');

            const filePreviewArea = document.getElementById('file-preview-area');
            const previewImg = document.getElementById('preview-img');
            const previewFileIcon = document.getElementById('preview-file-icon');
            const previewFilename = document.getElementById('preview-filename');
            const previewFilesize = document.getElementById('preview-filesize');
            const uploadProgressBar = document.getElementById('upload-progress-bar');
            const previewPercent = document.getElementById('preview-percent');

            const imageInput = document.getElementById('image-input');
            const fileInput = document.getElementById('file-input');
            const dropOverlay = document.getElementById('drop-overlay');

            document.addEventListener('dragover', (e) => { e.preventDefault(); });
            document.addEventListener('drop', (e) => { e.preventDefault(); });

            if (chatArea) {
                chatArea.addEventListener('dragover', (e) => {
                    e.preventDefault();
                    dropOverlay.classList.add('show');
                });
                chatArea.addEventListener('dragleave', (e) => {
                    e.preventDefault();
                    dropOverlay.classList.remove('show');
                });
                chatArea.addEventListener('drop', (e) => {
                    e.preventDefault();
                    dropOverlay.classList.remove('show');
                    if (!currentTargetId) { alert('请先在左侧选择联系人'); return; }
                    if (!isNapcatOnline) { alert('NapCat 未连接，无法上传'); return; }
                    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                        const file = e.dataTransfer.files[0];
                        const isImage = file.type.startsWith('image/');
                        handleFileSelect(file, isImage);
                    }
                });
            }

            if (backBtn) {
                backBtn.onclick = () => {
                    chatArea.classList.add('hidden');
                    chatArea.classList.remove('flex');
                    sidebar.classList.remove('hidden');
                    currentTargetId = null;
                };
            }

            if (inputBox) {
                inputBox.addEventListener('input', function() {
                    this.style.height = 'auto';
                    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
                });
            }

            document.getElementById('btn-image').onclick = () => {
                if(!currentTargetId) { alert('请先在左侧选择联系人'); return; }
                if(!isNapcatOnline) { alert('NapCat 未连接'); return; }
                imageInput.click();
            };
            document.getElementById('btn-file').onclick = () => {
                if(!currentTargetId) { alert('请先在左侧选择联系人'); return; }
                if(!isNapcatOnline) { alert('NapCat 未连接'); return; }
                fileInput.click();
            };

            function formatSize(bytes) {
                if (bytes < 1024) return bytes + ' B';
                else if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
                else return (bytes / 1048576).toFixed(1) + ' MB';
            }

            function handleFileSelect(file, isImage) {
                if (!file) return;
                if (file.size > 50 * 1024 * 1024) {
                    alert('文件过大，限制 50MB');
                    return;
                }

                if (currentXHR) {
                    try { currentXHR.abort(); } catch (e) {}
                    currentXHR = null;
                }

                pendingFile = { name: file.name, size: file.size, is_image: isImage, local_path: null };
                previewFilename.innerText = file.name;
                previewFilesize.innerText = formatSize(file.size);

                if (isImage) {
                    previewImg.src = URL.createObjectURL(file);
                    previewImg.classList.remove('hidden');
                    previewFileIcon.classList.add('hidden');
                } else {
                    previewImg.classList.add('hidden');
                    previewFileIcon.classList.remove('hidden');
                }

                uploadProgressBar.style.width = '0%';
                previewPercent.innerText = '准备中...';
                previewPercent.classList.remove('hidden');
                filePreviewArea.classList.remove('hidden');

                const xhr = new XMLHttpRequest();
                currentXHR = xhr;

                xhr.open('POST', '/api/upload', true);

                const formData = new FormData();
                formData.append(isImage ? 'image' : 'file', file);

                xhr.timeout = 60000;

                xhr.upload.onprogress = function(e) {
                    if (e.lengthComputable) {
                        const percent = Math.round((e.loaded / e.total) * 100);
                        uploadProgressBar.style.width = percent + '%';
                        previewPercent.innerText = percent + '%';
                    }
                };

                xhr.onload = function() {
                    if (xhr.status === 200) {
                        try {
                            const data = JSON.parse(xhr.responseText);
                            pendingFile.url = data.url;
                            pendingFile.local_path = data.local_path;
                            uploadProgressBar.style.width = '100%';
                            previewPercent.innerText = '完成';
                            setTimeout(() => {
                                uploadProgressBar.style.width = '0%';
                                previewPercent.classList.add('hidden');
                            }, 1000);
                        } catch (err) {
                            alert('解析服务器响应失败：' + xhr.responseText);
                            cancelFile();
                        }
                    } else {
                        let errMsg = '上传失败，HTTP 状态码: ' + xhr.status;
                        try {
                            const errData = JSON.parse(xhr.responseText);
                            if (errData.error) errMsg += '\\n具体原因: ' + errData.error;
                        } catch(e) {}
                        alert(errMsg);
                        cancelFile();
                    }
                };

                xhr.onerror = function() { alert('上传网络错误或跨域被拦截'); cancelFile(); };
                xhr.ontimeout = function() { alert('上传超时！服务器可能未响应'); cancelFile(); };

                xhr.send(formData);
            }

            imageInput.onchange = (e) => handleFileSelect(e.target.files[0], true);
            fileInput.onchange = (e) => handleFileSelect(e.target.files[0], false);

            document.getElementById('cancel-file').onclick = cancelFile;
            function cancelFile() {
                if (currentXHR) {
                    try { currentXHR.abort(); } catch (e) {}
                    currentXHR = null;
                }
                pendingFile = null;
                filePreviewArea.classList.add('hidden');
                previewImg.src = '';
                imageInput.value = '';
                fileInput.value = '';
            }

            window.addEventListener('paste', async function(e) {
                if (!currentTargetId || !isNapcatOnline) return;
                const clipboardData = e.clipboardData || e.originalEvent.clipboardData;
                let blob = null;

                if (clipboardData.items) {
                    for (let i = 0; i < clipboardData.items.length; i++) {
                        if (clipboardData.items[i].type.indexOf('image') !== -1) {
                            blob = clipboardData.items[i].getAsFile();
                            break;
                        }
                    }
                }
                if (blob) {
                    e.preventDefault();
                    blob.name = 'paste.png';
                    handleFileSelect(blob, true);
                }
            });

            document.addEventListener('click', hideContextMenu);
            function hideContextMenu() { contextMenu.classList.remove('show'); }

            function showToast(msg) {
                let toast = document.createElement('div');
                toast.className = 'toast';
                toast.innerText = msg;
                document.body.appendChild(toast);
                setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 2000);
            }

            // 粉色粒子连线背景（canvas-nest 原版算法）
            (function canvasNest() {
                const host = document.getElementById('login-bg');
                const overlay = document.getElementById('login-overlay');
                if (!host || !overlay) return;

                function l() {
                    return { l: 0, z: 0, o: 0.55, c: '244,114,182', n: 150 };
                }
                function k() {
                    r = u.width = host.clientWidth;
                    n = u.height = host.clientHeight;
                }
                function b() {
                    if (overlay.style.display === 'none') return;
                    requestAnimationFrame(b);
                    if (!r || !n) { k(); return; }
                    e.clearRect(0, 0, r, n);
                    var w = [f].concat(t);
                    var x, v, A, B, z, y;
                    t.forEach(function(i) {
                        i.x += i.xa;
                        i.y += i.ya;
                        i.xa *= i.x > r || i.x < 0 ? -1 : 1;
                        i.ya *= i.y > n || i.y < 0 ? -1 : 1;
                        e.fillStyle = 'rgba(' + s.c + ',0.9)';
                        e.fillRect(i.x - 0.5, i.y - 0.5, 1, 1);
                        for (v = 0; v < w.length; v++) {
                            x = w[v];
                            if (i !== x && null !== x.x && null !== x.y) {
                                B = i.x - x.x;
                                z = i.y - x.y;
                                y = B * B + z * z;
                                if (y < x.max) {
                                    if (x === f && y >= x.max / 2) {
                                        i.x -= 0.03 * B;
                                        i.y -= 0.03 * z;
                                    }
                                    A = (x.max - y) / x.max;
                                    e.beginPath();
                                    e.lineWidth = 0.8 + A * 0.6;
                                    e.strokeStyle = 'rgba(' + s.c + ',' + (A + 0.2) + ')';
                                    e.moveTo(i.x, i.y);
                                    e.lineTo(x.x, x.y);
                                    e.stroke();
                                }
                            }
                        }
                        w.splice(w.indexOf(i), 1);
                    });
                }

                var u = document.createElement('canvas');
                var s = l();
                var e = u.getContext('2d');
                var r, n;
                var f = { x: null, y: null, max: 20000 };
                var a = Math.random;
                u.id = 'c_n' + s.l;
                u.style.cssText = 'position:absolute;top:0;left:0;z-index:0;opacity:' + s.o;
                host.appendChild(u);
                k();
                window.addEventListener('resize', k);
                window.addEventListener('mousemove', function(i) {
                    i = i || window.event;
                    f.x = i.clientX;
                    f.y = i.clientY;
                });
                window.addEventListener('mouseout', function() {
                    f.x = null;
                    f.y = null;
                });
                for (var t = [], p = 0; s.n > p; p++) {
                    var h = a() * r;
                    var g = a() * n;
                    var q = 2 * a() - 1;
                    var d = 2 * a() - 1;
                    t.push({ x: h, y: g, xa: q, ya: d, max: 6000 });
                }
                window.__nestStart = function() { requestAnimationFrame(b); };
                setTimeout(function() { b(); }, 100);
            })();

            function updateWallpaperView() {
                if (!uiCfg) return;
                const img = document.getElementById('login-bg-img');
                if (img) {
                    if (uiCfg.use_image_host && wallpaperUrl) {
                        if (img.getAttribute('src') !== wallpaperUrl) {
                            clearTimeout(wallpaperRevealTimer);
                            img.src = wallpaperUrl;
                        }
                        const wait = Math.max(0, 3000 - performance.now());
                        clearTimeout(wallpaperRevealTimer);
                        if (wait > 0) {
                            const shownUrl = wallpaperUrl;
                            wallpaperRevealTimer = setTimeout(function() {
                                if (uiCfg.use_image_host && wallpaperUrl === shownUrl) img.style.opacity = '1';
                            }, wait);
                        } else {
                            img.style.opacity = '1';
                        }
                    } else {
                        clearTimeout(wallpaperRevealTimer);
                        img.style.opacity = '0';
                        img.removeAttribute('src');
                    }
                }
                if (uiCfg.chat_background && wallpaperUrl) {
                    document.body.classList.add('chat-bg-on');
                    if (document.body.getAttribute('data-chat-background') !== wallpaperUrl) {
                        document.body.style.setProperty('--chat-wallpaper', 'url("' + wallpaperUrl + '")');
                        document.body.setAttribute('data-chat-background', wallpaperUrl);
                    }
                } else {
                    document.body.classList.remove('chat-bg-on');
                    document.body.style.removeProperty('--chat-wallpaper');
                    document.body.removeAttribute('data-chat-background');
                }
                const visible = !!authToken && loginOverlay.style.display === 'none';
                saveWallpaperBtn.hidden = !visible;
                saveWallpaperBtn.disabled = !(visible && uiCfg.chat_background && wallpaperBlob);
                document.body.classList.toggle('wallpaper-save-visible', visible);
                refreshBgBtn.disabled = !!wallpaperRequest;
            }

            function ensureWallpaper(forceRefresh = false) {
                if (!uiCfg.use_image_host && !uiCfg.chat_background) return Promise.resolve(false);
                if (wallpaperRequest) return wallpaperRequest;
                if (wallpaperBlob && !forceRefresh) {
                    updateWallpaperView();
                    return Promise.resolve(true);
                }
                const variant = window.matchMedia('(max-width: 768px)').matches ? 'mobile' : 'desktop';
                const tip = document.getElementById('bg-error');
                if (tip) tip.classList.remove('show');
                wallpaperRequest = (async function() {
                    let candidateUrl = null;
                    try {
                        const response = await fetch('/api/wallpaper?variant=' + variant, {cache: 'no-store'});
                        if (!response.ok) throw new Error('Wallpaper request failed');
                        const blob = await response.blob();
                        if (!blob.size || blob.type.indexOf('image/') !== 0) throw new Error('Invalid wallpaper');
                        candidateUrl = URL.createObjectURL(blob);
                        await new Promise(function(resolve, reject) {
                            const image = new Image();
                            image.onload = resolve;
                            image.onerror = function() { reject(new Error('Wallpaper decode failed')); };
                            image.src = candidateUrl;
                        });
                        const oldUrl = wallpaperUrl;
                        wallpaperBlob = blob;
                        wallpaperUrl = candidateUrl;
                        candidateUrl = null;
                        updateWallpaperView();
                        if (oldUrl) URL.revokeObjectURL(oldUrl);
                        return true;
                    } catch (e) {
                        if (candidateUrl) URL.revokeObjectURL(candidateUrl);
                        if (!wallpaperBlob && uiCfg.use_image_host && tip) tip.classList.add('show');
                        if (forceRefresh) showToast('壁纸加载失败，已保留当前壁纸');
                        return false;
                    } finally {
                        wallpaperRequest = null;
                        updateWallpaperView();
                    }
                })();
                updateWallpaperView();
                return wallpaperRequest;
            }

            function saveCurrentWallpaper() {
                if (!authToken || !uiCfg.chat_background || !wallpaperBlob || saveWallpaperBtn.disabled) return;
                const types = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif', 'image/avif': 'avif'};
                const downloadUrl = URL.createObjectURL(wallpaperBlob);
                const link = document.createElement('a');
                link.href = downloadUrl;
                link.download = 'QQ-Web-Chat-wallpaper-' + Date.now() + '.' + (types[wallpaperBlob.type] || 'img');
                link.style.display = 'none';
                document.body.appendChild(link);
                link.click();
                link.remove();
                setTimeout(function() { URL.revokeObjectURL(downloadUrl); }, 60000);
            }

            async function sha256Hex(text) {
                try {
                    if (window.crypto && window.crypto.subtle && window.crypto.subtle.digest) {
                        const buf = new TextEncoder().encode(text);
                        const d = await window.crypto.subtle.digest('SHA-256', buf);
                        return Array.from(new Uint8Array(d)).map(function(b) { return b.toString(16).padStart(2, '0'); }).join('');
                    }
                } catch (e) {}
                return null;
            }

            async function doLogin() {
                const pwd = loginPwd.value.trim();
                if (!pwd) { alert('请输入密码'); return; }
                loginBtn.innerText = '验证中...';
                loginBtn.disabled = true;
                try {
                    const hash = await sha256Hex(pwd);
                    const res = await fetch('/api/login', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(hash ? {password_hash: hash} : {password: pwd})
                    });

                    if (res.ok) {
                        const data = await res.json();
                        authToken = data.token;
                        if(!authToken) {
                            alert("后端未返回 Token，请检查后端代码是否更新！");
                            return;
                        }
                        localStorage.setItem('webchat_token', authToken);
                        loginOverlay.style.display = 'none';
                        updateWallpaperView();
                        resetSettingsCache();
                        loadSettings();
                        connect();
                    } else {
                        const errData = await res.json().catch(() => ({}));
                        alert('登录失败，状态码: ' + res.status + (errData.msg ? '\\n原因: ' + errData.msg : ''));
                        loginError.classList.remove('hidden');
                        loginPwd.value = '';
                    }
                } catch (e) {
                    alert("登录请求失败！\\n错误信息: " + e.message + "\\n请确认 Nginx 配置正确，或按 F12 查看网络面板");
                    showToast('网络错误');
                } finally {
                    loginBtn.innerText = '登录';
                    loginBtn.disabled = false;
                }
            }

            function doLogout() {
                try {
                    const x = new XMLHttpRequest();
                    x.open('POST', '/api/logout', true);
                    x.setRequestHeader('Content-Type', 'application/json');
                    x.send(JSON.stringify({ token: authToken }));
                } catch (e) {}
                if (ws) {
                    ws.onclose = null;
                    try { ws.close(); } catch (e) {}
                    ws = null;
                }
                clearTimeout(reconnectTimeout);
                clearInterval(countdownInterval);
                if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
                reconnectStartedAt = 0;
                localStorage.removeItem('webchat_token');
                authToken = null;
                resetSettingsCache();
                wsStatus = 'closed';
                currentTargetId = null;
                unreadCounts = {};
                saveUnread();
                quoteMsg = null;
                messageArea.innerHTML = '';
                loginPwd.value = '';
                loginError.classList.add('hidden');
                loginError.innerText = '';
                loginBtn.innerText = '登录';
                loginBtn.disabled = false;
                loginOverlay.style.display = 'flex';
                updateWallpaperView();
                var introCard = document.getElementById('login-card');
                if (introCard) { introCard.style.pointerEvents = 'auto'; }
                if (window.__nestStart) { window.__nestStart(); }
                updateStatusUI();
            }

            if (logoutBtn) logoutBtn.addEventListener('click', doLogout);
            if (settingsBtn) settingsBtn.addEventListener('click', openSettings);
            if (refreshBgBtn) refreshBgBtn.addEventListener('click', async function() {
                if (!uiCfg.chat_background) { showToast('请先在设置中开启聊天背景'); return; }
                if (wallpaperRequest) return;
                if (await ensureWallpaper(true)) showToast('壁纸已刷新');
            });
            if (saveWallpaperBtn) saveWallpaperBtn.addEventListener('click', saveCurrentWallpaper);
            loginBtn.addEventListener('click', doLogin);
            loginPwd.addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });

            if (!authToken) {
                loginOverlay.style.display = 'flex';
            } else {
                loginOverlay.style.display = 'none';
                fetch('/api/restore', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({token:authToken})}).then(() => connect()).catch(() => connect());
            }

            function connect() {
                wsStatus = 'connecting';
                if (!reconnectStartedAt) { updateStatusUI(); }

                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                try {
                    ws = new WebSocket(`${protocol}//${window.location.host}/client_ws?token=${encodeURIComponent(authToken)}`);
                } catch (e) {
                    startReconnect();
                    return;
                }

                ws.onopen = () => {
                    wsStatus = 'open';
                    reconnectStartedAt = 0;
                    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
                    updateStatusUI();
                    clearTimeout(reconnectTimeout);
                    clearInterval(countdownInterval);
                    if (currentTargetId) ws.send(JSON.stringify({type: 'get_history', target_id: currentTargetId}));
                };

                ws.onmessage = (event) => {
                    const data = JSON.parse(event.data);
                    if (data.type === 'status') {
                        isNapcatOnline = data.connected;
                        if (data.self_id) myQQ = data.self_id;
                        updateStatusUI(data.error, data.self_id);
                    }
                    else if (data.type === 'contact_add') addContact(data.contact);
                    else if (data.type === 'message') appendMessage(data);
                    else if (data.type === 'toast') showToast(data.content);
                    else if (data.type === 'clear_history') {
                        if (data.target_id === currentTargetId) messageArea.innerHTML = '';
                    }
                    else if (data.type === 'message_recalled') updateRecallUI(data.message_id, data.sender_name);
                    else if (data.type === 'history_data') {
                        if (data.target_id === currentTargetId) {
                            messageArea.innerHTML = '';
                            data.messages.forEach(msg => appendMessage(msg, false));
                            setTimeout(() => { messageArea.scrollTop = messageArea.scrollHeight; }, 50);
                        }
                    }
                };

                ws.onclose = (e) => {
                    if (e.code === 1008) {
                        localStorage.removeItem('webchat_token');
                        authToken = null;
                        resetSettingsCache();
                        loginOverlay.style.display = 'flex';
                        updateWallpaperView();
                        loginError.classList.remove('hidden');
                        loginError.innerText = "登录已过期，请重新输入密码";
                    } else {
                        wsStatus = 'closed';
                        startReconnect();
                    }
                };
            }

            var reconnectStartedAt = 0;
            var reconnectTimer = null;

            function startReconnect() {
                if (!reconnectStartedAt) { reconnectStartedAt = Date.now(); }
                const elapsed = Date.now() - reconnectStartedAt;
                if (elapsed >= 10000) {
                    statusDot.className = 'w-3 h-3 rounded-full bg-red-500 mr-2 shadow-sm shrink-0';
                    statusText.className = 'text-sm font-semibold text-red-500 truncate';
                    statusText.innerText = 'NapCat 未连接';
                } else if (elapsed >= 5000) {
                    statusDot.className = 'w-3 h-3 rounded-full bg-yellow-500 mr-2 shadow-[0_0_8px_rgba(234,179,8,0.6)] shrink-0';
                    statusText.className = 'text-sm font-semibold text-yellow-600 truncate';
                    statusText.innerText = '连不上服务器，正在重试...';
                }
                if (reconnectTimer) { return; }
                const delay = elapsed >= 10000 ? 5000 : 1000;
                reconnectTimer = setTimeout(() => {
                    reconnectTimer = null;
                    if (authToken) { connect(); }
                }, delay);
            }

            function updateStatusUI(errorMsg = null, selfId = null, countdown = null) {
                if (wsStatus === 'closed' || wsStatus === 'connecting' || wsStatus === 'timeout') {
                    statusDot.className = 'w-3 h-3 rounded-full bg-yellow-500 mr-2 shadow-[0_0_8px_rgba(234,179,8,0.6)] shrink-0';
                    statusText.className = 'text-sm font-semibold text-yellow-600 truncate';
                    if (countdown !== null) {
                        if (countdown > 0) {
                            statusText.innerText = `正在重连服务器... (${countdown}s)`;
                        } else {
                            statusText.innerText = '服务端异常，请刷新网页';
                            statusDot.className = 'w-3 h-3 rounded-full bg-red-500 mr-2 shadow-sm shrink-0';
                        }
                    } else {
                        statusText.innerText = '正在连接服务器...';
                    }
                    return;
                }

                if (isNapcatOnline) {
                    statusDot.className = 'w-3 h-3 rounded-full bg-green-500 mr-2 shadow-[0_0_8px_rgba(34,197,94,0.6)] shrink-0';
                    statusText.className = 'text-sm font-semibold text-gray-600 truncate';
                    if (selfId) {
                        statusText.innerText = `NapCat已连接，后端QQ号：${selfId}`;
                    } else {
                        statusText.innerText = 'NapCat已连接，获取QQ号中...';
                    }
                } else {
                    statusDot.className = 'w-3 h-3 rounded-full bg-red-500 mr-2 shadow-[0_0_8px_rgba(239,68,68,0.6)] shrink-0';
                    statusText.className = 'text-sm font-semibold text-red-500 truncate';
                    if (errorMsg) {
                        statusText.innerText = `NapCat 异常: ${errorMsg}`;
                    } else {
                        statusText.innerText = 'NapCat未连接';
                    }
                }
            }

            function isSelfSender(name) {
                if (name === '我') return true;
                return myQQ !== null && String(name) === String(myQQ);
            }

            function addContact(contact) {
                contacts[contact.id] = contact;
                if (unreadCounts[contact.id] === undefined) unreadCounts[contact.id] = 0;
                if (emptyContact) emptyContact.style.display = 'none';

                let div = document.getElementById(`contact-${contact.id}`);
                if (!div) {
                    div = document.createElement('div');
                    div.className = 'flex items-center p-3 rounded-lg cursor-pointer hover:bg-gray-100 transition-colors relative';
                    div.id = `contact-${contact.id}`;
                    div.onclick = () => switchChat(contacts[contact.id]);
                    contactList.appendChild(div);
                }

                const avatarText = escapeHTML((contact.name || '?').charAt(0));
                const avatarHTML = contact.avatar
                    ? `<img src="${escapeHTML(contact.avatar)}" alt="" referrerpolicy="no-referrer" class="w-10 h-10 rounded-full object-cover bg-gray-200" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';"><div class="w-10 h-10 rounded-full bg-blue-500 text-white items-center justify-center font-bold hidden">${avatarText}</div>`
                    : `<div class="w-10 h-10 rounded-full bg-blue-500 text-white flex items-center justify-center font-bold">${avatarText}</div>`;

                div.innerHTML = `
                    <div class="relative shrink-0 mr-3">
                        ${avatarHTML}
                        <span id="unread-${contact.id}" class="hidden absolute -top-1 -right-1 bg-red-500 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full border-2 border-white shadow-sm"></span>
                    </div>
                    <div class="flex-1 overflow-hidden">
                        <div class="text-sm font-semibold truncate text-gray-700">${escapeHTML(contact.name)}</div>
                        <div class="text-xs text-gray-400 truncate">ID: ${escapeHTML(contact.id)}</div>
                    </div>
                `;
                updateUnreadBadge(contact.id);
                if (contact.id === currentTargetId) currentChatName.innerText = contact.name || '';
            }

            function updateUnreadBadge(contactId) {
                const badge = document.getElementById(`unread-${contactId}`);
                if (badge) {
                    const count = unreadCounts[contactId] || 0;
                    if (count > 0) {
                        badge.innerText = count > 99 ? '99+' : count;
                        badge.classList.remove('hidden');
                    } else {
                        badge.classList.add('hidden');
                    }
                }
            }

            function switchChat(contact) {
                currentTargetId = contact.id;
                currentMsgType = contact.type;
                currentChatName.innerText = contact.name;
                cancelQuote();
                cancelFile();

                unreadCounts[contact.id] = 0;
                updateUnreadBadge(contact.id);
                saveUnread();

                if (window.innerWidth < 768) {
                    sidebar.classList.add('hidden');
                    chatArea.classList.remove('hidden');
                    chatArea.classList.add('flex');
                }

                document.querySelectorAll('#contact-list > div').forEach(el => el.classList.remove('bg-blue-50', 'border-l-4', 'border-blue-500'));
                const activeEl = document.getElementById(`contact-${contact.id}`);
                if (activeEl) activeEl.classList.add('bg-blue-50', 'border-l-4', 'border-blue-500');

                messageArea.innerHTML = '<div class="text-center text-gray-400 text-sm mt-4">加载中...</div>';
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: 'get_history', target_id: currentTargetId }));
                }

                inputBox.disabled = false;
                sendBtn.disabled = false;
                inputBox.focus();
            }

            function appendMessage(data, scroll = true) {
                if (!isSelfSender(data.sender_name) && scroll !== false) { playNotifySound(); }
                if (data.target_id !== currentTargetId) {
                    if (!isSelfSender(data.sender_name)) {
                        if (!unreadCounts[data.target_id]) unreadCounts[data.target_id] = 0;
                        unreadCounts[data.target_id]++;
                        updateUnreadBadge(data.target_id);
                        saveUnread();
                    }
                    return;
                }

                if (messageArea.innerText.includes('加载中')) messageArea.innerHTML = '';

                const isSelf = isSelfSender(data.sender_name);
                const row = document.createElement('div');
                row.className = `w-full flex mb-4 ${isSelf ? 'justify-end' : 'justify-start'}`;
                row.id = `msg-${data.message_id}`;

                if (data.is_recalled) {
                    row.className = 'w-full flex justify-center mb-4';
                    row.innerHTML = `<div class="text-xs text-gray-400 bg-gray-200 px-3 py-1 rounded-full">${escapeHTML(data.content)}</div>`;
                    messageArea.appendChild(row);
                    if(scroll) messageArea.scrollTop = messageArea.scrollHeight;
                    return;
                }

                const col = document.createElement('div');
                col.className = `flex flex-col ${isSelf ? 'items-end' : 'items-start'} max-w-[85%] md:max-w-[75%]`;

                const meta = document.createElement('div');
                meta.className = 'text-[10px] md:text-xs text-gray-400 mb-1 px-1 whitespace-nowrap';
                meta.innerText = `${isSelf ? (myQQ !== null ? String(myQQ) : '我') : data.sender_name} · ${data.time}`;

                const bubble = document.createElement('div');
                const isImage = !!data.media_url && !data.file_name;
                bubble.className = `rounded-2xl text-sm break-words shadow-sm whitespace-pre-wrap cursor-context-menu ${
                    isImage ? 'p-1 bg-transparent border-none shadow-none' :
                    (isSelf ? 'bg-blue-500 text-white rounded-tr-sm px-3 py-1.5 md:px-4 md:py-2' : 'bg-white border border-gray-200 text-gray-800 rounded-tl-sm px-3 py-1.5 md:px-4 md:py-2')
                }`;
                bubble.style.width = 'fit-content';
                bubble.style.maxWidth = '100%';
                bubble.style.wordBreak = 'break-word';

                if (data.quote) {
                    let quoteDiv = document.createElement('div');
                    quoteDiv.className = 'mb-2 px-2 py-1.5 rounded text-xs border-l-4 border-gray-400/50 bg-black/10 opacity-90 truncate cursor-pointer hover:opacity-100 transition-opacity';
                    quoteDiv.innerHTML = `<span class="font-semibold">${escapeHTML(data.quote.sender_name)}</span>: ${escapeHTML(data.quote.content)}`;
                    quoteDiv.onclick = (e) => {
                        e.stopPropagation();
                        const targetRow = document.getElementById(`msg-${data.quote.id}`);
                        if (targetRow) {
                            targetRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            targetRow.style.transition = 'background-color 0.3s';
                            targetRow.style.backgroundColor = 'rgba(59, 130, 246, 0.1)';
                            setTimeout(() => { targetRow.style.backgroundColor = 'transparent'; }, 1500);
                        } else {
                            showToast('引用的消息已不在当前会话中');
                        }
                    };
                    bubble.appendChild(quoteDiv);
                }

                if (isImage) {
                    let extraText = data.content.replace(/\\[图片\\]/g, "").replace(/\\[表情包\\]/g, "").trim();
                    let img = document.createElement('img');
                    img.className = 'max-w-full rounded-md object-contain max-h-[200px] md:max-h-60';
                    const imgSrc = data.media_url.startsWith('/files/') ? data.media_url : `/proxy_img?url=${encodeURIComponent(data.media_url)}`;
                    let holder = document.createElement('div');
                    holder.className = 'px-4 py-3 bg-gray-100 text-gray-400 text-sm rounded-lg border border-gray-200 text-center w-32 h-32 flex items-center justify-center';
                    holder.innerText = '加载中...';
                    bubble.appendChild(holder);
                    fetch(imgSrc).then(resp => { if (!resp.ok) throw new Error('http'); return resp.blob(); }).then(blob => {
                        img.onload = () => { holder.replaceWith(img); };
                        img.onerror = () => { holder.innerText = '[图片已失效]'; };
                        img.src = URL.createObjectURL(blob);
                    }).catch(() => { holder.innerText = '[图片已失效]'; });
                    if (extraText) {
                        let textDiv = document.createElement('div');
                        textDiv.className = `mt-1 px-2 py-1 ${isSelf ? 'bg-blue-500 text-white rounded-lg' : 'bg-white text-gray-800 rounded-lg border border-gray-200'}`;
                        textDiv.innerText = extraText;
                        bubble.appendChild(textDiv);
                    }
                } else if (data.file_name) {
                    let fileRow = document.createElement('div');
                    fileRow.className = 'flex items-center gap-2 py-0.5';
                    let iconWrap = document.createElement('div');
                    iconWrap.className = `w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${isSelf ? 'bg-white/20' : 'bg-blue-50'}`;
                    iconWrap.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-5 h-5 ${isSelf ? 'text-white' : 'text-blue-500'}"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>`;
                    let info = document.createElement('div');
                    info.className = 'min-w-0 flex-1 max-w-[200px] md:max-w-[280px]';
                    let nameEl;
                    if (typeof data.file_url === 'string' && (/^https?:\\/\\//i.test(data.file_url) || /^\\/files\\/[a-zA-Z0-9_.-]+$/.test(data.file_url))) {
                        nameEl = document.createElement('a');
                        if (/^https?:/i.test(data.file_url)) {
                            nameEl.href = `/download?url=${encodeURIComponent(data.file_url)}&name=${encodeURIComponent(data.file_name || 'file')}`;
                        } else {
                            nameEl.href = data.file_url;
                        }
                        nameEl.target = '_blank';
                        nameEl.rel = 'noopener';
                        nameEl.download = data.file_name;
                        nameEl.className = `block text-sm font-medium truncate ${isSelf ? 'text-white hover:underline' : 'text-gray-800 hover:text-blue-600 hover:underline'}`;
                    } else {
                        nameEl = document.createElement('span');
                        nameEl.className = `block text-sm font-medium truncate ${isSelf ? 'text-white' : 'text-gray-800'}`;
                    }
                    nameEl.title = data.file_name;
                    nameEl.textContent = data.file_name;
                    info.appendChild(nameEl);
                    let sizeEl = document.createElement('div');
                    sizeEl.className = `text-[11px] ${isSelf ? 'text-white/70' : 'text-gray-400'}`;
                    sizeEl.textContent = data.file_size ? formatSize(data.file_size) : '文件';
                    info.appendChild(sizeEl);
                    fileRow.appendChild(iconWrap);
                    fileRow.appendChild(info);
                    bubble.appendChild(fileRow);
                    if (data.content && data.content.indexOf('[文件]') !== 0) {
                        let extra = document.createElement('div');
                        extra.className = 'mt-1 whitespace-pre-wrap text-sm';
                        extra.innerText = data.content;
                        bubble.appendChild(extra);
                    }
                } else {
                    let textDiv = document.createElement('div');
                    textDiv.className = 'whitespace-pre-wrap';
                    textDiv.innerText = data.content;
                    bubble.appendChild(textDiv);
                }

                bubble.addEventListener('contextmenu', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    contextMenuMsg = data;

                    if (isSelf) {
                        ctxRecall.style.display = 'block';
                    } else {
                        ctxRecall.style.display = 'none';
                    }

                    contextMenu.style.visibility = 'hidden';
                    contextMenu.classList.add('show');

                    const menuWidth = contextMenu.offsetWidth;
                    const menuHeight = contextMenu.offsetHeight;

                    let left = e.clientX;
                    let top = e.clientY;

                    if (isSelf) left = e.clientX - menuWidth;

                    if (left + menuWidth > window.innerWidth) left = left - menuWidth;
                    if (top + menuHeight > window.innerHeight) top = top - menuHeight;

                    contextMenu.style.left = left + 'px';
                    contextMenu.style.top = top + 'px';
                    contextMenu.style.visibility = 'visible';
                });

                col.appendChild(meta);
                col.appendChild(bubble);
                row.appendChild(col);
                messageArea.appendChild(row);
                if(scroll) messageArea.scrollTop = messageArea.scrollHeight;
            }

            function updateRecallUI(messageId, senderName) {
                const row = document.getElementById(`msg-${messageId}`);
                if (row) {
                    const name = senderName || '某人';
                    row.className = 'w-full flex justify-center mb-4';
                    row.innerHTML = `<div class="text-xs text-gray-400 bg-gray-200 px-3 py-1 rounded-full">「${escapeHTML(name)}」撤回了一条消息</div>`;
                }
            }

            document.getElementById('ctx-reply').onclick = () => {
                if (!contextMenuMsg) return;
                quoteMsg = contextMenuMsg;
                quoteText.innerText = `引用: ${quoteMsg.content}`;
                quotePreview.classList.remove('hidden');
                inputBox.focus();
                hideContextMenu();
            };

            ctxRecall.onclick = () => {
                if (!contextMenuMsg) return;
                ws.send(JSON.stringify({
                    type: 'recall_message',
                    target_id: currentTargetId,
                    message_id: contextMenuMsg.message_id
                }));
                hideContextMenu();
            };

            function cancelQuote() {
                quoteMsg = null;
                quotePreview.classList.add('hidden');
            }
            document.getElementById('cancel-quote').onclick = cancelQuote;

            function escapeHTML(str) {
                return str.replace(/[&<>'"]/g, tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag));
            }

            function sendMessage() {
                const content = inputBox.value.trim();
                if (!currentTargetId || !ws || ws.readyState !== WebSocket.OPEN) return;

                if (!content && !pendingFile) return;

                if (!isNapcatOnline) {
                    alert('NapCat 未连接，请检查配置');
                    return;
                }

                if (pendingFile && !pendingFile.local_path) {
                    alert('文件还在上传中，请等待进度条走完！');
                    return;
                }

                let quoteObj = null;
                if (quoteMsg) {
                    quoteObj = {
                        "id": quoteMsg.message_id,
                        "napcat_id": quoteMsg.napcat_message_id || null,
                        "sender_name": quoteMsg.sender_name,
                        "content": quoteMsg.content
                    };
                }

                ws.send(JSON.stringify({
                    type: 'send_message',
                    target_id: currentTargetId,
                    msg_type: currentMsgType,
                    content: content,
                    quote: quoteObj,
                    file_local_path: pendingFile ? pendingFile.local_path : null,
                    file_orig_name: pendingFile ? pendingFile.name : null,
                    file_is_image: pendingFile ? pendingFile.is_image : false
                }));

                inputBox.value = '';
                inputBox.style.height = '38px';
                cancelQuote();
                cancelFile();
            }

            sendBtn.addEventListener('click', sendMessage);
            inputBox.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendMessage();
                }
            });

            var uiCfg = { use_image_host: true, theme: 'light', theme_auto: false, dark_start: '20:00', dark_end: '07:00', chat_background: false, sound_enabled: true };
            var settingsData = null, settingsTab = 'whitelist';
            var savedSettings = null, settingsRequest = null, settingsLoading = false;
            var settingsLoadError = '', uiSettingsRequest = null;
            var wallpaperBlob = null, wallpaperUrl = null, wallpaperRequest = null;
            var wallpaperRevealTimer = null;

            function parseHM(v) {
                var p = String(v || '00:00').split(':');
                return (parseInt(p[0], 10) || 0) * 60 + (parseInt(p[1], 10) || 0);
            }
            function inDarkRange() {
                var now = new Date();
                var cur = now.getHours() * 60 + now.getMinutes();
                var a = parseHM(uiCfg.dark_start), b = parseHM(uiCfg.dark_end);
                if (a === b) return false;
                if (a < b) return cur >= a && cur < b;
                return cur >= a || cur < b;
            }
            function applyTheme() {
                var dark = uiCfg.theme === 'dark';
                if (uiCfg.theme_auto) { dark = inDarkRange(); }
                if (dark) { document.body.classList.add('theme-dark'); } else { document.body.classList.remove('theme-dark'); }
            }
            function applyUiCfg(cfg) {
                if (!cfg) return;
                uiCfg.use_image_host = cfg.use_image_host !== false;
                uiCfg.theme = cfg.theme === 'dark' ? 'dark' : 'light';
                uiCfg.theme_auto = !!cfg.theme_auto;
                uiCfg.dark_start = cfg.dark_start || '20:00';
                uiCfg.dark_end = cfg.dark_end || '07:00';
                uiCfg.chat_background = !!cfg.chat_background;
                uiCfg.sound_enabled = cfg.sound_enabled !== false;
                applyTheme();
                updateWallpaperView();
                ensureWallpaper();
            }
            (function initUiCfg() {
                var x = new XMLHttpRequest();
                uiSettingsRequest = x;
                function finish() {
                    if (uiSettingsRequest !== x) return;
                    uiSettingsRequest = null;
                    if (x.status === 200) {
                        try { applyUiCfg(JSON.parse(x.responseText)); return; } catch (e) {}
                    }
                    applyUiCfg(uiCfg);
                }
                try {
                    x.open('GET', '/api/ui_settings', true);
                    x.timeout = 8000;
                    x.onload = finish;
                    x.onerror = finish;
                    x.ontimeout = finish;
                    x.send();
                } catch (e) { finish(); }
            })();
            if (authToken) { loadSettings(); }
            setInterval(function() { if (uiCfg.theme_auto) { applyTheme(); } }, 30000);

            (function preloadNotifySound() {
                try {
                    notifyAudio = new Audio('/static/qq_notify.mp3');
                    notifyAudio.preload = 'auto';
                    try { notifyAudio.load(); } catch (e) {}
                    try { fetch('/static/qq_notify.mp3', { cache: 'force-cache' }).catch(function() {}); } catch (e) {}
                } catch (e) {}
            })();

            var notifyAudio = null;
            function playNotifySound() {
                if (!uiCfg.sound_enabled) return;
                try {
                    if (!notifyAudio) { notifyAudio = new Audio('/static/qq_notify.mp3'); }
                    notifyAudio.currentTime = 0;
                    var p = notifyAudio.play();
                    if (p && p.catch) { p.catch(function() {}); }
                } catch (e) {}
            }
            function testNotifySound() { playNotifySound(); }
            function openSettings() {
                var m = document.getElementById('settings-modal');
                if (!m) return;
                if (!m.getAttribute('data-bound')) {
                    m.setAttribute('data-bound', '1');
                    var mask = document.getElementById('settings-mask');
                    var cancel = document.getElementById('settings-cancel');
                    var save = document.getElementById('settings-save');
                    if (mask) mask.onclick = closeSettings;
                    if (cancel) cancel.onclick = closeSettings;
                    if (save) save.onclick = saveSettings;
                }
                var alreadyOpen = !m.classList.contains('hidden');
                m.classList.remove('hidden');
                updateSettingsTabs();
                if (!alreadyOpen || !settingsData) {
                    settingsData = savedSettings ? JSON.parse(JSON.stringify(savedSettings)) : null;
                }
                if (settingsData) {
                    renderSettingsTab(settingsTab);
                } else {
                    renderSettingsLoading();
                    loadSettings();
                }
            }
            function closeSettings() {
                var m = document.getElementById('settings-modal');
                if (m) m.classList.add('hidden');
                settingsData = null;
            }
            function renderSettingsLoading() {
                var el = document.getElementById('settings-body');
                if (!el) return;
                if (settingsLoadError) {
                    el.innerHTML = '<p class="text-sm text-red-500 mb-3">' + escapeAttr(settingsLoadError) + '</p>'
                        + '<button type="button" id="settings-retry" class="px-3 py-1.5 text-sm rounded-md border border-gray-300 text-gray-600">重试</button>';
                    document.getElementById('settings-retry').onclick = function() { loadSettings(); };
                } else {
                    el.innerHTML = '<p class="text-sm text-gray-400" role="status">正在加载设置…</p>';
                }
                document.getElementById('settings-save').disabled = true;
            }
            function resetSettingsCache() {
                var pending = settingsRequest;
                settingsRequest = null;
                settingsLoading = false;
                if (pending) pending.abort();
                savedSettings = null;
                settingsLoadError = '';
                closeSettings();
            }
            function updateSettingsTabs() {
                var tabs = document.querySelectorAll('.set-tab');
                for (var i = 0; i < tabs.length; i++) {
                    if (tabs[i].getAttribute('data-tab') === settingsTab) {
                        tabs[i].className = 'set-tab w-full text-left px-3 py-2 rounded-lg text-sm mb-1 bg-blue-50 text-blue-600 font-semibold';
                    } else {
                        tabs[i].className = 'set-tab w-full text-left px-3 py-2 rounded-lg text-sm mb-1 text-gray-600 hover:bg-gray-100';
                    }
                }
            }
            function switchSettingsTab(tab) {
                collectSettingsTab();
                settingsTab = tab;
                if (settingsData) { renderSettingsTab(tab); } else { renderSettingsLoading(); }
                updateSettingsTabs();
            }
            function loadSettings() {
                if (!authToken || settingsLoading) return;
                var requestToken = authToken;
                var x = new XMLHttpRequest();
                settingsRequest = x;
                settingsLoading = true;
                settingsLoadError = '';
                function isOpen() {
                    var modal = document.getElementById('settings-modal');
                    return modal && !modal.classList.contains('hidden');
                }
                if (isOpen() && !settingsData) renderSettingsLoading();
                function fail(message) {
                    if (settingsRequest !== x) return;
                    settingsRequest = null;
                    settingsLoading = false;
                    if (authToken !== requestToken) return;
                    settingsLoadError = message;
                    if (isOpen()) renderSettingsLoading();
                }
                try {
                    x.open('GET', '/api/settings?token=' + encodeURIComponent(requestToken), true);
                    x.timeout = 8000;
                    x.onload = function() {
                        if (settingsRequest !== x) return;
                        if (authToken !== requestToken) { fail('登录状态已变化，请重新打开设置'); return; }
                        if (x.status !== 200) {
                            fail(x.status === 401 ? '登录已过期，请重新登录' : '设置加载失败，请重试');
                            return;
                        }
                        var cfg;
                        try {
                            cfg = JSON.parse(x.responseText);
                            if (!cfg || typeof cfg !== 'object' || Array.isArray(cfg)) throw new Error('Invalid settings');
                        } catch (e) { fail('设置数据无效，请重试'); return; }
                        settingsRequest = null;
                        settingsLoading = false;
                        settingsLoadError = '';
                        savedSettings = cfg;
                        if (isOpen()) {
                            settingsData = JSON.parse(JSON.stringify(savedSettings));
                            renderSettingsTab(settingsTab);
                            updateSettingsTabs();
                        }
                    };
                    x.onerror = function() { fail('设置加载失败，请检查网络后重试'); };
                    x.ontimeout = function() { fail('设置加载超时，请重试'); };
                    x.send();
                } catch (e) { fail('设置加载失败，请重试'); }
            }
            function escapeAttr(v) {
                return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
            }
            function renderSettingsTab(tab) {
                if (!settingsData) return;
                var el = document.getElementById('settings-body');
                if (!el) return;
                var h = '';
                if (tab === 'whitelist') {
                    h = '<div class="text-sm font-semibold text-gray-800 mb-2">白名单设置</div>'
                      + '<p class="text-xs text-gray-400 mb-3">只有这些 QQ 号 / 群号的消息才会被处理，多个用分号（;）分割。</p>'
                      + '<textarea id="set-whitelist" class="w-full h-40 border border-gray-300 rounded-lg p-3 text-sm outline-none focus:border-blue-500" placeholder="123456;789012">' + escapeAttr((settingsData.whitelist || []).join(';')) + '</textarea>';
                } else if (tab === 'blacklist') {
                    h = '<div class="text-sm font-semibold text-gray-800 mb-2">黑名单设置</div>'
                      + '<p class="text-xs text-gray-400 mb-3">这些 QQ 号 / 群号的消息会被完全忽略（不回复、不转发）。多个用分号（;）分割。</p>'
                      + '<textarea id="set-blacklist" class="w-full h-40 border border-gray-300 rounded-lg p-3 text-sm outline-none focus:border-blue-500" placeholder="QQ ID">' + escapeAttr((settingsData.blacklist || []).join(';')) + '</textarea>';
                } else if (tab === 'background') {
                    var yes = settingsData.use_image_host !== false;
                    var chatBg = !!settingsData.chat_background;
                    h = '<div class="text-sm font-semibold text-gray-800 mb-2">背景设置</div>'
                      + '<p class="text-xs text-gray-400 mb-3">登录页背景是否从图床随机加载。</p>'
                      + '<div class="mb-4"><span class="text-sm mr-4">使用图床：</span>'
                      + '<label class="inline-flex items-center mr-6"><input type="radio" name="set-bg" value="yes" ' + (yes ? 'checked' : '') + '> <span class="ml-2 text-sm">是</span></label>'
                      + '<label class="inline-flex items-center"><input type="radio" name="set-bg" value="no" ' + (!yes ? 'checked' : '') + '> <span class="ml-2 text-sm">否</span></label></div>'
                      + '<div><span class="text-sm mr-4">聊天背景：</span>'
                      + '<label class="inline-flex items-center mr-6"><input type="radio" name="set-chat-bg" value="yes" ' + (chatBg ? 'checked' : '') + '> <span class="ml-2 text-sm">是</span></label>'
                      + '<label class="inline-flex items-center"><input type="radio" name="set-chat-bg" value="no" ' + (!chatBg ? 'checked' : '') + '> <span class="ml-2 text-sm">否</span></label></div>'
                      + '<p class="text-xs text-gray-400 mt-3">聊天背景默认关闭；开启后聊天区域显示图床图片。</p>';
                } else if (tab === 'sound') {
                    var snd = settingsData.sound_enabled !== false;
                    h = '<div class="text-sm font-semibold text-gray-800 mb-2">提示音设置</div>'
                      + '<p class="text-xs text-gray-400 mb-3">收到新消息时播放一次 QQ 提示音。</p>'
                      + '<label class="inline-flex items-center mr-6"><input type="radio" name="set-sound" value="yes" ' + (snd ? 'checked' : '') + '> <span class="ml-2 text-sm">开启</span></label>'
                      + '<label class="inline-flex items-center mr-6"><input type="radio" name="set-sound" value="no" ' + (!snd ? 'checked' : '') + '> <span class="ml-2 text-sm">关闭</span></label>'
                      + '<button type="button" class="px-3 py-1.5 text-sm rounded-md border border-gray-300 text-gray-600 hover:bg-gray-50" onclick="testNotifySound()">试听</button>';
                } else if (tab === 'color') {
                    var dark = settingsData.theme === 'dark';
                    var auto = !!settingsData.theme_auto;
                    h = '<div class="text-sm font-semibold text-gray-800 mb-3">颜色设置</div>'
                      + '<div class="mb-4"><span class="text-sm mr-4">主题：</span>'
                      + '<label class="inline-flex items-center mr-6"><input type="radio" name="set-theme" value="light" ' + (!dark ? 'checked' : '') + '> <span class="ml-2 text-sm">白</span></label>'
                      + '<label class="inline-flex items-center"><input type="radio" name="set-theme" value="dark" ' + (dark ? 'checked' : '') + '> <span class="ml-2 text-sm">黑</span></label></div>'
                      + '<label class="inline-flex items-center mb-3"><input type="checkbox" id="set-theme-auto" ' + (auto ? 'checked' : '') + '> <span class="ml-2 text-sm">自定义切换时间段</span></label>'
                      + '<div class="flex items-center gap-3 ' + (auto ? '' : 'opacity-50') + '" id="set-theme-range">'
                      + '<span class="text-sm">黑色时间段</span>'
                      + '<input type="time" id="set-dark-start" value="' + escapeAttr(settingsData.dark_start || '20:00') + '" ' + (auto ? '' : 'disabled') + ' class="border border-gray-300 rounded-md px-2 py-1 text-sm">'
                      + '<span class="text-sm">至</span>'
                      + '<input type="time" id="set-dark-end" value="' + escapeAttr(settingsData.dark_end || '07:00') + '" ' + (auto ? '' : 'disabled') + ' class="border border-gray-300 rounded-md px-2 py-1 text-sm">'
                      + '</div>'
                      + '<p class="text-xs text-gray-400 mt-3">启用后，黑色时间段内自动使用黑色主题，其余时间为白色主题；未启用则手动选择。</p>';
                } else {
                    h = '<div class="text-lg font-bold text-gray-800 mb-3">关于 QQ Web Chat</div>'
                      + '<div class="text-sm text-gray-600 space-y-2">'
                      + '<p>Copyright © 2026 LJY2231 · 保留所有权</p>'
                      + '<p>基于 Web + WS 技术架构</p>'
                      + '</div>';
                }
                el.innerHTML = h;
                document.getElementById('settings-save').disabled = false;
                if (tab === 'color') {
                    var autoEl = document.getElementById('set-theme-auto');
                    if (autoEl) {
                        autoEl.onchange = function() {
                            var range = document.getElementById('set-theme-range');
                            var ds = document.getElementById('set-dark-start');
                            var de = document.getElementById('set-dark-end');
                            if (this.checked) {
                                range.classList.remove('opacity-50'); ds.disabled = false; de.disabled = false;
                            } else {
                                range.classList.add('opacity-50'); ds.disabled = true; de.disabled = true;
                            }
                        };
                    }
                }
            }
            function collectSettingsTab() {
                if (!settingsData) return;
                if (settingsTab === 'whitelist') {
                    var a = document.getElementById('set-whitelist'); if (a) settingsData.whitelist = a.value.replace(/；/g, ';').split(';').map(function(id) { return id.trim(); }).filter(Boolean);
                } else if (settingsTab === 'blacklist') {
                    var b = document.getElementById('set-blacklist'); if (b) settingsData.blacklist = b.value.replace(/；/g, ';').split(';').map(function(id) { return id.trim(); }).filter(Boolean);
                } else if (settingsTab === 'background') {
                    var r = document.querySelector('input[name="set-bg"]:checked');
                    if (r) settingsData.use_image_host = (r.value === 'yes');
                    var cb = document.querySelector('input[name="set-chat-bg"]:checked');
                    if (cb) settingsData.chat_background = (cb.value === 'yes');
                } else if (settingsTab === 'sound') {
                    var sr = document.querySelector('input[name="set-sound"]:checked');
                    if (sr) settingsData.sound_enabled = (sr.value === 'yes');
                } else if (settingsTab === 'color') {
                    var t = document.querySelector('input[name="set-theme"]:checked');
                    if (t) settingsData.theme = t.value;
                    var auto = document.getElementById('set-theme-auto'); if (auto) settingsData.theme_auto = auto.checked;
                    var s1 = document.getElementById('set-dark-start'); if (s1) settingsData.dark_start = s1.value;
                    var e1 = document.getElementById('set-dark-end'); if (e1) settingsData.dark_end = e1.value;
                }
            }
            function saveSettings() {
                collectSettingsTab();
                if (!settingsData) return;
                var payload = {
                    token: authToken,
                    whitelist: settingsData.whitelist,
                    blacklist: settingsData.blacklist,
                    use_image_host: settingsData.use_image_host !== false,
                    theme: settingsData.theme || 'light',
                    theme_auto: !!settingsData.theme_auto,
                    dark_start: settingsData.dark_start || '20:00',
                    dark_end: settingsData.dark_end || '07:00',
                    chat_background: !!settingsData.chat_background,
                    sound_enabled: settingsData.sound_enabled !== false
                };
                var x = new XMLHttpRequest();
                x.open('POST', '/api/settings', true);
                x.setRequestHeader('Content-Type', 'application/json');
                x.onreadystatechange = function() {
                    if (x.readyState !== 4) return;
                    if (authToken !== payload.token) return;
                    if (x.status === 200) {
                        var d;
                        try {
                            d = JSON.parse(x.responseText);
                            if (!d.ok || !d.settings) throw new Error('Invalid settings');
                        } catch (e) { showToast('保存失败：服务器返回数据无效'); return; }
                        savedSettings = d.settings;
                        if (uiSettingsRequest) {
                            var pendingUi = uiSettingsRequest;
                            uiSettingsRequest = null;
                            pendingUi.abort();
                        }
                        applyUiCfg(savedSettings);
                        showToast('设置已保存');
                        closeSettings();
                    } else {
                        showToast('保存失败');
                    }
                };
                x.timeout = 8000;
                x.send(JSON.stringify(payload));
            }
</script>

        <div id="settings-modal" class="fixed inset-0 z-[10000] hidden">
            <div class="absolute inset-0 bg-black/40" id="settings-mask"></div>
            <div class="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 bg-white rounded-2xl shadow-2xl w-[92vw] max-w-[720px] h-[70vh] flex overflow-hidden border border-gray-200 settings-panel">
                <div class="w-40 bg-gray-50 border-r border-gray-200 p-2 shrink-0">
                    <button type="button" class="set-tab w-full text-left px-3 py-2 rounded-lg text-sm mb-1" data-tab="whitelist" onclick="switchSettingsTab('whitelist')">白名单设置</button>
                    <button type="button" class="set-tab w-full text-left px-3 py-2 rounded-lg text-sm mb-1" data-tab="blacklist" onclick="switchSettingsTab('blacklist')">黑名单设置</button>
                    <button type="button" class="set-tab w-full text-left px-3 py-2 rounded-lg text-sm mb-1" data-tab="background" onclick="switchSettingsTab('background')">背景设置</button>
                    <button type="button" class="set-tab w-full text-left px-3 py-2 rounded-lg text-sm mb-1" data-tab="color" onclick="switchSettingsTab('color')">颜色设置</button>
                    <button type="button" class="set-tab w-full text-left px-3 py-2 rounded-lg text-sm mb-1" data-tab="sound" onclick="switchSettingsTab('sound')">提示音设置</button>
                    <button type="button" class="set-tab w-full text-left px-3 py-2 rounded-lg text-sm mb-1" data-tab="about" onclick="switchSettingsTab('about')">关于</button>
                </div>
                <div class="flex-1 flex flex-col min-w-0">
                    <div id="settings-body" class="flex-1 overflow-y-auto p-5"></div>
                    <div class="border-t border-gray-200 px-4 py-3 flex justify-end gap-2 shrink-0">
                        <button type="button" id="settings-cancel" class="px-4 py-1.5 text-sm rounded-md border border-gray-300 text-gray-600 hover:bg-gray-50">取消</button>
                        <button type="button" id="settings-save" class="px-4 py-1.5 text-sm rounded-md bg-blue-500 text-white hover:bg-blue-600">保存</button>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html', charset='utf-8')

async def web_nr_handler(request):
    html = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>QQ Web Chat NR</title>
<style>
html,body{height:100%;margin:0;padding:0;}
body{font-family:Arial,"Microsoft YaHei",sans-serif;font-size:14px;color:#333;background:#f2f3f5;overflow:hidden;}
#login{position:fixed;left:0;top:0;right:0;bottom:0;background:#f2f3f5;z-index:100;padding:40px 16px;}
#login .box{max-width:360px;margin:0 auto;background:#fff;border-radius:8px;padding:24px;box-shadow:0 2px 10px rgba(0,0,0,.1);}
#login h2{margin:0 0 16px;font-size:20px;}
#login input{width:100%;padding:10px;border:1px solid #ccc;border-radius:6px;font-size:16px;}
#login button{margin-top:12px;width:100%;padding:11px;border:0;border-radius:6px;background:#3b82f6;color:#fff;font-size:16px;}
#login .err{color:#e5484d;margin-top:10px;font-size:13px;min-height:18px;}
#app{display:none;height:100%;}
#sidebar{position:absolute;left:0;top:0;bottom:0;width:230px;background:#fff;border-right:1px solid #e5e5e5;overflow-y:auto;}
#status{height:40px;line-height:40px;padding:0 12px;font-size:12px;color:#666;border-bottom:1px solid #eee;background:#fafafa;overflow:hidden;white-space:nowrap;}
#contacts{padding:0;}
.contact{padding:10px;border-bottom:1px solid #f0f0f0;cursor:pointer;overflow:hidden;}
.contact.active{background:#e8f0fe;}
.contact img{float:left;width:38px;height:38px;border-radius:50%;margin-right:8px;background:#ddd;}
.contact .cinfo{overflow:hidden;}
.contact .cname{font-weight:bold;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.contact .cid{font-size:11px;color:#999;}
#chat{position:absolute;left:230px;right:0;top:0;bottom:0;}
#chatHeader{height:40px;line-height:40px;padding:0 12px;background:#fff;border-bottom:1px solid #e5e5e5;font-weight:bold;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
#backBtn{display:none;height:40px;border:0;background:transparent;font-size:20px;color:#3b82f6;margin-right:2px;padding:0 6px;float:left;}
#messages{position:absolute;top:40px;bottom:48px;left:0;right:0;overflow-y:auto;padding:10px;}
#composer{position:absolute;left:0;right:0;bottom:0;height:48px;background:#fff;border-top:1px solid #e5e5e5;padding:6px 8px;}
#composer table{width:100%;border-collapse:collapse;}
#composer td{vertical-align:middle;padding:0;}
#composer td.cbtn{width:46px;}
#composer td.csend{width:54px;}
#composer td.cbtn button{width:42px;}
#composer td.csend button{width:50px;}
#composer input[type=text]{width:100%;height:32px;padding:0 6px;border:1px solid #ccc;border-radius:4px;font-size:14px;box-sizing:border-box;-webkit-box-sizing:border-box;}
#composer button{height:32px;padding:0;border:0;border-radius:4px;background:#3b82f6;color:#fff;font-size:13px;}
#composer .pick{background:#eef2f7;color:#333;border:1px solid #dde3ea;}
.msg{margin:8px 0;overflow:hidden;}
.msg .meta{font-size:11px;color:#999;margin-bottom:2px;}
.msg .bubble{display:inline-block;max-width:75%;padding:7px 10px;border-radius:8px;background:#fff;border:1px solid #e3e3e3;text-align:left;word-break:break-all;white-space:pre-wrap;}
.msg.self{text-align:right;}
.msg.self .bubble{background:#3b82f6;border-color:#3b82f6;color:#fff;}
.msg img{max-width:100%;border-radius:4px;display:block;}
.msg .filelink{color:inherit;text-decoration:underline;}
.msg .recall{display:inline-block;font-size:11px;color:#999;margin-top:2px;cursor:pointer;}
.msg.recalled{text-align:center;}
.msg.recalled .bubble{background:#e5e5e5;border:0;color:#888;font-size:12px;border-radius:10px;}
@media (max-width:620px){
  #sidebar{width:100%;}
  #chat{left:100%;}
  #app.chat-open #sidebar{display:none;}
  #backBtn{display:inline-block;}
  #app.chat-open #chat{left:0;}
}
</style>
</head>
<body>
<div id="login">
  <div class="box">
    <h2>安全验证</h2>
    <input id="pwd" type="password" placeholder="请输入访问密码">
    <button type="button" id="loginBtn">进入聊天</button>
    <div class="err" id="loginErr"></div>
  </div>
</div>
<div id="app">
  <div id="sidebar">
    <div id="status">等待连接...</div>
    <div id="contacts"></div>
  </div>
  <div id="chat">
    <div id="chatHeader"><button type="button" id="backBtn">‹</button><span id="chatTitle">请选择联系人</span></div>
    <div id="messages"></div>
    <div id="composer">
      <table cellpadding="0" cellspacing="0" border="0"><tr>
        <td class="cbtn"><button type="button" class="pick" id="pickImg">图片</button></td>
        <td class="cbtn"><button type="button" class="pick" id="pickFile">文件</button></td>
        <td class="cinput"><input type="text" id="msgInput" placeholder="输入消息..."></td>
        <td class="csend"><button type="button" id="sendBtn">发送</button></td>
      </tr></table>
      <input type="file" id="imgInput" accept="image/*" style="display:none">
      <input type="file" id="fileInput" style="display:none">
    </div>
  </div>
</div>
<script>
var token=null, ws=null, myQQ=null, contacts={}, currentId=null, currentType=null, pendingFile=null;

function $(id){ return document.getElementById(id); }
function esc(s){ if(s===null||s===undefined) return ''; return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function setStatus(t){ $('status').innerHTML = esc(t); }

function postJSON(url, obj, cb){
  var x = new XMLHttpRequest();
  x.open('POST', url, true);
  x.setRequestHeader('Content-Type','application/json');
  x.onreadystatechange = function(){ if(x.readyState===4){ cb(x.status, x.responseText); } };
  x.send(JSON.stringify(obj));
}

function doLogin(){
  var pwd = $('pwd').value;
  $('loginErr').innerHTML = '';
  if(!pwd){ $('loginErr').innerHTML='请输入密码'; return; }
  postJSON('/api/login', {password:pwd}, function(status, text){
    if(status===200){
      var d = JSON.parse(text);
      token = d.token;
      try{ localStorage.setItem('webchat_token', token); }catch(e){}
      $('login').style.display='none';
      $('app').style.display='block';
      connect();
    } else {
      $('loginErr').innerHTML='密码错误，请重试';
    }
  });
}

function connect(){
  var scheme = location.protocol==='https:' ? 'wss://' : 'ws://';
  try{
    ws = new WebSocket(scheme + location.host + '/client_ws?token=' + encodeURIComponent(token));
  }catch(e){ setStatus('浏览器不支持 WebSocket'); return; }
  ws.onopen = function(){ setStatus('已连接'); if(currentId) ws.send(JSON.stringify({type:'get_history', target_id:currentId})); };
  ws.onmessage = function(ev){ handle(JSON.parse(ev.data)); };
  ws.onclose = function(ev){
    if(ev.code===1008){
      try{ localStorage.removeItem('webchat_token'); }catch(e){}
      token=null; $('app').style.display='none'; $('login').style.display='block';
      $('loginErr').innerHTML='登录已过期，请重新输入密码';
    } else {
      setStatus('连接断开，3 秒后重连...');
      setTimeout(function(){ if(token) connect(); }, 3000);
    }
  };
  ws.onerror = function(){};
}

function handle(d){
  if(d.type==='status'){
    myQQ = d.self_id || myQQ;
    setStatus(d.connected ? 'NapCat已连接，QQ：'+(d.self_id||'') : 'NapCat未连接');
  } else if(d.type==='contact_add'){
    addContact(d.contact);
  } else if(d.type==='message'){
    if(d.target_id===currentId){ appendMessage(d); }
  } else if(d.type==='history_data'){
    if(d.target_id===currentId){
      $('messages').innerHTML='';
      var list=d.messages||[];
      for(var i=0;i<list.length;i++){ appendMessage(list[i], i===list.length-1); }
    }
  } else if(d.type==='toast'){
    alert(d.content);
  } else if(d.type==='message_recalled'){
    var el=$('msg-'+d.message_id);
    if(el){ el.className='msg recalled'; el.innerHTML='<div class="bubble">「'+esc(d.sender_name||'某人')+'」撤回了一条消息</div>'; }
  } else if(d.type==='clear_history'){
    if(d.target_id===currentId){ $('messages').innerHTML=''; }
  }
}

function addContact(c){
  contacts[c.id]=c;
  var el=$('contact-'+c.id);
  if(!el){
    el=document.createElement('div');
    el.className='contact';
    el.id='contact-'+c.id;
    el.onclick=(function(id){ return function(){ switchChat(id); }; })(c.id);
    $('contacts').appendChild(el);
  }
  var html='';
  if(c.avatar){ html += '<img src="'+esc(c.avatar)+'" onerror="this.style.visibility=\\'hidden\\'">'; }
  html += '<div class="cinfo"><div class="cname">'+esc(c.name)+'</div><div class="cid">ID: '+esc(c.id)+'</div></div>';
  el.innerHTML=html;
}

function switchChat(id){
  var c=contacts[id];
  if(!c) return;
  currentId=id; currentType=c.type;
  $('chatTitle').innerHTML=esc(c.name);
  $('messages').innerHTML='<div style="color:#999;text-align:center;padding:20px;">加载中...</div>';
  var all=$('contacts').getElementsByTagName('div');
  for(var i=0;i<all.length;i++){ if(all[i].className==='contact active') all[i].className='contact'; }
  var cur=$('contact-'+id); if(cur) cur.className='contact active';
  if(window.innerWidth<=620 && $('app').className!=='chat-open'){ $('app').className='chat-open'; }
  if(ws && ws.readyState===1){ ws.send(JSON.stringify({type:'get_history', target_id:id})); }
}

function isSelfName(name){
  if(name==='我') return true;
  return myQQ!==null && String(name)===String(myQQ);
}

function appendMessage(m, scroll){
  var self=isSelfName(m.sender_name);
  var div=document.createElement('div');
  div.className='msg'+(self?' self':'')+(m.is_recalled?' recalled':'');
  div.id='msg-'+m.message_id;
  if(m.is_recalled){
    div.innerHTML='<div class="bubble">'+esc(m.content)+'</div>';
    $('messages').appendChild(div);
    return;
  }
  var meta=document.createElement('div');
  meta.className='meta';
  meta.appendChild(document.createTextNode((self?(myQQ!==null?String(myQQ):'我'):m.sender_name)+' · '+m.time));
  div.appendChild(meta);

  var bubble=document.createElement('div');
  bubble.className='bubble';

  if(m.media_url){
    var img=document.createElement('img');
    img.src = m.media_url.indexOf('/files/')===0 ? m.media_url : '/proxy_img?url='+encodeURIComponent(m.media_url);
    img.onerror=function(){ this.style.display='none'; };
    bubble.appendChild(img);
    if(m.content && m.content!=='[图片]'){
      var t=document.createElement('div'); t.appendChild(document.createTextNode(m.content)); bubble.appendChild(t);
    }
  } else if(m.file_name){
    if(typeof m.file_url==='string' && (/^https?:\\/\\//i.test(m.file_url) || /^\\/files\\/[a-zA-Z0-9_.-]+$/.test(m.file_url))){
      var a=document.createElement('a');
      a.className='filelink';
      a.href = /^https?:\\/\\//i.test(m.file_url) ? ('/download?url='+encodeURIComponent(m.file_url)+'&name='+encodeURIComponent(m.file_name)) : m.file_url;
      a.target='_blank';
      a.appendChild(document.createTextNode(m.file_name));
      bubble.appendChild(a);
    } else {
      bubble.appendChild(document.createTextNode(m.file_name));
    }
    if(m.file_size){ var s=document.createElement('div'); s.style.fontSize='11px'; s.appendChild(document.createTextNode(formatSize(m.file_size))); bubble.appendChild(s); }
  } else {
    bubble.appendChild(document.createTextNode(m.content));
  }

  div.appendChild(bubble);

  if(self && m.message_id){
    var r=document.createElement('div');
    r.className='recall';
    r.appendChild(document.createTextNode('撤回'));
    r.onclick=(function(mid){ return function(){ if(confirm('撤回这条消息？')) recallMsg(mid); }; })(m.message_id);
    div.appendChild(r);
  }

  $('messages').appendChild(div);
  if(scroll!==false){ $('messages').scrollTop=$('messages').scrollHeight; }
}

function formatSize(n){
  n=Number(n)||0;
  if(n<1024) return n+' B';
  if(n<1024*1024) return (n/1024).toFixed(1)+' KB';
  return (n/1024/1024).toFixed(2)+' MB';
}

function recallMsg(id){
  if(ws && ws.readyState===1){ ws.send(JSON.stringify({type:'recall_message', target_id:currentId, message_id:id})); }
}

function sendMsg(){
  if(!currentId){ alert('请先选择联系人'); return; }
  if(!ws || ws.readyState!==1){ alert('连接已断开'); return; }
  var text=$('msgInput').value;
  if(!text && !pendingFile) return;
  ws.send(JSON.stringify({
    type:'send_message',
    target_id:currentId,
    msg_type:currentType,
    content:text,
    quote:null,
    file_local_path: pendingFile ? pendingFile.local_path : null,
    file_orig_name: pendingFile ? pendingFile.name : null,
    file_is_image: pendingFile ? pendingFile.is_image : false
  }));
  $('msgInput').value='';
  pendingFile=null;
  setStatus('已发送');
}

function uploadFile(file, isImage){
  if(!file) return;
  if(file.size > 50*1024*1024){ alert('文件过大，限制 50MB'); return; }
  setStatus('上传中：'+file.name);
  var fd=new FormData();
  fd.append(isImage?'image':'file', file);
  var x=new XMLHttpRequest();
  x.open('POST','/api/upload',true);
  x.onreadystatechange=function(){
    if(x.readyState===4){
      if(x.status===200){
        var d=JSON.parse(x.responseText);
        pendingFile={name:file.name, local_path:d.local_path, is_image:isImage};
        setStatus('已选择：'+file.name);
      } else {
        setStatus('上传失败：'+x.status);
        alert('上传失败，HTTP '+x.status);
      }
    }
  };
  x.send(fd);
}

$('backBtn').onclick=function(){ $('app').className=''; };
$('loginBtn').onclick=doLogin;
$('pwd').onkeydown=function(e){ if((e.keyCode||e.which)===13) doLogin(); };
$('sendBtn').onclick=sendMsg;
$('msgInput').onkeydown=function(e){ if((e.keyCode||e.which)===13) sendMsg(); };
$('pickImg').onclick=function(){ $('imgInput').click(); };
$('pickFile').onclick=function(){ $('fileInput').click(); };
$('imgInput').onchange=function(){ uploadFile(this.files[0], true); };
$('fileInput').onchange=function(){ uploadFile(this.files[0], false); };

try{
  var saved=localStorage.getItem('webchat_token');
  if(saved){ token=saved; $('login').style.display='none'; $('app').style.display='block'; var restore=new XMLHttpRequest(); restore.open('POST','/api/restore',true); restore.setRequestHeader('Content-Type','application/json'); restore.onreadystatechange=function(){if(restore.readyState===4) connect();}; restore.send(JSON.stringify({token:token})); }
}catch(e){}
</script>
</body>
</html>"""
    return web.Response(text=html, content_type='text/html', charset='utf-8')

async def files_handler(request):
    name = request.match_info['name']
    if not re.fullmatch(r'(?:[0-9a-f]{32}(?:\.[a-zA-Z0-9]{1,10})?|img_[0-9a-f]{32}\.(?:jpg|jpeg|png|gif|webp|avif))', name):
        raise web.HTTPNotFound()
    try:
        directory = os.open(UPLOAD_DIR, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
        f = os.fdopen(fd, 'rb')
        info = os.fstat(fd)
        size = info.st_size
        if not stat.S_ISREG(info.st_mode) or size > MAX_TRANSFER:
            f.close()
            raise web.HTTPNotFound()
    except OSError:
        raise web.HTTPNotFound()
    types = {'.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.png':'image/png', '.gif':'image/gif', '.webp':'image/webp', '.avif':'image/avif'}
    ext = os.path.splitext(name)[1].lower()
    headers = {'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "sandbox; default-src 'none'", 'Content-Length': str(size)}
    if ext not in types:
        headers['Content-Disposition'] = 'attachment; filename="download"'
    response = web.StreamResponse(headers=headers)
    response.content_type = types.get(ext, 'application/octet-stream')
    try:
        await response.prepare(request)
        remaining = size
        while remaining:
            chunk = await asyncio.to_thread(f.read, min(65536, remaining))
            if not chunk:
                break
            await response.write(chunk)
            remaining -= len(chunk)
        await response.write_eof()
        return response
    finally:
        f.close()

async def main():
    if not WEB_PASSWORD or not EXPECTED_TOKEN:
        raise RuntimeError("请设置 WEB_PASSWORD 和 NAPCAT_ACCESS_TOKEN")
    if not PUBLIC_BASE_URL.startswith(("http://", "https://")):
        raise RuntimeError("请将 PUBLIC_BASE_URL 设置为服务的公网 HTTP(S) 地址")
    os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
    init_storage()
    load_data()
    load_settings()
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    app = web.Application(middlewares=[error_middleware], client_max_size=100 * 1024 * 1024)
    app.router.add_get('/ws', napcat_ws_handler)
    app.router.add_get('/', napcat_ws_handler)
    app.router.add_post('/api/login', login_handler)
    app.router.add_post('/api/restore', restore_handler)
    app.router.add_post('/api/logout', logout_handler)
    app.router.add_get('/api/ui_settings', ui_settings_handler)
    app.router.add_get('/api/wallpaper', wallpaper_handler)
    app.router.add_get('/api/settings', settings_handler)
    app.router.add_post('/api/settings', settings_handler)
    app.router.add_post('/api/upload', upload_handler)
    app.router.add_post('/api/bridge/connect', bridge_connect_handler)
    app.router.add_get('/api/bridge/poll', bridge_poll_handler)
    app.router.add_post('/api/bridge/send', bridge_send_handler)
    app.router.add_get('/client_ws', client_ws_handler)
    app.router.add_get('/web', web_handler)
    app.router.add_get('/web/nr', web_nr_handler)
    app.router.add_get('/proxy_img', proxy_image_handler)
    app.router.add_get('/download', download_handler)
    app.router.add_get('/files/{name}', files_handler)
    app.router.add_static('/static', path=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 4001)
    await site.start()

    asyncio.create_task(cleanup_old_files())
    asyncio.create_task(bridge_cleanup())
    logger.info("WebChat 启动: http://127.0.0.1:4001/web")
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass