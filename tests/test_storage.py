"""Only temporary synthetic data; no server imports or network connections."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

from webchat.storage import AsyncStore, MigrationError, RevisionConflict, StorageBusy, Store


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "test.sqlite3"
        self.store = None

    def tearDown(self):
        if self.store:
            self.store.close()
        self.tmp.cleanup()

    def open(self, **kwargs):
        self.store = Store(self.path, **kwargs)
        return self.store

    def legacy(self, data, settings=None):
        settings = settings if settings is not None else {"whitelist": ["10001"]}
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE storage(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            db.executemany("INSERT INTO storage VALUES(?,?)", [("data", json.dumps(data)), ("settings", json.dumps(settings)), ("initialized", "true")])
        return json.dumps(data)

    def test_table_migration_preserves_blob_and_imported_history(self):
        data = {"contacts": {"10001": {"id": "10001", "name": "合成", "type": "private"}}, "messages": {"10001": [{"message_id": i + 1, "target_id": "10001", "content": "synthetic", "sender_name": "x"} for i in range(205)]}}
        source = self.legacy(data)
        store = self.open()
        self.assertEqual(len(store.history("private:10001")["messages"]), 205)
        self.assertEqual(store.find_platform_message(1)["message_id"], "1")
        self.assertEqual(store.db.execute("SELECT value FROM storage WHERE key='data'").fetchone()[0], source)
        self.assertEqual(store.list_contacts()[0]["peer_id"], "10001")
        self.assertEqual(store.history("private:10001")["messages"][0]["direction"], "incoming")
        store.append_message("private:10001", {"message_id": "new", "content": "new", "sender_id": "10001", "direction": "incoming"}, platform_ids=[999])
        self.assertEqual(len(store.history("private:10001")["messages"]), 200)
        self.assertIsNone(store.find_platform_message(1))
        self.assertEqual(store.db.execute("SELECT value FROM storage WHERE key='data'").fetchone()[0], source)
        self.assertEqual(store.integrity_check(), {"integrity": ["ok"], "foreign_keys": [], "schema_version": 3})

    def test_json_import_atomic_and_sources_untouched(self):
        data = self.root / "legacy.json"
        settings = self.root / "settings.json"
        raw = '{"contacts":{},"messages":{}}'
        data.write_text(raw)
        settings.write_text('{"theme":"dark"}')
        store = self.open(legacy_data_path=data, legacy_settings_path=settings, defaults={"sound_enabled": True})
        self.assertEqual(store.get_settings()["settings"], {"theme": "dark", "sound_enabled": True})
        self.assertEqual(data.read_text(), raw)
        store.close()
        settings.write_text('{broken')
        self.store = Store(self.path, legacy_data_path=data, legacy_settings_path=settings)
        self.assertEqual(self.store.get_settings()["settings"]["theme"], "dark")

    def test_nested_validation_rolls_back_schema(self):
        cases = [
            ({"contacts": {}, "messages": {"10001": {}}}, {}),
            ({"contacts": {"10001": []}, "messages": {}}, {}),
            ({"contacts": {"10001": {"type": "private"}}, "messages": {"10001": [3]}}, {}),
            ({"contacts": {"10001": {"type": "private"}}, "messages": {"10001": [{"quote": []}]}}, {}),
            ({"contacts": {"10001": {"type": "private"}}, "messages": {"10001": [{"segments": [{"type": "image", "data": []}]}]}}, {}),
            ({"contacts": {}, "messages": {}}, {"whitelist": "10001"}),
            ({"contacts": {}, "messages": {}}, {"sound_enabled": "false"}),
            ({"contacts": {}, "messages": {}}, {"dark_start": "24:00"}),
        ]
        for index, (data, settings) in enumerate(cases):
            with self.subTest(case=index):
                self.path = self.root / (str(index) + ".sqlite3")
                raw = self.legacy(data, settings)
                with self.assertRaises(MigrationError):
                    Store(self.path)
                with sqlite3.connect(self.path) as db:
                    self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
                    self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='messages'").fetchone())
                    self.assertEqual(db.execute("SELECT value FROM storage WHERE key='data'").fetchone()[0], raw)
                    db.execute("UPDATE storage SET value=? WHERE key='data'", (json.dumps({"contacts": {}, "messages": {}}),))
                    db.execute("UPDATE storage SET value='{}' WHERE key='settings'")
                fixed = Store(self.path)
                fixed.close()

    def test_typed_conversations_platform_collision_and_idempotency(self):
        store = self.open()
        one = store.append_message("private:10001", {"message_id": "same", "content": "private"}, platform_ids=[700])
        two = store.append_message("group:10001", {"message_id": "same", "content": "group", "sender_id": "20002", "direction": "incoming"}, platform_ids=[700])
        self.assertGreater(two["revision"], one["revision"])
        self.assertEqual(store.get_message("private:10001", "same")["content"], "private")
        self.assertEqual(store.find_platform_message(700, "group:10001")["content"], "group")
        with self.assertRaises(ValueError):
            store.find_platform_message(700)
        with self.assertRaises(ValueError):
            store.history("10001")
        again = store.append_message("private:10001", {"message_id": "same", "content": "ignored"})
        self.assertFalse(again["inserted"])
        self.assertEqual(again["revision"], two["revision"])
        with self.assertRaises(sqlite3.IntegrityError):
            store.append_message("private:10001", {"message_id": "other"}, platform_ids=[700])
        self.assertIsNone(store.get_message("private:10001", "other"))
        self.assertEqual(store.history("private:10001")["revision"], two["revision"])

    def test_recall_clears_attachments_quotes_and_resists_late_patch(self):
        store = self.open()
        target = "private:10001"
        store.append_message(target, {"message_id": "local_1", "content": "secret synthetic", "file_url": "/f", "file_name": "f", "file_id": "f1", "file_size": 5, "media_url": "/m", "segments": [{"type": "image", "data": {"url": "/m"}}], "attachments": [{"url": "/f"}], "direction": "outgoing", "sender_id": "20002"}, platform_ids=[700, 701])
        store.append_message(target, {"message_id": "quote"}, quote_id=701, quote_id_kind="platform")
        self.assertEqual(store.get_message(target, "quote")["quote"]["id"], "local_1")
        result = store.recall_message(700, target_id=target, platform_id=True)
        self.assertEqual(len(result["messages"]), 2)
        recalled = store.get_message(target, "local_1")
        for field in ("file_url", "file_name", "file_id", "file_size", "media_url"):
            self.assertIsNone(recalled[field])
        self.assertEqual(recalled["attachments"], [])
        self.assertEqual(recalled["segments"], [])
        self.assertEqual(store.get_message(target, "quote")["quote"]["content"], "[消息已撤回]")
        self.assertEqual(store.platform_ids(target, "local_1"), [{"platform_id": "700", "recalled": True}, {"platform_id": "701", "recalled": False}])
        patched = store.update_message(target, "local_1", {"media_url": "/late", "content": "late"})
        self.assertIsNone(patched["message"]["media_url"])
        self.assertEqual(patched["revision"], result["revision"])

    def test_update_delete_clear_and_settings_cas(self):
        store = self.open()
        target = "private:10001"
        first = store.append_message(target, {"message_id": "a"})
        updated = store.update_message(target, "a", {"content": "new"})
        self.assertGreater(updated["revision"], first["revision"])
        store.append_message(target, {"message_id": "b"}, quote_id="a")
        store.delete_message(target, "a")
        self.assertIsNone(store.get_message(target, "b")["quote"])
        store.clear_history(target)
        self.assertEqual(store.history(target)["messages"], [])
        cfg = store.get_settings()
        store.save_settings({"theme": "dark"}, cfg["revision"])
        with self.assertRaises(RevisionConflict):
            store.save_settings({"theme": "light"}, cfg["revision"])
        self.assertEqual(store.get_settings(), {"settings": {"theme": "dark"}, "revision": 1})
        store.set_mode(target, True)
        self.assertTrue(store.get_mode(target))
        self.assertFalse(store.get_mode("group:10001"))

    def test_durable_inbox_outbound_restart_and_submessages(self):
        store = self.open()
        inbox = store.enqueue_inbox({"message_id": 1}, "incoming-1")
        self.assertEqual(store.enqueue_inbox({"message_id": 1}, "incoming-1"), inbox)
        req = store.begin_outbound("r1", "private:10001", {"content": "hello"})
        self.assertTrue(req["is_new"])
        self.assertFalse(store.begin_outbound("r1", "private:10001", {"content": "hello"})["is_new"])
        with self.assertRaises(ValueError):
            store.begin_outbound("r1", "group:10001", {"content": "hello"})
        store.append_message("private:10001", {"message_id": "r1-0", "request_id": "r1", "direction": "outgoing"}, platform_ids=[700])
        store.update_outbound("r1", "partial", parts=[{"index": 0, "status": "confirmed", "message_id": "r1-0", "platform_ids": [700]}, {"index": 1, "status": "unknown"}])
        store.close()
        self.store = store = Store(self.path)
        self.assertEqual(store.pending_inbox()[0]["event"], {"message_id": 1})
        self.assertEqual(store.pending_outbound()[0]["result"]["parts"][1]["status"], "unknown")
        self.assertEqual(store.find_platform_message(700)["request_id"], "r1")
        store.finish_inbox(inbox)
        self.assertEqual(store.pending_inbox(), [])

    def test_backup_permissions_and_single_owner(self):
        store = self.open()
        with self.assertRaises(StorageBusy):
            Store(self.path)
        store.append_message("private:10001", {"content": "synthetic"})
        backup = self.root / "backup.sqlite3"
        store.backup(backup)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(backup).st_mode), 0o600)
        with self.assertRaises(FileExistsError):
            store.backup(backup)
        copied = Store(backup)
        try:
            self.assertEqual(copied.snapshot(), store.snapshot())
        finally:
            copied.close()

    def test_legacy_duplicate_rows_not_discarded(self):
        self.legacy({"contacts": {"10001": {"type": "private"}}, "messages": {"10001": [{"message_id": 1}, {"message_id": 1}]}})
        store = self.open()
        rows = store.history("private:10001")["messages"]
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["message_id"], rows[1]["message_id"])

    def test_orphan_history_is_readonly_archive_not_guessed(self):
        self.legacy({"contacts": {}, "messages": {"10001": [{"message_id": 3, "content": "ambiguous"}]}})
        store = self.open()
        contact = store.list_contacts()[0]
        self.assertEqual(contact["id"], "legacy:10001")
        self.assertTrue(contact["readonly"])
        self.assertEqual(store.history("legacy:10001")["messages"][0]["content"], "ambiguous")
        with self.assertRaises(ValueError):
            store.append_message("legacy:10001", {"content": "cannot send"})
        with self.assertRaises(ValueError):
            store.begin_outbound("r", "legacy:10001", {})

    def test_alias_buckets_keep_metadata_history_and_recall_all_duplicates(self):
        data = {"contacts": {"123": {"type": "private", "name": "synthetic-name", "avatar": "/synthetic-avatar"}}, "messages": {
            "123": [{"message_id": 7, "content": "first"}],
            "private:123": [{"message_id": 7, "content": "second"}, {"message_id": 8, "quote": {"id": 7, "content": "old"}}]}}
        source = self.legacy(data)
        store = self.open()
        self.assertEqual(store.list_contacts()[0]["name"], "synthetic-name")
        self.assertEqual(store.list_contacts()[0]["avatar"], "/synthetic-avatar")
        self.assertEqual(len(store.history("private:123")["messages"]), 3)
        with self.assertRaises(ValueError):
            store.find_platform_message(7, "private:123")
        result = store.recall_message(7, "private:123", platform_id=True)
        rows = store.history("private:123")["messages"]
        self.assertEqual(len(result["messages"]), 3)
        self.assertTrue(all(row.get("is_recalled") for row in rows[:2]))
        self.assertEqual(rows[2]["quote"]["content"], "[消息已撤回]")
        self.assertEqual(store.db.execute("SELECT value FROM storage WHERE key='data'").fetchone()[0], source)

    def test_recalled_import_redacts_payload_quotes_and_flags(self):
        self.legacy({"contacts": {"123": {"type": "private"}}, "messages": {"123": [
            {"message_id": 7, "is_recalled": True, "content": "old", "file_url": "/synthetic", "segments": [{"type": "image", "data": {"url": "/synthetic"}}]},
            {"message_id": 8, "quote": {"id": 7, "content": "old"}}]}})
        store = self.open()
        rows = store.history("private:123")["messages"]
        self.assertIsNone(rows[0]["file_url"])
        self.assertEqual(rows[0]["segments"], [])
        self.assertEqual(rows[1]["quote"]["content"], "[消息已撤回]")
        self.assertTrue(store.platform_ids("private:123", 7)[0]["recalled"])

    def test_quote_namespaces_late_patch_and_delete_alias(self):
        store, target = self.open(), "private:123"
        store.append_message(target, {"message_id": "7", "content": "internal"}, platform_ids=[10])
        store.append_message(target, {"message_id": "right", "content": "platform"}, platform_ids=[7])
        internal = store.append_message(target, {"message_id": "internal-reply"}, quote_id=7)["message"]
        platform = store.append_message(target, {"message_id": "platform-reply"}, quote_id=7, quote_id_kind="platform")["message"]
        self.assertEqual(internal["quote"]["id"], "7")
        self.assertEqual(platform["quote"]["id"], "right")
        unresolved = store.append_message(target, {"message_id": "unresolved"}, quote_id="future", quote_id_kind="platform")["message"]
        store.append_message(target, {"message_id": "future"}, platform_ids=[101])
        store.recall_message("future", target)
        self.assertEqual(store.get_message(target, "unresolved")["quote"], unresolved["quote"])
        store.recall_message(7, target, platform_id=True)
        self.assertEqual(store.get_message(target, "internal-reply")["quote"]["content"], "internal")
        patched = store.update_message(target, "platform-reply", {"quote": {"id": "right", "content": "resurrect"}})
        self.assertEqual(patched["message"]["quote"]["content"], "[消息已撤回]")
        store.append_message(target, {"message_id": "new", "content": "new"}, platform_ids=[99])
        store.append_message(target, {"message_id": "alias-reply", "quote": {"id": 99, "content": "untrusted"}})
        # Simulate an existing pre-v3 uncanonicalized platform alias quote.
        value = store.get_message(target, "alias-reply")
        value["quote"] = {"id": 99, "content": "old"}
        store._replace_message(value, value["revision"])
        store.delete_message(target, "new")
        self.assertIsNone(store.get_message(target, "alias-reply")["quote"])
        with self.assertRaises(ValueError):
            store.append_message(target, {"message_id": "bad"}, quote_id_kind="guess")

    def test_archive_payload_is_readonly_but_deletion_is_available(self):
        self.legacy({"contacts": {}, "messages": {"123": [{"message_id": 7, "content": "archive"}]}})
        store = self.open()
        with self.assertRaises(ValueError):
            store.update_message("legacy:123", 7, {"content": "changed"})
        self.assertEqual(store.get_message("legacy:123", 7)["content"], "archive")
        store.delete_message("legacy:123", 7)
        self.assertEqual(store.history("legacy:123")["messages"], [])

    def test_hardlinks_rejected_without_permission_changes(self):
        store = self.open()
        alias = self.root / "alias.sqlite3"
        os.link(self.path, alias)
        os.chmod(self.path, 0o640)
        with self.assertRaises(StorageBusy):
            Store(alias)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o640)
        lock_alias = self.root / "lock-alias.sqlite3.lock"
        os.link(str(self.path) + ".lock", lock_alias)
        os.chmod(lock_alias, 0o640)
        with self.assertRaises(StorageBusy):
            Store(self.root / "lock-alias.sqlite3")
        self.assertEqual(stat.S_IMODE(os.stat(lock_alias).st_mode), 0o640)

    def test_inode_lock_blocks_alias_even_if_link_count_check_is_bypassed(self):
        self.open()
        alias = self.root / "alias.sqlite3"
        os.link(self.path, alias)
        import webchat.storage as storage
        original = storage._secure_file
        def allow_hardlink(path, exclusive=False):
            if os.fspath(path) == str(alias):
                return os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            return original(path, exclusive)
        with patch.object(storage, "_secure_file", allow_hardlink):
            with self.assertRaises(StorageBusy):
                Store(alias)

    def test_v2_upgrade_recovers_duplicate_mappings_and_sanitizes(self):
        self.legacy({"contacts": {"123": {"type": "private"}}, "messages": {"123": [
            {"message_id": 7, "content": "one"}, {"message_id": 7, "content": "two"}]}})
        store = self.open()
        rows = store.history("private:123")["messages"]
        second = rows[1]["message_id"]
        store.db.execute("DELETE FROM platform_ids WHERE message_id=?", (second,))
        store.db.execute("ALTER TABLE platform_ids RENAME TO platform_v3")
        store.db.execute("DROP INDEX platform_lookup")
        store.db.execute("CREATE TABLE platform_ids(conversation_id TEXT NOT NULL,platform_id TEXT NOT NULL,message_id TEXT NOT NULL,recalled INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(conversation_id,platform_id),FOREIGN KEY(conversation_id,message_id) REFERENCES messages(conversation_id,id) ON DELETE CASCADE)")
        store.db.execute("INSERT INTO platform_ids SELECT * FROM platform_v3")
        store.db.execute("DROP TABLE platform_v3")
        store.db.execute("CREATE INDEX platform_lookup ON platform_ids(platform_id)")
        store.db.execute("UPDATE platform_ids SET recalled=1 WHERE platform_id='7'")
        store.db.execute("PRAGMA user_version=2")
        store.close()
        self.store = store = Store(self.path)
        self.assertEqual(len(store.platform_ids("private:123", second)), 1)
        self.assertTrue(all(row["is_recalled"] for row in store.history("private:123")["messages"]))
        store.recall_message(7, "private:123", platform_id=True)
        self.assertTrue(all(row["is_recalled"] for row in store.history("private:123")["messages"]))
        self.assertEqual(store.integrity_check()["schema_version"], 3)

    def test_v2_upgrade_rolls_back_atomically(self):
        store = self.open()
        store.db.execute("PRAGMA user_version=2")
        store.close()
        with patch.object(Store, "_sanitize_import", side_effect=ValueError("synthetic failure")):
            with self.assertRaises(ValueError):
                Store(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='platform_ids_old'").fetchone())
        self.store = Store(self.path)
        self.assertEqual(self.store.integrity_check()["foreign_keys"], [])

    def test_queue_utf8_byte_budget_and_disk_reserve_admission_only(self):
        store = self.open(queue_max_bytes=25, min_free=100)
        with patch("webchat.storage.shutil.disk_usage") as disk:
            disk.return_value.free = 1000
            inbox = store.enqueue_inbox({"text": "合成"}, "first")
            with self.assertRaises(StorageBusy):
                store.enqueue_inbox({"text": "合成"}, "second")
            store.finish_inbox(inbox)
            store.enqueue_inbox({"text": "合成"}, "second")
            self.assertIsNone(store.db.execute("SELECT id FROM inbox WHERE event_key='first'").fetchone())
            store.begin_outbound("one", "private:123", {"text": "合成"})
            with self.assertRaises(StorageBusy):
                store.begin_outbound("two", "private:123", {"text": "合成"})
            store.update_outbound("one", "confirmed")
            store.begin_outbound("two", "private:123", {"text": "合成"})
            self.assertIsNone(store.get_outbound("one"))
            disk.return_value.free = 99
            with self.assertRaises(StorageBusy):
                store.enqueue_inbox({}, "reserve")
            with self.assertRaises(StorageBusy):
                store.begin_outbound("reserve", "private:123", {})
            self.assertFalse(store.begin_outbound("two", "private:123", {"text": "合成"})["is_new"])
            # Already accepted work can still record QQ success despite reserve.
            part = {"index": 0, "status": "confirmed", "message": {"message_id": "two-message"}, "publication_pending": True, "platform_ids": [7]}
            store.update_outbound("two", "partial", parts=[part])
            self.assertTrue(store.publish_outbound_part("two", 0)["inserted"])
            self.assertTrue(store.append_message("private:123", {"message_id": "direct"})["inserted"])

    def test_clear_or_delete_before_publication_consumes_intent_atomically(self):
        store, target = self.open(), "private:123"
        for rid in ("clear", "delete"):
            store.begin_outbound(rid, target, {})
            part = {"index": 0, "status": "confirmed", "message": {"message_id": rid}, "publication_pending": True, "platform_ids": [rid]}
            store.update_outbound(rid, "partial", parts=[part])
        store.delete_message(target, "delete")
        self.assertEqual(store.get_outbound("delete")["result"]["parts"][0]["publication_suppressed"], "delete_message")
        self.assertTrue(store.get_outbound("clear")["result"]["parts"][0]["publication_pending"])
        store.clear_history(target)
        self.assertEqual(store.get_outbound("clear")["result"]["parts"][0]["publication_suppressed"], "clear_history")
        store.close()
        self.store = store = Store(self.path)
        for rid in ("clear", "delete"):
            self.assertFalse(store.get_outbound(rid)["result"]["parts"][0]["publication_pending"])
            self.assertIsNone(store.publish_outbound_part(rid, 0)["message"])
        self.assertEqual(store.history(target)["messages"], [])

    def test_outbound_publication_is_atomic_and_consumed_after_clear(self):
        store, target = self.open(), "private:123"
        store.begin_outbound("request", target, {"content": "synthetic"})
        part = {"index": 0, "status": "confirmed", "message": {"message_id": "published", "request_id": "request", "content": "synthetic", "direction": "outgoing"}, "quote_id": None, "platform_ids": [7], "publication_pending": True}
        store.update_outbound("request", "partial", parts=[part])
        original = store._insert_message
        def fail_after_insert(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("synthetic post-insert failure")
        revision = store.history(target)["revision"]
        with patch.object(store, "_insert_message", side_effect=fail_after_insert):
            with self.assertRaises(RuntimeError):
                store.publish_outbound_part("request", 0)
        self.assertIsNone(store.get_message(target, "published"))
        self.assertTrue(store.get_outbound("request")["result"]["parts"][0]["publication_pending"])
        self.assertEqual(store.history(target)["revision"], revision)
        # Fail the intent update too, after the append path completed.
        store.db.execute("CREATE TEMP TRIGGER synthetic_publication_failure BEFORE UPDATE OF result ON outbound BEGIN SELECT RAISE(ABORT,'synthetic publication failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            store.publish_outbound_part("request", 0)
        store.db.execute("DROP TRIGGER synthetic_publication_failure")
        self.assertIsNone(store.get_message(target, "published"))
        self.assertTrue(store.get_outbound("request")["result"]["parts"][0]["publication_pending"])
        store.close()
        self.store = store = Store(self.path)
        published = store.publish_outbound_part("request", 0)
        self.assertTrue(published["inserted"])
        self.assertFalse(store.get_outbound("request")["result"]["parts"][0]["publication_pending"])
        self.assertFalse(store.publish_outbound_part("request", 0)["inserted"])
        store.clear_history(target)
        repeated = store.publish_outbound_part("request", 0)
        self.assertFalse(repeated["inserted"])
        self.assertIsNone(repeated["message"])
        self.assertEqual(store.history(target)["messages"], [])
        with self.assertRaises(ValueError):
            store.publish_outbound_part("request", True)
        with self.assertRaises(ValueError):
            store.publish_outbound_part("request", 1)

    def test_bounded_queues_maintenance_pagination_and_recovery(self):
        store = self.open()
        with patch("webchat.storage.QUEUE_HARD_LIMIT", 3):
            for n in range(3):
                store.enqueue_inbox({"n": n}, str(n))
                store.begin_outbound(str(n), "private:123", {"n": n})
            self.assertEqual(store.enqueue_inbox({}, "0"), 1)
            self.assertFalse(store.begin_outbound("0", "private:123", {"n": 0})["is_new"])
            with self.assertRaises(StorageBusy):
                store.enqueue_inbox({})
            with self.assertRaises(StorageBusy):
                store.begin_outbound("extra", "private:123", {})
        store.update_outbound("0", "partial", parts=[{"status": "confirmed"}])
        self.assertEqual(store.recover_outbound(), 2)
        self.assertEqual(store.pending_outbound(status="pending"), [])
        first = store.pending_outbound(limit=1)
        self.assertEqual(len(store.pending_outbound(after=first[-1]["request_id"])), 2)
        store.finish_inbox(1)
        store.finish_inbox(2, "synthetic")
        store.update_outbound("1", "confirmed")
        result = store.maintenance(retention_days=0, max_completed=0)
        self.assertEqual(result["removed"], {"inbox": 1, "outbound": 1})
        self.assertEqual(result["stats"]["inbox"], {"failed": 1, "pending": 1})
        self.assertEqual(result["stats"]["outbound"], {"partial": 1, "unknown": 1})
        with patch("webchat.storage.QUEUE_PAYLOAD_LIMIT", 10):
            with self.assertRaises(ValueError):
                store.begin_outbound("huge", "private:123", {"content": "x" * 11})
            with self.assertRaises(ValueError):
                store.update_outbound("0", "partial", content="x" * 11)
        for method, kwargs in ((store.pending_outbound, {"limit": 0}), (store.maintenance, {"retention_days": -1}), (store.maintenance, {"max_completed": True})):
            with self.assertRaises(ValueError):
                method(**kwargs)

    def test_full_queues_reclaim_terminal_rows_before_admission(self):
        store = self.open()
        with patch("webchat.storage.QUEUE_HARD_LIMIT", 3):
            for n in range(3):
                inbox = store.enqueue_inbox({"n": n}, str(n))
                store.finish_inbox(inbox)
                store.begin_outbound(str(n), "private:123", {"n": n})
                store.update_outbound(str(n), "confirmed")
            # No explicit maintenance needed to admit at exactly the hard limit.
            store.enqueue_inbox({}, "new")
            store.begin_outbound("new", "private:123", {})
            self.assertEqual(store.queue_stats(), {"inbox": {"done": 2, "pending": 1}, "outbound": {"confirmed": 2, "pending": 1}})
            self.assertIsNone(store.get_outbound("0"))
            self.assertIsNone(store.db.execute("SELECT id FROM inbox WHERE event_key='0'").fetchone())
            # The maintenance default cannot itself retain a full quota of done.
            store.finish_inbox(store.db.execute("SELECT id FROM inbox WHERE event_key='new'").fetchone()[0])
            store.update_outbound("new", "failed")
            result = store.maintenance()
            self.assertEqual(result["removed"], {"inbox": 1, "outbound": 1})
            self.assertEqual(sum(result["stats"]["inbox"].values()), 2)
            self.assertEqual(sum(result["stats"]["outbound"].values()), 2)
            store.enqueue_inbox({}, "last")
            store.begin_outbound("last", "private:123", {})
            # Mixed unresolved and completed pressure only removes completed rows.
            store.update_outbound("last", "unknown")
            store.enqueue_inbox({}, "after")
            store.begin_outbound("after", "private:123", {})
            self.assertEqual(store.get_outbound("last")["status"], "unknown")

    def test_runtime_writes_are_incremental(self):
        store = self.open()
        statements = []
        store.db.set_trace_callback(statements.append)
        store.append_message("private:10001", {"content": "synthetic"})
        self.assertFalse(any("UPDATE storage" in statement for statement in statements))
        self.assertFalse(any("SELECT payload FROM messages" in statement for statement in statements))


class AsyncStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "async.sqlite3"
        self.store = await AsyncStore.open(self.path, queue_limit=1)

    async def asyncTearDown(self):
        await self.store.close()
        self.tmp.cleanup()

    async def test_dedicated_worker_bounded_queue_and_cancel_consistency(self):
        entered, release = threading.Event(), threading.Event()
        original = Store.append_message
        thread_ids = []
        def delayed(store, *args, **kwargs):
            thread_ids.append(threading.get_ident())
            entered.set()
            release.wait(5)
            return original(store, *args, **kwargs)
        with patch.object(Store, "append_message", delayed):
            task = asyncio.create_task(self.store.call("append_message", "private:10001", {"message_id": "commit"}))
            await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), 6)
            try:
                with self.assertRaises(StorageBusy):
                    await self.store.call("history", "private:10001")
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        history = await self.store.call("history", "private:10001")
        self.assertEqual(history["messages"][0]["message_id"], "commit")
        self.assertNotEqual(thread_ids[0], threading.get_ident())

    async def test_concurrent_close_waits_and_repeated_cancellation_settles(self):
        entered, release = threading.Event(), threading.Event()
        original = Store.close
        def delayed(owner):
            entered.set()
            release.wait(5)
            return original(owner)
        with patch.object(Store, "close", delayed):
            first = asyncio.create_task(self.store.close())
            await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), 6)
            second = asyncio.create_task(self.store.close())
            try:
                await asyncio.sleep(0)
                self.assertFalse(first.done())
                self.assertFalse(second.done())
                first.cancel()
                await asyncio.sleep(0)
                first.cancel()
                await asyncio.sleep(0)
                self.assertFalse(first.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await first
            await second
        reopened = Store(self.path)
        reopened.close()

    async def test_open_cancellation_releases_owner(self):
        path = Path(self.tmp.name) / "cancelled-open.sqlite3"
        entered, release = threading.Event(), threading.Event()
        original = Store.__init__
        def delayed(owner, *args, **kwargs):
            original(owner, *args, **kwargs)
            entered.set()
            release.wait(5)
        with patch.object(Store, "__init__", delayed):
            opening = asyncio.create_task(AsyncStore.open(path))
            await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), 6)
            opening.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await opening
        reopened = Store(path)
        reopened.close()

    async def test_exception_releases_queue_slot(self):
        with self.assertRaises(ValueError):
            await self.store.call("history", "untyped")
        self.assertEqual((await self.store.call("history", "private:10001"))["messages"], [])


if __name__ == "__main__":
    unittest.main()
