"""Windows spooler adapter. Preserve vendor DEVMODE data; never scale label pixels.

Opening a DC / preflight does not call StartDoc and cannot submit a print job.
Private ribbon, sensor, speed, darkness and halftone options stay with the driver.
"""
import ctypes as c
import hashlib
import struct
import sys

def choose_paper_source(choices,mode):
    # ZMIN exposes these named bins; do not guess numeric vendor IDs.
    marker={'gap':'有定位间隙','continuous':'无定位间隙'}.get(mode)
    matches=[identifier for identifier,name in choices if marker and marker in name]
    if len(matches)!=1:raise ValueError('驱动未提供明确的纸张定位选项，请在驱动中设置后选择“沿用驱动”')
    return matches[0]


def label_devmode(raw, width, height, dpi, paper_source=None):
    """Patch only public DEVMODEW fields, preserving all vendor-private bytes."""
    if not (10 <= width <= 150 and 10 <= height <= 150 and dpi in (203, 300, 600)):
        raise ValueError('标签尺寸或 DPI 无效')
    data = bytearray(raw)
    if len(data) < 220:
        raise ValueError('驱动返回的 DEVMODEW 不完整')
    size, extra = struct.unpack_from('<HH', data, 68)
    if size < 220 or size + extra > len(data):
        raise ValueError('驱动返回的 DEVMODEW 长度无效')
    fields = struct.unpack_from('<I', data, 72)[0]
    # Custom paper overrides form name. One copy, simplex, monochrome, 100%.
    fields = (fields & ~0x10000) | 0x1 | 0x2 | 0x4 | 0x8 | 0x10 | 0x100 | 0x400 | 0x800 | 0x1000 | 0x2000
    struct.pack_into('<I', data, 72, fields)
    for offset, value in {76: 1, 78: 0, 80: round(height * 10), 82: round(width * 10),
                          84: 100, 86: 1, 90: dpi, 92: 1, 94: 1, 96: dpi}.items():
        struct.pack_into('<h', data, offset, value)
    data[102:166] = bytes(64)
    if paper_source is not None:
        if not isinstance(paper_source,int) or not 1<=paper_source<=32767:raise ValueError('纸张来源编号无效')
        struct.pack_into('<I',data,72,fields|0x200)
        struct.pack_into('<h',data,88,paper_source)
    return bytes(data)


def validate_caps(caps, width, height, dpi):
    if caps['dpi_x'] != dpi or caps['dpi_y'] != dpi:
        raise ValueError(f"驱动实际分辨率为 {caps['dpi_x']} × {caps['dpi_y']} dpi，请重新选择 DPI 并准备标签")
    expected = (round(width * dpi / 25.4), round(height * dpi / 25.4))
    if any(abs(caps[key] - value) > 1 for key, value in zip(('width_px', 'height_px'), expected)):
        raise ValueError(f"驱动未接受 {width} × {height} mm 标签纸尺寸，已停止提交")
    if caps['printable_width'] <= 0 or caps['printable_height'] <= 0:
        raise ValueError('驱动未返回有效的可打印区域')
    return expected


class WindowsPrinter:
    def __init__(self, name, width, height, dpi, paper_mode='driver'):
        if sys.platform != 'win32':
            raise RuntimeError('此打印通道仅用于 Windows')
        if not name or '\0' in name:
            raise ValueError('请选择有效的系统打印机')
        self.name, self.width, self.height, self.dpi = name, width, height, dpi
        self.dc = None
        self.spool = c.WinDLL('winspool.drv', use_last_error=True)
        self.gdi = c.WinDLL('gdi32', use_last_error=True)
        ptr, integer, string = c.c_void_p, c.c_int, c.c_wchar_p
        signatures = [
            (self.spool, 'OpenPrinterW', integer, [string, c.POINTER(ptr), ptr]),
            (self.spool, 'ClosePrinter', integer, [ptr]),
            (self.spool, 'DocumentPropertiesW', c.c_long, [ptr, ptr, string, ptr, ptr, c.c_uint32]),
            (self.spool, 'DeviceCapabilitiesW', integer, [string,string,c.c_uint16,ptr,ptr]),
            (self.gdi, 'CreateDCW', ptr, [string, string, string, ptr]),
            (self.gdi, 'DeleteDC', integer, [ptr]),
            (self.gdi, 'GetDeviceCaps', integer, [ptr, integer]),
            (self.gdi, 'StartDocW', integer, [ptr, ptr]),
            (self.gdi, 'StartPage', integer, [ptr]),
            (self.gdi, 'EndPage', integer, [ptr]),
            (self.gdi, 'EndDoc', integer, [ptr]),
            (self.gdi, 'AbortDoc', integer, [ptr]),
            (self.gdi, 'StretchDIBits', integer, [ptr] + [integer] * 8 + [ptr, ptr, c.c_uint32, c.c_uint32]),
        ]
        for lib, key, result, args in signatures:
            fn = getattr(lib, key); fn.restype = result; fn.argtypes = args
        handle = ptr()
        if not self.spool.OpenPrinterW(name, c.byref(handle), None):
            raise OSError('无法打开所选打印队列')
        try:
            length = self.spool.DocumentPropertiesW(None, handle, name, None, None, 0)
            if not 220 <= length <= 1024 * 1024:
                raise OSError('驱动设置缓冲区大小无效')
            devmode = c.create_string_buffer(length)
            if self.spool.DocumentPropertiesW(None, handle, name, devmode, None, 2) != 1:
                raise OSError('无法读取驱动首选项')
            source=None;self.paper_mode=paper_mode
            if paper_mode not in ('driver','gap','continuous'):raise ValueError('纸张定位方式无效')
            if paper_mode!='driver':
                count=self.spool.DeviceCapabilitiesW(name,None,6,None,None)
                if not 0<count<256:raise ValueError('无法读取纸张来源，请在驱动中设置并选择“沿用驱动”')
                ids=(c.c_uint16*count)();names=c.create_unicode_buffer(count*24)
                if self.spool.DeviceCapabilitiesW(name,None,6,ids,None)!=count or self.spool.DeviceCapabilitiesW(name,None,12,names,None)!=count:raise ValueError('读取纸张来源失败')
                choices=[(int(ids[i]),''.join(names[i*24:(i+1)*24]).split('\0')[0]) for i in range(count)]
                source=choose_paper_source(choices,paper_mode)
            devmode.raw = label_devmode(devmode.raw, width, height, dpi,source)
            if self.spool.DocumentPropertiesW(None, handle, name, devmode, devmode, 10) != 1:
                raise OSError('驱动拒绝标签设置')
            for offset, expected in ((76, 1), (84, 100), (86, 1), (94, 1)):
                if struct.unpack_from('<h', devmode.raw, offset)[0] != expected:
                    raise ValueError('驱动未接受单份、单面或 100% 尺寸设置，已停止提交')
            self.paper_source=struct.unpack_from('<h',devmode.raw,88)[0]
            if source is not None and self.paper_source!=source:raise ValueError('驱动未接受指定的纸张定位方式，已停止提交')
            self.devmode_digest = hashlib.sha256(devmode.raw).hexdigest()
            self.dc = self.gdi.CreateDCW('WINSPOOL', name, None, devmode)
            if not self.dc:
                raise OSError('无法创建打印机绘图上下文')
            keys = {'dpi_x': 88, 'dpi_y': 90, 'width_px': 110, 'height_px': 111,
                    'offset_x': 112, 'offset_y': 113, 'printable_width': 8, 'printable_height': 10}
            self.caps = {key: self.gdi.GetDeviceCaps(self.dc, value) for key, value in keys.items()}
            validate_caps(self.caps, width, height, dpi)
        except Exception:
            self.close()
            raise
        finally:
            self.spool.ClosePrinter(handle)

    def close(self):
        if self.dc:
            self.gdi.DeleteDC(self.dc); self.dc = None

    def __enter__(self): return self
    def __exit__(self, *args): self.close()

    def check_image(self, image):
        from PySide6.QtGui import QImage
        if image.isNull() or (image.width(), image.height()) != (round(self.width*self.dpi/25.4), round(self.height*self.dpi/25.4)):
            raise ValueError('标签图像尺寸与打印机设置不一致，请重新准备标签')
        # Refuse clipped ink, including on printers with nonzero physical margins.
        gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
        raw = bytes(gray.constBits()); stride = gray.bytesPerLine(); caps = self.caps
        x0, y0 = caps['offset_x'], caps['offset_y']
        x1, y1 = x0 + caps['printable_width'], y0 + caps['printable_height']
        for y in range(image.height()):
            for x, value in enumerate(raw[y*stride:y*stride+image.width()]):
                if value < 128 and not (x0 <= x < x1 and y0 <= y < y1):
                    raise ValueError('标签内容超出驱动可打印区域，请调整模板留白')

    def proof(self):
        return {'printer': self.name, **self.caps, 'driver_settings_digest': self.devmode_digest,
                'copies': 1, 'scale_percent': 100,'paper_mode':self.paper_mode,'paper_source':self.paper_source}

    def print_image(self, image, title='Label Desk'):
        from PySide6.QtGui import QImage
        self.check_image(image)
        class DocInfo(c.Structure):
            _fields_ = [('cbSize', c.c_int), ('docName', c.c_wchar_p), ('output', c.c_wchar_p),
                        ('dataType', c.c_wchar_p), ('type', c.c_uint32)]
        doc = DocInfo(c.sizeof(DocInfo), title, None, None, 0)
        raster = image.convertToFormat(QImage.Format.Format_RGB32)
        pixels = c.create_string_buffer(bytes(raster.constBits()))
        # RGB32 on Windows is BGRX. Negative DIB height supplies top-down rows.
        header = c.create_string_buffer(struct.pack('<IiiHHIIiiII', 40, raster.width(), -raster.height(), 1, 32, 0,
                                                     raster.sizeInBytes(), 0, 0, 0, 0))
        job = self.gdi.StartDocW(self.dc, c.byref(doc))
        if job <= 0:
            raise OSError('系统打印任务未能启动，请检查打印队列')
        try:
            if self.gdi.StartPage(self.dc) <= 0: raise OSError('系统打印页启动失败')
            count = self.gdi.StretchDIBits(self.dc, -self.caps['offset_x'], -self.caps['offset_y'],
                raster.width(), raster.height(), 0, 0, raster.width(), raster.height(), pixels, header, 0, 0x00CC0020)
            if count <= 0: raise OSError('标签光栅提交失败')
            if self.gdi.EndPage(self.dc) <= 0: raise OSError('打印页结束失败')
            if self.gdi.EndDoc(self.dc) <= 0: raise OSError('打印任务结束失败')
        except Exception:
            self.gdi.AbortDoc(self.dc)
            raise
        return {'status': 'submitted', 'message': f'已交给 Windows 打印队列（任务 {job}），等待纸面确认', 'job_id': job}
