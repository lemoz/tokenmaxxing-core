# Metadata project catalog

`catalog` maps explicit metadata JSONL files into **project candidates**, for operator
reconciliation before outcome/profile analysis. It never opens referenced histories,
directories, repositories or databases, and makes no network/model calls. Titles and
grouped metadata do not establish accomplishments, ownership or project existence.

```sh
python3 -m tokenmaxxing catalog \
  --metadata /absolute/private/meta1.jsonl \
  --metadata /absolute/private/meta2.jsonl \
  --shared-root /home/example \
  --output /absolute/private/project-map.json
```

Inputs must be explicit absolute regular files without symlink components, hard links
or `..`. Repeating the same input path reads it once; separate batch files retain all
their rows. Output requires an existing, owned mode-0700 directory outside the
executing source tree. Publication creates a new mode-0600 file exclusively; existing
destinations, including dangling links, are refused. The existing secure `Scope`
reader checks input identity before/after consumption. Errors never print row values.

Stdout contains only aggregate counts and fixed diagnostic codes. Exit **0** means
no known metadata gaps, **3** means a catalog was written with gaps (including an
empty input file), and **2** means processing/publication failed. Input I/O or resource
failure prevents publication; malformed individual rows remain diagnostic entries.

## Input contract

Each line is one JSON object with `provider`: `codex`, `claude` or `hermes`. Unversioned
collector rows are accepted; optional top-level `schema_version` and
`metadata_version` must each be integer `1`. Other declared versions are diagnostic
rows. Duplicate JSON keys, non-JSON constants, invalid UTF-8, blank lines and non-object
rows are diagnostic rows. An unterminated valid final line is accepted.

Recognized optional fields follow the builder metadata contract: `origin`, `path`,
`format_candidate`, `cwd`, `agent_id`, `title`, `agentType` are strings.
`sidechain_path` accepts a string or boolean inspection hint, never identity evidence;
`bytes` is a nonnegative integer. Flags are booleans. `git` and `metadata` are objects
or null. `git.repository_url`, `branch`, `commit_hash` and metadata text fields are
strings or null; `metadata.message_count` is a nonnegative integer. Missing/null fields
are allowed. Wrong types in recognized fields make that row diagnostic, with its input
locator retained. `inspection` and `issue` are opaque: only presence/nonempty issue is
observed. Unknown keys/values are omitted and diagnosed; no transcript content is
rendered. Valid projected strings remain inert data, including shell/HTML-looking titles.

## Identity and grouping

- Codex uses only `source_session_id`. Hermes uses the provider plus typed native
  `source_session_id` (string or integer, kept distinct). Equal Hermes IDs across DB
  paths collapse this **observed identity** count; they do not prove equal content,
  copies, revisions or a single real-world session. Missing/empty IDs stay missing.
- Claude reconciles `source_session_id` with observed `metadata.sessionId`. A known
  main context requires `metadata.isSidechain=false` and no `metadata.agentId`; a
  child requires `isSidechain=true` plus nonempty `agentId`. Missing/conflicting
  observations retain a null context key. Top-level `agent_id`/`sidechain_observed`
  can flag conflicts/incompleteness but cannot replace those metadata observations.
  Filenames, `sidechain_path` and unsupported `session_id` aliases establish nothing.
- `sidecar_not_independent_session=true` rows are leads, never sessions or independent
  project candidates. Their `candidate_lead_key` is a possible group key and may not
  correspond to an existing candidate. Parent IDs are observed linkage only.
- A valid explicit repository URL supports a repository-reference group. URL userinfo,
  query and fragment are discarded before output; scheme/host case and default ports
  normalize. HTTP/HTTPS/SSH/git transports, path case, `.git` suffixes and trailing
  slashes remain distinct. SCP-style remotes and local paths are unsupported URLs;
  supply a reviewed URL upstream or use directory aliases. No fuzzy merging occurs.
- Without a repository/alias, one distinct observed directory from `cwd`,
  `metadata.cwd` or `metadata.git_repo_root`, scoped by exact `origin`, gives a tentative
  candidate. Different directory observations remain unassigned. Missing origin is a
  disclosed gap. POSIX absolute directories are treated lexically, never opened.
  `/` and each explicit shared root are excluded by **exact equality**; child project
  directories remain eligible. A shared directory cannot establish a group via an
  alias, but independent explicit repository evidence can still establish one.

## Explicit corrections

Add `--aliases /absolute/private/aliases.json` with this versioned JSON object:

```json
{"schema_version":1,"rules":[
  {"match":{"repository_url":"https://example.test/team/lantern.git"},"project":"lantern","label":"Lantern"},
  {"match":{"repository_url":"ssh://example.test/team/lantern.git"},"project":"lantern","label":"Lantern"},
  {"match":{"cwd":"/home/example/lantern","origin":"synthetic-machine"},"project":"lantern","label":"Lantern"}
]}
```

Selectors are exact repository URLs (normalized as above) or directory plus origin
(`null` selects unknown origin). Matching rules must agree on project ID and its label.
Conflicting selectors, conflicting labels, or a row matching different projects are
diagnosed, never decided by rule order. Conflicting rows stay unassigned. Operator
groups are labelled `operator_asserted`; original metadata remains untouched. The
catalog records normalized rules, matches and the alias file reference/hash.

## Reading the artifact

Schema version `1` contains input hashes/byte totals, sorted candidates and every
input row's line/byte locator. Each row has exactly one accounting category:
`assigned`, `unassigned`, `sidecar`, or `diagnostic`. Their sum equals total rows.
Diagnostics also annotate eligible rows; diagnostic-code totals are not disjoint.
Candidate `row_keys` lead back to all decisions, source references and observations.

Counts distinguish session-eligible rows, rows with/without native IDs, distinct
provider/native IDs, resolved contexts, unresolved context rows and distinct
provider/origin/source-path references. Candidate counts also break down by provider.
References with changed size/format remain inspectable; no file-copy/revision count is
inferred from metadata. A context observed in multiple candidates is diagnosed and
counted once globally; candidate counts therefore need not add up to the global total.

ISO dates and timezone-bearing ISO datetimes are observed separately from identity.
Hermes `started_at`/`ended_at` additionally accept Unix seconds. Naive datetimes and
other numeric timestamps are gaps. Date-only midnight is a sorting boundary with
`day` precision, not an observed instant. Missing/unparseable observations remain in
coverage denominators. There is no current-clock field: identical files/configuration
produce identical output, independent of argument order.

The library exposes `map_metadata((reference, binary_stream) pairs, ...)` for deterministic
in-memory use, `build_catalog(explicit_paths, ...)` for secure file reads, and
`catalog(explicit_paths, output, ...)` for private publication. Stream callers own source
verification. Input decoding is line-streamed without an ingestion cap; projected rows
and the final artifact are held in memory. This is metadata inventory, not the complete
inventory-to-accomplishment analysis release.

Synthetic validation: run `python3 -m unittest discover -s tests -p test_discovery.py -v`,
then the established serial suite. File tests invoke the real CLI. Workspace-only
builders may use the existing [synthetic scratch arrangement](private-pilot.md#verification-and-remaining-gates).
If secure ancestor traversal is denied, leave that protection intact and report the
file/CLI acceptance gap; in-memory mapping tests cannot establish successful publication.
