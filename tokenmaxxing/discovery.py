"""Deterministic project leads from explicit metadata files, never transcripts."""

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import os
from pathlib import PurePosixPath
import re
import tempfile
from urllib.parse import urlsplit, urlunsplit

from .normalize import signature
from .private_paths import Scope, absolute, private_directory, regular
from .records import NormalizationError, encode, loads

SCHEMA_VERSION = 1
PROVIDERS = ("claude", "codex", "hermes")
TEXT_FIELDS = ("origin", "path", "format_candidate", "cwd", "agent_id", "sidechain_path",
               "title", "agentType")
META_TEXT = ("sessionId", "agentId", "cwd", "timestamp", "customTitle", "slug", "gitBranch",
             "title", "source", "git_repo_root", "agentType")
TOP_FIELDS = set(TEXT_FIELDS) | {
    "provider", "bytes", "source_session_id", "source_timestamp", "inspection", "issue",
    "git", "metadata", "sidechain_observed", "sidecar_not_independent_session",
    "schema_version", "metadata_version",
}
META_FIELDS = set(META_TEXT) | {"isSidechain", "started_at", "ended_at", "parent_session_id",
                              "message_count"}


def _key(kind, value):
    return kind + ":" + hashlib.sha256(encode(value).encode("ascii")).hexdigest()


def repository_url(value):
    """Strip authentication and URL suffixes; preserve transport and path semantics."""
    if not isinstance(value, str) or not value or any(ord(c) <= 32 for c in value):
        raise ValueError("invalid_repository_url")
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https", "ssh", "git"} or not parsed.hostname:
        raise ValueError("invalid_repository_url")
    host = parsed.hostname.lower()
    if any(c in host for c in "\\%") or not parsed.path or parsed.path == "/":
        raise ValueError("invalid_repository_url")
    if "\\" in parsed.path:
        raise ValueError("invalid_repository_url")
    port = parsed.port
    if ":" in host:
        host = "[" + host + "]"
    if port is not None and port != {"http": 80, "https": 443, "ssh": 22, "git": 9418}[scheme]:
        host += ":" + str(port)
    return urlunsplit((scheme, host, parsed.path, "", ""))


def _directory(value):
    if (not isinstance(value, str) or not value.startswith("/") or value.startswith("//")
            or "\x00" in value or ".." in value.split("/")):
        raise ValueError("invalid_directory_evidence")
    return str(PurePosixPath(value))


def _valid_id(value, provider):
    return isinstance(value, str) or provider == "hermes" and type(value) is int


def _structure(row):
    if not isinstance(row, dict):
        return "metadata_object_required"
    for field in ("schema_version", "metadata_version"):
        if field in row and (type(row[field]) is not int or row[field] != SCHEMA_VERSION):
            return "unsupported_metadata_version"
    if not isinstance(row.get("provider"), str) or row["provider"] not in PROVIDERS:
        return "unsupported_provider"
    for field in TEXT_FIELDS:
        if (row.get(field) is not None and not isinstance(row[field], str)
                and not (field == "sidechain_path" and type(row[field]) is bool)):
            return "invalid_metadata_field_type"
    for field in ("sidechain_observed", "sidecar_not_independent_session"):
        if row.get(field) is not None and type(row[field]) is not bool:
            return "invalid_metadata_field_type"
    if row.get("bytes") is not None and (type(row["bytes"]) is not int or row["bytes"] < 0):
        return "invalid_metadata_field_type"
    if row.get("source_session_id") is not None and not _valid_id(row["source_session_id"], row["provider"]):
        return "invalid_metadata_field_type"
    for container in ("git", "metadata"):
        if row.get(container) is not None and not isinstance(row[container], dict):
            return "invalid_metadata_field_type"
    meta, git = row.get("metadata") or {}, row.get("git") or {}
    for obj, fields in ((meta, META_TEXT), (git, ("repository_url", "branch", "commit_hash"))):
        for field in fields:
            if obj.get(field) is not None and not isinstance(obj[field], str):
                return "invalid_metadata_field_type"
    if meta.get("isSidechain") is not None and type(meta["isSidechain"]) is not bool:
        return "invalid_metadata_field_type"
    if meta.get("message_count") is not None and (type(meta["message_count"]) is not int or meta["message_count"] < 0):
        return "invalid_metadata_field_type"
    if meta.get("parent_session_id") is not None and not _valid_id(meta["parent_session_id"], row["provider"]):
        return "invalid_metadata_field_type"
    for obj, field in ((row, "source_timestamp"), (meta, "started_at"), (meta, "ended_at")):
        if obj.get(field) is not None and type(obj[field]) not in (str, int, float):
            return "invalid_metadata_field_type"
    return None


def _context(row, diagnostics):
    provider, meta = row["provider"], row.get("metadata") or {}
    native = row.get("source_session_id")
    if native == "":
        native = None
    agent, sidechain = None, None
    if provider == "claude":
        other = meta.get("sessionId") or None
        if native and other and native != other:
            diagnostics.append("conflicting_native_session_ids")
            native = None
        else:
            native = native or other
        agent, sidechain = meta.get("agentId") or None, meta.get("isSidechain")
    result = {"key": None, "native_session_id": native, "agent_id": agent,
              "is_sidechain": sidechain}
    if native is None:
        diagnostics.append("missing_native_session_id")
    if provider == "claude":
        conflict = ((row.get("agent_id") and row["agent_id"] != agent)
                    or (row.get("sidechain_observed") is not None
                        and row["sidechain_observed"] != sidechain))
        if conflict:
            diagnostics.append("conflicting_or_incomplete_claude_context")
        elif native is not None and sidechain is False and agent is None:
            result["key"] = _key("context", [provider, native, "main"])
        elif native is not None and sidechain is True and agent is not None:
            result["key"] = _key("context", [provider, native, "agent", agent])
        if result["key"] is None:
            diagnostics.append("unresolved_claude_context")
    elif native is not None:
        result["key"] = _key("context", [provider, native])
    return result


def _time(value, epoch=False):
    if epoch and type(value) in (int, float):
        parsed = datetime.fromtimestamp(value, timezone.utc)
        precision = "instant"
    elif isinstance(value, str):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            parsed = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
            precision = "day"
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone_required")
            precision = "instant"
    else:
        raise ValueError("unsupported_timestamp")
    return {"utc": parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "precision": precision}


def _observations(row, diagnostics):
    meta = row.get("metadata") or {}
    observations = []
    for field, value in (("source_timestamp", row.get("source_timestamp")),
                         ("metadata.timestamp", meta.get("timestamp")),
                         ("metadata.started_at", meta.get("started_at")),
                         ("metadata.ended_at", meta.get("ended_at"))):
        if value is None:
            continue
        try:
            observations.append({"field": field, **_time(value, row["provider"] == "hermes"
                                                        and field in ("metadata.started_at", "metadata.ended_at"))})
        except (ValueError, OverflowError, OSError):
            diagnostics.append("unparseable_timestamp")
    if not observations:
        diagnostics.append("missing_parseable_timestamp")
    return observations


class Aliases:
    """Exact selectors only; contradictory rules remain unresolved."""

    def __init__(self, value=None):
        self.source = None
        self.targets = defaultdict(set)
        self.labels = defaultdict(set)
        self.selectors = defaultdict(set)
        if value is None:
            value = {"schema_version": 1, "rules": []}
        if (not isinstance(value, dict) or set(value) != {"schema_version", "rules"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or not isinstance(value["rules"], list)):
            raise NormalizationError("invalid_alias_mapping")
        try:
            for rule in value["rules"]:
                if (not isinstance(rule, dict) or set(rule) != {"match", "project", "label"}
                        or not all(isinstance(rule[k], str) and rule[k] for k in ("project", "label"))
                        or not isinstance(rule["match"], dict)):
                    raise ValueError("invalid_alias_rule")
                match = rule["match"]
                if set(match) == {"repository_url"}:
                    selector = ("repository_url", repository_url(match["repository_url"]))
                elif set(match) == {"cwd", "origin"} and (match["origin"] is None or isinstance(match["origin"], str)):
                    selector = ("cwd", match["origin"], _directory(match["cwd"]))
                else:
                    raise ValueError("invalid_alias_selector")
                self.targets[selector].add(rule["project"])
                self.labels[rule["project"]].add(rule["label"])
                self.selectors[rule["project"]].add(selector)
        except (ValueError, TypeError):
            raise NormalizationError("invalid_alias_mapping") from None

    def diagnostics(self):
        return {"alias_selector_conflicts": sum(len(v) > 1 for v in self.targets.values()),
                "alias_label_conflicts": sum(len(v) > 1 for v in self.labels.values())}

    def evidence(self, selectors):
        return [{"selector": list(s), "projects": sorted(self.targets[s])}
                for s in sorted(set(selectors), key=encode) if s in self.targets]

    def rules(self):
        return [{"selector": list(s), "project": target, "labels": sorted(self.labels[target])}
                for s in sorted(self.targets, key=encode) for target in sorted(self.targets[s])]

    def select(self, selectors):
        targets = set().union(*(self.targets.get(s, set()) for s in selectors))
        if len(targets) > 1 or any(len(self.labels[t]) > 1 for t in targets):
            return None, "alias_conflict"
        if not targets:
            return None, None
        target = next(iter(targets))
        return {"key": _key("project", ["alias", target]), "label": next(iter(self.labels[target])),
                "basis": "operator_alias", "confidence": "operator_asserted",
                "aliases": [list(s) for s in sorted(self.selectors[target], key=encode)],
                "operator_project_id": target}, None


def _group(row, shared_roots, aliases, diagnostics):
    meta, git = row.get("metadata") or {}, row.get("git") or {}
    evidence = {"repository_url": None, "directories": []}
    selectors = []
    if git.get("repository_url"):
        try:
            evidence["repository_url"] = repository_url(git["repository_url"])
            selectors.append(("repository_url", evidence["repository_url"]))
        except ValueError:
            diagnostics.append("invalid_repository_url")
    for field, value in (("cwd", row.get("cwd")), ("metadata.cwd", meta.get("cwd")),
                         ("metadata.git_repo_root", meta.get("git_repo_root"))):
        if value:
            try:
                directory = _directory(value)
                evidence["directories"].append({"field": field, "path": directory})
                if directory not in shared_roots:
                    selectors.append(("cwd", row.get("origin"), directory))
            except ValueError:
                diagnostics.append("invalid_directory_evidence")
    evidence["alias_matches"] = aliases.evidence(selectors)
    group, problem = aliases.select(selectors)
    if problem:
        diagnostics.append(problem)
        return None, evidence
    if group:
        return group, evidence
    repo = evidence["repository_url"]
    if repo:
        return {"key": _key("project", ["repository_url", repo]), "label": repo,
                "aliases": [repo], "basis": "repository_url", "confidence": "repository_reference"}, evidence
    directories = {d["path"] for d in evidence["directories"]}
    if directories & shared_roots:
        diagnostics.append("shared_root_evidence")
    directories -= shared_roots
    if len(directories) == 1:
        directory = next(iter(directories))
        if not row.get("origin"):
            diagnostics.append("directory_origin_unknown")
        return {"key": _key("project", ["cwd", row.get("origin"), directory]), "label": directory,
                "aliases": [directory], "basis": "working_directory", "confidence": "tentative"}, evidence
    diagnostics.append("conflicting_directories" if directories else "no_project_evidence")
    return None, evidence


def _project_row(row, locator, shared_roots, aliases):
    projected = {"key": _key("row", [locator["input"], locator["line"]]), "locator": locator,
                 "accounting": "diagnostic", "candidate_key": None, "diagnostics": []}
    problem = _structure(row)
    if problem:
        projected["diagnostics"].append(problem)
        return projected, None
    diagnostics = projected["diagnostics"]
    meta, git = row.get("metadata") or {}, row.get("git") or {}
    if set(row) - TOP_FIELDS or set(meta) - META_FIELDS or set(git) - {"repository_url", "branch", "commit_hash"}:
        diagnostics.append("unmodeled_fields_omitted")
    if row.get("issue"):
        diagnostics.append("input_issue_reported")
    if not row.get("path"):
        diagnostics.append("missing_source_reference")
    projected.update({"provider": row["provider"],
                      "source_reference": {k: row.get(k) for k in ("provider", "origin", "path", "bytes", "format_candidate")},
                      "inspection_present": row.get("inspection") is not None,
                      "observed_times": _observations(row, diagnostics),
                      "title_leads": [], "context": None,
                      "git_observations": {k: git[k] for k in ("branch", "commit_hash") if git.get(k) is not None}})
    projected["identity_observations"] = {
        **{k: row[k] for k in ("source_session_id", "agent_id", "sidechain_observed") if row.get(k) is not None},
        **{"metadata." + k: meta[k] for k in ("sessionId", "agentId", "isSidechain") if meta.get(k) is not None},
    }
    for prefix, obj, fields in (("metadata.", meta, ("title", "customTitle", "slug", "agentType")),
                                ("", row, ("title", "agentType"))):
        for field in fields:
            if obj.get(field):
                projected["title_leads"].append({"field": prefix + field, "value": obj[field]})
    if meta.get("parent_session_id") is not None:
        projected["observed_parent_session_id"] = meta["parent_session_id"]
    group, evidence = _group(row, shared_roots, aliases, diagnostics)
    projected["grouping_evidence"] = evidence
    if row.get("sidecar_not_independent_session") is True:
        projected["accounting"] = "sidecar"
        projected["candidate_lead_key"] = group["key"] if group else None
        projected["diagnostics"] = sorted(set(diagnostics))
        return projected, None
    projected["context"] = _context(row, diagnostics)
    projected["accounting"] = "assigned" if group else "unassigned"
    if group:
        projected["candidate_key"] = group["key"]
    projected["diagnostics"] = sorted(set(diagnostics))
    return projected, group


def _counts(rows):
    sessions = [r for r in rows if r["accounting"] in ("assigned", "unassigned")]
    native = {encode([r["provider"], r["context"]["native_session_id"]]) for r in sessions
              if r["context"]["native_session_id"] is not None}
    contexts = {r["context"]["key"] for r in sessions if r["context"]["key"] is not None}
    known_native = sum(r["context"]["native_session_id"] is not None for r in sessions)
    known_context = sum(r["context"]["key"] is not None for r in sessions)
    references = {encode([r["provider"], r["source_reference"]["origin"], r["source_reference"]["path"]])
                  for r in rows if r.get("source_reference", {}).get("path")}
    return {"rows": len(rows), "session_rows": len(sessions), "rows_with_native_id": known_native,
            "rows_without_native_id": len(sessions) - known_native,
            "distinct_native_ids": len(native), "distinct_session_contexts": len(contexts),
            "rows_with_resolved_context": known_context, "unresolved_context_rows": len(sessions) - known_context,
            "distinct_source_references": len(references),
            "rows_without_source_reference": sum(not r.get("source_reference", {}).get("path") for r in rows),
            "rows_with_parseable_time": sum(bool(r.get("observed_times")) for r in rows),
            "rows_without_parseable_time": sum(not r.get("observed_times") for r in rows)}


def _finish(inputs, rows, groups, aliases, shared_roots):
    members = defaultdict(list)
    context_groups = defaultdict(set)
    for row in rows:
        if row["candidate_key"]:
            members[row["candidate_key"]].append(row)
            if row["context"]["key"]:
                context_groups[row["context"]["key"]].add(row["candidate_key"])
    for row in rows:
        context = row.get("context") or {}
        if len(context_groups.get(context.get("key"), ())) > 1:
            row["diagnostics"] = sorted(set(row["diagnostics"]) | {"context_seen_in_multiple_candidates"})
    candidates = []
    for key in sorted(groups):
        selected = members[key]
        times = [t for r in selected for t in r["observed_times"]]
        references = {encode(r["source_reference"]): r["source_reference"] for r in selected}
        candidates.append({**groups[key], "providers": sorted({r["provider"] for r in selected}),
                           "counts": _counts(selected), "row_keys": sorted(r["key"] for r in selected),
                           "provider_counts": {p: _counts([r for r in selected if r["provider"] == p])
                                               for p in sorted({r["provider"] for r in selected})},
                           "source_references": [references[k] for k in sorted(references)],
                           "date_span": {"first": min(t["utc"] for t in times), "last": max(t["utc"] for t in times),
                                         "precisions": sorted({t["precision"] for t in times})} if times else None,
                           "title_leads": [{"row_key": r["key"], **t} for r in selected for t in r["title_leads"]],
                           "diagnostics": sorted({d for r in selected for d in r["diagnostics"]})})
    diagnostics = dict(sorted(Counter(d for r in rows for d in r["diagnostics"]).items()))
    mapping_diagnostics = aliases.diagnostics()
    accounting = {k: sum(r["accounting"] == k for r in rows) for k in ("assigned", "unassigned", "sidecar", "diagnostic")}
    summary = {**_counts(rows), "input_files": len(inputs), "project_candidates": len(candidates),
               "accounting": accounting, "diagnostics": diagnostics,
               "alias_diagnostics": mapping_diagnostics,
               "coverage": "partial" if diagnostics or any(mapping_diagnostics.values()) or any(not item["rows"] for item in inputs) else "no_known_gaps"}
    return {"schema_version": SCHEMA_VERSION, "record_kind": "project_catalog", "summary": summary,
            "semantics": {"counts": "Observed metadata identities and references, never accomplishments or verified projects.",
                          "hermes_identity": "Provider plus typed native ID across database paths; equality does not establish equal content or a unique real-world session.",
                          "claude_identity": "Observed sessionId/source_session_id plus metadata.isSidechain and metadata.agentId. Unresolved rows have no context key.",
                          "unknown_ids": "Rows without a native ID include missing, empty and conflicting observations; diagnostics and identity_observations distinguish them.",
                          "directories": "Exact origin and directory, tentative only; shared roots excluded by equality, not subtree.",
                          "source_references": "Distinct provider/origin/path tuples, not verified files, byte copies or revisions.",
                          "timestamps": "Observations only; day precision uses midnight UTC as a sorting boundary, not an observed instant.",
                          "projection": "Allowlisted metadata only. Unknown fields and inspection/issue contents are not rendered."},
            "shared_roots": sorted(shared_roots), "alias_source": aliases.source, "alias_rules": aliases.rules(),
            "inputs": inputs, "candidates": candidates, "rows": rows}


def _read_aliases(path):
    path = absolute(path)
    scope = Scope([path])
    regular(path.stat())
    with scope.open(path) as stream:
        initial = os.fstat(stream.fileno())
        try:
            raw = stream.read()
            value = loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            raise NormalizationError("invalid_alias_mapping") from None
        scope.verify(path, initial)
    if not isinstance(value, dict):
        raise NormalizationError("invalid_alias_mapping")
    aliases = Aliases(value)
    aliases.source = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    return aliases


def map_metadata(sources, *, shared_roots=(), aliases=None):
    """Map (reference, binary stream) pairs without filesystem or clock access.

    References must be unique strings. The caller owns streams and verification;
    use build_catalog for explicit files with the repository's secure reader.
    """
    try:
        shared_roots = {_directory(p) for p in shared_roots} | {"/"}
    except ValueError:
        raise NormalizationError("invalid_shared_root") from None
    aliases = aliases if isinstance(aliases, Aliases) else Aliases(aliases)
    inputs, rows, groups = [], [], {}
    seen = set()
    for reference, stream in sources:
        if not isinstance(reference, str) or not reference or reference in seen:
            raise NormalizationError("invalid_or_duplicate_metadata_reference")
        seen.add(reference)
        digest, offset, count = hashlib.sha256(), 0, 0
        for count, raw in enumerate(stream, 1):
            digest.update(raw)
            locator = {"input": reference, "line": count, "byte_offset": offset, "byte_length": len(raw)}
            offset += len(raw)
            try:
                value = loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError, RecursionError):
                value = None
            projected, group = _project_row(value, locator, shared_roots, aliases)
            if value is None:
                projected["diagnostics"] = ["invalid_json_record" if raw.strip() != b"null" else "metadata_object_required"]
            rows.append(projected)
            if group:
                groups[group["key"]] = group
        inputs.append({"path": reference, "sha256": digest.hexdigest(), "bytes": offset, "rows": count})
    if not seen:
        raise NormalizationError("metadata_input_required")
    inputs.sort(key=lambda item: item["path"])
    rows.sort(key=lambda item: (item["locator"]["input"], item["locator"]["line"]))
    return _finish(inputs, rows, groups, aliases, shared_roots)


def _file_streams(paths, scope):
    for path in paths:
        with scope.open(path) as stream:
            initial = os.fstat(stream.fileno())
            yield str(path), stream
            if signature(initial) != signature(os.fstat(stream.fileno())):
                raise NormalizationError("source_changed_during_import")
            scope.verify(path, initial)


def build_catalog(metadata, *, shared_roots=(), aliases=None):
    """Stream each selected file once with no symlinks, discovery or row cap."""
    paths = sorted({absolute(p) for p in metadata})
    if not paths:
        raise NormalizationError("metadata_input_required")
    aliases = _read_aliases(aliases) if aliases is not None else Aliases()
    scope = Scope(paths)
    for path in paths:
        regular(path.stat())
    with closing(_file_streams(paths, scope)) as streams:
        return map_metadata(streams, shared_roots=shared_roots, aliases=aliases)


def catalog(metadata, output, *, shared_roots=(), aliases=None):
    """Publish a new mode-0600 catalog; return only aggregate counts and fixed codes."""
    destination = absolute(output)
    private_directory(destination.parent, create=False)
    if destination.exists() or destination.is_symlink():
        raise NormalizationError("output_already_exists")
    report = build_catalog(metadata, shared_roots=shared_roots, aliases=aliases)
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".tokenmaxxing-catalog-", dir=destination.parent)
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as stream:
            stream.write(encode(report) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        private_directory(destination.parent, create=False)
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            os.unlink(temporary)
    return report["summary"]
