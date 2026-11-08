import re
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlparse

import app as paste_app


class ExpiringNotesTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp_dir.name) / "test.db")
        paste_app.app.config.update(TESTING=True, DATABASE=self.database)
        with sqlite3.connect(self.database) as conn:
            conn.execute(
                """CREATE TABLE notes (
                    id TEXT PRIMARY KEY,
                    body TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER,
                    burn_after_read INTEGER NOT NULL DEFAULT 0
                )"""
            )
            conn.execute("CREATE INDEX idx_notes_expires_at ON notes(expires_at)")
        self.client = paste_app.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_note(self, body="hello", expiry="10m"):
        response = self.client.post("/create", data={"body": body, "expiry": expiry})
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'href="([^"]+/n/([^"/]+))"', response.data)
        # The URL itself is shown in the readonly input rather than an anchor.
        if match is None:
            match = re.search(rb'value="([^"]+/n/([^"/]+))"', response.data)
        self.assertIsNotNone(match)
        return "/n/" + match.group(2).decode()

    def test_one_time_note_is_deleted_after_first_get(self):
        path = self.create_note("secret", "once")
        first = self.client.get(path)
        self.assertEqual(first.status_code, 200)
        self.assertIn(b"secret", first.data)
        self.assertIn(b"deleted from the database", first.data)
        second = self.client.get(path)
        self.assertEqual(second.status_code, 404)

    def test_head_does_not_consume_one_time_note(self):
        path = self.create_note("head safe", "once")
        self.assertEqual(self.client.head(path).status_code, 200)
        self.assertEqual(self.client.get(path).status_code, 200)

    def test_expired_note_is_unavailable(self):
        path = self.create_note("old note", "10m")
        note_id = path.rsplit("/", 1)[1]
        with sqlite3.connect(self.database) as conn:
            conn.execute("UPDATE notes SET expires_at = ? WHERE id = ?", (int(time.time()) - 1, note_id))
        self.assertEqual(self.client.get(path).status_code, 404)

    def test_note_text_is_escaped(self):
        path = self.create_note("<script>alert(1)</script>")
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"&lt;script&gt;", response.data)
        self.assertNotIn(b"<script>alert(1)</script>", response.data)

    def test_blank_note_is_rejected(self):
        response = self.client.post("/create", data={"body": "   ", "expiry": "10m"})
        self.assertEqual(response.status_code, 400)

    def test_invalid_expiration_is_rejected(self):
        response = self.client.post("/create", data={"body": "hello", "expiry": "never"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
