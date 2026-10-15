"""Synthetic metadata only; subprocess checks use the actual catalog CLI."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tokenmaxxing.discovery import build_catalog, catalog, map_metadata, repository_url
from tokenmaxxing.private_paths import REPOSITORY, Scope
from tokenmaxxing.records import NormalizationError, encode


REPO = "https://example.test/team/lantern.git"


def row(provider="codex", native="synthetic-session", **fields):
    value = {"provider": provider, "source_session_id": native,
             "origin": "synthetic-machine", "path": "/synthetic/source-" + provider,
             "bytes": 100, "format_candidate": "synthetic-metadata",
             "source_timestamp": "2026-01-02T03:00:00Z", "git": {"repository_url": REPO}}
    if provider == "claude":
        value["metadata"] = {"sessionId": native, "isSidechain": False}
    value.update(fields)
    return value


class SyntheticFixtures:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-project-catalog-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def write(self, rows, name="metadata.jsonl"):
        path = self.root / name
        path.write_text("".join(encode(r) + "\n" for r in rows), encoding="ascii")
        return path

    def build(self, rows, **kwargs):
        return self.map_bytes(encode_rows(rows), **kwargs)

    def map_bytes(self, raw, **kwargs):
        return map_metadata([("/synthetic/metadata.jsonl", io.BytesIO(raw))], **kwargs)

    def aliases(self, rules):
        return {"schema_version": 1, "rules": rules}

    def rule(self, match, project="lantern", label="Synthetic Lantern"):
        return {"match": match, "project": project, "label": label}


def encode_rows(rows):
    return "".join(encode(r) + "\n" for r in rows).encode("ascii")


class MappingTests(SyntheticFixtures, unittest.TestCase):

    def test_multi_provider_repo_and_explicit_transport_aliases(self):
        other = "ssh://example.test/team/lantern.git"
        aliases = self.aliases([self.rule({"repository_url": REPO}), self.rule({"repository_url": other})])
        report = self.build([row(), row("claude"), row("hermes", git={"repository_url": other})], aliases=aliases)
        self.assertEqual(report["summary"]["project_candidates"], 1)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["providers"], ["claude", "codex", "hermes"])
        self.assertEqual(candidate["counts"]["distinct_session_contexts"], 3)
        self.assertEqual(candidate["confidence"], "operator_asserted")
        self.assertEqual(len(candidate["source_references"]), 3)
        self.assertEqual(len(report["alias_rules"]), 2)
        self.assertIsNone(report["alias_source"])

    def test_conservative_url_normalization_and_redaction_everywhere(self):
        secret = "https://user:synthetic-password@EXAMPLE.test:443/team/lantern.git?synthetic-token#synthetic-fragment"
        report = self.build([row(git={"repository_url": secret}), row("hermes")])
        self.assertEqual(report["summary"]["project_candidates"], 1)
        serialized = encode(report)
        for text in ("synthetic-password", "synthetic-token", "synthetic-fragment", "user:"):
            self.assertNotIn(text, serialized)
        self.assertEqual(repository_url(secret), REPO)
        for different in ("http://example.test/team/lantern.git", "https://example.test/team/lantern",
                          "https://example.test/Team/lantern.git", REPO + "/", "ssh://example.test/team/lantern.git"):
            self.assertNotEqual(repository_url(different), REPO)
        for invalid in ("git@example.test:team/lantern.git", "file:///synthetic/repo", "https://example.test:bad/repo"):
            report = self.build([row(git={"repository_url": invalid})])
            self.assertEqual(report["summary"]["accounting"]["unassigned"], 1)
            self.assertIn("invalid_repository_url", report["rows"][0]["diagnostics"])

    def test_different_directories_origins_and_shared_roots(self):
        report = self.build([row(git=None, cwd="/synthetic/home/alpha"),
                             row(git=None, cwd="/synthetic/home/beta"),
                             row(git=None, cwd="/synthetic/home/alpha", origin="second-machine"),
                             row(git=None, cwd="/synthetic/home"), row(git=None, cwd="/")],
                            shared_roots=["/synthetic/home"])
        self.assertEqual(report["summary"]["accounting"], {"assigned": 3, "unassigned": 2, "sidecar": 0, "diagnostic": 0})
        self.assertEqual(len(report["candidates"]), 3)
        self.assertTrue(all(c["confidence"] == "tentative" for c in report["candidates"]))
        report = self.build([row(git=None, cwd="/synthetic/a", metadata={"cwd": "/synthetic/b"})])
        self.assertIn("conflicting_directories", report["rows"][0]["diagnostics"])
        self.assertEqual(report["summary"]["project_candidates"], 0)

    def test_shared_directory_cannot_be_restored_by_alias(self):
        aliases = self.aliases([self.rule({"cwd": "/synthetic/home", "origin": "synthetic-machine"})])
        report = self.build([row(git=None, cwd="/synthetic/home")], shared_roots=["/synthetic/home"], aliases=aliases)
        self.assertEqual(report["summary"]["project_candidates"], 0)

    def test_copies_revisions_and_retries_keep_identity_counts(self):
        first = row()
        copied = row(path="/synthetic/copy")
        changed = row(bytes=200, source_timestamp="2026-02-03T00:00:00Z")
        report = self.build([first, copied, changed, first])
        counts = report["summary"]
        self.assertEqual(counts["rows"], 4)
        self.assertEqual(counts["input_files"], 1)
        self.assertEqual(counts["distinct_session_contexts"], 1)
        self.assertEqual(counts["distinct_native_ids"], 1)
        self.assertEqual(counts["distinct_source_references"], 2)
        self.assertEqual(len(report["candidates"][0]["source_references"]), 3)

    def test_hermes_typed_native_ids_across_databases(self):
        report = self.build([row("hermes", 7, path="/synthetic/one.db"),
                             row("hermes", 7, path="/synthetic/two.db"), row("hermes", "7")])
        self.assertEqual(report["summary"]["distinct_session_contexts"], 2)
        self.assertEqual(report["summary"]["distinct_native_ids"], 2)
        self.assertIn("does not establish equal content", report["semantics"]["hermes_identity"])

    def test_claude_main_child_unknown_children_and_sidecar(self):
        rows = [row("claude"),
                row("claude", metadata={"sessionId": "synthetic-session", "agentId": "child", "isSidechain": True}),
                row("claude", metadata={"sessionId": "synthetic-session", "agentId": "child", "isSidechain": True}, path="/synthetic/copy"),
                row("claude", metadata={"sessionId": "synthetic-session", "isSidechain": True}, sidechain_path="/synthetic/agent-a"),
                row("claude", metadata={"sessionId": "synthetic-session", "isSidechain": True}, sidechain_path="/synthetic/agent-b"),
                row("claude", metadata={"sessionId": "synthetic-session"}),
                row("claude", sidecar_not_independent_session=True, title="Synthetic title lead")]
        report = self.build(rows)
        counts = report["summary"]
        self.assertEqual(counts["distinct_native_ids"], 1)
        self.assertEqual(counts["distinct_session_contexts"], 2)
        self.assertEqual(counts["unresolved_context_rows"], 3)
        self.assertEqual(counts["session_rows"], 6)
        self.assertEqual(counts["accounting"]["sidecar"], 1)
        self.assertIsNone(report["rows"][-1]["context"])
        self.assertIsNone(report["rows"][-1]["candidate_key"])
        self.assertEqual(report["rows"][-1]["title_leads"][0]["value"], "Synthetic title lead")

    def test_boolean_sidechain_path_hint_is_not_identity(self):
        report = self.build([row("claude", sidechain_path=False),
                             row("claude", sidechain_path=True,
                                 metadata={"sessionId": "synthetic-session", "agentId": "child", "isSidechain": True}),
                             row("claude", None, sidechain_path=True, metadata={}),
                             row("claude", sidechain_path=True, sidecar_not_independent_session=True)])
        self.assertEqual(report["summary"]["accounting"], {"assigned": 3, "unassigned": 0, "sidecar": 1, "diagnostic": 0})
        self.assertEqual(report["summary"]["distinct_session_contexts"], 2)
        self.assertEqual(report["summary"]["unresolved_context_rows"], 1)

    def test_no_identity_from_filename_alias_or_sidechain_path(self):
        report = self.build([row(native=None, metadata={"session_id": "unsupported"}),
                             row("claude", None, path="/synthetic/session-filename.jsonl", metadata={},
                                 agent_id="unverified-child", sidechain_path="/synthetic/agent-unverified-child"),
                             row("hermes", None)])
        self.assertEqual(report["summary"]["distinct_native_ids"], 0)
        self.assertEqual(report["summary"]["rows_without_native_id"], 3)
        self.assertEqual(report["summary"]["unresolved_context_rows"], 3)

    def test_empty_id_and_zero_integer_id_are_distinct(self):
        report = self.build([row(native=""), row("hermes", 0)])
        self.assertEqual(report["summary"]["rows_without_native_id"], 1)
        self.assertEqual(report["summary"]["distinct_session_contexts"], 1)
        self.assertEqual(report["rows"][1]["context"]["native_session_id"], 0)

    def test_alias_url_credentials_are_removed_from_rule_provenance(self):
        aliases = self.aliases([self.rule({"repository_url": "https://user:synthetic-secret@example.test/team/lantern.git?secret#fragment"})])
        report = self.build([row()], aliases=aliases)
        self.assertEqual(report["summary"]["project_candidates"], 1)
        for private in ("user:", "synthetic-secret", "?secret", "#fragment"):
            self.assertNotIn(private, encode(report))

    def test_conflicting_claude_observations_do_not_resolve(self):
        report = self.build([row("claude", metadata={"sessionId": "another", "isSidechain": False}),
                             row("claude", sidechain_observed=True),
                             row("claude", metadata={"agentId": "one", "isSidechain": True}, agent_id="two"),
                             row("claude", metadata={"agentId": "one", "isSidechain": False})])
        self.assertEqual(report["summary"]["distinct_session_contexts"], 0)
        self.assertEqual(report["summary"]["unresolved_context_rows"], 4)
        self.assertIsNone(report["rows"][0]["context"]["native_session_id"])
        self.assertEqual(report["rows"][0]["identity_observations"]["metadata.sessionId"], "another")

    def test_timestamp_precision_offsets_missing_and_epoch(self):
        report = self.build([row(source_timestamp="2026-01-02"),
                             row(source_timestamp="2026-01-03T01:00:00+01:00"),
                             row(source_timestamp=None), row(source_timestamp="not a date"),
                             row(source_timestamp="2026-01-01T00:00:00"),
                             row("hermes", source_timestamp=None, metadata={"started_at": 0, "ended_at": 1.5})])
        span = report["candidates"][0]["date_span"]
        self.assertEqual(span["first"], "1970-01-01T00:00:00.000000Z")
        self.assertEqual(span["last"], "2026-01-03T00:00:00.000000Z")
        self.assertEqual(span["precisions"], ["day", "instant"])
        self.assertEqual(report["summary"]["rows_without_parseable_time"], 3)
        before = report["rows"][0]["context"]["key"]
        after = self.build([row(source_timestamp="2025-01-01")])["rows"][0]["context"]["key"]
        self.assertEqual(before, after)

    def test_malformed_json_types_and_versions_exact_accounting(self):
        raw = encode_rows([row(), [], None, row(provider="unknown"), row(schema_version=2),
                           row(metadata_version=True), row(metadata=[]), row(bytes=True),
                           row(source_session_id=[]), row(metadata={"isSidechain": 1}),
                           row(source_timestamp={"invalid": True})])
        raw += b'\n{"x":1,"x":2}\n{"x":NaN}\n\xff\n{"private":"SYNTHETIC_SECRET"\n'
        report = self.map_bytes(raw)
        self.assertEqual(report["summary"]["rows"], 16)
        self.assertEqual(report["summary"]["accounting"]["diagnostic"], 15)
        self.assertEqual(sum(report["summary"]["accounting"].values()), 16)
        self.assertNotIn("SYNTHETIC_SECRET", encode(report))
        offset = 0
        for number, (line, observed) in enumerate(zip(raw.splitlines(keepends=True), report["rows"]), 1):
            self.assertEqual(observed["locator"], {"input": "/synthetic/metadata.jsonl", "line": number,
                                                   "byte_offset": offset, "byte_length": len(line)})
            offset += len(line)
        self.assertEqual(report["inputs"][0]["bytes"], offset)

    def test_unknown_fields_and_raw_content_not_rendered(self):
        report = self.build([row(metadata={"novel": {"content": "SYNTHETIC_RAW"}},
                                 instructions="SYNTHETIC_RAW", message="SYNTHETIC_RAW",
                                 inspection={"messages": ["SYNTHETIC_RAW"]}, issue="SYNTHETIC_RAW")])
        self.assertNotIn("SYNTHETIC_RAW", encode(report))
        self.assertIn("unmodeled_fields_omitted", report["rows"][0]["diagnostics"])
        self.assertIn("input_issue_reported", report["rows"][0]["diagnostics"])

    def test_conflicting_alias_selectors_labels_and_cross_evidence(self):
        cases = [([self.rule({"repository_url": REPO}), self.rule({"repository_url": REPO}, "other")], {}),
                 ([self.rule({"repository_url": REPO}), self.rule({"repository_url": REPO}, label="Other label")], {}),
                 ([self.rule({"repository_url": REPO}),
                   self.rule({"cwd": "/synthetic/work", "origin": "synthetic-machine"}, "other")], {"cwd": "/synthetic/work"})]
        for rules, fields in cases:
            with self.subTest(rules=rules):
                report = self.build([row(**fields)], aliases=self.aliases(rules))
                self.assertEqual(report["summary"]["accounting"]["unassigned"], 1)
                self.assertIn("alias_conflict", report["rows"][0]["diagnostics"])
                self.assertTrue(report["rows"][0]["grouping_evidence"]["alias_matches"])
                reverse = self.build([row(**fields)], aliases=self.aliases(list(reversed(rules))))
                self.assertEqual(report["rows"], reverse["rows"])

    def test_invalid_alias_mapping_is_fixed_error(self):
        for value in ({"schema_version": 2, "rules": []}, {"schema_version": 1, "rules": ["private-marker"]},
                      {"schema_version": 1, "rules": [{"match": {}, "project": "x", "label": "x"}]}):
            with self.assertRaisesRegex(NormalizationError, "^invalid_alias_mapping$"):
                self.build([row()], aliases=value)

    def test_context_spanning_projects_diagnosed_without_forced_merge(self):
        report = self.build([row(), row(git={"repository_url": "https://example.test/other/repo.git"})])
        self.assertEqual(report["summary"]["distinct_session_contexts"], 1)
        self.assertEqual(report["summary"]["project_candidates"], 2)
        self.assertTrue(all("context_seen_in_multiple_candidates" in r["diagnostics"] for r in report["rows"]))

    def test_deterministic_file_order_and_unterminated_final_line(self):
        first, second = encode_rows([row()]), encode_rows([row("hermes")]).rstrip(b"\n")
        a = map_metadata([("a", io.BytesIO(first)), ("b", io.BytesIO(second))])
        b = map_metadata([("b", io.BytesIO(second)), ("a", io.BytesIO(first))])
        self.assertEqual(encode(a), encode(b))
        self.assertEqual(a["summary"]["rows"], 2)

    def test_more_than_pagination_size_no_row_cap(self):
        report = self.build([row(native="synthetic-" + str(i)) for i in range(513)])
        self.assertEqual(report["summary"]["rows"], 513)
        self.assertEqual(report["summary"]["distinct_session_contexts"], 513)

    def test_empty_input_and_empty_file(self):
        with self.assertRaisesRegex(NormalizationError, "metadata_input_required"):
            build_catalog([])
        report = self.build([])
        self.assertEqual(report["summary"]["rows"], 0)
        self.assertEqual(report["summary"]["coverage"], "partial")

    def test_metadata_stream_is_consumed_once_without_bulk_reads(self):
        class StreamingOnly(io.BytesIO):
            def read(self, *args):
                raise AssertionError("Metadata must be line-streamed")

        report = map_metadata([("synthetic", StreamingOnly(encode_rows([row(), row("hermes")])))])
        self.assertEqual(report["summary"]["rows"], 2)

    def test_duplicate_stream_references_fail_explicitly(self):
        with self.assertRaisesRegex(NormalizationError, "invalid_or_duplicate_metadata_reference"):
            map_metadata([("same", io.BytesIO(b"")), ("same", io.BytesIO(b""))])


class FileDiscoveryTests(SyntheticFixtures, unittest.TestCase):

    def test_explicit_null_alias_file_never_published(self):
        source = self.write([row()])
        aliases = self.root / "aliases.json"
        aliases.write_text("null", encoding="utf-8")
        aliases.chmod(0o600)
        output = self.root / "catalog.json"
        result = self.cli("--metadata", source, "--aliases", aliases, "--output", output)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_alias_mapping"})
        self.assertFalse(output.exists())

    def test_mixed_empty_and_nonempty_inputs_remain_partial(self):
        source = self.write([row()])
        empty = self.write([], "empty.jsonl")
        output = self.root / "catalog.json"
        result = self.cli("--metadata", source, "--metadata", empty, "--output", output)
        self.assertEqual(result.returncode, 3)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["coverage"], "partial")
        self.assertEqual(summary["rows"], 1)
        self.assertEqual(summary["input_files"], 2)
        self.assertEqual(summary["accounting"], {"assigned": 1, "unassigned": 0, "sidecar": 0, "diagnostic": 0})
        self.assertEqual(json.loads(output.read_text())["summary"], summary)

    def test_input_links_and_directories_refused(self):
        source = self.write([row()])
        link = self.root / "linked"
        link.symlink_to(source)
        hardlink = self.root / "hardlinked"
        os.link(source, hardlink)
        for path in (link, hardlink, self.root):
            with self.subTest(path=path), self.assertRaises(NormalizationError):
                build_catalog([path])

    def test_changed_source_never_published(self):
        source = self.write([row()])
        output = self.root / "catalog.json"
        with patch.object(Scope, "verify", side_effect=NormalizationError("source_changed_during_import")):
            with self.assertRaisesRegex(NormalizationError, "source_changed_during_import"):
                catalog([source], output)
        self.assertFalse(output.exists())

    def test_output_permissions_no_overwrite_links_and_source_boundary(self):
        source = self.write([row()])
        output = self.root / "catalog.json"
        result = catalog([source], output)
        self.assertEqual(result["project_candidates"], 1)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        for target in (output, source):
            with self.assertRaisesRegex(NormalizationError, "output_already_exists"):
                catalog([source], target)
        for name, dangling in (("link", False), ("dangling", True)):
            target = self.root / name
            target.symlink_to(self.root / "missing" if dangling else output)
            with self.assertRaisesRegex(NormalizationError, "output_already_exists"):
                catalog([source], target)
        with self.assertRaisesRegex(NormalizationError, "outside_repository"):
            catalog([source], REPOSITORY / "must-not-create-catalog.json")
        shared = self.root / "public"
        shared.mkdir(mode=0o755)
        shared.chmod(0o755)
        with self.assertRaisesRegex(NormalizationError, "private_directory_0700_required"):
            catalog([source], shared / "catalog.json")
        directory_link = self.root / "directory-link"
        directory_link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(NormalizationError, "symlink_not_allowed"):
            catalog([source], directory_link / "new.json")
        self.assertEqual(list(self.root.glob(".tokenmaxxing-catalog-*")), [])

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "tokenmaxxing", "catalog", *map(str, args)],
                              cwd=REPOSITORY, capture_output=True, text=True, timeout=30)

    def test_actual_cli_success_partial_and_fixed_error(self):
        source = self.write([row(), row("claude"), row("hermes")])
        output = self.root / "catalog.json"
        result = self.cli("--metadata", source, "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        summary = json.loads(result.stdout)
        self.assertEqual(summary["distinct_session_contexts"], 3)
        self.assertEqual(summary["project_candidates"], 1)
        self.assertEqual(json.loads(output.read_text())["summary"], summary)
        for private in (str(source), REPO, "synthetic-session"):
            self.assertNotIn(private, result.stdout)
        second = self.write([row(native=None), row(sidecar_not_independent_session=True)], "second.jsonl")
        partial = self.cli("--metadata", source, "--metadata", second,
                           "--shared-root", "/synthetic/home", "--output", self.root / "partial.json")
        self.assertEqual(partial.returncode, 3, partial.stderr)
        self.assertEqual(json.loads(partial.stdout)["accounting"], {"assigned": 4, "unassigned": 0, "sidecar": 1, "diagnostic": 0})
        failed = self.cli("--metadata", source, "--output", output)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertEqual(json.loads(failed.stderr), {"error": "output_already_exists"})

    def test_arbitrary_strings_are_inert(self):
        marker = self.root / "must-not-exist"
        text = "$(touch " + str(marker) + ") `touch " + str(marker) + "` <script>synthetic</script>"
        source = self.write([row(metadata={"title": text}, path=text)])
        result = self.cli("--metadata", source, "--output", self.root / "inert.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        self.assertNotIn(text, result.stdout)
        report = json.loads((self.root / "inert.json").read_text())
        self.assertEqual(report["rows"][0]["title_leads"][0]["value"], text)


if __name__ == "__main__":
    unittest.main()
