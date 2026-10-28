"""Local printer calibration and debounced USB arrival policy."""
import json
import math
from pathlib import Path


def atomic_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)


def printer_key(options):
    interface=options.get('interface')
    identity={'system':options.get('name'),'usb':options.get('serial'),'net':options.get('ip')}.get(interface)
    return json.dumps([interface,identity],ensure_ascii=False) if identity else None


class CalibrationStore:
    def __init__(self,path):self.path=Path(path)
    def all(self):
        if not self.path.exists():return {}
        data=json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data,dict):raise ValueError('微调配置文件无效')
        return data
    @staticmethod
    def validate(x,y):
        for value in (x,y):
            if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or abs(value)>10:
                raise ValueError('微调须为 -10 到 +10 mm')
        return float(x),float(y)
    def get(self,key):
        v=self.all().get(key,{'x':0,'y':0});return self.validate(v['x'],v['y'])
    def save(self,key,x,y):
        if not key:raise ValueError('请先选择打印机，再保存微调')
        x,y=self.validate(x,y);data=self.all();data[key]={'x':x,'y':y};atomic_json(self.path,data)


class ArrivalGate:
    """Consume one stable arrival; retain it across short USB reset disconnects."""
    def __init__(self,vid,pid,settle=1.5,absent=3):
        self.vid=vid;self.pid=pid;self.settle=settle;self.absent=absent;self.entries={}
    @staticmethod
    def identity(port):return (port.vid,port.pid,port.serial_number or port.device)
    def poll(self,ports,now,ready=True):
        matches=[p for p in ports if p.vid==self.vid and p.pid==self.pid]
        present={self.identity(p) for p in matches}
        for key in list(self.entries):
            entry=self.entries[key]
            missing=entry.get('missing_since')
            if key in present:
                if missing is not None and (not entry['used'] or now-missing>=self.absent):
                    del self.entries[key]
                else:
                    entry['last']=now;entry.pop('missing_since',None)
            else:
                entry.setdefault('missing_since',now)
                if now-entry['missing_since']>=self.absent:del self.entries[key]
        for port in matches:
            self.entries.setdefault(self.identity(port),{'first':now,'last':now,'used':False})
        if len(matches)>1 and ready:raise ValueError('检测到多个匹配设备，请只连接一台待打印 ESP32')
        if ready and len(matches)==1:
            port=matches[0];entry=self.entries[self.identity(port)]
            if not entry['used'] and now-entry['first']>=self.settle:
                entry['used']=True
                return port
        return None
