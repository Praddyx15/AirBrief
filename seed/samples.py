"""Write every built-in sample file: ``python -m seed.samples [--dest samples]``.

The files come from :mod:`airbrief_core.samples` and are byte-for-byte deterministic. Note that
``tier0/zipbomb_small.zip`` is small on disk (about 0.2 MB) but expands to 201 MiB; it exists to
exercise the ingest size guard and should never be unpacked by hand.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from airbrief_core.samples import SAMPLES, build_sample, write_all


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m seed.samples",
        description="Write the AirBrief sample files (Tier 0 cases, module demos, FlightMD reports).",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path("samples"),
        help="Destination directory (default: samples). Sub-folders tier0/, modules/ and flightmd/ are created.",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="NAME",
        choices=sorted(SAMPLES),
        help="Write only these samples (default: all).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    args = _parse_args(argv)
    dest: Path = args.dest
    if args.only:
        paths: list[Path] = []
        for name in args.only:
            path = dest / SAMPLES[name].filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(build_sample(name))
            paths.append(path)
    else:
        paths = write_all(dest)
    for path in paths:
        print(f"{path.as_posix():<55} {path.stat().st_size:>12,} bytes")
    print(f"Wrote {len(paths)} sample file(s) to {dest.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
