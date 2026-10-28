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
        self.window = Window(self.engine, updates_enabled=False)
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

    def test_update_prompt_does_not_download_or_install_without_a_click(self):
        from PySide6.QtWidgets import QWidget, QVBoxLayout
        from update_ui import Updates
        panel = QWidget(self.window)
        updates = Updates(self.window, self.engine, QVBoxLayout(panel), automatic=False)
        updates.task = Mock()
        notices = []
        updates.available.connect(notices.append)
        updates.check(True)
        self.assertFalse(updates.button.isEnabled())
        updates.finished('checked', {'version':'0.5.0'})
        self.assertEqual(notices, ['0.5.0'])
        self.assertTrue(updates.button.isEnabled())
        self.assertFalse(updates.download.isHidden())
        self.assertEqual(updates.task.call_count, 1)
        self.assertFalse(self.engine.update_installing)
        self.engine.running = True
        updates.begin_download()
        self.assertIn('停止生产', updates.info.text())
        self.assertEqual(updates.task.call_count, 1)
        updates.check(True)
        updates.finished('error', '网络暂不可用')
        self.assertTrue(updates.button.isEnabled())
        self.assertEqual(updates.phase, 'idle')

    def test_device_count_is_visible_in_card_and_records(self):
        fields={'device_id':'02000000ABCD','mac':'02:00:00:00:AB:CD'}
        for _ in range(2):
            job=self.engine.local.start(fields,{},'test',{})
            self.engine.local.finish(job,'submitted','queue accepted')
        self.engine.last_mac=fields['mac'];self.window.changed(self.engine.snapshot())
        self.assertIn('累计打印 2 次',self.window.result_card.value.text())
        self.assertEqual(self.window.records.item(0,2).text(),'2')

    def test_install_preparation_disables_production_controls(self):
        self.assertTrue(self.engine.prepare_update())
        self.window.changed(self.engine.snapshot())
        for button in (self.window.start,self.window.stop,self.window.settings_button,self.window.import_button):
            self.assertFalse(button.isEnabled())


if __name__ == '__main__':
    unittest.main()
