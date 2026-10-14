"""Selected SQLite snapshots, conservatively modeled; native validation pending.

No schema version gate, discovery, database writes, or message filtering. The
digest covers retained selected rows and their column inventory, never DB bytes.
"""

import base64
from contextlib import contextmanager
import hashlib
import math
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time

from . import SCHEMA_VERSION
from .records import NormalizationError, encode, loads, pointer
from .summary import Summary

FORMAT = "hermes-sqlite"
ADAPTER_VERSION = "hermes-1"
DIGEST_SCOPE = "hermes-selected-rows-v1"
ENCODING = "SQLite BLOB/invalid UTF8 TEXT: tagged base64; nonfinite REAL: tagged hex; other values: JSON"
SESSION_CODES = {"native_validation_pending", "optional_schema_absent", "empty_messages"}
DIAGNOSTICS = {"unsupported_role", "unsupported_content", "opaque_fields_retained",
               "inactive_evidence", "compacted_evidence", "summary_evidence",
               "malformed_tool_calls", "unsupported_tool_calls", "invalid_tool_identity",
               "missing_timestamp", "unsupported_timestamp"}
ROLES = {"user", "assistant", "system", "developer", "tool", "function"}


def text_value(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeError:
        return {"sqlite_type": "text", "base64": base64.b64encode(raw).decode("ascii")}


def retained(value):
    if isinstance(value, bytes):
        return {"sqlite_type": "blob", "base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float) and not math.isfinite(value):
        return {"sqlite_type": "real", "hex": value.hex()}
    return value


def row_value(row):
    return {key: retained(row[key]) for key in row.keys()}


def inventory(conn, table, required):
    # Only literal table names from this module are ever interpolated.
    kind = conn.execute("SELECT type FROM sqlite_master WHERE name=?", (table,)).fetchone()
    if not kind or kind[0] != "table":
        raise NormalizationError("unsupported_hermes_schema")
    columns = [dict(row) for row in conn.execute('PRAGMA table_info("' + table + '")')]
    names = {column["name"] for column in columns}
    primary = [c["name"] for c in sorted(columns, key=lambda c: c["pk"]) if c["pk"]]
    if not required <= names or primary != ["id"]:
        raise NormalizationError("unsupported_hermes_schema")
    return columns


@contextmanager
def snapshot(source, selected_session, source_identity=None):
    if not isinstance(selected_session, str) or not selected_session:
        raise NormalizationError("selected_session_required")
    deadline = time.monotonic() + 30
    conn = None
    def verify_path():
        info = Path(source).lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                source_identity is not None and (info.st_dev, info.st_ino) != (source_identity.st_dev, source_identity.st_ino)):
            raise NormalizationError("hermes_source_identity_changed")
    try:
        verify_path()
        conn = sqlite3.connect(Path(source).as_uri() + "?mode=ro", uri=True, timeout=2)
        verify_path()
        conn.row_factory = sqlite3.Row
        conn.text_factory = text_value
        conn.execute("PRAGMA query_only=ON")
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        conn.execute("BEGIN")
        schema = {"sessions": inventory(conn, "sessions", {"id"}),
                  "messages": inventory(conn, "messages", {"id", "session_id", "role", "content"})}
        rows = conn.execute("SELECT * FROM sessions WHERE id=?", (selected_session,)).fetchmany(2)
        if len(rows) != 1 or rows[0]["id"] != selected_session:
            raise NormalizationError("selected_session_not_found_or_conflicting")
        session = row_value(rows[0])

        def records():
            yield "sessions", session
            previous = None
            for row in conn.execute("SELECT * FROM messages WHERE session_id=? ORDER BY id", (selected_session,)):
                if time.monotonic() > deadline:
                    raise NormalizationError("hermes_snapshot_timeout")
                value = row_value(row)
                if type(value["id"]) not in (int, str) or value["id"] == "" or value["session_id"] != selected_session:
                    raise NormalizationError("invalid_hermes_record_identity")
                if previous is not None and (type(previous) != type(value["id"]) or previous >= value["id"]):
                    raise NormalizationError("invalid_hermes_record_order")
                previous = value["id"]
                yield "messages", value
            verify_path()
        yield schema, session, records
    except sqlite3.Error:
        raise NormalizationError("hermes_sqlite_read_failed") from None
    finally:
        if conn is not None:
            conn.close()  # Rolls back the read transaction; never checkpoint.


def digest_start(schema):
    digest = hashlib.sha256()
    digest.update((encode([DIGEST_SCOPE, schema]) + "\n").encode())
    return digest


def digest_row(digest, table, row):
    raw = (encode([table, row]) + "\n").encode()
    digest.update(raw)
    return len(raw)


def event(row, table, session_id, revision, order):
    locator = {"table": table, "primary_key": {"id": row["id"]}}
    issues, canonical, interactions = set(), {}, []
    kind, timestamp = "source_metadata", None
    if table == "messages":
        role, content = row["role"], row["content"]
        kind = "message" if isinstance(role, str) and role in ROLES else "unsupported"
        if kind == "unsupported":
            issues.add("unsupported_role")
        canonical = {k: row[k] for k in ("role", "content", "tool_call_id", "tool_name", "message_uid",
                                        "tool_call_uid", "tool_call_uids", "finish_reason") if k in row}
        if content is not None and not isinstance(content, str):
            issues.add("unsupported_content")
        if row.get("active") == 0:
            issues.add("inactive_evidence")
        if row.get("compacted"):
            issues.add("compacted_evidence")
        if row.get("_compressed_summary"):
            issues.add("summary_evidence")
        timestamp = row.get("timestamp")
        if timestamp is None:
            issues.add("missing_timestamp")
        elif type(timestamp) not in (int, float, str):
            issues.add("unsupported_timestamp")
        raw_calls = row.get("tool_calls")
        if raw_calls is not None:
            try:
                calls = loads(raw_calls) if isinstance(raw_calls, str) else None
            except (ValueError, RecursionError):
                calls = None
                issues.add("malformed_tool_calls")
            if not isinstance(calls, list):
                issues.add("unsupported_tool_calls")
            else:
                for index, call in enumerate(calls):
                    if (not isinstance(call, dict) or call.get("type") != "function"
                            or not isinstance(call.get("function"), dict)):
                        issues.add("unsupported_tool_calls")
                        continue
                    function = call["function"]
                    view = {"call_id": call.get("id"), "name": function.get("name"),
                            "arguments": function.get("arguments")}
                    if any(not isinstance(view[key], str) or not view[key] for key in ("call_id", "name")):
                        issues.add("invalid_tool_identity")
                    if not isinstance(view["arguments"], str):
                        issues.add("unsupported_tool_calls")
                    interactions.append({"kind": "tool_call", "canonical": view, "outcome": "unknown",
                                         "storage_field": "tool_calls", "json_pointer": f"/{index}"})
        if role in ("tool", "function"):
            if not isinstance(row.get("tool_call_id"), str) or not row["tool_call_id"]:
                issues.add("invalid_tool_identity")
            interactions.append({"kind": "tool_result", "outcome": "unknown", "storage_field": "content",
                                 "canonical": {"call_id": row.get("tool_call_id"),
                                               "name": row.get("tool_name"), "output": content}})
        interpreted = {"id", "session_id", "role", "content", "timestamp", "tool_calls", "tool_call_id",
                       "tool_name", "active", "compacted", "_compressed_summary"}
        if any(k not in interpreted and row[k] is not None for k in row):
            issues.add("opaque_fields_retained")
    identity = [SCHEMA_VERSION, ADAPTER_VERSION, FORMAT, session_id, revision, order, locator]
    return {"record_kind": "event", "event_id": "event:" + hashlib.sha256(encode(identity).encode()).hexdigest(),
            "session_id": session_id, "source_revision_id": revision, "order": order, "kind": kind,
            "locator": locator, "timestamp": timestamp,
            "timestamp_pointer": "/timestamp" if "timestamp" in row else None,
            "source_type": table, "source_subtype": None, "canonical": canonical,
            "field_pointers": {key: "/" + pointer(key) for key in canonical},
            "source_field_pointers": {key: "/" + pointer(key) for key in row},
            "interactions": interactions, "outcome": "unknown", "diagnostics": sorted(issues), "source_record": row}


class HermesSummary(Summary):
    session_codes = SESSION_CODES
    diagnostic_codes = DIAGNOSTICS
    source_types = {"sessions", "messages"}

    def __init__(self, diagnostics):
        super().__init__(diagnostics)
        self.interactions = {"tool_call": 0, "tool_result": 0}

    def add(self, value):
        for item in value["interactions"]:
            self.interactions[item["kind"]] += 1
        super().add(value)

    def result(self):
        return {**super().result(), "tool_interactions": sum(self.interactions.values()),
                "tool_interaction_types": dict(self.interactions)}


def normalize(source, destination, selected_session, source_identity=None):
    temporary = None
    try:
        with snapshot(source, selected_session, source_identity) as (schema, session, records):
            digest, count, size = digest_start(schema), 0, 0
            for table, row in records():
                size += digest_row(digest, table, row)
                count += 1
            revision = digest.hexdigest()
            issues = ["native_validation_pending"]
            if not {"tool_calls", "timestamp", "active", "compacted"} <= {c["name"] for c in schema["messages"]}:
                issues.append("optional_schema_absent")
            if count == 1:
                issues.append("empty_messages")
            header = {"record_kind": "session", "schema_version": SCHEMA_VERSION, "adapter_version": ADAPTER_VERSION,
                      "provider": "hermes", "session_id": "hermes:" + selected_session,
                      "native_session_id": selected_session, "observed_metadata": session,
                      "parent_session_id": session.get("parent_session_id"),
                      "parent_session_relation": "recorded_linkage_only",
                      "source": {"reference": str(source), "format": FORMAT, "sha256": revision,
                                 "digest_scope": DIGEST_SCOPE, "snapshot_row_bytes": size},
                      "sqlite_schema": schema, "value_encoding": ENCODING,
                      "source_records": count, "diagnostics": sorted(issues)}
            summary = HermesSummary(header["diagnostics"])
            descriptor, temporary = tempfile.mkstemp(prefix=".tokenmaxxing-", dir=destination.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as out:
                out.write(encode(header) + "\n")
                for order, (table, row) in enumerate(records()):
                    value = event(row, table, header["session_id"], revision, order)
                    summary.add(value)
                    out.write(encode(value) + "\n")
                result = summary.result()
                out.write(encode({"record_kind": "summary", **result}) + "\n")
                out.flush()
                os.fsync(out.fileno())
            os.link(temporary, destination)
            return result
    finally:
        if temporary is not None:
            os.unlink(temporary)


class Inspector:
    def __init__(self, header):
        self.header = header
        self.previous = None
        self.size = 0
        if (header.get("session_id") != "hermes:" + (header.get("native_session_id") or "")
                or not header.get("native_session_id") or header.get("value_encoding") != ENCODING
                or header["source"].get("digest_scope") != DIGEST_SCOPE
                or not isinstance(header.get("sqlite_schema"), dict)
                or set(header["sqlite_schema"]) != {"sessions", "messages"}
                or not isinstance(header.get("observed_metadata"), dict)
                or header["observed_metadata"].get("id") != header["native_session_id"]
                or header.get("parent_session_relation") != "recorded_linkage_only"
                or header.get("parent_session_id") != header["observed_metadata"].get("parent_session_id")
                or "native_validation_pending" not in header.get("diagnostics", [])):
            raise NormalizationError("invalid_normalized_header")
        self.digest = digest_start(header["sqlite_schema"])

    def add(self, value):
        table = "sessions" if value["order"] == 0 else "messages"
        row = value["source_record"]
        if (not isinstance(row, dict) or "id" not in row or
                (table == "sessions" and row != self.header["observed_metadata"]) or
                (table == "messages" and row.get("session_id") != self.header["native_session_id"])):
            raise NormalizationError("invalid_hermes_record")
        expected = event(row, table, self.header["session_id"], self.header["source"]["sha256"], value["order"])
        if expected != value:
            raise NormalizationError("invalid_hermes_record")
        if table == "messages":
            if type(row["id"]) not in (int, str) or row["id"] == "":
                raise NormalizationError("invalid_hermes_record_identity")
            if self.previous is not None and (type(self.previous) != type(row["id"]) or self.previous >= row["id"]):
                raise NormalizationError("invalid_hermes_record_order")
            self.previous = row["id"]
        self.size += digest_row(self.digest, table, row)

    def finish(self):
        if (self.digest.hexdigest() != self.header["source"]["sha256"] or
                self.size != self.header["source"].get("snapshot_row_bytes")):
            raise NormalizationError("invalid_hermes_snapshot_digest")
