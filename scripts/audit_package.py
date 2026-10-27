"""Inspect the built client, including application bytecode inside both EXEs."""
import hashlib
import marshal
import types
from pathlib import Path
from PyInstaller.archive.readers import CArchiveReader
from privacy_check import MAC, PATTERNS

ROOT = Path(__file__).resolve().parents[1]

def check_text(text):
    if any(p.search(text) for p in PATTERNS):
        raise ValueError('Possible private path or credential in application bytecode')
    if any(not m.group().upper().startswith('02:') for m in MAC.finditer(text)):
        raise ValueError('Non-synthetic MAC in application bytecode')

def check_code(code):
    check_text(code.co_filename)
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            check_code(value)
        elif isinstance(value, str):
            check_text(value)

def audit(source):
    modules = {p.stem for p in (ROOT / 'desktop').glob('*.py')} - {'app'}
    checked = 0
    for filename in ('MarkFlow.exe', 'LabelDeskWorker.exe'):
        archive = CArchiveReader(str(source / filename))
        check_code(marshal.loads(archive.extract('app')))
        pyz_name = next(name for name in archive.toc if name.endswith('.pyz'))
        pyz = archive.open_embedded_archive(pyz_name)
        for name in modules:
            check_code(pyz.extract(name))
            checked += 1
    for folder in ('assets', 'examples'):
        expected = list((ROOT / 'desktop' / folder).glob('*'))
        for original in expected:
            if not original.is_file():
                continue
            packaged = source / '_internal' / folder / original.name
            if hashlib.sha256(packaged.read_bytes()).digest() != hashlib.sha256(original.read_bytes()).digest():
                raise ValueError('Packaged resource differs from reviewed source')
    print(f'PASS: {checked} embedded application modules, both entry points and all application resources reviewed.')

if __name__ == '__main__':
    audit(ROOT / 'dist/MarkFlow')
