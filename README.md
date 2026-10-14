# Tokenmaxxing Core

An independent session-normalization foundation for evidence-backed work profiles.

The current slice supports Codex JSONL and older Codex `session`/`items` JSON. It preserves source provenance, provides conservative canonical message/tool events, and makes missing or unsupported evidence explicit. It does not infer accomplishments, score users or rank token usage.

Claude Code and Hermes adapters, work profiles, outcome checks and niche-specific leaderboards are future work.

## Run locally

Python 3.9+ standard library. No package installation, service, network or database is needed to run the normalizer. CI exercises Python 3.11.

```sh
git clone https://github.com/lemoz/tokenmaxxing-core.git
cd tokenmaxxing-core
python3 -m unittest discover -s tests -v
```

## Normalize and inspect

Choose an existing private output directory outside this repository, with mode 0700. Use a new output filename; existing destinations are refused, including symlinks and hard links.

```sh
python3 -m tokenmaxxing normalize --source /absolute/source.jsonl --format codex-jsonl --output /absolute/private/session.normalized.jsonl
python3 -m tokenmaxxing inspect --source /absolute/private/session.normalized.jsonl
```

For legacy `session`/`items` JSON, select `--format codex-legacy-json` explicitly.

Output is private JSONL: a session header, one event per input record/item (including metadata and unsupported records), and a structural summary footer. Output files are created with mode 0600. The CLI prints only a content-free structural summary. Never direct transcript content to stdout, git or a shared report.

Exit codes:

- 0: parsed with no known coverage warnings.
- 2: failed; no normalized output published by the normalizer.
- 3: output produced with explicit missing or unsupported evidence.

Neither exit 0 nor a source status means successful work or a complete work history. Repeated unchanged inputs and unchanged copies have stable event identities. This is not a persistent ingestion store or cross-history deduplication system. JSONL is processed incrementally; legacy JSON is parsed in memory.

## Private source validation

Inspection verifies the normalized envelope and recomputes its summary, not its authenticity against original sources. For an explicitly authorized source:

```sh
python3 scripts/validate_source.py --source /absolute/source.jsonl --format codex-jsonl --output-dir /absolute/private/proof
```

The output directory must be new, under an existing parent. The validator creates it with mode 0700, writes private artifacts, compares source records and canonical fields/blocks to their locators, repeats imports, checks copied-input identities, and verifies the original source was unchanged. It never prints transcript content and does not discover other files.

Read [the normalization contract](docs/normalization-contract.md) before interpreting results. All committed tests use synthetic inputs. Original histories, normalized content and private validation reports must stay outside the repository.

## Contributing

Use feature branches, focused commits and pull requests. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development flow and validation expectations.
