"""C ABI adapter. Called only in a child process, Windows x64."""
import base64
import ctypes as c
import json
import os
import sys
import tempfile
from pathlib import Path

class USBInfo(c.Structure):
    _fields_=[('printerName',c.c_char*64),('portName',c.c_char*32),('serialNo',c.c_char*32)]
class PrinterSDK:
    def __init__(self,path=None):
        if sys.platform!='win32':raise RuntimeError('厂商原生 SDK 目前仅接入 Windows；Mac 可使用已安装驱动的系统打印机')
        if path is None:path=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'sdk'/'LP_LabelPrinter.dll'
        self.lib=c.CDLL(str(path));self.f={}
        ptr=c.c_void_p;flt=c.c_float;integer=c.c_int;s=c.c_char_p
        specs={
          'LpPrinter_Create':(ptr,[]),'LpPrinter_Destroy':(None,[ptr]),'LpPrinter_Disconnect':(None,[ptr]),
          'LpPrinter_Connect':(integer,[ptr]),'LpPrinter_EnumUsb':(integer,[c.POINTER(USBInfo),integer]),
          'LpPrinter_GetStatusCode':(integer,[ptr]),'LpPrinter_SetUsbSerial':(None,[ptr,s]),
          'LpPrinter_SetNetAddress':(None,[ptr,s]),'LpPrinter_SetName':(None,[ptr,s]),
          'LpLabel_Create':(ptr,[]),'LpLabel_Destroy':(None,[ptr]),
          'LpLabel_AddImage':(integer,[ptr,s,flt,flt,flt,s]),'LpLabel_SetImageScale':(integer,[ptr,integer,flt,flt]),
          'LpLabel_Print':(integer,[ptr,ptr]),'LpLabel_SavePreview':(integer,[ptr,ptr,s]),
          'LpGetLastErrorMessage':(s,[]),
        }
        for name in ['Interface','Dpi','Speed','Darkness','PageDirection']:specs['LpPrinter_Set'+name]=(None,[ptr,integer])
        for name in ['Width','Height','RowGap','ColumnGap','LeftOffset','TopOffset']:specs['LpLabel_Set'+name]=(None,[ptr,flt])
        for name in ['RowCount','ColumnCount']:specs['LpLabel_Set'+name]=(None,[ptr,integer])
        for name,(restype,argtypes) in specs.items():
            fn=getattr(self.lib,name);fn.restype=restype;fn.argtypes=argtypes;self.f[name]=fn
    def error(self):return (self.f['LpGetLastErrorMessage']() or b'unknown').decode('utf-8',errors='replace')
    def enum(self):
        array=(USBInfo*32)();n=self.f['LpPrinter_EnumUsb'](array,32)
        if n<0:raise RuntimeError('枚举打印机失败：'+self.error())
        decode=lambda v:v.decode('utf-8',errors='replace')
        return [{'name':decode(v.printerName),'port':decode(v.portName),'serial':decode(v.serialNo)} for v in array[:min(n,32)]]
    def run(self,request):
        f=self.f;printer=f['LpPrinter_Create']();label=f['LpLabel_Create']()
        if not printer or not label:raise RuntimeError('SDK 对象初始化失败')
        connected=False
        try:
            mode=request.get('interface','usb');f['LpPrinter_SetInterface'](printer,1 if mode=='usb' else 2)
            for key,fn in [('dpi','Dpi'),('speed','Speed'),('darkness','Darkness')]:f['LpPrinter_Set'+fn](printer,int(request[key]))
            if mode=='usb':f['LpPrinter_SetUsbSerial'](printer,request.get('serial','').encode())
            else:f['LpPrinter_SetNetAddress'](printer,request.get('ip','').encode())
            for key in ('width','height'):f['LpLabel_Set'+key.title()](label,float(request[key]))
            f['LpLabel_SetRowCount'](label,1);f['LpLabel_SetColumnCount'](label,1)
            f['LpLabel_SetRowGap'](label,float(request.get('gap',2)))
            for name in ['ColumnGap','LeftOffset','TopOffset']:f['LpLabel_Set'+name](label,0)
            # Vendor DLL accepts PNG without error but renders it blank on this SDK build.
            # Convert to uncompressed BMP, then verify the SDK raster before any submission.
            from PySide6.QtGui import QImage
            from PySide6.QtCore import QBuffer,QIODevice
            source=QImage(request['image'])
            if source.isNull():raise ValueError('标签图像不可读取')
            buffer=QBuffer();buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not source.convertToFormat(QImage.Format.Format_RGB888).save(buffer,'BMP'):raise ValueError('BMP 转换失败')
            data=bytes(buffer.data());buffer.close()
            idx=f['LpLabel_AddImage'](label,b'image-label',0,0,1,base64.b64encode(data))
            if idx<0:raise RuntimeError('标签图像转换失败：'+self.error())
            with tempfile.TemporaryDirectory(prefix='label-raster-') as directory:
                preview=str(Path(directory)/'sdk.bmp')
                result=f['LpLabel_SavePreview'](label,printer,preview.encode('utf-8'))
                if result!=0:raise RuntimeError('SDK 预览失败：'+self.error())
                rendered=QImage(preview)
                if rendered.isNull() or abs(source.width()-rendered.width())>1 or abs(source.height()-rendered.height())>1:raise RuntimeError('SDK 渲染尺寸不一致，已停止提交')
                width=max(source.width(),rendered.width());height=max(source.height(),rendered.height())
                def mask(image):
                    gray=image.convertToFormat(QImage.Format.Format_Grayscale8);raw=bytes(gray.constBits());stride=gray.bytesPerLine()
                    result=bytearray()
                    for y in range(height):
                        row=raw[y*stride:y*stride+gray.width()] if y<gray.height() else b''
                        result.extend(int(v<128) for v in row);result.extend(bytes(width-len(row)))
                    return result
                original=mask(source);actual=mask(rendered)
                if not any(original) or original!=actual:raise RuntimeError('SDK 渲染内容与预览不一致，已停止提交')
                if request['action']=='preview':
                    import shutil
                    shutil.copyfile(preview,request['output'])
                    return {'status':'preview','output':request['output'],'raster_verified':True}
            if request['action']!='print':raise ValueError('不支持的 SDK 操作')
            if mode=='usb' and not request.get('serial'):raise ValueError('必须明确选择 USB 打印机')
            if mode=='net':
                import ipaddress
                ipaddress.IPv4Address(request.get('ip',''))
            if f['LpPrinter_Connect'](printer)!=0:raise RuntimeError('连接打印机失败：'+self.error())
            connected=True;status=f['LpPrinter_GetStatusCode'](printer)
            if status!=0:raise RuntimeError(f'打印机未就绪，状态码 {status}。未提交标签。')
            result=f['LpLabel_Print'](label,printer)
            if result!=0:return {'status':'unknown','message':'SDK 打印调用未成功返回，需人工核对纸面后再决定补打：'+self.error()}
            return {'status':'submitted','message':'SDK 已接受提交；等待纸面或扫码确认'}
        finally:
            if connected:f['LpPrinter_Disconnect'](printer)
            f['LpLabel_Destroy'](label);f['LpPrinter_Destroy'](printer)

def worker(filename):
    req=json.loads(Path(filename).read_text(encoding='utf-8'));sdk=PrinterSDK()
    result={'printers':sdk.enum()} if req['action']=='enumerate' else sdk.run(req)
    return result
