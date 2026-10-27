import struct
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'desktop'))
from windows_print import WindowsPrinter, label_devmode, validate_caps,choose_paper_source


class WindowsPrintTests(unittest.TestCase):
    def test_gap_paper_explicit_source_not_continuous_or_auto(self):
        choices=[(15,'Automatically Select'),(256,'有定位间隙/黑线'),(257,'无定位间隙/黑线')]
        self.assertEqual(choose_paper_source(choices,'gap'),256)
        self.assertEqual(choose_paper_source(choices,'continuous'),257)
        with self.assertRaises(ValueError):choose_paper_source([(15,'Auto')],'gap')
        raw=bytearray(284);struct.pack_into('<HH',raw,68,220,64);raw[220:]=bytes(range(64))
        result=label_devmode(raw,30,20,300,256)
        self.assertEqual(struct.unpack_from('<h',result,88)[0],256)
        self.assertTrue(struct.unpack_from('<I',result,72)[0]&0x200)
        self.assertEqual(result[220:],raw[220:])
    def test_preserve_private_ribbon_sensor_halftone_data(self):
        raw=bytearray(284);struct.pack_into('<HHI',raw,68,220,64,0x10000)
        raw[220:]=bytes(range(64));raw[102:166]='旧纸张'.encode('utf-16le').ljust(64,b'\0')
        patched=label_devmode(raw,30,20,300)
        self.assertEqual(patched[220:],raw[220:])
        self.assertEqual(struct.unpack_from('<5h',patched,76),(1,0,200,300,100))
        self.assertEqual(struct.unpack_from('<h',patched,86)[0],1)
        self.assertEqual(struct.unpack_from('<h',patched,90)[0],300)
        self.assertEqual(struct.unpack_from('<h',patched,96)[0],300)
        self.assertEqual(patched[102:166],bytes(64))
        self.assertEqual(struct.unpack_from('<I',patched,72)[0]&0x10000,0)

    def test_invalid_settings_and_truncated_devmode_rejected(self):
        with self.assertRaises(ValueError):label_devmode(bytes(100),30,20,300)
        with self.assertRaises(ValueError):label_devmode(bytes(220),30,20,300)
        raw=bytearray(220);struct.pack_into('<HH',raw,68,220,30)
        with self.assertRaises(ValueError):label_devmode(raw,30,20,300)
        with self.assertRaises(ValueError):label_devmode(raw,30,20,96)

    def test_actual_driver_geometry_must_match(self):
        caps={'dpi_x':300,'dpi_y':300,'width_px':354,'height_px':236,
              'printable_width':354,'printable_height':236}
        self.assertEqual(validate_caps(caps,30,20,300),(354,236))
        for delta in ({'dpi_x':203},{'dpi_y':600},{'width_px':2480},
                      {'height_px':354},{'printable_width':0}):
            with self.subTest(delta=delta),self.assertRaises(ValueError):
                validate_caps({**caps,**delta},30,20,300)

    def sample_printer(self):
        p=WindowsPrinter.__new__(WindowsPrinter);p.width=30;p.height=20;p.dpi=300;p.dc=1
        p.caps={'dpi_x':300,'dpi_y':300,'width_px':354,'height_px':236,
                'offset_x':2,'offset_y':2,'printable_width':350,'printable_height':232}
        return p

    def sample_image(self):
        from PySide6.QtGui import QImage
        image=QImage(354,236,QImage.Format.Format_RGB32);image.fill(0xffffffff)
        image.setPixel(20,20,0xff000000)
        return image

    def test_printable_margins_allow_white_but_reject_clipped_ink(self):
        p=self.sample_printer();image=self.sample_image();p.check_image(image)
        image.setPixel(0,0,0xff000000)
        with self.assertRaises(ValueError):p.check_image(image)

    def test_wrong_image_geometry_rejected_before_submission(self):
        from PySide6.QtGui import QImage
        with self.assertRaises(ValueError):self.sample_printer().check_image(QImage(240,160,QImage.Format.Format_RGB32))

    def test_gdi_uses_equal_source_and_target_sizes_and_offsets(self):
        p=self.sample_printer();calls=[]
        class Gdi:
            def StartDocW(self,*args):calls.append('start');return 42
            def StartPage(self,*args):return 1
            def StretchDIBits(self,*args):calls.append(args[1:9]);return 236
            def EndPage(self,*args):return 1
            def EndDoc(self,*args):calls.append('end');return 1
            def AbortDoc(self,*args):calls.append('abort');return 1
        p.gdi=Gdi();result=p.print_image(self.sample_image())
        self.assertEqual(result['job_id'],42)
        self.assertEqual(calls,['start',(-2,-2,354,236,0,0,354,236),'end'])

    def test_gdi_failure_aborts_and_does_not_report_success(self):
        p=self.sample_printer();calls=[]
        class Gdi:
            def StartDocW(self,*args):calls.append('start');return 42
            def StartPage(self,*args):return 1
            def StretchDIBits(self,*args):return 236
            def EndPage(self,*args):return 0
            def AbortDoc(self,*args):calls.append('abort');return 1
        p.gdi=Gdi()
        with self.assertRaises(OSError):p.print_image(self.sample_image())
        self.assertEqual(calls,['start','abort'])


if __name__=='__main__':unittest.main()
