"""Claude JSONL evidence views; no transcript execution or cross-file inference."""

import hashlib

from . import SCHEMA_VERSION
from .records import NormalizationError, encode
from .summary import Summary

FORMAT = "claude-code-jsonl"
ADAPTER_VERSION = "claude-1"
METADATA = {"system", "mode", "permission-mode", "file-history-snapshot", "atis-latch",
            "attachment", "last-prompt", "ai-title", "queue-operation"}
MESSAGE_FIELDS = ("role", "content", "id", "type", "stop_reason", "stop_sequence", "usage", "model", "status")
RECORD_FIELDS = ("sessionId", "uuid", "parentUuid", "isSidechain", "agentId",
                 "sourceToolAssistantUUID", "sourceToolUseID", "isMeta")
SESSION_CODES = {
    "empty_source", "missing_native_session_id", "missing_session_timestamp",
    "unsupported_session_timestamp", "unsupported_session_identifier",
    "unsupported_agent_identifier", "unsupported_session_id_alias",
    "unsupported_thread_metadata", "ambiguous_thread_identity",
}
DIAGNOSTICS = {
    "unterminated_final_line", "invalid_json_record", "unsupported_record_shape",
    "unsupported_record_type", "missing_timestamp", "unsupported_timestamp_shape",
    "missing_or_invalid_role", "missing_or_invalid_content", "unsupported_content_block",
    "missing_tool_call_id", "missing_tool_name", "missing_tool_input", "missing_tool_output",
    "missing_tool_is_error", "invalid_tool_identity", "unsupported_tool_input_shape",
    "unsupported_tool_output_shape", "unsupported_tool_is_error", "compacted_evidence",
    "redacted_evidence", "encrypted_evidence", "projection_not_reconciled",
    "unsupported_session_id_alias",
}


def nonempty_string(value):
    return isinstance(value, str) and bool(value)


def session_identity(root, agent, kind):
    if kind == "main":
        return "claude:" + root
    if kind == "subagent":
        # Structured encoding prevents ambiguous delimiters in arbitrary opaque IDs.
        return "claude-subagent:" + hashlib.sha256(encode([root, agent]).encode()).hexdigest()
    return None


def header(reader, source, info):
    native = {"sessionId": None, "agentId": None}
    observed, evidence, native_evidence = {}, {}, {}
    issues = set()
    count = conversations = 0
    sidechain = None
    saw_agent_field = False
    main_messages = child_records = True
    for record in reader:
        count += 1
        raw = record.value
        if record.category == "invalid" or not isinstance(raw, dict):
            continue
        for key in ("sessionId", "agentId", "isSidechain", "timestamp"):
            if key in raw and key not in observed:
                observed[key] = raw[key]
                evidence[key] = {"locator": record.locator, "field_pointer": "/" + key}
        for key, diagnostic in (("sessionId", "unsupported_session_identifier"),
                                ("agentId", "unsupported_agent_identifier")):
            if key not in raw:
                continue
            value = raw[key]
            if not nonempty_string(value):
                issues.add(diagnostic)
            elif native[key] is not None and native[key] != value:
                raise NormalizationError("conflicting_native_session_ids" if key == "sessionId"
                                         else "conflicting_native_agent_ids")
            else:
                native[key] = value
                if key not in native_evidence:
                    native_evidence[key] = {"locator": record.locator, "field_pointer": "/" + key}
        if "session_id" in raw:
            issues.add("unsupported_session_id_alias")
        saw_agent_field |= "agentId" in raw
        if "isSidechain" in raw:
            value = raw["isSidechain"]
            if type(value) is not bool:
                issues.add("unsupported_thread_metadata")
            elif sidechain is not None and sidechain != value:
                raise NormalizationError("conflicting_claude_sidechain_metadata")
            else:
                sidechain = value
        child_records &= (raw.get("isSidechain") is True and nonempty_string(raw.get("agentId")))
        if raw.get("type") in ("user", "assistant"):
            conversations += 1
            main_messages &= (raw.get("isSidechain") is False and
                              nonempty_string(raw.get("sessionId")) and "agentId" not in raw)
            # Missing root context on a conversational record is ambiguous, too.
            child_records &= nonempty_string(raw.get("sessionId"))
    root, agent = native["sessionId"], native["agentId"]
    if agent is not None and sidechain is False:
        raise NormalizationError("conflicting_claude_thread_metadata")
    identity_errors = {"unsupported_session_identifier", "unsupported_agent_identifier",
                       "unsupported_thread_metadata"}
    kind = "unresolved"
    if root and conversations and not issues.intersection(identity_errors):
        if main_messages and not saw_agent_field:
            kind = "main"
        elif child_records and agent and sidechain is True:
            kind = "subagent"
    if kind == "unresolved":
        issues.add("ambiguous_thread_identity")
    if not root:
        issues.add("missing_native_session_id")
    if not count:
        issues.add("empty_source")
    if observed.get("timestamp") is None:
        issues.add("missing_session_timestamp")
    elif not isinstance(observed["timestamp"], str):
        issues.add("unsupported_session_timestamp")
    return {
        "record_kind": "session", "schema_version": SCHEMA_VERSION, "adapter_version": ADAPTER_VERSION,
        "provider": "claude", "session_id": session_identity(root, agent, kind),
        "native_session_id": root, "native_agent_id": agent, "session_kind": kind,
        "parent_session_id": "claude:" + root if kind == "subagent" else None,
        "parent_session_relation": "observed_main_session_context" if kind == "subagent" else None,
        "observed_metadata": observed, "metadata_evidence": evidence,
        "native_identity_evidence": native_evidence,
        "missing_fields": [key for key in ("sessionId", "agentId", "isSidechain", "timestamp") if key not in observed],
        "unsupported_fields": "Unmodeled fields are retained in source_record; no semantic interpretation is claimed.",
        "source": {"reference": str(source), "format": reader.source_format,
                   "sha256": reader.digest.hexdigest(), "bytes": reader.bytes_read,
                   "size": info.st_size, "mtime_ns": info.st_mtime_ns},
        "source_records": count, "diagnostics": sorted(issues),
    }


def evidence_gaps(value, issues):
    """Recognize explicit markers only; opaque strings are never searched/parsed."""
    if not isinstance(value, dict):
        return
    if (value.get("type") in ("summary", "compaction") or
            value.get("subtype") == "compact_boundary" or
            "compactMetadata" in value or value.get("isCompactSummary") is True):
        issues.add("compacted_evidence")
    if value.get("type") == "redacted_thinking" or "redacted_content" in value:
        issues.add("redacted_evidence")
    if "encrypted_content" in value:
        issues.add("encrypted_evidence")


def interaction(block, index, pointer, issues):
    call = block["type"] == "tool_use"
    fields = {"call_id": "id", "name": "name", "input": "input"} if call else {
        "call_id": "tool_use_id", "output": "content", "is_error": "is_error"}
    canonical = {key: block[source] for key, source in fields.items() if source in block}
    for key in fields:
        if key not in canonical:
            issues.add("missing_tool_" + key)
    if any(key in canonical and not nonempty_string(canonical[key]) for key in ("call_id", "name")):
        issues.add("invalid_tool_identity")
    if call and "input" in canonical and not isinstance(canonical["input"], dict):
        issues.add("unsupported_tool_input_shape")
    if not call:
        output = canonical.get("output")
        if "output" in canonical and not isinstance(output, (str, list)):
            issues.add("unsupported_tool_output_shape")
        if "is_error" in canonical and type(canonical["is_error"]) is not bool:
            issues.add("unsupported_tool_is_error")
        if isinstance(output, list):
            for item in output:
                evidence_gaps(item, issues)
                if (not isinstance(item, dict) or item.get("type") != "text" or
                        not isinstance(item.get("text"), str)):
                    issues.add("unsupported_content_block")
    return {
        "kind": "tool_call" if call else "tool_result", "block_index": index,
        "block_pointer": pointer, "canonical": canonical,
        "field_pointers": {key: pointer + "/" + fields[key] for key in canonical},
        "output_block_pointers": ([pointer + f"/content/{i}" for i in range(len(canonical["output"]))]
                                  if isinstance(canonical.get("output"), list) else []),
        "outcome": "unknown",
    }


def event(record, source_format, session_id, revision, order):
    raw = record.value
    issues = set(record.issues)
    kind, canonical, interactions = "unsupported", {}, []
    data_pointer, subtype = "", None
    timestamp, timestamp_pointer = None, None
    observed = {}
    if record.category != "invalid" and isinstance(raw, dict):
        top = raw.get("type")
        observed = {key: raw[key] for key in RECORD_FIELDS if key in raw}
        if "timestamp" in raw:
            timestamp, timestamp_pointer = raw["timestamp"], "/timestamp"
        if "session_id" in raw:
            issues.add("unsupported_session_id_alias")
        evidence_gaps(raw, issues)
        if top in ("user", "assistant") and "compacted_evidence" not in issues:
            data_pointer = "/message"
            message = raw.get("message")
            if not isinstance(message, dict):
                issues.add("unsupported_record_shape")
            else:
                kind = "message"
                subtype = message.get("type")
                evidence_gaps(message, issues)
                canonical = {key: message[key] for key in MESSAGE_FIELDS if key in message}
                if not nonempty_string(message.get("role")):
                    issues.add("missing_or_invalid_role")
                content = message.get("content")
                if isinstance(content, list):
                    for index, block in enumerate(content):
                        evidence_gaps(block, issues)
                        if not isinstance(block, dict):
                            issues.add("unsupported_content_block")
                            continue
                        block_type = block.get("type")
                        if block_type in ("tool_use", "tool_result"):
                            interactions.append(interaction(block, index, f"/message/content/{index}", issues))
                        elif block_type == "text" and isinstance(block.get("text"), str):
                            pass
                        elif (block_type == "thinking" and isinstance(block.get("thinking"), str) and
                              ("signature" not in block or isinstance(block["signature"], str))):
                            pass
                        else:
                            issues.add("unsupported_content_block")
                elif not isinstance(content, str):
                    issues.add("missing_or_invalid_content")
        elif isinstance(top, str) and top in METADATA:
            kind = "source_metadata"
            subtype = raw.get("subtype")
        else:
            issues.add("unsupported_record_type")
            if top == "progress":
                issues.add("projection_not_reconciled")
    elif record.category != "invalid":
        issues.add("unsupported_record_shape")
    if timestamp is None:
        issues.add("missing_timestamp")
    elif not isinstance(timestamp, str):
        issues.add("unsupported_timestamp_shape")
    identity = [SCHEMA_VERSION, ADAPTER_VERSION, source_format, session_id, revision, order, record.locator]
    return {
        "record_kind": "event", "event_id": "event:" + hashlib.sha256(encode(identity).encode()).hexdigest(),
        "session_id": session_id, "source_revision_id": revision, "order": order, "kind": kind,
        "timestamp": timestamp, "timestamp_pointer": timestamp_pointer, "locator": record.locator,
        "data_pointer": data_pointer, "source_type": raw.get("type") if isinstance(raw, dict) else None,
        "source_subtype": subtype, "canonical": canonical,
        "field_pointers": {key: data_pointer + "/" + key for key in canonical},
        "block_pointers": ([f"/message/content/{i}" for i in range(len(canonical["content"]))]
                           if isinstance(canonical.get("content"), list) else []),
        "observed_metadata": observed, "metadata_pointers": {key: "/" + key for key in observed},
        "interactions": interactions, "outcome": "unknown", "diagnostics": sorted(issues), "source_record": raw,
    }


class ClaudeSummary(Summary):
    session_codes = SESSION_CODES
    diagnostic_codes = DIAGNOSTICS
    source_types = METADATA | {"user", "assistant", "summary", "compaction", "progress"}
    subtypes_vocabulary = {"message", "compact_boundary"}
    block_types = {"text", "thinking", "tool_use", "tool_result", "image", "document", "redacted_thinking"}

    def __init__(self, session_diagnostics):
        super().__init__(session_diagnostics)
        self.interaction_counts = {"tool_call": 0, "tool_result": 0}

    def add(self, value):
        interactions = value.get("interactions")
        if (value.get("kind") not in ("message", "source_metadata", "unsupported") or
                not isinstance(value.get("canonical"), dict) or not isinstance(interactions, list) or
                (interactions and value.get("kind") != "message")):
            raise NormalizationError("invalid_claude_interactions")
        content = value.get("canonical", {}).get("content")
        expected_indices = ([index for index, block in enumerate(content)
                             if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result")]
                            if value.get("kind") == "message" and isinstance(content, list) else [])
        if len(interactions) != len(expected_indices):
            raise NormalizationError("invalid_claude_interactions")
        for index, item in zip(expected_indices, interactions):
            expected_kind = "tool_call" if content[index]["type"] == "tool_use" else "tool_result"
            if (not isinstance(item, dict) or item.get("kind") not in ("tool_call", "tool_result") or
                    item["kind"] != expected_kind or
                    item.get("outcome") != "unknown" or not isinstance(item.get("canonical"), dict) or
                    type(item.get("block_index")) is not int or item["block_index"] != index or
                    item.get("block_pointer") != f'/message/content/{item["block_index"]}' or
                    not isinstance(item.get("field_pointers"), dict) or
                    not isinstance(item.get("output_block_pointers"), list)):
                raise NormalizationError("invalid_claude_interactions")
            self.interaction_counts[item["kind"]] += 1
        super().add(value)

    def result(self):
        return {**super().result(), "tool_interactions": sum(self.interaction_counts.values()),
                "tool_interaction_types": dict(self.interaction_counts)}


def valid_header(value):
    """Check Claude-specific identity consistency; source authenticity is separate."""
    kind = value.get("session_kind")
    root, agent = value.get("native_session_id"), value.get("native_agent_id")
    if (kind not in ("main", "subagent", "unresolved") or "native_agent_id" not in value or
            (agent is not None and not nonempty_string(agent)) or
            "parent_session_id" not in value or "parent_session_relation" not in value):
        return False
    if kind == "main" and (not root or agent is not None):
        return False
    if kind == "subagent" and (not root or not agent):
        return False
    return (value["session_id"] == session_identity(root, agent, kind) and
            value["parent_session_id"] == ("claude:" + root if kind == "subagent" else None) and
            value["parent_session_relation"] == ("observed_main_session_context" if kind == "subagent" else None))
