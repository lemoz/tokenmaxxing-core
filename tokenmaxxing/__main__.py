"""CLI whose successful and error output contain no source content."""

import argparse
import json
import sys

from .normalize import FORMATS, inspect, normalize
from .records import NormalizationError


def main(argv=None):
    parser = argparse.ArgumentParser(description="Normalize an explicit Codex or Claude source to a new private file.")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("normalize")
    importer.add_argument("--source", required=True)
    importer.add_argument("--format", required=True, choices=FORMATS)
    importer.add_argument("--output", required=True)
    inspector = commands.add_parser("inspect")
    inspector.add_argument("--source", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "normalize":
            result = normalize(args.source, args.output, args.format)
        else:
            result = inspect(args.source)
    except NormalizationError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, RecursionError, MemoryError, OverflowError):
        # Exception messages can contain source fragments; never echo them.
        print('{"error":"io_or_resource_error"}', file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 3 if result["coverage"] == "partial" else 0


if __name__ == "__main__":
    sys.exit(main())
