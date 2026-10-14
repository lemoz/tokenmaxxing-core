# Normalization contract v1 / adapters codex-1 and claude-1

This slice preserves source evidence and exposes conservative canonical views.
It does not establish accomplishments, task success, shipments, or work episodes.
All event and interaction outcomes are `unknown`, even with a source `status`,
`task_complete`, `stop_reason` or `is_error`.
Source status values remain unchanged in `canonical` and/or `source_record`.
Explicit formats are `codex-jsonl`, `codex-legacy-json` and `claude-code-jsonl`.
This document covers standalone Codex/Claude normalization. The private pilot's
persistent imports, evidence ledger and experimental Hermes adapter are specified
in [Private pilot operation and acceptance](private-pilot.md). Category leaderboards
remain future work. The Claude view is additive; Codex bytes, identities and
summaries retain the `codex-1` contract.

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

For Codex, identity is provider `codex` plus the exact nonempty string observed at
`session_meta.payload.id` or legacy `session.id`. The serialized `session_id` is
`codex:` followed by that string; `native_session_id` retains it verbatim. It is
never inferred from a filename, timestamp, or transcript text. Missing IDs yield
null, explicit diagnostics, and revision-scoped event identities; they do not
create a verified native session. Conflicting observed IDs abort the import.

The Codex header records the first metadata record's observed ID aliases, timestamp,
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

## Codex canonicalization and coverage

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

## Claude identity and relationship evidence

`claude-code-jsonl` uses provider `claude`, adapter `claude-1`, and the same
two-pass reader, event identity formula, source-integrity and private-output
rules. One physical source line still produces exactly one event. Claude does
not use a special session metadata line or infer identity from a filename.

`native_session_id` retains the one nonempty string observed as root `sessionId`
verbatim. `native_agent_id` independently retains observed `agentId`. Conflicting
nonempty `sessionId` values fail with `conflicting_native_session_ids`;
conflicting nonempty `agentId` values fail with `conflicting_native_agent_ids`.
The unsupported `session_id` alias stays raw with diagnostics, even when a valid
`sessionId` is also present. It never supplies or reconciles native identity.

Identity classification requires at least one top-level `user`/`assistant`
record and an observed native root ID:

* `session_kind: main`: every conversational record has a nonempty `sessionId`
  and literal `isSidechain: false`; no parsed object contains `agentId`.
  Metadata records may omit `isSidechain`. The normalized ID is `claude:` plus
  the exact root ID.
* `session_kind: subagent`: every parsed object has literal `isSidechain: true`
  and a nonempty `agentId`; each conversational record also has a nonempty
  `sessionId`. The normalized ID is `claude-subagent:` plus SHA-256 of compact,
  sorted-key, ASCII JSON encoding of `[root_session_id, agent_id]`. Structured
  encoding avoids delimiter collisions for arbitrary opaque IDs.
* Otherwise, `session_kind: unresolved`, `session_id: null` and an
  `ambiguous_thread_identity` diagnostic preserve the gap. Observed native IDs
  remain available separately. Present but invalid ID/sidechain fields also
  prevent classification. Invalid/non-object lines cannot supply identity
  evidence and retain their own event diagnostics.

Conflicting boolean sidechain declarations fail with
`conflicting_claude_sidechain_metadata`; a nonempty agent ID combined with an
observed false sidechain fails with `conflicting_claude_thread_metadata`.
No output is published on these conflicts. These are conservative source-evidence
rules, not authentication of a source's claims.

Only a classified subagent exposes `parent_session_id: claude:<root ID>` and
`parent_session_relation: observed_main_session_context`. This indicates the
main session context recorded by the source, not proof of spawn origin or an
immediate parent agent. `parentUuid` is a record-to-record edge; it never becomes
a parent session. No cross-file reconstruction, stitching or deduplication occurs.

The header's `observed_metadata` stores the first observed `sessionId`, `agentId`,
`isSidechain` and timestamp fields, with `metadata_evidence` containing the exact
record locator and field pointer. `native_identity_evidence` separately points
to the first valid native ID fields (the first observed value may be invalid).
Every event retains observed `sessionId`, `uuid`, `parentUuid`, `isSidechain`,
`agentId`, `sourceToolAssistantUUID`, `sourceToolUseID` and `isMeta` in its
`observed_metadata`, with exact `metadata_pointers`. These fields do not prove
spawn or tool pairing. All other keys remain in `source_record`.

## Claude messages, interactions and coverage

Top-level `user` and `assistant` records with a message object produce `message`
events. Role comes from `message.role`; it is never filled from the top-level
type. Canonical fields retain observed `role`, `content`, `id`, `type`,
`stop_reason`, `stop_sequence`, `usage`, `model` and `status`, including unexpected
values. Each field has an exact pointer under `/message`. String content remains
a string, with no synthetic source blocks. Lists preserve every block in order,
with `/message/content/<index>` block pointers. Missing/invalid role/content or
message shapes receive diagnostics. Full source records and unknown fields stay
intact. Timestamps come only from the top-level field with `/timestamp` provenance.

Text blocks support string `text`. Thinking blocks retain string `thinking` and
an optional opaque string `signature`; this is supplied content, not a claim of
complete internal reasoning. Images, documents, redacted material, malformed
blocks and unknown block types remain present with coverage diagnostics.

Every Claude event has an `interactions` list (empty outside applicable messages).
Each ordered `tool_use`/`tool_result` block produces one interaction:

* `kind: tool_call` maps observed block `id` to canonical `call_id`, retaining
  `name` and opaque `input` (expected to be a dict).
* `kind: tool_result` maps observed `tool_use_id` to `call_id`, `content` to
  `output`, and retains `is_error` (expected to be a bool). String and ordered-list
  outputs stay unchanged. Structured output blocks also have exact
  `output_block_pointers`; unknown/non-text output blocks remain explicit gaps.
* Each interaction has its original `block_index`, `block_pointer`, per-field
  `field_pointers`, and `outcome: unknown`. Its original block remains in the
  containing event's complete `source_record`. Missing fields are omitted from
  canonical values and diagnosed; invalid values are retained and diagnosed.
  Opaque unexpected output shapes are retained too. No ID pairing, string parsing,
  command execution or success inference occurs, even for `is_error: false`.

Claude summaries add `tool_interactions` and `tool_interaction_types` with fixed
`tool_call`/`tool_result` counts. `events` and `canonical_events` continue to count
source-record events: a message containing three tool blocks is one message event
and three interactions, not four events. Block/role/type vocabularies and all
diagnostics are bounded; unknown strings go into `other`, never stdout. Diagnostic
counts count affected source events once per code, not individual interactions.
Codex summary keys and count semantics are unchanged.

Recognized metadata types are `system`, `mode`, `permission-mode`,
`file-history-snapshot`, `atis-latch`, `attachment`, `last-prompt`, `ai-title` and
`queue-operation`. They are `source_metadata`, not reconstructed conversations or
deep semantic interpretations. Unknown top-level types remain `unsupported`.
`progress` stays unsupported with `projection_not_reconciled`; no projected tool
or message is promoted. Known summary/compaction markers (`summary`, `compaction`,
`compact_boundary`, `compactMetadata`, `isCompactSummary: true`) add
`compacted_evidence`. A top-level compaction-marked conversational record stays
unsupported rather than expanding into hypothetical original messages. Explicit
redaction/encryption markers add evidence-gap diagnostics. Detection is limited
to modeled record/message/content-block locations, not a recursive interpretation
of arbitrary opaque payloads. No full-history or corpus-wide support is claimed.

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
Claude inspection also checks provider/adapter compatibility, header identity
consistency, interaction ordering and unknown outcomes. The operator's
`scripts/validate_source.py --format claude-code-jsonl` check compares original
records, message/relationship/interaction values and pointers, repeat imports,
copied-source identities and source fingerprints. That validator requires valid
object-shaped JSONL; invalid/non-object lines fail validation even though the
normalizer preserves them. Synthetic validator tests are not real-input proof.
