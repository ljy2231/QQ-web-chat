import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('backup_tool', Path(__file__).parents[1] / 'tools' / 'backup.py')
backup_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup_tool)


class BackupTests(unittest.TestCase):
    def test_backup_wal_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory)/'source.sqlite3', Path(directory)/'copy.sqlite3'
            connection = sqlite3.connect(source)
            try:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT)')
                connection.execute('INSERT INTO messages(body) VALUES (?)', ('synthetic',))
                connection.commit()
                result = backup_tool.backup_database(source, target)
                self.assertEqual(result['integrity'], 'ok')
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                restored = sqlite3.connect(target)
                try:
                    self.assertEqual(restored.execute('SELECT body FROM messages').fetchone()[0], 'synthetic')
                finally:
                    restored.close()
                with self.assertRaises(ValueError):
                    backup_tool.backup_database(source, target)
            finally:
                connection.close()


if __name__ == '__main__':
    unittest.main()
