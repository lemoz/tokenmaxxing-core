"""Two-pass normalization, source change detection and exclusive publication."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import tempfile

from . import ADAPTER_VERSION, SCHEMA_VERSION
from . import claude
from .events import event, metadata
from .records import NormalizationError, Reader, encode, loads
from .summary import Summary

FORMATS = ("codex-jsonl", "codex-legacy-json", claude.FORMAT)


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def regular_source(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise NormalizationError("source_not_regular_file")
        yield stream


def _header(reader, source, info):
    if reader.source_format == claude.FORMAT:
        return claude.header(reader, source, info)
    selected = None
    native_id = None
    count = 0
    meta_count = 0
    diagnostics = set()
    for record in reader:
        count += 1
        meta = metadata(record, reader.source_format)
        if meta is not None:
            meta_count += 1
            observed = meta.get("id")
            if isinstance(observed, str) and observed:
                if native_id is not None and native_id != observed:
                    raise NormalizationError("conflicting_native_session_ids")
                native_id = observed
            else:
                diagnostics.add("unsupported_session_identifier")
            if selected is None:
                selected = {k: meta[k] for k in ("id", "session_id", "thread_id", "timestamp",
                            "parent_thread_id", "parent_session_id", "parent_id", "forked_from_id") if k in meta}
    if not meta_count:
        diagnostics.add("missing_session_metadata")
    if meta_count > 1:
        diagnostics.add("multiple_session_metadata_records")
    if not native_id:
        diagnostics.add("missing_native_session_id")
    if not count:
        diagnostics.add("empty_source")
    observed = selected or {}
    if observed.get("timestamp") is None:
        diagnostics.add("missing_session_timestamp")
    elif not isinstance(observed["timestamp"], str):
        diagnostics.add("unsupported_session_timestamp")
    return {
        "record_kind": "session", "schema_version": SCHEMA_VERSION, "adapter_version": ADAPTER_VERSION,
        "provider": "codex", "session_id": "codex:" + native_id if native_id else None,
        "native_session_id": native_id, "observed_metadata": observed,
        "missing_fields": [k for k in ("id", "timestamp", "parent_thread_id", "parent_session_id") if k not in observed],
        "unsupported_fields": "Unmodeled fields are retained in source_record; no semantic interpretation is claimed.",
        "source": {"reference": str(source), "format": reader.source_format,
                   "sha256": reader.digest.hexdigest(), "bytes": reader.bytes_read,
                   "size": info.st_size, "mtime_ns": info.st_mtime_ns},
        "source_records": count, "diagnostics": sorted(diagnostics),
    }


def _destination(source, output):
    output = Path(output).absolute()
    # Refuse every existing destination, including dangling symlinks. Nothing
    # is opened for truncation. Publication also uses an exclusive hard link.
    if output.is_symlink() or output.exists():
        raise NormalizationError("output_already_exists")
    if output.resolve() == source.resolve():
        raise NormalizationError("output_aliases_input")
    parent = output.parent.resolve(strict=True)
    if not parent.is_dir() or parent.stat().st_mode & 0o077:
        raise NormalizationError("output_directory_must_be_private_0700")
    return parent / output.name


def normalize(source, output, source_format):
    """Write one new private artifact; return only a content-free summary.

    No overwrite, append, database writes, auto-discovery, or tool execution.
    Errors before publication remove this call's temporary output.
    """
    if source_format not in FORMATS:
        raise NormalizationError("unsupported_source_format")
    source = Path(source).resolve(strict=True)
    destination = _destination(source, output)
    temporary = None
    try:
        with regular_source(source) as stream:
            initial = os.fstat(stream.fileno())
            first = Reader(stream, source_format)
            header = _header(first, source, initial)
            if signature(initial) != signature(os.fstat(stream.fileno())):
                raise NormalizationError("source_changed_during_import")
            stream.seek(0)
            second = Reader(stream, source_format)
            summary_class = claude.ClaudeSummary if source_format == claude.FORMAT else Summary
            make_event = claude.event if source_format == claude.FORMAT else event
            summary = summary_class(header["diagnostics"])
            descriptor, temporary = tempfile.mkstemp(prefix=".tokenmaxxing-", dir=destination.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as out:
                out.write(encode(header) + "\n")
                for order, record in enumerate(second):
                    value = make_event(record, source_format, header["session_id"], header["source"]["sha256"], order)
                    summary.add(value)
                    out.write(encode(value) + "\n")
                if (second.digest.hexdigest() != first.digest.hexdigest()
                        or summary.events != header["source_records"]
                        or signature(initial) != signature(os.fstat(stream.fileno()))
                        or signature(initial) != signature(source.stat())):
                    raise NormalizationError("source_changed_during_import")
                result = summary.result()
                out.write(encode({"record_kind": "summary", **result}) + "\n")
                out.flush()
                os.fsync(out.fileno())
            os.link(temporary, destination)  # Atomic create-if-absent, never replace.
        return result
    finally:
        if temporary is not None:
            os.unlink(temporary)


def inspect(source):
    """Recompute a content-free summary; reject incomplete/altered envelopes.

    This verifies internal structure, not authenticity or source equality.
    """
    try:
        with regular_source(source) as stream:
            header = loads(stream.readline().decode("utf-8"))
            if (not isinstance(header, dict) or header.get("record_kind") != "session"
                    or header.get("schema_version") != SCHEMA_VERSION
                    or any(key not in header or (header[key] is not None and
                           (not isinstance(header[key], str) or not header[key]))
                           for key in ("session_id", "native_session_id"))
                    or not isinstance(header.get("source"), dict)
                    or header["source"].get("format") not in FORMATS
                    or not isinstance(header["source"].get("sha256"), str)
                    or len(header["source"]["sha256"]) != 64
                    or any(char not in "0123456789abcdef" for char in header["source"]["sha256"])
                    or type(header.get("source_records")) is not int
                    or header["source_records"] < 0):
                raise NormalizationError("invalid_normalized_header")
            is_claude = header["source"]["format"] == claude.FORMAT
            adapter_version = claude.ADAPTER_VERSION if is_claude else ADAPTER_VERSION
            if (header.get("adapter_version") != adapter_version or
                    header.get("provider") != ("claude" if is_claude else "codex") or
                    (is_claude and not claude.valid_header(header))):
                raise NormalizationError("invalid_normalized_header")
            summary_class = claude.ClaudeSummary if is_claude else Summary
            summary = summary_class(header.get("diagnostics"))
            footer = None
            for raw in stream:
                value = loads(raw.decode("utf-8"))
                if not isinstance(value, dict) or footer is not None:
                    raise NormalizationError("invalid_normalized_sequence")
                if value.get("record_kind") == "summary":
                    footer = value
                    continue
                if (value.get("record_kind") != "event" or value.get("order") != summary.events
                        or value.get("session_id") != header["session_id"]
                        or value.get("source_revision_id") != header["source"]["sha256"]
                        or value.get("outcome") != "unknown"):
                    raise NormalizationError("invalid_normalized_event")
                identity = [SCHEMA_VERSION, adapter_version, header["source"]["format"], header["session_id"],
                            header["source"]["sha256"], value["order"], value["locator"]]
                expected = "event:" + hashlib.sha256(encode(identity).encode()).hexdigest()
                if value.get("event_id") != expected:
                    raise NormalizationError("invalid_event_identity")
                summary.add(value)
            result = summary.result()
            if footer != {"record_kind": "summary", **result} or summary.events != header["source_records"]:
                raise NormalizationError("missing_or_inconsistent_summary")
            return result
    except (ValueError, UnicodeError, KeyError, TypeError, RecursionError):
        raise NormalizationError("invalid_normalized_structure") from None
