"""All fixtures in this module are synthetic; no private transcript examples."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tokenmaxxing.__main__ import main
from tokenmaxxing.events import event
from tokenmaxxing.normalize import inspect, normalize
from tokenmaxxing.records import NormalizationError, Reader, Record
from tokenmaxxing.summary import Summary


def message(text="synthetic repeated message"):
    return {"type": "response_item", "timestamp": "2026-01-01T00:00:00Z", "payload": {
        "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}


def session():
    return {"type": "session_meta", "payload": {"id": "opaque:Test/01", "timestamp": "2026-01-01"}}


class EventTests(unittest.TestCase):
    def test_repeated_messages_have_distinct_stable_ids(self):
        raw = {"type": "response_item", "payload": {"type": "message", "role": "user",
               "content": [{"type": "input_text", "text": "synthetic repeated message"}]}}
        def make(line):
            return event(Record({"line": line, "pointer": ""}, raw), "codex-jsonl", "codex:test", "hash", line)
        self.assertEqual(make(1), make(1))
        self.assertNotEqual(make(1)["event_id"], make(2)["event_id"])
        self.assertEqual(make(1)["canonical"]["content"], raw["payload"]["content"])
        self.assertEqual(make(1)["outcome"], "unknown")


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "synthetic.source"
        self.output = self.root / "normalized.jsonl"

    def write_jsonl(self, rows):
        self.source.write_text("".join(json.dumps(x) + "\n" for x in rows))

    def read_output(self, output=None):
        return [json.loads(x) for x in (output or self.output).read_text().splitlines()]

    def run_import(self, fmt="codex-jsonl"):
        return normalize(self.source, self.output, fmt)

    def test_repeat_copy_and_revision_identities(self):
        self.write_jsonl([message(), session(), message()])
        summary = self.run_import()
        first = self.read_output()
        repeated = self.root / "repeated.jsonl"
        self.assertEqual(summary, normalize(self.source, repeated, "codex-jsonl"))
        self.assertEqual(first, self.read_output(repeated))
        copied = self.root / "copy.source"
        copied.write_bytes(self.source.read_bytes())
        copy_output = self.root / "copy.jsonl"
        normalize(copied, copy_output, "codex-jsonl")
        self.assertEqual(first[1:], self.read_output(copy_output)[1:])
        self.assertNotEqual(first[1]["event_id"], first[3]["event_id"])
        self.assertEqual(first[0]["native_session_id"], "opaque:Test/01")
        self.assertEqual(summary, inspect(self.output))
        self.source.write_bytes(self.source.read_bytes() + b'{}\n')
        revision = self.root / "revision.jsonl"
        normalize(self.source, revision, "codex-jsonl")
        self.assertEqual(first[0]["session_id"], self.read_output(revision)[0]["session_id"])
        self.assertNotEqual(first[1]["event_id"], self.read_output(revision)[1]["event_id"])

    def test_tools_projections_and_unknown_evidence(self):
        items = [message(),
                 {"type": "response_item", "payload": {"type": "custom_tool_call", "call_id": "A:01", "name": "synthetic_tool", "input": "inert input", "status": "completed"}},
                 {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "A:01", "output": [{"type": "text", "text": "inert result"}]}},
                 {"type": "event_msg", "payload": {"type": "user_message", "message": "synthetic repeated message"}},
                 {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "tool"}}},
                 {"type": "event_msg", "payload": {"type": "task_complete"}},
                 {"type": "response_item", "payload": {"type": "compaction", "encrypted_content": "synthetic opaque"}},
                 {"type": "novel_record", "payload": {"type": "novel_payload"}},
                 {"type": ["unexpected"], "payload": {"type": {"unexpected": 1}}}]
        self.write_jsonl([session()] + items)
        result = self.run_import()
        events = self.read_output()[1:-1]
        self.assertEqual(result["canonical_events"], 3)
        self.assertEqual(result["event_types"]["source_projection"], 2)
        self.assertEqual(result["diagnostics"]["encrypted_evidence"], 1)
        for expected, observed in zip([session()] + items, events):
            self.assertEqual(expected, observed["source_record"])
            self.assertEqual(observed["outcome"], "unknown")
        self.assertEqual(events[2]["canonical"]["call_id"], "A:01")

    def test_legacy_metadata_tools_blocks_and_extra_root(self):
        rows = [message()["payload"],
                {"type": "function_call", "call_id": "F/1", "name": "synthetic", "arguments": '{"x":1}', "status": "completed"},
                {"type": "function_call_output", "call_id": "F/1", "output": "opaque output"},
                {"type": "reasoning", "summary": []}]
        self.source.write_text(json.dumps({"session": {"id": "legacy", "instructions": "inert", "timestamp": "2025"},
                                           "items": rows, "new/key": {"synthetic": True}}))
        result = self.run_import("codex-legacy-json")
        events = self.read_output()[1:-1]
        self.assertEqual(result["events"], 6)
        self.assertEqual(events[1]["locator"]["pointer"], "/new~1key")
        self.assertEqual(events[2]["block_pointers"], ["/items/0/content/0"])
        self.assertEqual(events[3]["canonical"]["arguments"], '{"x":1}')
        self.assertIsNone(events[3]["timestamp"])
        self.assertEqual(events[0]["source_record"]["instructions"], "inert")

    def test_source_format_gates_legacy_items(self):
        items = [message()["payload"],
                 {"type": "custom_tool_call", "call_id": "C/1", "name": "synthetic", "input": "inert"},
                 {"type": "custom_tool_call_output", "call_id": "C/1", "output": {"synthetic": True}},
                 {"type": "function_call", "call_id": "F/1", "name": "synthetic", "arguments": "{}"},
                 {"type": "function_call_output", "call_id": "F/1", "output": "inert"}]
        for fmt, wire_type, supported in (("codex-jsonl", "legacy_item", False),
                                          ("codex-jsonl", "response_item", True),
                                          ("codex-legacy-json", None, True)):
            with self.subTest(format=fmt, wire_type=wire_type):
                meta = session()
                timestamp = meta["payload"]["timestamp"]
                if fmt == "codex-jsonl":
                    meta["timestamp"] = timestamp
                    records = [{"type": wire_type, "timestamp": timestamp, "payload": item} for item in items]
                    self.write_jsonl([meta] + records)
                else:
                    records = [{**item, "timestamp": timestamp} for item in items]
                    self.source.write_text(json.dumps({"session": meta["payload"], "items": records}))
                output = self.root / (fmt + str(wire_type) + ".jsonl")
                result = normalize(self.source, output, fmt)
                self.assertEqual(result["canonical_events"], len(items) if supported else 0)
                self.assertEqual(result["coverage"], "no_known_gaps" if supported else "partial")
                self.assertEqual(result["diagnostics"], {} if supported else {"unsupported_record_type": len(items)})
                self.assertEqual(result, inspect(output))
                for item, raw, observed in zip(items, records, self.read_output(output)[2:-1]):
                    self.assertEqual(observed["source_record"], raw)
                    self.assertEqual(observed["canonical"], {k: v for k, v in item.items() if k != "type"} if supported else {})
                    if not supported:
                        self.assertEqual(observed["kind"], "unsupported")

    def test_missing_metadata_rich_content_and_bad_tool(self):
        m = message()
        m["payload"]["content"].append({"type": "input_image", "image_url": "synthetic://image"})
        self.write_jsonl([m, {"type": "response_item", "payload": {"type": "function_call"}}])
        result = self.run_import()
        header = self.read_output()[0]
        self.assertIsNone(header["session_id"])
        self.assertIn("missing_native_session_id", result["session_diagnostics"])
        self.assertIn("unsupported_content_block", result["diagnostics"])
        self.assertIn("missing_tool_call_id", result["diagnostics"])

    def test_bad_json_lines_retained_with_diagnostics(self):
        self.source.write_bytes(b'{"secret":"synthetic marker"\n\xff\n\n{"x":1,"x":2}\n{"x":NaN}\n{}')
        result = self.run_import()
        self.assertEqual(result["events"], 6)
        self.assertEqual(result["diagnostics"]["invalid_json_record"], 5)
        self.assertEqual(result["diagnostics"]["unterminated_final_line"], 1)
        self.assertIn("raw_bytes_base64", self.read_output()[1]["source_record"])

    def test_invalid_legacy_is_not_published(self):
        for raw in ('{"session":', '{"session":{},"items":{}}', '{"session":{},"items":[],"items":[]}'):
            self.source.write_text(raw)
            with self.assertRaises(NormalizationError):
                self.run_import("codex-legacy-json")
            self.assertFalse(self.output.exists())

    def test_output_safety_and_permissions(self):
        self.write_jsonl([session(), message()])
        before = self.source.read_bytes()
        self.output.symlink_to(self.source)
        for target in (self.source, self.output):
            with self.assertRaises(NormalizationError):
                normalize(self.source, target, "codex-jsonl")
        self.output.unlink()
        os.link(self.source, self.output)
        with self.assertRaises(NormalizationError):
            self.run_import()
        self.output.unlink()
        self.output.symlink_to(self.root / "missing")
        with self.assertRaises(NormalizationError):
            self.run_import()
        self.output.unlink()
        shared = self.root / "shared"
        shared.mkdir(mode=0o755)
        shared.chmod(0o755)
        with self.assertRaises(NormalizationError):
            normalize(self.source, shared / "out", "codex-jsonl")
        self.run_import()
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(NormalizationError):
            self.run_import()
        self.assertEqual(before, self.source.read_bytes())

    def test_source_change_rejects_output(self):
        import importlib
        module = importlib.import_module("tokenmaxxing.normalize")
        original = module._header
        self.write_jsonl([session(), message()])
        def change(*args):
            result = original(*args)
            with self.source.open("ab") as source:
                source.write(b'{}\n')
            return result
        with patch.object(module, "_header", change):
            with self.assertRaisesRegex(NormalizationError, "source_changed"):
                self.run_import()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".tokenmaxxing-*")), [])

    def test_conflicting_session_ids_reject_output(self):
        other = session()
        other["payload"]["id"] = "different"
        self.write_jsonl([session(), other])
        with self.assertRaisesRegex(NormalizationError, "conflicting"):
            self.run_import()
        self.assertFalse(self.output.exists())

    def test_inspect_detects_missing_footer_and_changed_event_id(self):
        self.write_jsonl([session(), message()])
        self.run_import()
        rows = self.read_output()
        self.output.write_text("".join(json.dumps(x) + "\n" for x in rows[:-1]))
        with self.assertRaises(NormalizationError):
            inspect(self.output)
        rows[1]["event_id"] = "altered"
        self.output.write_text("".join(json.dumps(x) + "\n" for x in rows))
        with self.assertRaises(NormalizationError):
            inspect(self.output)

    def test_inspect_rejects_empty_header_missing_required_fields(self):
        header = {"record_kind": "session", "schema_version": "1", "adapter_version": "codex-1",
                  "diagnostics": [], "source_records": 0}
        footer = {"record_kind": "summary", **Summary([]).result()}
        self.output.write_text(json.dumps(header) + "\n" + json.dumps(footer) + "\n")
        with self.assertRaises(NormalizationError):
            inspect(self.output)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["inspect", "--source", str(self.output)])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(stderr.getvalue()), {"error": "invalid_normalized_header"})

    def test_inspect_validates_empty_header_fields_and_types(self):
        self.source.write_bytes(b"")
        self.run_import()
        rows = self.read_output()
        marker = "SYNTHETIC-PRIVATE-CONTENT-DO-NOT-PRINT"
        cases = [((), "provider", [None, 0, [], marker]),
                 ((), "session_id", [False, 1, [], {}, ""]),
                 ((), "native_session_id", [False, 1, [], {}, ""]),
                 ((), "source", [None, [], marker]),
                 (("source",), "format", [None, [], marker]),
                 (("source",), "sha256", [None, 0, [], "", "a" * 63, "a" * 65, "g" * 64, marker]),
                 ((), "source_records", [None, False, True, -1, 0.0, "0", [], {}])]
        for path, key, invalid_values in cases:
            for index in range(len(invalid_values) + 1):
                with self.subTest(path=path, field=key, case=index):
                    altered = json.loads(json.dumps(rows))
                    target = altered[0]
                    for part in path:
                        target = target[part]
                    if index == len(invalid_values):
                        del target[key]
                    else:
                        target[key] = invalid_values[index]
                    self.output.write_text("".join(json.dumps(row) + "\n" for row in altered))
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        code = main(["inspect", "--source", str(self.output)])
                    self.assertEqual(code, 2)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertEqual(json.loads(stderr.getvalue()), {"error": "invalid_normalized_header"})
                    self.assertNotIn(marker, stdout.getvalue() + stderr.getvalue())

    def test_inspect_accepts_normalized_empty_source(self):
        self.source.write_bytes(b"")
        result = self.run_import()
        header = self.read_output()[0]
        self.assertIsNone(header["session_id"])
        self.assertIsNone(header["native_session_id"])
        self.assertEqual(result["events"], 0)
        self.assertEqual(result["coverage"], "partial")
        self.assertEqual(result["session_diagnostics"], ["empty_source", "missing_native_session_id",
                                                       "missing_session_metadata", "missing_session_timestamp"])
        self.assertEqual(result, inspect(self.output))
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["inspect", "--source", str(self.output)])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(stdout.getvalue()), result)
        self.assertEqual(stderr.getvalue(), "")

    def test_cli_content_free_summary_and_errors(self):
        marker = "SYNTHETIC-PRIVATE-CONTENT-DO-NOT-PRINT"
        row = message(marker)
        row["payload"]["role"] = marker
        self.write_jsonl([row, {"type": marker, "payload": {"type": marker}}])
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["normalize", "--source", str(self.source), "--format", "codex-jsonl", "--output", str(self.output)])
        self.assertEqual(code, 3)
        self.assertNotIn(marker, stdout.getvalue() + stderr.getvalue())
        self.assertEqual(json.loads(stdout.getvalue())["roles"], {"other": 1})
        self.source.write_text('{"private":"' + marker)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["normalize", "--source", str(self.source), "--format", "codex-legacy-json", "--output", str(self.root / "bad")])
        self.assertEqual(code, 2)
        self.assertNotIn(marker, stdout.getvalue() + stderr.getvalue())


class ReaderTests(unittest.TestCase):
    def test_jsonl_is_lazy_and_does_not_cap_large_lines(self):
        class IterationOnly:
            consumed = 0
            def __iter__(self):
                for raw in (b'{}\n', json.dumps(message("x" * 2_000_000)).encode() + b'\n'):
                    self.consumed += 1
                    yield raw
            def read(self, *args):
                raise AssertionError("JSONL must not use read")
        stream = IterationOnly()
        reader = iter(Reader(stream, "codex-jsonl"))
        self.assertEqual(next(reader).value, {})
        self.assertEqual(stream.consumed, 1)
        self.assertEqual(len(next(reader).value["payload"]["content"][0]["text"]), 2_000_000)
        self.assertEqual(stream.consumed, 2)
        with self.assertRaises(StopIteration):
            next(reader)
