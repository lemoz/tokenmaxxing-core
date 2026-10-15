from copy import deepcopy
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest

from analysis_fixtures import seed, response, SyntheticProvider
from tokenmaxxing import pilot_api
from tokenmaxxing.analysis_contract import LIMITS, validate, prompt
from tokenmaxxing.analysis_packet import parts, redact
from tokenmaxxing.events import event
from tokenmaxxing.outcomes import now
from tokenmaxxing.pilot_store import Store, SCHEMA, identity
from tokenmaxxing.records import NormalizationError, Record, encode, loads


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="analysis-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = Store(self.root / "store", [self.root], [self.root])
        self.addCleanup(lambda: self.store.close())
        self.provider = SyntheticProvider()
        self.store.analysis.configure(["Alpha", "Beta"], self.provider, [self.root])
        self.selection = seed(self.store, self.root)

    def preview(self, project="Alpha", selection=None, target="", observations=None):
        return self.store.analysis.preview(project, selection or self.selection, target, observations)

    def finish(self, preview):
        started = self.store.analysis.start(preview["packet_id"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = self.store.analysis.run(started["run_id"])
            if row["status"] != "pending":
                # Also allow worker to finish its owned-state reconciliation.
                with self.store.analysis.guard:
                    worker = self.store.analysis.active
                if worker: worker[2].join(timeout=2)
                return row
            time.sleep(.01)
        self.fail("synthetic worker did not finish")

    def stored_observation(self, episode):
        # Synthetic persisted-body fixture. This tests reauthorization without
        # bypassing Scope; actual proof attachment remains in the HTTP tests.
        proof, target = self.root / "proof.txt", self.root / "artifact.txt"
        proof.write_text("Synthetic captured observation proof")
        target.write_text("Synthetic observed artifact")
        body = {"kind": "functional_check", "method": "Synthetic operator observation",
            "target_path": str(target), "target_revision": "synthetic-r2",
            "expected_predicate": "touch " + str(self.root / "must-not-execute"),
            "actual_result": "Synthetic predicate reported passed", "status": "passed", "observed_at": now(),
            "limitations": "Synthetic only; no real check performed", "provenance": "operator_attestation",
            "proof_path": str(proof), "proof": proof.read_text(),
            "proof_sha256": hashlib.sha256(proof.read_bytes()).hexdigest(),
            "evidence": [self.selection[0]["event_id"]], "app_executed": False}
        oid = identity([episode, body])
        with self.store.transaction(True) as db:
            db.execute("INSERT INTO work_observations VALUES (?,?,?,?)", (oid, episode, encode(body), now()))
        return oid, body

    def test_reusable_alpha_beta_profile_and_exact_repeat_no_call(self):
        first = self.preview()
        self.assertEqual(first["external_prompt"], prompt(first["packet"]))
        self.assertNotIn(str(self.root), first["external_prompt"])
        self.assertNotIn('source_record', first["packet"])
        run = self.finish(first)
        self.assertEqual(run["status"], "succeeded", run["error"])
        self.assertIn("offline", run["result"]["project"]["purpose"][0]["text"])
        repeated = self.preview()
        self.assertEqual(repeated["packet_id"], first["packet_id"])
        self.assertTrue(self.store.analysis.start(repeated["packet_id"])["reused"])
        self.assertEqual(self.provider.calls, 1)
        beta = seed(self.store, self.root, "Beta")
        second = self.finish(self.preview("Beta", beta))
        self.assertEqual(second["status"], "succeeded")
        self.assertIn("annotations", second["result"]["profile"]["problem_areas"][0]["text"])
        for project in ("Alpha", "Beta"):
            ledger = self.store.analysis.overview(project)
            self.assertEqual(ledger["counts"], {"episodes": 1, "assessments": 1, "observations": 0})
        self.assertEqual(self.provider.calls, 2)

    def test_disabled_scope_revision_citation_and_shape_validation(self):
        self.assertRaisesRegex(NormalizationError, "disabled", self.preview, "Unauthorized")
        beta = seed(self.store, self.root, "Beta")
        self.assertRaisesRegex(NormalizationError, "ownership", self.preview, "Alpha", beta)
        forged = deepcopy(self.selection); forged[0]["session_id"] = "a" * 64
        self.assertRaisesRegex(NormalizationError, "ownership", self.preview, "Alpha", forged)
        packet = self.preview()["packet"]
        mutations = [
            lambda v: v.update(extra="bad"),
            lambda v: v.update(run_id="bad"),
            lambda v: v["episodes"][0].update(state="verified"),
            lambda v: v["project"]["purpose"][0].update(citations=[]),
            lambda v: v["project"]["purpose"][0].update(citations=[beta[0]["event_id"]]),
            lambda v: v["profile"]["problem_areas"][0].update(text="Verified expert shipped the feature"),
            lambda v: v["episodes"][0].update(state="observed_predicate"),
            lambda v: v["episodes"][0].update(episode_id="b" * 64),
            lambda v: v["episodes"][0]["work"][0].update(basis="operator_observation"),
        ]
        for mutation in mutations:
            value = deepcopy(response(packet)); mutation(value)
            with self.assertRaises(NormalizationError): validate(value, packet)
        seed(self.store, self.root, later=True)
        self.assertRaisesRegex(NormalizationError, "revision", self.preview)
        self.assertRaisesRegex(NormalizationError, "stale", self.store.analysis.start, packet["packet_id"])

    def test_two_previews_before_first_run_reject_stale_episode_context(self):
        first = self.preview(selection=self.selection[:1])
        second = self.preview(selection=self.selection[:2])
        self.assertEqual(first["packet"]["known_episodes"], [])
        self.assertEqual(second["packet"]["known_episodes"], [])
        self.assertEqual(self.finish(first)["status"], "succeeded")
        with self.assertRaisesRegex(NormalizationError, "analysis_packet_stale_preview_again"):
            self.store.analysis.start(second["packet_id"])
        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(self.store.analysis.overview("Alpha")["counts"],
                         {"episodes": 1, "assessments": 1, "observations": 0})
        refreshed = self.preview(selection=self.selection[:2])
        self.assertNotEqual(refreshed["packet_id"], second["packet_id"])
        self.assertEqual(len(refreshed["packet"]["known_episodes"]), 1)
        # A current packet can legitimately update that same episode.
        self.assertEqual(self.finish(refreshed)["status"], "succeeded")
        self.assertEqual(self.store.analysis.overview("Alpha")["counts"]["episodes"], 1)
        repeated = self.preview(selection=self.selection[:1])
        self.assertEqual(repeated["packet_id"], first["packet_id"])
        self.assertTrue(self.store.analysis.start(repeated["packet_id"])["reused"])
        self.assertEqual(self.provider.calls, 2)

    def test_live_episode_support_rejects_partial_persistence(self):
        preview = self.preview()
        live_id = identity("synthetic live episode")
        def change_live_context(value):
            with self.store.transaction(True) as db:
                db.execute("INSERT INTO work_episodes VALUES (?,?,?)", (live_id, "Alpha", now()))
                db.execute("INSERT INTO episode_evidence VALUES (?,?)", (live_id, self.selection[1]["event_id"]))
            # The first result episode is valid. The second links live support
            # through a contradiction, so all result writes must roll back.
            value["episodes"][0]["work"] = value["episodes"][0]["work"][:1]
            value["episodes"][0]["state"] = "attempted"
            other = deepcopy(value["episodes"][0])
            other["episode_id"] = preview["packet"]["allowed_episode_ids"][1]
            for claim in (other["title"], *other["work"], *other["limits"]):
                claim.update(basis="gap", citations=[self.selection[2]["event_id"]],
                             contradictions=[self.selection[1]["event_id"]])
            value["episodes"].append(other)
            validate(value, preview["packet"])
            return value
        self.store.analysis.provider = SyntheticProvider(change_live_context)
        failed = self.finish(preview)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"], "analysis_related_evidence_requires_existing_episode")
        self.assertIsNone(failed["result"])
        with self.store.transaction() as db:
            self.assertEqual([tuple(r) for r in db.execute("SELECT id FROM work_episodes")], [(live_id,)])
            self.assertEqual([tuple(r) for r in db.execute("SELECT * FROM episode_evidence")],
                             [(live_id, self.selection[1]["event_id"])])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM work_assessments").fetchone()[0], 0)

    def test_live_episode_cap_rejects_partial_persistence(self):
        preview = self.preview(selection=self.selection[:2])
        live_ids = [identity(["synthetic live cap", i]) for i in range(LIMITS["episodes"] - 1)]
        def fill_live_context(value):
            with self.store.transaction(True) as db:
                for eid in live_ids:
                    db.execute("INSERT INTO work_episodes VALUES (?,?,?)", (eid, "Alpha", now()))
                    db.execute("INSERT INTO episode_evidence VALUES (?,?)", (eid, self.selection[2]["event_id"]))
            # Two disjoint proposed episodes would take the live count to 13.
            other = deepcopy(value["episodes"][0])
            other["episode_id"] = preview["packet"]["allowed_episode_ids"][1]
            for claim in (other["title"], *other["work"], *other["limits"]):
                claim.update(basis="gap", citations=[self.selection[1]["event_id"]])
            value["episodes"].append(other)
            validate(value, preview["packet"])
            return value
        self.store.analysis.provider = SyntheticProvider(fill_live_context)
        failed = self.finish(preview)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"], "analysis_project_episode_limit_12")
        self.assertIsNone(failed["result"])
        with self.store.transaction() as db:
            self.assertEqual({r[0] for r in db.execute("SELECT id FROM work_episodes")}, set(live_ids))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM episode_evidence").fetchone()[0], len(live_ids))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM work_assessments").fetchone()[0], 0)

    def test_exclusions_excerpt_provenance_and_distinct_equal_text(self):
        with self.store.transaction(True) as db:
            row = db.execute("SELECT body FROM events WHERE id=?", (self.selection[0]["event_id"],)).fetchone()
            body = loads(row[0]); body["canonical"]["content"][0]["text"] = "email synthetic@example.test token=sk-SYNTHETIC credentialvalue /Users/example/private/file.txt ordinary personal work"
            db.execute("UPDATE events SET body=? WHERE id=?", (encode(body), self.selection[0]["event_id"]))
            # Seed a wrapper and a projection with distinct IDs. No native edits.
            for selection, text in zip(self.selection[1:], ["<environment_context>framework</environment_context>", "guardian approval granted"]):
                value = loads(db.execute("SELECT body FROM events WHERE id=?", (selection["event_id"],)).fetchone()[0])
                if "content" in value["canonical"]: value["canonical"]["content"][0]["text"] = text
                else: value["canonical"]["output"] = text
                db.execute("UPDATE events SET body=? WHERE id=?", (encode(value), selection["event_id"]))
        packet = self.preview()
        self.assertEqual(packet["packet"]["coverage"]["included"], 1)
        self.assertEqual(len(packet["packet"]["coverage"]["excluded"]), 2)
        self.assertNotIn("synthetic@example", packet["external_prompt"])
        self.assertNotIn("/Users/example", packet["external_prompt"])
        self.assertTrue(packet["local_provenance"][self.selection[0]["event_id"]]["redactions"])
        limited = deepcopy(self.selection[:1]); limited[0].update(start=0, end=5)
        short = self.preview(selection=limited)
        self.assertGreater(short["packet"]["coverage"]["omissions"][0]["omitted_characters"], 0)
        self.assertRaisesRegex(NormalizationError, "duplicate", self.preview, "Alpha", self.selection[:1] * 2)

    def test_unicode_excerpt_offsets_preserve_exact_provenance(self):
        selected = deepcopy(self.selection[:1])
        event_id = selected[0]["event_id"]
        with self.store.transaction(True) as db:
            body = loads(db.execute("SELECT body FROM events WHERE id=?", (event_id,)).fetchone()[0])
            body["canonical"]["content"][0]["text"] = "A😀B"
            db.execute("UPDATE events SET body=? WHERE id=?", (encode(body), event_id))
        selected[0].update(start=0, end=3)
        whole = self.preview(selection=selected)
        self.assertEqual(whole["packet"]["evidence"][0]["excerpt"], "A😀B")
        selected[0].update(start=1, end=2)
        excerpt = self.preview(selection=selected)
        self.assertEqual(excerpt["packet"]["evidence"][0]["excerpt"], "😀")
        provenance = excerpt["local_provenance"][event_id]
        self.assertEqual(provenance["original_excerpt_json_sha256"], identity("😀"))
        self.assertEqual(provenance["omitted_characters"], 2)

    def test_codex_structured_tool_output_normalization_parts_and_preview(self):
        selected = deepcopy(self.selection[2:])
        event_id = selected[0]["event_id"]
        outputs = [
            {"type": "input_text", "text": "SYNTHETIC_STATUS_METADATA"},
            {"type": "input_text", "text": "SYNTHETIC_ACTUAL_TOOL_RESULT"},
            {"type": "text", "text": "prefix 😀 synthetic@example.test suffix"},
            {"type": "output_text", "text": "SYNTHETIC_OTHER_RESULT"},
            {"type": "reasoning", "text": "SYNTHETIC_REASONING_EXCLUDED"},
            {"type": "image", "text": "SYNTHETIC_IMAGE_EXCLUDED"},
            {"type": "input_text", "text": {"raw": "SYNTHETIC_OBJECT_EXCLUDED"}},
            "SYNTHETIC_BARE_STRING_EXCLUDED",
        ]
        with self.store.transaction(True) as db:
            old = loads(db.execute("SELECT body FROM events WHERE id=?", (event_id,)).fetchone()[0])
            raw = {"type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "synthetic", "output": outputs}}
            # Exercise the native event normalizer, with the existing synthetic
            # ledger fixture. This is not acceptance of the source-import Scope.
            body = event(Record(old["locator"], raw), "codex-jsonl", old["session_id"],
                         old["source_revision_id"], old["order"])
            native = encode(body)
            self.assertEqual(body["event_id"], event_id)
            self.assertEqual(body["canonical"]["output"], outputs)
            self.assertEqual(body["field_pointers"]["output"], "/payload/output")
            db.execute("UPDATE events SET body=? WHERE id=?", (native, event_id))
        expected = [{"part": "/payload/output/%d/text" % i, "text": block["text"],
                     "evidence_type": "recorded_tool_result"} for i, block in enumerate(outputs[:4])]
        self.assertEqual(parts(body), expected)
        candidate = next(row for row in self.store.analysis.candidates("Alpha")["items"]
                         if row["event_id"] == event_id)
        self.assertEqual(candidate["parts"], expected)
        ambiguous = self.preview(selection=selected)
        self.assertFalse(ambiguous["can_analyze"])
        self.assertEqual(ambiguous["packet"]["coverage"]["excluded"],
                         [{"event_id": event_id, "reason": "select_one_exact_text_part"}])
        selected[0]["part"] = "/payload/output/1/text"
        preview = self.preview(selection=selected)
        evidence = preview["packet"]["evidence"][0]
        self.assertEqual(evidence["excerpt"], outputs[1]["text"])
        self.assertEqual(evidence["part"], selected[0]["part"])
        self.assertEqual(evidence["evidence_type"], "recorded_tool_result")
        self.assertEqual(evidence["locator"], {k: v for k, v in old["locator"].items() if type(v) is int})
        self.assertNotIn(outputs[0]["text"], preview["external_prompt"])
        for block in outputs[4:6]:
            self.assertNotIn(block["text"], preview["external_prompt"])
        self.assertEqual(preview["local_provenance"][event_id]["omitted_parts"],
                         [p["part"] for p in expected if p["part"] != selected[0]["part"]])
        selected[0].update(part="/payload/output/2/text", start=7, end=31)
        preview = self.preview(selection=selected)
        original = outputs[2]["text"][7:31]
        self.assertEqual(original, "😀 synthetic@example.test")
        self.assertEqual(preview["packet"]["evidence"][0]["excerpt"], "😀 [REDACTED_email_1]")
        provenance = preview["local_provenance"][event_id]
        self.assertEqual((provenance["start"], provenance["end"]), (7, 31))
        self.assertEqual(provenance["original_excerpt_json_sha256"], identity(original))
        self.assertEqual(provenance["redactions"][0]["original"], "synthetic@example.test")
        self.assertEqual(provenance["omitted_characters"], len(outputs[2]["text"]) - len(original))
        for index in range(4, len(outputs)):
            selected[0] = {**self.selection[2], "part": "/payload/output/%d/text" % index}
            self.assertFalse(self.preview(selection=selected)["can_analyze"])
        self.assertEqual(encode(body), native)
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT body FROM events WHERE id=?", (event_id,)).fetchone()[0], native)
        self.assertEqual(self.provider.calls, 0)

    def test_redaction_and_explicit_resource_errors(self):
        value = "api_key: sk-syntheticvalue email@example.test https://user:password@example.test 0x" + "a" * 40 + " eyJhbGciOiJ9.eyJzdWIiOiJ9.signature -----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----"
        redacted, mapping = redact(value)
        for secret in ("sk-syntheticvalue", "email@example.test", "user:password", "eyJhbGci", "synthetic\n"):
            self.assertNotIn(secret, redacted)
        self.assertGreaterEqual(len(mapping), 5)
        self.assertRaisesRegex(NormalizationError, "narrow_selection", self.preview, "Alpha", self.selection * 30)
        with self.store.transaction(True) as db:
            body = loads(db.execute("SELECT body FROM events WHERE id=?", (self.selection[0]["event_id"],)).fetchone()[0])
            body["canonical"]["content"][0]["text"] = "x " * 7000
            db.execute("UPDATE events SET body=? WHERE id=?", (encode(body), self.selection[0]["event_id"]))
        self.assertRaisesRegex(NormalizationError, "narrow_excerpt", self.preview)

    def test_later_evidence_observation_same_episode_reopen_history(self):
        first = self.finish(self.preview())
        episode = first["result"]["episodes"][0]["episode_id"]
        later = seed(self.store, self.root, later=True)
        # Observation persistence/model incorporation fixture. Actual proof-file
        # attachment is tested separately through Scope, never mocked to pass.
        observation = {"kind": "functional_check", "method": "Manually observed synthetic offline lookup",
            "target_revision": "synthetic-r2", "expected_predicate": "lookup returns one stored note offline",
            "actual_result": "one stored note returned", "status": "passed", "observed_at": now(),
            "limitations": "Only the lookup predicate; no publishing or full feature check",
            "provenance": "operator_attestation", "proof": "Synthetic observation fixture only",
            "proof_path": str(self.root / "proof.txt"), "target_path": str(self.root / "artifact.txt"),
            "proof_sha256": "a" * 64, "evidence": [self.selection[0]["event_id"]], "app_executed": False}
        Path(observation["proof_path"]).write_text(observation["proof"])
        Path(observation["target_path"]).write_text("Synthetic observed artifact")
        observation["proof_sha256"] = hashlib.sha256(observation["proof"].encode()).hexdigest()
        oid = identity(observation)
        with self.store.transaction(True) as db:
            db.execute("INSERT INTO work_observations VALUES (?,?,?,?)", (oid, episode, encode(observation), now()))
        self.assertTrue(self.store.analysis.overview("Alpha")["episodes"][0]["assessments"][0]["stale"])
        refreshed = self.preview(selection=later, target=episode, observations=[oid])
        second = self.finish(refreshed)
        self.assertEqual(second["status"], "succeeded", second["error"])
        self.assertEqual(second["result"]["episodes"][0]["episode_id"], episode)
        self.assertEqual(second["result"]["episodes"][0]["state"], "observed_predicate")
        self.assertEqual(self.store.analysis.overview("Alpha")["counts"], {"episodes": 1, "assessments": 2, "observations": 1})
        self.assertTrue(self.store.analysis.start(self.preview(selection=later, target=episode, observations=[oid])["packet_id"])["reused"])
        before = self.store.analysis.overview("Alpha")
        self.store.close(); self.store = Store(self.root / "store", [self.root], [self.root])
        self.store.analysis.configure(["Alpha", "Beta"], self.provider, [self.root])
        self.assertEqual(self.store.analysis.overview("Alpha"), before)
        self.assertEqual(self.provider.calls, 2)

    def assert_revoked_observation(self, revoked):
        first = self.finish(self.preview(selection=self.selection[:1]))
        episode = first["result"]["episodes"][0]["episode_id"]
        oid, body = self.stored_observation(episode)
        prepared = self.preview(selection=self.selection[:1], target=episode, observations=[oid])
        source = self.root / "Alpha.jsonl"
        for successful in (False, True):
            self.store.close()
            sources = [source] if revoked == "proof" else [source, body["proof_path"]]
            artifacts = [] if revoked == "artifact" else [body["target_path"]]
            self.store = Store(self.root / "store", sources, artifacts)
            self.provider = SyntheticProvider()
            self.store.analysis.configure(["Alpha"], self.provider, [source])
            actions = {
                "cached_preview": lambda: self.preview(selection=self.selection[:1], target=episode, observations=[oid]),
                "new_preview": lambda: self.preview(selection=self.selection[:2], target=episode, observations=[oid]),
                "persisted_start": lambda: self.finish(prepared),
            }
            for operation, action in actions.items():
                with self.subTest(successful=successful, operation=operation):
                    with self.assertRaisesRegex(NormalizationError, "analysis_observation_requires_startup_grant"):
                        action()
            with self.subTest(successful=successful, operation="no_calls_or_partial_rows"):
                self.assertEqual(self.provider.calls, 0)
                self.assertEqual(self.store.analysis.overview("Alpha")["counts"],
                                 {"episodes": 1, "assessments": 2 if successful else 1, "observations": 1})
            self.store.close()
            self.store = Store(self.root / "store", [source, body["proof_path"]], [body["target_path"]])
            self.store.analysis.configure(["Alpha"], self.provider, [source])
            if not successful:
                self.assertEqual(self.finish(prepared)["status"], "succeeded")

    def test_observation_proof_grant_revoked_after_restart(self):
        self.assert_revoked_observation("proof")

    def test_observation_artifact_grant_revoked_after_restart(self):
        self.assert_revoked_observation("artifact")

    def test_observation_grants_preserve_captured_proof_and_repeat(self):
        first = self.finish(self.preview(selection=self.selection[:1]))
        episode = first["result"]["episodes"][0]["episode_id"]
        oid, body = self.stored_observation(episode)
        prepared = self.preview(selection=self.selection[:1], target=episode, observations=[oid])
        # A replacement file does not replace the captured historical proof.
        Path(body["proof_path"]).write_text("Different synthetic proof bytes")
        self.store.close()
        self.store = Store(self.root / "store", [self.root / "Alpha.jsonl", body["proof_path"]], [body["target_path"]])
        self.store.analysis.configure(["Alpha"], self.provider, [self.root / "Alpha.jsonl"])
        reused = self.preview(selection=self.selection[:1], target=episode, observations=[oid])
        self.assertEqual(reused, prepared)
        fresh = self.preview(selection=self.selection[:2], target=episode, observations=[oid])
        self.assertEqual(fresh["packet"]["observations"], prepared["packet"]["observations"])
        self.assertEqual(fresh["packet"]["observations"][0]["proof"], body["proof"])
        self.assertEqual(self.finish(prepared)["status"], "succeeded")
        self.assertTrue(self.store.analysis.start(self.preview(selection=self.selection[:1],
            target=episode, observations=[oid])["packet_id"])["reused"])
        self.assertEqual(self.provider.calls, 2)
        stored = self.store.analysis.overview("Alpha")["episodes"][0]["observations"][0]["body"]
        self.assertEqual(stored, body)
        self.assertFalse(stored["app_executed"])
        self.assertFalse((self.root / "must-not-execute").exists())

    def test_failure_atomicity_move_pending_and_cancel(self):
        ready, release = threading.Event(), threading.Event()
        class Waiting(SyntheticProvider):
            def analyze(self, packet, cancelled):
                ready.set(); release.wait(3)
                return response(packet)
        self.store.analysis.provider = Waiting()
        preview = self.preview(); started = self.store.analysis.start(preview["packet_id"])
        self.assertTrue(ready.wait(2))
        self.assertEqual(self.store.evidence(project="Alpha")["total"], 3)
        self.store.move(self.selection[0]["session_id"], "Different")
        release.set()
        self.store.analysis.close()
        failed = self.store.analysis.run(started["run_id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.store.analysis.overview("Alpha")["counts"]["assessments"], 0)

    def test_api_unknown_fields_and_no_http_commands(self):
        for route, data in [("/api/analysis/start", {"packet_id": "a" * 64, "command": "never execute"}),
                            ("/api/analysis/preview", {"project": "Alpha", "selections": self.selection, "provider": "remote"})]:
            self.assertRaises(NormalizationError, pilot_api.post, self.store, route, data)
        response = pilot_api.post(self.store, "/api/analysis/preview", {"project": "Alpha", "selections": self.selection})
        self.assertEqual(response["packet"]["coverage"]["included"], 3)
        self.assertEqual(self.provider.calls, 0)

    def test_equal_distinct_messages_are_retained_and_restart_grants_checked(self):
        with self.store.transaction(True) as db:
            for selection in self.selection[:2]:
                body = loads(db.execute("SELECT body FROM events WHERE id=?", (selection["event_id"],)).fetchone()[0])
                body["canonical"]["role"] = "user"
                body["canonical"]["content"][0]["text"] = "Same synthetic request, distinct actual record"
                db.execute("UPDATE events SET body=? WHERE id=?", (encode(body), selection["event_id"]))
        packet = self.preview(selection=self.selection[:2])
        self.assertEqual(len(packet["packet"]["evidence"]), 2)
        self.assertNotEqual(packet["packet"]["evidence"][0]["event_id"], packet["packet"]["evidence"][1]["event_id"])
        self.store.analysis.configure(["Alpha"], self.provider, [])
        self.assertRaisesRegex(NormalizationError, "startup_grant", self.store.analysis.start, packet["packet_id"])
        self.assertEqual(self.provider.calls, 0)

    def test_unsupported_and_assistant_only_do_not_become_observed_success(self):
        assistant = self.preview(selection=[self.selection[1]])["packet"]
        result = response(assistant)
        result["episodes"][0]["state"] = "observed_predicate"
        self.assertRaisesRegex(NormalizationError, "unsupported_observation", validate, result, assistant)
        result["episodes"][0]["state"] = "recorded_result"
        self.assertRaisesRegex(NormalizationError, "unsupported_recorded_result", validate, result, assistant)
        with self.store.transaction(True) as db:
            for selection in self.selection:
                body = loads(db.execute("SELECT body FROM events WHERE id=?", (selection["event_id"],)).fetchone()[0])
                body["diagnostics"] = ["compacted_evidence"]
                db.execute("UPDATE events SET body=? WHERE id=?", (encode(body), selection["event_id"]))
        excluded = self.preview()
        self.assertFalse(excluded["can_analyze"])
        self.assertEqual(len(excluded["packet"]["coverage"]["excluded"]), 3)
        self.assertRaisesRegex(NormalizationError, "no_supported", self.store.analysis.start, excluded["packet_id"])

    def test_duplicate_episodes_unselected_observation_and_reordered_repeat(self):
        packet = self.preview()["packet"]
        result = response(packet)
        duplicate = deepcopy(result["episodes"][0]); duplicate["episode_id"] = packet["allowed_episode_ids"][1]
        result["episodes"].append(duplicate)
        self.assertRaisesRegex(NormalizationError, "duplicate_episode", validate, result, packet)
        self.finish({"packet_id": packet["packet_id"]})
        self.assertEqual(self.preview(selection=list(reversed(self.selection)))["packet_id"], packet["packet_id"])
        episode = self.store.analysis.overview("Alpha")["episodes"][0]["id"]
        self.assertRaisesRegex(NormalizationError, "observation_ownership", self.preview,
                               "Alpha", self.selection, episode, ["f" * 64])
        # Shared support cannot be converted into a new episode by changed prose.
        changed = self.preview(target=episode)["packet"]
        result = response(changed)
        result["episodes"][0]["episode_id"] = "f" * 64
        self.assertRaisesRegex(NormalizationError, "invalid_episode", validate, result, changed)

    def test_cancel_pending_and_failed_output_preserve_zero_assessments(self):
        ready = threading.Event()
        class Cancellable(SyntheticProvider):
            def analyze(self, packet, cancelled):
                ready.set(); cancelled.wait(3)
                raise NormalizationError("analysis_cancelled")
        self.store.analysis.provider = Cancellable()
        started = self.store.analysis.start(self.preview()["packet_id"])
        self.assertTrue(ready.wait(2))
        self.store.analysis.cancel(started["run_id"])
        self.store.analysis.close()
        self.assertEqual(self.store.analysis.run(started["run_id"])["error"], "analysis_cancelled")
        self.assertEqual(self.store.analysis.overview("Alpha")["counts"]["episodes"], 0)
        self.store.analysis.provider = SyntheticProvider(lambda value: {**value, "extra": "private text"})
        failed = self.finish(self.preview())
        self.assertEqual(failed["error"], "analysis_invalid_schema")
        self.assertIsNone(failed["result"])

    def test_historical_project_remains_inspectable_after_move(self):
        self.finish(self.preview())
        self.store.move(self.selection[0]["session_id"], "Other grouping")
        ledger = self.store.analysis.overview("Alpha")
        self.assertEqual(ledger["counts"]["episodes"], 1)
        self.assertTrue(ledger["episodes"][0]["assessments"][0]["stale"])
        self.assertIn("Alpha", self.store.analysis.projects_history()["projects"])

    def test_moved_episode_context_excluded_from_retained_project_preview(self):
        retained = seed(self.store, self.root, "Beta")
        self.store.move(retained[0]["session_id"], "Alpha")
        first = self.finish(self.preview(selection=self.selection[:1]))
        self.assertEqual(first["status"], "succeeded")
        episode = first["result"]["episodes"][0]
        before_move = self.preview(selection=retained[:1])
        self.assertEqual(before_move["packet"]["known_episodes"][0]["episode_id"], episode["episode_id"])
        self.store.move(self.selection[0]["session_id"], "Beta")
        after_move = self.preview(selection=retained[:1])
        self.assertEqual(after_move["packet"]["known_episodes"], [])
        self.assertNotIn(episode["episode_id"], after_move["packet"]["allowed_episode_ids"])
        self.assertNotIn(episode["title"]["text"], after_move["external_prompt"])
        for selected in self.selection:
            self.assertNotIn(selected["event_id"], after_move["external_prompt"])
        self.assertEqual(self.preview(selection=retained[:1])["packet_id"], after_move["packet_id"])
        with self.assertRaisesRegex(NormalizationError, "ownership"):
            self.preview(selection=retained[:1], target=episode["episode_id"])
        with self.assertRaisesRegex(NormalizationError, "stale"):
            self.store.analysis.start(before_move["packet_id"])
        historical = self.store.analysis.overview("Alpha")["episodes"][0]
        self.assertEqual(historical["assessments"][0]["body"], episode)
        self.assertTrue(historical["assessments"][0]["stale"])
        self.assertEqual(historical["evidence_ids"], [self.selection[0]["event_id"]])
        self.assertEqual(self.provider.calls, 1)


class MigrationTests(unittest.TestCase):
    def test_existing_v1_evidence_receipt_history_preserved(self):
        import os
        with tempfile.TemporaryDirectory(prefix="analysis-v1-") as tmp:
            root = Path(tmp).resolve(); private = root / "store"; private.mkdir(mode=0o700)
            path = private / "pilot.sqlite3"
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600); os.close(descriptor)
            db = sqlite3.connect(path); db.executescript(SCHEMA)
            # An existing source revision and historical receipt, byte-for-byte.
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)", ("s", "codex", "codex-jsonl", '"synthetic"', "Alpha", "r2", "t", "t"))
            for revision in ("r1", "r2"):
                db.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?)", (revision, "s", revision, '{}', '{}', 't'))
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", ("e", "r1", 0, "observed", '{"synthetic":true}', 'synthetic'))
            db.execute("INSERT INTO outcomes VALUES (?,?,?,?,?)", ("o", "s", "report", '{"claim":"synthetic historical receipt"}', 't'))
            db.execute("INSERT INTO outcome_links VALUES (?,?)", ("o", "e")); db.commit()
            tables = ("sessions", "revisions", "events", "outcomes", "outcome_links")
            before = {t: db.execute("SELECT * FROM " + t).fetchall() for t in tables}; db.close()
            store = Store(private)
            try:
                self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], 2)
                after = {t: [tuple(r) for r in store.db.execute("SELECT * FROM " + t)] for t in tables}
                self.assertEqual(before, after)
                self.assertTrue(store.outcomes()["items"][0]["historical"])
                store._check_files()
            finally: store.close()
