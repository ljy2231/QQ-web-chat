"""Synthetic messaging integration: temporary storage and loopback random ports only."""
import asyncio
import importlib
import json
import os
import signal
import socket
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp import web, WSMsgType

from webchat.storage import StorageBusy
from webchat.auth import SessionManager
from webchat.messaging import (conversation_key, parse_target, normalize_segments,
                               validate_client_message, all_command, inbox_key)


class ProtocolTests(unittest.TestCase):
    def test_typed_targets_and_shape(self):
        self.assertNotEqual(conversation_key('private', 12), conversation_key('group', 12))
        for target in ('12', 'group:01', 'private:0', 'group:12:1', None):
            with self.assertRaises(ValueError):
                parse_target(target)
        with self.assertRaises(ValueError):
            parse_target('group:12', 'private')
        validate_client_message({'type': 'send_message', 'target_id': 'private:12',
                                 'request_id': 'r1', 'content': 'ok', 'file_orig_name': None})
        with self.assertRaises(ValueError):
            validate_client_message({'type': 'send_message', 'target_id': 'private:12',
                                     'request_id': 'r1', 'file_local_path': '/tmp/a'})

    def test_segments_and_commands(self):
        segments, text, quote = normalize_segments([
            {'type': 'reply', 'data': {'id': 77}},
            {'type': 'image', 'data': {'url': 'https://example.invalid/a'}},
            {'type': 'image', 'data': {'url': 'file:///etc/passwd'}},
            {'type': 'file', 'data': {'id': 'file1', 'name': '../a.txt', 'file_size': 5}},
            {'type': 'record', 'data': {}}, {'type': 'future', 'data': {}}])
        self.assertEqual(quote, '77')
        self.assertEqual(segments[3]['name'], 'a.txt')
        self.assertEqual(segments[3]['file_id'], 'file1')
        self.assertIn('[语音]', text)
        self.assertIn('[不支持的消息段]', text)
        self.assertIsNone(segments[2]['url'])
        self.assertEqual(all_command('= ALL', 'private', '1', '1'), 'help')
        self.assertIs(all_command('= ALL ON', 'private', '1', '1'), True)
        self.assertEqual(all_command('= ALL ON', 'group', '1', '1'), 'denied')


class MessagingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'WEB_PASSWORD': 'synthetic-password',
                                    'NAPCAT_ACCESS_TOKEN': 'synthetic-napcat',
                                    'PUBLIC_BASE_URL': '', 'TRUSTED_PROXIES': '',
                                    'CHAT_DATA_DIR': self.temp.name}):
            self.s = importlib.import_module('server')
        s = self.s
        s.DATA_DIR = self.temp.name
        s.UPLOAD_DIR = str(Path(self.temp.name) / 'files')
        s.DB_FILE = str(Path(self.temp.name) / 'db.sqlite3')
        s.DATA_FILE = str(Path(self.temp.name) / 'legacy.json')
        s.SETTINGS_FILE = str(Path(self.temp.name) / 'settings.json')
        s.EXPECTED_TOKEN = 'synthetic-napcat'
        s.ADMIN_QQ_ID = '12'
        s.PUBLIC_BASE_URL = ''
        s.sessions = SessionManager('synthetic-password')
        s.state = s.ServerState()
        s.shutdown_event = asyncio.Event()
        s.inbox_available = asyncio.Event()
        s.napcat_transition_lock = asyncio.Lock()
        s.settings.clear()
        s.settings.update(s.DEFAULT_SETTINGS)
        self.runner = web.AppRunner(s.create_app(), access_log=None, shutdown_timeout=1)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.assertNotEqual(port, 4001)
        self.base = 'http://127.0.0.1:' + str(port)
        self.http = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        response = await self.http.post(self.base + '/api/login', json={'password': 'synthetic-password'})
        self.assertEqual(response.status, 200)
        await response.read()
        self.tasks = []
        self.sockets = []
        self.actions = []
        self.send_count = 0
        self.send_behavior = None
        self.block_sends = asyncio.Event()
        self.block_media = asyncio.Event()
        self.block_media.set()

    async def asyncTearDown(self):
        try:
            await asyncio.wait_for(self.runner.cleanup(), 6)
        finally:
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            for ws in self.sockets:
                await ws.close()
            await self.http.close()
            self.temp.cleanup()

    async def allow(self, *peers):
        settings = await self.s.store.call('get_settings')
        settings['settings']['whitelist'] = list(peers)
        await self.s.store.call('save_settings', settings['settings'], settings['revision'])
        self.s.settings.update(settings['settings'])

    async def browser(self):
        ws = await self.http.ws_connect(self.base + '/client_ws', headers={'Origin': self.base})
        self.sockets.append(ws)
        await self.event(ws, 'status')
        return ws

    async def event(self, ws, kind, predicate=lambda d: True):
        async with asyncio.timeout(4):
            while True:
                msg = await ws.receive()
                if msg.type != WSMsgType.TEXT:
                    raise AssertionError('socket closed while awaiting ' + kind)
                data = json.loads(msg.data)
                if data.get('type') == kind and predicate(data):
                    return data

    async def napcat(self, online=True):
        ws = await self.http.ws_connect(self.base + '/ws',
                                       headers={'Authorization': 'Bearer synthetic-napcat'})
        self.sockets.append(ws)
        async def respond():
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                request = json.loads(msg.data)
                action = request['action']
                self.actions.append(request)
                status, code, data = 'ok', 0, {}
                if action == 'get_login_info':
                    data = {'user_id': 999}
                elif action == 'get_status':
                    data = {'online': online}
                elif action == 'get_group_info':
                    data = {'group_name': 'Synthetic group'}
                elif action in ('get_group_file_url', 'get_private_file_url'):
                    await self.block_media.wait()
                    data = {'url': 'https://example.invalid/file'}
                elif action in ('send_private_msg', 'send_group_msg'):
                    self.send_count += 1
                    if self.send_behavior == 'hold':
                        await self.block_sends.wait()
                    if self.send_behavior == 'second_failed' and self.send_count == 2:
                        status, code = 'failed', 1
                    data = {'message_id': 9000 + self.send_count}
                await ws.send_json({'echo': request['echo'], 'status': status, 'retcode': code, 'data': data})
        self.tasks.append(asyncio.create_task(respond()))
        return ws

    def incoming(self, platform=1, kind='private', content='= hello', segments=None):
        return {'post_type': 'message', 'message_type': kind, 'user_id': 12, 'group_id': 12,
                'sender': {'user_id': 12, 'nickname': 'Synthetic'}, 'message_id': platform,
                'message': segments if segments is not None else [{'type': 'text', 'data': {'text': content}}]}

    async def upload(self, target='private:12', uid='upload1', other=None):
        form = aiohttp.FormData()
        form.add_field('target_id', target)
        form.add_field('request_id', uid)
        form.add_field('file', b'synthetic-file', filename='a.txt', content_type='text/plain')
        client = other or self.http
        response = await client.post(self.base + '/api/upload', data=form, headers={'Origin': self.base})
        self.assertEqual(response.status, 200)
        return await response.json()

    async def test_health_cookie_ws_origin_and_logout(self):
        for path in ('/healthz', '/readyz', '/web', '/web/nr', '/api/session'):
            response = await self.http.get(self.base + path)
            self.assertEqual(response.status, 200)
            await response.read()
        with self.assertRaises(aiohttp.WSServerHandshakeError):
            await self.http.ws_connect(self.base + '/client_ws', headers={'Origin': 'https://wrong.invalid'})
        ws = await self.browser()
        response = await self.http.post(self.base + '/api/logout', json={}, headers={'Origin': self.base})
        self.assertEqual(response.status, 200)
        await response.read()
        self.assertIn((await asyncio.wait_for(ws.receive(), 3)).type,
                      (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING))

    async def test_more_than_64_contacts_and_history(self):
        for peer in range(100, 180):
            await self.s.store.call('upsert_contact', {'id': 'private:' + str(peer),
                                    'peer_id': str(peer), 'type': 'private', 'name': 'Synthetic'})
        ws = await self.browser()
        contacts = [await self.event(ws, 'contact_add') for _ in range(80)]
        self.assertEqual(len({d['contact']['id'] for d in contacts}), 80)
        await ws.send_json({'type': 'get_history', 'target_id': 'private:100'})
        history = await self.event(ws, 'history_data')
        self.assertEqual(history['messages'], [])
        self.assertIn('revision', history)
        await ws.send_json({'type': 'get_history', 'target_id': '100'})
        await self.event(ws, 'toast')

    async def test_online_inbox_typed_ids_quote_and_recall(self):
        await self.allow('12')
        browser = await self.browser()
        qq = await self.napcat(online=False)
        await self.event(browser, 'status', lambda d: d.get('self_id') == 999)
        self.assertFalse(self.s.state.qq_online)
        await qq.send_json({'post_type': 'meta_event', 'meta_event_type': 'heartbeat', 'status': {'online': True}})
        await self.event(browser, 'status', lambda d: d['connected'])
        await qq.send_json(self.incoming(1))
        first = await self.event(browser, 'message')
        self.assertEqual(first['target_id'], 'private:12')
        self.assertEqual(first['direction'], 'incoming')
        self.assertEqual(first['content'], 'hello')
        await qq.send_json(self.incoming(1))
        await qq.send_json(self.incoming(1, kind='group', content='group hello'))
        group = await self.event(browser, 'message', lambda d: d['target_id'] == 'group:12')
        self.assertNotEqual(group['message_id'], first['message_id'])
        await qq.send_json(self.incoming(2, segments=[{'type': 'reply', 'data': {'id': 1}},
                                                   {'type': 'text', 'data': {'text': '= quoted'}}]))
        quoted = await self.event(browser, 'message', lambda d: d['content'] == 'quoted')
        self.assertEqual(quoted['quote']['id'], first['message_id'])
        await qq.send_json({'post_type': 'notice', 'notice_type': 'friend_recall', 'user_id': 12, 'message_id': 1})
        await self.event(browser, 'message_recalled', lambda d: d['message_id'] == first['message_id'])
        history = await self.s.store.call('history', 'private:12')
        self.assertEqual(len(history['messages']), 2)
        self.assertTrue(history['messages'][0]['is_recalled'])
        self.assertEqual(history['messages'][1]['quote']['content'], '[消息已撤回]')
        self.assertEqual((await self.s.store.call('history', 'group:12'))['messages'][0]['content'], 'group hello')

    async def test_reply_does_not_bypass_private_prefix_and_all_is_per_target(self):
        await self.allow('12', '13')
        await self.s.process_inbound(self.incoming(1, segments=[{'type': 'reply', 'data': {'id': 5}},
                                                              {'type': 'text', 'data': {'text': 'hidden'}}]))
        self.assertEqual((await self.s.store.call('history', 'private:12'))['messages'], [])
        await self.s.process_inbound(self.incoming(2, content='= ALL ON'))
        self.assertTrue(await self.s.store.call('get_mode', 'private:12'))
        self.assertFalse(await self.s.store.call('get_mode', 'private:13'))
        await self.s.process_inbound(self.incoming(3, content='= ALL'))
        self.assertTrue(await self.s.store.call('get_mode', 'private:12'))
        await self.s.process_inbound(self.incoming(4, content='no prefix'))
        self.assertEqual((await self.s.store.call('history', 'private:12'))['messages'][0]['content'], 'no prefix')

    async def test_private_receipt_and_equals_help_menu_unchanged(self):
        self.s.ADMIN_QQ_ID = '12'
        calls = []
        async def fake(action, params):
            calls.append((action, params.get('message')))
        with patch.object(self.s, 'send_qq_api', fake):
            await self.allow('12', '13')
            # Forwarded private chat keeps the original receipt.
            await self.s.process_inbound(self.incoming(1, content='= hello'))
            self.assertEqual(calls[-1], ('send_private_msg', '[Web-QQ] 消息已转发到Web端'))
            # A duplicate delivery is not acknowledged twice.
            await self.s.process_inbound(self.incoming(1, content='= hello'))
            self.assertEqual(len(calls), 1)
            # Authorized group forwarding stays silent.
            await self.s.process_inbound(self.incoming(2, kind='group', content='group hello'))
            self.assertEqual((await self.s.store.call('history', 'group:12'))['messages'][0]['content'], 'group hello')
            self.assertNotIn('send_group_msg', [action for action, _ in calls])
            # The original help menu is restored verbatim.
            await self.s.process_inbound(self.incoming(3, content='='))
            self.assertEqual(calls[-1][1], '【指令菜单】\n= ALL 开启全量转发\n= CLEAR 清空聊天记录\n= REBOOT 重启服务器')
            other = self.incoming(4, content='=')
            other['sender']['user_id'] = 13
            other['user_id'] = 13
            await self.s.process_inbound(other)
            self.assertEqual(calls[-1][1], '【指令菜单】\n= ALL 开启全量转发')
            # A lone '=' in a group still gets no reply.
            count = len(calls)
            await self.s.process_inbound(self.incoming(5, kind='group', content='='))
            self.assertEqual(len(calls), count)

    async def test_unauthorized_private_equals_hint_is_preserved(self):
        self.s.ADMIN_QQ_ID = '12'
        calls = []
        async def fake(action, params):
            calls.append((action, params.get('message')))
        with patch.object(self.s, 'send_qq_api', fake):
            await self.allow('12')
            stranger = self.incoming(1, content='= hi')
            stranger['sender']['user_id'] = 77
            stranger['user_id'] = 77
            await self.s.process_inbound(stranger)
            self.assertEqual(calls[-1], ('send_private_msg', '你的ID:77 当前未授权，请联系管理员授权'))
            self.assertEqual((await self.s.store.call('history', 'private:77'))['messages'], [])
            # An unauthorized ordinary private message and a group message stay silent.
            count = len(calls)
            plain = self.incoming(2, content='= hello')
            plain['sender']['user_id'] = 78
            plain['user_id'] = 78
            plain['message'] = [{'type': 'text', 'data': {'text': 'hello'}}]
            await self.s.process_inbound(plain)
            group_plain = self.incoming(3, kind='group', content='= group')
            group_plain['group_id'] = 79
            await self.s.process_inbound(group_plain)
            self.assertEqual(len(calls), count)
            # A blacklisted private contact is ignored even with '='.
            settings = await self.s.store.call('get_settings')
            settings['settings']['blacklist'] = ['80']
            await self.s.store.call('save_settings', settings['settings'], settings['revision'])
            self.s.settings.update(settings['settings'])
            blocked = self.incoming(4, content='= hi')
            blocked['sender']['user_id'] = 80
            blocked['user_id'] = 80
            await self.s.process_inbound(blocked)
            self.assertEqual(len(calls), count)

    async def test_durable_inbox_replay(self):
        await self.allow('12')
        event = self.incoming(11)
        await self.s.store.call('enqueue_inbox', event, event_key=inbox_key(event))
        await self.runner.cleanup()
        self.runner = web.AppRunner(self.s.create_app(), access_log=None, shutdown_timeout=1)
        await self.runner.setup()
        async with asyncio.timeout(4):
            while (await self.s.store.call('pending_inbox')):
                await asyncio.sleep(.01)
        history = await self.s.store.call('history', 'private:12')
        self.assertEqual(len(history['messages']), 1)
        self.assertEqual(history['messages'][0]['content'], 'hello')

    async def test_pending_outbound_recovery_repairs_history_without_resend(self):
        message = {'message_id': 'out_repair', 'target_id': 'private:12', 'sender_id': '999',
                   'direction': 'outgoing', 'content': 'confirmed before crash', 'napcat_message_ids': ['42']}
        await self.s.store.call('begin_outbound', 'repair1', 'private:12', {'content': 'synthetic'})
        await self.s.store.call('update_outbound', 'repair1', 'pending', parts=[
            {'index': 0, 'status': 'confirmed', 'message_id': 'out_repair', 'platform_ids': ['42'],
             'message': message, 'publication_pending': True}])
        await self.s.recover_pending_outbound()
        record = await self.s.store.call('get_outbound', 'repair1')
        self.assertEqual(record['status'], 'unknown')
        self.assertEqual((await self.s.store.call('history', 'private:12'))['messages'][0]['message_id'], 'out_repair')
        self.assertEqual(self.send_count, 0)
        self.assertNotIn('message', self.s.send_result(record)['parts'][0])

    async def publication_fault(self, failure):
        browser = await self.browser()
        await self.napcat()
        owner = next(iter(self.s.sessions.sessions.values()))
        transport = next(iter(self.s.state.clients))
        real = self.s.store.call
        async def fault(method, *args, **kwargs):
            if method == 'publish_outbound_part':
                if failure == 'busy':
                    raise StorageBusy('synthetic full queue')
                if failure == 'cancel_before':
                    raise asyncio.CancelledError
                result = await real(method, *args, **kwargs)
                if failure == 'cancel_after':
                    raise asyncio.CancelledError
                return result
            return await real(method, *args, **kwargs)
        payload = {'type': 'send_message', 'target_id': 'private:12',
                   'request_id': 'publication-fault', 'content': 'synthetic confirmed'}
        with patch.object(self.s.store, 'call', fault):
            if failure.startswith('cancel'):
                with self.assertRaises(asyncio.CancelledError):
                    await self.s.outbound_send(transport, payload, '127.0.0.1', owner)
            else:
                await self.s.outbound_send(transport, payload, '127.0.0.1', owner)
        record = await real('get_outbound', 'publication-fault')
        self.assertEqual(record['status'], 'partial')
        self.assertEqual(record['result']['parts'][0]['publication_pending'], failure != 'cancel_after')
        self.assertEqual(self.send_count, 1)
        if failure == 'cancel_after':
            # Commit succeeded but cancellation hid its result; a subsequent clear
            # must not be undone by restart repair using a stale in-memory marker.
            await self.s.clear_history('private:12')
        await self.s.recover_pending_outbound()
        history = await real('history', 'private:12')
        self.assertEqual(len(history['messages']), 0 if failure == 'cancel_after' else 1)
        record = await real('get_outbound', 'publication-fault')
        self.assertFalse(record['result']['parts'][0]['publication_pending'])
        await self.s.recover_pending_outbound()
        self.assertEqual(len((await real('history', 'private:12'))['messages']), len(history['messages']))
        self.assertEqual(self.send_count, 1)
        await self.s.outbound_send(transport, payload, '127.0.0.1', owner)
        self.assertEqual(self.send_count, 1)
        await browser.send_json({'type': 'ping'})
        await self.event(browser, 'pong')

    async def test_publication_busy_retains_partial_recovery_marker(self):
        await self.publication_fault('busy')

    async def test_publication_cancel_before_commit_repairs_partial(self):
        await self.publication_fault('cancel_before')

    async def test_publication_cancel_after_commit_does_not_resurrect_clear(self):
        await self.publication_fault('cancel_after')

    async def test_napcat_replacement_releases_transition_lock(self):
        browser = await self.browser()
        old = await self.napcat()
        await self.event(browser, 'status', lambda d: d['connected'])
        new = await self.napcat()
        await self.event(browser, 'status', lambda d: d['connected'])
        self.assertTrue(old.closed or old.close_code is not None)
        await new.close()
        await self.event(browser, 'status', lambda d: not d['napcat_connected'])
        async with asyncio.timeout(2):
            await self.s.napcat_transition_lock.acquire()
        self.s.napcat_transition_lock.release()
        self.assertEqual(self.s.pending_requests, {})

    async def test_async_file_enrichment_does_not_block_inbox(self):
        await self.allow('12')
        self.block_media.clear()
        browser = await self.browser()
        qq = await self.napcat()
        await qq.send_json(self.incoming(1, kind='group', segments=[{'type': 'file', 'data': {'id': 'f1', 'name': 'a.txt', 'file_size': 7}}]))
        first = await self.event(browser, 'message')
        self.assertEqual(first['file_name'], 'a.txt')
        await qq.send_json(self.incoming(2, kind='group', content='later text'))
        await self.event(browser, 'message', lambda d: d['content'] == 'later text')
        self.block_media.set()
        updated = await self.event(browser, 'message_updated', lambda d: bool(d.get('file_url')))
        self.assertEqual(updated['file_url'], 'https://example.invalid/file')

    async def test_outbound_partial_idempotency_upload_binding_and_quote(self):
        browser = await self.browser()
        await self.napcat()
        self.s.PUBLIC_BASE_URL = self.base
        upload = await self.upload()
        self.send_behavior = 'second_failed'
        payload = {'type': 'send_message', 'target_id': 'private:12', 'request_id': 'send1',
                   'content': 'caption', 'file_local_path': upload['local_path'], 'file_orig_name': 'a.txt',
                   'upload_request_id': 'upload1', 'file_is_image': False}
        await browser.send_json(payload)
        result = await self.event(browser, 'send_result', lambda d: d['status'] == 'partial')
        self.assertEqual(len(result['parts']), 1)
        self.assertEqual(self.send_count, 2)
        await browser.send_json(payload)
        await self.event(browser, 'send_result', lambda d: d['status'] == 'partial')
        self.assertEqual(self.send_count, 2)
        mid = result['parts'][0]['message_id']
        self.send_behavior = None
        await browser.send_json({'type': 'send_message', 'target_id': 'private:12', 'request_id': 'quote1',
                                 'content': 'reply', 'quote': {'id': mid}})
        await self.event(browser, 'send_result', lambda d: d['request_id'] == 'quote1' and d['status'] == 'confirmed')
        sends = [d for d in self.actions if d['action'] == 'send_private_msg']
        self.assertEqual(sends[-1]['params']['message'][0], {'type': 'reply', 'data': {'id': '9001'}})
        self.assertNotIn('IP:', json.dumps(sends[-1]))
        for request, target in [('other-target', 'group:12'), ('reuse', 'private:12')]:
            await browser.send_json({**payload, 'request_id': request, 'target_id': target})
            await self.event(browser, 'send_result', lambda d: d['request_id'] == request and d['status'] == 'failed')
        await browser.send_json({'type': 'recall_message', 'target_id': 'private:12', 'message_id': mid})
        await self.event(browser, 'message_recalled', lambda d: d['message_id'] == mid)
        saved = await self.s.store.call('get_message', 'private:12', mid)
        self.assertTrue(saved['is_recalled'])
        self.assertIsNone(saved['file_url'])

    async def test_timeout_pending_cleanup_and_unknown_no_retry(self):
        browser = await self.browser()
        await self.napcat()
        original = self.s.send_qq_api_and_wait
        async def short(action, params, timeout=120):
            return await original(action, params, timeout=.05 if action.startswith('send_') else timeout)
        self.send_behavior = 'hold'
        with patch.object(self.s, 'send_qq_api_and_wait', short):
            payload = {'type': 'send_message', 'target_id': 'private:12', 'request_id': 'unknown1', 'content': 'hello'}
            await browser.send_json(payload)
            await self.event(browser, 'send_result', lambda d: d['status'] == 'unknown')
            await browser.send_json(payload)
            await self.event(browser, 'send_result', lambda d: d['status'] == 'unknown')
            self.assertEqual(self.send_count, 1)
            self.assertEqual(self.s.pending_requests, {})
            await browser.send_json({'type': 'ping'})
            await self.event(browser, 'pong')
        self.block_sends.set()

    async def test_api_transport_deadline_and_cancellation_finally(self):
        class BlockedSocket:
            closed = False
            async def send_json(self, payload):
                await asyncio.Event().wait()
        self.s.state.napcat_ws = BlockedSocket()
        self.assertIsNone(await self.s.send_qq_api_and_wait('synthetic', {}, timeout=.02))
        self.assertEqual(self.s.pending_requests, {})
        task = asyncio.create_task(self.s.send_qq_api_and_wait('synthetic', {}))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.s.pending_requests, {})
        self.s.state.napcat_ws = None

    async def test_main_process_random_port_sigterm(self):
        with tempfile.TemporaryDirectory() as directory:
            # Reserve an ephemeral port briefly; never contact a configured production port.
            with socket.socket() as reserved:
                reserved.bind(('127.0.0.1', 0))
                port = reserved.getsockname()[1]
            self.assertNotEqual(port, 4001)
            env = {key: value for key, value in os.environ.items()
                   if key in ('PATH', 'HOME', 'LANG', 'PYTHONPATH')}
            env.update(WEB_PASSWORD='synthetic-process-password', NAPCAT_ACCESS_TOKEN='synthetic-process-token',
                       CHAT_DATA_DIR=directory, CHAT_HOST='127.0.0.1', CHAT_PORT=str(port),
                       PUBLIC_BASE_URL='http://127.0.0.1:' + str(port))
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).resolve().parents[1] / 'server.py'), env=env,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try:
                async with aiohttp.ClientSession() as client:
                    async with asyncio.timeout(5):
                        while True:
                            try:
                                response = await client.get(env['PUBLIC_BASE_URL'] + '/readyz')
                                self.assertEqual(response.status, 200)
                                self.assertTrue((await response.json())['ready'])
                                break
                            except aiohttp.ClientConnectorError:
                                if process.returncode is not None:
                                    self.fail('isolated server exited before readiness')
                                await asyncio.sleep(.02)
                    qq = await client.ws_connect(env['PUBLIC_BASE_URL'] + '/ws',
                                                  headers={'Authorization': 'Bearer synthetic-process-token'})
                    process.send_signal(signal.SIGTERM)
                    self.assertEqual(await asyncio.wait_for(process.wait(), 5), 0)
                    await qq.close()
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()

    async def test_bridge_session_ownership_and_expiry(self):
        response = await self.http.post(self.base + '/api/bridge/connect', json={}, headers={'Origin': self.base})
        sid = (await response.json())['session']
        other = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        try:
            login = await other.post(self.base + '/api/login', json={'password': 'synthetic-password'})
            await login.read()
            denied = await other.get(self.base + '/api/bridge/poll', params={'session': sid})
            self.assertEqual(denied.status, 404)
            await denied.read()
            owner = self.s.bridge_sessions[sid]['owner']
            await self.s.sessions.revoke(owner)
            self.assertNotIn(sid, self.s.bridge_sessions)
        finally:
            await other.close()


if __name__ == '__main__':
    unittest.main()
