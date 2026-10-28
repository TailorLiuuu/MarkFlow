"""MarkFlow: local template production with optional managed template delivery."""
import datetime,json,platform,queue,socket,time,uuid,threading
from pathlib import Path
from concurrent.futures import Future
from agent_runtime import Engine,CloudError,VERSION
from template_library import TemplateLibrary
from workstation import atomic_json,ArrivalGate,CalibrationStore,printer_key
from label_contract import bundle_digest,execute_data
from label_agent_config import checked_config

def background_call(action):
    future=Future()
    def work():
        try:future.set_result(action())
        except Exception as error:future.set_exception(error)
    threading.Thread(target=work,daemon=True).start()
    return future

class StudioEngine(Engine):
    def __init__(self,directory,emit):
        super().__init__(directory,emit)
        self.library=TemplateLibrary(self.dir/'templates');self.actions=queue.Queue();self.running=False;self.busy=False
        self.start_request=None;self.start_pending=False;self._start_lock=threading.Lock()
        self.update_installing=False;self.update_error="";self.ready=False;self._pause_before_update=False
        self.local_pause=False;self.online=False;self.cloud_name='';self.pair_code='';self.catalog_revision='';self.legacy_applied=None
        self.preview=None;self.last_mac='';self.events=[];self.selected=None;self.config=self.load_config();self.cloud_retry=0
        self._cloud_cycle=None;self._cloud_future=None;self._printers_future=None;self._printers_retry=0;self._printers=[]
        previous=self.local.recent(1)
        if previous:self.last_mac=previous[0]['mac']
        self.outbox=self.dir/'local-outbox';self.outbox.mkdir(exist_ok=True)
        for candidate in [self.dir/'active.labeljob',Path(__file__).parent/'examples/ringhi-mac.labeljob']:
            if candidate.exists():
                try:self.library.put(json.loads(candidate.read_text(encoding='utf-8')),'内置模板')
                except ValueError:pass
        try:self.selected=json.loads((self.dir/'studio-state.json').read_text(encoding='utf-8')).get('selected')
        except (OSError,ValueError):pass
        if not self.library.get(self.selected):self.selected=next((i['id'] for i in self.library.all()),None)
        self.message='选择模板，点击开始';self.paused=False
    def load_config(self):
        defaults={'name':'','dpi':300,'paper_mode':'gap','offset_x_mm':0.,'offset_y_mm':0.,'vid':0x303a,'pid':0x1001}
        try:defaults.update({k:v for k,v in json.loads((self.dir/'printer-settings.json').read_text(encoding='utf-8')).items() if k in defaults})
        except (OSError,ValueError):pass
        return defaults
    def enqueue(self,kind,**data):
        if kind=='start':
            with self._start_lock:
                if self.start_pending or self.running or self.busy or self.paused or self.update_installing:return None
                self.start_request=str(uuid.uuid4());self.start_pending=True
                data['request_id']=self.start_request
                self.actions.put((kind,data))
                return self.start_request
        self.actions.put((kind,data))
    def halt(self):
        with self._start_lock:
            self.start_pending=False;self.running=False;self.local_pause=True
        self.message='已停止；当前已提交任务无法撤回';self.enqueue('stop')
    def prepare_update(self):
        with self._start_lock:
            self.update_error=''
            if self.running or self.busy or self.start_pending:
                self.update_error='生产正在运行，请先停止再更新。';return False
            self._pause_before_update=self.local_pause
            self.update_installing=True;self.local_pause=True
        self.message='正在更新软件，已停止接收打印任务';return True
    def process_start(self,data):
        request=data['request_id']
        with self._start_lock:
            if request!=self.start_request or not self.start_pending:return
        try:self.start_production(data.get('config'),request)
        finally:
            with self._start_lock:
                if request==self.start_request:self.start_pending=False
    def log(self,text):
        self.events.append({'time':datetime.datetime.now().strftime('%H:%M:%S'),'text':text});self.events=self.events[-80:]
    def snapshot(self):
        current=self.library.get(self.selected);rows=self.local.recent();counts=self.local.print_counts()
        return {'station_id':self.cloud.id,'version':VERSION,'bound':self.bound,'online':self.online,'pair_code':self.pair_code,'name':self.cloud_name or socket.gethostname(),'running':self.running,'busy':self.busy,'start_pending':self.start_pending,'start_request':self.start_request,'update_installing':self.update_installing,'update_error':self.update_error,'local_pause':self.local_pause,'paused':self.paused,'message':self.message,'error':self.last_error,'selected':self.selected,'templates':[{'id':i['id'],'name':i['bundle']['name'],'width':i['bundle']['template']['width'],'height':i['bundle']['template']['height'],'origin':i['origin']} for i in self.library.all()],'config':self.config,'ports':self.inventory.get('ports',[]),'printers':self.inventory.get('printers',[]),'preview':str(self.preview) if self.preview else '', 'last_mac':self.last_mac,'print_count':counts.get(self.last_mac.upper(),0),'last_job':self.last_job,'events':self.events[-8:],'recent':[{'created':r['created_at'],'mac':r['mac'],'status':r['status'],'message':r['message'],'print_count':counts.get(r['mac'].upper(),0)} for r in rows],'catalog_revision':self.catalog_revision,'assignment':current['bundle']['name'] if current else None}
    def update(self,**values):
        if not hasattr(self,'library'):return super().update(**values)
        value=self.snapshot();value.update(values);atomic_json(self.dir/'agent-status.json',value);self.emit(value)
    def devices(self):
        import serial.tools.list_ports
        from PySide6.QtPrintSupport import QPrinterInfo
        # Driver enumeration can be slow; it must never delay USB arrival detection.
        if self._printers_future is not None and self._printers_future.done():
            try:self._printers=self._printers_future.result()[:30]
            except Exception:self._printers=[]
            self._printers_future=None;self._printers_retry=time.monotonic()+10
        if self._printers_future is None and time.monotonic()>=self._printers_retry:
            self._printers_future=background_call(QPrinterInfo.availablePrinterNames)
        ports=list(serial.tools.list_ports.comports())
        self.inventory={'printers':self._printers,'ports':[{'port':p.device,'vid':p.vid,'pid':p.pid,'description':p.description[:100]} for p in ports[:50]],'current_printer':dict(self.config),'capabilities':['template_library','test_print','local_import','embedded_png','esp32_mac','system_print','calibration'],'running':self.running,'selected':self.selected,'catalog_revision':self.catalog_revision,'last_mac':self.last_mac,'busy':self.busy,'message':self.message[-500:],'error':self.last_error[-1000:],'local_pause':self.local_pause,'contract':1}
        return ports
    def render_preview(self):
        item=self.library.get(self.selected)
        if not item:return
        from label_render import render_label
        fields=execute_data(item['bundle'],{'mac':self.last_mac or '02:00:00:00:AB:CD','model':'ESP32-S3','date':datetime.date.today().isoformat()})
        image,warnings=render_label(item['bundle']['template'],fields,self.config['dpi']);path=self.dir/'studio-preview.png';image.save(str(path));self.preview=path
        if warnings:raise ValueError('；'.join(warnings))
    def choose(self,key):
        if not self.library.get(key):raise ValueError('模板不存在')
        self.running=False;self.selected=key;self.last_error='';self.message='模板已选择，点击开始';atomic_json(self.dir/'studio-state.json',{'selected':key});self.render_preview()
    def start_production(self,config=None,request=None):
        if self.running:return
        if self.paused:raise ValueError('管理端已暂停，请先解除暂停')
        self.config=checked_config(config or self.config)
        item=self.library.get(self.selected)
        if not item:raise ValueError('请先导入或选择一个模板')
        if item['bundle']['workflow']['steps'][0]['type']!='read_esp32_mac':raise ValueError('当前开始模式支持 ESP32 MAC 模板')
        self.prepare_image({'bundle':item['bundle'],'digest':item['id'],'config':self.config},{'mac':'02:00:00:00:AB:CD','model':'ESP32','date':datetime.date.today().isoformat()})
        atomic_json(self.dir/'printer-settings.json',{'interface':'system',**self.config})
        CalibrationStore(self.dir/'calibration.json').save(printer_key({'interface':'system','name':self.config['name']}),self.config['offset_x_mm'],self.config['offset_y_mm'])
        with self._start_lock:
            if request is not None and (request!=self.start_request or not self.start_pending):return
            self.local_pause=False;self.running=True;self.gate=ArrivalGate(self.config['vid'],self.config['pid'],settle=.4,absent=1.0)
        self.last_error='';self.message='运行中 · 等待 ESP32 接入';self.log('开始生产：'+item['bundle']['name'])
    def local_print(self,port,detected_at=None):
        from app import read_mac
        from system_print import submit
        self.busy=True;self.message='读取 '+port+' 的设备 MAC';self.update();local_id=None;started=time.monotonic();timing={}
        try:
            source=read_mac(port);timing['read_mac_seconds']=round(time.monotonic()-started,3);prepared=time.monotonic();self.last_mac=source['mac'];source.update(model=source['chip'],date=datetime.date.today().isoformat());item=self.library.get(self.selected)
            a={'bundle':item['bundle'],'digest':item['id'],'config':dict(self.config)}
            bundle,fields,image,proof,cfg=self.prepare_image(a,source)
            timing['prepare_seconds']=round(time.monotonic()-prepared,3)
            if not self.running or self.local_pause or (self.bound and self.paused):raise ValueError('已停止，未提交打印')
            local_id=self.local.start(fields,bundle,item['id'],{'interface':'system',**cfg},'本地生产')
            self.message='正在打印 1 张';self.update();submitted=time.monotonic();image.save(str(self.dir/'agent-last-label.png'))
            result=submit(cfg['name'],bundle['template'],cfg['dpi'],image,proof,cfg['paper_mode']);self.local.finish(local_id,result['status'],result['message'])
            timing['submit_seconds']=round(time.monotonic()-submitted,3);timing['total_seconds']=round(time.monotonic()-(detected_at if detected_at is not None else started),3)
            self.message=result['message'];self.last_error='';self.last_job={'job_id':local_id,'mac':source['mac'],**result,'timing':timing}
            self.log('本次耗时：检测到提交 '+str(timing['total_seconds'])+' 秒；读 MAC '+str(timing['read_mac_seconds'])+' 秒，排版与校验 '+str(timing['prepare_seconds'])+' 秒，提交 '+str(timing['submit_seconds'])+' 秒')
            self.queue_local_result(local_id,item,source,result)
        except Exception as e:
            self.last_error=str(e);self.message='执行失败：'+str(e);self.log(self.message)
            if local_id:
                self.local.finish(local_id,'unknown',str(e));self.running=False;self.local_pause=True
                self.queue_local_result(local_id,item,source,{'status':'unknown','message':str(e)})
        finally:
            self.busy=False;self.render_preview();self.update()
    def queue_local_result(self,identifier,item,source,result):
        atomic_json(self.outbox/(identifier+'.json'),{'id':identifier,'mac':source['mac'],'name':item['bundle']['name'],'digest':item['id'],'status':result['status'],'message':result['message'],'queue_job_id':result.get('job_id'),'time':datetime.datetime.now().isoformat(),'reported':False})
    def sync_catalog(self,catalog,downloaded=None):
        if not catalog or catalog['revision']==self.catalog_revision:return
        for item in catalog['items']:
            if self.library.get(item['digest']):continue
            data=downloaded[item['digest']] if downloaded is not None else self.cloud.call('catalog-template',{'template':item['id'],'revision':item['revision']})
            if bundle_digest(data['bundle'])!=item['digest']:raise ValueError('收到的模板摘要不匹配')
            self.library.put(data['bundle'],'管理端下发',{'id':item['id'],'revision':item['revision']})
        self.catalog_revision=catalog['revision'];self.log('收到模板列表：'+str(len(catalog['items']))+' 个');self.update()
    def remote_test(self,command):
        path=self.commands/(command['id']+'.json')
        if path.exists():return
        c={'id':command['id'],'done':False,'reported':False};atomic_json(path,c)
        old=self.assignment;was_running=self.running;self.busy=True
        try:
            if self.local_pause or self.paused:raise ValueError('本机已停止，未打印；点击开始或解除停止后再测试')
            if command['expires']<=time.time():raise ValueError('测试任务已过期，未打印')
            a=command.get('target')
            if not a:raise ValueError('请使用管理端新版“测试打印”，旧版生产请求不再执行')
            self.assignment=a
            ports=self.devices();matching=[p for p in ports if p.vid==a['config']['vid'] and p.pid==a['config']['pid']]
            if len(matching)!=1:raise ValueError('请连接且只连接一台匹配的 ESP32')
            c['message']=self.execute(matching[0].device,command['id']);self.last_mac=(self.last_job or {}).get('mac',self.last_mac);self.log('远程测试：'+c['message'])
        except Exception as e:c['message']=str(e)[:1000];self.last_error=c['message'];self.message='远程测试失败：'+c['message'];self.log(self.message)
        finally:
            self.assignment=old;self.busy=False;c['done']=True;atomic_json(path,c);self.update()
    def cloud_cycle(self):
        if not self.bound:
            r=yield ('enroll',{'host':socket.gethostname(),'platform':platform.system()+' '+platform.release(),'version':VERSION});self.bound=r['bound'];self.pair_code=r.get('pair_code','')
            if not self.bound:return
        yield from self.flush_requests()
        for p in self.outbox.glob('*.json'):
            r=json.loads(p.read_text(encoding='utf-8'))
            if not r.get('reported'):(yield ('local-result',{'record':r}));r['reported']=True;atomic_json(p,r)
        r=yield ('poll',{'version':VERSION,'inventory':json.loads(json.dumps(self.inventory)),'applied':self.legacy_applied})
        self.online=True;self.cloud_name=r['name'];self.paused=r['paused']
        if r.get('assignment'):
            a=r['assignment'];self.library.put(a['bundle'],'管理端下发',{'id':a['template_id'],'revision':a['revision']})
            (yield ('applied',{'assignment':a['id']}));self.legacy_applied=a['id']
        catalog=r.get('catalog');downloaded={}
        if catalog and catalog['revision']!=self.catalog_revision:
            for item in catalog['items']:
                if not self.library.get(item['digest']):downloaded[item['digest']]=yield ('catalog-template',{'template':item['id'],'revision':item['revision']})
        self.sync_catalog(catalog,downloaded)
        if r.get('commands'):
            self.remote_test(r['commands'][0])
            yield from self.flush_requests()
    def poll_cloud(self):
        # Only the HTTP call runs in the background. All state changes and print actions
        # resume on the engine thread, so remote and local jobs cannot race each other.
        if self.update_installing or self.stop.is_set():return
        try:
            if self._cloud_cycle is None:
                if time.monotonic()<self.cloud_retry:return
                self._cloud_cycle=self.cloud_cycle();request=next(self._cloud_cycle)
            else:
                if self._cloud_future is None or not self._cloud_future.done():return
                reply=self._cloud_future.result();self._cloud_future=None
                request=self._cloud_cycle.send(reply)
            route,data=request
            self._cloud_future=background_call(lambda:self.cloud.call(route,data))
        except StopIteration:
            self._cloud_cycle=None;self._cloud_future=None;self.cloud_retry=time.monotonic()+3
        except Exception as e:
            self._cloud_cycle=None;self._cloud_future=None;self.online=False;self.cloud_retry=time.monotonic()+6
            if isinstance(e,CloudError) and e.status in (401,403):self.bound=False
            self.inventory['cloud_error']=str(e)[:300]
    def run(self):
        self.render_preview();self.ready=True
        while not self.stop.is_set():
            try:
                while not self.actions.empty():
                    kind,data=self.actions.get_nowait()
                    if self.update_installing and kind!='cancel-update':continue
                    if kind=='import':
                        items=self.library.import_file(data['path']);self.choose(items[0]['id']);self.log('导入 '+str(len(items))+' 个模板')
                    elif kind=='select':self.choose(data['id'])
                    elif kind=='start':self.process_start(data)
                    elif kind=='prepare-update':self.prepare_update()
                    elif kind=='cancel-update':self.update_installing=False;self.local_pause=self._pause_before_update;self.message='更新已取消，原有程序未变动'
                    elif kind=='stop':self.running=False;self.local_pause=True;self.log('已停止接收新任务')
                    elif kind=='allow-remote':self.local_pause=False;self.message='已允许远程测试；本地生产未启动'
                    elif kind=='config':
                        self.config=checked_config(data['config']);self.running=False;self.gate=None;atomic_json(self.dir/'printer-settings.json',{'interface':'system',**self.config});self.message='打印设置已保存，点击开始';self.render_preview()
                if self.update_installing:
                    self.update();self.stop.wait(.15);continue
                ports=self.devices()
                self.poll_cloud()
                if self.running and not self.local_pause:
                    # Local imports run offline. A deliberate cloud pause still stops this bound station.
                    if self.bound and self.paused:self.message='管理端已暂停生产'
                    else:
                        port=self.gate.poll(ports,time.monotonic(),ready=True)
                        if port:self.local_print(port.device,self.gate.entries[self.gate.identity(port)]['first'])
                self.update()
            except Exception as e:self.record_failure('execute',e);self.running=False;self.update()
            self.stop.wait(.15)
