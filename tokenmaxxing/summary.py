"""Bounded counters: untrusted vocabulary never reaches public summaries."""

from collections import Counter

from .events import CALLS, RESULTS, PROJECTIONS, EVENT_METADATA, METADATA, DIAGNOSTICS
from .records import NormalizationError

KINDS = {"message", "tool_call", "tool_result", "source_metadata", "source_projection", "unsupported"}
ROLES = {"system", "developer", "user", "assistant", "tool", "function"}
SOURCE_TYPES = METADATA | {"event_msg", "response_item", "message", "reasoning", "compaction"} | set(CALLS) | RESULTS
SUBTYPES = SOURCE_TYPES | PROJECTIONS | EVENT_METADATA
SESSION_DIAGNOSTICS = {"missing_native_session_id", "missing_session_metadata", "missing_session_timestamp",
                       "unsupported_session_identifier", "unsupported_session_timestamp",
                       "multiple_session_metadata_records", "empty_source"}


def bucket(value, known):
    if value is None:
        return "missing"
    return value if isinstance(value, str) and value in known else "other"


class Summary:
    def __init__(self, session_diagnostics):
        if (not isinstance(session_diagnostics, list) or
                any(not isinstance(x, str) or x not in SESSION_DIAGNOSTICS for x in session_diagnostics)):
            raise NormalizationError("invalid_session_diagnostics")
        self.session_diagnostics = sorted(set(session_diagnostics))
        self.events = 0
        self.kinds = Counter()
        self.roles = Counter()
        self.types = Counter()
        self.subtypes = Counter()
        self.blocks = Counter()
        self.diagnostics = Counter()

    def add(self, value):
        kind = value.get("kind")
        if not isinstance(kind, str) or kind not in KINDS:
            raise NormalizationError("invalid_event_kind")
        canonical = value.get("canonical")
        diagnostics = value.get("diagnostics")
        if not isinstance(canonical, dict) or not isinstance(diagnostics, list):
            raise NormalizationError("invalid_event_shape")
        if any(not isinstance(x, str) or x not in DIAGNOSTICS for x in diagnostics):
            raise NormalizationError("invalid_event_diagnostics")
        self.events += 1
        self.kinds[kind] += 1
        self.types[bucket(value.get("source_type"), SOURCE_TYPES)] += 1
        self.subtypes[bucket(value.get("source_subtype"), SUBTYPES)] += 1
        self.diagnostics.update(set(diagnostics))
        if kind == "message":
            self.roles[bucket(canonical.get("role"), ROLES)] += 1
            content = canonical.get("content")
            if isinstance(content, list):
                for block in content:
                    self.blocks[bucket(block.get("type") if isinstance(block, dict) else None,
                                       {"input_text", "output_text"})] += 1

    def result(self):
        return {
            "events": self.events, "canonical_events": sum(self.kinds[k] for k in ("message", "tool_call", "tool_result")),
            "event_types": dict(self.kinds), "roles": dict(self.roles),
            "source_types": dict(self.types), "source_subtypes": dict(self.subtypes),
            "content_block_types": dict(self.blocks), "diagnostics": dict(self.diagnostics),
            "session_diagnostics": self.session_diagnostics,
            "coverage": "partial" if self.diagnostics or self.session_diagnostics else "no_known_gaps",
            "outcome": "unknown",
        }
