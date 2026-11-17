import unittest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from webchat.http_security import security_middleware, response_headers


class HttpSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_rate_limit_and_headers(self):
        app = web.Application(middlewares=[security_middleware(lambda request: 'synthetic')])
        app.on_response_prepare.append(response_headers)
        async def page(request):
            return web.Response(text='synthetic')
        for path in ('/web', '/api/wallpaper', '/files/test'):
            app.router.add_get(path, page)
        async with TestClient(TestServer(app)) as client:
            response = await client.get('/web')
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
            self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
            response = await client.get('/files/test')
            self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
            for _ in range(20):
                response = await client.get('/api/wallpaper')
                self.assertEqual(response.status, 200)
                await response.read()
            response = await client.get('/api/wallpaper')
            self.assertEqual(response.status, 429)


if __name__ == '__main__':
    unittest.main()
