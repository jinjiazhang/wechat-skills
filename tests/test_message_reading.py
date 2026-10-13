import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock

import zstandard as zstd

from wxdb import message
from wxdb.config import WeChatDBConfig
from wxdb.diagnose import export_schema
from wxdb.sqlcipher import _decode_json_stream, run_query


class ContentTests(unittest.TestCase):
    def decode(self, data, storage="blob"):
        row = {"message_content_type": storage, "message_content_hex": data.hex()}
        return message._decode_message_content(row), row

    def test_utf8_and_literal_backslashes(self):
        text = '中文 😀 C:\\Root\\new\n"quoted"\x00尾部'
        for storage in ("text", "blob"):
            self.assertEqual(self.decode(text.encode(), storage)[0], text)

    def test_zstd_with_and_without_declared_size(self):
        for has_size in (True, False):
            raw = zstd.ZstdCompressor(write_content_size=has_size).compress("中文消息".encode())
            text, row = self.decode(raw)
            self.assertEqual(text, "中文消息")
            self.assertEqual(row["content_decode_status"], "ok")

    def test_invalid_and_truncated_bytes_are_reported(self):
        compressed = zstd.ZstdCompressor().compress(b"a message")
        for raw in (b"\xff", compressed[:-2], compressed + b"junk"):
            text, row = self.decode(raw)
            self.assertEqual(text, "")
            self.assertEqual(row["content_decode_status"], "error")
            self.assertEqual(row["message_content_hex"], raw.hex())

    def test_size_limit(self):
        with patch.object(message, "MAX_CONTENT_BYTES", 8):
            for size in (True, False):
                raw = zstd.ZstdCompressor(write_content_size=size).compress(b"x" * 32)
                self.assertEqual(self.decode(raw)[1]["content_decode_status"], "error")

    def test_null_empty_invalid_hex(self):
        self.assertEqual(message._decode_message_content({"message_content_type": "null"}), "")
        self.assertEqual(self.decode(b"")[0], "")
        row = {"message_content_type": "blob", "message_content_hex": "xyz"}
        self.assertEqual(message._decode_message_content(row), "")
        self.assertEqual(row["content_decode_error"], "invalid_hex")

    def test_json_preserves_escaped_backslashes(self):
        payload = [{"text": "C:\\Root\\path\\[file]", "id": 9223372036854775807}]
        self.assertEqual(_decode_json_stream('[{"ok":"ok"}]\n' + json.dumps(payload)), payload)

    def test_sqlcipher_uses_stdin_and_readonly(self):
        config = Mock()
        config.resolve_db_path.return_value = Path("sample.db")
        config.get_db_key.return_value = "ab" * 32
        with patch("wxdb.sqlcipher.get_config", return_value=config), \
             patch("wxdb.sqlcipher._find_sqlcipher", return_value="sqlcipher"), \
             patch("wxdb.sqlcipher.subprocess.run", return_value=Mock(
                 returncode=0, stdout='[{"ok":"ok"}]\n[{"n":1}]', stderr="")) as run:
            self.assertEqual(run_query("message/message_0.db", "SELECT 1 AS n;"), [{"n": 1}])
            args, kwargs = run.call_args
            self.assertIn("-readonly", args[0])
            self.assertNotIn("ab" * 32, " ".join(args[0]))
            self.assertIn("ab" * 32, kwargs["input"])
            self.assertEqual(kwargs["timeout"], 30)


class ShardTests(unittest.TestCase):
    def setUp(self):
        self.connections = {}
        self.username = "test-session"
        self.table = message._message_table_name(self.username)
        for shard in (0, 1):
            path = f"message/message_{shard}.db"
            db = sqlite3.connect(":memory:")
            db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE Name2Id(user_name TEXT)")
            db.execute("INSERT INTO Name2Id VALUES (?)", (f"sender-{shard}",))
            db.execute(f"CREATE TABLE {self.table} (local_id INTEGER, server_id INTEGER, "
                       "local_type INTEGER, sort_seq INTEGER, real_sender_id INTEGER, "
                       "create_time INTEGER, status INTEGER, upload_status INTEGER, "
                       "download_status INTEGER, server_seq INTEGER, origin_source INTEGER, message_content)")
            for local_id, seq in enumerate((10 + shard * 10, 30 + shard * 10), start=1):
                db.execute(f"INSERT INTO {self.table} VALUES (?, ?, 1, ?, 1, ?, 0,0,0,0,0,?)",
                           (local_id, seq, seq, seq, f"message-{seq}"))
            self.connections[path] = db
        config = WeChatDBConfig("test", Path("."),
                               {p: {"enc_key": "00"} for p in self.connections})
        self.patches = [patch.object(message, "get_config", return_value=config),
                        patch.object(message, "run_query", side_effect=self.query)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        for db in self.connections.values():
            db.close()

    def query(self, path, sql):
        return [dict(r) for r in self.connections[path].execute(sql).fetchall()]

    def test_global_limit_order_and_shard_sender(self):
        rows = message.list_session_messages(self.username, limit=3)
        self.assertEqual([r["sort_seq"] for r in rows], [40, 30, 20])
        self.assertEqual([r["sender_username"] for r in rows], ["sender-1", "sender-0", "sender-1"])
        self.assertTrue(all("source_db" in r for r in rows))
        self.assertEqual([r["sort_seq"] for r in message.list_session_messages(
            self.username, limit=2, ascending=True, since_timestamp=20)], [20, 30])

    def test_new_sender_is_not_stale(self):
        message.list_session_messages(self.username)
        self.connections["message/message_1.db"].execute("UPDATE Name2Id SET user_name='updated'")
        self.assertEqual(message.list_session_messages(self.username, limit=1)[0]["sender_username"], "updated")

    def test_no_table_zero_and_invalid_limits(self):
        self.assertEqual(message.list_session_messages("absent"), [])
        self.assertEqual(message.list_session_messages(self.username, limit=0), [])
        for limit in (-1, "1; DROP TABLE Name2Id", True):
            with self.assertRaises(ValueError):
                message.list_session_messages(self.username, limit=limit)

    def test_numbered_shards_only_and_missing_config(self):
        config = WeChatDBConfig("test", Path("."), {p: {} for p in (
            "message/message_10.db", "message/message_2.db", "message/message_fts.db",
            "message/message_resource.db")})
        with patch.object(message, "get_config", return_value=config):
            self.assertEqual(message.message_db_paths(), ["message/message_2.db", "message/message_10.db"])
        with patch.object(message, "get_config", return_value=WeChatDBConfig("test", Path("."), {})):
            with self.assertRaises(ValueError):
                message.message_db_paths()

    def test_unread_merges_shards(self):
        with patch.object(message, "list_sessions", return_value=[{
            "username": self.username, "unread_count": 2, "last_clear_unread_timestamp": 0,
        }]):
            self.assertEqual([r["sort_seq"] for r in message.list_unread_messages()], [40, 30])

    def test_hex_transport_for_blob_and_text(self):
        text = "中文\\path\x00".encode()
        db = self.connections["message/message_1.db"]
        db.execute(f"UPDATE {self.table} SET message_content=?", (zstd.ZstdCompressor().compress(text),))
        rows = message.list_session_messages(self.username, limit=1)
        self.assertEqual(rows[0]["message_content"], text.decode())

    def test_schema_export_has_no_rows(self):
        with tempfile.TemporaryDirectory() as tmp, patch("wxdb.diagnose.run_query", side_effect=self.query):
            output = Path(tmp) / "export"
            report = export_schema(output)
            self.assertEqual(len(report["databases"]), 2)
            with closing(sqlite3.connect(output / "schema.sqlite")) as db:
                for shard in report["databases"]:
                    name = shard["samples"][0]["schema_table"]
                    self.assertEqual(db.execute(f"SELECT count(*) FROM {name}").fetchone()[0], 0)
            with self.assertRaises(FileExistsError):
                export_schema(output)


if __name__ == "__main__":
    unittest.main()
