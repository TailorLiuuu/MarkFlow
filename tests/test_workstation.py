import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from workstation import CalibrationStore,ArrivalGate,printer_key
from label_render import translate_label
from label_store import LocalStore


class WorkstationTests(unittest.TestCase):
    def test_calibration_survives_restart_and_is_per_printer(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'calibration.json';a=printer_key({'interface':'system','name':'ZMIN X1i'});b=printer_key({'interface':'system','name':'Other'})
            store=CalibrationStore(path);store.save(a,-.7,1.2)
            reopened=CalibrationStore(path);self.assertEqual(reopened.get(a),(-.7,1.2));self.assertEqual(reopened.get(b),(0,0))
            reopened.save(b,.2,.3);self.assertEqual(reopened.get(a),(-.7,1.2))
    def test_invalid_calibration_rejected(self):
        for x in [float('nan'),float('inf'),11,True,'0']:
            with self.assertRaises(ValueError):CalibrationStore.validate(x,0)
        self.assertIsNone(printer_key({'interface':'system','name':''}))
    def test_pixel_translation_direction_and_no_scaling(self):
        from PySide6.QtGui import QImage
        image=QImage(50,50,QImage.Format.Format_RGB32);image.fill(0xffffffff);image.setPixel(20,20,0xff000000)
        shifted=translate_label(image,.254,-.508,300)
        self.assertEqual(shifted.pixel(23,14),0xff000000);self.assertEqual(shifted.pixel(20,20),0xffffffff)
        self.assertEqual(shifted.size(),image.size())
    def test_translation_rejects_clipped_ink(self):
        from PySide6.QtGui import QImage
        image=QImage(20,20,QImage.Format.Format_RGB32);image.fill(0xffffffff);image.setPixel(0,0,0xff000000)
        with self.assertRaises(ValueError):translate_label(image,-1,0,300)
    def port(self,serial='first',vid=0x303a,device='COM3'):
        return SimpleNamespace(device=device,vid=vid,pid=0x1001,serial_number=serial)
    def test_debounce_consumes_only_once_while_connected(self):
        gate=ArrivalGate(0x303a,0x1001);p=self.port()
        self.assertIsNone(gate.poll([p],0));self.assertIsNone(gate.poll([p],1))
        self.assertIs(gate.poll([p],2),p)
        for now in (3,20,100):self.assertIsNone(gate.poll([p],now))
    def test_reset_disconnect_does_not_rearm_but_unplug_does(self):
        gate=ArrivalGate(0x303a,0x1001);p=self.port()
        gate.poll([p],0);gate.poll([p],2);gate.poll([],2.5)
        self.assertIsNone(gate.poll([self.port(device='COM51')],3))
        gate.poll([],7);gate.poll([],10);self.assertIsNone(gate.poll([p],11));self.assertIs(gate.poll([p],13),p)
    def test_modem_ignored_and_multiple_esp_rejected(self):
        gate=ArrivalGate(0x303a,0x1001)
        self.assertIsNone(gate.poll([self.port(vid=13667)],0))
        with self.assertRaises(ValueError):gate.poll([self.port(),self.port('second')],1)
    def test_auto_dedup_is_by_mac_across_templates_and_restart(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'jobs.db';store=LocalStore(p);fields={'device_id':'02000000ABCD','mac':'02:00:00:00:AB:CD'}
            job=store.start(fields,{},'template-a',{})
            reopened=LocalStore(p);self.assertEqual(reopened.duplicate_mac('02:00:00:00:ab:cd')['status'],'unknown')
            reopened.finish(job,'submitted','accepted');self.assertIsNotNone(reopened.duplicate_mac(fields['mac']))
            self.assertIsNone(reopened.duplicate_mac('02:00:00:00:AB:CE'))


if __name__=='__main__':unittest.main()
