import sys,json,unittest,tempfile,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'shared'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from label_contract import *
from label_store import LocalStore
SAMPLE=Path(__file__).resolve().parents[1]/'desktop/examples/esp32-mac.labeljob'
class ContractTests(unittest.TestCase):
 def setUp(self):self.b=json.loads(SAMPLE.read_text(encoding="utf-8"))
 def test_mac_format_and_field_binding(self):
  v=execute_data(self.b,{'mac':'02:00:00:00:00:01','date':'2026-01-01'})
  self.assertEqual(v['device_id'],'020000000001');self.assertEqual(v['mac'],'02:00:00:00:00:01')
  self.assertEqual(bind('{{device_id}}',v),'020000000001')
 def test_invalid_order_fails_closed(self):
  s=self.b['workflow']['steps'];s[-2],s[-1]=s[-1],s[-2]
  with self.assertRaises(ContractError):validate_bundle(self.b)
 def test_arbitrary_action_and_parameters_rejected(self):
  for step in [{'id':'evil','type':'shell','command':'echo unsafe'},{'id':'source','type':'read_esp32_mac','command':'echo unsafe'}]:
   b=copy.deepcopy(self.b);b['workflow']['steps'][0]=step
   with self.assertRaises(ContractError):validate_bundle(b)
 def test_bad_mac_and_missing_field_rejected(self):
  with self.assertRaises(ContractError):execute_data(self.b,{'mac':'DEMO'})
  with self.assertRaises(ContractError):execute_data(self.b,{'mac':'02:00:00:00:00:01'})
 def test_template_overflow_and_nan_rejected(self):
  for value in [float('nan'),-1,200]:
   b=copy.deepcopy(self.b);b['template']['elements'][0]['x']=value
   with self.assertRaises(ValueError):validate_bundle(b)
 def test_manual_leading_zero(self):
  self.b['workflow']['steps'][0]['type']='manual_input'
  self.assertEqual(execute_data(self.b,{'device_id':'000123','date':'2026-01-01'})['device_id'],'000123')
 def test_mac_suffix_is_derived_and_does_not_replace_identity(self):
  fields=execute_data(self.b,{'mac':'02:00:00:00:ab:cd','mac_last4':'FAKE','date':'2026-01-01'})
  self.assertEqual(fields['mac_last4'],'ABCD');self.assertEqual(fields['device_id'],'02000000ABCD')
  self.assertEqual(bind('RingHi_{{mac_last4}}',fields),'RingHi_ABCD')
 def test_recovery_marks_interrupted_submission_unknown(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'jobs.db';s=LocalStore(p);fields={'device_id':'00123'};digest=bundle_digest(self.b)
   identifier=s.start(fields,self.b,digest,{'interface':'usb'})
   recovered=LocalStore(p);self.assertEqual(recovered.rows()[0]['status'],'unknown');self.assertIsNotNone(recovered.duplicate('00123',digest))
   recovered.finish(identifier,'confirmed','paper checked');self.assertEqual(recovered.rows()[0]['status'],'confirmed')
if __name__=='__main__':unittest.main()
