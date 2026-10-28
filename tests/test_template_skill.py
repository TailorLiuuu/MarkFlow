"""Keep the standalone skill compatible with actual desktop imports and rendering."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'desktop'))
from template_library import TemplateLibrary

SKILL = ROOT / 'skills' / 'markflow-templates'


class TemplateSkillTests(unittest.TestCase):
    def command(self, *args):
        result = subprocess.run([sys.executable, str(SKILL / 'scripts' / 'markflow_template.py'),
                                 *map(str, args)], capture_output=True, text=True,
                                env={**os.environ, 'PYTHONUTF8': '1'}, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_embedded_runtime_matches_client(self):
        # A stale copy could accept templates that the installed client rejects.
        for name in ('label_contract.py', 'label_render.py'):
            self.assertEqual((ROOT / 'desktop' / name).read_bytes(),
                             (SKILL / 'scripts' / 'runtime' / name).read_bytes(), name)

    def test_generated_template_list_imports_and_renders(self):
        from PySide6.QtGui import QImage
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); template = root / 'device.labeljob'
            summary = self.command('create', '--output', template)
            catalog = root / 'templates.markflow'
            self.command('pack', template, '--output', catalog)
            imported = TemplateLibrary(root / 'library').import_file(catalog)
            self.assertEqual([item['id'] for item in imported], [summary['digest']])
            preview = root / 'preview.png'
            result = self.command('preview', template, '--output', preview)
            self.assertEqual(result['warnings'], [])
            image = QImage(str(preview))
            self.assertFalse(image.isNull())
            self.assertEqual([image.width(), image.height()], [354, 236])
            # Both text regions must contain ink; a blank placeholder is not a preview.
            for start, end in ((0, 45), (190, 235)):
                ink = sum(image.pixelColor(x, y).lightness() < 128
                          for y in range(start, end) for x in range(image.width()))
                self.assertGreater(ink, 100)


if __name__ == '__main__':
    unittest.main()
