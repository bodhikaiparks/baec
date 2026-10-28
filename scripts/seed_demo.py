"""Build the local development database from the synthetic seed files.

Usage, from the repository root:

    python3 scripts/seed_demo.py                 # creates var/baec_dev.sqlite3
    python3 scripts/seed_demo.py --force         # rebuilds it from scratch
    python3 scripts/seed_demo.py --path "some/other file.sqlite3"

The database is built in a temporary file and moved into place only if
seeding succeeds, so a failed run never leaves a half-seeded database.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from baec_app.data.database import table_counts  # noqa: E402
from baec_app.data.seed import build_seed_database  # noqa: E402

DEFAULT_PATH = REPOSITORY_ROOT / "var" / "baec_dev.sqlite3"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", default=str(DEFAULT_PATH), help="database file to create")
    parser.add_argument("--force", action="store_true", help="replace an existing database")
    arguments = parser.parse_args(argv)

    target = Path(arguments.path)
    if target.exists() and not arguments.force:
        print(f"{target} already exists. Use --force to rebuild it.")
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".building")
    if temporary.exists():
        temporary.unlink()
    try:
        connection = build_seed_database(str(temporary))
        try:
            counts = table_counts(connection)
        finally:
            connection.close()
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()

    print("Research Prototype - Synthetic Data Only")
    print(f"Seeded {target}")
    for table, count in counts.items():
        print(f"  {table}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
