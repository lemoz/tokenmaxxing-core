"""Private work ledger, snapshots and asynchronous bounded inference orchestration."""

from datetime import datetime, timezone
import hashlib
import threading
import uuid

from .analysis_contract import LIMITS, VERSION, prompt, validate
from .analysis_packet import build, event_row, parts, redact, SUSPICIOUS
from .outcomes import now
from .pilot_store import identity, identifier, label
from .private_paths import Scope
from .records import NormalizationError, encode, loads

MIGRATION = """
CREATE TABLE analysis_packets(id TEXT PRIMARY KEY, project TEXT NOT NULL, body TEXT NOT NULL,
 provenance TEXT NOT NULL, snapshot TEXT NOT NULL, request TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE analysis_runs(id TEXT PRIMARY KEY, packet_id TEXT NOT NULL REFERENCES analysis_packets(id),
 provider TEXT NOT NULL, status TEXT NOT NULL, error TEXT, result TEXT, created_at TEXT NOT NULL, finished_at TEXT);
CREATE TABLE work_episodes(id TEXT PRIMARY KEY, project TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE episode_evidence(episode_id TEXT NOT NULL REFERENCES work_episodes(id), event_id TEXT NOT NULL,
 PRIMARY KEY(episode_id,event_id));
CREATE TABLE work_assessments(id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES work_episodes(id),
 run_id TEXT NOT NULL REFERENCES analysis_runs(id), version INTEGER NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(episode_id,version));
CREATE TABLE work_observations(id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES work_episodes(id),
 body TEXT NOT NULL, created_at TEXT NOT NULL);
PRAGMA user_version=2;
"""


def migrate(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == 1:
        db.executescript("BEGIN IMMEDIATE;" + MIGRATION + "COMMIT;")
    elif version != 2:
        raise NormalizationError("unsupported_private_store_version")
    db.execute("UPDATE analysis_runs SET status='failed',error='analysis_interrupted',finished_at=? WHERE status='pending'", (now(),))
    db.commit()


class AnalysisLedger:
    def __init__(self, store):
        self.store, self.provider = store, None
        self.projects, self.source_grants = frozenset(), Scope()
        self.guard = threading.RLock()
        self.active = None

    def configure(self, projects=(), provider=None, source_grants=()):
        """Startup-only authority. Never exposed as an HTTP operation."""
        self.projects = frozenset(label(p) for p in projects)
        self.provider, self.source_grants = provider, Scope(source_grants)

    def close(self):
        with self.guard:
            active = self.active
            if active:
                active[1].set()
        if active:
            # Provider timeouts and cleanup grace bound the owned worker.
            active[2].join()

    def _authorized(self, project):
        project = label(project)
        if project not in self.projects or self.provider is None:
            raise NormalizationError("analysis_disabled_for_project")
        return project

    @staticmethod
    def _episode(db, episode_id, project):
        identifier(episode_id)
        row = db.execute("SELECT * FROM work_episodes WHERE id=? AND project=?", (episode_id, project)).fetchone()
        if not row:
            raise NormalizationError("analysis_episode_ownership_mismatch")
        # A move/deletion cannot silently transfer an old interpretation.
        for e in db.execute("SELECT event_id FROM episode_evidence WHERE episode_id=?", (episode_id,)):
            event_row(db, e[0], project, current=False)
        return row

    def candidates(self, project, offset=0, limit=50):
        page = self.store.evidence(project=label(project), offset=offset, limit=limit)
        return {**page, "items": [{"event_id": r["id"], "session_id": r["session_id"],
                "revision_id": r["revision_id"], "provider": r["provider"], "parts": parts(r["body"]),
                "kind": r["body"]["kind"], "diagnostics": r["body"]["diagnostics"]} for r in page["items"]]}

    def _known_episodes(self, db, project):
        known = []
        for row in db.execute("SELECT id FROM work_episodes WHERE project=? ORDER BY id", (project,)):
            try:
                self._episode(db, row[0], project)
            except NormalizationError as error:
                if str(error) != "analysis_evidence_ownership_or_revision_changed":
                    raise
                # Retain local history, but never export moved/deleted support.
                continue
            prior = db.execute("SELECT body FROM work_assessments WHERE episode_id=? ORDER BY version DESC LIMIT 1", (row[0],)).fetchone()
            known.append({"episode_id": row[0], "evidence_ids": [e[0] for e in db.execute(
                "SELECT event_id FROM episode_evidence WHERE episode_id=? ORDER BY event_id", (row[0],))],
                "prior_interpretation_title": loads(prior[0])["title"]["text"] if prior else "Unassessed work"})
        return known

    def _successful_run(self, db, packet_id):
        return db.execute("SELECT * FROM analysis_runs WHERE packet_id=? AND provider=? AND status='succeeded' ORDER BY created_at LIMIT 1",
                          (packet_id, encode(self.provider.descriptor))).fetchone()

    def _observation(self, db, observation_id, episode_id, project):
        identifier(observation_id)
        row = db.execute("SELECT body FROM work_observations WHERE id=? AND episode_id=?", (observation_id, episode_id)).fetchone()
        if not row:
            raise NormalizationError("analysis_observation_ownership_mismatch")
        body = loads(row["body"])
        for event in body["evidence"]:
            event_row(db, event, project, current=False)
        try:
            # Reauthorize original local paths, never the exported placeholder.
            # This checks authority only; captured proof bytes stay immutable.
            self.store.sources.selected(body["proof_path"])
            self.store.artifacts.selected(body["target_path"])
        except (KeyError, NormalizationError, OSError):
            raise NormalizationError("analysis_observation_requires_startup_grant") from None
        return body

    def preview(self, project, selections, update_episode_id="", observation_ids=None):
        project = self._authorized(project)
        observation_ids = [] if observation_ids is None else observation_ids
        if (not isinstance(update_episode_id, str) or not isinstance(observation_ids, list)
                or len(observation_ids) > LIMITS["observations"] or len(set(observation_ids)) != len(observation_ids)):
            raise NormalizationError("analysis_invalid_update_or_observations")
        request = {"project": project, "selections": sorted(selections, key=encode) if isinstance(selections, list) else selections, "update_episode_id": update_episode_id,
                   "observation_ids": sorted(observation_ids)}
        with self.store.transaction(True) as db:
            if update_episode_id:
                self._episode(db, update_episode_id, project)
            observations = []
            for oid in sorted(observation_ids):
                body = self._observation(db, oid, update_episode_id, project)
                exported = {key: body[key] for key in ("kind", "method", "target_revision", "expected_predicate",
                    "actual_result", "status", "observed_at", "limitations", "provenance", "proof", "proof_sha256")}
                exported["target"] = "explicitly_granted_local_artifact"
                exported = {key: redact(value)[0] if isinstance(value, str) else value for key, value in exported.items()}
                if any(SUSPICIOUS.search(v) for v in exported.values() if isinstance(v, str)):
                    raise NormalizationError("analysis_suspicious_observation_narrow_selection")
                observations.append({**exported, "citation_id": "observation:" + oid,
                                     "evidence_type": "operator_observation", "episode_id": update_episode_id})
            known = self._known_episodes(db, project)
            if len(known) > LIMITS["episodes"]:
                raise NormalizationError("analysis_project_episode_limit_12")
            # Exact repeats use the originally previewed packet, even after that
            # packet has created its episodes. No generated wording in cache keys.
            previous = db.execute("SELECT * FROM analysis_packets WHERE project=? AND request=? ORDER BY created_at DESC", (project, encode(request))).fetchall()
            current_scope = [list(r) for r in db.execute("SELECT id,current_revision FROM sessions WHERE project=? ORDER BY id", (project,))]
            observation_state = [list(r) for r in db.execute("SELECT o.id FROM work_observations o JOIN work_episodes e ON e.id=o.episode_id WHERE e.project=? ORDER BY o.id", (project,))]
            for prior in previous:
                snapshot = loads(prior["snapshot"])
                prior_known = loads(prior["body"])["known_episodes"]
                if (snapshot == {"scope": current_scope, "observations": observation_state}
                        and {e["episode_id"] for e in prior_known} <= {e["episode_id"] for e in known}
                        and (prior_known == known or self._successful_run(db, prior["id"]))):
                    # Revalidate startup grants and selection ownership as well.
                    build(self.store, db, project, selections, update_episode_id, observations, known, self.source_grants)
                    return self._preview_response(prior)
            packet, provenance, scope = build(self.store, db, project, selections, update_episode_id,
                                               observations, known, self.source_grants)
            # Prior observation state changes invalidate reuse even when the
            # operator elects not to include the new observation in this packet.
            if observation_state:
                packet["packet_id"] = identity([packet, observation_state])
                packet["run_id"] = identity([packet["packet_id"], VERSION])
            snapshot = {"scope": scope, "observations": observation_state}
            db.execute("INSERT OR IGNORE INTO analysis_packets VALUES (?,?,?,?,?,?,?)", (packet["packet_id"], project,
                encode(packet), encode(provenance), encode(snapshot), encode(request), now()))
            row = db.execute("SELECT * FROM analysis_packets WHERE id=?", (packet["packet_id"],)).fetchone()
            return self._preview_response(row)

    def _preview_response(self, row):
        packet = loads(row["body"])
        return {"packet_id": row["id"], "packet": packet, "external_prompt": prompt(packet),
                "provider": self.provider.descriptor, "local_provenance": loads(row["provenance"]),
                "limits": LIMITS, "can_analyze": bool(packet["evidence"])}

    def _fresh(self, db, row):
        snapshot = loads(row["snapshot"])
        current = [list(r) for r in db.execute("SELECT id,current_revision FROM sessions WHERE project=? ORDER BY id", (row["project"],))]
        observations = [list(r) for r in db.execute("SELECT o.id FROM work_observations o JOIN work_episodes e ON e.id=o.episode_id WHERE e.project=? ORDER BY o.id", (row["project"],))]
        return snapshot == {"scope": current, "observations": observations}

    def start(self, packet_id):
        identifier(packet_id)
        with self.guard:
            with self.store.transaction(True) as db:
                row = db.execute("SELECT * FROM analysis_packets WHERE id=?", (packet_id,)).fetchone()
                if not row:
                    raise NormalizationError("analysis_packet_not_found")
                self._authorized(row["project"])
                if not self._fresh(db, row):
                    raise NormalizationError("analysis_packet_stale_preview_again")
                packet = loads(row["body"])
                if not packet["evidence"]:
                    raise NormalizationError("analysis_no_supported_evidence")
                request = loads(row["request"])
                if packet["update_episode_id"]:
                    self._episode(db, packet["update_episode_id"], row["project"])
                for episode in packet["known_episodes"]:
                    self._episode(db, episode["episode_id"], row["project"])
                for observation in packet["observations"]:
                    self._observation(db, observation["citation_id"].removeprefix("observation:"),
                                      packet["update_episode_id"], row["project"])
                # A preview can outlive a restart. Current startup grants must
                # still authorize every selected immutable source before send.
                build(self.store, db, row["project"], request["selections"], packet["update_episode_id"],
                      packet["observations"], packet["known_episodes"], self.source_grants)
                for item in packet["evidence"]:
                    event_row(db, item["event_id"], row["project"], item["session_id"], item["revision_id"])
                provider = encode(self.provider.descriptor)
                prior = self._successful_run(db, packet_id)
                if prior:
                    return {"run_id": prior["id"], "status": "succeeded", "reused": True}
                if packet["known_episodes"] != self._known_episodes(db, row["project"]):
                    raise NormalizationError("analysis_packet_stale_preview_again")
                if self.active:
                    if self.active[3] == packet_id:
                        return {"run_id": self.active[0], "status": "pending", "reused": True}
                    raise NormalizationError("analysis_one_run_at_a_time")
                run_id = uuid.uuid4().hex
                db.execute("INSERT INTO analysis_runs VALUES (?,?,?,?,?,?,?,?)", (run_id, packet_id, provider, "pending", None, None, now(), None))
            cancel = threading.Event()
            worker = threading.Thread(target=self._work, args=(run_id, row["project"], packet, cancel), name="selected-evidence-analysis")
            self.active = (run_id, cancel, worker, packet_id)
            worker.start()
            return {"run_id": run_id, "status": "pending", "reused": False}

    def _work(self, run_id, project, packet, cancel):
        try:
            result = validate(self.provider.analyze(packet, cancel), packet)
            with self.store.transaction(True) as db:
                row = db.execute("SELECT * FROM analysis_packets WHERE id=?", (packet["packet_id"],)).fetchone()
                if cancel.is_set():
                    raise NormalizationError("analysis_cancelled")
                if not self._fresh(db, row):
                    raise NormalizationError("analysis_evidence_changed_during_run")
                target = packet["update_episode_id"]
                if target:
                    self._episode(db, target, project)
                existing_ids = {r[0] for r in db.execute("SELECT id FROM work_episodes WHERE project=?", (project,))}
                for episode in sorted(result["episodes"], key=lambda e: e["episode_id"] not in existing_ids):
                    eid = episode["episode_id"]
                    exists = db.execute("SELECT 1 FROM work_episodes WHERE id=?", (eid,)).fetchone()
                    if exists:
                        self._episode(db, eid, project)
                    # Keep submitted evidence as episode context, not a count of
                    # achievements. Each episode's claims still cite exact support.
                    from .analysis_contract import claims
                    linked = {i for c in claims(episode) for i in c["citations"] + c["contradictions"] if i.startswith("event:")}
                    if not linked:
                        linked = {i["event_id"] for i in packet["evidence"]} if target else set()
                    if not linked:
                        raise NormalizationError("analysis_episode_requires_source_evidence")
                    if not exists:
                        # Packets and provider replies can predate live work.
                        # Check both invariants in this same write transaction;
                        # any failure rolls back all episodes and assessments.
                        count = db.execute("SELECT COUNT(*) FROM work_episodes WHERE project=?", (project,)).fetchone()[0]
                        if count >= LIMITS["episodes"]:
                            raise NormalizationError("analysis_project_episode_limit_12")
                        if any(db.execute("SELECT 1 FROM episode_evidence v JOIN work_episodes e ON e.id=v.episode_id WHERE e.project=? AND v.event_id=?",
                                          (project, event_id)).fetchone() for event_id in linked):
                            raise NormalizationError("analysis_related_evidence_requires_existing_episode")
                    db.execute("INSERT OR IGNORE INTO work_episodes VALUES (?,?,?)", (eid, project, now()))
                    for event_id in linked:
                        event_row(db, event_id, project)
                        db.execute("INSERT OR IGNORE INTO episode_evidence VALUES (?,?)", (eid, event_id))
                    version = db.execute("SELECT COUNT(*) FROM work_assessments WHERE episode_id=?", (eid,)).fetchone()[0] + 1
                    db.execute("INSERT INTO work_assessments VALUES (?,?,?,?,?,?)", (identity([run_id, eid]), eid, run_id, version, encode(episode), now()))
                db.execute("UPDATE analysis_runs SET status='succeeded',result=?,finished_at=? WHERE id=?", (encode(result), now(), run_id))
        except Exception as error:
            # Only fixed diagnostics originating in our contract may reach UI.
            code = str(error) if isinstance(error, NormalizationError) and str(error).startswith("analysis_") else "analysis_internal_failure"
            if not code.replace("_", "").isalnum() or len(code) > 100:
                code = "analysis_internal_failure"
            with self.store.transaction(True) as db:
                db.execute("UPDATE analysis_runs SET status='failed',error=?,finished_at=? WHERE id=?", (code, now(), run_id))
        finally:
            with self.guard:
                self.active = None

    def cancel(self, run_id):
        with self.guard:
            if self.active and self.active[0] == run_id:
                self.active[1].set()
                return {"run_id": run_id, "status": "cancelling"}
        return self.run(run_id)

    def run(self, run_id):
        if not isinstance(run_id, str) or len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
            raise NormalizationError("analysis_invalid_run_id")
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise NormalizationError("analysis_run_not_found")
            return {**dict(row), "provider": loads(row["provider"]), "result": loads(row["result"]) if row["result"] else None}

    def observe(self, project, episode_id, observation):
        project = label(project)
        keys = {"kind", "method", "target_path", "target_revision", "expected_predicate", "actual_result",
                "status", "observed_at", "limitations", "provenance", "proof_path", "evidence"}
        if not isinstance(observation, dict) or set(observation) != keys:
            raise NormalizationError("analysis_invalid_observation_fields")
        if any(not isinstance(observation[k], str) or not 1 <= len(observation[k].strip()) <= 4000 for k in keys - {"evidence"}):
            raise NormalizationError("analysis_invalid_observation_text")
        if (observation["kind"] not in ("functional_check", "browser_observation")
                or observation["status"] not in ("passed", "failed", "skipped", "inconclusive")
                or observation["provenance"] != "operator_attestation"):
            raise NormalizationError("analysis_observation_requires_operator_attestation")
        try:
            stamp = datetime.fromisoformat(observation["observed_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
                raise ValueError()
        except ValueError:
            raise NormalizationError("analysis_invalid_observation_timestamp") from None
        evidence = observation["evidence"]
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= LIMITS["selected_events"]:
            raise NormalizationError("analysis_observation_evidence_required")
        for event in evidence:
            identifier(event, event=True)
        with self.store.transaction() as db:
            self._episode(db, episode_id, project)
            linked = {r[0] for r in db.execute("SELECT event_id FROM episode_evidence WHERE episode_id=?", (episode_id,))}
            if not set(evidence) <= linked:
                raise NormalizationError("analysis_observation_must_link_episode_evidence")
        # Validate the existing grant, regular-file and no-symlink contract. No
        # current behavior or bytes are asserted by merely opening this target.
        with self.store.artifacts.open(observation["target_path"]):
            pass
        with self.store.sources.open(observation["proof_path"]) as stream:
            raw = stream.read(16001)
            if not raw or len(raw) > 16000:
                raise NormalizationError("analysis_observation_proof_limit_16000_bytes")
            try:
                proof = raw.decode("utf-8")
            except UnicodeError:
                raise NormalizationError("analysis_observation_utf8_proof_required") from None
        body = {**observation, "proof": proof, "proof_sha256": hashlib.sha256(raw).hexdigest(),
                "app_executed": False, "evidence": sorted(set(evidence))}
        oid = identity([episode_id, body])
        with self.store.transaction(True) as db:
            self._episode(db, episode_id, project)
            linked = {r[0] for r in db.execute("SELECT event_id FROM episode_evidence WHERE episode_id=?", (episode_id,))}
            if not set(evidence) <= linked:
                raise NormalizationError("analysis_observation_must_link_episode_evidence")
            for event in evidence:
                event_row(db, event, project, current=False)
            exists = db.execute("SELECT 1 FROM work_observations WHERE id=?", (oid,)).fetchone()
            db.execute("INSERT OR IGNORE INTO work_observations VALUES (?,?,?,?)", (oid, episode_id, encode(body), now()))
        return {"id": oid, "added": not bool(exists), "observation": body}

    def overview(self, project):
        project = label(project)
        with self.store.transaction() as db:
            episodes = []
            for episode in db.execute("SELECT * FROM work_episodes WHERE project=? ORDER BY created_at,id", (project,)):
                assessments = []
                for row in db.execute("SELECT a.*,r.packet_id,r.provider,r.result FROM work_assessments a JOIN analysis_runs r ON r.id=a.run_id WHERE a.episode_id=? ORDER BY a.version", (episode["id"],)):
                    packet = db.execute("SELECT * FROM analysis_packets WHERE id=?", (row["packet_id"],)).fetchone()
                    assessments.append({**dict(row), "body": loads(row["body"]), "provider": loads(row["provider"]),
                        "result": loads(row["result"]), "packet": loads(packet["body"]), "stale": not self._fresh(db, packet)})
                observations = [{**dict(r), "body": loads(r["body"])} for r in db.execute("SELECT * FROM work_observations WHERE episode_id=? ORDER BY created_at,id", (episode["id"],))]
                episodes.append({**dict(episode), "assessments": assessments, "observations": observations,
                    "evidence_ids": [r[0] for r in db.execute("SELECT event_id FROM episode_evidence WHERE episode_id=? ORDER BY event_id", (episode["id"],))]})
            runs = [dict(r) for r in db.execute("SELECT r.id,r.status,r.error,r.created_at,r.finished_at FROM analysis_runs r JOIN analysis_packets p ON p.id=r.packet_id WHERE p.project=? ORDER BY r.created_at DESC", (project,))]
            return {"enabled": project in self.projects and self.provider is not None,
                "provider": self.provider.descriptor if self.provider else None, "limits": LIMITS,
                "episodes": episodes, "runs": runs, "counts": {"episodes": len(episodes),
                    "assessments": sum(len(e["assessments"]) for e in episodes),
                    "observations": sum(len(e["observations"]) for e in episodes)}}

    def projects_history(self):
        with self.store.transaction() as db:
            return {"projects": [r[0] for r in db.execute("SELECT DISTINCT project FROM analysis_packets ORDER BY project")]}
