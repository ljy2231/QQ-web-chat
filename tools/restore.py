#!/usr/bin/env python3
"""Restore a verified SQLite snapshot into a NEW path. Stop service before swapping."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup import backup_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup', type=Path)
    parser.add_argument('new_database', type=Path)
    parser.add_argument('--acknowledge-service-stopped', action='store_true', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        result = backup_database(args.backup, args.new_database)
    except Exception:
        parser.exit(1, 'Restore failed; existing databases were not overwritten.\n')
    print('Snapshot verified and copied into new path; integrity=' + result['integrity'])
    print('Before swapping files: confirm the service is stopped, archive the current database and its sidecars, set ownership, then start and verify.')


if __name__ == '__main__':
    main()
