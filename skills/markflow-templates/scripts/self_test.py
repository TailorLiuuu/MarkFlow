"""Offline behavioral checks; writes only inside a temporary directory."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).with_name('markflow_template.py')


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = self.root / 'original.labeljob'
        self.run_cli('create', '--output', self.original)

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args, code=0):
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True)
        self.assertEqual(result.returncode, code, result.stderr or result.stdout)
        return json.loads(result.stdout if code == 0 else result.stderr)

    def test_create_dynamic_mac_and_qr(self):
        result = self.run_cli('validate', self.original, '--mac', '02:00:00:12:AB:CD')
        content = {r['id']: r['content'] for r in result['resolved']}
        self.assertEqual(content, {'name': 'Device_ABCD', 'qr': '02:00:00:12:AB:CD', 'mac': '02000012ABCD'})

    def test_only_mac_text_changes(self):
        colon = self.root / 'colon.labeljob'; compact = self.root / 'compact.labeljob'
        self.run_cli('mac-text', self.original, '--style', 'colon', '--output', colon)
        self.run_cli('mac-text', colon, '--style', 'compact', '--output', compact)
        self.assertEqual(json.loads(self.original.read_text()), json.loads(compact.read_text()))
        before = json.loads(colon.read_text()); after = json.loads(compact.read_text())
        for bundle in (before, after):
            next(e for e in bundle['template']['elements'] if e['id'] == 'mac').pop('content')
        self.assertEqual(before, after)

    def test_reject_prefixed_id_and_preserve_input(self):
        bundle = json.loads(self.original.read_text()); bundle['workflow']['steps'][1]['prefix'] = 'Unit_'
        self.original.write_text(json.dumps(bundle)); raw = self.original.read_bytes()
        target = self.root / 'rejected.labeljob'
        self.run_cli('mac-text', self.original, '--style', 'compact', '--output', target, code=1)
        self.assertFalse(target.exists()); self.assertEqual(raw, self.original.read_bytes())

    def test_no_overwrite(self):
        raw = self.original.read_bytes()
        self.run_cli('create', '--output', self.original, code=1)
        self.assertEqual(raw, self.original.read_bytes())

    def test_boundary_validation(self):
        bundle = json.loads(self.original.read_text()); bundle['template']['elements'][1]['x'] = 29
        self.original.write_text(json.dumps(bundle))
        self.run_cli('validate', self.original, code=1)

    def test_pack_and_reject_duplicates(self):
        out = self.root / 'list.markflow'
        self.run_cli('pack', self.original, '--output', out)
        self.assertEqual(json.loads(out.read_text())['templates'], [json.loads(self.original.read_text())])
        self.run_cli('pack', self.original, self.original, '--output', self.root / 'dup.markflow', code=1)


if __name__ == '__main__':
    unittest.main()
