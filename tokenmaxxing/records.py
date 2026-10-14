"""Source readers. JSONL retains only one line/record at a time."""

import base64
from dataclasses import dataclass
import hashlib
import json


class NormalizationError(Exception):
    """A fixed, content-free diagnostic suitable for CLI output."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("nonfinite_json_number")


def loads(raw):
    return json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False)


def pointer(key):
    return str(key).replace("~", "~0").replace("/", "~1")


@dataclass
class Record:
    locator: dict
    value: object
    category: str = "record"
    issues: tuple = ()


class Reader:
    def __init__(self, stream, source_format):
        self.stream = stream
        self.source_format = source_format
        self.digest = hashlib.sha256()
        self.bytes_read = 0

    def _account(self, raw):
        self.digest.update(raw)
        self.bytes_read += len(raw)

    def __iter__(self):
        if self.source_format in ("codex-jsonl", "claude-code-jsonl"):
            yield from self._jsonl()
        elif self.source_format == "codex-legacy-json":
            yield from self._legacy()
        else:
            raise NormalizationError("unsupported_source_format")

    def _jsonl(self):
        offset = 0
        for number, raw in enumerate(self.stream, 1):
            self._account(raw)
            locator = {"line": number, "byte_offset": offset,
                       "byte_length": len(raw), "pointer": ""}
            offset += len(raw)
            issues = () if raw.endswith(b"\n") else ("unterminated_final_line",)
            try:
                value = loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError, RecursionError):
                yield Record(locator, {"raw_bytes_base64": base64.b64encode(raw).decode("ascii")},
                             "invalid", issues + ("invalid_json_record",))
                continue
            yield Record(locator, value, issues=issues)

    def _legacy(self):
        # Deliberately in memory: documented first-slice limitation.
        raw = self.stream.read()
        self._account(raw)
        try:
            root = loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            raise NormalizationError("invalid_legacy_json") from None
        if (not isinstance(root, dict) or not isinstance(root.get("session"), dict)
                or not isinstance(root.get("items"), list)):
            raise NormalizationError("invalid_legacy_container")
        yield Record({"pointer": "/session"}, root["session"], "session")
        for key, value in root.items():
            if key not in {"session", "items"}:
                yield Record({"pointer": "/" + pointer(key)}, value, "extra_root",
                             ("unsupported_root_field",))
        for index, item in enumerate(root["items"]):
            yield Record({"pointer": f"/items/{index}", "item_index": index}, item)
