#!/usr/bin/env python3
"""Check tracked paths and obvious hardcoded credentials without printing values."""
import ast
from pathlib import Path
import re
import subprocess
import sys


def main():
    paths = subprocess.check_output(['git', 'ls-files', '-z']).decode().split('\0')
    errors = []
    forbidden = re.compile(r'(^|/)(?:data|uploads|files|backups?|backup-[^/]*|venv|\.venv|__pycache__)(?:/|$)|(?:\.sqlite3(?:-wal|-shm)?|\.db|\.pyc|\.backup|\.bak[^/]*)$|(^|/)(?:chat_data|settings)\.json')
    for name in filter(None, paths):
        if forbidden.search(name) or (Path(name).name.startswith('.env') and Path(name).name != '.env.example'):
            errors.append((name, 'runtime or secret path'))
        if name.endswith('.py') and Path(name).is_file():
            try:
                tree = ast.parse(Path(name).read_text())
            except (UnicodeError, SyntaxError):
                errors.append((name, 'invalid Python source')); continue
            if name.startswith('tests/'):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) and node.value.value:
                    if any(isinstance(target, ast.Name) and target.id in ('WEB_PASSWORD','EXPECTED_TOKEN','NAPCAT_ACCESS_TOKEN') for target in node.targets):
                        errors.append((name, 'hardcoded credential assignment'))
    for name, reason in errors:
        print(f'{name}: {reason}')
    if errors:
        return 1
    print('Tracked-path and obvious credential checks passed; not a substitute for secret scanning/review.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
