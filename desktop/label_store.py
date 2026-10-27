from contextlib import contextmanager
import json
import sqlite3
import time
import uuid
class LocalStore:
    def __init__(self,path):
        self.path=str(path)
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,created_at TEXT,device_id TEXT,mac TEXT,digest TEXT,bundle TEXT,fields TEXT,printer TEXT,status TEXT,message TEXT,reprint_reason TEXT)')
            c.execute("UPDATE jobs SET status='unknown',message='客户端上次执行中断；请核对纸面，禁止自动重试' WHERE status='sending'")
    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.path);c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()
    def duplicate(self,device_id,digest):
        with self.connect() as c:return c.execute("SELECT id,status FROM jobs WHERE device_id=? AND digest=? AND status IN ('sending','submitted','confirmed','unknown') ORDER BY rowid DESC LIMIT 1",(device_id,digest)).fetchone()
    def duplicate_mac(self,mac):
        with self.connect() as c:return c.execute("SELECT id,status FROM jobs WHERE UPPER(mac)=? AND status IN ('sending','submitted','confirmed','unknown') ORDER BY rowid DESC LIMIT 1",(mac.upper(),)).fetchone()
    def start(self,fields,bundle,digest,printer,reason=''):
        identifier=str(uuid.uuid4())
        with self.connect() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',(identifier,time.strftime('%Y-%m-%d %H:%M:%S'),fields['device_id'],fields.get('mac',''),digest,json.dumps(bundle,ensure_ascii=False),json.dumps(fields,ensure_ascii=False),json.dumps(printer,ensure_ascii=False),'sending','尚未获得设备提交结果',reason))
        return identifier
    def finish(self,identifier,status,message):
        if status not in ('submitted','confirmed','failed','unknown','simulated'):raise ValueError('invalid status')
        with self.connect() as c:c.execute('UPDATE jobs SET status=?,message=? WHERE id=?',(status,message,identifier))
    def rows(self):
        with self.connect() as c:return [dict(r) for r in c.execute('SELECT * FROM jobs ORDER BY rowid DESC LIMIT 2000')]
