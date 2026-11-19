import asyncio
import signal
from pathlib import Path
from webchat.storage import AsyncStore, RevisionConflict, StorageBusy
from webchat.auth import SessionManager, SafeAccessLogger, json_object, load_credentials
from webchat.resources import DiskBudget, io_safely
from webchat.http_security import security_middleware, response_headers
from webchat.messaging import (conversation_key, parse_target, normalize_segments,
                               inbox_key, validate_client_message, validate_request_id, all_command)
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
    "admin_qq_id": "",
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
TEMPLATE_DIR = Path(__file__).resolve().parent / 'templates'
PUBLIC_BASE_URL = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')
sessions = SessionManager(WEB_PASSWORD, public_base_url=PUBLIC_BASE_URL,
                          trusted_proxies=tuple(x.strip() for x in os.environ.get('TRUSTED_PROXIES', '').split(',') if x.strip()))
store = None
background_tasks = set()
shutdown_event = asyncio.Event()
inbox_available = asyncio.Event()
media_queue = asyncio.Queue(maxsize=128)
DISCLOSE_CLIENT_IP = os.environ.get('DISCLOSE_CLIENT_IP', 'true').lower() not in ('0', 'false', 'no')
ready = False
# Upload authority is separate from the public, expiring delivery capability.
upload_bindings = {}
UPLOAD_BINDING_LIMIT = 4096
UPLOAD_TTL = 7 * 86400


def spawn(coro):
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    def finished(done):
        background_tasks.discard(done)
        if not done.cancelled() and done.exception() is not None:
            logger.error('background task failed: %s', type(done.exception()).__name__)
    task.add_done_callback(finished)
    return task


def status_event():
    return {'type': 'status', 'connected': state.napcat_connected and state.qq_online,
            'napcat_connected': state.napcat_connected, 'qq_online': state.qq_online,
            'self_id': state.self_id}


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
    except StorageBusy:
        return web.json_response({'error': 'durable storage capacity; retry later'}, status=503,
                                 headers={'Retry-After': '5'})
    except web.HTTPException as ex:
        if ex.status == 404 and not request.path.startswith('/api/'):
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
        self.qq_online = False

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

def current_admin():
    """Settings value wins; the environment variable is the initial fallback."""
    value = settings.get('admin_qq_id')
    if isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,19}', value):
        return value
    return ADMIN_QQ_ID


async def ui_settings_handler(request):
    return web.json_response(public_settings())

async def settings_handler(request):
    sessions.require(request)
    if request.method == 'GET':
        result = await store.call('get_settings')
        return web.json_response({**result['settings'], 'admin_qq_id': current_admin(),
                                  'revision': result['revision']})
    data = await json_object(request)
    revision = data.get('revision')
    if not isinstance(revision, int) or isinstance(revision, bool):
        return web.json_response({'error': 'revision required'}, status=400)
    async with state.settings_lock:
        current = await store.call('get_settings')
        updated = dict(current['settings'])
        for key in DEFAULT_SETTINGS:
            if key not in data:
                continue
            value = data[key]
            if key in ('whitelist', 'blacklist'):
                if not isinstance(value, (str, list)):
                    raise web.HTTPBadRequest()
                value = clean_ids(value)
                if len(value) > 10000 or any(not re.fullmatch(r'[1-9][0-9]{0,19}', x) for x in value):
                    raise web.HTTPBadRequest()
            elif key == 'admin_qq_id':
                if not isinstance(value, str) or (value and not re.fullmatch(r'[1-9][0-9]{0,19}', value)):
                    raise web.HTTPBadRequest()
            elif isinstance(DEFAULT_SETTINGS[key], bool):
                if not isinstance(value, bool):
                    raise web.HTTPBadRequest()
            elif not isinstance(value, str) or len(value) > 100:
                raise web.HTTPBadRequest()
            updated[key] = value
        try:
            result = await store.call('save_settings', updated, revision)
        except RevisionConflict:
            return web.json_response({'error': 'settings changed; reload'}, status=409)
        settings.clear()
        settings.update(result['settings'])
        return web.json_response({'ok': True, 'settings': {**settings, 'revision': result['revision']}, 'revision': result['revision']})




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


async def queue_client_message(ws, message, backpressure=False):
    sender = client_senders.get(ws)
    if sender is None or ws.closed:
        return False
    payload = json.dumps(message, ensure_ascii=False)
    size = len(payload.encode("utf-8"))
    if backpressure and size <= CLIENT_FRAME_BYTES:
        try:
            async with asyncio.timeout(BROADCAST_TIMEOUT):
                while sender[0].full() or sender[0].bytes_used + size > CLIENT_QUEUE_BYTES:
                    if ws.closed or sender[1].done():
                        return False
                    await asyncio.sleep(0.001)
                sender[0].put_nowait((payload, size))
                sender[0].bytes_used += size
                return True
        except TimeoutError:
            pass
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
    await asyncio.gather(*(queue_client_message(ws, message) for ws in list(state.clients)
                           if sessions.is_active(client_owners.get(ws), touch=False)))

async def send_history(ws, target_id):
    validate_client_message({'type': 'get_history', 'target_id': target_id})
    async with state.lock:
        snapshot = await store.call('history', target_id)
        message = {'type': 'history_data', **snapshot}
        if len(json.dumps(message, ensure_ascii=False).encode()) > CLIENT_FRAME_BYTES:
            await queue_client_message(ws, {'type': 'toast', 'content': '聊天记录超过8MiB，无法显示；没有截断历史'})
            return
        await queue_client_message(ws, message)

async def save_message(target_id, msg_data, contact=None, quote_id=None, quote_id_kind='internal'):
    async with state.lock:
        result = await store.call('append_message', target_id, msg_data, contact=contact,
                                  quote_id=quote_id, quote_id_kind=quote_id_kind,
                                  platform_ids=msg_data.get('napcat_message_ids'))
        if not result['inserted']:
            return result
        if result.get('contact'):
            state.contacts[target_id] = result['contact']
            await broadcast_to_clients({'type': 'contact_add', 'contact': result['contact'], 'revision': result['revision']})
        await broadcast_to_clients({'type': 'message', **result['message'], 'revision': result['revision']})
        return result

async def clear_history(target_id):
    async with state.lock:
        result = await store.call('clear_history', target_id)
        await broadcast_to_clients({'type': 'clear_history', **result})

async def send_qq_api_and_wait(action, params, timeout=120.0):
    ws = state.napcat_ws
    if not ws or ws.closed:
        return None
    echo = secrets.token_urlsafe(24)
    future = asyncio.get_running_loop().create_future()
    pending_requests[echo] = (ws, future)
    try:
        # A single deadline includes transport backpressure, not just the echo wait.
        async with asyncio.timeout(timeout):
            await ws.send_json({'action': action, 'params': params, 'echo': echo})
            return await future
    except (TimeoutError, ConnectionError, RuntimeError, aiohttp.ClientError):
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
    # Even fire-and-forget commands have a bounded transport wait.
    ws = state.napcat_ws
    if ws is not None and not ws.closed:
        try:
            async with asyncio.timeout(5):
                await ws.send_json({'action': action, 'params': params, 'echo': secrets.token_urlsafe(24)})
        except (TimeoutError, ConnectionError, RuntimeError, aiohttp.ClientError):
            pass

MAX_TRANSFER = 50 * 1024 * 1024
remote_slots = asyncio.Semaphore(4)
wallpaper_slots = asyncio.Semaphore(2)
upload_slots = asyncio.Semaphore(4)
attachment_budget = DiskBudget(UPLOAD_DIR,
    max_bytes=int(os.environ.get('ATTACHMENT_MAX_BYTES', str(1024 * 1024 * 1024))),
    min_free=int(os.environ.get('MIN_FREE_BYTES', str(512 * 1024 * 1024))),
    max_files=int(os.environ.get('ATTACHMENT_MAX_FILES', '10000')))

def public_ip(value):
    ip = ipaddress.ip_address(value)
    mapped = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) else None
    return (ip.is_global and not ip.is_multicast and
            (mapped is None or (mapped.is_global and not mapped.is_multicast)))

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
    return sessions.authenticate(request) is not None

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




bridge_sessions = {}
BRIDGE_SESSION_LIMIT = 64
BRIDGE_QUEUE_LIMIT = 128
BRIDGE_TOTAL_BYTES = 8 * 1024 * 1024
BRIDGE_FRAME_BYTES = CLIENT_FRAME_BYTES


class BridgeSocket:
    """Internal transport: no loopback credential URLs or second WS size limit."""
    def __init__(self):
        self.closed = False
        self.queue = asyncio.Queue(maxsize=BRIDGE_QUEUE_LIMIT)
        self.queue.bytes_used = 0
        self.available = asyncio.Event()

    async def send_str(self, payload):
        size = len(payload.encode())
        total = sum(s['ws'].queue.bytes_used for s in bridge_sessions.values())
        if size > BRIDGE_FRAME_BYTES or total + size > BRIDGE_TOTAL_BYTES or self.queue.full():
            await self.close()
            raise ConnectionError('bridge buffer limit')
        self.queue.put_nowait((payload, size))
        self.queue.bytes_used += size
        self.available.set()

    async def close(self, **kwargs):
        self.closed = True
        self.available.set()
        state.clients.discard(self)
        client_owners.pop(self, None)
        remove_client_sender(self)


async def close_bridge(sid):
    item = bridge_sessions.pop(sid, None)
    if item:
        await item['ws'].close()
        sessions.unbind(item['owner'], sid)


async def bridge_connect_handler(request):
    owner = sessions.require(request)
    await json_object(request)
    if len(bridge_sessions) >= BRIDGE_SESSION_LIMIT:
        return web.json_response({'error': 'bridge capacity'}, status=503)
    sid = secrets.token_urlsafe(16)
    ws = BridgeSocket()
    if not sessions.bind(owner, sid, lambda: close_bridge(sid)):
        return web.json_response({'error': 'session capacity'}, status=503)
    bridge_sessions[sid] = {'ws': ws, 'owner': owner, 'last': time.monotonic(), 'polling': False}
    try:
        await initialize_client(ws, owner)
    except BaseException:
        await close_bridge(sid)
        raise
    return web.json_response({'session': sid})


def owned_bridge(request, sid):
    owner = sessions.require(request)
    if not isinstance(sid, str):
        raise web.HTTPBadRequest()
    item = bridge_sessions.get(sid)
    if item is None or item['owner'] is not owner:
        raise web.HTTPNotFound()
    item['last'] = time.monotonic()
    return item


async def bridge_poll_handler(request):
    item = owned_bridge(request, request.query.get('session'))
    if item['polling']:
        return web.json_response({'error': 'poll already active'}, status=409)
    item['polling'] = True
    ws = item['ws']
    messages = []
    try:
        if ws.queue.empty() and not ws.closed:
            ws.available.clear()
            try:
                await asyncio.wait_for(ws.available.wait(), 2)
            except TimeoutError:
                pass
        if not sessions.is_active(item['owner']):
            raise web.HTTPUnauthorized()
        if not ws.queue.empty():
            payload, size = ws.queue.get_nowait()
            ws.queue.bytes_used -= size
            ws.queue.task_done()
            messages.append(payload)
        return web.json_response({'messages': messages, 'closed': ws.closed and ws.queue.empty()})
    finally:
        item['polling'] = False


async def bridge_send_handler(request):
    data = await json_object(request, max_bytes=CLIENT_FRAME_BYTES)
    item = owned_bridge(request, data.get('session'))
    if item['ws'].closed:
        raise web.HTTPGone()
    await handle_client_payload(item['ws'], data.get('payload'), item['owner'], sessions.client_ip(request))
    return web.json_response({'ok': True})


async def bridge_cleanup():
    while True:
        for sid, item in list(bridge_sessions.items()):
            if not sessions.is_active(item['owner']) or time.monotonic() - item['last'] > 300:
                await close_bridge(sid)
        await asyncio.sleep(30)

async def upload_handler(request):
    if not authenticated(request):
        return web.json_response({'error': 'unauthorized'}, status=401)
    try:
        await asyncio.wait_for(upload_slots.acquire(), timeout=2)
    except asyncio.TimeoutError:
        return web.json_response({'error': 'busy'}, status=503)
    try:
        async with attachment_budget.reserve(MAX_TRANSFER):
            return await asyncio.wait_for(_upload_handler(request), timeout=120)
    except asyncio.TimeoutError:
        return web.json_response({'error': 'upload timed out'}, status=408)
    finally:
        upload_slots.release()

async def _upload_handler(request):
    if not authenticated(request):
        return web.json_response({'error': 'unauthorized'}, status=401)
    owner = sessions.require(request)
    filepath = None
    try:
        now = time.time()
        for path, binding in list(upload_bindings.items()):
            if binding['expires'] <= now or not sessions.is_active(binding['owner']):
                upload_bindings.pop(path, None)
        if len(upload_bindings) >= UPLOAD_BINDING_LIMIT:
            return web.json_response({'error': 'upload capacity'}, status=503)
        reader = await request.multipart()
        target = request.headers.get('X-Conversation-ID')
        upload_id = request.headers.get('X-Upload-ID') or request.headers.get('X-Request-ID')
        field = await reader.next()
        # Identity fields must precede the file, avoiding unbounded multipart buffering.
        for _ in range(4):
            if field is None or field.name in ('image', 'file'):
                break
            if field.name not in ('target_id', 'upload_id', 'request_id'):
                raise ValueError('invalid upload field')
            raw = bytearray()
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > 256:
                    raise ValueError('upload identity too large')
            value = raw.decode('utf-8')
            if field.name == 'target_id':
                target = value
            else:
                upload_id = value
            field = await reader.next()
        parse_target(target)
        validate_request_id(upload_id)
        if not field or field.name not in ('image', 'file'):
            raise ValueError('invalid file field')
        if any(b['owner'] is owner and b['upload_id'] == upload_id for b in upload_bindings.values()):
            raise ValueError('upload identity reused')
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
                await io_safely(f.write, chunk)
        if not sessions.is_active(owner):
            raise ValueError('session expired')
        upload_bindings[filepath] = {'owner': owner, 'target_id': target, 'upload_id': upload_id,
                                     'expires': time.time() + UPLOAD_TTL, 'request_id': None}
        return web.json_response({'url': '/files/' + filename, 'local_path': filepath,
                                  'orig_name': orig_name, 'upload_id': upload_id, 'target_id': target})
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
        async with attachment_budget.reserve(len(body)):
            with open(os.path.join(UPLOAD_DIR, filename), 'xb') as f:
                await io_safely(f.write, body)
        return '/files/' + filename
    except (ValueError, OSError, aiohttp.ClientError, asyncio.TimeoutError, web.HTTPInsufficientStorage):
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
    for contact in list(state.contacts.values()):
        if contact.get('type') == 'group':
            schedule_media({'contact': contact})

def schedule_media(job):
    try:
        media_queue.put_nowait(job)
    except asyncio.QueueFull:
        # Optional enrichment never delays or discards the durable message.
        pass


async def media_worker():
    while True:
        job = await media_queue.get()
        try:
            async with asyncio.timeout(65):
                contact = job.get('contact')
                if contact:
                    response = await send_qq_api_and_wait('get_group_info', {'group_id': int(contact['peer_id'])}, timeout=8)
                    if isinstance(response, dict) and isinstance(response.get('data'), dict):
                        name = response['data'].get('group_name')
                        if isinstance(name, str) and name:
                            async with state.lock:
                                contact = {**contact, 'name': name[:256]}
                                result = await store.call('upsert_contact', contact)
                                state.contacts[contact['id']] = contact
                                await broadcast_to_clients({'type': 'contact_add', 'contact': contact, 'revision': result['revision']})
                else:
                    target, mid = job['target_id'], job['message_id']
                    current = await store.call('get_message', target, mid)
                    if not current or current.get('is_recalled'):
                        continue
                    segments = copy.deepcopy(current.get('segments', []))
                    changed = False
                    for segment in segments:
                        if segment.get('type') == 'file' and not segment.get('url') and segment.get('file_id'):
                            kind, peer = parse_target(target)
                            params = {'file_id': segment['file_id']}
                            if kind == 'group':
                                params['group_id'] = int(peer)
                                if segment.get('busid') is not None:
                                    params['busid'] = segment['busid']
                            response = await send_qq_api_and_wait('get_group_file_url' if kind == 'group' else 'get_private_file_url', params, timeout=8)
                            if isinstance(response, dict) and isinstance(response.get('data'), dict):
                                from webchat.messaging import public_url
                                url = public_url(response['data'].get('url'))
                                if url:
                                    segment['url'] = url
                                    changed = True
                        if segment.get('type') == 'image' and str(segment.get('url') or '').startswith(('http://', 'https://')):
                            cached = await cache_remote_image(segment['url'])
                            if cached:
                                segment['url'] = cached
                                changed = True
                    if changed:
                        async with state.lock:
                            file = next((x for x in segments if x.get('type') == 'file'), {})
                            result = await store.call('update_message', target, mid, {
                                'segments': segments,
                                'media_url': next((x['url'] for x in segments if x.get('type') == 'image' and x.get('url')), None),
                                'file_url': file.get('url'), 'file_name': file.get('name'),
                                'file_size': file.get('size')})
                            if result.get('message'):
                                await broadcast_to_clients({'type': 'message_updated', **result['message'], 'revision': result['revision']})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning('optional media metadata update failed')
        finally:
            media_queue.task_done()


async def process_inbound(data):
    if data.get('post_type') == 'notice' and data.get('notice_type') in ('offline_file', 'group_upload'):
        # File notices have no message segments; convert them without losing their identity.
        kind = 'group' if data['notice_type'] == 'group_upload' else 'private'
        file = data.get('file')
        if not isinstance(file, dict):
            raise ValueError('invalid file notice')
        sender_id = data.get('user_id')
        identity = 'file_' + hashlib.sha256(inbox_key(data).encode()).hexdigest()[:32]
        data = {**data, 'post_type': 'message', 'message_type': kind, 'message_id': identity,
                '_file_notice': True, 'sender': {'user_id': sender_id, 'nickname': str(sender_id)},
                'message': [{'type': 'file', 'data': {**file, 'file_id': file.get('id') or file.get('file_id'),
                                                      'file_size': file.get('size') or file.get('file_size')}}]}
    if data.get('post_type') == 'notice' and data.get('notice_type') in ('friend_recall', 'group_recall'):
        kind = 'group' if data['notice_type'] == 'group_recall' else 'private'
        target = conversation_key(kind, data.get('group_id') if kind == 'group' else data.get('user_id'))
        await handle_recall(target, data.get('message_id'), platform_id=True)
        return
    if data.get('post_type') != 'message' or data.get('message_type') not in ('private', 'group'):
        return
    kind = data['message_type']
    sender = data.get('sender')
    if not isinstance(sender, dict):
        raise ValueError('invalid sender')
    sender_id = str(sender.get('user_id') or data.get('user_id') or '')
    peer = str(data.get('group_id')) if kind == 'group' else sender_id
    target = conversation_key(kind, peer)
    segments, content, quote_id = normalize_segments(data.get('message', ''))
    blacklist = set(settings.get('blacklist', []))
    if peer in blacklist or sender_id in blacklist:
        return
    if peer not in set(settings.get('whitelist', [])):
        # Restore the original unauthorized hint: only a private '=' message is answered.
        if kind == 'private' and content.startswith('='):
            await send_qq_api('send_private_msg', {'user_id': int(peer),
                                                   'message': f'你的ID:{sender_id} 当前未授权，请联系管理员授权'})
        return
    command = all_command(content, kind)
    action = 'send_private_msg' if kind == 'private' else 'send_group_msg'
    params = {'user_id' if kind == 'private' else 'group_id': int(peer)}
    if command is not None:
        if isinstance(command, bool):
            await store.call('set_mode', target, command)
            tip = '当前会话全量转发已' + ('开启' if command else '关闭')
        elif command == 'help':
            enabled = await store.call('get_mode', target)
            tip = '当前会话全量转发：' + ('ON' if enabled else 'OFF') + '；使用 = ALL ON 或 = ALL OFF'
        else:
            tip = '全量转发命令仅限私聊使用'
        await send_qq_api(action, {**params, 'message': tip})
        return
    if content.upper() in ('= CLEAR', '= REBOOT'):
        admin = current_admin()
        if kind != 'private' or not admin or sender_id != admin:
            return
        if content.upper() == '= CLEAR':
            await clear_history(target)
        else:
            shutdown_event.set()
        return
    if content == '=':
        # Restore the original help menu; a leading command in a group gets no reply.
        if kind == 'group':
            return
        admin = current_admin()
        if admin and sender_id == admin:
            help_text = '[QQweb-Chat]\n= ALL 开启全量转发\n= CLEAR 清空聊天记录\n= REBOOT 重启服务器'
        else:
            help_text = '[QQweb-Chat]\n= ALL 开启全量转发'
        await send_qq_api(action, {**params, 'message': help_text})
        return
    if kind == 'private' and not await store.call('get_mode', target):
        # A reply segment does not bypass the forwarding prefix.
        if not content.startswith('='):
            return
        content = content[1:].strip()
        for segment in segments:
            if segment['type'] == 'text':
                segment['text'] = segment['text'].lstrip()
                if segment['text'].startswith('='):
                    segment['text'] = segment['text'][1:].lstrip()
                break
    if not content:
        return
    platform = data.get('message_id')
    if not isinstance(platform, (str, int)) or isinstance(platform, bool):
        raise ValueError('invalid platform message id')
    # Deterministic internal ID remains stable across inbox replay and duplicate QQ events.
    mid = 'in_' + hashlib.sha256((target + ':' + str(platform)).encode()).hexdigest()[:32]
    name = str(sender.get('card') or sender.get('nickname') or sender_id)[:256]
    contact = state.contacts.get(target) or {'id': target, 'peer_id': peer, 'type': kind,
               'name': name if kind == 'private' else peer,
               'avatar': f'https://p.qlogo.cn/gh/{peer}/{peer}/100' if kind == 'group' else f'https://q.qlogo.cn/headimg_dl?dst_uin={peer}&spec=100'}
    message = {'message_id': mid, 'target_id': target, 'sender_id': sender_id,
               'sender_name': name, 'direction': 'incoming', 'content': content,
               'segments': segments, 'napcat_message_ids': [] if data.get('_file_notice') else [str(platform)],
               'media_url': next((x['url'] for x in segments if x['type'] == 'image' and x.get('url')), None),
               'file_url': next((x.get('url') for x in segments if x['type'] == 'file'), None),
               'file_name': next((x.get('name') for x in segments if x['type'] == 'file'), None),
               'file_size': next((x.get('size') for x in segments if x['type'] == 'file'), None),
               'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    result = await save_message(target, message, contact, quote_id, quote_id_kind='platform')
    if result['inserted']:
        schedule_media({'target_id': target, 'message_id': result['message']['message_id']})
        if kind == 'group' and contact['name'] == peer:
            schedule_media({'contact': contact})
        # Restore the original private-only forwarded receipt; groups stay silent.
        if kind == 'private':
            await send_qq_api(action, {**params, 'message': '[Web-QQ] 消息已转发到Web端'})


async def inbox_worker():
    while True:
        inbox_available.clear()
        batch = await store.call('pending_inbox', limit=32)
        if not batch:
            await inbox_available.wait()
            continue
        for row in batch:
            try:
                await process_inbound(row['event'])
            except asyncio.CancelledError:
                raise  # durable pending row is replayed at startup
            except (ValueError, TypeError, KeyError):
                await store.call('finish_inbox', row['id'], error='invalid event schema')
            except Exception:
                logger.error('durable inbox processing failed; retained for retry')
                await asyncio.sleep(1)
                break
            else:
                await store.call('finish_inbox', row['id'])


async def napcat_ws_handler(request):
    if request.headers.get('Upgrade', '').lower() != 'websocket':
        raise web.HTTPNotFound()
    token = request.headers.get('Authorization', '').removeprefix('Bearer ') or request.query.get('access_token', '')
    if not EXPECTED_TOKEN or not secrets.compare_digest(token, EXPECTED_TOKEN):
        raise web.HTTPUnauthorized()
    ws = web.WebSocketResponse(max_msg_size=NAPCAT_FRAME_BYTES, heartbeat=30)
    await ws.prepare(request)
    old = state.napcat_ws
    if old is not None:
        fail_pending_for(old)
        try:
            await asyncio.wait_for(old.close(), 2)
        except TimeoutError:
            pass
    try:
        await asyncio.wait_for(napcat_transition_lock.acquire(), DRAIN_TIMEOUT + 2)
    except TimeoutError:
        await ws.close(code=1013, message=b'connection busy')
        return ws
    startup = []
    try:
        state.napcat_ws, state.napcat_connected, state.qq_online = ws, True, False
        state.self_id = None
        async def login_info():
            response = await send_qq_api_and_wait('get_login_info', {}, timeout=8)
            if state.napcat_ws is ws and isinstance(response, dict) and isinstance(response.get('data'), dict):
                state.self_id = response['data'].get('user_id')
                await broadcast_to_clients(status_event())
        online_observed = False
        async def online_info():
            response = await send_qq_api_and_wait('get_status', {}, timeout=8)
            if (not online_observed and state.napcat_ws is ws and isinstance(response, dict)
                    and response.get('status') == 'ok' and isinstance(response.get('data'), dict)
                    and isinstance(response['data'].get('online'), bool)):
                state.qq_online = response['data']['online']
                await broadcast_to_clients(status_event())
        startup = [spawn(login_info()), spawn(online_info()), spawn(refresh_contacts_meta())]
        await broadcast_to_clients(status_event())
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
                if not isinstance(data, dict):
                    continue
                echo = data.get('echo')
                pending = pending_requests.get(echo) if isinstance(echo, str) else None
                if pending and pending[0] is ws:
                    if not pending[1].done():
                        pending[1].set_result(data)
                    continue
                if state.napcat_ws is not ws:
                    break
                if data.get('post_type') == 'meta_event':
                    status = data.get('status')
                    if isinstance(status, dict) and isinstance(status.get('online'), bool):
                        online_observed = True
                        online = status['online']
                        if online != state.qq_online:
                            state.qq_online = online
                            await broadcast_to_clients(status_event())
                    continue
                if data.get('notice_type') == 'bot_offline':
                    online_observed = True
                    state.qq_online = False
                    await broadcast_to_clients(status_event())
                    continue
                if data.get('post_type') not in ('message', 'notice'):
                    continue
                # Persist before any optional media/metadata processing or disconnect drain.
                await store.call('enqueue_inbox', data, event_key=inbox_key(data))
                inbox_available.set()
            except StorageBusy:
                # Do not silently acknowledge or drop an event we cannot persist.
                await ws.close(code=1013, message=b'durable inbox capacity')
                break
            except (ValueError, TypeError, RecursionError):
                continue
    finally:
        # Lock release must be unconditional even if cleanup notification is cancelled.
        try:
            fail_pending_for(ws)
            for task in startup:
                task.cancel()
            if state.napcat_ws is ws:
                state.napcat_ws, state.napcat_connected, state.qq_online = None, False, False
                state.self_id = None
                await broadcast_to_clients(status_event())
            await asyncio.gather(*startup, return_exceptions=True)
        finally:
            napcat_transition_lock.release()
    return ws

async def handle_recall(target_id, message_id, platform_id=False):
    async with state.lock:
        result = await store.call('recall_message', message_id, target_id=target_id, platform_id=platform_id)
        for message in result['messages']:
            event = 'message_recalled' if message.get('is_recalled') else 'message_updated'
            await broadcast_to_clients({'type': event, **message, 'revision': result['revision']})

outbound_tasks = set()
client_owners = {}


def send_result(record):
    parts = [{key: part[key] for key in ('index', 'status', 'message_id', 'platform_ids') if key in part}
             for part in record.get('result', {}).get('parts', [])]
    result = {'type': 'send_result', 'request_id': record['request_id'], 'target_id': record['target_id'],
              'status': record['status'], 'content': record.get('result', {}).get('content', ''), 'parts': parts}
    if len(parts) == 1:
        result['message_id'] = parts[0].get('message_id')
    return result


async def publish_outbound_part(request_id, index):
    # Store commits history and clears publication_pending in ONE transaction.
    # A cancelled await may still commit; never overwrite that flag from local parts.
    async with state.lock:
        result = await store.call('publish_outbound_part', request_id, index)
        if not result or not result.get('inserted'):
            return result
        message = result['message']
        if result.get('contact'):
            state.contacts[message['target_id']] = result['contact']
            await broadcast_to_clients({'type': 'contact_add', 'contact': result['contact'], 'revision': result['revision']})
        await broadcast_to_clients({'type': 'message', **message, 'revision': result['revision']})
        return result


async def finish_outbound(request_id, status, parts, wording):
    record = await store.call('get_outbound', request_id)
    # Existing durable publication flags win over possibly stale local flags after
    # cancellation (AsyncStore settles accepted transactions before propagating it).
    durable = {part['index']: part for part in record['result'].get('parts', [])}
    merged = [durable.get(part['index'], part) for part in parts]
    return await store.call('update_outbound', request_id, status, parts=merged, content=wording)


async def outbound_send(ws, data, client_ip, owner=None):
    target = data['target_id']
    kind, peer = parse_target(target, data.get('msg_type'))
    request_id = data['request_id']
    # begin + is_new is transactional; never retry existing pending/unknown requests.
    try:
        record = await store.call('begin_outbound', request_id, target, data)
    except StorageBusy:
        await queue_client_message(ws, {'type': 'send_result', 'request_id': request_id, 'target_id': target,
                                        'status': 'failed', 'content': '持久发送队列已满，未执行发送'})
        return
    except ValueError:
        await queue_client_message(ws, {'type': 'toast', 'content': '请求ID已用于其他操作'})
        return
    await queue_client_message(ws, send_result(record))
    if not record['is_new']:
        return
    parts = []
    status, wording = 'failed', '发送失败'
    try:
        if not state.napcat_connected or not state.napcat_ws or state.napcat_ws.closed:
            raise ValueError('NapCat 未连接')
        action = 'send_private_msg' if kind == 'private' else 'send_group_msg'
        params = {'user_id' if kind == 'private' else 'group_id': int(peer), 'auto_escape': False}
        payload = []
        quote = data.get('quote')
        quote_id = None
        if quote:
            quote_id = quote.get('id')
            if not isinstance(quote_id, str):
                raise ValueError('无效引用')
            original = await store.call('get_message', target, quote_id)
            if not original or original.get('is_recalled'):
                raise ValueError('引用消息不可用')
            ids = await store.call('platform_ids', target, quote_id)
            live = next((x['platform_id'] for x in ids if not x['recalled']), None)
            if not live:
                raise ValueError('引用消息没有平台ID')
            payload.append({'type': 'reply', 'data': {'id': live}})
        local_path = data.get('file_local_path')
        file_segment = None
        operations = []
        if local_path:
            local_path = safe_upload_path(local_path)
            if not local_path:
                raise ValueError('文件路径无效')
            binding = upload_bindings.get(local_path)
            upload_id = data.get('file_upload_id') or data.get('upload_request_id')
            if (not binding or binding['owner'] is not owner or binding['target_id'] != target
                    or binding['upload_id'] != upload_id or binding['expires'] <= time.time()
                    or binding['request_id'] not in (None, request_id)):
                raise ValueError('附件不属于当前会话或请求，请重新上传')
            binding['request_id'] = request_id
            local_url = '/files/' + os.path.basename(local_path)
            if data.get('file_is_image'):
                image = await asyncio.to_thread(encode_upload_image, local_path)
                payload.append({'type': 'image', 'data': {'file': 'base64://' + image}})
                file_segment = {'type': 'image', 'url': local_url}
            else:
                name = os.path.basename(data.get('file_orig_name', 'file').replace('\\', '/'))[:255]
                with open_upload(local_path) as file:
                    size = os.fstat(file.fileno()).st_size
                if size > MAX_TRANSFER:
                    raise ValueError('文件过大')
                file_segment = {'type': 'file', 'url': local_url, 'name': name, 'size': size}
                if not PUBLIC_BASE_URL:
                    raise ValueError('未配置附件公网地址')
                operations.append(([{'type': 'file', 'data': {'file': PUBLIC_BASE_URL + local_url, 'name': name}}],
                                   {'content': '[文件] ' + name, 'file_name': name, 'file_size': size,
                                    'file_url': local_url, 'segments': [file_segment]}, 600.0))
        time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        content = data.get('content', '')
        if content:
            # Restore the original wire format: 正文 / IP / time.
            text = '正文:' + content
            if DISCLOSE_CLIENT_IP:
                text += '\nIP:' + str(client_ip)
            text += '\n' + time_str
            payload.append({'type': 'text', 'data': {'text': text}})
        if payload and any(x['type'] != 'reply' for x in payload):
            segments = ([file_segment] if file_segment and file_segment['type'] == 'image' else [])
            if content:
                segments.append({'type': 'text', 'text': content})
            operations.append((payload, {'content': content or '[图片]', 'segments': segments,
                                        'media_url': file_segment['url'] if file_segment and file_segment['type'] == 'image' else None}, 120.0))
        if not operations:
            raise ValueError('空消息')
        for index, (segments, display, deadline) in enumerate(operations):
            if not sessions.is_active(owner):
                status = 'partial' if parts else 'failed'
                wording = '会话已结束，未继续发送'
                break
            response = await send_qq_api_and_wait(action, {**params, 'message': segments}, timeout=deadline)
            if response is None:
                status = 'partial' if parts else 'unknown'
                wording = '部分已发送；其余结果未知，勿盲目重试' if parts else '发送结果未知，请先确认，勿盲目重试'
                break
            if not isinstance(response, dict) or response.get('status') != 'ok' or response.get('retcode') != 0:
                status = 'partial' if parts else 'failed'
                wording = '部分已发送，后续发送失败' if parts else '发送失败'
                break
            result_data = response.get('data')
            platform = result_data.get('message_id') if isinstance(result_data, dict) else None
            # A successful side effect without an ID is still recorded, but cannot be recalled.
            mid = 'out_' + hashlib.sha256((request_id + ':' + str(index)).encode()).hexdigest()[:32]
            message = {**display, 'message_id': mid, 'target_id': target,
                       'sender_name': str(state.self_id or '我'), 'sender_id': str(state.self_id or ''),
                       'direction': 'outgoing', 'request_id': request_id,
                       'napcat_message_ids': [str(platform)] if platform is not None else [],
                       'time': time_str}
            parts.append({'index': index, 'status': 'confirmed', 'message_id': mid,
                          'platform_ids': message['napcat_message_ids'], 'message': message,
                          'quote_id': quote_id, 'publication_pending': True})
            # Persist the external result before local publication; only the atomic
            # publication transaction may clear its recovery marker.
            await store.call('update_outbound', request_id, 'pending', parts=parts)
            await publish_outbound_part(request_id, index)
            parts[-1]['publication_pending'] = False
        else:
            status, wording = 'confirmed', '已发送'
    except asyncio.CancelledError:
        await finish_outbound(request_id, 'partial' if parts else 'unknown', parts,
                              '操作中断，结果可能未知，勿盲目重试')
        raise
    except ValueError as exc:
        status = 'partial' if parts else 'failed'
        wording = str(exc)
    except Exception:
        status = 'partial' if parts else 'unknown'
        wording = '发送结果需核实，勿盲目重试'
        logger.error('outbound operation could not be completed')
    record = await finish_outbound(request_id, status, parts, wording)
    await queue_client_message(ws, send_result(record))


async def handle_client_payload(ws, data, owner, client_ip):
    if not sessions.is_active(owner, touch=True):
        await ws.close(code=1008, message=b'session expired')
        return
    try:
        data = validate_client_message(data)
        kind = data['type']
        if kind == 'ping':
            await queue_client_message(ws, {'type': 'pong'})
        elif kind == 'get_history':
            await send_history(ws, data['target_id'])
        elif kind == 'get_send_result':
            record = await store.call('get_outbound', data['request_id'])
            if record:
                if data.get('target_id') not in (None, record['target_id']):
                    raise ValueError('request target mismatch')
                await queue_client_message(ws, send_result(record))
        elif kind == 'send_message':
            # Independent of the receive loop: logout, ping and shutdown remain responsive.
            # The transactional request ID gate prevents concurrent duplicate sends.
            if len(outbound_tasks) >= 64:
                raise ValueError('发送任务已满')
            task = spawn(outbound_send(ws, data, client_ip, owner))
            outbound_tasks.add(task)
            task.add_done_callback(outbound_tasks.discard)
        elif kind == 'recall_message':
            target, mid = data['target_id'], data['message_id']
            message = await store.call('get_message', target, mid)
            if not message or message.get('direction') != 'outgoing':
                raise ValueError('只能撤回自己发送的消息')
            ids = await store.call('platform_ids', target, mid)
            if not ids:
                raise ValueError('消息没有可撤回的平台ID')
            for item in ids:
                if item['recalled']:
                    continue
                response = await send_qq_api_and_wait('delete_msg', {'message_id': item['platform_id']})
                if not isinstance(response, dict) or response.get('status') != 'ok' or response.get('retcode') != 0:
                    raise ValueError('撤回结果未知，请先确认' if response is None else '撤回失败')
                await handle_recall(target, item['platform_id'], platform_id=True)
    except StorageBusy:
        await queue_client_message(ws, {'type': 'toast', 'content': '持久存储繁忙，请稍后查询操作状态'})
    except (ValueError, TypeError, KeyError, RecursionError):
        await queue_client_message(ws, {'type': 'toast', 'content': '请求无效或操作未完成，请检查目标与消息状态'})


async def initialize_client(ws, owner):
    queue = asyncio.Queue(maxsize=BROADCAST_QUEUE_LIMIT)
    queue.bytes_used = 0
    client_senders[ws] = (queue, spawn(client_sender(ws, queue)))
    client_owners[ws] = owner
    # Serialize snapshot boundary against all publish paths; initial contacts use backpressure.
    async with state.lock:
        await queue_client_message(ws, status_event(), backpressure=True)
        for contact in await store.call('list_contacts'):
            if not await queue_client_message(ws, {'type': 'contact_add', 'contact': contact}, backpressure=True):
                return
        state.clients.add(ws)


async def client_ws_handler(request):
    owner = sessions.authenticate(request, websocket=True)
    if owner is None:
        # Some old Android WebViews do not attach cookies to a WebSocket upgrade.
        # Accept an active session token in the query for this handshake only;
        # the access logger records route templates, never query strings.
        token = request.query.get('token')
        candidate = sessions.sessions.get(token) if isinstance(token, str) else None
        if sessions.is_active(candidate, touch=True):
            sessions.check_origin(request, required=False)
            owner = candidate
    if owner is None:
        raise web.HTTPUnauthorized()
    ws = web.WebSocketResponse(max_msg_size=CLIENT_FRAME_BYTES, heartbeat=30)
    await ws.prepare(request)
    resource = 'ws:' + secrets.token_hex(16)
    if not sessions.bind(owner, resource, lambda: ws.close(code=1008, message=b'session revoked')):
        await ws.close(code=1013, message=b'session capacity')
        return ws
    try:
        await initialize_client(ws, owner)
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except (ValueError, RecursionError):
                    await queue_client_message(ws, {'type': 'toast', 'content': '无效JSON请求'})
                    continue
                await handle_client_payload(ws, data, owner, sessions.client_ip(request))
    finally:
        state.clients.discard(ws)
        client_owners.pop(ws, None)
        remove_client_sender(ws)
        sessions.unbind(owner, resource)
    return ws

async def web_handler(request):
    return web.Response(text=(TEMPLATE_DIR / 'main.html').read_text(encoding='utf-8'), content_type='text/html', charset='utf-8')

async def web_nr_handler(request):
    return web.Response(text=(TEMPLATE_DIR / 'nr.html').read_text(encoding='utf-8'), content_type='text/html', charset='utf-8')

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
        if not stat.S_ISREG(info.st_mode) or size > MAX_TRANSFER or info.st_mtime < time.time() - UPLOAD_TTL:
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
            chunk = await io_safely(f.read, min(65536, remaining))
            if not chunk:
                break
            await response.write(chunk)
            remaining -= len(chunk)
        await response.write_eof()
        return response
    finally:
        f.close()

async def health_handler(request):
    # Public process/transport booleans only; no identities, paths, credentials or counts.
    return web.json_response({'ok': True, 'napcat_connected': state.napcat_connected,
                              'qq_online': state.qq_online})


async def ready_handler(request):
    # Readiness means durable local service ready, not that the external QQ account is online.
    return web.json_response({'ready': ready and not shutdown_event.is_set()},
                             status=200 if ready and not shutdown_event.is_set() else 503)


async def storage_maintenance():
    while True:
        await store.call('maintenance')
        await asyncio.sleep(3600)


async def recover_pending_outbound():
    # Replay only explicitly unpublished local results across pending/partial/unknown.
    # Cleared published messages have no marker and therefore cannot resurrect.
    # This NEVER replays an external QQ operation.
    after = None
    while True:
        records = await store.call('pending_outbound', limit=100, after=after)
        if not records:
            break
        for record in records:
            for part in record['result'].get('parts', []):
                if part.get('publication_pending') is True:
                    await store.call('publish_outbound_part', record['request_id'], part['index'])
            after = record['request_id']
    await store.call('recover_outbound')


async def shutdown_transports(app):
    global ready
    ready = False
    shutdown_event.set()
    sockets = list(state.clients)
    if state.napcat_ws is not None:
        fail_pending_for(state.napcat_ws)
        sockets.append(state.napcat_ws)
    try:
        async with asyncio.timeout(3):
            await asyncio.gather(*(ws.close(code=1001, message=b'server shutdown') for ws in sockets), return_exceptions=True)
    except TimeoutError:
        pass


async def application_lifecycle(app):
    global store, ready, media_queue
    shutdown_event.clear()
    inbox_available.clear()
    media_queue = asyncio.Queue(maxsize=128)
    os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
    os.makedirs(UPLOAD_DIR, mode=0o700, exist_ok=True)
    attachment_budget.directory = Path(UPLOAD_DIR)
    store = await AsyncStore.open(DB_FILE, legacy_data_path=DATA_FILE,
                                 legacy_settings_path=SETTINGS_FILE, defaults=DEFAULT_SETTINGS)
    try:
        state.contacts = {c['id']: c for c in await store.call('list_contacts')}
        settings.update((await store.call('get_settings'))['settings'])
        await recover_pending_outbound()
        spawn(storage_maintenance())
        spawn(inbox_worker())
        spawn(media_worker())
        spawn(media_worker())
        spawn(cleanup_old_files())
        spawn(bridge_cleanup())
        inbox_available.set()
        ready = True
        yield
    finally:
        await shutdown_transports(app)
        tasks = list(background_tasks)
        for task in tasks:
            task.cancel()
        try:
            async with asyncio.timeout(DRAIN_TIMEOUT):
                await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            logger.warning('background task shutdown deadline reached')
        await store.close()
        store = None
        state.napcat_ws = None
        state.napcat_connected = state.qq_online = False
        state.self_id = None
        state.clients.clear()
        client_owners.clear()
        bridge_sessions.clear()
        upload_bindings.clear()
        client_senders.clear()
        outbound_tasks.clear()


def create_app():
    app = web.Application(middlewares=[error_middleware, security_middleware(sessions.client_ip)], client_max_size=MAX_TRANSFER + 1024 * 1024)
    app.on_response_prepare.append(response_headers)
    app.cleanup_ctx.append(application_lifecycle)
    app.on_shutdown.append(shutdown_transports)
    sessions.install(app)
    app.router.add_get('/healthz', health_handler)
    app.router.add_get('/readyz', ready_handler)
    app.router.add_get('/ws', napcat_ws_handler)
    app.router.add_get('/', napcat_ws_handler)
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
    app.router.add_static('/static', path=str(Path(__file__).resolve().parent / 'static'))
    return app


async def main():
    load_credentials()
    if not WEB_PASSWORD or not EXPECTED_TOKEN:
        raise RuntimeError('请设置 WEB_PASSWORD 和 NAPCAT_ACCESS_TOKEN')
    if not PUBLIC_BASE_URL.startswith(('http://', 'https://')):
        raise RuntimeError('请配置 PUBLIC_BASE_URL')
    runner = web.AppRunner(create_app(), access_log_class=SafeAccessLogger, shutdown_timeout=DRAIN_TIMEOUT)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_event.set)
    try:
        await runner.setup()
        site = web.TCPSite(runner, os.environ.get('CHAT_HOST', '0.0.0.0'), int(os.environ.get('CHAT_PORT', '4001')))
        await site.start()
        logger.info('WebChat started')
        await shutdown_event.wait()
    finally:
        await runner.cleanup()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
