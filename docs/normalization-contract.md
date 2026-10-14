# Normalization contract v1 / adapter codex-1

This slice preserves source evidence and exposes conservative canonical views.
It does not establish accomplishments, task success, shipments, or work episodes.
All event outcomes are `unknown`, even with a source `status` or `task_complete`.
Source status values remain unchanged in `canonical` and/or `source_record`.
Codex alone is implemented. Claude, Hermes, persistent imports, evidence profiles,
and category leaderboards are later work.

## Artifact and ordering

An output is UTF-8 JSONL: one session header, ordered events, one summary footer.
Version strings appear in the header. JSONL inputs produce one event per physical
line, including invalid/blank lines. Legacy inputs produce one session metadata
event, one event per unknown root field, and one event per `items` entry. The
header's `source_records` uses these units, not canonical message counts.

Every event has an independent zero-based `order`. Input timestamps are retained
without timezone parsing or inference; missing timestamps are null with a
diagnostic. A legacy session timestamp is not copied onto its items. The original
record is preserved in `source_record`, including unmodeled keys, opaque strings,
IDs, roles, structured tool output, and encrypted material. Invalid JSONL bytes
are retained as base64 in that private field. Original JSON whitespace is not
preserved for valid records. The source byte hash identifies the exact revision.

JSONL locators contain a one-based line number, zero-based byte offset, byte
length including line ending, and a JSON Pointer relative to the line's object.
Legacy locators contain a JSON Pointer relative to the root; items also have a
zero-based item index. `data_pointer`, `field_pointers`, and `block_pointers`
locate the canonical payload, fields, and content blocks. No path in a transcript
is followed. Source references are descriptive, never execution instructions.

## Session and event identities

The identity is provider `codex` plus the exact nonempty string observed at
`session_meta.payload.id` or legacy `session.id`. The serialized `session_id` is
`codex:` followed by that string; `native_session_id` retains it verbatim. It is
never inferred from a filename, timestamp, or transcript text. Missing IDs yield
null, explicit diagnostics, and revision-scoped event identities; they do not
create a verified native session. Conflicting observed IDs abort the import.

The header records the first metadata record's observed ID aliases, timestamp,
and direct parent fields (`parent_thread_id`, `parent_session_id`, `parent_id`,
`forked_from_id`) if present, plus explicit missing fields. Extra metadata records
are retained as events and flagged. Other/nested parent representations remain
in the source record and are not interpreted. No parent relationship is invented.

`source_revision_id` is SHA-256 of exact input bytes. An `event_id` is `event:`
plus SHA-256 of compact, sorted-key, ASCII JSON encoding of the list:

```
[schema_version, adapter_version, source_format, session_id,
 source_revision_id, order, locator]
```

Locators in this list contain only within-source positions, never file paths.
Source path, filesystem times, and import observation time are not identity
inputs. No import time is recorded in v1. Unchanged input and an unchanged copy
produce identical session/event IDs and event counts. Legitimate identical text
at distinct positions remains distinct. A byte change creates a new revision and
new event IDs, including for an appended history. Differing revisions of one
native session are never merged automatically. Cross-format or semantically
equivalent re-encodings are not deduplicated.

Each invocation writes a fresh artifact and refuses an existing destination.
It does not append events. This is deterministic normalization, not database
idempotency, a persistent import registry, or cross-history deduplication.

## Canonicalization and coverage

* `response_item` and legacy item `message` become canonical messages. Preserve
  role and the full ordered content list, plus observed ID/status. Only
  `input_text`/`output_text` blocks containing string `text` are interpreted as
  supported text. Other blocks remain intact with coverage diagnostics.
* `custom_tool_call` preserves `input`; `function_call` preserves `arguments`.
  Both become tool calls with observed call ID, name, ID and status when present.
  Arguments are not reparsed or executed. Their matching `_output` variants
  become tool results and preserve the complete `output` value without flattening.
  Call/result relationships remain observed call IDs; v1 does not infer missing
  pairs, resolve ambiguous IDs, or parse output strings for success.
* `event_msg` user/agent messages and item completions become `source_projection`.
  They never add canonical messages/tools, even when no matching response item
  is present. They retain the entire record and a `projection_not_reconciled`
  diagnostic. V1 deliberately does not claim pairwise projection reconciliation
  or canonical completeness for projection-only sources. There is no text-based
  deduplication; repeated canonical messages are kept.
* Session metadata, turn context, world state, token usage, and the recognized
  event task/settings/usage variants become `source_metadata`. Retention is not
  semantic support for their internal fields. Unknown event variants and response
  items (including reasoning and compaction) are `unsupported`. Encrypted and
  compacted payloads get explicit evidence-gap diagnostics. Summaries are never
  expanded into hypothetical original messages.

Each record has exactly one event. Unmodeled fields stay in `source_record`;
`unsupported_fields` in the header explains that retention is not interpretation.
The fixed-vocabulary summary distinguishes canonical count, metadata, projections,
unsupported events, roles, block types and diagnostics. Unrecognized type/role
strings are bucketed as `other` so source-controlled text never leaks to stdout.
Diagnostic counts count affected events once per code, not affected blocks.
`coverage: partial` means a known gap; `no_known_gaps` is not corpus completeness.

## Resource, error, and privacy boundaries

JSONL uses two sequential passes on one open regular-file descriptor. Each pass
iterates lines and hashes bytes. The first discovers metadata/revision; the
second emits events incrementally. Memory depends on the largest line, parsed
record, encoded event, and small fixed-vocabulary counters, not transcript size.
There is no line or source-size cap. A single huge line can still exhaust memory.
Legacy JSON is parsed in memory on each pass, with no arbitrary cap; the first
container is released before the second pass. This is not a scalable legacy
parser. Gigabyte-scale legacy behavior has not been exercised.

The Python JSON decoder rejects duplicate keys, invalid UTF-8, non-finite JSON
constants and malformed JSON. Invalid JSONL lines produce retained unsupported
events and exit 3. Invalid legacy syntax/container shape aborts with exit 2 and
no published output. Resource errors likewise fail instead of truncating output.
A valid final JSONL record without newline is retained and diagnosed; this is
not proof of truncation. A file cut at a valid record boundary cannot be detected
without external completeness evidence.

Both passes' hashes, record counts, and file stat signatures must agree. Source
size/mtime/ctime/inode changes during reading abort publication. This is a local
change-detection check, not a lock against a hostile writer or a guarantee that a
live source will never change after validation.

Destinations must have an existing private parent directory (no group/other mode
bits, normally 0700). All existing destinations, including symlink/hard-link aliases,
are refused. A mode-0600 temporary file in the resolved parent is fsynced and
published by exclusive hard link. A concurrently created destination cannot be
overwritten. The temporary file is removed on ordinary errors; hard process
termination can leave a private `.tokenmaxxing-*` artifact. Directory durability
and hostile parent-directory rename races are not guaranteed. ACL privacy is the
operator's responsibility. No source is opened for writing.

`inspect` streams a normalized artifact, checks ordering/identity/envelope counts,
and compares a recomputed summary with the footer. It rejects incomplete outputs
and never prints source content. It does not authenticate canonical values or
replace source-to-output validation. Raw and normalized contents are private;
only synthetic tests and implementation belong in git. Real validation reports
contain exact selected paths, stats/hashes, counts and equality checks only.
