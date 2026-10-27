import io
import json
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
import update_core as u
from studio_engine import StudioEngine
from test_agent_recovery import FakeCloud


def release(tag='v0.4.2'):
    return {'tag_name': tag, 'draft': False, 'prerelease': False, 'assets': [
        {'name': name, 'state': 'uploaded', 'size': 100, 'browser_download_url': u.WEB+'/releases/download/'+tag+'/'+name}
        for name in (u.ASSET, 'SHA256SUMS')]}


class UpdatesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, extra=None):
        path = self.root/'update.zip'
        with zipfile.ZipFile(path, 'w') as z:
            for name in ('MarkFlow.exe', 'LabelDeskWorker.exe', 'MarkFlowUpdater.exe'):
                z.writestr('MarkFlow/'+name, b'new executable')
            z.writestr('MarkFlow/version.json', json.dumps({'product':'MarkFlow','version':'0.4.2','platform':'windows-x64'}))
            z.writestr('MarkFlow/_internal/test.dll', b'library')
            if extra:
                z.writestr(*extra)
        return path

    def app(self):
        app = self.root/'MarkFlow'; app.mkdir(); (app/'MarkFlow.exe').write_bytes(b'old executable')
        return app

    def test_numeric_versions_and_no_downgrades(self):
        self.assertIsNotNone(u.parse_release(release('v0.4.10'), '0.4.2'))
        self.assertIsNone(u.parse_release(release(), '0.4.2'))
        self.assertIsNone(u.parse_release(release(), '0.5.0'))
        for version in ('v0.4.2-rc1', 'v1.2', 'v1.2.3/../x'):
            with self.assertRaises(ValueError): u.version_tuple(version)

    def test_release_must_be_stable_complete_and_in_same_repository(self):
        for edit in ('draft', 'prerelease', 'missing', 'external', 'oversize'):
            data=release()
            if edit in ('draft','prerelease'): data[edit]=True
            elif edit=='missing': data['assets'].pop()
            elif edit=='external': data['assets'][0]['browser_download_url']='https://example.com/update.zip'
            else: data['assets'][0]['size']=u.MAX_ARCHIVE+1
            with self.subTest(edit=edit), self.assertRaises(ValueError): u.parse_release(data,'0.4.1')

    def test_redirects_cannot_leave_https_github_hosts(self):
        for url in ('http://github.com/x','https://example.com/x','https://github.com:444/x','https://github.com@evil.invalid/x'):
            with self.assertRaises(ValueError):u.safe_url(url)
        self.assertTrue(u.safe_url('https://release-assets.githubusercontent.com/x'))

    def test_corruption_and_cancellation_never_leave_installable_archive(self):
        r=u.parse_release(release(),'0.4.1');r['size']=3
        for cancelled in (False,True):
            cancel=threading.Event()
            if cancelled:cancel.set()
            with patch.object(u,'read_small',return_value=('0'*64+'  '+u.ASSET).encode()), patch.object(u,'open_url',return_value=io.BytesIO(b'bad')):
                with self.assertRaises(ValueError):u.download_release(r,self.root,lambda *_:None,cancel)
            self.assertFalse((self.root/u.ASSET).exists());self.assertFalse((self.root/'download.part').exists())

    def test_package_rejects_traversal_private_files_duplicates_and_wrong_version(self):
        app=self.app();data=self.root/'data'
        for name in ('MarkFlow/../escape','MarkFlow/_internal/../../escape','MarkFlow/_internal/CON.txt','MarkFlow/_internal/file:stream','MarkFlow/agent-identity.json','MarkFlow/markflow.exe'):
            z=self.archive((name,'bad'))
            with self.subTest(name=name), self.assertRaises(ValueError):u.prepare_package(z,u.sha256(z),'0.4.2',app,data)
            self.assertEqual((app/'MarkFlow.exe').read_bytes(),b'old executable')
        z=self.archive()
        with self.assertRaises(ValueError):u.prepare_package(z,u.sha256(z),'0.4.3',app,data)
        with self.assertRaises(ValueError):u.prepare_package(z,'0'*64,'0.4.2',app,data)
        with self.assertRaises(ValueError):u.prepare_package(z,u.sha256(z),'0.4.2',app,app/'data')

    def test_successful_replace_preserves_all_profile_bytes(self):
        app=self.app(); data=self.root/'profile';data.mkdir()
        for name in ('templates.json','identity.json','offsets.json'):(data/name).write_text('unchanged synthetic data')
        before=u.tree_digest(data);z=self.archive();stage=u.prepare_package(z,u.sha256(z),'0.4.2',app,data)
        states=[];u.replace_transaction(app,stage,self.root/'.markflow-backup-test',lambda _:True,Mock(),states.append)
        self.assertEqual((app/'MarkFlow.exe').read_bytes(),b'new executable');self.assertEqual(before,u.tree_digest(data));self.assertEqual(states[-1],'success')

    def test_failed_health_check_restores_old_program_and_preserves_profile(self):
        app=self.app();data=self.root/'profile';data.mkdir();(data/'settings').write_bytes(b'untouched')
        z=self.archive();stage=u.prepare_package(z,u.sha256(z),'0.4.2',app,data);relaunch=Mock();states=[]
        with self.assertRaises(RuntimeError):u.replace_transaction(app,stage,self.root/'.markflow-backup-test',lambda _:False,relaunch,states.append)
        self.assertEqual((app/'MarkFlow.exe').read_bytes(),b'old executable');self.assertEqual((data/'settings').read_bytes(),b'untouched');relaunch.assert_called_once_with(app.resolve());self.assertEqual(states[-1],'rolled_back')

    def test_busy_printing_cannot_install_and_install_blocks_start(self):
        with patch('agent_runtime.Cloud',FakeCloud):e=StudioEngine(self.root,lambda _:None)
        for field in ('running','busy','start_pending'):
            setattr(e,field,True);self.assertFalse(e.prepare_update());self.assertFalse(e.update_installing);setattr(e,field,False)
        self.assertTrue(e.prepare_update());self.assertTrue(e.local_pause);self.assertIsNone(e.enqueue('start'))

    def test_broken_proxy_falls_back_to_verified_direct_connection(self):
        import urllib.error
        proxy=Mock()
        def fail(request,**kwargs):
            request.set_proxy('127.0.0.1:1234','http')
            raise urllib.error.URLError('connection reset')
        proxy.open.side_effect=fail
        direct=Mock();direct.open.return_value=io.BytesIO(b'{}')
        with patch.object(u,'_prefer_direct',False),patch('urllib.request.getproxies',return_value={'https':'http://127.0.0.1:1234'}),patch('urllib.request.build_opener',side_effect=[proxy,direct]) as build:
            self.assertEqual(u.open_url(u.API).read(),b'{}')
            self.assertEqual(build.call_count,2)
            self.assertTrue(u._prefer_direct)
            self.assertEqual(build.call_args_list[1].args[0].proxies,{})
            self.assertEqual(direct.open.call_args.args[0].host,'api.github.com')

    def test_http_denials_and_certificate_errors_are_not_bypassed(self):
        import urllib.error,ssl
        for error in (urllib.error.HTTPError(u.API,403,'Forbidden',{},None),urllib.error.URLError(ssl.SSLCertVerificationError('certificate mismatch'))):
            opener=Mock();opener.open.side_effect=error
            with patch.object(u,'_prefer_direct',False),patch('urllib.request.getproxies',return_value={'https':'http://127.0.0.1:1234'}),patch('urllib.request.build_opener',return_value=opener) as build:
                with self.assertRaises(urllib.error.URLError):u.open_url(u.API)
                self.assertEqual(build.call_count,1)

if __name__=='__main__':unittest.main()
