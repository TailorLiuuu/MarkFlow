"""Validate all remotely supplied printer settings before invoking native code."""
from workstation import CalibrationStore

def checked_config(value):
    if not isinstance(value,dict):raise ValueError('工站打印配置无效')
    cfg=dict(value)
    if not isinstance(cfg.get('name'),str) or not 1<=len(cfg['name'])<=200:raise ValueError('打印机名称无效')
    if cfg.get('dpi') not in (203,300,600) or cfg.get('paper_mode') not in ('gap','continuous','driver'):raise ValueError('DPI 或纸张类型无效')
    CalibrationStore.validate(cfg.get('offset_x_mm'),cfg.get('offset_y_mm'))
    if cfg.get('vid') not in (0x303a,0x10c4,0x1a86,0x0403) or not isinstance(cfg.get('pid'),int) or not 0<=cfg['pid']<=65535:raise ValueError('USB 匹配规则无效')
    return cfg
