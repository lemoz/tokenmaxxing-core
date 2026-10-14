"""Create explicitly synthetic operator fixtures in a NEW private directory.

Never discovers a history or creates an app store. No native inputs are read.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tokenmaxxing.private_paths import private_directory

STAMP = "2026-01-02T03:04:05Z"
ARTIFACT = b"Synthetic Lantern index\nNorth: maps\nSouth: notes\n"
HOSTILE = '<script>window.tokenmaxxingInjected=true</script><img src="https://invalid.example/never-load">'


def write_private(path, data):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(data)


def jsonl(path, rows):
    write_private(path, b"".join((json.dumps(row, sort_keys=True) + "\n").encode() for row in rows))


def hermes_fixture(path, minimal=False):
    write_private(path, b"")
    conn = sqlite3.connect(path)
    try:
        if minimal:
            conn.executescript("CREATE TABLE sessions(id TEXT PRIMARY KEY); CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT);")
            conn.execute("INSERT INTO sessions VALUES ('synthetic-hermes-minimal')")
            conn.execute("INSERT INTO messages VALUES (1,'synthetic-hermes-minimal','user','Synthetic minimal request')")
        else:
            conn.executescript("""CREATE TABLE sessions(id TEXT PRIMARY KEY, parent_session_id TEXT, source TEXT,
                started_at REAL, future_blob BLOB);
                CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
                tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL, active INTEGER,
                compacted INTEGER, _compressed_summary INTEGER, display_identity BLOB, message_uid TEXT,
                tool_call_uid TEXT, tool_call_uids TEXT, absorbed_message_uids TEXT, effect_disposition TEXT,
                finish_reason TEXT, future_field TEXT);
            """)
            conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?)", ('synthetic-hermes', 'synthetic-parent', 'synthetic', 1.0, b'\x00\xff'))
            conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?)", ('synthetic-unselected', None, 'synthetic', 2.0, None))
            calls = json.dumps([{"id": "synthetic-call", "type": "function", "function": {"name": "read_synthetic", "arguments": '{"path":"inert"}'}}])
            rows = [
                (1, 'user', 'Sketch a synthetic notes export.', None, None, None, 1, 0, 0),
                (2, 'assistant', HOSTILE, None, calls, None, 1, 0, 0),
                (3, 'tool', 'success (source claim only)', 'synthetic-call', None, 'read_synthetic', 1, 0, 0),
                (4, 'assistant', 'Inactive synthetic earlier draft', None, None, None, 0, 1, 0),
                (5, 'user', 'Synthetic summary is not new human intent', None, None, None, 1, 0, 1),
                (6, 'attachment', 'Unsupported synthetic role', None, '{bad', None, 1, 0, 0),
            ]
            for i, role, content, call, tools, name, active, compacted, summary in rows:
                conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    i, 'synthetic-hermes', role, content, call, tools, name, float(i), active, compacted, summary,
                    b'\x00\xff' + bytes([i]), 'synthetic-message-' + str(i), 'durable-tool-uid', '["durable-tool-uid"]',
                    '["absorbed-uid"]', 'success-is-not-outcome', 'stop', 'opaque synthetic value'))
            conn.execute("INSERT INTO messages(id,session_id,role,content) VALUES (99,'synthetic-unselected','user','must not import')")
        conn.commit()
    finally:
        conn.close()


def fixtures(root):
    """Caller supplies an empty synthetic temporary/private directory."""
    root = Path(root)
    codex = [{"type": "session_meta", "timestamp": STAMP, "payload": {"id": "synthetic-codex", "timestamp": STAMP}},
             {"type": "response_item", "timestamp": STAMP, "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Make a synthetic Lantern index."}]}},
             {"type": "response_item", "timestamp": STAMP, "payload": {"type": "function_call", "name": "synthetic_read", "call_id": "call-1", "arguments": "do not execute"}},
             {"type": "response_item", "timestamp": STAMP, "payload": {"type": "function_call_output", "call_id": "call-1", "output": "success is a source claim"}},
             {"type": "synthetic-unsupported", "timestamp": STAMP, "payload": {"untrusted": HOSTILE}}]
    jsonl(root / 'synthetic-codex.jsonl', codex)
    jsonl(root / 'synthetic-codex-copy.jsonl', codex)
    jsonl(root / 'synthetic-codex-revision.jsonl', codex + [{"type": "response_item", "timestamp": STAMP, "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Synthetic revised draft."}]}}])
    write_private(root / 'synthetic-legacy.json', json.dumps({"session": {"id": "synthetic-legacy", "timestamp": STAMP}, "items": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Synthetic legacy request"}], "timestamp": STAMP}]}).encode())
    main = [{"type": "user", "sessionId": "synthetic-claude", "isSidechain": False, "uuid": "synthetic-1", "timestamp": STAMP, "message": {"role": "user", "content": "Review a synthetic field list."}},
            {"type": "assistant", "sessionId": "synthetic-claude", "isSidechain": False, "uuid": "synthetic-2", "parentUuid": "synthetic-1", "timestamp": STAMP, "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "synthetic-tool", "name": "Read", "input": {"path": "inert"}}]}},
            {"type": "user", "sessionId": "synthetic-claude", "isSidechain": False, "uuid": "synthetic-3", "timestamp": STAMP, "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "synthetic-tool", "is_error": False, "content": "Synthetic success claim"}]}}]
    jsonl(root / 'synthetic-claude.jsonl', main)
    jsonl(root / 'synthetic-claude-child.jsonl', [{**main[1], "agentId": "synthetic-agent", "isSidechain": True}])
    hermes_fixture(root / 'synthetic-hermes.sqlite3')
    hermes_fixture(root / 'synthetic-hermes-minimal.sqlite3', minimal=True)
    write_private(root / 'synthetic-artifact.txt', ARTIFACT)
    write_private(root / 'synthetic-empty.txt', b'')
    return {"label": "Clearly synthetic fixtures only; no native inputs", "directory": str(root),
            "artifact_sha256": hashlib.sha256(ARTIFACT).hexdigest(), "hermes_session": "synthetic-hermes"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    args = parser.parse_args()
    path = Path(args.directory)
    if path.exists() or path.is_symlink():
        parser.error('Fixture directory must be new; nothing is overwritten.')
    print(json.dumps(fixtures(private_directory(path)), sort_keys=True))


if __name__ == '__main__':
    main()
