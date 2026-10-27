import base64
import json
import struct
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from label_contract import decode_logo_png,execute_data
from label_render import printable_logo

class LogoTests(unittest.TestCase):
    def setUp(self):
        self.bundle=json.loads((Path(__file__).resolve().parents[1]/'desktop/examples/ringhi-mac.labeljob').read_text(encoding='utf-8'))
        self.encoded=base64.b64encode((Path(__file__).resolve().parents[1]/'assets/ringhi-original.png').read_bytes()).decode()
    def test_original_asset_decoder_unchanged(self):
        import hashlib
        self.assertEqual(hashlib.sha256(decode_logo_png(self.encoded)).hexdigest(),'a4221cc087e7e810e1c5ebeb147846d946cfc194f110bdc817caf1c44a197053')
    def test_logo_black_white_silhouette_retains_white_space(self):
        logo=printable_logo(self.encoded)
        self.assertEqual(logo.pixel(0,0),0xffffffff)
        self.assertEqual(logo.pixel(65,200),0xff000000)
        self.assertEqual(logo.pixel(160,160),0xffffffff)
        self.assertEqual(logo.pixel(137,189),0xff000000)
    def test_external_or_oversized_data_rejected(self):
        for value in ('file:///secret.png','https://example.com/logo.png','@'*10,'A'*1500001):
            with self.subTest(value=value[:30]),self.assertRaises(ValueError):decode_logo_png(value)
        raw=bytearray(decode_logo_png(self.encoded));struct.pack_into('>I',raw,16,90000)
        with self.assertRaises(ValueError):decode_logo_png(base64.b64encode(raw).decode())
    def test_two_mac_lines_reconstruct_full_address(self):
        v=execute_data(self.bundle,{'mac':'02:00:00:00:ab:cd','mac_first3':'FAKE','mac_last3':'FAKE'})
        self.assertEqual(v['mac_first3']+':'+v['mac_last3'],'02:00:00:00:AB:CD')
        self.assertEqual(v['device_id'],'02000000ABCD')

if __name__=='__main__':unittest.main()
