import json,sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from template_library import TemplateLibrary
from studio_engine import StudioEngine
from test_agent_recovery import FakeCloud
from label_contract import bundle_digest
SAMPLE=Path(__file__).resolve().parents[1]/'desktop/examples/ringhi-mac.labeljob'
class StudioTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.dir=Path(self.temp.name);self.bundle=json.loads(SAMPLE.read_text(encoding='utf-8'))
 def engine(self):
  with patch('agent_runtime.Cloud',FakeCloud):e=StudioEngine(self.dir,lambda _:None)
  e.render_preview=Mock();return e
 def test_import_many_is_atomic_and_idempotent(self):
  lib=TemplateLibrary(self.dir/'templates');p=self.dir/'list.markflow';p.write_text(json.dumps({'format':'markflow-list','templates':[self.bundle,{}]}),encoding='utf-8')
  with self.assertRaises(ValueError):lib.import_file(p)
  self.assertEqual(lib.all(),[]);p.write_text(json.dumps(self.bundle),encoding='utf-8');lib.import_file(p);lib.import_file(p);self.assertEqual(len(lib.all()),1)
 def test_local_start_does_not_require_cloud_and_stop_takes_effect(self):
  e=self.engine();e.config={'name':'Printer','dpi':300,'paper_mode':'gap','offset_x_mm':1,'offset_y_mm':0,'vid':12346,'pid':4097};e.prepare_image=Mock();e.start_production();self.assertTrue(e.running);self.assertFalse(e.bound);e.halt();self.assertFalse(e.running);self.assertTrue(e.local_pause)
 def test_stop_during_mac_read_never_submits(self):
  e=self.engine();e.running=True;e.prepare_image=Mock(return_value=(self.bundle,{'device_id':'02000000ABCD'},Mock(),{},e.config))
  def read(port):e.halt();return {'mac':'02:00:00:00:AB:CD','chip':'ESP32'}
  with patch('app.read_mac',side_effect=read),patch('system_print.submit') as submit:e.local_print('COM3');submit.assert_not_called()
 def test_receive_list_does_not_start_or_replace_selected_template(self):
  e=self.engine();selected=e.selected;b=json.loads(json.dumps(self.bundle));b['name']='Second template';key=bundle_digest(b)
  e.cloud.call=Mock(return_value={'bundle':b,'digest':key});e.sync_catalog({'revision':'list1','items':[{'id':str(uuid.uuid4()),'revision':1,'digest':key}]})
  self.assertEqual(e.selected,selected);self.assertFalse(e.running);self.assertEqual(e.catalog_revision,'list1');self.assertIsNotNone(e.library.get(key))
 def test_test_command_does_not_change_local_selection(self):
  e=self.engine();e.paused=False;e.devices=Mock(return_value=[type('Port',(),{'vid':12346,'pid':4097,'device':'COM3'})()]);e.execute=Mock(return_value='queue accepted');selected=e.selected;old=e.assignment;cid=str(uuid.uuid4())
  e.remote_test({'id':cid,'expires':9999999999,'target':{'config':{'vid':12346,'pid':4097}}});e.remote_test({'id':cid,'expires':9999999999,'target':{'config':{'vid':12346,'pid':4097}}})
  e.execute.assert_called_once();self.assertEqual(e.selected,selected);self.assertIs(e.assignment,old)
if __name__=='__main__':unittest.main()
