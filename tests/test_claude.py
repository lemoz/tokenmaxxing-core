"""Invented Claude-shaped inputs only; no native transcript values or paths."""

import base64
import contextlib
import copy
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tokenmaxxing.__main__ import main
from tokenmaxxing.claude import ClaudeSummary, header as claude_header
from tokenmaxxing.normalize import inspect, normalize
from tokenmaxxing.records import NormalizationError, Reader


def message(content="synthetic text", agent=None):
    row = {"type": "assistant", "sessionId": "synthetic:root/01", "isSidechain": agent is not None,
           "uuid": "synthetic-record", "parentUuid": "synthetic-record-parent",
           "timestamp": "2026-01-01T01:02:03Z", "message": {
               "role": "assistant", "content": content, "id": "synthetic-message",
               "type": "message", "model": "synthetic-model", "stop_reason": "tool_use",
               "stop_sequence": None, "usage": {"input_tokens": 7}, "status": "completed"}}
    if agent is not None:
        row["agentId"] = agent
    return row


def resolve(value, pointer):
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


class ClaudeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "synthetic.source"
        self.output = self.root / "normalized.jsonl"

    def write(self, rows):
        self.source.write_bytes(b"".join(json.dumps(row, ensure_ascii=False).encode() + b"\n" for row in rows))

    def run_import(self, output=None):
        return normalize(self.source, output or self.output, "claude-code-jsonl")

    def read(self, output=None):
        return [json.loads(line) for line in (output or self.output).read_text().splitlines()]

    def test_full_fidelity_and_resolvable_provenance(self):
        blocks = [{"type": "text", "text": "synthetic λ", "new/key": 1},
                  {"type": "thinking", "thinking": "synthetic thought", "signature": "synthetic-signature"},
                  {"type": "tool_use", "id": "synthetic-call/1", "name": "synthetic_tool",
                   "input": {"command": "inert; never executed", "nested": [1, {"x": False}]}, "extra": 1},
                  {"type": "tool_use", "id": "synthetic-call/2", "name": "synthetic_tool", "input": {}},
                  {"type": "tool_result", "tool_use_id": "synthetic-call/1", "is_error": False,
                   "content": [{"type": "text", "text": "synthetic opaque result", "extra": {"x": [None]}}]},
                  {"type": "tool_result", "tool_use_id": "unpaired-synthetic", "is_error": True,
                   "content": "opaque success-looking string"}]
        row = message(blocks)
        row.update({"sourceToolAssistantUUID": "synthetic-origin", "sourceToolUseID": "synthetic-source-tool",
                    "isMeta": True, "unknown": {"new/key": "retained"}})
        rows = [message("string stays a string"), row]
        self.write(rows)
        result = self.run_import()
        header, first, second, _ = self.read()
        self.assertEqual(header["provider"], "claude")
        self.assertEqual(header["adapter_version"], "claude-1")
        self.assertEqual(result["events"], 2)
        self.assertEqual(result["canonical_events"], 2)
        self.assertEqual(result["event_types"], {"message": 2})
        self.assertEqual(result["tool_interactions"], 4)
        self.assertEqual(result["tool_interaction_types"], {"tool_call": 2, "tool_result": 2})
        self.assertEqual(result["coverage"], "no_known_gaps")
        self.assertEqual(first["canonical"]["content"], "string stays a string")
        self.assertEqual(first["block_pointers"], [])
        offset = 0
        for number, (raw_bytes, raw, event) in enumerate(zip(self.source.read_bytes().splitlines(keepends=True), rows, [first, second]), 1):
            self.assertEqual(event["source_record"], raw)
            self.assertEqual(event["locator"], {"line": number, "byte_offset": offset,
                                               "byte_length": len(raw_bytes), "pointer": ""})
            offset += len(raw_bytes)
            self.assertEqual(event["canonical"], raw["message"])
            self.assertEqual(resolve(raw, event["timestamp_pointer"]), event["timestamp"])
            self.assertEqual(event["outcome"], "unknown")
            for field, pointer in event["field_pointers"].items():
                self.assertEqual(resolve(raw, pointer), event["canonical"][field])
            for field, pointer in event["metadata_pointers"].items():
                self.assertEqual(resolve(raw, pointer), event["observed_metadata"][field])
            for index, pointer in enumerate(event["block_pointers"]):
                self.assertEqual(resolve(raw, pointer), raw["message"]["content"][index])
        for field, evidence in header["metadata_evidence"].items():
            self.assertEqual(resolve(rows[evidence["locator"]["line"] - 1], evidence["field_pointer"]),
                             header["observed_metadata"][field])
        for item, index in zip(second["interactions"], [2, 3, 4, 5]):
            self.assertEqual(item["block_index"], index)
            self.assertEqual(resolve(row, item["block_pointer"]), blocks[index])
            self.assertEqual(item["outcome"], "unknown")
            for field, pointer in item["field_pointers"].items():
                self.assertEqual(resolve(row, pointer), item["canonical"][field])
            for output_index, pointer in enumerate(item["output_block_pointers"]):
                self.assertEqual(resolve(row, pointer), item["canonical"]["output"][output_index])
        self.assertEqual(second["interactions"][0]["canonical"], {
            "call_id": "synthetic-call/1", "name": "synthetic_tool", "input": blocks[2]["input"]})
        self.assertEqual(result, inspect(self.output))

    def test_main_subagent_context_and_collision_resistant_ids(self):
        self.write([message()])
        self.run_import()
        parent = self.read()[0]
        self.assertEqual(parent["session_id"], "claude:synthetic:root/01")
        self.assertIsNone(parent["parent_session_id"])
        self.assertIsNone(parent["native_agent_id"])
        identities = set()
        for index, (root, agent) in enumerate([("synthetic:root/01", "a:b"), ("synthetic:a:b", "c"),
                                               ("synthetic:a", "b:c"), ('synthetic:["a",', '"b"]\x00λ')]):
            row = message(agent=agent)
            row["sessionId"] = root
            self.write([row])
            output = self.root / str(index)
            self.run_import(output)
            child = self.read(output)[0]
            expected = hashlib.sha256(json.dumps([root, agent], ensure_ascii=True,
                                                 sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            self.assertEqual(child["session_id"], "claude-subagent:" + expected)
            self.assertEqual(child["native_session_id"], root)
            self.assertEqual(child["native_agent_id"], agent)
            self.assertEqual(child["session_kind"], "subagent")
            self.assertEqual(child["parent_session_id"], "claude:" + root)
            self.assertEqual(child["parent_session_relation"], "observed_main_session_context")
            self.assertNotEqual(child["parent_session_id"], row["parentUuid"])
            self.assertEqual(inspect(output)["events"], 1)
            identities.add(child["session_id"])
        self.assertEqual(len(identities), 4)

    def test_repeat_copy_revision_and_repeated_messages(self):
        self.write([message(agent="synthetic-child"), message(agent="synthetic-child")])
        before = self.source.read_bytes()
        summary = self.run_import()
        first = self.read()
        repeated = self.root / "repeat"
        self.assertEqual(summary, self.run_import(repeated))
        self.assertEqual(self.output.read_bytes(), repeated.read_bytes())
        copied = self.root / "main-looking-name.jsonl"
        copied.write_bytes(before)
        other = self.root / "copied"
        normalize(copied, other, "claude-code-jsonl")
        self.assertEqual(first[1:], self.read(other)[1:])
        self.assertEqual(first[0]["session_id"], self.read(other)[0]["session_id"])
        self.assertEqual(first[0]["native_identity_evidence"], self.read(other)[0]["native_identity_evidence"])
        self.assertNotEqual(first[1]["event_id"], first[2]["event_id"])
        self.assertEqual(before, self.source.read_bytes())
        self.write([message("synthetic changed content", agent="synthetic-child"), message(agent="synthetic-child")])
        edited = self.root / "edited"
        self.run_import(edited)
        self.assertEqual(first[0]["session_id"], self.read(edited)[0]["session_id"])
        self.assertNotEqual(first[1]["event_id"], self.read(edited)[1]["event_id"])
        self.source.write_bytes(before + b'{}\n')
        revised = self.root / "revision"
        self.run_import(revised)
        self.assertNotEqual(first[1]["source_revision_id"], self.read(revised)[1]["source_revision_id"])
        # Missing child metadata in the appended object also makes identity unresolved.
        self.assertIsNone(self.read(revised)[0]["session_id"])

    def test_alias_is_not_identity_and_parent_uuid_is_not_session_parent(self):
        row = message()
        row["session_id"] = "synthetic-unsupported-alias"
        self.write([row])
        self.run_import()
        header = self.read()[0]
        self.assertEqual(header["native_session_id"], row["sessionId"])
        self.assertIsNone(header["parent_session_id"])
        self.assertIn("unsupported_session_id_alias", header["diagnostics"])
        del row["sessionId"]
        self.write([row])
        alias_only = self.root / "agent-synthetic-alias.jsonl"
        self.run_import(alias_only)
        self.assertIsNone(self.read(alias_only)[0]["native_session_id"])
        self.assertIsNone(self.read(alias_only)[0]["session_id"])

    def test_missing_ambiguous_identity_and_metadata_omissions(self):
        cases = []
        for key in ("sessionId", "isSidechain"):
            row = message()
            del row[key]
            cases.append([row])
        for updates in ({"isSidechain": True}, {"isSidechain": "false"}, {"agentId": ""},
                        {"sessionId": ""}, {"agentId": None}, {"isSidechain": None}):
            cases.append([{**message(), **updates}])
        child = message(agent="synthetic-child")
        cases.extend([[child, {"type": "system", "sessionId": child["sessionId"]}],
                      [{"type": "system", "sessionId": "synthetic-root", "isSidechain": False}], []])
        for index, rows in enumerate(cases):
            with self.subTest(case=index):
                self.write(rows)
                output = self.root / str(index)
                result = self.run_import(output)
                header = self.read(output)[0]
                self.assertIsNone(header["session_id"])
                self.assertIsNone(header["parent_session_id"])
                self.assertEqual(header["session_kind"], "unresolved")
                self.assertIn("ambiguous_thread_identity", result["session_diagnostics"])
                self.assertEqual(result, inspect(output))
        # Omitted sidechain on root metadata is an observed allowed shape.
        self.write([message(), {"type": "system", "sessionId": "synthetic:root/01"}])
        self.run_import()
        self.assertEqual(self.read()[0]["session_kind"], "main")

    def test_conflicting_ids_and_sidechain_fail_with_fixed_errors(self):
        cases = [([message(), {**message(), "sessionId": "synthetic-other"}], "conflicting_native_session_ids"),
                 ([message(agent="a"), message(agent="b")], "conflicting_native_agent_ids"),
                 ([message(), message(agent="a")], "conflicting_claude_sidechain_metadata"),
                 ([{**message(), "agentId": "synthetic-agent"}], "conflicting_claude_thread_metadata")]
        for rows, error in cases:
            self.write(rows)
            with self.assertRaisesRegex(NormalizationError, "^" + error + "$"):
                self.run_import()
            self.assertFalse(self.output.exists())
            self.assertEqual(list(self.root.glob(".tokenmaxxing-*")), [])

    def test_unknown_blocks_types_compaction_and_redaction_are_retained(self):
        blocks = [{"type": "image", "source": {"data": "synthetic-only"}}, {"type": "document"},
                  {"type": "redacted_thinking", "data": "synthetic-redacted"},
                  {"type": "new_synthetic_type", "encrypted_content": "synthetic-encrypted"},
                  {"type": ["invalid-type"]}, "invalid-block"]
        rows = [message(blocks), {"type": "summary", "summary": "synthetic compressed evidence"},
                {"type": "system", "subtype": "compact_boundary", "compactMetadata": {}},
                {"type": "progress", "data": message()}, {"type": "novel-synthetic-type"},
                {**message("synthetic compaction"), "isCompactSummary": True}]
        self.write(rows)
        result = self.run_import()
        events = self.read()[1:-1]
        self.assertEqual(result["canonical_events"], 1)
        self.assertEqual(result["diagnostics"]["compacted_evidence"], 3)
        self.assertEqual(result["diagnostics"]["unsupported_content_block"], 1)
        self.assertEqual(result["diagnostics"]["redacted_evidence"], 1)
        self.assertEqual(result["diagnostics"]["encrypted_evidence"], 1)
        self.assertEqual(result["tool_interactions"], 0)
        for raw, event in zip(rows, events):
            self.assertEqual(event["source_record"], raw)
            self.assertEqual(event["outcome"], "unknown")
        self.assertEqual(events[3]["kind"], "unsupported")
        self.assertIn("projection_not_reconciled", events[3]["diagnostics"])
        self.assertEqual(result, inspect(self.output))

    def test_metadata_types_are_retained_without_canonical_messages(self):
        types = ["system", "mode", "permission-mode", "file-history-snapshot", "atis-latch", "attachment",
                 "last-prompt", "ai-title", "queue-operation"]
        rows = [message()] + [{"type": top, "opaque": [1, {"x": True}]} for top in types]
        self.write(rows)
        result = self.run_import()
        self.assertEqual(result["canonical_events"], 1)
        self.assertEqual(result["event_types"]["source_metadata"], len(types))
        for row, event in zip(rows[1:], self.read()[2:-1]):
            self.assertEqual(event["source_record"], row)
            self.assertEqual(event["canonical"], {})
            self.assertIn("missing_timestamp", event["diagnostics"])

    def test_unexpected_shapes_and_missing_tool_fields_preserve_values(self):
        blocks = [{"type": "tool_use"}, {"type": "tool_result"},
                  {"type": "tool_use", "id": 0, "name": {}, "input": "opaque input"},
                  {"type": "tool_result", "tool_use_id": [], "is_error": "false",
                   "content": {"nested": [1, 2]}},
                  {"type": "tool_result", "tool_use_id": "synthetic", "is_error": False,
                   "content": [{"type": "image"}, {"type": "redacted_thinking"}]}]
        rows = [message(blocks), message({"unexpected": True}), {"type": "user", "message": []},
                {"type": "assistant", "message": {}}, {"type": []}, [], None,
                {**message(), "timestamp": 5}]
        self.write(rows)
        result = self.run_import()
        self.assertEqual(result["tool_interactions"], 5)
        self.assertEqual(result["coverage"], "partial")
        for code in ("missing_tool_call_id", "missing_tool_name", "missing_tool_input", "missing_tool_output",
                     "missing_tool_is_error", "invalid_tool_identity", "unsupported_tool_input_shape",
                     "unsupported_tool_output_shape", "unsupported_tool_is_error", "unsupported_timestamp_shape"):
            self.assertIn(code, result["diagnostics"])
        events = self.read()[1:-1]
        self.assertEqual(events[0]["interactions"][3]["canonical"]["output"], {"nested": [1, 2]})
        self.assertEqual(events[1]["canonical"]["content"], {"unexpected": True})
        self.assertEqual(events[-1]["timestamp"], 5)
        self.assertEqual(result, inspect(self.output))

    def test_invalid_json_duplicate_keys_nonfinite_utf8_and_unterminated(self):
        raw_lines = [b'{"private":"synthetic"\n', b'\xff\n', b'\n', b'{"x":1,"x":2}\n',
                     b'{"message":{"id":"a","id":"b"}}\n', b'{"x":NaN}\n', b'{"x":Infinity}\n']
        self.source.write_bytes(b"".join(raw_lines) + json.dumps(message()).encode())
        result = self.run_import()
        self.assertEqual(result["events"], 8)
        self.assertEqual(result["diagnostics"]["invalid_json_record"], 7)
        self.assertEqual(result["diagnostics"]["unterminated_final_line"], 1)
        for raw, event in zip(raw_lines, self.read()[1:]):
            self.assertEqual(base64.b64decode(event["source_record"]["raw_bytes_base64"]), raw)
            self.assertEqual(event["kind"], "unsupported")
        self.assertEqual(result, inspect(self.output))

    def test_cli_normalize_inspect_and_fixed_errors_are_content_free(self):
        marker = "SYNTHETIC-PRIVATE-MARKER"
        row = message([{"type": marker, "text": marker}])
        row["message"]["role"] = marker
        row["message"]["type"] = marker
        self.write([row, {"type": marker}])
        summaries = []
        for arguments in (["normalize", "--source", str(self.source), "--format", "claude-code-jsonl", "--output", str(self.output)],
                          ["inspect", "--source", str(self.output)]):
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(arguments)
            self.assertEqual(code, 3)
            self.assertNotIn(marker, stdout.getvalue() + stderr.getvalue())
            self.assertEqual(stderr.getvalue(), "")
            summaries.append(json.loads(stdout.getvalue()))
        self.assertEqual(summaries[0], summaries[1])
        self.assertEqual(summaries[0]["roles"], {"other": 1})
        self.write([message(), {**message(), "sessionId": marker}])
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["normalize", "--source", str(self.source), "--format", "claude-code-jsonl", "--output", str(self.root / "fail")])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(stderr.getvalue()), {"error": "conflicting_native_session_ids"})

    def test_private_fresh_outputs_and_source_change_rejection(self):
        self.write([message()])
        before = self.source.read_bytes()
        self.run_import()
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(NormalizationError):
            self.run_import()
        for index, link_type in enumerate(("symbolic", "hard", "dangling")):
            target = self.root / str(index)
            if link_type == "hard":
                os.link(self.source, target)
            else:
                target.symlink_to(self.source if link_type == "symbolic" else self.root / "absent")
            with self.assertRaises(NormalizationError):
                self.run_import(target)
        shared = self.root / "shared"
        shared.mkdir(mode=0o755)
        shared.chmod(0o755)
        with self.assertRaises(NormalizationError):
            self.run_import(shared / "out")
        self.assertEqual(self.source.read_bytes(), before)
        module = importlib.import_module("tokenmaxxing.normalize")
        original = module._header
        def change(*args):
            value = original(*args)
            with self.source.open("ab") as stream:
                stream.write(b'{}\n')
            return value
        with patch.object(module, "_header", change):
            with self.assertRaisesRegex(NormalizationError, "source_changed"):
                self.run_import(self.root / "changed")
        self.assertFalse((self.root / "changed").exists())
        self.assertEqual(list(self.root.glob(".tokenmaxxing-*")), [])

    def test_inspector_rejects_cross_adapter_and_interaction_envelope_tampering(self):
        self.write([message([{"type": "tool_use", "id": "synthetic", "name": "synthetic", "input": {}}])])
        self.run_import()
        rows = self.read()
        mutations = [(0, "adapter_version", "codex-1"), (0, "provider", "codex"),
                     (0, "session_id", "claude:wrong"), (0, "native_agent_id", []),
                     (0, "parent_session_id", "claude:invented"), (0, "session_kind", "subagent"),
                     (1, "interactions", None), (1, "interactions", []),
                     (1, "canonical", []), (1, "outcome", "success")]
        for row, key, value in mutations:
            altered = copy.deepcopy(rows)
            altered[row][key] = value
            self.output.write_text("".join(json.dumps(item) + "\n" for item in altered))
            with self.subTest(field=key), self.assertRaises(NormalizationError):
                inspect(self.output)

        for key, value in (("block_index", -1), ("block_index", True), ("block_pointer", "/wrong"),
                           ("outcome", "success"), ("kind", "message"), ("kind", "tool_result")):
            altered = copy.deepcopy(rows)
            altered[1]["interactions"][0][key] = value
            self.output.write_text("".join(json.dumps(item) + "\n" for item in altered))
            with self.subTest(field=key), self.assertRaises(NormalizationError):
                inspect(self.output)

    def test_operator_validator_on_synthetic_main_and_subagent(self):
        marker = "SYNTHETIC-VALIDATOR-CONTENT"
        blocks = [{"type": "tool_use", "id": marker, "name": marker, "input": {"text": marker}},
                  {"type": "tool_result", "tool_use_id": marker, "is_error": False,
                   "content": [{"type": "text", "text": marker}, {"type": "image", "data": marker}]}]
        for index, agent in enumerate((None, "synthetic-validator-agent")):
            self.write([message(marker, agent=agent), message(blocks, agent=agent)])
            directory = self.root / ("proof-" + str(index))
            result = subprocess.run([sys.executable, "scripts/validate_source.py", "--source", str(self.source),
                                     "--format", "claude-code-jsonl", "--output-dir", str(directory)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn(marker, result.stdout + result.stderr)
            report = (directory / "validation-report.json").read_text()
            self.assertNotIn(marker, report)
            proof = json.loads(report)
            self.assertTrue(proof["stable_repeat"])
            self.assertTrue(proof["unchanged_copy_identity"])
            self.assertTrue(proof["source_unchanged"])
            self.assertEqual(proof["source_to_output_assertions"]["tool_call_interactions"], 1)
            self.assertEqual(proof["source_to_output_assertions"]["tool_result_interactions"], 1)


class ClaudeStreamingTests(unittest.TestCase):
    def test_reader_and_header_stream_without_whole_source_reads_or_line_caps(self):
        class IterationOnly:
            consumed = 0
            def __iter__(self):
                for raw in (json.dumps(message()).encode() + b"\n",
                            json.dumps(message("x" * 2_000_000)).encode() + b"\n"):
                    self.consumed += 1
                    yield raw
            def read(self, *args):
                raise AssertionError("Claude JSONL must not use read")
        stream = IterationOnly()
        records = iter(Reader(stream, "claude-code-jsonl"))
        self.assertEqual(next(records).value["type"], "assistant")
        self.assertEqual(stream.consumed, 1)
        self.assertEqual(len(next(records).value["message"]["content"]), 2_000_000)
        self.assertEqual(stream.consumed, 2)
        with self.assertRaises(StopIteration):
            next(records)
        class Info:
            st_size = 0
            st_mtime_ns = 0
        selected = claude_header(Reader(IterationOnly(), "claude-code-jsonl"), "synthetic", Info())
        self.assertEqual(selected["source_records"], 2)
        self.assertNotIn("content", selected["observed_metadata"])

    def test_summary_vocabulary_is_bounded(self):
        from tokenmaxxing.claude import event
        from tokenmaxxing.records import Record
        summary = ClaudeSummary([])
        for index in range(1000):
            row = message([{"type": "synthetic-" + str(index)}])
            row["message"]["role"] = "synthetic-" + str(index)
            row["message"]["type"] = "synthetic-" + str(index)
            summary.add(event(Record({"pointer": "", "line": index + 1}, row), "claude-code-jsonl", None, "synthetic", index))
        result = summary.result()
        self.assertEqual(result["roles"], {"other": 1000})
        self.assertEqual(result["content_block_types"], {"other": 1000})
        self.assertEqual(result["source_subtypes"], {"other": 1000})
