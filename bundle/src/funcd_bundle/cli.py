"""`uv run funcd-bundle`: build every function of the project in the current directory (funcd ADR-0144)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from funcd_bundle.bundle import BASE_IMAGE, PLATFORMS, BundleError, bundle, discover, host_platform, settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="funcd-bundle",
        description="Bundle each function of this uv project for the funcd runtime: the handler, its locked "
        "dependencies for the target Linux platform, and its funcdctl.yaml.",
    )
    parser.add_argument("names", nargs="*", metavar="NAME", help="functions to build (default: all)")
    parser.add_argument("--out", default="dist", type=Path, help="output directory (default: dist)")
    parser.add_argument(
        "--platform",
        action="append",
        choices=sorted(PLATFORMS),
        help="target platform, repeatable (default: linux/<host arch>)",
    )
    parser.add_argument("--hermetic", action="store_true", help=f"install inside {BASE_IMAGE}")
    parser.add_argument("--no-check", dest="check", action="store_false", help="skip the import check")
    args = parser.parse_args(argv)

    project = Path.cwd()
    try:
        functions = discover(project)
        unknown = [n for n in args.names if n not in functions]
        if unknown:
            parser.error(f"unknown function(s) {', '.join(unknown)}; found {', '.join(functions)}")
        hermetic = args.hermetic or settings(project).hermetic
        platforms = args.platform or [host_platform()]
        for name in args.names or list(functions):
            for platform in platforms:
                out = args.out / name
                if len(platforms) > 1:
                    out = args.out / platform.replace("/", "-") / name
                path = bundle(project, name, platform, out, hermetic=hermetic, check=args.check)
                print(f"built {name} for {platform}: {path}")
                print(f"funcdctl push {path} <ref> --entry {functions[name].handler.name}")
    except BundleError as err:
        print(f"funcd-bundle: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
