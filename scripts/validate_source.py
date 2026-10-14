"""Opt-in real-input proof. Never print or report transcript values.

All assertions use fixed messages (no value repr); output artifacts are private.
No discovery: the only original opened is the explicit --source argument.
"""

import argparse
from collections import Counter
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tokenmaxxing.records import NormalizationError


def check(condition, diagnostic):
    if not condition:
        raise NormalizationError(diagnostic)


def fingerprint(path):
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    check((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
          (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "source_changed_during_fingerprint")
    return {"sha256": digest.hexdigest(), "bytes": after.st_size,
            "mtime_ns": after.st_mtime_ns, "ctime_ns": after.st_ctime_ns,
            "device": after.st_dev, "inode": after.st_ino}


def private_file(path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "wb")


def resolve(value, pointer):
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def source_records(source, fmt):
    with source.open("rb") as stream:
        if fmt == "codex-jsonl":
            offset = 0
            for line, raw in enumerate(stream, 1):
                value = json.loads(raw)
                yield value, value, {"line": line, "byte_offset": offset,
                                     "byte_length": len(raw), "pointer": ""}
                offset += len(raw)
        else:
            root = json.load(stream)
            yield root, root["session"], {"pointer": "/session"}
            for key, value in root.items():
                if key not in ("session", "items"):
                    yield root, value, {"pointer": "/" + key.replace("~", "~0").replace("/", "~1")}
            for index, value in enumerate(root["items"]):
                yield root, value, {"pointer": f"/items/{index}", "item_index": index}


def compare_source(source, output, fmt, baseline):
    checks = Counter()
    with output.open() as stream:
        header = json.loads(next(stream))
        check(header["source"]["sha256"] == baseline["sha256"], "source_hash_mismatch")
        check(header["source"]["reference"] == str(source), "source_reference_mismatch")
        for root, raw, locator in source_records(source, fmt):
            event = json.loads(next(stream))
            check(event["record_kind"] == "event", "missing_event")
            check(event["locator"] == locator, "record_locator_mismatch")
            check(resolve(root, locator["pointer"]) == raw == event["source_record"], "source_record_mismatch")
            check(event["order"] == checks["records"], "event_order_mismatch")
            check(event["outcome"] == "unknown", "inferred_outcome")
            checks["records"] += 1
            if fmt == "codex-jsonl":
                data = raw.get("payload", {})
                canonical_eligible = raw.get("type") == "response_item"
                meta = data if raw.get("type") == "session_meta" else None
            else:
                data = raw
                canonical_eligible = "item_index" in locator
                meta = raw if locator["pointer"] == "/session" else None
            if meta is not None:
                check(header["native_session_id"] == meta["id"], "native_id_mismatch")
                check(header["session_id"] == "codex:" + meta["id"], "session_identity_mismatch")
                for key in ("timestamp", "parent_thread_id", "parent_session_id", "parent_id", "forked_from_id"):
                    if key in meta:
                        check(header["observed_metadata"][key] == meta[key], "metadata_mismatch")
                checks["session_metadata"] += 1
            subtype = data.get("type") if isinstance(data, dict) else None
            expected_kind = None
            fields = ()
            if canonical_eligible:
                if subtype == "message":
                    expected_kind, fields = "message", ("role", "content", "id", "status")
                elif subtype in ("function_call", "custom_tool_call"):
                    expected_kind = "tool_call"
                    fields = ("call_id", "name", "arguments" if subtype == "function_call" else "input", "id", "status")
                elif subtype in ("function_call_output", "custom_tool_call_output"):
                    expected_kind, fields = "tool_result", ("call_id", "name", "output", "id", "status")
            if expected_kind:
                check(event["kind"] == expected_kind, "canonical_kind_mismatch")
                expected = {key: data[key] for key in fields if key in data}
                check(event["canonical"] == expected, "canonical_fields_mismatch")
                for key, value in expected.items():
                    check(resolve(root, event["field_pointers"][key]) == value, "canonical_pointer_mismatch")
                    checks["canonical_fields"] += 1
                checks[expected_kind] += 1
                if expected_kind == "message":
                    check(len(event["block_pointers"]) == len(data["content"]), "block_count_mismatch")
                    for pointer, block in zip(event["block_pointers"], data["content"]):
                        check(resolve(root, pointer) == block, "block_pointer_mismatch")
                        checks["content_blocks"] += 1
            else:
                check(event["canonical"] == {}, "unexpected_canonical_event")
            if fmt == "codex-jsonl" and raw.get("type") == "event_msg" and subtype in ("user_message", "agent_message", "item_completed"):
                check(event["kind"] == "source_projection", "projection_double_counted")
                checks["projections"] += 1
            expected_timestamp = raw.get("timestamp") if isinstance(raw, dict) else None
            check(event["timestamp"] == expected_timestamp, "timestamp_mismatch")
        footer = json.loads(next(stream))
        check(footer["record_kind"] == "summary", "extra_or_missing_event")
        check(not stream.read(1), "extra_output_records")
        check(header["source_records"] == checks["records"] == footer["events"], "record_coverage_mismatch")
    return dict(checks)


def compare_imports(first, other, same_path):
    with first.open() as left, other.open() as right:
        left_header, right_header = json.loads(next(left)), json.loads(next(right))
        if same_path:
            check(left_header == right_header, "repeat_header_mismatch")
        else:
            for key in ("session_id", "native_session_id", "observed_metadata"):
                check(left_header[key] == right_header[key], "copy_identity_mismatch")
        for a, b in itertools.zip_longest(left, right):
            check(a == b, "repeat_or_copy_event_mismatch")


def run_cli(arguments, commands):
    command = [sys.executable, "-m", "tokenmaxxing", *arguments]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    check(result.returncode in (0, 3), "cli_failed_no_content_echoed")
    check(not result.stderr, "unexpected_cli_stderr")
    summary = json.loads(result.stdout)
    commands.append({"argv": command, "exit_code": result.returncode, "summary": summary})
    return summary


def validate(source, fmt, directory):
    source = source.resolve(strict=True)
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    check(directory.stat().st_mode & 0o077 == 0, "proof_directory_not_private")
    before = fingerprint(source)
    commands = []
    first, repeat, copied_out = (directory / name for name in ("first.normalized.jsonl", "repeat.normalized.jsonl", "copy.normalized.jsonl"))
    summary = run_cli(["normalize", "--source", str(source), "--format", fmt, "--output", str(first)], commands)
    assertions = compare_source(source, first, fmt, before)
    repeated_summary = run_cli(["normalize", "--source", str(source), "--format", fmt, "--output", str(repeat)], commands)
    compare_imports(first, repeat, same_path=True)
    copy = directory / ("unchanged-source-copy.jsonl" if fmt == "codex-jsonl" else "unchanged-source-copy.json")
    with source.open("rb") as src, private_file(copy) as dst:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            dst.write(chunk)
    check(fingerprint(copy)["sha256"] == before["sha256"], "copy_hash_mismatch")
    copied_summary = run_cli(["normalize", "--source", str(copy), "--format", fmt, "--output", str(copied_out)], commands)
    compare_imports(first, copied_out, same_path=False)
    inspected_summary = run_cli(["inspect", "--source", str(first)], commands)
    check(summary == repeated_summary == copied_summary == inspected_summary, "summary_mismatch")
    after = fingerprint(source)
    check(before == after, "original_source_changed")
    for output in (first, repeat, copy, copied_out):
        check(output.stat().st_mode & 0o777 == 0o600, "output_not_private")
    report = {"source": str(source), "format": fmt, "before": before, "after": after,
              "source_unchanged": True, "source_to_output_assertions": assertions,
              "stable_repeat": True, "unchanged_copy_identity": True,
              "private_modes": True, "summary": summary, "commands": commands,
              "gaps": ["Selected-source proof only; no corpus-wide claim.",
                       "Reasoning, projections, encrypted/compacted evidence remain explicit gaps.",
                       "Legacy containers are in memory; no gigabyte legacy trial.",
                       "Tool outcomes and task success remain unknown."]}
    with private_file(directory / "validation-report.json") as stream:
        stream.write((json.dumps(report, sort_keys=True, indent=2) + "\n").encode())
    return {"report": str(directory / "validation-report.json"), "records": assertions["records"],
            "canonical_events": summary["canonical_events"], "source_unchanged": True,
            "stable_repeat": True, "unchanged_copy_identity": True, "coverage": summary["coverage"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--format", choices=("codex-jsonl", "codex-legacy-json"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = validate(args.source, args.format, args.output_dir)
    except NormalizationError as error:
        print(json.dumps({"validation": "failed", "diagnostic": str(error)}))
        return 2
    except Exception:
        # Never reveal exception repr or source values when validating private inputs.
        print('{"validation":"failed","diagnostic":"validation_exception_no_content_echoed"}')
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
