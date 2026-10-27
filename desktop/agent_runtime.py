"""Cloud-bound, unattended station; only fixed print actions are accepted."""
import base64
import ctypes
import datetime
import json
import os
import platform
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from label_contract import validate_bundle,bundle_digest,execute_data,MAX_BUNDLE_BYTES
from workstation import atomic_json,CalibrationStore,printer_key,ArrivalGate

VERSION='0.4.2'
ORIGIN='https://toolbox.infinlab.cn'

class CloudError(RuntimeError):
    def __init__(self,status,message):self.status=status;super().__init__(message)

def protect(value,decrypt=False):
    if sys.platform!='win32':raise RuntimeError('机器凭据保护当前仅开放 Windows，macOS 联网工站将在 Keychain 适配后提供')
    from ctypes import wintypes
    class Blob(ctypes.Structure):_fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_byte))]
    raw=base64.b64decode(value) if decrypt else value.encode()
    buf=ctypes.create_string_buffer(raw);source=Blob(len(raw),ctypes.cast(buf,ctypes.POINTER(ctypes.c_byte)));target=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    fn=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)];fn.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):raise ctypes.WinError(ctypes.get_last_error())
    try:result=ctypes.string_at(target.data,target.size)
    finally:kernel.LocalFree(target.data)
    return result.decode() if decrypt else base64.b64encode(result).decode()

class Cloud:
    def __init__(self,directory):
        p=directory/'agent-identity.json'
        if p.exists():
            identity=json.loads(p.read_text(encoding='utf-8'));self.id=str(uuid.UUID(identity['id']));self.token=protect(identity['protected_token'],True)
        else:
            self.id=str(uuid.uuid4());self.token=secrets.token_urlsafe(32)
            atomic_json(p,{'id':self.id,'protected_token':protect(self.token)})
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):raise CloudError(400,'机器接口发生重定向，已停止连接')
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def call(self,route,data=None):
        body={'id':self.id,**(data or {})}
        request=urllib.request.Request(ORIGIN+'/api/label-agent/v1/'+route,data=json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token,'User-Agent':'LabelDeskAgent/'+VERSION},method='POST')
        try:
            with self.opener.open(request,timeout=12) as response:raw=response.read(MAX_BUNDLE_BYTES*2+65536)
        except urllib.error.HTTPError as e:
            try:
                error=json.loads(e.read(4096));message=error.get('message') or error.get('error',{}).get('message') or ('HTTP '+str(e.code))
            except Exception:message='HTTP '+str(e.code)
            raise CloudError(e.code,str(message)) from e
        if len(raw)>=MAX_BUNDLE_BYTES*2+65536:raise CloudError(400,'服务端数据过大')
        return json.loads(raw)

class Engine:
    def __init__(self,directory,emit):
        self.dir=Path(directory);self.dir.mkdir(parents=True,exist_ok=True);self.emit=emit;self.cloud=Cloud(self.dir)
        self.stop=threading.Event();self.local_pause=False;self.assignment=None;self.gate=None;self.bound=False;self.paused=True
        self.last_error='';self.message='连接工站服务';self.last_report=0;self.last_inventory=0;self.inventory={};self.last_job=None;self.connection_error=False
        self.jobs=self.dir/'agent-jobs';self.jobs.mkdir(exist_ok=True);self.commands=self.dir/'agent-commands';self.commands.mkdir(exist_ok=True)
        from label_store import LocalStore
        self.local=LocalStore(self.dir/'production.sqlite3')
        for p in self.jobs.glob('*.json'):
            j=json.loads(p.read_text(encoding='utf-8'))
            if j['status'] in ('claim_pending','claimed','sending'):
                j.update(status='unknown',message='工站执行时中断，请核对纸面；禁止自动重打',reported=False,recover=True);atomic_json(p,j)
        for p in self.commands.glob('*.json'):
            j=json.loads(p.read_text(encoding='utf-8'))
            if not j.get('done'):j.update(done=True,reported=False,message='单次任务在工站中断，未自动重试');atomic_json(p,j)
    def update(self,**values):
        snapshot={'station_id':self.cloud.id,'version':VERSION,'bound':self.bound,'message':self.message,'error':self.last_error,'assignment':self.assignment['name'] if self.assignment else None,'work_order':self.assignment['work_order'] if self.assignment else None,'local_pause':self.local_pause,'paused':self.paused,'last_job':self.last_job,**values}
        atomic_json(self.dir/'agent-status.json',snapshot);self.emit(snapshot)
    def devices(self):
        import serial.tools.list_ports
        from PySide6.QtPrintSupport import QPrinterInfo
        ports=list(serial.tools.list_ports.comports())
        printers=QPrinterInfo.availablePrinterNames()
        try:settings=json.loads((self.dir/'printer-settings.json').read_text(encoding='utf-8'))
        except FileNotFoundError:settings={}
        self.inventory={'printers':printers[:30],'ports':[{'port':p.device,'vid':p.vid,'pid':p.pid,'description':p.description[:100]} for p in ports[:50]],'current_printer':{k:settings[k] for k in ('name','dpi','paper_mode','offset_x_mm','offset_y_mm') if k in settings},'message':self.message[-500:],'error':self.last_error[-1000:],'local_pause':self.local_pause,'capabilities':['embedded_png','align','esp32_mac','system_print','calibration','idempotent_jobs'],'contract':1}
        return ports
    def prepare_image(self,a,source):
        from label_render import render_label,translate_label
        from system_print import preflight
        from label_agent_config import checked_config
        cfg=checked_config(a['config']);bundle=validate_bundle(a['bundle'])
        if bundle_digest(bundle)!=a['digest']:raise ValueError('模板摘要不匹配')
        fields=execute_data(bundle,source);image,warnings=render_label(bundle['template'],fields,cfg['dpi'])
        if warnings:raise ValueError('；'.join(warnings))
        image=translate_label(image,cfg['offset_x_mm'],cfg['offset_y_mm'],cfg['dpi'])
        proof=preflight(cfg['name'],bundle['template'],cfg['dpi'],image,cfg['paper_mode'])
        return bundle,fields,image,proof,cfg
    def apply(self,a):
        if a.get('automatic') not in (0,1):raise ValueError('自动执行参数无效')
        if a['bundle']['workflow']['steps'][0]['type']!='read_esp32_mac':raise ValueError('此 Agent 当前只支持真实 ESP32 MAC 数据源')
        self.prepare_image(a,{'mac':'02:00:00:00:AB:CD','model':'ESP32','date':datetime.date.today().isoformat()})
        cfg=a['config'];key=printer_key({'interface':'system','name':cfg['name']})
        CalibrationStore(self.dir/'calibration.json').save(key,cfg['offset_x_mm'],cfg['offset_y_mm'])
        atomic_json(self.dir/'printer-settings.json',{'interface':'system','serial':'','ip':'',**{k:cfg[k] for k in ('name','dpi','paper_mode','offset_x_mm','offset_y_mm')}})
        atomic_json(self.dir/'active.labeljob',a['bundle']);atomic_json(self.dir/'agent-assignment.json',a)
        self.cloud.call('applied',{'assignment':a['id']})
        old=self.assignment;self.assignment=a
        if not self.gate or not old or (old['config']['vid'],old['config']['pid'])!=(cfg['vid'],cfg['pid']):self.gate=ArrivalGate(cfg['vid'],cfg['pid'])
        self.last_error='';self.message='模板已生效，等待 ESP32 接入';self.update()
    def flush(self):
        for p in self.jobs.glob('*.json'):
            j=json.loads(p.read_text(encoding='utf-8'))
            if j.get('reported') or j['status'] not in ('submitted','failed','unknown'):continue
            if not j.get('job_id'):
                found=self.cloud.call('recover',{'request_id':j['request_id']})
                if not found['job_id']:j['reported']=True;atomic_json(p,j);continue
                j['job_id']=found['job_id']
                if found['status'] in ('submitted','failed','unknown'):j['status']=found['status']
            self.cloud.call('result',{'job_id':j['job_id'],'status':j['status'],'message':j['message'],'details':j.get('details',{})})
            j['reported']=True;atomic_json(p,j)
        for p in self.commands.glob('*.json'):
            j=json.loads(p.read_text(encoding='utf-8'))
            if j.get('done') and not j.get('reported'):
                self.cloud.call('command-result',{'command':j['id'],'message':j['message']});j['reported']=True;atomic_json(p,j)
    def record_failure(self,phase,error):
        self.last_error=str(error)[:1000];self.message='执行失败，等待新设备或网页重试' if phase=='execute' else '连接异常，暂停新任务'
        atomic_json(self.dir/'agent-last-error.json',{'time':datetime.datetime.now().isoformat(),'phase':phase,'message':self.last_error})
        self.update()
    def execute(self,port,request_id=None):
        try:return self._execute(port,request_id)
        except Exception as e:
            self.record_failure('execute',e)
            raise
    def _execute(self,port,request_id=None):
        from app import read_mac
        from system_print import submit
        a=self.assignment
        if not a or self.paused or self.local_pause:raise ValueError('工站未就绪或已暂停')
        rid=request_id or str(uuid.uuid4());path=self.jobs/(rid+'.json')
        if path.exists():return '此请求已在本机记录，不重复执行'
        self.message='读取 '+port+' 的真实 MAC';self.update()
        source=read_mac(port);source.update(model=source['chip'],date=datetime.date.today().isoformat())
        bundle,fields,image,proof,cfg=self.prepare_image(a,source)
        if self.local_pause:raise ValueError('本机已停止，未提交打印')
        j={'request_id':rid,'assignment':a['id'],'mac':source['mac'],'status':'claim_pending','reported':False,'message':'等待服务端认领'};atomic_json(path,j)
        try:
            claim=self.cloud.call('claim',{'assignment':a['id'],'request_id':rid,'mac':source['mac'],'model':source['chip']})
            if claim['duplicate']:
                j.update(status='duplicate',reported=True,message='同一工单内已打印或结果待核查，跳过重复设备',job_id=claim['job_id']);atomic_json(path,j);self.message=j['message'];self.last_error='';self.update();return j['message']
            j.update(job_id=claim['job_id'],status='claimed');atomic_json(path,j)
            if self.local_pause:raise ValueError('本机已停止，未提交打印')
            local_id=self.local.start(fields,bundle,a['digest'],{'interface':'system',**cfg,'cloud_assignment':a['id'],'cloud_job':j['job_id']},'云端工单：'+a['work_order']);j['local_id']=local_id
            j.update(status='sending',message='提交打印前已持久化');atomic_json(path,j)
            self.message='正在提交 1 张标签';self.update()
            image.save(str(self.dir/'agent-last-label.png'))
            reply=submit(cfg['name'],bundle['template'],cfg['dpi'],image,proof,cfg['paper_mode'])
            j.update(status=reply['status'],message=reply['message'],details={'driver':proof,'printer':cfg,'queue_job_id':reply.get('job_id'),'live_mac_read':True,'mac':source['mac'],'paper_confirmed':False,'local_record':local_id,'template_digest':a['digest']});atomic_json(path,j)
            self.local.finish(local_id,j['status'],j['message']);self.last_job={k:j[k] for k in ('job_id','mac','status','message')};self.message=j['message'];self.last_error=''
        except Exception as e:
            j.update(status='unknown' if j['status'] in ('sending','claim_pending') else 'failed',message=str(e)[:1500]);atomic_json(path,j)
            if j.get('local_id'):self.local.finish(j['local_id'],j['status'],j['message'])
            self.local_pause=True;self.last_error=j['message'];raise
        finally:self.update()
        self.flush();return j['message']
    def run(self):
        while not self.stop.is_set():
            try:
                if not self.bound:
                    result=self.cloud.call('enroll',{'host':socket.gethostname()[:100],'platform':platform.system()+' '+platform.release(),'version':VERSION})
                    self.bound=result['bound']
                    if not self.bound:
                        self.message='请在网页工站管理输入配对码';self.last_error='';self.update(pair_code=result['pair_code'],expires=result['expires']);self.stop.wait(5);continue
                self.flush();ports=self.devices()
                status=self.cloud.call('poll',{'version':VERSION,'inventory':self.inventory,'applied':self.assignment['id'] if self.assignment else None})
                self.last_report=time.monotonic();self.paused=status['paused']
                if self.connection_error:
                    self.connection_error=False;self.last_error='';self.message='连接已恢复，等待 ESP32 接入'
                if status['assignment']:
                    try:self.apply(status['assignment'])
                    except Exception as e:
                        self.last_error=str(e);self.cloud.call('applied',{'assignment':status['assignment']['id'],'error':str(e)[:1000]});self.assignment=None;self.update();self.stop.wait(5);continue
                self.update(online=True,name=status['name'])
                if self.assignment and not self.paused and not self.local_pause:
                    matching=[p for p in ports if p.vid==self.assignment['config']['vid'] and p.pid==self.assignment['config']['pid']]
                    if status['commands']:
                        command=status['commands'][0];p=self.commands/(command['id']+'.json')
                        if not p.exists():
                            c={'id':command['id'],'done':False,'reported':False};atomic_json(p,c)
                            try:
                                if len(matching)!=1:raise ValueError('须连接且只连接一台匹配的 ESP32')
                                c['message']=self.execute(matching[0].device,command['id'])
                            except Exception as e:c['message']=str(e)[:1000];self.last_error=c['message']
                            c['done']=True;atomic_json(p,c);self.flush()
                    elif self.assignment['automatic']:
                        port=self.gate.poll(ports,time.monotonic(),ready=True)
                        if port:
                            try:self.execute(port.device)
                            except Exception:pass  # The consumed arrival is never automatically retried.
                elif self.gate:self.gate.poll(ports,time.monotonic(),ready=False)
            except Exception as e:
                self.connection_error=True;self.record_failure('connection',e);self.update(online=False)
                if isinstance(e,CloudError) and e.status in (401,403):self.bound=False
            self.stop.wait(3)

def main():
    from PySide6.QtCore import QObject,Signal,QStandardPaths,QLockFile,QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QPlainTextEdit,QMessageBox,QSystemTrayIcon,QMenu,QStyle
    app=QApplication.instance() or QApplication(sys.argv);app.setOrganizationName('InfinLab');app.setApplicationName('LabelDesk')
    directory=Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation));directory.mkdir(parents=True,exist_ok=True)
    lock=QLockFile(str(directory/'workstation.lock'))
    if not lock.tryLock(100):
        if '--agent-headless' not in sys.argv:QMessageBox.information(None,'Label Desk','工站已经运行，请从系统托盘打开。')
        return 0
    class Bridge(QObject):changed=Signal(dict)
    bridge=Bridge();window=QWidget();window.setWindowTitle('Label Desk · 联网工站 0.3.1');window.resize(650,510)
    layout=QVBoxLayout(window);layout.setContentsMargins(24,24,24,24)
    title=QLabel('Label Desk · 自动生产工站');title.setStyleSheet('font-size:24px;font-weight:bold');layout.addWidget(title)
    hint=QLabel('首次在 Toolbox 网页绑定电脑；绑定后自动接收已分配模板。');hint.setWordWrap(True);layout.addWidget(hint)
    pairing=QLabel('正在连接…');pairing.setStyleSheet('font-size:28px;font-weight:bold');pairing.setTextInteractionFlags(__import__('PySide6.QtCore',fromlist=['Qt']).Qt.TextInteractionFlag.TextSelectableByMouse);layout.addWidget(pairing)
    description=QLabel();description.setWordWrap(True);layout.addWidget(description)
    log=QPlainTextEdit();log.setReadOnly(True);layout.addWidget(log,1)
    buttons=QHBoxLayout();pause=QPushButton('本机暂停');open_web=QPushButton('打开网页管理');buttons.addWidget(pause);buttons.addWidget(open_web);layout.addLayout(buttons)
    engine=Engine(directory,bridge.changed.emit)
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtCore import QUrl
    open_web.clicked.connect(lambda:QDesktopServices.openUrl(QUrl(ORIGIN+'/tools/label-studio/')))
    def toggle():engine.local_pause=not engine.local_pause;pause.setText('恢复本机' if engine.local_pause else '本机暂停');engine.update()
    pause.clicked.connect(toggle);last_text=[None]
    def changed(s):
        pairing.setText('配对码：'+s['pair_code'] if s.get('pair_code') else ('本机已暂停' if s['local_pause'] else '云端已暂停' if s['paused'] and s['bound'] else '自动工站已连接' if s.get('online') else s['message']))
        description.setText('电脑：'+socket.gethostname()+'\n工站 ID：'+s['station_id']+'\n模板：'+str(s.get('assignment') or '等待网页分配')+'\n工单：'+str(s.get('work_order') or '—'))
        entry=s.get('error') or s['message']
        if entry!=last_text[0]:log.appendPlainText(datetime.datetime.now().strftime('%H:%M:%S')+' '+entry);last_text[0]=entry
    bridge.changed.connect(changed)
    tray=QSystemTrayIcon(window.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),window);tray.setToolTip('Label Desk 联网工站');menu=QMenu();menu.addAction('显示工站',window.show);menu.addAction('退出',app.quit);tray.setContextMenu(menu);tray.activated.connect(lambda _:window.show());tray.show()
    app.setQuitOnLastWindowClosed(False)
    if sys.platform=='win32' and getattr(sys,'frozen',False):
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:winreg.SetValueEx(key,'InfinLabLabelDesk',0,winreg.REG_SZ,'"'+sys.executable+'" --tray')
    worker=threading.Thread(target=engine.run,daemon=True,name='label-cloud');worker.start()
    if '--agent-headless' not in sys.argv and '--tray' not in sys.argv:window.show()
    if '--agent-duration' in sys.argv:QTimer.singleShot(int(sys.argv[sys.argv.index('--agent-duration')+1])*1000,app.quit)
    result=app.exec();engine.stop.set();worker.join(timeout=15);lock.unlock();return result
