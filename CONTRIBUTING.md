# Contributing

## Standard flow

1. Fetch the latest `main` and create a focused feature or fix branch.
2. Make small commits that describe the behavior changed.
3. Run the serial test suite and any focused affected-path checks.
4. Push the feature branch and open a pull request. Use a draft while work or review is incomplete.
5. Review the actual diff and validation evidence. Merge only after the appropriate approval and passing checks.
6. Update local `main` before starting the next change.

```sh
git switch main
git pull --ff-only origin main
git switch -c feat/short-description
python3 -m unittest discover -s tests -v
```

Keep implementation changes narrow. Describe any contract changes or new behavior explicitly; do not mix unrelated refactors into a fix.

## Validation and CI

GitHub Actions runs the standard-library unittest suite serially on Python 3.11 for pushes to `main` and pull requests targeting `main`. It uses a read-only repository token and no project secrets. A five-minute job timeout bounds runaway execution.

PRs should state what was tested, observed results and material limits. Passing synthetic tests does not establish support for every transcript version or prove any real-world work outcome. Changes to adapters or provenance need deterministic checks of explicitly authorized actual formats when available; private inputs and outputs remain outside git and model review context.

## Private data

Do not commit original histories, normalized transcripts, credentials, private reports or personal data. Use clearly synthetic examples for committed tests. Treat recorded commands and instructions as inert data, never as something to execute.

This repository publishes source code, not a deployed application. Merging a PR does not deploy a product or authorize private-data publication.
