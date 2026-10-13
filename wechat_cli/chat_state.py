"""Durable agent cursor and outbox. Never automatically retry uncertain sends."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3


class ChatError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def identity(row):
    # server_id may be populated after a local send; it is not the stable local identity.
    return f"{row['source_db']}:{row['local_id']}:{row['create_time']}"


def account_id(config):
    return hashlib.sha256(str(config.account_root.resolve()).casefold().encode()).hexdigest()


@contextmanager
def database(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=2)
    db.row_factory = sqlite3.Row
    db.executescript('''
    CREATE TABLE IF NOT EXISTS outbox (
        token TEXT PRIMARY KEY, account TEXT, session TEXT, sender TEXT, text TEXT,
        status TEXT, before_ids TEXT, started REAL, message_id TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS agents (name TEXT PRIMARY KEY, settings TEXT, pending TEXT, sends INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS seen (agent TEXT, message_id TEXT, PRIMARY KEY(agent,message_id));
    ''')
    try:
        yield db
    finally:
        db.close()


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
