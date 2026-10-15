"""CLI whose successful and error output contain no source content."""

import argparse
import json
import sqlite3
import sys

from .normalize import FORMATS, inspect, normalize
from .records import NormalizationError


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicit local normalization and private evidence pilot.")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("normalize")
    importer.add_argument("--source", required=True)
    importer.add_argument("--format", required=True, choices=FORMATS)
    importer.add_argument("--output", required=True)
    importer.add_argument("--selected-session")
    inspector = commands.add_parser("inspect")
    inspector.add_argument("--source", required=True)
    cataloger = commands.add_parser("catalog")
    cataloger.add_argument("--metadata", action="append", required=True)
    cataloger.add_argument("--shared-root", action="append", default=[])
    cataloger.add_argument("--aliases")
    cataloger.add_argument("--output", required=True)
    server = commands.add_parser("serve")
    server.add_argument("--store", required=True)
    server.add_argument("--allow-source", action="append", default=[])
    server.add_argument("--allow-artifact", action="append", default=[])
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--open-browser", action="store_true")
    server.add_argument("--analysis-project", action="append", default=[], help="Approved personal project label; inference disabled when absent")
    server.add_argument("--analysis-directory", help="Separate private 0700 provider temporary root")
    server.add_argument("--analysis-runner-prefix", default="[]", help="Trusted startup JSON argv prefix; optional {prompt_path} and {cwd}")
    server.add_argument("--analysis-timeout", type=int, default=300, help="Provider outer timeout seconds, including wrapper cleanup allowance")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .server import serve
            from .records import loads
            runner = loads(args.analysis_runner_prefix)
            serve(args.store, args.allow_source, args.allow_artifact, args.port, args.open_browser,
                  args.analysis_project, args.analysis_directory, runner, args.analysis_timeout)
            return 0
        elif args.command == "normalize":
            result = normalize(args.source, args.output, args.format, selected_session=args.selected_session)
        elif args.command == "catalog":
            from .discovery import catalog
            result = catalog(args.metadata, args.output, shared_roots=args.shared_root, aliases=args.aliases)
        else:
            result = inspect(args.source)
    except NormalizationError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, RecursionError, MemoryError, OverflowError, sqlite3.Error):
        # Exception messages can contain source fragments; never echo them.
        print('{"error":"io_or_resource_error"}', file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 3 if result["coverage"] == "partial" else 0


if __name__ == "__main__":
    sys.exit(main())
