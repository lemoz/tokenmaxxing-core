"""Transactional private evidence ledger. Source records remain inert JSON."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading

from .normalize import FORMATS, inspect, normalize
from .outcomes import artifact_check, now
from .private_paths import Scope, check_private_file, private_directory
from .records import NormalizationError, encode, loads


def identity(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def identifier(value, event=False):
    if not isinstance(value, str) or not re.fullmatch(("event:" if event else "") + r"[a-f0-9]{64}", value):
        raise NormalizationError("invalid_identifier")
    return value


def label(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80:
        raise NormalizationError("project_label_required_1_to_80")
    return value.strip()


def category(event):
    """Recorded user text is intent evidence, never inferred human ownership."""
    canonical = event["canonical"]
    if event["kind"] not in ("message", "tool_call", "tool_result"):
        return "retained"
    content = canonical.get("content")
    has_text = isinstance(content, str) and bool(content.strip()) or isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") in ("text", "input_text") and
        isinstance(b.get("text"), str) and b["text"].strip() for b in content)
    excluded = {"inactive_evidence", "compacted_evidence", "summary_evidence"}
    if (event["kind"] == "message" and canonical.get("role") == "user" and has_text
            and not excluded.intersection(event["diagnostics"])
            and not event.get("observed_metadata", {}).get("isMeta")):
        return "intent"
    return "observed"


SCHEMA = """
CREATE TABLE sessions (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, format TEXT NOT NULL,
 native_identity TEXT, project TEXT NOT NULL, current_revision TEXT NOT NULL,
 created_at TEXT NOT NULL, selected_at TEXT NOT NULL);
CREATE TABLE revisions (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 digest TEXT NOT NULL, header TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(session_id, digest));
CREATE TABLE sources (
 revision_id TEXT NOT NULL REFERENCES revisions(id) ON DELETE CASCADE,
 reference TEXT NOT NULL, PRIMARY KEY(revision_id, reference));
CREATE TABLE events (
 id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES revisions(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, category TEXT NOT NULL, body TEXT NOT NULL, search_text TEXT NOT NULL,
 UNIQUE(revision_id, ordinal));
CREATE INDEX event_revision ON events(revision_id, ordinal);
CREATE TABLE outcomes (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, receipt TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE outcome_links (
 outcome_id TEXT NOT NULL REFERENCES outcomes(id) ON DELETE CASCADE,
 event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
 PRIMARY KEY(outcome_id, event_id));
PRAGMA user_version=1;
"""


class Store:
    def __init__(self, root, allow_source=(), allow_artifact=()):
        self.root = private_directory(root)
        self.root_identity = (self.root.stat().st_dev, self.root.stat().st_ino)
        self.sources = Scope(allow_source)
        self.artifacts = Scope(allow_artifact)
        self.lock = threading.RLock()
        self.db = None
        self.lock_fd = None
        self.path = self.root / "pilot.sqlite3"
        self._check_files()
        try:
            self.lock_fd = os.open(self.root / "pilot.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            check_private_file(self.root / "pilot.lock")
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise NormalizationError("private_store_already_open") from None
            fresh = not self.path.exists()
            if fresh:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(descriptor)
            check_private_file(self.path)
            self.db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA trusted_schema=OFF")
            if fresh:
                self.db.executescript("BEGIN IMMEDIATE;" + SCHEMA + "COMMIT;")
            from .analysis_store import AnalysisLedger, migrate
            migrate(self.db)
            self.analysis = AnalysisLedger(self)
            self._check_files()
        except BaseException:
            self.close()
            raise

    def _check_files(self):
        private_directory(self.root, create=False)
        if (self.root.stat().st_dev, self.root.stat().st_ino) != self.root_identity:
            raise NormalizationError("private_store_directory_changed")
        for path in self.root.iterdir():
            if path.name not in {"pilot.sqlite3", "pilot.sqlite3-journal", "pilot.sqlite3-wal",
                                 "pilot.sqlite3-shm", "pilot.lock"}:
                raise NormalizationError("unexpected_private_store_entry")
            check_private_file(path)

    def close(self):
        if getattr(self, "analysis", None) is not None:
            self.analysis.close()
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

    @contextmanager
    def transaction(self, write=False):
        with self.lock:
            self._check_files()
            self.db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield self.db
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def import_source(self, path, source_format, project, selected_session=None):
        project = label(project)
        if source_format not in FORMATS:
            raise NormalizationError("unsupported_source_format")
        if selected_session is not None and (not isinstance(selected_session, str) or not selected_session):
            raise NormalizationError("invalid_selected_session")
        path = self.sources.selected(path)
        with self.lock:
            self._check_files()
            with self.sources.open(path) as source:
                initial = os.fstat(source.fileno())
                # Temporary normalized artifacts never survive success or error.
                with tempfile.TemporaryDirectory(prefix="import-", dir=self.root) as staging:
                    output = os.path.join(staging, "normalized.jsonl")
                    normalize(path, output, source_format, selected_session=selected_session,
                              source_opener=self.sources.open)
                    summary = inspect(output)
                    if source_format != "hermes-sqlite":
                        self.sources.verify(path, initial)
                    with open(output, encoding="utf-8") as stream:
                        header = loads(stream.readline())
                        session_id = identity([header["provider"], source_format,
                                               header["session_id"] or ["unresolved_revision", header["source"]["sha256"]]])
                        revision = identity([session_id, header["source"]["sha256"]])
                        # Staging is private and owned by this call; transaction
                        # checks are performed before staging, not during it.
                        self.db.execute("BEGIN IMMEDIATE")
                        try:
                            existing = self.db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
                            added = self.db.execute("SELECT 1 FROM revisions WHERE id=?", (revision,)).fetchone() is None
                            observed = now()
                            if existing is None:
                                self.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)",
                                                (session_id, header["provider"], source_format, encode(header["session_id"]),
                                                 project, revision, observed, observed))
                            if added:
                                self.db.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?)",
                                                (revision, session_id, header["source"]["sha256"], encode(header),
                                                 encode(summary), observed))
                                for raw in stream:
                                    value = loads(raw)
                                    if value["record_kind"] == "summary":
                                        break
                                    searchable = json.dumps([value["canonical"], value.get("interactions", []),
                                                             value["locator"], value["source_record"]], ensure_ascii=False).encode("utf-8", "backslashreplace").decode("utf-8").lower()
                                    self.db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                                                    (value["event_id"], revision, value["order"], category(value),
                                                     encode(value), searchable))
                            self.db.execute("INSERT OR IGNORE INTO sources VALUES (?,?)", (revision, str(path)))
                            self.db.execute("UPDATE sessions SET current_revision=?, selected_at=? WHERE id=?",
                                            (revision, observed, session_id))
                            self.db.commit()
                        except BaseException:
                            self.db.rollback()
                            raise
            return {"session_id": session_id, "revision_id": revision, "new_session": existing is None,
                    "new_revision": added, "added_records": summary["events"] if added else 0,
                    "project": existing["project"] if existing else project,
                    "grouping_unchanged": bool(existing and existing["project"] != project), "summary": summary}

    def overview(self):
        with self.transaction() as db:
            projects = []
            for row in db.execute("SELECT project, COUNT(*) AS sessions FROM sessions GROUP BY project ORDER BY project"):
                counts = {"intent": 0, "observed": 0, "retained": 0}
                for count in db.execute("""SELECT e.category, COUNT(*) FROM events e JOIN sessions s
                        ON s.current_revision=e.revision_id WHERE s.project=? GROUP BY e.category""", (row["project"],)):
                    counts[count[0]] = count[1]
                outcomes = db.execute("""SELECT o.kind, COUNT(*) FROM outcomes o JOIN sessions s ON s.id=o.session_id
                                        WHERE s.project=? GROUP BY o.kind""", (row["project"],)).fetchall()
                projects.append({**dict(row), **counts, "records": sum(counts.values()),
                                 "outcomes": dict(outcomes)})
            return {"projects": projects,
                    "sessions": db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                    "records": db.execute("SELECT COUNT(*) FROM events e JOIN sessions s ON s.current_revision=e.revision_id").fetchone()[0],
                    "revisions": db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0],
                    "outcomes": db.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]}

    def sessions(self, project="", offset=0, limit=50):
        with self.transaction() as db:
            where, args = ("WHERE s.project=?", [project]) if project else ("", [])
            total = db.execute("SELECT COUNT(*) FROM sessions s " + where, args).fetchone()[0]
            rows = db.execute("""SELECT s.*, r.header, r.summary FROM sessions s JOIN revisions r ON r.id=s.current_revision
                               """ + where + " ORDER BY s.created_at,s.id LIMIT ? OFFSET ?", args + [limit, offset])
            items = [{**dict(row), "header": loads(row["header"]), "summary": loads(row["summary"]),
                      "native_identity": loads(row["header"])["session_id"]} for row in rows]
            return {"items": items, "total": total, "offset": offset, "limit": limit}

    def revisions(self, session_id):
        identifier(session_id)
        with self.transaction() as db:
            self._session(db, session_id)
            rows = db.execute("""SELECT r.*, s.current_revision=r.id AS current FROM revisions r JOIN sessions s ON s.id=r.session_id
                                  WHERE s.id=? ORDER BY r.created_at,r.id""", (session_id,))
            return {"items": [{**dict(row), "header": loads(row["header"]), "summary": loads(row["summary"])} for row in rows]}

    def evidence(self, project="", provider="", query="", session_id="", revision_id="", category_filter="", offset=0, limit=50):
        clauses, args = [], []
        for column, value in (("s.project", project), ("s.provider", provider), ("s.id", session_id),
                              ("r.id", revision_id), ("e.category", category_filter)):
            if value:
                clauses.append(column + "=?")
                args.append(value)
        if not revision_id:
            clauses.append("r.id=s.current_revision")
        if query:
            clauses.append("instr(e.search_text,?)>0")
            args.append(query.lower())
        join = " FROM events e JOIN revisions r ON r.id=e.revision_id JOIN sessions s ON s.id=r.session_id WHERE "
        join += " AND ".join(clauses) or "1"
        with self.transaction() as db:
            total = db.execute("SELECT COUNT(*)" + join, args).fetchone()[0]
            rows = db.execute("SELECT e.*, s.id AS session_id, s.project, s.provider, r.id=s.current_revision AS current" + join +
                              " ORDER BY s.created_at,s.id,e.ordinal LIMIT ? OFFSET ?", args + [limit, offset])
            return {"items": [self._event(row) for row in rows], "total": total, "offset": offset, "limit": limit}

    @staticmethod
    def _event(row):
        return {key: loads(row[key]) if key == "body" else row[key] for key in row.keys() if key != "search_text"}

    def detail(self, event_id):
        identifier(event_id, event=True)
        with self.transaction() as db:
            row = db.execute("""SELECT e.*, s.id AS session_id, s.project, s.provider, r.header, r.summary,
                r.id=s.current_revision AS current FROM events e JOIN revisions r ON r.id=e.revision_id
                JOIN sessions s ON s.id=r.session_id WHERE e.id=?""", (event_id,)).fetchone()
            if row is None:
                raise NormalizationError("evidence_not_found")
            return {**self._event(row), "header": loads(row["header"]), "summary": loads(row["summary"]),
                    "source_references": [r[0] for r in db.execute("SELECT reference FROM sources WHERE revision_id=? ORDER BY reference", (row["revision_id"],))]}

    @staticmethod
    def _session(db, session_id):
        row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise NormalizationError("session_not_found")
        return row

    def move(self, session_id, project):
        identifier(session_id)
        project = label(project)
        with self.transaction(True) as db:
            self._session(db, session_id)
            db.execute("UPDATE sessions SET project=? WHERE id=?", (project, session_id))
        return {"project": project}

    def rename(self, old, new):
        old, new = label(old), label(new)
        with self.transaction(True) as db:
            if not db.execute("SELECT 1 FROM sessions WHERE project=?", (old,)).fetchone():
                raise NormalizationError("project_not_found")
            if old != new and db.execute("SELECT 1 FROM sessions WHERE project=?", (new,)).fetchone():
                raise NormalizationError("project_exists_use_session_move")
            db.execute("UPDATE sessions SET project=? WHERE project=?", (new, old))
        return {"project": new}

    def delete(self, session_id, confirmation):
        identifier(session_id)
        if confirmation != "DELETE " + session_id:
            raise NormalizationError("exact_delete_confirmation_required")
        with self.transaction(True) as db:
            self._session(db, session_id)
            db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        return {"deleted_session": session_id, "original_sources_unchanged": True}

    def record_outcome(self, session_id, evidence, kind, claim="", note="", path=None, expected=None):
        identifier(session_id)
        if not isinstance(evidence, list) or not evidence:
            raise NormalizationError("linked_evidence_required")
        evidence = sorted({identifier(e, event=True) for e in evidence})
        if kind not in ("check", "report") or not isinstance(note, str) or len(note) > 2000:
            raise NormalizationError("invalid_outcome_fields")
        if not isinstance(claim, str) or len(claim) > 2000 or kind == "report" and not claim.strip():
            raise NormalizationError("human_report_required")
        with self.transaction(True) as db:
            self._session(db, session_id)
            for event_id in evidence:
                if not db.execute("""SELECT 1 FROM events e JOIN revisions r ON r.id=e.revision_id
                                     WHERE e.id=? AND r.session_id=?""", (event_id, session_id)).fetchone():
                    raise NormalizationError("evidence_must_belong_to_selected_session")
            receipt = {"kind": kind, "claim": claim.strip(), "note": note, "evidence": evidence}
            if kind == "check":
                receipt["observation"] = artifact_check(self.artifacts, path, expected)
                observation = receipt["observation"]
                fingerprint = [session_id, kind, evidence, note, observation["path"], observation["expected_sha256"],
                               observation["actual_sha256"], observation["bytes"], observation["status"]]
            else:
                receipt["status"] = "unverified_human_report"
                fingerprint = [session_id, kind, evidence, claim.strip(), note]
            outcome_id = identity(fingerprint)
            created = now()
            prior = db.execute("SELECT receipt FROM outcomes WHERE id=?", (outcome_id,)).fetchone()
            if not prior:
                db.execute("INSERT INTO outcomes VALUES (?,?,?,?,?)", (outcome_id, session_id, kind, encode(receipt), created))
                db.executemany("INSERT INTO outcome_links VALUES (?,?)", [(outcome_id, e) for e in evidence])
        return {"id": outcome_id, "added": not bool(prior), "receipt": loads(prior[0]) if prior else receipt,
                "repeat_policy": "identical_assertion_retains_first_observation"}

    def outcomes(self, project="", offset=0, limit=50):
        where, args = ("WHERE s.project=?", [project]) if project else ("", [])
        join = " FROM outcomes o JOIN sessions s ON s.id=o.session_id " + where
        with self.transaction() as db:
            total = db.execute("SELECT COUNT(*)" + join, args).fetchone()[0]
            rows = db.execute("SELECT o.*,s.project,s.current_revision" + join + " ORDER BY o.created_at,o.id LIMIT ? OFFSET ?", args + [limit, offset])
            items = []
            for row in rows:
                links = [dict(r) for r in db.execute("""SELECT e.id,e.revision_id,e.revision_id=? AS current
                         FROM outcome_links l JOIN events e ON e.id=l.event_id WHERE l.outcome_id=? ORDER BY e.id""",
                                                       (row["current_revision"], row["id"]))]
                items.append({**dict(row), "receipt": loads(row["receipt"]), "links": links,
                              "historical": any(not link["current"] for link in links)})
            return {"items": items, "total": total, "offset": offset, "limit": limit}
