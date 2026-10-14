"""Canonical views retain observed values beside the complete source record."""

import hashlib

from . import ADAPTER_VERSION, SCHEMA_VERSION
from .records import encode

CALLS = {"custom_tool_call": "input", "function_call": "arguments"}
RESULTS = {"custom_tool_call_output", "function_call_output"}
PROJECTIONS = {"user_message", "agent_message", "item_completed"}
EVENT_METADATA = {"task_started", "task_complete", "token_count",
                  "thread_settings_applied"}
METADATA = {"session_meta", "turn_context", "world_state", "token_usage_record"}
DIAGNOSTICS = {
    "unterminated_final_line", "invalid_json_record", "unsupported_root_field",
    "unsupported_record_shape", "projection_not_reconciled", "unsupported_event_msg",
    "missing_or_invalid_role", "missing_or_invalid_content", "unsupported_content_block",
    "missing_tool_call_id", "missing_tool_name", "missing_tool_input", "missing_tool_arguments",
    "missing_tool_output", "invalid_tool_identity", "unsupported_response_item",
    "unsupported_record_type", "encrypted_evidence", "compacted_evidence",
    "missing_timestamp", "unsupported_timestamp_shape",
}


def metadata(record, source_format):
    if record.category == "session":
        return record.value
    value = record.value
    if (source_format == "codex-jsonl" and isinstance(value, dict)
            and value.get("type") == "session_meta"
            and isinstance(value.get("payload"), dict)):
        return value["payload"]
    return None


def event(record, source_format, session_id, revision, order):
    raw = record.value
    issues = list(record.issues)
    kind = "unsupported"
    canonical = {}
    data = raw
    data_pointer = record.locator["pointer"]
    timestamp = None
    timestamp_pointer = None
    if isinstance(raw, dict) and "timestamp" in raw:
        timestamp = raw["timestamp"]
        timestamp_pointer = data_pointer + "/timestamp"
    if source_format == "codex-jsonl" and isinstance(raw, dict):
        data = raw.get("payload")
        data_pointer += "/payload"
        top = raw.get("type")
    else:
        top = "legacy_item"
    subtype = data.get("type") if isinstance(data, dict) else None

    if record.category == "invalid":
        pass
    elif record.category == "extra_root":
        pass
    elif record.category == "session":
        kind = "source_metadata"
    elif not isinstance(raw, dict) or not isinstance(data, dict):
        issues.append("unsupported_record_shape")
    elif isinstance(top, str) and top in METADATA:
        kind = "source_metadata"
    elif top == "event_msg":
        if isinstance(subtype, str) and subtype in PROJECTIONS:
            kind = "source_projection"
            issues.append("projection_not_reconciled")
        elif isinstance(subtype, str) and subtype in EVENT_METADATA:
            kind = "source_metadata"
        else:
            issues.append("unsupported_event_msg")
    elif top == "response_item" or (top == "legacy_item" and source_format == "codex-legacy-json"):
        if subtype == "message":
            kind = "message"
            canonical = {k: data[k] for k in ("role", "content", "id", "status") if k in data}
            if not isinstance(data.get("role"), str):
                issues.append("missing_or_invalid_role")
            if not isinstance(data.get("content"), list):
                issues.append("missing_or_invalid_content")
            else:
                for block in data["content"]:
                    if (not isinstance(block, dict) or
                            block.get("type") not in ("input_text", "output_text") or
                            not isinstance(block.get("text"), str)):
                        issues.append("unsupported_content_block")
        elif isinstance(subtype, str) and subtype in CALLS:
            kind = "tool_call"
            field = CALLS[subtype]
            canonical = {k: data[k] for k in ("call_id", "name", field, "id", "status") if k in data}
            for key in ("call_id", "name", field):
                if key not in data:
                    issues.append("missing_tool_" + key)
            if any(k in data and not isinstance(data[k], str) for k in ("call_id", "name")):
                issues.append("invalid_tool_identity")
        elif isinstance(subtype, str) and subtype in RESULTS:
            kind = "tool_result"
            canonical = {k: data[k] for k in ("call_id", "name", "output", "id", "status") if k in data}
            for key in ("call_id", "output"):
                if key not in data:
                    issues.append("missing_tool_" + key)
            if "call_id" in data and not isinstance(data["call_id"], str):
                issues.append("invalid_tool_identity")
        else:
            issues.append("unsupported_response_item")
    else:
        issues.append("unsupported_record_type")

    if isinstance(data, dict):
        if "encrypted_content" in data:
            issues.append("encrypted_evidence")
        if subtype == "compaction":
            issues.append("compacted_evidence")
    if timestamp is None:
        issues.append("missing_timestamp")
    elif not isinstance(timestamp, str):
        issues.append("unsupported_timestamp_shape")

    identity = [SCHEMA_VERSION, ADAPTER_VERSION, source_format, session_id, revision,
                order, record.locator]
    return {
        "record_kind": "event", "event_id": "event:" + hashlib.sha256(encode(identity).encode()).hexdigest(),
        "session_id": session_id, "source_revision_id": revision, "order": order,
        "kind": kind, "timestamp": timestamp, "timestamp_pointer": timestamp_pointer,
        "locator": record.locator, "data_pointer": data_pointer,
        "source_type": raw.get("type") if isinstance(raw, dict) else None,
        "source_subtype": subtype, "canonical": canonical,
        "field_pointers": {key: data_pointer + "/" + key for key in canonical},
        "block_pointers": ([data_pointer + f"/content/{i}" for i in range(len(canonical["content"]))]
                           if isinstance(canonical.get("content"), list) else []),
        "outcome": "unknown", "diagnostics": sorted(set(issues)), "source_record": raw,
    }
