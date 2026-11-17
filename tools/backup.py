#!/usr/bin/env python3
"""Consistent SQLite backup; never prints application data or credentials."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote


def backup_database(source: Path, destination: Path) -> dict:
    source = source.resolve(strict=True)
    destination = destination.absolute()
    if destination.exists():
        raise ValueError('backup destination already exists')
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    # Read-only source and backup API also work when a WAL file is active.
    uri = 'file:' + quote(str(source), safe='/') + '?mode=ro'
    try:
        with sqlite3.connect(uri, uri=True) as origin:
            with sqlite3.connect(destination) as target:
                origin.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('backup integrity check failed')
    finally:
        # sqlite Connection context managers do not close descriptors.
        if 'target' in locals():
            target.close()
        if 'origin' in locals():
            origin.close()
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {'database': destination.name, 'sha256': digest,
            'bytes': destination.stat().st_size, 'integrity': 'ok'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        result = backup_database(args.source, args.destination)
    except (OSError, ValueError, sqlite3.Error, RuntimeError):
        parser.exit(1, 'Backup failed; inspect permissions, free space and database integrity. A partial destination may remain.\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
