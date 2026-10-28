"""Offline MarkFlow template tools. No network, serial, printer or user-profile access."""
import argparse
import base64
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent / 'runtime'))
from label_contract import validate_bundle, bundle_digest, execute_data, bind, MAX_BUNDLE_BYTES


def load(path):
    p = Path(path)
    if p.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError('单模板超过 2 MiB')
    return validate_bundle(json.loads(p.read_text(encoding='utf-8-sig')))


def write(path, content):
    # Never overwrite the input or an existing output implicitly.
    with Path(path).open('xb') as f:
        f.write(content)


def save(path, bundle):
    validate_bundle(bundle)
    write(path, json.dumps(bundle, ensure_ascii=False, indent=2).encode())


def summarize(bundle, mac):
    fields = execute_data(bundle, {'mac': mac, 'model': 'ESP32', 'date': '2000-01-01'})
    return {'valid': True, 'digest': bundle_digest(bundle),
            'size_mm': [bundle['template']['width'], bundle['template']['height']],
            'resolved': [{'id': e['id'], 'type': e['type'], 'content': bind(e.get('content', ''), fields)}
                         for e in bundle['template']['elements'] if e['type'] in ('text', 'qr')]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    new = sub.add_parser('create', help='创建 30 × 20 mm ESP32 模板')
    new.add_argument('--output', required=True)
    new.add_argument('--name', default='ESP32 · 设备标签')
    new.add_argument('--prefix', default='Device_')
    new.add_argument('--logo', help='可选的本地 PNG；不下载外部图片')
    edit = sub.add_parser('mac-text', help='仅修改一个文本元素的 MAC 显示格式')
    edit.add_argument('input'); edit.add_argument('--output', required=True)
    edit.add_argument('--element', default='mac')
    edit.add_argument('--style', choices=['compact', 'colon'], required=True)
    check = sub.add_parser('validate'); check.add_argument('input')
    check.add_argument('--mac', default='02:00:00:00:C3:AC')
    render = sub.add_parser('preview'); render.add_argument('input')
    render.add_argument('--output', required=True)
    render.add_argument('--mac', default='02:00:00:00:C3:AC')
    render.add_argument('--dpi', choices=[203, 300, 600], type=int, default=300)
    render.add_argument('--font', help='可选本地 TTF/OTF 字体，用于后台渲染环境')
    pack = sub.add_parser('pack'); pack.add_argument('inputs', nargs='+')
    pack.add_argument('--output', required=True)
    args = parser.parse_args()

    if args.command == 'create':
        bundle = load(ROOT / 'assets' / 'esp32-30x20.labeljob')
        bundle['name'] = args.name
        bundle['template']['elements'][0]['content'] = args.prefix + '{{mac_last4}}'
        if args.logo:
            p = Path(args.logo)
            if p.stat().st_size > 1_000_000: raise ValueError('Logo PNG 须小于 1 MB')
            bundle['template']['elements'][1]['x'] = 1.7
            bundle['template']['elements'].insert(2, {
                'id': 'logo', 'type': 'image', 'x': 16.545, 'y': 5.655,
                'w': 8.89, 'h': 8.89, 'crop_white': True,
                'image_png': base64.b64encode(p.read_bytes()).decode('ascii')})
        result = summarize(bundle, '02:00:00:00:C3:AC')
        save(args.output, bundle)
    elif args.command == 'mac-text':
        bundle = load(args.input)
        elements = [e for e in bundle['template']['elements'] if e['id'] == args.element and e['type'] == 'text']
        if len(elements) != 1: raise ValueError('找不到指定的文本元素')
        if elements[0].get('content') not in ('{{mac}}', '{{device_id}}'):
            raise ValueError('该元素含其他文字；请显式编辑并校验，避免误删内容')
        if args.style == 'compact':
            steps = bundle['workflow']['steps']
            if (steps[0]['type'] != 'read_esp32_mac' or len(steps) != 4 or
                steps[1].get('type') != 'format_id' or steps[1].get('prefix', '') or
                steps[1].get('separator', 'compact') != 'compact' or
                steps[1].get('case', 'upper') != 'upper'):
                raise ValueError('device_id 不是纯大写无冒号 MAC；不能直接替换文字引用')
        elements[0]['content'] = '{{device_id}}' if args.style == 'compact' else '{{mac}}'
        result = summarize(bundle, '02:00:00:00:C3:AC')
        save(args.output, bundle)
    elif args.command == 'pack':
        if not 1 <= len(args.inputs) <= 30: raise ValueError('列表须包含 1–30 个模板')
        bundles = [load(p) for p in args.inputs]
        digests = [bundle_digest(b) for b in bundles]
        if len(set(digests)) != len(digests): raise ValueError('列表含重复模板')
        payload = {'format': 'markflow-list', 'version': 1, 'templates': bundles}
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode()
        if len(raw) > MAX_BUNDLE_BYTES * 30: raise ValueError('模板列表过大')
        write(args.output, raw)
        result = {'valid': True, 'count': len(bundles), 'digests': digests}
    else:
        bundle = load(args.input); result = summarize(bundle, args.mac)
        if args.command == 'preview':
            os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
            try:
                from PySide6.QtWidgets import QApplication
                from PySide6.QtCore import QByteArray, QBuffer, QIODevice
                from PySide6.QtGui import QFontDatabase
                from label_render import render_label
            except ImportError as error:
                raise ValueError('预览需要 scripts/requirements-preview.txt 中的依赖') from error
            app = QApplication.instance() or QApplication([])
            font = Path(args.font) if args.font else None
            if font is None and sys.platform == 'win32':
                font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'arial.ttf'
            if font is not None:
                if not font.is_file() or QFontDatabase.addApplicationFont(str(font)) < 0:
                    raise ValueError('无法加载预览字体；请用 --font 指定可用的本地 TTF/OTF')
            if not QFontDatabase.families():
                raise ValueError('后台环境没有可用字体；请用 --font 指定本地 TTF/OTF')
            values = execute_data(bundle, {'mac': args.mac, 'model': 'ESP32', 'date': '2000-01-01'})
            image, warnings = render_label(bundle['template'], values, args.dpi)
            data = QByteArray(); buffer = QBuffer(data); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not image.save(buffer, 'PNG'): raise ValueError('无法编码 PNG')
            buffer.close(); write(args.output, bytes(data))
            result.update(dpi=args.dpi, pixels=[image.width(), image.height()], warnings=warnings)
            print(json.dumps(result, ensure_ascii=False))
            # Keep the preview for diagnosis, but never report clipping/dense QR as a pass.
            return 2 if warnings else 0
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'valid': False, 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
