"""CLI entry point: ``python -m census_validation [path/to/file.csv]``."""

from __future__ import annotations

import sys

from .processor import CensusFileProcessor


def main() -> None:
    proc = CensusFileProcessor.from_config_file()
    path = sys.argv[1] if len(sys.argv) > 1 else proc.settings.default_file
    sys.exit(proc.process_file(path))


if __name__ == "__main__":
    main()
