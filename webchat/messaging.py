"""Validated wire protocol helpers. No network or persistence side effects."""
import hashlib
import json
import os
import re

MAX_TEXT = 64 * 1024
MAX_SEGMENTS = 256


def conversation_key(kind, peer_id):
    if kind not in ('private', 'group') or isinstance(peer_id, bool):
        raise ValueError('invalid conversation')
    peer = str(peer_id)
    if not re.fullmatch(r'[1-9][0-9]{0,19}', peer):
        raise ValueError('invalid peer id')
    return kind + ':' + peer


def parse_target(target, kind=None):
    if not isinstance(target, str) or ':' not in target:
        raise ValueError('typed target_id required')
    actual, peer = target.split(':', 1)
    if conversation_key(actual, peer) != target or (kind is not None and kind != actual):
        raise ValueError('conversation type mismatch')
    return actual, peer


def public_url(value):
    if not isinstance(value, str) or len(value) > 8192:
        return None
    return value if value.startswith(('https://', 'http://', '/files/')) else None


def normalize_segments(raw):
    if isinstance(raw, str):
        raw = [{'type': 'text', 'data': {'text': raw}}]
    if not isinstance(raw, list) or len(raw) > MAX_SEGMENTS:
        raise ValueError('invalid message segments')
    segments, text, quote = [], [], None
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get('data', {}), dict):
            segments.append({'type': 'unsupported', 'text': '[无效消息段]'})
            text.append('[无效消息段]')
            continue
        kind, data = item.get('type'), item.get('data', {})
        if kind == 'text':
            value = data.get('text', '')
            if not isinstance(value, str):
                raise ValueError('invalid message text')
            segments.append({'type': 'text', 'text': value})
            text.append(value)
        elif kind == 'reply':
            value = data.get('id')
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                quote = str(value)[:128]
                segments.append({'type': 'reply', 'platform_id': quote})
        elif kind in ('image', 'mface'):
            url = public_url(data.get('url') or data.get('file'))
            segments.append({'type': 'image', 'url': url, 'text': '[图片]' if url else '[图片不可用]'})
            text.append(segments[-1]['text'])
        elif kind == 'file':
            name = os.path.basename(str(data.get('name') or '文件').replace('\\', '/'))[:255]
            try:
                size = max(0, int(data.get('file_size') or 0))
            except (ValueError, TypeError):
                size = 0
            segments.append({'type': 'file', 'name': name, 'size': size,
                             'url': public_url(data.get('url')), 'file_id': str(data.get('file_id') or data.get('id') or '')[:256],
                             'busid': data.get('busid') if isinstance(data.get('busid'), int) else None})
            text.append('[文件] ' + name)
        else:
            label = {'face': '表情', 'record': '语音', 'video': '视频', 'at': '提及', 'forward': '合并转发'}.get(kind, '不支持的消息段') if isinstance(kind, str) else '不支持的消息段'
            segments.append({'type': 'unsupported', 'original_type': kind if isinstance(kind, str) else 'invalid', 'label': '[' + label + ']', 'text': '[' + label + ']'})
            text.append('[' + label + ']')
    content = ''.join(text).strip()
    if len(content.encode('utf-8')) > MAX_TEXT:
        raise ValueError('message text too large')
    return segments, content, quote


def inbox_key(event):
    # Stable QQ identities deduplicate redelivery; other notices use canonical bytes.
    kind = event.get('message_type')
    if event.get('post_type') == 'message' and kind in ('private', 'group'):
        sender = event.get('sender') if isinstance(event.get('sender'), dict) else {}
        peer = event.get('group_id') if kind == 'group' else (event.get('user_id') or sender.get('user_id'))
        target = conversation_key(kind, peer)
        platform = event.get('message_id')
        if isinstance(platform, (str, int)) and not isinstance(platform, bool):
            return 'message:' + target + ':' + str(platform)
    encoded = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return 'event:' + hashlib.sha256(encoded).hexdigest()


def validate_client_message(data):
    if isinstance(data, dict) and data.get('type') == 'ping':
        return data
    if not isinstance(data, dict) or data.get('type') not in ('get_history', 'send_message', 'recall_message', 'get_send_result'):
        raise ValueError('invalid request type')
    if data['type'] == 'get_send_result':
        validate_request_id(data.get('request_id'))
        if data.get('target_id') is not None:
            parse_target(data['target_id'])
        return data
    if data['type'] == 'get_history' and isinstance(data.get('target_id'), str) and re.fullmatch(r'legacy:[1-9][0-9]{0,19}', data['target_id']):
        return data
    parse_target(data.get('target_id'), data.get('msg_type'))
    if data['type'] == 'send_message':
        validate_request_id(data.get('request_id'))
        content = data.get('content', '')
        if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_TEXT:
            raise ValueError('invalid content')
        if data.get('quote') is not None and not isinstance(data['quote'], dict):
            raise ValueError('invalid quote')
        if not isinstance(data.get('file_is_image', False), bool):
            raise ValueError('invalid image flag')
        if data.get('file_local_path') is not None and not isinstance(data['file_local_path'], str):
            raise ValueError('invalid file path')
        if data.get('file_orig_name') is not None and (not isinstance(data['file_orig_name'], str) or len(data['file_orig_name']) > 512):
            raise ValueError('invalid filename')
        if data.get('file_local_path'):
            validate_request_id(data.get('file_upload_id') or data.get('upload_request_id'))
        if data.get('quote') is not None:
            quote = data['quote']
            if not isinstance(quote.get('id'), str) or not 1 <= len(quote['id']) <= 256:
                raise ValueError('invalid quote id')
        if not content.strip() and not data.get('file_local_path'):
            raise ValueError('empty message')
    elif data['type'] == 'recall_message':
        if not isinstance(data.get('message_id'), str) or len(data['message_id']) > 128:
            raise ValueError('invalid message id')
    return data


def validate_request_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value):
        raise ValueError('request_id required')
    return value


def all_command(content, kind):
    """Parse a per-chat command after the caller's blacklist/whitelist checks."""
    text = content.strip()
    if not text.startswith('='):
        return None
    words = text[1:].upper().split()
    if words[:1] != ['ALL']:
        return None
    if kind != 'private':
        return 'denied'
    if len(words) == 2 and words[1] in ('ON', 'OFF'):
        return words[1] == 'ON'
    return 'help'
