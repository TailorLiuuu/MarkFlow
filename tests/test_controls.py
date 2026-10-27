import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from PySide6.QtWidgets import QApplication
from studio_ui import Window, QSS
from studio_engine import StudioEngine
from test_agent_recovery import FakeCloud


class ControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(QSS)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch('agent_runtime.Cloud', FakeCloud):
            self.engine = StudioEngine(Path(self.temp.name), lambda _: None)
        self.engine.config['name'] = 'Test Printer'
        self.engine.prepare_image = Mock()
        self.window = Window(self.engine)
        self.window.changed(self.engine.snapshot())
        self.addCleanup(self.window.close)

    def process(self):
        kind, data = self.engine.actions.get_nowait()
        self.assertEqual(kind, 'start')
        self.engine.process_start(data)
        self.window.changed(self.engine.snapshot())

    def test_double_click_and_stale_snapshot_do_not_reenable_start(self):
        stale = self.engine.snapshot()
        self.window.start.click()
        self.window.start.click()
        self.window.changed(stale)
        self.assertFalse(self.window.start.isEnabled())
        self.assertEqual(self.window.start.text(), '启动中…')
        self.assertEqual(self.engine.actions.qsize(), 1)
        self.assertIsNone(self.engine.enqueue('start'))
        self.process()
        gate = self.engine.gate
        self.assertFalse(self.window.start.isEnabled())
        self.assertEqual(self.window.start.text(), '运行中')
        self.assertFalse(self.window.settings_button.isEnabled())
        self.assertIsNone(self.engine.enqueue('start'))
        self.engine.start_production()
        self.assertIs(self.engine.gate, gate)
        self.engine.prepare_image.assert_called_once()

    def test_stop_cancels_pending_start_and_restores_controls(self):
        self.window.start.click()
        self.engine.halt()
        self.process()
        self.assertFalse(self.engine.running)
        self.engine.prepare_image.assert_not_called()
        self.assertTrue(self.window.start.isEnabled())
        self.assertFalse(self.window.stop.isEnabled())
        self.assertTrue(self.window.import_button.isEnabled())

    def test_failed_start_allows_retry_and_stop_during_preflight_cancels(self):
        self.window.start.click()
        self.engine.prepare_image.side_effect = ValueError('Printer unavailable')
        with self.assertRaises(ValueError):
            self.process()
        self.window.changed(self.engine.snapshot())
        self.assertTrue(self.window.start.isEnabled())
        self.window.start.click()
        self.engine.prepare_image.side_effect = lambda *_: self.engine.halt()
        self.process()
        self.assertFalse(self.engine.running)
        self.assertTrue(self.window.start.isEnabled())

    def test_disabled_button_is_gray_and_pause_prevents_start(self):
        self.window.show()
        self.window.start.click()
        self.app.processEvents()
        color = self.window.start.grab().toImage().pixelColor(12, 12)
        self.assertEqual(color.name(), '#e6eaf0')
        self.engine.halt()
        self.engine.paused = True
        self.window.changed(self.engine.snapshot())
        self.assertFalse(self.window.start.isEnabled())
        self.assertIsNone(self.engine.enqueue('start'))


if __name__ == '__main__':
    unittest.main()
