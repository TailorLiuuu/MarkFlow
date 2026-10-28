import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from studio_engine import StudioEngine
from label_store import LocalStore
from workstation import ArrivalGate
from test_agent_recovery import FakeCloud

MAC='02:00:00:00:AB:CD'
FIELDS={'device_id':'02000000ABCD','mac':MAC}

class ProductionArrivalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)

    def engine(self):
        with patch('agent_runtime.Cloud',FakeCloud):engine=StudioEngine(self.directory,lambda _:None)
        engine.render_preview=Mock();engine.running=True;engine.paused=False
        engine.config['name']='Test Printer'
        bundle=engine.library.get(engine.selected)['bundle']
        engine.prepare_image=Mock(return_value=(bundle,FIELDS,Mock(),{},engine.config))
        return engine

    def test_same_mac_can_print_again_and_count_survives_restart(self):
        engine=self.engine()
        with patch('app.read_mac',return_value={'mac':MAC,'chip':'ESP32'}),patch('system_print.submit',return_value={'status':'submitted','message':'queue accepted','job_id':1}) as submit:
            engine.local_print('COM3');engine.local_print('COM3')
        self.assertEqual(submit.call_count,2)
        self.assertEqual(engine.snapshot()['print_count'],2)
        self.assertIn('read_mac_seconds',engine.last_job['timing'])
        reopened=self.engine()
        self.assertEqual(reopened.snapshot()['print_count'],2)
        self.assertTrue(all(row['print_count']==2 for row in reopened.snapshot()['recent']))
        with patch('app.read_mac',return_value={'mac':MAC,'chip':'ESP32'}),patch('system_print.submit',return_value={'status':'submitted','message':'queue accepted'}):
            reopened.local_print('COM3')
        self.assertEqual(reopened.snapshot()['print_count'],3)

    def test_counts_exclude_failed_unknown_simulated_and_include_other_templates(self):
        store=LocalStore(self.directory/'counts.sqlite3')
        for index,status in enumerate(['submitted','confirmed','failed','unknown','simulated']):
            job=store.start(FIELDS,{},'template-'+str(index),{})
            store.finish(job,status,'test')
        other={**FIELDS,'mac':'02:00:00:00:AB:CE'}
        job=store.start(other,{},'other',{});store.finish(job,'submitted','accepted')
        self.assertEqual(store.print_counts(),{MAC:2,other['mac']:1})
        self.assertNotIn('bundle',store.recent(1)[0])
        self.assertEqual(LocalStore(self.directory/'counts.sqlite3').print_counts()[MAC],2)

    def test_unknown_submission_stops_production_without_counting_or_retrying(self):
        engine=self.engine()
        with patch('app.read_mac',return_value={'mac':MAC,'chip':'ESP32'}),patch('system_print.submit',side_effect=OSError('queue reply lost')) as submit:
            engine.local_print('COM3')
        self.assertEqual(submit.call_count,1);self.assertFalse(engine.running)
        self.assertTrue(engine.local_pause);self.assertEqual(engine.snapshot()['print_count'],0)
        self.assertEqual(engine.snapshot()['recent'][0]['status'],'unknown')

    def test_arrival_stable_reset_ignored_and_reinsert_rearms(self):
        gate=ArrivalGate(12346,4097,settle=.4,absent=1)
        port=SimpleNamespace(device='COM3',vid=12346,pid=4097,serial_number='test')
        self.assertIsNone(gate.poll([port],0));self.assertIs(gate.poll([port],.41),port)
        for now in (1,10,100):self.assertIsNone(gate.poll([port],now))
        gate.poll([],101);self.assertIsNone(gate.poll([port],101.3))
        gate.poll([],102)
        # A full unplug interval must rearm even if the next sample already contains the port.
        self.assertIsNone(gate.poll([port],103.1));self.assertIs(gate.poll([port],103.6),port)
        self.assertIsNone(gate.poll([port],104))

    def test_initial_usb_bounce_restarts_stability_timer(self):
        gate=ArrivalGate(12346,4097,settle=.4,absent=1)
        port=SimpleNamespace(device='COM3',vid=12346,pid=4097,serial_number='test')
        gate.poll([port],0);gate.poll([],0.2)
        self.assertIsNone(gate.poll([port],.3));self.assertIsNone(gate.poll([port],.5))
        self.assertIs(gate.poll([port],.71),port)

    def test_slow_cloud_cannot_block_usb_print_dispatch(self):
        engine=self.engine();engine.bound=True
        engine.gate=ArrivalGate(12346,4097,settle=0)
        port=SimpleNamespace(device='COM3',vid=12346,pid=4097,serial_number='test')
        engine.devices=Mock(return_value=[port])
        release=threading.Event();entered=threading.Event();printed=threading.Event()
        def cloud(*_):
            entered.set();release.wait(5)
            return {'name':'Test','paused':False,'assignment':None,'catalog':None,'commands':[]}
        def print_label(*_):printed.set();engine.stop.set()
        engine.cloud.call=cloud;engine.local_print=print_label
        worker=threading.Thread(target=engine.run,daemon=True);worker.start()
        try:
            self.assertTrue(entered.wait(1.5));self.assertTrue(printed.wait(1.5))
            self.assertFalse(release.is_set())
        finally:
            release.set();engine.stop.set();worker.join(2)

    def test_failed_background_receipt_remains_retryable_without_reprinting(self):
        from concurrent.futures import Future
        engine=self.engine();engine.bound=True
        item=engine.library.get(engine.selected)
        engine.queue_local_result('test-record',item,{'mac':MAC},{'status':'submitted','message':'accepted'})
        path=engine.outbox/'test-record.json'
        pending=Future()
        with patch('studio_engine.background_call',return_value=pending):engine.poll_cloud()
        self.assertFalse(json.loads(path.read_text())['reported'])
        pending.set_exception(OSError('offline'));engine.poll_cloud()
        self.assertFalse(json.loads(path.read_text())['reported']);self.assertFalse(engine.online)
        engine.cloud_retry=0;receipt=Future()
        with patch('studio_engine.background_call',return_value=receipt):engine.poll_cloud()
        receipt.set_result({})
        with patch('studio_engine.background_call',return_value=Future()):engine.poll_cloud()
        self.assertTrue(json.loads(path.read_text())['reported'])
        self.assertEqual(engine.local.print_counts(),{})

    def test_printer_enumeration_cannot_block_serial_inventory(self):
        engine=self.engine();release=threading.Event();returned=threading.Event()
        def printers():release.wait(5);return ['Test Printer']
        def sample():engine.devices();returned.set()
        with patch('serial.tools.list_ports.comports',return_value=[]),patch('PySide6.QtPrintSupport.QPrinterInfo.availablePrinterNames',side_effect=printers):
            worker=threading.Thread(target=sample,daemon=True);worker.start()
            try:self.assertTrue(returned.wait(1.5));self.assertFalse(release.is_set())
            finally:release.set();worker.join(2);engine._printers_future.result(timeout=2)

if __name__=='__main__':unittest.main()
