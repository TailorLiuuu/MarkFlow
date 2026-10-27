"""Versioned, data-only label workflow. Shared by Toolbox and native clients."""
import copy
import json
import math
import re
import base64
import struct

MAX_BUNDLE_BYTES=2*1024*1024
FIELDS={'device_id','mac','mac_last4','mac_first3','mac_last3','model','batch','date'}
STEP_TYPES={'read_esp32_mac','manual_input','format_id','preview','print_label'}
class ContractError(ValueError): pass

def decode_logo_png(value):
    if not isinstance(value,str) or len(value)>1500000:raise ContractError('Logo PNG 数据无效或过大')
    try:data=base64.b64decode(value,validate=True)
    except Exception as e:raise ContractError('Logo PNG 编码无效') from e
    if len(data)<33 or data[:8]!=b'\x89PNG\r\n\x1a\n' or data[12:16]!=b'IHDR':raise ContractError('Logo 仅支持内嵌 PNG 图像')
    w,h=struct.unpack('>II',data[16:24])
    if not 1<=w<=2048 or not 1<=h<=2048:raise ContractError('Logo 图像不能超过 2048 × 2048 像素')
    return data

def number(v,name,minimum,maximum):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not minimum<=v<=maximum:
        raise ContractError(f'{name} 必须在 {minimum}–{maximum} 范围内')
    return v

def text(v,name,limit=120,empty=True):
    if not isinstance(v,str) or len(v)>limit or (not empty and not v.strip()) or any(ord(c)<32 and c not in '\n\t' for c in v):
        raise ContractError(f'{name} 无效或过长')
    return v

def validate_template(t):
    if not isinstance(t,dict) or t.get('format')!='label-desk' or t.get('version')!=1: raise ContractError('不支持的标签模板版本')
    text(t.get('name'),'模板名称',48,False)
    w=number(t.get('width'),'宽度',10,150);h=number(t.get('height'),'高度',10,150)
    es=t.get('elements')
    if not isinstance(es,list) or not 1<=len(es)<=60: raise ContractError('标签须包含 1–60 个元素')
    ids=set()
    for e in es:
        if not isinstance(e,dict) or e.get('type') not in {'text','qr','line','rect','image'}: raise ContractError('不支持的标签元素')
        key=text(e.get('id'),'元素 ID',80,False)
        if key in ids:raise ContractError('元素 ID 重复')
        ids.add(key)
        if 'name' in e:text(e['name'],'元素名称',80)
        x=number(e.get('x'),'X',0,w);y=number(e.get('y'),'Y',0,h)
        ew=number(e.get('w'),'元素宽',.01,w);eh=number(e.get('h'),'元素高',.01,h)
        if x+ew>w+.001 or y+eh>h+.001:raise ContractError('元素超出标签边界')
        if e['type']=='qr' and abs(ew-eh)>.001:raise ContractError('二维码须为正方形')
        if e['type']=='text':number(e.get('font'),'字号',.6,15)
        if e['type']=='image':
            decode_logo_png(e.get('image_png'))
            if 'crop_white' in e and not isinstance(e['crop_white'],bool):raise ContractError('crop_white 须为布尔值')
        if 'align' in e and e['align'] not in ('left','center','right'):raise ContractError('文字对齐无效')
        if 'bold' in e and not isinstance(e['bold'],bool):raise ContractError('bold 须为布尔值')
        s=text(e.get('content',''),'元素内容',1000)
        if set(re.findall(r'\{\{\s*(\w+)\s*\}\}',s))-FIELDS:raise ContractError('模板包含不支持的字段')
        if re.search(r'\{\{|\}\}',re.sub(r'\{\{\s*(\w+)\s*\}\}','',s)):raise ContractError('模板变量语法无效')
    return t

def validate_bundle(b):
    if not isinstance(b,dict) or b.get('format')!='label-production' or b.get('version')!=1:raise ContractError('不支持的生产流程格式')
    if len(json.dumps(b,ensure_ascii=False,allow_nan=False).encode())>MAX_BUNDLE_BYTES:raise ContractError('流程包超过 2 MiB')
    text(b.get('name'),'流程名称',80,False)
    validate_template(b.get('template'))
    wf=b.get('workflow')
    if not isinstance(wf,dict):raise ContractError('缺少流程定义')
    variables=wf.get('variables',{})
    if not isinstance(variables,dict) or set(variables)-FIELDS:raise ContractError('流程默认字段无效')
    for k,v in variables.items():text(v,k,120)
    steps=wf.get('steps')
    if not isinstance(steps,list) or not 3<=len(steps)<=8:raise ContractError('流程须为 3–8 步')
    kinds=[];ids=set()
    for s in steps:
        if not isinstance(s,dict) or s.get('type') not in STEP_TYPES:raise ContractError('包含不支持的流程节点')
        sid=text(s.get('id'),'节点 ID',80,False)
        if sid in ids:raise ContractError('节点 ID 重复')
        ids.add(sid);kinds.append(s['type'])
        allowed={'id','type'}|({'case','separator','prefix'} if s['type']=='format_id' else set())
        if set(s)-allowed:raise ContractError('流程节点包含不支持的参数')
        if s['type']=='format_id':
            if s.get('case','upper') not in {'upper','lower','keep'} or s.get('separator','compact') not in {'compact','colon','keep'}:raise ContractError('设备号格式选项无效')
            text(s.get('prefix',''),'设备号前缀',24)
    if kinds[0] not in {'read_esp32_mac','manual_input'} or sum(k in {'read_esp32_mac','manual_input'} for k in kinds)!=1:raise ContractError('第一步须且只能为一个数据源')
    if kinds[-2:]!=['preview','print_label'] or kinds.count('preview')!=1 or kinds.count('print_label')!=1:raise ContractError('最后两步必须是预览确认、打印标签')
    if any(k!='format_id' for k in kinds[1:-2]):raise ContractError('数据处理步骤顺序无效')
    return copy.deepcopy(b)

def execute_data(b,source):
    b=validate_bundle(b);v=dict(b['workflow'].get('variables',{}))
    v.update({k:str(val) for k,val in source.items() if k in FIELDS})
    for key in ('mac_last4','mac_first3','mac_last3'):v.pop(key,None)
    if re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',v.get('mac','')):
        v['mac_last4']=v['mac'].replace(':','')[-4:].upper()
        v['mac_first3']=':'.join(v['mac'].upper().split(':')[:3])
        v['mac_last3']=':'.join(v['mac'].upper().split(':')[3:])
    if b['workflow']['steps'][0]['type']=='read_esp32_mac':
        mac=v.get('mac','')
        if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',mac):raise ContractError('ESP32 未返回有效 MAC')
        v['device_id']=mac
    for step in b['workflow']['steps'][1:-2]:
        value=v.get('device_id','')
        sep=step.get('separator','compact');compact=re.sub(r'[:-]','',value)
        if sep=='compact':value=compact
        elif sep=='colon':
            if not re.fullmatch('[0-9a-fA-F]{12}',compact):raise ContractError('冒号格式要求 12 位十六进制 MAC')
            value=':'.join(compact[i:i+2] for i in range(0,12,2))
        case=step.get('case','upper')
        if case!='keep':value=value.upper() if case=='upper' else value.lower()
        v['device_id']=step.get('prefix','')+value
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}',v.get('device_id','')):raise ContractError('设备号须为 1–64 位字母、数字、点、下划线、冒号或短横线')
    for e in b['template']['elements']:
        for k in re.findall(r'\{\{\s*(\w+)\s*\}\}',e.get('content','')):
            if k not in v or not v[k]:raise ContractError(f'缺少字段值：{k}')
    return v

def bind(s,v):return re.sub(r'\{\{\s*(\w+)\s*\}\}',lambda m:v.get(m[1],m[0]),s)

def bundle_digest(b):
    import hashlib
    return hashlib.sha256(json.dumps(b,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
