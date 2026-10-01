#!/usr/bin/env python3
"""Create a consistent SQLite snapshot, including committed WAL transactions."""

import argparse
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path


def snapshot(source: Path, destination: Path) -> None:
    source = source.resolve(strict=True)
    if source == destination.resolve():
        raise ValueError("Snapshot must not replace its source database.")
    # Exclusive creation prevents accidental replacement of a previous backup.
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as origin:
            with closing(sqlite3.connect(destination)) as target:
                origin.backup(target)
                result = target.execute("PRAGMA quick_check").fetchall()
                if result != [("ok",)]:
                    raise RuntimeError(f"Invalid SQLite snapshot: {result}")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path)
    source.add_argument("--from-settings", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    path = args.source
    if args.from_settings:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        from django.conf import settings

        database = settings.DATABASES["default"]
        if database["ENGINE"] != "django.db.backends.sqlite3":
            parser.error("--data database replacement supports SQLite only.")
        if database["NAME"] == ":memory:":
            parser.error("Cannot snapshot an in-memory database.")
        path = Path(database["NAME"])
    snapshot(path, args.output)


if __name__ == "__main__":
    main()
