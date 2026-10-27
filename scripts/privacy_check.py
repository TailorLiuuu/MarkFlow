"""Conservative source-only checks; never prints matching secret contents."""
import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {'.gitignore', 'README.md', 'NOTICE.md'}
DIRS = {'desktop', 'tests', 'assets', 'docs', 'scripts', '.github'}
SUFFIXES = {'.py', '.md', '.txt', '.spec', '.labeljob', '.png', '.ico', '.yml'}
BLOCKED = re.compile(r'(?:auth_server|label_agent_api|test_cloud_store|agent-identity|agent-status|printer-settings|calibration|studio-state|e2e-result)', re.I)
PATTERNS = [
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(r'gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,}'),
    re.compile(r'AKIA[0-9A-Z]{16}|sk-(?:proj-)?[A-Za-z0-9_-]{40,}'),
    re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+/|[A-Z]:[\\/]+Users[\\/]+[A-Za-z0-9_.-]+[\\/]'),
    re.compile(r'https?://[^\s/:]+:[^\s/@]+@'),
    re.compile(r'(?im)^[ \t]*(?:password|api_key|client_secret|access_token|protected_token)[ \t]*=[ \t]*[\'"][^\'"\n]{12,}[\'"]'),
]
MAC = re.compile(r'(?<![\w:])(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}(?![\w:])')

def files(working_tree):
    args = ['git', 'ls-files', '-z']
    if working_tree:
        args += ['--cached', '--others', '--exclude-standard']
    return sorted(set(subprocess.check_output(args, cwd=ROOT).decode().split('\0')) - {''})

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--working-tree', action='store_true')
    args = parser.parse_args()
    failures = []
    paths = files(args.working_tree)
    for name in paths:
        path = Path(name)
        if (name not in ROOT_FILES and (path.parts[0] not in DIRS or path.suffix not in SUFFIXES)) or BLOCKED.search(name):
            failures.append((name, 'file outside desktop source allowlist'))
            continue
        actual = ROOT / path
        if actual.is_symlink():
            failures.append((name, 'symlink is not allowed'))
            continue
        raw = actual.read_bytes()
        if path.suffix in {'.png', '.ico'}:
            # Images are reviewed separately; still inspect binary strings for paths/keys.
            text = raw.decode('latin1')
        else:
            text = raw.decode('utf-8-sig')
        for pattern in PATTERNS:
            if pattern.search(text):
                failures.append((name, 'possible credential or private path'))
                break
        if path.suffix not in {'.png', '.ico'}:
            if any(not m.group().upper().startswith('02:') for m in MAC.finditer(text)):
                failures.append((name, 'non-synthetic static MAC address'))
    for name, reason in failures:
        print(f'FAIL {name}: {reason}')
    if failures:
        raise SystemExit(1)
    print(f'PASS: {len(paths)} source files; no forbidden paths or matching sensitive data.')

if __name__ == '__main__':
    main()
