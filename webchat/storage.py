"""Incremental SQLite storage. One Store owner/process; AsyncStore owns one worker.

Legacy source blobs/files are retained verbatim. Migration failure rolls back every
v2 table and user_version. Runtime operations never rewrite the legacy blobs.
Callers must not mutate arguments until an async call completes. Cancellation of
an accepted call is delayed until its transaction finishes: cancellation does NOT
mean rollback. Read the durable result by request ID before retrying side effects.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import fcntl
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 3
HISTORY_LIMIT = 200
QUEUE_HARD_LIMIT = 10000
QUEUE_PAYLOAD_LIMIT = 8 * 1024 * 1024


def _redact(message):
    message.update(content="[消息已撤回]", is_recalled=True, quote=None)
    for key in ("media_url", "file_url", "file_name", "file_size", "file_id", "attachments", "segments", "raw_segments"):
        if key in message:
            message[key] = [] if key in ("attachments", "segments", "raw_segments") else None
    return message


class StorageError(RuntimeError):
    pass


class MigrationError(StorageError):
    pass


class RevisionConflict(StorageError):
    pass


class StorageBusy(StorageError):
    pass


def _json_value(value, depth=0):
    if depth > 40:
        raise ValueError("JSON nesting exceeds limit")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item, depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        for item in value.values():
            _json_value(item, depth + 1)
        return
    raise ValueError("invalid JSON value")


def _dump(value):
    _json_value(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _copy(value):
    return json.loads(_dump(value))


def _id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value) or len(str(value)) > 256:
        raise ValueError("invalid identifier")
    return str(value)


def conversation_id(kind, peer_id):
    peer = _id(peer_id)
    if kind not in ("private", "group", "legacy") or not re.fullmatch(r"[1-9][0-9]{0,19}", peer):
        raise ValueError("invalid conversation")
    return kind + ":" + peer


def parse_conversation(target_id):
    if not isinstance(target_id, str) or ":" not in target_id:
        raise ValueError("typed conversation ID required")
    kind, peer = target_id.split(":", 1)
    if conversation_id(kind, peer) != target_id:
        raise ValueError("invalid conversation")
    return kind, peer


def _settings(value):
    if not isinstance(value, dict):
        raise ValueError("settings must be an object")
    _json_value(value)
    for key in ("whitelist", "blacklist"):
        if key in value:
            if not isinstance(value[key], list):
                raise ValueError("invalid settings ID list")
            for item in value[key]:
                if not re.fullmatch(r"[1-9][0-9]{0,19}", _id(item)):
                    raise ValueError("invalid settings peer ID")
    for key in ("use_image_host", "theme_auto", "chat_background", "sound_enabled"):
        if key in value and not isinstance(value[key], bool):
            raise ValueError("invalid boolean setting")
    if "theme" in value and value["theme"] not in ("light", "dark"):
        raise ValueError("invalid theme")
    for key in ("dark_start", "dark_end"):
        if key in value and (not isinstance(value[key], str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value[key])):
            raise ValueError("invalid time setting")


def _message(value):
    if not isinstance(value, dict):
        raise ValueError("message must be an object")
    _json_value(value)
    for key in ("message_id", "target_id", "sender_id", "napcat_message_id", "file_id", "request_id"):
        if value.get(key) is not None:
            _id(value[key])
    for key in ("content", "sender_name", "media_url", "file_url", "file_name", "time"):
        if value.get(key) is not None and not isinstance(value[key], str):
            raise ValueError("invalid message text field: " + key)
    if value.get("file_size") is not None and (isinstance(value["file_size"], bool) or not isinstance(value["file_size"], (int, float)) or value["file_size"] < 0):
        raise ValueError("invalid file size")
    if "direction" in value and value["direction"] not in ("incoming", "outgoing"):
        raise ValueError("invalid direction")
    if "is_recalled" in value and not isinstance(value["is_recalled"], bool):
        raise ValueError("invalid recall flag")
    if value.get("napcat_message_ids") is not None:
        if not isinstance(value["napcat_message_ids"], list):
            raise ValueError("invalid platform IDs")
        for item in value["napcat_message_ids"]:
            _id(item)
    quote = value.get("quote")
    if quote is not None:
        if not isinstance(quote, dict) or "id" not in quote:
            raise ValueError("invalid quote")
        _id(quote["id"])
        for key in ("content", "sender_name"):
            if quote.get(key) is not None and not isinstance(quote[key], str):
                raise ValueError("invalid quote text")
    if "attachments" in value:
        if not isinstance(value["attachments"], list) or any(not isinstance(item, dict) for item in value["attachments"]):
            raise ValueError("invalid attachments")
    if "segments" in value:
        if not isinstance(value["segments"], list):
            raise ValueError("invalid segments")
        for segment in value["segments"]:
            if not isinstance(segment, dict) or not isinstance(segment.get("type"), str) or not isinstance(segment.get("data", {}), dict):
                raise ValueError("invalid segment")


def _secure_file(path, exclusive=False):
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
    if exclusive:
        flags |= os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("storage path is not a regular file")
        if info.st_nlink != 1:
            raise StorageBusy("storage path has multiple hardlinks")
        os.fchmod(fd, 0o600)
    except BaseException:
        os.close(fd)
        raise
    return fd


_SCHEMA = [
    "CREATE TABLE meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL)",
    "CREATE TABLE conversations(id TEXT PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('private','group','legacy')),peer_id TEXT NOT NULL,payload TEXT NOT NULL,revision INTEGER NOT NULL,UNIQUE(kind,peer_id))",
    "CREATE TABLE messages(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL,conversation_id TEXT NOT NULL REFERENCES conversations(id),sender_id TEXT,direction TEXT NOT NULL CHECK(direction IN ('incoming','outgoing')),payload TEXT NOT NULL,quote_id TEXT,request_id TEXT,revision INTEGER NOT NULL,UNIQUE(conversation_id,id))",
    "CREATE INDEX messages_conversation ON messages(conversation_id,seq)",
    "CREATE INDEX messages_id ON messages(id)",
    "CREATE INDEX messages_quote ON messages(conversation_id,quote_id)",
    "CREATE TABLE platform_ids(conversation_id TEXT NOT NULL,platform_id TEXT NOT NULL,message_id TEXT NOT NULL,recalled INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(conversation_id,platform_id,message_id),FOREIGN KEY(conversation_id,message_id) REFERENCES messages(conversation_id,id) ON DELETE CASCADE)",
    "CREATE INDEX platform_lookup ON platform_ids(platform_id)",
    "CREATE TABLE settings_v2(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL,revision INTEGER NOT NULL)",
    "CREATE TABLE inbox(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT UNIQUE,payload TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,error TEXT)",
    "CREATE INDEX inbox_status ON inbox(status,id)",
    "CREATE TABLE outbound(request_id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,result TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL)",
    "CREATE TABLE modes(conversation_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL)",
]


class Store:
    """Synchronous API. Construct/use/close on the same dedicated thread."""

    def __init__(self, db_path, legacy_data_path=None, legacy_settings_path=None, defaults=None, *, queue_max_bytes=64 * 1024 * 1024, min_free=512 * 1024 * 1024):
        if type(queue_max_bytes) is not int or queue_max_bytes < 1 or type(min_free) is not int or min_free < 0:
            raise ValueError("invalid storage resource budget")
        self.queue_max_bytes, self.min_free = queue_max_bytes, min_free
        self.path = os.path.abspath(os.fspath(db_path))
        self.db = None
        self._db_fd = None
        self._lock_fd = _secure_file(self.path + ".lock")
        try:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StorageBusy("another Store owns this database") from exc
            self._db_fd = _secure_file(self.path)
            try:
                # Lock the inode too: a separate path-side lock cannot detect hardlinks.
                fcntl.flock(self._db_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StorageBusy("another Store owns this database inode") from exc
            for suffix in ("-wal", "-shm", "-journal"):
                if os.path.lexists(self.path + suffix):
                    os.close(_secure_file(self.path + suffix))
            self.db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA busy_timeout=30000")
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=FULL")
            self._migrate(legacy_data_path, legacy_settings_path, defaults or {})
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self._db_fd is not None:
            os.close(self._db_fd)
            self._db_fd = None
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    @contextmanager
    def _transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _migrate(self, data_path, settings_path, defaults):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        if version == 2:
            with self._transaction():
                self.db.execute("ALTER TABLE platform_ids RENAME TO platform_ids_old")
                self.db.execute("DROP INDEX platform_lookup")
                self.db.execute(next(sql for sql in _SCHEMA if sql.startswith("CREATE TABLE platform_ids")))
                self.db.execute("INSERT INTO platform_ids SELECT * FROM platform_ids_old")
                self.db.execute("DROP TABLE platform_ids_old")
                self.db.execute("CREATE INDEX platform_lookup ON platform_ids(platform_id)")
                # v2 kept only the first index owner but preserved duplicate payloads.
                for row in self.db.execute("SELECT payload FROM messages").fetchall():
                    value = json.loads(row[0])
                    ids = list(value.get("napcat_message_ids") or [])
                    for key in ("legacy_message_id", "napcat_message_id"):
                        if value.get(key) is not None:
                            ids.append(value[key])
                    for pid in ids:
                        self.db.execute("INSERT OR IGNORE INTO platform_ids VALUES(?,?,?,?)", (value["target_id"], _id(pid), value["message_id"], 0))
                self._sanitize_import()
                self.db.execute("PRAGMA user_version=3")
            return
        if version not in (0, 1):
            raise MigrationError("unsupported schema version")
        try:
            with self._transaction():
                data, settings = {"contacts": {}, "messages": {}}, {}
                has_old = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='storage'").fetchone()
                rows = dict(self.db.execute("SELECT key,value FROM storage")) if has_old else {}
                if rows:
                    if "data" not in rows or "settings" not in rows:
                        raise ValueError("incomplete legacy storage table")
                    data, settings = json.loads(rows["data"]), json.loads(rows["settings"])
                else:
                    if data_path and Path(data_path).exists():
                        with open(data_path, encoding="utf-8") as handle:
                            data = json.load(handle)
                    if settings_path and Path(settings_path).exists():
                        with open(settings_path, encoding="utf-8") as handle:
                            settings = json.load(handle)
                contacts, buckets = self._validate_legacy(data)
                _settings(settings)
                _settings(defaults)
                for statement in _SCHEMA:
                    self.db.execute(statement)
                self.db.execute("INSERT INTO meta VALUES('revision',0)")
                self.db.execute("INSERT INTO settings_v2 VALUES(1,?,0)", (_dump({**defaults, **settings}),))
                for contact in contacts.values():
                    self._put_contact(contact, 0)
                for target, messages in buckets.items():
                    for message in messages:
                        self._insert_message(target, message, 0, None, legacy=True)
                self._sanitize_import()
                self.db.execute("PRAGMA user_version=3")
        except (ValueError, TypeError, KeyError, sqlite3.IntegrityError) as exc:
            # Never include legacy contents in diagnostics/logs.
            raise MigrationError("legacy migration failed validation; source preserved") from exc

    def _validate_legacy(self, data):
        if not isinstance(data, dict) or not isinstance(data.get("contacts", {}), dict) or not isinstance(data.get("messages", {}), dict):
            raise ValueError("invalid legacy data")
        _json_value(data)
        contacts, mapping, buckets, seen_by_target = {}, {}, {}, {}
        for old_id, raw in data.get("contacts", {}).items():
            if not isinstance(raw, dict):
                raise ValueError("invalid legacy contact")
            if ":" in old_id:
                kind, peer = parse_conversation(old_id)
            else:
                kind, peer = raw.get("type"), old_id
            target = conversation_id(kind, peer)
            if raw.get("id") is not None and str(raw["id"]) not in (old_id, target):
                raise ValueError("contact identifier mismatch")
            if raw.get("type", kind) != kind or str(raw.get("peer_id", peer)) != peer:
                raise ValueError("legacy contact type mismatch")
            contact = self._normalize_contact({**raw, "id": target, "peer_id": peer, "type": kind})
            if target in contacts:
                raise ValueError("duplicate conversation")
            contacts[target], mapping[old_id] = contact, target
            mapping[target] = target
        for old_id, messages in data.get("messages", {}).items():
            if not isinstance(messages, list):
                raise ValueError("message bucket must be a list")
            target = mapping.get(old_id)
            if target is None:
                if ":" in old_id:
                    kind, peer = parse_conversation(old_id)
                elif not messages:
                    # Empty orphan buckets carry no routing information or history.
                    continue
                else:
                    kinds = {m.get("type") for m in messages if isinstance(m, dict)}
                    kind = next(iter(kinds)) if len(kinds) == 1 and next(iter(kinds)) in ("private", "group") else "legacy"
                    peer = old_id
                target = conversation_id(kind, peer)
                if target not in contacts:
                    contacts[target] = self._normalize_contact({"id": target, "type": kind, "peer_id": peer, "name": peer})
            buckets.setdefault(target, [])
            seen = seen_by_target.setdefault(target, set())
            for raw in messages:
                _message(raw)
                if raw.get("target_id") is not None and str(raw["target_id"]) not in (old_id, target):
                    raise ValueError("message target mismatch")
                message = _copy(raw)
                message["target_id"] = target
                if message.get("message_id") is None:
                    message["message_id"] = uuid.uuid4().hex
                mid = str(message["message_id"])
                if mid in seen:
                    # Preserve every legacy row, including duplicate platform IDs.
                    message["legacy_message_id"] = message["message_id"]
                    message["message_id"] = "legacy_" + uuid.uuid4().hex
                seen.add(str(message["message_id"]))
                buckets[target].append(message)
        return contacts, buckets

    def _revision(self):
        return self.db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]

    def _bump(self):
        self.db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
        return self._revision()

    def _normalize_contact(self, contact):
        if not isinstance(contact, dict):
            raise ValueError("invalid contact")
        result = _copy(contact)
        kind, peer = parse_conversation(result.get("id"))
        if result.get("type", kind) != kind or str(result.get("peer_id", peer)) != peer:
            raise ValueError("contact type mismatch")
        for key in ("name", "avatar"):
            if result.get(key) is not None and not isinstance(result[key], str):
                raise ValueError("invalid contact text")
        result.update(id=kind + ":" + peer, type=kind, peer_id=peer)
        if kind == "legacy":
            result.update(readonly=True, read_only=True)
        return result

    def _put_contact(self, contact, revision):
        self.db.execute("INSERT INTO conversations VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,revision=excluded.revision", (contact["id"], contact["type"], contact["peer_id"], _dump(contact), revision))

    def upsert_contact(self, contact):
        contact = self._normalize_contact(contact)
        with self._transaction():
            revision = self._bump()
            self._put_contact(contact, revision)
        return {**contact, "revision": revision}

    def list_contacts(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM conversations ORDER BY id")]

    def history(self, target_id):
        parse_conversation(target_id)
        # Single owner serializes this complete snapshot with writes.
        return {"target_id": target_id, "messages": [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM messages WHERE conversation_id=? ORDER BY seq", (target_id,))], "revision": self._revision()}

    def snapshot(self):
        contacts = {c["id"]: c for c in self.list_contacts()}
        return {"contacts": contacts, "messages": {cid: self.history(cid)["messages"] for cid in contacts}, "revision": self._revision()}

    def get_message(self, target_id, message_id):
        parse_conversation(target_id)
        row = self.db.execute("SELECT payload FROM messages WHERE conversation_id=? AND id=?", (target_id, _id(message_id))).fetchone()
        return json.loads(row[0]) if row else None

    def find_platform_message(self, platform_id, target_id=None):
        params = [_id(platform_id)]
        sql = "SELECT m.payload FROM platform_ids p JOIN messages m ON m.conversation_id=p.conversation_id AND m.id=p.message_id WHERE p.platform_id=?"
        if target_id is not None:
            parse_conversation(target_id)
            sql += " AND p.conversation_id=?"
            params.append(target_id)
        rows = self.db.execute(sql, params).fetchall()
        if len(rows) > 1:
            raise ValueError("ambiguous platform message ID; conversation required")
        return json.loads(rows[0][0]) if rows else None

    def _quote(self, target, identifier, kind="internal", legacy=False):
        identifier = _id(identifier)
        if kind not in ("internal", "platform"):
            raise ValueError("invalid quote ID kind")
        internal = self.get_message(target, identifier) if kind == "internal" or legacy else None
        rows = []
        if kind == "platform" or legacy:
            rows = self.db.execute("SELECT m.payload FROM platform_ids p JOIN messages m ON m.conversation_id=p.conversation_id AND m.id=p.message_id WHERE p.conversation_id=? AND p.platform_id=? ORDER BY m.seq", (target, identifier)).fetchall()
        candidates = {value["message_id"]: value for value in ([internal] if internal else []) + [json.loads(row[0]) for row in rows]}
        recalled = any(value.get("is_recalled") for value in candidates.values())
        original = next(iter(candidates.values())) if len(candidates) == 1 else None
        return {"id": original["message_id"] if original else identifier,
                **({"id_kind": "internal" if original else kind} if original or not legacy else {}),
                "sender_name": "" if recalled else original.get("sender_name") if original else "未知",
                "content": "[消息已撤回]" if recalled else original.get("content") if original else "[找不到原消息]",
                **({"is_recalled": True} if recalled else {})}

    def _sanitize_import(self):
        # Sources stay verbatim; only the operational projection is sanitized.
        rows = self.db.execute("SELECT payload FROM messages ORDER BY seq").fetchall()
        for row in rows:
            value = json.loads(row[0])
            if value.get("is_recalled"):
                self._replace_message(_redact(value), value["revision"])
                # Preserve known per-part flags in v2 composites; old imports with
                # no such state need their platform recall state initialized.
                flags = self.platform_ids(value["target_id"], value["message_id"])
                if not any(item["recalled"] for item in flags):
                    self.db.execute("UPDATE platform_ids SET recalled=1 WHERE conversation_id=? AND message_id=?", (value["target_id"], value["message_id"]))
        # A v2 recall may have reached only its first duplicate owner. Propagate
        # durable per-platform recall flags to every preserved display copy.
        for row in self.db.execute("SELECT DISTINCT m.payload FROM messages m JOIN platform_ids p ON p.conversation_id=m.conversation_id AND p.message_id=m.id WHERE EXISTS (SELECT 1 FROM platform_ids r WHERE r.conversation_id=p.conversation_id AND r.platform_id=p.platform_id AND r.recalled=1)").fetchall():
            value = json.loads(row[0])
            self._replace_message(_redact(value), value["revision"])
        self.db.execute("UPDATE platform_ids AS p SET recalled=1 WHERE EXISTS (SELECT 1 FROM platform_ids r WHERE r.conversation_id=p.conversation_id AND r.platform_id=p.platform_id AND r.recalled=1)")
        for row in self.db.execute("SELECT payload FROM messages WHERE quote_id IS NOT NULL").fetchall():
            value = json.loads(row[0])
            quote = value["quote"]
            value["quote"] = self._quote(value["target_id"], quote["id"], quote.get("id_kind", "internal"), legacy="id_kind" not in quote)
            self._replace_message(value, value["revision"])

    def _insert_message(self, target, message, revision, platform_ids, legacy=False):
        _message(message)
        value = _copy(message)
        value["message_id"] = _id(value.get("message_id") or uuid.uuid4().hex)
        value["target_id"] = target
        value.setdefault("direction", "outgoing" if value["message_id"].startswith("local_") or value.get("napcat_message_ids") else "incoming")
        value.setdefault("sender_id", None)
        value["revision"] = revision
        ids = platform_ids
        if ids is None:
            ids = list(value.get("napcat_message_ids") or [])
            if value.get("napcat_message_id") is not None:
                ids.append(value["napcat_message_id"])
            if legacy and value["direction"] == "incoming":
                ids.append(value.get("legacy_message_id", value["message_id"]))
        if not isinstance(ids, (list, tuple)):
            raise ValueError("platform_ids must be a list")
        ids = list(dict.fromkeys(_id(item) for item in ids))
        if value.get("is_recalled"):
            _redact(value)
        quote = value.get("quote")
        if quote and not legacy:
            value["quote"] = quote = self._quote(target, quote["id"], quote.get("id_kind", "internal"), legacy="id_kind" not in quote)
        self.db.execute("INSERT INTO messages(id,conversation_id,sender_id,direction,payload,quote_id,request_id,revision) VALUES(?,?,?,?,?,?,?,?)", (value["message_id"], target, value["sender_id"], value["direction"], _dump(value), _id(quote["id"]) if quote else None, value.get("request_id"), revision))
        for pid in ids:
            if not legacy and self.db.execute("SELECT 1 FROM platform_ids WHERE conversation_id=? AND platform_id=?", (target, pid)).fetchone():
                raise sqlite3.IntegrityError("duplicate platform message ID")
            self.db.execute("INSERT INTO platform_ids VALUES(?,?,?,?)", (target, pid, value["message_id"], int(bool(value.get("is_recalled")))))
        return value

    def append_message(self, target_id, message, contact=None, quote_id=None, platform_ids=None, quote_id_kind="internal"):
        with self._transaction():
            return self._append_message(target_id, message, contact, quote_id, platform_ids, quote_id_kind)

    def _append_message(self, target_id, message, contact=None, quote_id=None, platform_ids=None, quote_id_kind="internal"):
        """Append within the caller-owned transaction."""
        if quote_id_kind not in ("internal", "platform"):
            raise ValueError("invalid quote ID kind")
        kind, peer = parse_conversation(target_id)
        if kind == "legacy":
            raise ValueError("legacy archive is read-only")
        _message(message)
        if message.get("target_id", target_id) != target_id:
            raise ValueError("message target mismatch")
        if contact is not None:
            contact = self._normalize_contact(contact)
            if contact["id"] != target_id:
                raise ValueError("contact target mismatch")
        if message.get("message_id") is not None:
            existing = self.get_message(target_id, message["message_id"])
            if existing:
                return {"message": existing, "contact": None, "revision": self._revision(), "inserted": False}
        revision = self._bump()
        if not self.db.execute("SELECT 1 FROM conversations WHERE id=?", (target_id,)).fetchone():
            contact = contact or {"id": target_id, "peer_id": peer, "type": kind, "name": peer}
            self._put_contact(contact, revision)
        elif contact:
            self._put_contact(contact, revision)
        value = _copy(message)
        if quote_id is not None:
            value["quote"] = self._quote(target_id, quote_id, quote_id_kind)
        value = self._insert_message(target_id, value, revision, platform_ids)
        self.db.execute("DELETE FROM messages WHERE conversation_id=? AND seq NOT IN (SELECT seq FROM messages WHERE conversation_id=? ORDER BY seq DESC LIMIT ?)", (target_id, target_id, HISTORY_LIMIT))
        return {"message": value, "contact": contact, "revision": revision, "inserted": True}

    def platform_ids(self, target_id, message_id):
        parse_conversation(target_id)
        return [{"platform_id": row[0], "recalled": bool(row[1])} for row in self.db.execute("SELECT platform_id,recalled FROM platform_ids WHERE conversation_id=? AND message_id=? ORDER BY platform_id", (target_id, _id(message_id)))]

    def update_message(self, target_id, message_id, patch):
        if parse_conversation(target_id)[0] == "legacy":
            raise ValueError("legacy archive is read-only")
        if not isinstance(patch, dict):
            raise ValueError("message patch must be an object")
        if set(patch) & {"message_id", "target_id", "sender_id", "direction", "request_id", "revision", "napcat_message_id", "napcat_message_ids", "is_recalled"}:
            raise ValueError("immutable message field")
        with self._transaction():
            message = self.get_message(target_id, message_id)
            if message is None:
                return {"message": None, "revision": self._revision()}
            if message.get("is_recalled"):
                return {"message": message, "revision": self._revision()}
            message.update(_copy(patch))
            _message(message)
            if message.get("quote"):
                quote = message["quote"]
                message["quote"] = self._quote(target_id, quote["id"], quote.get("id_kind", "internal"), legacy="id_kind" not in quote)
            revision = self._bump()
            self._replace_message(message, revision)
        return {"message": message, "revision": revision}

    def _replace_message(self, message, revision):
        message["revision"] = revision
        self.db.execute("UPDATE messages SET payload=?,revision=?,quote_id=? WHERE conversation_id=? AND id=?", (_dump(message), revision, _id(message["quote"]["id"]) if message.get("quote") else None, message["target_id"], message["message_id"]))

    def _referencing(self, target, mid):
        platform_aliases = {row[0] for row in self.db.execute("SELECT platform_id FROM platform_ids WHERE conversation_id=? AND message_id=?", (target, mid))}
        aliases = {mid} | platform_aliases
        result = {}
        for alias in aliases:
            for row in self.db.execute("SELECT payload FROM messages WHERE conversation_id=? AND quote_id=?", (target, alias)).fetchall():
                value = json.loads(row[0])
                # Explicit internal references must never be matched to a distinct
                # platform owner just because their numeric spellings coincide.
                kind = value["quote"].get("id_kind")
                if kind == "internal" and alias != mid:
                    continue
                if kind == "platform" and alias not in platform_aliases:
                    continue
                result[value["message_id"]] = value
        return result.values()

    def recall_message(self, message_id, target_id=None, platform_id=False):
        if target_id is not None:
            parse_conversation(target_id)
        with self._transaction():
            if platform_id:
                params = [_id(message_id)]
                sql = "SELECT m.payload FROM platform_ids p JOIN messages m ON m.conversation_id=p.conversation_id AND m.id=p.message_id WHERE p.platform_id=?"
                if target_id is not None:
                    sql += " AND p.conversation_id=?"
                    params.append(target_id)
                originals = [json.loads(row[0]) for row in self.db.execute(sql, params)]
                if len({value["target_id"] for value in originals}) > 1:
                    raise ValueError("ambiguous platform message ID; conversation required")
            elif target_id:
                original = self.get_message(target_id, message_id)
                originals = [original] if original else []
            else:
                originals = [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM messages WHERE id=?", (_id(message_id),))]
                if len(originals) > 1:
                    raise ValueError("ambiguous message ID; conversation required")
            if not originals:
                return {"messages": [], "revision": self._revision()}
            revision = self._bump()
            affected = {}
            for original in originals:
                target, mid = original["target_id"], original["message_id"]
                self._replace_message(_redact(original), revision)
                affected[mid] = original
                if platform_id:
                    self.db.execute("UPDATE platform_ids SET recalled=1 WHERE conversation_id=? AND platform_id=?", (target, _id(message_id)))
                else:
                    self.db.execute("UPDATE platform_ids SET recalled=1 WHERE conversation_id=? AND message_id=?", (target, mid))
                for quoted in self._referencing(target, mid):
                    quoted["quote"] = {"id": mid, "id_kind": "internal", "sender_name": "", "content": "[消息已撤回]", "is_recalled": True}
                    self._replace_message(quoted, revision)
                    affected[quoted["message_id"]] = quoted
        return {"messages": list(affected.values()), "revision": revision}

    def _suppress_publications(self, target_id, message_id=None):
        for row in self.db.execute("SELECT request_id,result FROM outbound WHERE conversation_id=?", (target_id,)).fetchall():
            result = json.loads(row[1])
            changed = False
            for part in result.get("parts", []):
                if not isinstance(part, dict) or part.get("publication_pending") is not True:
                    continue
                message = part.get("message") or {}
                if message_id is not None and str(message.get("message_id")) != _id(message_id):
                    continue
                part.update(publication_pending=False, publication_suppressed="clear_history" if message_id is None else "delete_message")
                changed = True
            if changed:
                self.db.execute("UPDATE outbound SET result=?,updated=? WHERE request_id=?", (_dump(result), time.time(), row[0]))

    def delete_message(self, target_id, message_id):
        parse_conversation(target_id)
        with self._transaction():
            revision = self._bump()
            self._suppress_publications(target_id, message_id)
            references = list(self._referencing(target_id, _id(message_id)))
            self.db.execute("DELETE FROM messages WHERE conversation_id=? AND id=?", (target_id, _id(message_id)))
            for value in references:
                value["quote"] = None
                self._replace_message(value, revision)
        return {"target_id": target_id, "message_id": str(message_id), "revision": revision}

    def clear_history(self, target_id):
        parse_conversation(target_id)
        with self._transaction():
            revision = self._bump()
            self._suppress_publications(target_id)
            self.db.execute("DELETE FROM messages WHERE conversation_id=?", (target_id,))
        return {"target_id": target_id, "revision": revision}

    def get_settings(self):
        row = self.db.execute("SELECT payload,revision FROM settings_v2 WHERE id=1").fetchone()
        return {"settings": json.loads(row[0]), "revision": row[1]}

    def save_settings(self, settings, expected_revision):
        _settings(settings)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("settings revision required")
        with self._transaction():
            cursor = self.db.execute("UPDATE settings_v2 SET payload=?,revision=revision+1 WHERE id=1 AND revision=?", (_dump(settings), expected_revision))
            if cursor.rowcount != 1:
                raise RevisionConflict("settings revision conflict")
        return self.get_settings()

    def get_mode(self, target_id):
        parse_conversation(target_id)
        row = self.db.execute("SELECT enabled FROM modes WHERE conversation_id=?", (target_id,)).fetchone()
        return bool(row[0]) if row else False

    def set_mode(self, target_id, enabled):
        parse_conversation(target_id)
        if not isinstance(enabled, bool):
            raise ValueError("mode must be boolean")
        with self._transaction():
            self.db.execute("INSERT INTO modes VALUES(?,?) ON CONFLICT(conversation_id) DO UPDATE SET enabled=excluded.enabled", (target_id, int(enabled)))
        return enabled

    def _admit_queue(self, table, incoming_bytes):
        # Admission only: confirmed updates/publication must retain disk headroom.
        # SQLite row deletion reuses pages but does not return filesystem space.
        if shutil.disk_usage(os.path.dirname(self.path)).free < self.min_free:
            raise StorageBusy("storage free-space reserve reached")
        if table == "inbox":
            predicate, key, clock = "status='done'", "id", "created"
            size = "length(CAST(payload AS BLOB))"
        else:
            predicate, key, clock = "status IN ('confirmed','failed')", "request_id", "updated"
            size = "length(CAST(payload AS BLOB))+length(CAST(result AS BLOB))"
        count, used = self.db.execute("SELECT count(*),coalesce(sum(" + size + "),0) FROM " + table).fetchone()
        if count < QUEUE_HARD_LIMIT and used + incoming_bytes <= self.queue_max_bytes:
            return
        for row in self.db.execute("SELECT " + key + "," + size + " FROM " + table + " WHERE " + predicate + " ORDER BY " + clock + "," + key).fetchall():
            if table == "outbound" and any(part.get("publication_pending") is True for part in self.get_outbound(row[0])["result"].get("parts", []) if isinstance(part, dict)):
                continue
            self.db.execute("DELETE FROM " + table + " WHERE " + key + "=?", (row[0],))
            count, used = count - 1, used - row[1]
            if count < QUEUE_HARD_LIMIT and used + incoming_bytes <= self.queue_max_bytes:
                return
        raise StorageBusy(table + " unresolved work quota reached")

    def enqueue_inbox(self, event, event_key=None):
        if not isinstance(event, dict):
            raise ValueError("inbox event must be an object")
        payload = _dump(event)
        if len(payload.encode("utf-8")) > QUEUE_PAYLOAD_LIMIT:
            raise ValueError("inbox event exceeds limit")
        if event_key is not None:
            event_key = _id(event_key)
        with self._transaction():
            if event_key is not None:
                existing = self.db.execute("SELECT id FROM inbox WHERE event_key=?", (event_key,)).fetchone()
                if existing:
                    return existing[0]
            self._admit_queue("inbox", len(payload.encode("utf-8")))
            cursor = self.db.execute("INSERT OR IGNORE INTO inbox(event_key,payload,status,created) VALUES(?,?,'pending',?)", (event_key, payload, time.time()))
            if cursor.rowcount:
                return cursor.lastrowid
            return self.db.execute("SELECT id FROM inbox WHERE event_key=?", (event_key,)).fetchone()[0]

    def pending_inbox(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid inbox limit")
        return [{**dict(row), "event": json.loads(row["payload"])} for row in self.db.execute("SELECT * FROM inbox WHERE status='pending' ORDER BY id LIMIT ?", (limit,))]

    def finish_inbox(self, inbox_id, error=None):
        if error is not None and not isinstance(error, str):
            raise ValueError("invalid inbox error")
        with self._transaction():
            self.db.execute("UPDATE inbox SET status=?,error=? WHERE id=?", ("failed" if error is not None else "done", error, inbox_id))

    def begin_outbound(self, request_id, target_id, payload):
        request_id = _id(request_id)
        if parse_conversation(target_id)[0] == "legacy":
            raise ValueError("legacy archive cannot send messages")
        if not isinstance(payload, dict):
            raise ValueError("outbound payload must be an object")
        encoded = _dump(payload)
        if len(encoded.encode("utf-8")) > QUEUE_PAYLOAD_LIMIT:
            raise ValueError("outbound payload exceeds limit")
        with self._transaction():
            existing = self.get_outbound(request_id)
            if existing:
                if existing["target_id"] != target_id or existing["payload"] != payload:
                    raise ValueError("request ID reused for a different operation")
                return {**existing, "is_new": False}
            self._admit_queue("outbound", len(encoded.encode("utf-8")) + 2)
            now = time.time()
            self.db.execute("INSERT INTO outbound VALUES(?,?,?,'pending','{}',?,?)", (request_id, target_id, encoded, now, now))
        return {**self.get_outbound(request_id), "is_new": True}

    def publish_outbound_part(self, request_id, index):
        """Atomically publish a confirmed durable part and consume its intent.

        Repeating an already-consumed intent never recreates deleted history.
        This is local publication only: it must never trigger a platform send.
        """
        if type(index) is not int or index < 0:
            raise ValueError("invalid outbound part index")
        with self._transaction():
            outbound = self.get_outbound(request_id)
            if outbound is None:
                raise ValueError("unknown outbound request")
            parts = outbound["result"].get("parts")
            if not isinstance(parts, list):
                raise ValueError("outbound parts required")
            matches = [part for part in parts if isinstance(part, dict) and part.get("index") == index]
            if len(matches) != 1:
                raise ValueError("unknown or duplicate outbound part index")
            part = matches[0]
            message = part.get("message")
            if not isinstance(message, dict) or message.get("message_id") is None:
                raise ValueError("outbound part message required")
            if part.get("publication_pending") is not True:
                return {"message": self.get_message(outbound["target_id"], message["message_id"]), "contact": None, "revision": self._revision(), "inserted": False}
            if part.get("status") != "confirmed":
                raise ValueError("only confirmed outbound parts can publish")
            if message.get("request_id", request_id) != request_id:
                raise ValueError("outbound part request mismatch")
            result = self._append_message(outbound["target_id"], message,
                                          contact=part.get("contact"), quote_id=part.get("quote_id"),
                                          platform_ids=part.get("platform_ids"),
                                          quote_id_kind=part.get("quote_id_kind", "internal"))
            part["publication_pending"] = False
            self.db.execute("UPDATE outbound SET result=?,updated=? WHERE request_id=?", (_dump(outbound["result"]), time.time(), request_id))
        return result

    @staticmethod
    def _outbound_row(row):
        if row is None:
            return None
        return {"request_id": row["request_id"], "target_id": row["conversation_id"], "payload": json.loads(row["payload"]), "status": row["status"], "result": json.loads(row["result"]), "created": row["created"], "updated": row["updated"]}

    def get_outbound(self, request_id):
        return self._outbound_row(self.db.execute("SELECT * FROM outbound WHERE request_id=?", (_id(request_id),)).fetchone())

    def update_outbound(self, request_id, status, **fields):
        if status not in ("pending", "confirmed", "failed", "unknown", "partial"):
            raise ValueError("invalid outbound status")
        with self._transaction():
            previous = self.get_outbound(request_id)
            if previous is None:
                raise ValueError("unknown outbound request")
            result = {**previous["result"], **fields}
            encoded = _dump(result)
            if len(encoded.encode("utf-8")) > QUEUE_PAYLOAD_LIMIT:
                raise ValueError("outbound result exceeds limit")
            self.db.execute("UPDATE outbound SET status=?,result=?,updated=? WHERE request_id=?", (status, encoded, time.time(), request_id))
        return self.get_outbound(request_id)

    def pending_outbound(self, limit=100, status=None, after=None):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid outbound limit")
        if status not in (None, "pending", "unknown", "partial"):
            raise ValueError("invalid pending outbound status")
        params = []
        sql = "SELECT * FROM outbound WHERE status IN ('pending','unknown','partial')"
        if status is not None:
            sql += " AND status=?"
            params.append(status)
        if after is not None:
            sql += " AND request_id>?"
            params.append(_id(after))
        sql += " ORDER BY request_id LIMIT ?"
        return [self._outbound_row(row) for row in self.db.execute(sql, [*params, limit])]

    def recover_outbound(self):
        """After restart, never automatically re-send an uncertain accepted send."""
        with self._transaction():
            return self.db.execute("UPDATE outbound SET status='unknown',updated=? WHERE status='pending'", (time.time(),)).rowcount

    def queue_stats(self):
        return {table: {row[0]: row[1] for row in self.db.execute("SELECT status,count(*) FROM " + table + " GROUP BY status")} for table in ("inbox", "outbound")}

    def maintenance(self, retention_days=30, max_completed=10000):
        if isinstance(retention_days, bool) or not isinstance(retention_days, (int, float)) or not math.isfinite(retention_days) or retention_days < 0:
            raise ValueError("invalid retention days")
        if type(max_completed) is not int or max_completed < 0:
            raise ValueError("invalid completed retention limit")
        cutoff = time.time() - retention_days * 86400
        max_completed = min(max_completed, max(0, QUEUE_HARD_LIMIT - 1))
        removed = {}
        with self._transaction():
            for table, predicate, key, clock in (("inbox", "status='done'", "id", "created"), ("outbound", "status IN ('confirmed','failed')", "request_id", "updated")):
                sql = "DELETE FROM " + table + " WHERE " + predicate + " AND (" + clock + "<? OR " + key + " NOT IN (SELECT " + key + " FROM " + table + " WHERE " + predicate + " ORDER BY " + clock + " DESC," + key + " DESC LIMIT ?))"
                removed[table] = self.db.execute(sql, (cutoff, max_completed)).rowcount
        return {"removed": removed, "stats": self.queue_stats()}

    def integrity_check(self):
        return {"integrity": [row[0] for row in self.db.execute("PRAGMA integrity_check")], "foreign_keys": [tuple(row) for row in self.db.execute("PRAGMA foreign_key_check")], "schema_version": self.db.execute("PRAGMA user_version").fetchone()[0]}

    def backup(self, destination):
        destination = os.path.abspath(os.fspath(destination))
        if destination == self.path:
            raise ValueError("backup cannot replace the active database")
        os.close(_secure_file(destination, exclusive=True))
        backup = sqlite3.connect(destination)
        try:
            self.db.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise StorageError("backup integrity check failed")
        finally:
            backup.close()
        return destination


class AsyncStore:
    """Bounded accepted operations; no default executor or event-loop JSON copy.

    Accepted calls finish before cancellation propagates. A full queue fails fast
    with StorageBusy (including the running operation in queue_limit). Callers
    should apply transport backpressure, not create unlimited waiting tasks.
    """

    def __init__(self, queue_limit=64):
        if type(queue_limit) is not int or queue_limit < 1:
            raise ValueError("invalid storage queue limit")
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="chat-storage")
        self._store = None
        self._limit = queue_limit
        self._pending = 0
        self._closed = False
        self._close_future = None

    @classmethod
    async def open(cls, *args, queue_limit=64, **kwargs):
        self = cls(queue_limit)
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._executor, lambda: Store(*args, **kwargs))
        try:
            self._store = await self._settle(future)
        except BaseException:
            if future.done() and not future.cancelled() and future.exception() is None:
                self._store = future.result()
                await self.close()
            else:
                self._executor.shutdown(wait=False)
            raise
        return self

    @staticmethod
    async def _settle(future):
        cancelled = False
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                cancelled = True
        result = future.result()
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def call(self, method, *args, **kwargs):
        if self._closed or self._store is None:
            raise StorageError("store is closed")
        if method.startswith("_") or method == "close" or not callable(getattr(self._store, method, None)):
            raise ValueError("invalid storage operation")
        if self._pending >= self._limit:
            raise StorageBusy("storage queue is full")
        self._pending += 1
        try:
            future = asyncio.get_running_loop().run_in_executor(self._executor, lambda: getattr(self._store, method)(*args, **kwargs))
            return await self._settle(future)
        finally:
            self._pending -= 1

    async def close(self):
        if self._close_future is None:
            self._closed = True
            if self._store is not None:
                self._close_future = asyncio.get_running_loop().run_in_executor(self._executor, self._store.close)
            else:
                self._close_future = asyncio.get_running_loop().create_future()
                self._close_future.set_result(None)
            # No accepted operations can follow close; queued work still drains.
            self._executor.shutdown(wait=False)
        await self._settle(self._close_future)
