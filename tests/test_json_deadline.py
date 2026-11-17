import asyncio
import unittest
from aiohttp import web
from webchat.auth import json_object


class JsonDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_body_times_out_and_closes_connection(self):
        class SlowContent:
            async def iter_chunked(self, size):
                yield b'{'
                await asyncio.Event().wait()
        class Request:
            content_length = 20
            content_type = 'application/json'
            content = SlowContent()
        with self.assertRaises(web.HTTPRequestTimeout) as result:
            await json_object(Request(), read_timeout=0.01)
        self.assertFalse(result.exception.keep_alive)


if __name__ == '__main__':
    unittest.main()
