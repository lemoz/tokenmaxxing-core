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
    server = commands.add_parser("serve")
    server.add_argument("--store", required=True)
    server.add_argument("--allow-source", action="append", default=[])
    server.add_argument("--allow-artifact", action="append", default=[])
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .server import serve
            serve(args.store, args.allow_source, args.allow_artifact, args.port, args.open_browser)
            return 0
        elif args.command == "normalize":
            result = normalize(args.source, args.output, args.format, selected_session=args.selected_session)
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
