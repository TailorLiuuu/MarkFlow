"""System printer preflight and submission shared by UI and smoke checks."""
import os
import subprocess
import sys
from pathlib import Path


def printer_details(name):
    from PySide6.QtPrintSupport import QPrinterInfo
    info = QPrinterInfo.printerInfo(name)
    if info.isNull(): raise ValueError('系统打印机已不存在，请重新扫描')
    return {'name': name, 'description': info.description(), 'dpi': info.supportedResolutions(),
            'custom_paper': info.supportsCustomPageSizes()}


def preferences_command(name):
    if sys.platform != 'win32': raise ValueError('请在系统设置的“打印机与扫描仪”中调整驱动选项')
    if not name or any(ch in name for ch in '\0\r\n"'): raise ValueError('打印机名称无效')
    return [str(Path(os.environ.get('WINDIR', 'C:/Windows'))/'System32'/'rundll32.exe'),
            'printui.dll,PrintUIEntry', '/e', '/n', name]


def open_preferences(name):
    printer_details(name)
    result = subprocess.run(preferences_command(name), check=False,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode: raise RuntimeError(f'驱动设置窗口退出异常：{result.returncode}')


def qt_printer(name, template, dpi):
    from PySide6.QtCore import QSizeF, QMarginsF
    from PySide6.QtGui import QPageSize, QPageLayout
    from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
    info = QPrinterInfo.printerInfo(name)
    if info.isNull(): raise ValueError('请选择有效的系统打印机')
    supported = info.supportedResolutions()
    if supported and dpi not in supported: raise ValueError('驱动不支持所选 DPI，请重新选择')
    printer = QPrinter(info, QPrinter.PrinterMode.HighResolution)
    printer.setResolution(dpi); printer.setFullPage(True); printer.setCopyCount(1)
    printer.setDuplex(QPrinter.DuplexMode.DuplexNone); printer.setColorMode(QPrinter.ColorMode.GrayScale)
    page = QPageSize(QSizeF(template['width'], template['height']), QPageSize.Unit.Millimeter,
                     'LabelDesk', QPageSize.SizeMatchPolicy.ExactMatch)
    if not printer.setPageLayout(QPageLayout(page, QPageLayout.Orientation.Portrait,
            QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)):
        raise ValueError('驱动拒绝标签纸尺寸，请先在系统驱动中设置')
    actual = printer.pageLayout().fullRect(QPageLayout.Unit.Millimeter)
    if abs(actual.width()-template['width']) > .15 or abs(actual.height()-template['height']) > .15:
        raise ValueError('驱动返回的纸张尺寸与模板不同，已停止提交')
    if printer.resolution() != dpi: raise ValueError('驱动实际 DPI 已改变，请重新准备标签')
    return printer


def preflight(name, template, dpi, image=None, paper_mode='driver'):
    if sys.platform == 'win32':
        from windows_print import WindowsPrinter
        with WindowsPrinter(name, template['width'], template['height'], dpi,paper_mode) as printer:
            if image is not None: printer.check_image(image)
            return printer.proof()
    printer = qt_printer(name, template, dpi)
    return {'printer': name, 'dpi_x': printer.resolution(), 'dpi_y': printer.resolution(),
            'width_px': round(template['width']*dpi/25.4), 'height_px': round(template['height']*dpi/25.4),
            'copies': 1, 'scale_percent': 100}


def submit(name, template, dpi, image, proof, paper_mode='driver'):
    if sys.platform == 'win32':
        from windows_print import WindowsPrinter
        with WindowsPrinter(name, template['width'], template['height'], dpi,paper_mode) as printer:
            if printer.proof() != proof: raise ValueError('驱动设置已变化，请重新准备标签')
            return printer.print_image(image)
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainter
    printer = qt_printer(name, template, dpi)
    painter = QPainter()
    if not painter.begin(printer): raise RuntimeError('无法启动系统打印任务')
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawImage(QRectF(0, 0, image.width(), image.height()), image)
    finally:
        ended = painter.end()
    if not ended: raise RuntimeError('系统打印任务未正常结束，请核对队列和纸面')
    return {'status': 'submitted', 'message': '已交给系统打印队列，等待纸面确认'}
