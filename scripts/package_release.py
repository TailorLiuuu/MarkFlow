"""Package only a clean PyInstaller output, never a user data directory."""
import hashlib
import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.search(r"^VERSION=['\"]([^'\"]+)", (ROOT / 'desktop/agent_runtime.py').read_text(), re.M)[1]

def main():
    source = ROOT / 'dist/MarkFlow'
    for name in ('MarkFlow.exe', 'LabelDeskWorker.exe', '_internal'):
        if not (source / name).exists():
            raise SystemExit(f'Missing build output: {name}')
    files = sorted(p for p in source.rglob('*') if p.is_file())
    forbidden = {'agent-identity.json', 'agent-status.json', 'printer-settings.json', 'calibration.json', 'studio-state.json', 'active.labeljob'}
    private_dirs = {'agent-jobs', 'agent-commands', 'local-outbox', 'print-requests'}
    for p in files:
        if p.is_symlink() or p.name in forbidden or private_dirs.intersection(p.relative_to(source).parts) or p.suffix in {'.db', '.sqlite', '.csv', '.log'}:
            raise SystemExit('Unexpected runtime data in build output; refusing to package.')
    output = ROOT / 'release'
    output.mkdir(exist_ok=True)
    archive = output / f'MarkFlow-{VERSION}-Windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, Path('MarkFlow') / p.relative_to(source))
        for name in ('README.md', 'NOTICE.md', 'docs/PRIVACY.md'):
            z.write(ROOT / name, 'MarkFlow/' + name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / 'SHA256SUMS').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(archive.name)
    print(digest)

if __name__ == '__main__':
    main()
