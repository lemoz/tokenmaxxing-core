"""Synthetic ledger seeds and model replies only, never production fallback.

Directly seeded rows test analysis independently of the import/Scope path. They
are NOT evidence of native imports, actual inference or independently checked work.
"""

from copy import deepcopy
from tokenmaxxing.analysis_contract import VERSION
from tokenmaxxing.events import event
from tokenmaxxing.outcomes import now
from tokenmaxxing.pilot_store import category, identity
from tokenmaxxing.records import Record, encode


# Sanitized Codex 0.162.1 startup observations; never read this config path.
STARTUP_STDERR = "2026-10-15T16:40:50.948348Z ERROR codex_core::tools::router: error=code-mode host is disabled"
STARTUP_ITEMS = [
    {"type": "item.completed", "item": {"id": "item_0", "type": "error", "message":
        "Under-development features enabled: skip_host_skill_discovery. Under-development features are incomplete "
        "and may behave unpredictably. To suppress this warning, set `suppress_unstable_features_warning = true` "
        "in /Users/example/.codex/config.toml."}},
    {"type": "item.completed", "item": {"id": "item_1", "type": "error", "message":
        "Code Mode is unavailable because code-mode host is disabled. Code mode will fail closed; "
        "enable `features.code_mode_host` and install `codex-code-mode-host`."}},
]


def seed(store, root, project="Alpha", later=False):
    topics = {"Alpha": ("Build offline search for field notes", "Search works; offline lookup failed"),
              "Beta": ("Filter reading-list notes without losing annotations", "Filter retained annotations; sharing failed")}
    request, result = topics[project]
    texts = [("user", request), ("assistant", "The requested feature is complete"), ("tool", result)]
    if later:
        texts.append(("user", "Investigate the failed path and keep the same work episode"))
    session = identity([project, "synthetic_session"])
    revision = identity([session, later])
    source = root / (project + ("-later" if later else "") + ".jsonl")
    source.write_text("Synthetic seed only. This file was not imported.\n")
    with store.transaction(True) as db:
        db.execute("INSERT OR IGNORE INTO sessions VALUES (?,?,?,?,?,?,?,?)", (session, "codex", "codex-jsonl", encode(project), project, revision, now(), now()))
        db.execute("UPDATE sessions SET current_revision=? WHERE id=?", (revision, session))
        header = {"session_id": project, "provider": "codex", "source": {"sha256": revision}}
        db.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?)", (revision, session, revision, encode(header), encode({"session_diagnostics": []}), now()))
        db.execute("INSERT INTO sources VALUES (?,?)", (revision, str(source)))
        selections = []
        for i, (role, text) in enumerate(texts):
            payload = {"type": "message", "role": role, "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}]}
            if role == "tool":
                payload = {"type": "function_call_output", "call_id": "synthetic", "output": text}
            body = event(Record({"line": i + 1, "pointer": "", "byte_offset": i * 100, "byte_length": 100},
                {"type": "response_item", "payload": payload}), "codex-jsonl", project, revision, i)
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (body["event_id"], revision, i, category(body), encode(body), text.lower()))
            selections.append({"event_id": body["event_id"], "session_id": session, "revision_id": revision, "scope": "personal_reviewed"})
    return selections


def response(packet):
    evidence = packet["evidence"]
    request = next((e for e in evidence if e["evidence_type"] == "recorded_request"), evidence[0])
    tool = next((e for e in evidence if e["evidence_type"] == "recorded_tool_result"), None)
    def claim(text, basis="interpretation", ref=None):
        return {"text": text, "basis": basis, "citations": [ref or request["citation_id"]], "contradictions": []}
    gap = claim("Selected agent-assisted records do not establish human authorship, deployment or full acceptance.", "gap")
    purpose = claim("The requested work concerns: " + request["excerpt"])
    work = [claim("The recorded request is: " + request["excerpt"], request["evidence_type"])]
    state = "attempted"
    if tool:
        work.append(claim("The recorded tool result reports: " + tool["excerpt"], "recorded_tool_result", tool["citation_id"]))
        state = "recorded_result"
    if packet["observations"]:
        observation = packet["observations"][0]
        work.append(claim("The operator attests only to this predicate: " + observation["expected_predicate"] + ". Result: " + observation["actual_result"], "operator_observation", observation["citation_id"]))
        if observation["status"] == "passed":
            state = "observed_predicate"
    return {"schema_version": VERSION, "packet_id": packet["packet_id"], "run_id": packet["run_id"],
        "project": {"purpose": [purpose], "requested_work": [work[0]], "approaches": work, "gaps": [gap]},
        "episodes": [{"episode_id": packet["allowed_episode_ids"][0], "title": purpose, "state": state, "work": work, "limits": [gap]}],
        "profile": {"supported_work": work, "problem_areas": [purpose], "checks": [work[-1]] if tool or packet["observations"] else [gap], "unfinished": [gap], "attribution": [gap]}}


class SyntheticProvider:
    descriptor = {"provider": "synthetic-tests-only", "model": "deterministic-fixture", "schema": VERSION}
    def __init__(self, transform=None):
        self.calls = 0
        self.transform = transform
    def analyze(self, packet, cancelled):
        self.calls += 1
        value = deepcopy(response(packet))
        return self.transform(value) if self.transform else value
