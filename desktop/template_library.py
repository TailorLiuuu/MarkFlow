"""Validated data-only template library shared by imports and cloud delivery."""
import json
from pathlib import Path
from label_contract import validate_bundle,bundle_digest,MAX_BUNDLE_BYTES
from workstation import atomic_json

class TemplateLibrary:
    def __init__(self,directory):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
    def all(self):
        result=[]
        for p in self.directory.glob('*.json'):
            try:
                item=json.loads(p.read_text(encoding='utf-8'));b=validate_bundle(item['bundle'])
                if bundle_digest(b)!=item['id']:continue
                result.append(item)
            except (ValueError,KeyError):continue
        return sorted(result,key=lambda r:r['bundle']['name'])
    def get(self,key):
        return next((v for v in self.all() if v['id']==key),None)
    def put(self,bundle,origin='本地导入',cloud=None):
        b=validate_bundle(bundle);key=bundle_digest(b);old=self.get(key)
        item={'id':key,'bundle':b,'origin':origin,'cloud':cloud}
        if old and origin=='内置模板':return old
        atomic_json(self.directory/(key+'.json'),item);return item
    def import_file(self,path):
        p=Path(path)
        if p.stat().st_size>MAX_BUNDLE_BYTES*30:raise ValueError('模板文件过大')
        raw=json.loads(p.read_text(encoding='utf-8-sig'))
        bundles=raw.get('templates') if raw.get('format')=='markflow-list' else [raw]
        if not isinstance(bundles,list) or not 1<=len(bundles)<=30:raise ValueError('模板列表必须包含 1–30 个模板')
        valid=[validate_bundle(b) for b in bundles]  # Validate the whole import before writing.
        return [self.put(b) for b in valid]
