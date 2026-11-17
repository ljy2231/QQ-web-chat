"""Offline file/SSRF regressions: synthetic credentials, temporary files, no sockets.

Load server under a private module name rather than mutating the cached module
used by test_messaging. Neither create_app nor the production lifecycle runs.
Every DNS answer, HTTP response and multipart body below is synthetic. A socket
tripwire makes an accidental real DNS lookup, connection or listener fail closed.
"""
import asyncio
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import socket
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlparse

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from webchat.auth import SessionManager
from webchat.resources import DiskBudget


ROOT = Path(__file__).resolve().parents[1]
PASSWORD = 'synthetic-file-network-password'
PUBLIC = '8.8.8.8'
DENIED = (
    '127.0.0.1', '0.0.0.0', '10.0.0.1', '172.16.0.1', '192.168.0.1',
    '169.254.169.254', '100.100.100.200', '::1', '::', 'fc00::1', 'fe80::1',
    '::ffff:127.0.0.1', '::ffff:169.254.169.254',
    '224.0.0.1', '239.255.255.250', 'ff02::1', 'ff0e::1',
    '::ffff:224.0.0.1',
)


def blocked_socket(*args, **kwargs):
    # Do not print arguments: an unexpected URL could contain a credential.
    raise AssertionError('real network operation forbidden in offline test')


class OfflineCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='file-network-test-')
        self.addCleanup(self.temp.cleanup)
        for method in ('connect', 'connect_ex', 'bind', 'listen', 'sendto'):
            tripwire = patch.object(socket.socket, method, blocked_socket)
            tripwire.start()
            self.addCleanup(tripwire.stop)
        for method in ('getaddrinfo', 'create_connection'):
            tripwire = patch.object(socket, method, blocked_socket)
            tripwire.start()
            self.addCleanup(tripwire.stop)
        spec = importlib.util.spec_from_file_location('_offline_file_network_server', ROOT / 'server.py')
        self.s = importlib.util.module_from_spec(spec)
        # Isolate all configuration, including disk budgets, from the host env.
        with patch.dict(os.environ, {
            'WEB_PASSWORD': PASSWORD, 'NAPCAT_ACCESS_TOKEN': 'synthetic-only',
            'CHAT_DATA_DIR': self.temp.name, 'PUBLIC_BASE_URL': '',
            'TRUSTED_PROXIES': '', 'ADMIN_QQ_ID': '',
            'ATTACHMENT_MAX_BYTES': str(1024 * 1024 * 1024),
            'MIN_FREE_BYTES': '0', 'ATTACHMENT_MAX_FILES': '1000',
        }):
            spec.loader.exec_module(self.s)
        self.uploads = Path(self.s.UPLOAD_DIR)
        self.uploads.mkdir()
        self.s.sessions = SessionManager(PASSWORD)
        self.addAsyncCleanup(self.s.sessions.close)
        self.owner = await self.s.sessions.issue()
        self.s.upload_bindings.clear()

    def request(self, method='GET', path='/', *, authenticated=False, headers=None):
        values = {'Host': '127.0.0.1:49152'}
        if authenticated:
            values.update({'Authorization': 'Bearer ' + self.owner.token,
                           'Origin': 'http://127.0.0.1:49152'})
        values.update(headers or {})
        transport = Mock()
        transport.get_extra_info.side_effect = lambda key, default=None: (
            ('127.0.0.1', 49153) if key == 'peername' else default)
        return make_mocked_request(method, path, headers=values, transport=transport)

    def file(self, name=None, body=b'synthetic-file'):
        path = self.uploads / (name or ('a' * 32 + '.txt'))
        path.write_bytes(body)
        return path

    def multipart_request(self, fields, *, authenticated=True, headers=None):
        request = self.request('POST', '/api/upload', authenticated=authenticated, headers=headers)
        reader = SimpleNamespace(next=AsyncMock(side_effect=[*fields, None]))
        request.multipart = AsyncMock(return_value=reader)
        return request

    @staticmethod
    def field(name, body=b'', filename=None):
        return SimpleNamespace(name=name, filename=filename,
                               read_chunk=AsyncMock(side_effect=[body, b'']))

    async def fetch(self, url, responses=(), dns=None, limit=32):
        network = FakeNetwork(responses, dns or {})
        with patch.object(self.s.aiohttp.resolver, 'DefaultResolver', return_value=network.dns), \
                patch.object(self.s.aiohttp, 'TCPConnector', network.connector), \
                patch.object(self.s.aiohttp, 'ClientSession', network.session):
            try:
                result = await self.s.safe_fetch(url, limit=limit)
            finally:
                self.assertEqual(self.s.remote_slots._value, 4, 'fetch leaked capacity')
                self.assertTrue(network.closed, 'fetch leaked HTTP session')
        return result, network


class FakeDNS:
    def __init__(self, records):
        self.records = records
        self.calls = []
        self.closed = False

    async def resolve(self, host, port=0, family=socket.AF_INET):
        self.calls.append((host, port, family))
        if host not in self.records:
            raise AssertionError('unexpected synthetic DNS name')
        return [{'hostname': host, 'host': address, 'port': port,
                 'family': socket.AF_INET6 if ':' in address else socket.AF_INET,
                 'proto': 0, 'flags': 0} for address in self.records[host]]

    async def close(self):
        self.closed = True


class FakeResponse:
    def __init__(self, status=200, headers=None, chunks=(b'ok',), length=None,
                 content_type='application/octet-stream'):
        self.status, self.headers = status, headers or {}
        self.chunks, self.content_length = chunks, length
        self.content_type = content_type
        self.content = self
        self.reads = 0

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk


class FakeNetwork:
    """ClientSession/TCPConnector boundary fake retaining the real URL policy."""
    def __init__(self, responses, dns):
        self.responses = list(responses)
        self.dns = FakeDNS(dns)
        self.requests = []
        self.closed = False
        self.resolver = None

    def connector(self, **kwargs):
        if kwargs.get('use_dns_cache') is not False:
            raise AssertionError('DNS cache must be disabled')
        self.resolver = kwargs['resolver']
        return self

    def session(self, **kwargs):
        if kwargs.get('trust_env') is not False:
            raise AssertionError('environment proxies must be disabled')
        if kwargs['connector'] is not self:
            raise AssertionError('unexpected connector')
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True
        await self.resolver.close()

    def get(self, url, **kwargs):
        if kwargs.get('allow_redirects') is not False:
            raise AssertionError('automatic redirects bypass hop validation')
        network = self

        class ResponseContext:
            async def __aenter__(self):
                network.requests.append(str(url))
                parsed = urlparse(str(url))
                try:
                    ipaddress.ip_address(parsed.hostname)
                except ValueError:
                    await network.resolver.resolve(parsed.hostname, parsed.port or 443)
                if not network.responses:
                    raise AssertionError('unexpected synthetic HTTP request')
                return network.responses.pop(0)

            async def __aexit__(self, *args):
                return False

        return ResponseContext()


class NetworkSecurityTests(OfflineCase):
    async def test_socket_guard_rejects_accidental_connections_and_dns(self):
        with socket.socket() as probe:
            with self.assertRaises(AssertionError):
                probe.connect(('127.0.0.1', 4001))
            with self.assertRaises(AssertionError):
                probe.bind(('127.0.0.1', 0))
        with self.assertRaises(AssertionError):
            socket.getaddrinfo('never-resolve.invalid', 443)

    async def test_public_ip_policy_including_mapped_multicast(self):
        for value in (PUBLIC, '1.1.1.1', '2606:4700:4700::1111', '::ffff:8.8.8.8'):
            with self.subTest(address=value):
                self.assertTrue(self.s.public_ip(value))
        for value in DENIED:
            with self.subTest(address=value):
                self.assertFalse(self.s.public_ip(value))
        with self.assertRaises(ValueError):
            self.s.public_ip('not-an-address')

    async def test_resolver_rejects_empty_or_any_nonpublic_record_in_both_orders(self):
        for address in DENIED:
            for records in ([PUBLIC, address], [address, PUBLIC]):
                with self.subTest(address=address, private_first=records[0] != PUBLIC):
                    delegate = FakeDNS({'mixed.invalid': records})
                    with patch.object(self.s.aiohttp.resolver, 'DefaultResolver', return_value=delegate):
                        resolver = self.s.PublicResolver()
                        with self.assertRaises(OSError):
                            await resolver.resolve('mixed.invalid', 443, socket.AF_UNSPEC)
                        await resolver.close()
                    self.assertTrue(delegate.closed)
        delegate = FakeDNS({'empty.invalid': []})
        with patch.object(self.s.aiohttp.resolver, 'DefaultResolver', return_value=delegate):
            resolver = self.s.PublicResolver()
            with self.assertRaises(OSError):
                await resolver.resolve('empty.invalid')
            await resolver.close()

    async def test_resolver_preserves_public_records_port_and_family(self):
        delegate = FakeDNS({'public.invalid': [PUBLIC, '2606:4700:4700::1111']})
        with patch.object(self.s.aiohttp.resolver, 'DefaultResolver', return_value=delegate):
            resolver = self.s.PublicResolver()
            result = await resolver.resolve('public.invalid', 8443, socket.AF_UNSPEC)
            await resolver.close()
        self.assertEqual([r['host'] for r in result], [PUBLIC, '2606:4700:4700::1111'])
        self.assertEqual(delegate.calls, [('public.invalid', 8443, socket.AF_UNSPEC)])
        self.assertTrue(all(r['port'] == 8443 for r in result))

    async def test_safe_fetch_rejects_nonpublic_literals_before_http(self):
        for address in DENIED:
            host = '[' + address + ']' if ':' in address else address
            with self.subTest(address=address), self.assertRaises(ValueError):
                await self.fetch('http://' + host + '/synthetic')

    async def test_safe_fetch_rejects_schemes_and_url_credentials(self):
        for url in ('file:///synthetic', 'ftp://8.8.8.8/a', '/relative',
                    'https://synthetic:synthetic@8.8.8.8/a', 'http:///no-host'):
            with self.subTest(case=url.split(':')[0]), self.assertRaises(ValueError):
                await self.fetch(url)

    async def test_safe_fetch_uses_public_resolver_for_mixed_dns(self):
        with self.assertRaises(OSError):
            await self.fetch('https://mixed.invalid/a', dns={'mixed.invalid': [PUBLIC, '127.0.0.1']})

    async def test_redirect_checks_every_hop_and_all_redirect_statuses(self):
        for status in (301, 302, 303, 307, 308):
            for location in ('http://127.0.0.1/private', 'http://169.254.169.254/meta',
                             'http://[::ffff:127.0.0.1]/private', 'http://224.0.0.1/multicast',
                             'file:///synthetic', 'https://user:pass@8.8.8.8/private'):
                with self.subTest(status=status, target=urlparse(location).hostname):
                    network = FakeNetwork([
                        FakeResponse(status, {'Location': '/second'}),
                        FakeResponse(status, {'Location': location}),
                    ], {'public.invalid': [PUBLIC]})
                    with patch.object(self.s.aiohttp.resolver, 'DefaultResolver', return_value=network.dns), \
                            patch.object(self.s.aiohttp, 'TCPConnector', network.connector), \
                            patch.object(self.s.aiohttp, 'ClientSession', network.session):
                        with self.assertRaises(ValueError):
                            await self.s.safe_fetch('https://public.invalid/start')
                    self.assertEqual(network.requests, ['https://public.invalid/start',
                                                        'https://public.invalid/second'])
                    self.assertTrue(network.closed)
                    self.assertEqual(self.s.remote_slots._value, 4)

    async def test_redirect_rechecks_new_hostname_dns(self):
        with self.assertRaises(OSError):
            await self.fetch('https://public.invalid/a', [
                FakeResponse(302, {'Location': 'https://mixed.invalid/b'})],
                dns={'public.invalid': [PUBLIC], 'mixed.invalid': [PUBLIC, '10.0.0.1']})

    async def test_redirect_relative_success_missing_location_and_hop_cap(self):
        result, network = await self.fetch('https://public.invalid/start', [
            FakeResponse(302, {'Location': '/final'}), FakeResponse(chunks=(b'safe',))],
            dns={'public.invalid': [PUBLIC]})
        self.assertEqual(result, (b'safe', 'application/octet-stream'))
        self.assertEqual(len(network.dns.calls), 2)
        with self.assertRaisesRegex(ValueError, 'missing redirect'):
            await self.fetch('https://8.8.8.8/a', [FakeResponse(302)])
        with self.assertRaisesRegex(ValueError, 'too many redirects'):
            await self.fetch('https://8.8.8.8/a', [FakeResponse(302, {'Location': '/a'}) for _ in range(5)])

    async def test_size_cap_enforces_header_and_stream_with_or_without_length(self):
        oversized = FakeResponse(length=9, chunks=(b'never-read',))
        with self.assertRaises(ValueError):
            await self.fetch('https://8.8.8.8/a', [oversized], limit=8)
        self.assertEqual(oversized.reads, 0)
        for advertised in (None, 1):
            with self.subTest(advertised=advertised), self.assertRaisesRegex(ValueError, 'size limit'):
                await self.fetch('https://8.8.8.8/a', [
                    FakeResponse(length=advertised, chunks=(b'1234', b'56789', b'not-read'))], limit=8)
        result, _ = await self.fetch('https://8.8.8.8/a', [
            FakeResponse(length=8, chunks=(b'1234', b'5678'))], limit=8)
        self.assertEqual(result[0], b'12345678')
        with self.assertRaises(ValueError):
            await self.fetch('https://8.8.8.8/a', [FakeResponse(status=503)])


class FileUploadSecurityTests(OfflineCase):
    async def serve(self, name):
        request = self.request(path='/files/' + name)
        request._match_info['name'] = name
        response = await self.s.files_handler(request)
        body = b''.join(call.args[0] for call in request._payload_writer.write.call_args_list)
        return response, body

    async def test_anonymous_html_svg_and_binary_are_sandboxed_attachments(self):
        body = b'<!doctype html><script>synthetic_marker()</script>'
        for extension in ('.html', '.svg', '.txt', ''):
            with self.subTest(extension=extension):
                path = self.file('a' * 32 + extension, body)
                response, actual = await self.serve(path.name)
                self.assertEqual(actual, body)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.content_type, 'application/octet-stream')
                self.assertEqual(response.headers['Content-Disposition'], 'attachment; filename="download"')
                self.assertEqual(response.headers['Content-Security-Policy'], "sandbox; default-src 'none'")
                self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(int(response.headers['Content-Length']), len(body))

    async def test_anonymous_image_and_cached_image_remain_sandboxed(self):
        for name in ('b' * 32 + '.png', 'img_' + 'c' * 32 + '.jpg'):
            with self.subTest(name=name):
                path = self.file(name, b'synthetic-image')
                response, body = await self.serve(path.name)
                self.assertEqual(body, b'synthetic-image')
                self.assertIn(response.content_type, ('image/png', 'image/jpeg'))
                self.assertEqual(response.headers['Content-Security-Policy'], "sandbox; default-src 'none'")
                self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')

    async def test_open_upload_accepts_only_regular_scoped_uuid_paths(self):
        path = self.file()
        with self.s.open_upload(str(path)) as source:
            self.assertEqual(source.read(), b'synthetic-file')
        outside = Path(self.temp.name) / ('d' * 32 + '.txt')
        outside.write_bytes(b'synthetic-outside')
        linked = self.uploads / ('e' * 32 + '.txt')
        linked.symlink_to(outside)
        directory = self.uploads / ('f' * 32)
        directory.mkdir()
        for candidate in (outside, linked, directory, self.uploads / 'arbitrary.txt',
                          self.uploads / '..' / outside.name, self.uploads / ('0' * 32 + '.txt')):
            with self.subTest(kind=candidate.name), self.assertRaises(OSError):
                self.s.open_upload(str(candidate))
            self.assertIsNone(self.s.safe_upload_path(str(candidate)))

    async def test_files_reject_symlink_arbitrary_paths_directories_and_fifo(self):
        outside = Path(self.temp.name) / 'outside.txt'
        outside.write_bytes(b'not-public')
        linked = self.uploads / ('e' * 32 + '.txt')
        linked.symlink_to(outside)
        directory = self.uploads / ('f' * 32)
        directory.mkdir()
        fifo = self.uploads / ('d' * 32 + '.txt')
        os.mkfifo(fifo)
        for name in (linked.name, directory.name, fifo.name, '../outside.txt', str(outside),
                     '%2e%2e%2foutside.txt', 'arbitrary.txt', 'a' * 32 + '.html/extra'):
            with self.subTest(kind=name.split('/')[-1]), self.assertRaises(web.HTTPNotFound):
                await asyncio.wait_for(self.serve(name), 1)

    async def test_files_older_than_seven_days_or_over_size_limit_are_not_public(self):
        path = self.file()
        now = time.time()
        self.assertEqual(self.s.UPLOAD_TTL, 7 * 86400)
        os.utime(path, (now - self.s.UPLOAD_TTL - 1,) * 2)
        with self.assertRaises(web.HTTPNotFound):
            await self.serve(path.name)
        os.utime(path, (now,) * 2)
        response, _ = await self.serve(path.name)
        self.assertEqual(response.status, 200)
        with patch.object(self.s, 'MAX_TRANSFER', 2):
            with self.assertRaises(web.HTTPNotFound):
                await self.serve(path.name)

    async def test_unauthenticated_upload_does_not_parse_or_write(self):
        request = self.multipart_request([self.field('file', b'data', 'a.html')], authenticated=False)
        response = await self.s.upload_handler(request)
        self.assertEqual(response.status, 401)
        request.multipart.assert_not_awaited()
        self.assertEqual(list(self.uploads.iterdir()), [])

    async def test_upload_requires_target_and_request_binding_before_file(self):
        cases = [[], [self.field('target_id', b'private:12')],
                 [self.field('request_id', b'upload-id')],
                 [self.field('target_id', b'12'), self.field('request_id', b'upload-id')]]
        for identity in cases:
            with self.subTest(fields=[field.name for field in identity]):
                request = self.multipart_request([*identity, self.field('file', b'data', 'a.html')])
                response = await self.s.upload_handler(request)
                self.assertEqual(response.status, 400)
                self.assertEqual(self.s.upload_bindings, {})
                self.assertEqual(list(self.uploads.iterdir()), [])
                self.assertEqual(self.s.upload_slots._value, 4)
                self.assertEqual(self.s.attachment_budget._reserved, 0)

    async def test_authenticated_upload_creates_session_target_binding_and_sandbox_delivery(self):
        body = b'<script>synthetic_only()</script>'
        request = self.multipart_request([
            self.field('target_id', b'private:12'), self.field('request_id', b'upload-id'),
            self.field('file', body, '../synthetic.html')])
        response = await self.s.upload_handler(request)
        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        path = Path(payload['local_path'])
        self.assertEqual(path.parent, self.uploads)
        self.assertEqual(path.read_bytes(), body)
        self.assertEqual(payload['orig_name'], 'synthetic.html')
        binding = self.s.upload_bindings[str(path)]
        self.assertIs(binding['owner'], self.owner)
        self.assertEqual((binding['target_id'], binding['upload_id']), ('private:12', 'upload-id'))
        self.assertIsNone(binding['request_id'])
        self.assertGreater(binding['expires'], time.time() + 7 * 86400 - 5)
        delivered, actual = await self.serve(path.name)
        self.assertEqual(actual, body)
        self.assertEqual(delivered.content_type, 'application/octet-stream')
        self.assertIn('attachment', delivered.headers['Content-Disposition'])

    async def test_outbound_rejects_missing_foreign_expired_or_reused_upload_binding(self):
        # No real NapCat: a minimal connected marker only reaches the authority
        # check; every external side-effect API is an assertion tripwire.
        path = self.file()
        self.s.state.napcat_connected = True
        self.s.state.napcat_ws = SimpleNamespace(closed=False)
        other_owner = await self.s.sessions.issue()
        valid = {'owner': self.owner, 'target_id': 'private:12',
                 'upload_id': 'upload-id', 'expires': time.time() + 60, 'request_id': None}
        variants = [None, {**valid, 'owner': other_owner},
                    {**valid, 'target_id': 'group:12'}, {**valid, 'upload_id': 'wrong-id'},
                    {**valid, 'expires': time.time() - 1},
                    {**valid, 'request_id': 'already-used'}]
        for index, binding in enumerate(variants):
            with self.subTest(binding_case=index):
                self.s.upload_bindings.clear()
                if binding is not None:
                    self.s.upload_bindings[str(path)] = binding
                request_id = 'synthetic-send-' + str(index)
                record = {'request_id': request_id, 'target_id': 'private:12',
                          'status': 'pending', 'is_new': True, 'result': {'parts': []}}

                async def store_call(method, *args):
                    if method != 'begin_outbound':
                        raise AssertionError('unexpected storage operation')
                    return record

                store = SimpleNamespace(call=AsyncMock(side_effect=store_call))
                queue = AsyncMock()
                finish = AsyncMock(return_value={**record, 'status': 'failed'})
                remote = AsyncMock(side_effect=blocked_socket)
                with patch.object(self.s, 'store', store), \
                        patch.object(self.s, 'queue_client_message', queue), \
                        patch.object(self.s, 'finish_outbound', finish), \
                        patch.object(self.s, 'send_qq_api_and_wait', remote), \
                        patch.object(self.s, 'send_qq_api', remote):
                    await self.s.outbound_send(object(), {
                        'target_id': 'private:12', 'request_id': request_id,
                        'file_local_path': str(path), 'file_upload_id': 'upload-id',
                        'file_is_image': True, 'content': '',
                    }, '127.0.0.1', owner=self.owner)
                remote.assert_not_awaited()
                finish.assert_awaited_once()
                self.assertEqual(finish.await_args.args[:3], (request_id, 'failed', []))
                self.assertIn('重新上传', finish.await_args.args[3])
                self.assertEqual(queue.await_args.args[1]['status'], 'failed')

    async def test_disk_budget_exhaustion_is_507_before_multipart_or_write(self):
        for used, files, free in ((1, 0, 100), (0, 1, 100), (0, 0, 9)):
            with self.subTest(used=used, files=files, free=free):
                budget = DiskBudget(self.uploads, max_bytes=10, min_free=0, max_files=1)
                request = self.multipart_request([self.field('file', b'data', 'a.txt')])
                with patch.object(self.s, 'MAX_TRANSFER', 10), \
                        patch.object(self.s, 'attachment_budget', budget), \
                        patch.object(budget, '_usage', return_value=(used, files, free)):
                    with self.assertRaises(web.HTTPInsufficientStorage) as caught:
                        await self.s.upload_handler(request)
                self.assertEqual(caught.exception.status, 507)
                request.multipart.assert_not_awaited()
                self.assertEqual(list(self.uploads.iterdir()), [])
                self.assertEqual((budget._reserved, budget._reservations), (0, 0))
                self.assertEqual(self.s.upload_slots._value, 4)


if __name__ == '__main__':
    unittest.main()
