"""Minimal explicitly reviewed excerpts, local provenance and conservative redaction."""

import re

from .analysis_contract import LIMITS, PACKET_VERSION, VERSION
from .pilot_store import identity, identifier
from .records import NormalizationError, encode, loads

PATTERNS = [
    ("private_key", r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----"),
    ("credential", r"(?i)\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|secret|password|authorization|bearer)\b[\s\"':=]+[^\s,;<>]+"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    ("url_userinfo", r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@]+(?::[^\s/@]*)?@"),
    ("email", r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}"),
    ("wallet", r"\b(?:0x[a-fA-F0-9]{40,}|bc1[a-zA-Z0-9]{25,}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b"),
    ("local_path", r"(?:[A-Za-z]:\\|~[/\\]|(?<![\w:])/(?:Users|home|private|tmp|var|Volumes|opt|workspace|work|mnt)/)[^\s\"'<>]+"),
    ("opaque_identifier", r"\b[A-Za-z0-9_+/-]{32,}={0,2}\b"),
]
SUSPICIOUS = re.compile(r"(?i)(-----BEGIN|\b(?:employer|company|client confidential|mixed.company|proprietary|seed phrase|mnemonic|credential)\b)")
WRAPPER = re.compile(r"(?i)(recommended_plugins|environment_context|AGENTS(?:\.md)?\s+instructions|<INSTRUCTIONS>|guardian.{0,40}approv|<system|<developer)")


def redact(text):
    spans = []
    for kind, pattern in PATTERNS:
        for match in re.finditer(pattern, text):
            spans.append((match.start(), match.end(), kind))
    merged = []
    for start, end, kind in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]), merged[-1][2])
        else:
            merged.append((start, end, kind))
    mapping, chunks, cursor = [], [], 0
    for number, (start, end, kind) in enumerate(merged, 1):
        replacement = "[REDACTED_%s_%d]" % (kind, number)
        chunks.extend([text[cursor:start], replacement])
        mapping.append({"start": start, "end": end, "original": text[start:end], "replacement": replacement})
        cursor = end
    chunks.append(text[cursor:])
    return "".join(chunks), mapping


def parts(event):
    """Only known text fields. Never stringify raw records, reasoning or headers."""
    canonical, result = event["canonical"], []
    kind, role = event["kind"], event["canonical"].get("role")
    pointers = event.get("field_pointers", {})
    if kind == "tool_result":
        output = canonical.get("output")
        base = pointers.get("output", event["data_pointer"] + "/output")
        if isinstance(output, str):
            result.append({"part": base, "text": output, "evidence_type": "recorded_tool_result"})
        elif isinstance(output, list):
            for index, block in enumerate(output):
                if (isinstance(block, dict) and block.get("type") in ("text", "input_text", "output_text")
                        and isinstance(block.get("text"), str)):
                    result.append({"part": base + "/%d/text" % index, "text": block["text"],
                                   "evidence_type": "recorded_tool_result"})
    elif kind == "message" and role in ("user", "assistant", "tool"):
        content = canonical.get("content")
        base = pointers.get("content", event["data_pointer"] + "/content")
        basis = {"user": "recorded_request", "assistant": "assistant_claim", "tool": "recorded_tool_result"}[role]
        if isinstance(content, str):
            result.append({"part": base, "text": content, "evidence_type": basis})
        elif isinstance(content, list):
            for index, block in enumerate(content):
                if not isinstance(block, dict):
                    continue
                if block.get("type") in ("text", "input_text", "output_text") and isinstance(block.get("text"), str):
                    result.append({"part": base + "/%d/text" % index, "text": block["text"], "evidence_type": basis})
                elif block.get("type") == "tool_result" and isinstance(block.get("content"), str):
                    result.append({"part": base + "/%d/content" % index, "text": block["content"],
                                   "evidence_type": "recorded_tool_result"})
    return result


def event_row(db, event_id, project, session_id=None, revision_id=None, current=True):
    identifier(event_id, event=True)
    row = db.execute("""SELECT e.*,r.session_id,s.project,s.provider,s.current_revision
        FROM events e JOIN revisions r ON r.id=e.revision_id JOIN sessions s ON s.id=r.session_id
        WHERE e.id=?""", (event_id,)).fetchone()
    if (not row or row["project"] != project or session_id and row["session_id"] != session_id
            or revision_id and row["revision_id"] != revision_id
            or current and row["revision_id"] != row["current_revision"]):
        raise NormalizationError("analysis_evidence_ownership_or_revision_changed")
    return row


def build(store, db, project, selections, target, observations, known, source_grants):
    if not isinstance(selections, list) or not 1 <= len(selections) <= LIMITS["selected_events"]:
        raise NormalizationError("analysis_narrow_selection_1_to_80_events")
    evidence, excluded, provenance, seen = [], [], {}, set()
    for selection in selections:
        required = {"event_id", "session_id", "revision_id", "scope"}
        if (not isinstance(selection, dict) or not required <= set(selection)
                or set(selection) - required - {"part", "start", "end"}):
            raise NormalizationError("analysis_invalid_selection")
        event_id = selection["event_id"]
        if event_id in seen:
            raise NormalizationError("analysis_duplicate_event_selection")
        seen.add(event_id)
        identifier(selection["session_id"]); identifier(selection["revision_id"])
        row = event_row(db, event_id, project, selection["session_id"], selection["revision_id"])
        references = [r[0] for r in db.execute("SELECT reference FROM sources WHERE revision_id=?", (row["revision_id"],))]
        granted = False
        for reference in references:
            try:
                source_grants.selected(reference)
                granted = True
            except NormalizationError:
                pass
        if not granted:
            raise NormalizationError("analysis_source_requires_startup_grant")
        body = loads(row["body"])
        available = parts(body)
        issues = set(body["diagnostics"]) - {"missing_timestamp", "unsupported_timestamp_shape"}
        reason = None
        if selection["scope"] != "personal_reviewed":
            reason = "personal_scope_not_reviewed"
        elif issues or body.get("observed_metadata", {}).get("isMeta"):
            reason = "unsupported_compacted_or_metadata_evidence"
        elif not available:
            reason = "no_supported_text_excerpts"
        chosen = [p for p in available if p["part"] == selection.get("part")] if "part" in selection else available
        if len(chosen) != 1:
            reason = reason or "select_one_exact_text_part"
        if reason:
            excluded.append({"event_id": event_id, "reason": reason})
            continue
        part = chosen[0]
        start, end = selection.get("start", 0), selection.get("end", len(part["text"]))
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(part["text"]):
            raise NormalizationError("analysis_invalid_excerpt_offsets")
        original = part["text"][start:end]
        if len(original) > LIMITS["excerpt_chars"]:
            raise NormalizationError("analysis_narrow_excerpt_max_12000_characters")
        redacted, mapping = redact(original)
        if WRAPPER.search(part["text"]):
            reason = "framework_or_historical_approval_not_human_intent"
        elif SUSPICIOUS.search(redacted):
            reason = "suspicious_or_mixed_scope_requires_narrower_selection"
        if reason:
            excluded.append({"event_id": event_id, "reason": reason})
            continue
        citation = event_id
        # Locators are limited to numeric positions and the selected known field.
        locator = {k: v for k, v in body["locator"].items() if type(v) is int}
        evidence.append({"citation_id": citation, "event_id": event_id, "session_id": row["session_id"],
            "revision_id": row["revision_id"], "provider": row["provider"], "locator": locator,
            "part": part["part"], "start": start, "end": end, "excerpt": redacted,
            "evidence_type": part["evidence_type"], "attribution": "recorded_role_not_verified_human_authorship"})
        provenance[citation] = {"event_id": event_id, "part": part["part"], "start": start, "end": end,
            "original_excerpt_json_sha256": identity(original), "redactions": mapping,
            "omitted_parts": [p["part"] for p in available if p != part],
            "omitted_characters": len(part["text"]) - len(original)}
    total = db.execute("SELECT COUNT(*) FROM events e JOIN sessions s ON s.current_revision=e.revision_id WHERE s.project=?", (project,)).fetchone()[0]
    scope_snapshot = [list(r) for r in db.execute("SELECT id,current_revision FROM sessions WHERE project=? ORDER BY id", (project,))]
    core = {"packet_version": PACKET_VERSION, "schema_version": VERSION, "project_id": identity(project),
            "evidence": sorted(evidence, key=lambda e: e["event_id"]), "observations": observations,
            "known_episodes": known, "update_episode_id": target,
            "coverage": {"current_project_records": total, "selected": len(selections), "included": len(evidence),
                         "excluded": sorted(excluded, key=lambda e: e["event_id"]), "unselected": total - len(selections),
                         "omissions": [{"event_id": k, "omitted_parts": v["omitted_parts"],
                             "omitted_characters": provenance[k]["omitted_characters"]} for k in sorted(provenance)
                             for v in [provenance[k]]],
                         "boundary": "Selected excerpts only. Raw records, headers, metadata, reasoning and unselected text stay local. Redaction cannot prove absence of secrets."}}
    # Local scope state participates in identity without exporting labels/paths.
    packet_id = identity([core, scope_snapshot])
    allowed = [target] if target else [e["episode_id"] for e in known] + [identity([packet_id, "episode", i]) for i in range(LIMITS["episodes"] - len(known))]
    packet = {**core, "packet_id": packet_id, "run_id": identity([packet_id, VERSION]), "allowed_episode_ids": allowed}
    if len(encode(packet).encode()) > LIMITS["packet_bytes"]:
        raise NormalizationError("analysis_narrow_selection_packet_max_160000_bytes")
    return packet, provenance, scope_snapshot
