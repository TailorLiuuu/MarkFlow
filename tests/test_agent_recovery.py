import json,sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from agent_runtime import Engine
from workstation import atomic_json

class FakeCloud:
 def __init__(self,directory):self.id=str(uuid.uuid4());self.calls=[];self.fail_result=False
 def call(self,route,data=None):
  self.calls.append((route,data))
  if route=='result' and self.fail_result:raise OSError('network down')
  if route=='recover':return {'job_id':'cloud-job','status':'claimed'}
  if route=='claim':return {'job_id':'cloud-job','duplicate':False,'status':'claimed'}
  return {'ok':True}

class RecoveryTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.dir=Path(self.temp.name)
  self.cloud_patch=patch('agent_runtime.Cloud',FakeCloud);self.cloud_patch.start();self.addCleanup(self.cloud_patch.stop)
 def test_crash_after_submit_is_unknown_not_resubmitted(self):
  p=self.dir/'agent-jobs'/'job.json';atomic_json(p,{'request_id':str(uuid.uuid4()),'job_id':'cloud-job','status':'sending','reported':False})
  e=Engine(self.dir,lambda _:None);self.assertEqual(json.loads(p.read_text(encoding='utf-8'))['status'],'unknown');e.flush()
  self.assertEqual([c[0] for c in e.cloud.calls],['result']);self.assertEqual(e.cloud.calls[0][1]['status'],'unknown');e.flush();self.assertEqual(len(e.cloud.calls),1)
 def test_crash_during_claim_looks_up_does_not_claim_again(self):
  p=self.dir/'agent-jobs'/'job.json';atomic_json(p,{'request_id':str(uuid.uuid4()),'status':'claim_pending','reported':False})
  e=Engine(self.dir,lambda _:None);e.flush();self.assertEqual([c[0] for c in e.cloud.calls],['recover','result'])
 def test_offline_result_retries_receipt_only(self):
  p=self.dir/'agent-jobs'/'job.json';atomic_json(p,{'request_id':str(uuid.uuid4()),'job_id':'cloud-job','status':'submitted','reported':False,'message':'queue accepted'})
  e=Engine(self.dir,lambda _:None);e.cloud.fail_result=True
  with self.assertRaises(OSError):e.flush()
  self.assertFalse(json.loads(p.read_text(encoding='utf-8'))['reported']);e.cloud.fail_result=False;e.flush();self.assertTrue(json.loads(p.read_text(encoding='utf-8'))['reported']);self.assertTrue(all(route=='result' for route,_ in e.cloud.calls))
 def test_pre_submit_error_is_durable_and_does_not_claim(self):
  e=Engine(self.dir,lambda _:None)
  with patch.object(e,'_execute',side_effect=ValueError('serial unavailable')):
   with self.assertRaises(ValueError):e.execute('COM3')
  self.assertEqual(e.last_error,'serial unavailable');self.assertEqual(json.loads((self.dir/'agent-last-error.json').read_text(encoding='utf-8'))['phase'],'execute');self.assertEqual(e.cloud.calls,[])
 def test_inflight_single_command_not_executed_again(self):
  p=self.dir/'agent-commands'/'test.json';atomic_json(p,{'id':str(uuid.uuid4()),'done':False,'reported':False})
  e=Engine(self.dir,lambda _:None);e.flush();self.assertEqual(e.cloud.calls[0][0],'command-result');self.assertIn('未自动重试',e.cloud.calls[0][1]['message'])

if __name__=='__main__':unittest.main()
