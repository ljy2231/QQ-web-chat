"""Bounded HTTP admission and response headers. No production-specific settings."""
import asyncio
from aiohttp import web
from .resources import RequestBudget


def security_middleware(identity):
    connections = RequestBudget(limit=128, per_ip=32)
    public_work = RequestBudget(limit=8, per_ip=3, rate=20)
    downloads = RequestBudget(limit=16, per_ip=4, rate=120)

    @web.middleware
    async def middleware(request, handler):
        key = identity(request)
        async with connections.admit(key):
            if request.path == '/api/wallpaper':
                async with public_work.admit(key, rate_limit=True):
                    async with asyncio.timeout(75):
                        response = await handler(request)
                        await finish_buffered(request, response)
                        return response
            if request.path.startswith('/files/') or request.path in ('/download', '/proxy_img'):
                async with downloads.admit(key, rate_limit=True):
                    async with asyncio.timeout(120):
                        response = await handler(request)
                        await finish_buffered(request, response)
                        return response
            return await handler(request)
    return middleware


async def finish_buffered(request, response):
    # Includes sending buffered fetch bodies in the admission lifetime; streaming
    # file handlers already finish before returning. TCP delivery is not an ACK.
    if isinstance(response, web.Response) and not response.prepared:
        await response.prepare(request)
        await response.write_eof()


async def response_headers(request, response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('Referrer-Policy', 'same-origin')
    if request.path in ('/web', '/web/nr'):
        response.headers.setdefault('Cache-Control', 'no-store')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Content-Security-Policy',
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob: data: https:; media-src 'self' blob:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
    if request.path.startswith('/api/'):
        response.headers.setdefault('Cache-Control', 'no-store')
    if request.path.startswith('/files/'):
        response.headers.setdefault('Cache-Control', 'private, no-store')
