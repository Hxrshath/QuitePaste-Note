import os
import secrets
import sqlite3
import time
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, url_for


BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("PASTEBIN_DATABASE", BASE_DIR / "notes.db"))
MAX_NOTE_CHARACTERS = 100_000
EXPIRY_SECONDS = {"10m": 10 * 60, "1h": 60 * 60}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1_100_000
app.config["DATABASE"] = str(DATABASE)


def connect_db():
    """Open a short-lived SQLite connection with sensible concurrency settings."""
    conn = sqlite3.connect(app.config["DATABASE"], timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db():
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    with connect_db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY,
                body TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER,
                burn_after_read INTEGER NOT NULL DEFAULT 0
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_expires_at ON notes(expires_at)")


@app.after_request
def add_security_headers(response):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'"
    return response


@app.get("/")
def home():
    return render_template("index.html", max_characters=MAX_NOTE_CHARACTERS)


@app.post("/create")
def create_note():
    body = request.form.get("body", "")
    expiry_choice = request.form.get("expiry", "10m")

    if not body.strip():
        return render_template("index.html", error="Please enter some text or code.", max_characters=MAX_NOTE_CHARACTERS), 400
    if len(body) > MAX_NOTE_CHARACTERS:
        return render_template("index.html", error=f"Notes can contain at most {MAX_NOTE_CHARACTERS:,} characters.", max_characters=MAX_NOTE_CHARACTERS), 413
    if expiry_choice not in (*EXPIRY_SECONDS.keys(), "once"):
        return render_template("index.html", error="Choose a valid expiration option.", max_characters=MAX_NOTE_CHARACTERS), 400

    now = int(time.time())
    expires_at = now + EXPIRY_SECONDS[expiry_choice] if expiry_choice in EXPIRY_SECONDS else None
    burn_after_read = int(expiry_choice == "once")

    # A 192-bit random token makes links impractical to guess.
    conn = connect_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM notes WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,))
        note_id = None
        for _ in range(3):
            candidate = secrets.token_urlsafe(24)
            try:
                conn.execute(
                    "INSERT INTO notes (id, body, created_at, expires_at, burn_after_read) VALUES (?, ?, ?, ?, ?)",
                    (candidate, body, now, expires_at, burn_after_read),
                )
                note_id = candidate
                break
            except sqlite3.IntegrityError:
                continue
        if note_id is None:
            conn.rollback()
            app.logger.error("Could not generate a unique note ID")
            abort(500)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return render_template(
        "created.html",
        note_url=url_for("read_note", note_id=note_id, _external=True),
        expiry_label={"10m": "10 minutes", "1h": "1 hour", "once": "after it is viewed once"}[expiry_choice],
    )


@app.get("/n/<note_id>")
def read_note(note_id):
    # HEAD requests (often used by link preview bots) must not consume one-time notes.
    if request.method == "HEAD":
        return "", 200

    now = int(time.time())
    conn = connect_db()
    try:
        if request.method == "HEAD":
            return "", 200

        # BEGIN IMMEDIATE serializes the read-and-delete operation so concurrent
        # requests cannot both successfully retrieve a one-time note.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            conn.commit()
            abort(404)
        if row["expires_at"] is not None and row["expires_at"] <= now:
            conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            conn.commit()
            abort(404)
        if row["burn_after_read"]:
            conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return render_template("note.html", body=row["body"], one_time=bool(row["burn_after_read"]))


@app.errorhandler(404)
def not_found(_error):
    return render_template("unavailable.html"), 404


@app.errorhandler(413)
def too_large(_error):
    return render_template("index.html", error="That request is too large. Please shorten the note and try again.", max_characters=MAX_NOTE_CHARACTERS), 413


init_db()

if __name__ == "__main__":
    # For local development only. Use a production WSGI server for deployment.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
