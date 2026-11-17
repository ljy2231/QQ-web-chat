import asyncio
from pathlib import Path
import tempfile
import unittest
from aiohttp import web
from webchat.resources import RequestBudget, DiskBudget, io_safely


class ResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_admission_is_immediate_and_releases(self):
        budget = RequestBudget(limit=2, per_ip=1)
        async with budget.admit('one'):
            with self.assertRaises(web.HTTPServiceUnavailable):
                async with budget.admit('one'):
                    pass
            async with budget.admit('two'):
                with self.assertRaises(web.HTTPServiceUnavailable):
                    async with budget.admit('three'):
                        pass
        self.assertEqual(budget.active, 0)
        self.assertEqual(budget.active_ips, {})

    async def test_rate_map_is_bounded_and_expires(self):
        current = [0]
        budget = RequestBudget(rate=1, max_ips=1, clock=lambda: current[0])
        async with budget.admit('one', rate_limit=True):
            pass
        with self.assertRaises(web.HTTPTooManyRequests):
            async with budget.admit('one', rate_limit=True):
                pass
        with self.assertRaises(web.HTTPServiceUnavailable):
            async with budget.admit('two', rate_limit=True):
                pass
        current[0] = 61
        async with budget.admit('two', rate_limit=True):
            pass
        self.assertEqual(len(budget.attempts), 1)

    async def test_disk_reservations_and_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = DiskBudget(directory, max_bytes=10, min_free=0, max_files=3)
            async with budget.reserve(6):
                with self.assertRaises(web.HTTPInsufficientStorage):
                    async with budget.reserve(6):
                        pass
            self.assertEqual(budget._reserved, 0)
            Path(directory, 'synthetic').write_bytes(b'12345678')
            with self.assertRaises(web.HTTPInsufficientStorage):
                async with budget.reserve(3):
                    pass

    async def test_io_cancellation_waits_for_worker(self):
        import threading
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def worker():
            entered.set()
            release.wait(1)
            finished.set()
        task = asyncio.create_task(io_safely(worker))
        await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(finished.is_set())


if __name__ == '__main__':
    unittest.main()
