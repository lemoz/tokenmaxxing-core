"""Local byte assertions, never shell execution or business-success claims."""

from datetime import datetime, timezone
import hashlib
import os
import re

from .normalize import signature
from .records import NormalizationError


def now():
    return datetime.now(timezone.utc).isoformat()


def artifact_check(scope, path, expected):
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise NormalizationError("expected_sha256_required")
    path = scope.selected(path)  # Reject out-of-scope before treating missing as an observation.
    result = {"path": str(path), "expected_sha256": expected.lower(), "actual_sha256": None,
              "bytes": None, "observed_at": now(), "status": "unavailable", "match": False,
              "assertion": "nonempty_file_sha256_equals_expected"}
    try:
        with scope.open(path) as stream:
            before = os.fstat(stream.fileno())
            digest, size = hashlib.sha256(), 0
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
            result.update(actual_sha256=digest.hexdigest(), bytes=size)
            after = os.fstat(stream.fileno())
            result["file_observation"] = {"before": list(signature(before)), "after": list(signature(after))}
            try:
                scope.verify(path, before)
                stable = signature(before) == signature(after)
            except (OSError, NormalizationError):
                stable = False
            result["status"] = ("changed" if not stable else "empty" if not size else
                                "match" if digest.hexdigest() == expected.lower() else "mismatch")
            result["match"] = result["status"] == "match"
    except FileNotFoundError:
        result["status"] = "missing"
    except OSError:
        result["status"] = "unavailable"
    return result
