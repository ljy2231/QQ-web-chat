"""Application-wide request and filesystem budgets (single service process)."""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
import os
from pathlib import Path
import shutil
import time

from aiohttp import web


class RequestBudget:
    """Immediate admission: no unbounded waiter queue or per-IP map."""
    def __init__(self, limit=64, per_ip=12, rate=120, period=60, max_ips=1024, clock=time.monotonic):
        self.limit, self.per_ip, self.rate = limit, per_ip, rate
        self.period, self.max_ips, self.clock = period, max_ips, clock
        self.active = 0
        self.active_ips = {}
        self.attempts = OrderedDict()

    @asynccontextmanager
    async def admit(self, identity='anonymous', *, rate_limit=False):
        now = self.clock()
        if rate_limit:
            for key in list(self.attempts):
                items = self.attempts[key]
                while items and items[0] <= now - self.period:
                    items.popleft()
                if not items:
                    del self.attempts[key]
            if identity not in self.attempts and len(self.attempts) >= self.max_ips:
                raise web.HTTPServiceUnavailable(text='request budget busy')
            items = self.attempts.setdefault(identity, deque())
            if len(items) >= self.rate:
                raise web.HTTPTooManyRequests(headers={'Retry-After': str(self.period)})
            items.append(now)
        if self.active >= self.limit or self.active_ips.get(identity, 0) >= self.per_ip:
            raise web.HTTPServiceUnavailable(text='request capacity reached')
        self.active += 1
        self.active_ips[identity] = self.active_ips.get(identity, 0) + 1
        try:
            yield
        finally:
            self.active -= 1
            count = self.active_ips.get(identity, 1) - 1
            if count:
                self.active_ips[identity] = count
            else:
                self.active_ips.pop(identity, None)


class DiskBudget:
    """Conservative reservations prevent concurrent uploads exceeding the quota.

    All writers (uploads and cached media) must reserve before opening a file;
    do not run multiple service processes or independent writers in this folder.
    """
    def __init__(self, directory, max_bytes=1024*1024*1024, min_free=512*1024*1024,
                 max_files=10000, clock=time.monotonic):
        self.directory = Path(directory)
        self.max_bytes, self.min_free, self.max_files = max_bytes, min_free, max_files
        self.clock = clock
        self._lock = asyncio.Lock()
        self._reserved = 0
        self._reservations = 0

    def _usage(self):
        total, count = 0, 0
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.scandir(self.directory) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
                    count += 1
        return total, count, shutil.disk_usage(self.directory).free

    @asynccontextmanager
    async def reserve(self, maximum):
        if not isinstance(maximum, int) or maximum < 0:
            raise ValueError('invalid reservation')
        async with self._lock:
            used, files, free = await asyncio.to_thread(self._usage)
            if (used + self._reserved + maximum > self.max_bytes or
                    files + self._reservations >= self.max_files or
                    free - self._reserved - maximum < self.min_free):
                raise web.HTTPInsufficientStorage(text='attachment storage budget exceeded')
            self._reserved += maximum
            self._reservations += 1
        try:
            yield
        finally:
            # No await: cancellation cannot strand a reservation.
            self._reserved -= maximum
            self._reservations -= 1


async def io_safely(function, *args):
    """Wait for filesystem thread completion before a caller closes its handle."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result
