"""Label Desk native work station. No web view or arbitrary workflow execution."""
import sys
import os
import json
import hmac
import re
import subprocess
import tempfile
import traceback
from pathlib import Path

BASE=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))
if not getattr(sys,'frozen',False):sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'shared'))
from label_contract import validate_bundle,execute_data,bundle_digest,ContractError,MAX_BUNDLE_BYTES

def json_output(value):
    try:sys.stdout.reconfigure(encoding='utf-8')
    except Exception:pass
    print(json.dumps(value,ensure_ascii=True))

def worker_entry():
    if len(sys.argv)>2 and sys.argv[1]=='--read-mac-worker':
        port=sys.argv[2]
        if not re.fullmatch(r'(?:COM[0-9]{1,4}|/dev/(?:cu\.|tty)[A-Za-z0-9_.-]+)',port):raise ValueError('串口路径无效')
        import esptool
        esptool.main(['--port',port,'--baud','115200','--before','default_reset','--after','hard_reset','--no-stub','read_mac'])
        return True
    if len(sys.argv)>2 and sys.argv[1]=='--sdk-worker':
        from printer_sdk import worker
        try:json_output({'ok':True,**worker(sys.argv[2])})
        except Exception as e:json_output({'ok':False,'error':str(e)})
        return True
    return False

def child_command(args):
    if getattr(sys,'frozen',False):
        worker=Path(sys.executable).with_name('LabelDeskWorker.exe' if sys.platform=='win32' else 'LabelDeskWorker')
        return [str(worker),*args]
    return [sys.executable,str(Path(__file__).resolve()),*args]

def child_run(args,timeout=45):
    result=subprocess.run(child_command(args),capture_output=True,timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),env={**os.environ,'PYTHONIOENCODING':'utf-8'})
    output=(result.stdout+result.stderr).decode('utf-8',errors='replace')
    if result.returncode:raise RuntimeError(output[-2500:] or f'辅助进程退出：{result.returncode}')
    return output

def read_mac(port):
    output=child_run(['--read-mac-worker',port])
    matches=re.findall(r'MAC:\s*((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})',output)
    if not matches:raise RuntimeError('未读取到 MAC。请检查串口、USB 数据线及 BOOT 模式。\n'+output[-1600:])
    chip=re.search(r'Chip is (ESP32(?:-[A-Za-z0-9]+)?)',output)
    return {'mac':matches[-1].upper(),'chip':chip[1] if chip else 'ESP32','log':output}

def sdk_request(request):
    with tempfile.TemporaryDirectory(prefix='label-sdk-') as directory:
        p=Path(directory)/'request.json';p.write_text(json.dumps(request),encoding='utf-8')
        raw=child_run(['--sdk-worker',str(p)],timeout=35)
        for line in reversed(raw.splitlines()):
            try:response=json.loads(line)
            except ValueError:continue
            if isinstance(response,dict) and 'ok' in response:
                if not response['ok']:raise RuntimeError(response.get('error','SDK 错误'))
                return response
        raise RuntimeError('SDK 未返回有效结果')

def receive_bundle(code):
    import urllib.request
    import urllib.error
    import hashlib
    if not re.fullmatch('[A-Za-z0-9_-]{32}',code):raise ValueError('请填写完整的 32 位一次性下发码')
    request=urllib.request.Request('https://toolbox.infinlab.cn/api/label-studio/redeem',data=json.dumps({'code':code}).encode(),method='POST',headers={'Content-Type':'application/json','User-Agent':'LabelDesk/0.3.1'})
    # Handoff destination is fixed; no code, executable URLs or scripts in recipes.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):raise ValueError('下发服务发生重定向，已停止接收')
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=20) as response:raw=response.read(MAX_BUNDLE_BYTES+1024)
    except urllib.error.HTTPError as e:
        if e.code==404:raise ValueError('下发码已使用、已过期或无效') from e
        raise ValueError(f'下发服务返回 HTTP {e.code}') from e
    if len(raw)>MAX_BUNDLE_BYTES+512:raise ValueError('下发数据过大')
    data=json.loads(raw);bundle=validate_bundle(data['bundle'])
    if not hmac.compare_digest(bundle_digest(bundle),data.get('digest','')):raise ValueError('流程包摘要校验失败')
    return bundle


def gui_main():
    import csv
    import time
    import datetime
    import ipaddress
    from PySide6.QtCore import Qt,QObject,Signal,Slot,QThread,QTimer,QStandardPaths,QSizeF,QMarginsF,QLockFile
    from PySide6.QtGui import QPixmap,QImage,QPainter,QPageSize,QPageLayout,QFontDatabase
    from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QHBoxLayout,QVBoxLayout,QFormLayout,QLabel,QPushButton,QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QTabWidget,QPlainTextEdit,QFileDialog,QMessageBox,QInputDialog,QTableWidget,QTableWidgetItem,QHeaderView,QSplitter,QCheckBox,QSizePolicy,QScrollArea)
    from label_render import render_label,translate_label
    from label_store import LocalStore
    from workstation import CalibrationStore,ArrivalGate,atomic_json,printer_key
    import serial.tools.list_ports
    class Window(QMainWindow):
        def __init__(self,data_dir=None):
            super().__init__();self.setWindowTitle('Label Desk · 本地生产工站');self.resize(1120,800)
            self.directory=Path(data_dir or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation));self.directory.mkdir(parents=True,exist_ok=True)
            self.calibration=CalibrationStore(self.directory/'calibration.json');self.loading_calibration=False;self.auto_enabled=False;self.auto_cooldown=0;self.auto_vid=0x303a;self.auto_pid=0x1001
            self.store=LocalStore(self.directory/'production.sqlite3');self.bundle=None;self.fields=None;self.image=None;self.busy=False;self.last_job=None;self.preview_options=None;self.current_warnings=[]
            outer=QWidget();self.setCentralWidget(outer);layout=QVBoxLayout(outer);layout.setContentsMargins(26,22,26,22);layout.setSpacing(12)
            header=QHBoxLayout();brand=QLabel('Label Desk');brand.setObjectName('brand');header.addWidget(brand);header.addWidget(QLabel('本地生产工站 / Windows' if sys.platform=='win32' else '本地生产工站 / macOS'));header.addStretch();self.state=QLabel('等待导入流程');self.state.setObjectName('state');header.addWidget(self.state);layout.addLayout(header)
            controls=QHBoxLayout();self.import_button=QPushButton('导入流程包');self.import_button.clicked.connect(self.import_file);controls.addWidget(self.import_button)
            self.code=QLineEdit();self.code.setPlaceholderText('粘贴 Toolbox 一次性下发码');self.code.setMaxLength(32);controls.addWidget(self.code)
            self.receive_button=QPushButton('接收云端流程');self.receive_button.clicked.connect(self.receive);controls.addWidget(self.receive_button);layout.addLayout(controls)
            self.preset_button=QPushButton('使用 RingHi 标签');self.preset_button.clicked.connect(self.load_ringhi);controls.addWidget(self.preset_button)
            self.auto_check=QCheckBox('插入 ESP32 自动读取并打印 · 每个 MAC 一张');self.auto_check.toggled.connect(self.toggle_auto);layout.addWidget(self.auto_check)
            self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
            page=QWidget();self.tabs.addTab(page,'生产工作台');body=QHBoxLayout(page);body.setSpacing(20)
            left=QWidget();form=QVBoxLayout(left);form.setSpacing(12);scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(left);scroll.setMinimumWidth(350);column=QVBoxLayout();column.addWidget(scroll,1);body.addLayout(column,2)
            self.recipe_name=QLabel('尚未导入流程');self.recipe_name.setObjectName('heading');self.recipe_name.setWordWrap(True);form.addWidget(self.recipe_name)
            self.steps=QLabel('在 Toolbox 中编排并导出 .labeljob\n或生成下发码给此工站。');self.steps.setWordWrap(True);form.addWidget(self.steps)
            self.serial=QComboBox();self.serial.setMinimumWidth(230);row=QHBoxLayout();row.addWidget(self.serial,1);self.refresh=QPushButton('刷新串口');self.refresh.clicked.connect(self.refresh_ports);row.addWidget(self.refresh);form.addLayout(row)
            self.manual=QLineEdit();self.manual.setPlaceholderText('手动输入设备号 / 扫码枪输入');form.addWidget(self.manual)
            self.prepare_button=QPushButton('读取 / 准备下一张标签');self.prepare_button.setObjectName('primary');self.prepare_button.clicked.connect(self.prepare);self.prepare_button.setEnabled(False);form.addWidget(self.prepare_button)
            notice=QLabel('读取 MAC 会进入下载模式后重启设备，\n不擦写 Flash。请确认所选串口属于目标 ESP32。');notice.setObjectName('muted');notice.setWordWrap(True);form.addWidget(notice)
            self.adapter=QComboBox();self.adapter.addItem('厂商 SDK · USB（Windows）','usb');self.adapter.addItem('厂商 SDK · 网口（Windows）','net');self.adapter.addItem('系统打印机（已安装驱动）','system');self.adapter.currentIndexChanged.connect(self.adapter_changed)
            self.printers=QComboBox();self.printers.setMinimumWidth(220);self.scan=QPushButton('扫描打印机');self.scan.clicked.connect(self.scan_printers)
            self.driver_preferences=QPushButton('打开官方驱动首选项');self.driver_preferences.clicked.connect(self.edit_driver_preferences)
            self.check_paper=QPushButton('校验纸张与 DPI（不打印）');self.check_paper.clicked.connect(self.check_system_paper)
            self.driver_note=QLabel('');self.driver_note.setWordWrap(True);self.driver_note.setObjectName('muted')
            self.ip=QLineEdit();self.ip.setPlaceholderText('打印机 IPv4 地址，例如 192.168.1.88');self.ip.setVisible(False)
            self.dpi=QComboBox();self.dpi.addItems(['203','300','600']);self.dpi.currentIndexChanged.connect(self.invalidate)
            self.paper_mode=QComboBox();self.paper_mode.addItem('有定位间隙 / 黑线','gap');self.paper_mode.addItem('连续纸（无间隙）','continuous');self.paper_mode.addItem('沿用官方驱动设置','driver');self.paper_mode.currentIndexChanged.connect(self.invalidate)
            self.darkness=QSpinBox();self.darkness.setRange(0,20);self.darkness.setValue(10);self.speed=QSpinBox();self.speed.setRange(1,10);self.speed.setValue(4);self.gap=QSpinBox();self.gap.setRange(0,10);self.gap.setValue(2)
            fields=QFormLayout();fields.addRow('打印通道',self.adapter);fields.addRow('目标打印机',self.printers);fields.addRow('',self.scan);fields.addRow('网口地址',self.ip);fields.addRow('分辨率',self.dpi);fields.addRow('黑度',self.darkness);fields.addRow('速度 inch/s',self.speed);fields.addRow('标签间隙 mm',self.gap);form.addLayout(fields)
            self.printer_form=fields
            fields.addRow('纸张定位',self.paper_mode)
            form.addWidget(self.driver_preferences);form.addWidget(self.check_paper);form.addWidget(self.driver_note)
            self.print_button=QPushButton('提交 1 张标签');self.print_button.setObjectName('primary');self.print_button.setEnabled(False);self.print_button.clicked.connect(self.print_label);column.addWidget(self.print_button)
            self.confirm_button=QPushButton('扫码 / 人工确认纸面完成');self.confirm_button.setEnabled(False);self.confirm_button.clicked.connect(self.confirm_print);column.addWidget(self.confirm_button);form.addStretch()
            right=QVBoxLayout();body.addLayout(right,3);right.addWidget(QLabel('标签预览 · 核对后再打印'))
            self.preview=QLabel('导入流程后，读取设备号生成标签');self.preview.setObjectName('preview');self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter);self.preview.setMinimumSize(360,220);self.preview.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored);right.addWidget(self.preview,1)
            self.device_label=QLabel('设备号：—');self.device_label.setObjectName('heading');self.device_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);right.addWidget(self.device_label)
            self.details=QLabel('');self.details.setWordWrap(True);right.addWidget(self.details)
            self.warning=QLabel('');self.warning.setObjectName('warning');self.warning.setWordWrap(True);right.addWidget(self.warning)
            buttons=QHBoxLayout();self.export_png=QPushButton('导出 PNG');self.export_png.clicked.connect(self.save_image);buttons.addWidget(self.export_png);self.sdk_preview=QPushButton('校验 SDK 渲染');self.sdk_preview.clicked.connect(self.native_preview);buttons.addWidget(self.sdk_preview);right.addLayout(buttons)
            calibration_row=QHBoxLayout();self.offset_x=QDoubleSpinBox();self.offset_y=QDoubleSpinBox()
            for spin in (self.offset_x,self.offset_y):spin.setRange(-10,10);spin.setDecimals(2);spin.setSingleStep(.1);spin.setSuffix(' mm');spin.valueChanged.connect(self.offset_changed)
            calibration_row.addWidget(QLabel('左右'));calibration_row.addWidget(self.offset_x);calibration_row.addWidget(QLabel('上下'));calibration_row.addWidget(self.offset_y)
            self.reset_offsets=QPushButton('归零');self.reset_offsets.clicked.connect(lambda:(self.offset_x.setValue(0),self.offset_y.setValue(0)));calibration_row.addWidget(self.reset_offsets);right.addLayout(calibration_row)
            self.calibration_note=QLabel('微调：正数向右 / 向下，负数向左 / 向上；按打印机自动保存。');self.calibration_note.setWordWrap(True);self.calibration_note.setObjectName('muted');right.addWidget(self.calibration_note)
            self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMinimumHeight(60);self.log.setMaximumHeight(90);right.addWidget(self.log)
            history=QWidget();hl=QVBoxLayout(history);self.history=QTableWidget(0,5);self.history.setHorizontalHeaderLabels(['时间','设备号','状态','说明','补打原因']);self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);hl.addWidget(self.history);export=QPushButton('导出本地记录 CSV');export.clicked.connect(self.export_records);hl.addWidget(export);self.tabs.addTab(history,'本地生产记录')
            guide=QPlainTextEdit();guide.setReadOnly(True);guide.setPlainText('ZMIN / Windows 基础设置\n\n1. 安装与机器型号相符的官方驱动，扫描并选择系统打印机。程序按驱动匹配 203 / 300 / 600 DPI；X1i 通常为 300 DPI，以驱动报告为准。\n2. 标签宽高来自 Toolbox 模板，默认 30 × 20 mm。系统通道每次提交使用单份、单面、黑白、100% 尺寸；不按页面自动缩放。\n3. 碳带 / 热转印、纸张类型（间隙 / 黑标 / 连续）、速度、黑度、间隙检测、切刀等选项，在“官方驱动首选项”中设置。应用程序保留驱动的私有参数。\n4. 修改驱动设置后重新准备标签。驱动返回的纸张尺寸或 DPI 不匹配时停止提交。\n5. 首次到货先用官方软件验证走纸、碳带和纸张，再在此工站打印并扫码确认。\n\n官方半色调指南\n\n官方针对图片打印建议：高级 → 半色调设置 → 属性，图案 2×2，设备伽玛与像素直径滑块拉到最右。请按纸面效果在驱动中手动调整。此程序不修改全局半色调参数；二维码采用黑白整数像素，仍需实机扫码验证。\n\n功能范围\n\n已支持文字、二维码、线框、内嵌 PNG Logo、自有 JSON 模板 / .labeljob、ESP32 MAC、单张提交、补打原因与 CSV 生产记录。\n尚不支持直接编辑 / 导入官方 LabelSoft LSF、Excel 批量数据源、流水号批量打印、一维码、RFID 或切刀流程编排。不要将改文件后缀当作模板转换。\n\nmacOS\n\n使用系统打印机，需要另外取得适配机型的 macOS 驱动或可用系统队列。Windows 驱动不能安装到 Mac。')
            self.tabs.addTab(guide,'打印设置说明')
            guide.appendPlainText('\n\n毫米微调与自动生产\n左右正数向右、上下正数向下；每次变化立即保存到当前打印机，下次启动恢复。预览与最终打印使用同一偏移，超出标签边界时停止提交。\n自动模式仅监控配置的 USB VID/PID，插入稳定后读取 MAC；每个 MAC 本机只自动提交一次。重复设备跳过；需要补打时关闭自动模式，手动读取并填写补打原因。\n参数变化或异常会暂停自动模式，请确认后重新勾选。设备读取会重启 ESP32，不擦写固件。\n勾选状态也会保存，重启后恢复监听；使用完请取消勾选。二维码内容为原始 MAC，纸面确认也支持扫描 MAC。')
            self.statusBar().showMessage('数据仅保存在本机 · 实际输出需纸面确认 · v0.3.1')
            self.mutations=[self.import_button,self.receive_button,self.refresh,self.scan,self.adapter,self.serial,self.manual,self.printers,self.ip,self.dpi,self.speed,self.darkness,self.gap,self.code,self.driver_preferences,self.check_paper,self.preset_button,self.offset_x,self.offset_y,self.reset_offsets]
            self.mutations.append(self.paper_mode)
            guide.appendPlainText('\n\n跨标签或跳纸\n系统通道可明确选择“有定位间隙 / 黑线”或连续纸。ZMIN 标签纸应选择前者；其他驱动若不提供对应选项，可选择沿用驱动并在官方首选项中设置。\n二维码跨过两张标签的间隙时，先确认纸张尺寸和做测纸校准，不能仅靠毫米偏移修复。测纸：待机长按进纸约 4 秒，灯闪烁并走纸后松开，等待恢复正常。具体机型以官方说明为准。\nRingHi 像素标签：顶部居中 RingHi_ 加 MAC 末四位，中间二维码与像素 Logo 并排，底部一行完整 MAC。二维码仍包含完整原始 MAC。PNG 原图嵌入模板，打印时转为黑白。')
            self.manual.textChanged.connect(self.invalidate);self.serial.currentIndexChanged.connect(self.invalidate)
            for control in [self.printers,self.adapter]:control.currentIndexChanged.connect(self.invalidate)
            for control in [self.speed,self.darkness,self.gap]:control.valueChanged.connect(self.invalidate)
            self.ip.textChanged.connect(self.invalidate)
            self.adapter.setCurrentIndex(2)
            self.recipe_name.setTextFormat(Qt.TextFormat.PlainText)
            self.refresh_ports();self.refresh_history();self.adapter_changed()
            self.printers.currentIndexChanged.connect(self.printer_changed)
            self.printers.currentIndexChanged.connect(self.load_calibration);self.ip.textChanged.connect(self.load_calibration)
            self.scan_printers()
            self.restore_settings()
            cache=self.directory/'active.labeljob'
            if cache.is_file():
                try:self.load_bundle(json.loads(cache.read_text(encoding='utf-8')))
                except Exception:self.log.appendPlainText('上次流程未能载入，请重新导入')
            self.auto_timer=QTimer(self);self.auto_timer.timeout.connect(self.auto_poll);self.auto_timer.start(700)
            try:
                auto=json.loads((self.directory/'auto-settings.json').read_text(encoding='utf-8'))
                if auto.get('vid') in (0x303a,0x10c4,0x1a86,0x0403) and isinstance(auto.get('pid'),int):self.auto_vid=auto['vid'];self.auto_pid=auto['pid']
                if data_dir is None and auto.get('enabled'):QTimer.singleShot(1000,lambda:self.auto_check.setChecked(True))
            except FileNotFoundError:pass
            except Exception:self.log_message('自动生产配置无效，请重新开启')
        def log_message(self,text):self.log.appendPlainText(text)
        def task(self,fn,success,failure=None):
            if self.busy:return
            self.busy=True;self.prepare_button.setEnabled(False);self.print_button.setEnabled(False)
            for control in self.mutations:control.setEnabled(False)
            import queue,threading
            self.task_results=queue.Queue();self.task_success=success;self.task_failure=failure
            def execute():
                try:self.task_results.put((True,fn()))
                except Exception as e:self.task_results.put((False,str(e)))
            self.task_timer=QTimer(self);self.task_timer.timeout.connect(self.poll_task);self.task_timer.start(80)
            threading.Thread(target=execute,daemon=True,name='label-io').start()
        @Slot()
        def poll_task(self):
            import queue
            try:ok,value=self.task_results.get_nowait()
            except queue.Empty:return
            self.task_timer.stop();self.task_timer.deleteLater();self.finish_task()
            if ok:self.task_success(value)
            else:self.log_message(value);(self.task_failure or self.error)(value)
        def finish_task(self):
            self.busy=False
            for control in self.mutations:control.setEnabled(True)
            self.prepare_button.setEnabled(bool(self.bundle));self.print_button.setEnabled(self.image is not None)
            self.update_channel_controls()
        def error(self,message):
            if self.auto_enabled:self.auto_failure(message);return
            QMessageBox.warning(self,'Label Desk',message)
        def invalidate(self,*args):
            if self.auto_enabled:self.pause_auto('参数变更，自动打印已暂停')
            self.fields=None;self.image=None;self.preview_options=None;self.system_proof=None;self.print_button.setEnabled(False);self.preview.setText('参数或设备已变更，请重新准备标签');self.device_label.setText('设备号：—')
        def refresh_ports(self):
            self.serial.clear();self.serial.addItem('请选择目标 ESP32 串口',None)
            for p in serial.tools.list_ports.comports():self.serial.addItem(f'{p.device} · {p.description}',p.device)
            if self.serial.count()==0:self.serial.addItem('未发现串口',None)
        def load_bundle(self,bundle):
            b=validate_bundle(bundle);self.bundle=b;self.invalidate();self.recipe_name.setText(b['name']);names={'read_esp32_mac':'读取 ESP32 MAC','manual_input':'手动 / 扫码输入','format_id':'格式处理','preview':'预览确认','print_label':'打印标签'}
            self.steps.setText(' → '.join(names[s['type']] for s in b['workflow']['steps']));self.prepare_button.setEnabled(True);self.state.setText('流程已接收，等待执行');self.manual.setVisible(b['workflow']['steps'][0]['type']=='manual_input');(self.directory/'active.labeljob').write_text(json.dumps(b,ensure_ascii=False,indent=2),encoding='utf-8');self.log_message('流程已验证：'+bundle_digest(b)[:16])
        def load_ringhi(self):
            try:self.load_bundle(json.loads((BASE/'examples'/'ringhi-mac.labeljob').read_text(encoding='utf-8')))
            except Exception as e:self.error(str(e))
        def import_file(self):
            path,_=QFileDialog.getOpenFileName(self,'导入生产流程','','生产流程 (*.labeljob *.json)')
            if not path:return
            try:
                if Path(path).stat().st_size>MAX_BUNDLE_BYTES:raise ContractError('流程包超过 2 MiB')
                self.load_bundle(json.loads(Path(path).read_text(encoding='utf-8-sig')))
            except Exception as e:self.error(str(e))
        def receive(self):
            code=self.code.text().strip()
            self.task(lambda:receive_bundle(code),lambda b:(self.load_bundle(b),self.code.clear()))
        def prepare(self):
            if not self.bundle:return
            self.invalidate();self.state.setText('准备设备数据')
            if self.bundle['workflow']['steps'][0]['type']=='read_esp32_mac':
                port=self.serial.currentData()
                if not port:return self.error('请选择 ESP32 串口')
                self.task(lambda:read_mac(port),self.mac_ready)
            else:
                try:self.render({'device_id':self.manual.text().strip()})
                except Exception as e:self.error(str(e))
        def mac_ready(self,result):
            self.log_message(result['log'])
            try:self.render({'mac':result['mac'],'model':result.get('chip','ESP32')})
            except Exception as e:self.error(str(e))
        def render(self,source):
            if not self.dpi.currentText():raise ContractError('所选打印机没有支持的标签 DPI，请重新选择打印机')
            source['date']=datetime.datetime.now().astimezone().date().isoformat();self.fields=execute_data(self.bundle,source);base,self.current_warnings=render_label(self.bundle['template'],self.fields,int(self.dpi.currentText()));self.image=translate_label(base,self.offset_x.value(),self.offset_y.value(),int(self.dpi.currentText()));self.preview_options=self.options();self.display_image(self.image);self.device_label.setText('设备号：'+self.fields['device_id']);self.details.setText(f"原始 MAC：{self.fields.get('mac','—')}\n{self.bundle['template']['width']} × {self.bundle['template']['height']} mm / {self.dpi.currentText()} dpi");self.warning.setText('；'.join(self.current_warnings));self.state.setText('标签已准备，未发送');self.print_button.setEnabled(True)
            self.save_settings()
        def display_image(self,image):
            self.preview.setPixmap(QPixmap.fromImage(image).scaled(self.preview.size()-__import__('PySide6.QtCore',fromlist=['QSize']).QSize(36,36),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.FastTransformation))
        def resizeEvent(self,event):
            super().resizeEvent(event)
            if getattr(self,'image',None) is not None:QTimer.singleShot(0,lambda:self.display_image(self.image) if self.image is not None else None)
        def adapter_changed(self,*args):
            mode=self.adapter.currentData();self.ip.setVisible(mode=='net');self.printers.setVisible(mode!='net');self.scan.setEnabled(mode!='net');self.printers.clear();self.printers.addItem('请扫描并明确选择打印机',None)
            self.dpi.clear();self.dpi.addItems(['203','300','600']);self.update_channel_controls()
            self.load_calibration()
        def update_channel_controls(self):
            system=self.adapter.currentData()=='system'
            self.prepare_button.setEnabled(bool(self.bundle) and not self.busy and not self.auto_enabled)
            for control in (self.darkness,self.speed,self.gap):
                control.setEnabled(not system and not self.busy);self.printer_form.setRowVisible(control,not system)
            self.printer_form.setRowVisible(self.ip,self.adapter.currentData()=='net')
            self.printer_form.setRowVisible(self.printers,self.adapter.currentData()!='net')
            self.printer_form.setRowVisible(self.scan,self.adapter.currentData()!='net')
            self.printer_form.setRowVisible(self.paper_mode,system and sys.platform=='win32')
            self.scan.setEnabled(self.adapter.currentData()!='net' and not self.busy)
            self.driver_preferences.setVisible(system and sys.platform=='win32');self.driver_preferences.setEnabled(system and not self.busy)
            self.check_paper.setVisible(system);self.check_paper.setEnabled(system and not self.busy)
            self.sdk_preview.setEnabled(sys.platform=='win32' and not system and not self.busy)
            self.driver_note.setText('黑度 / 速度 / 间隙 / 热转印等在官方驱动首选项中设置。标签尺寸跟随模板，单份、100% 尺寸打印。' if system else 'SDK 使用上方黑度、速度、间隙。碳带及传感器设置请先在机器或厂商工具中确认。')
        def printer_changed(self,*args):
            if self.adapter.currentData()!='system':return
            chosen=self.printers.currentData() or {}
            self.dpi.blockSignals(True);old=self.dpi.currentText();self.dpi.clear()
            try:
                from system_print import printer_details
                details=printer_details(chosen['name']) if chosen.get('name') else {'dpi':[]}
                supported=details['dpi'];values=[str(d) for d in (203,300,600) if not supported or d in supported]
                self.dpi.addItems(values)
                if old in values:self.dpi.setCurrentText(old)
                if supported:self.log_message('驱动报告的 DPI：'+', '.join(map(str,supported)))
                if not values:self.log_message('此驱动没有工站支持的 DPI，请选择标签打印机')
            except Exception as e:self.dpi.addItems(['203','300','600']);self.log_message(str(e))
            finally:self.dpi.blockSignals(False)
            self.invalidate()
        def edit_driver_preferences(self):
            name=(self.printers.currentData() or {}).get('name')
            if not name:return self.error('请先选择系统打印机')
            from system_print import open_preferences
            self.invalidate();self.task(lambda:open_preferences(name),lambda _:(self.printer_changed(),self.log_message('驱动设置窗口已关闭，请重新准备标签')))
        def check_system_paper(self):
            if not self.bundle:return self.error('请先导入流程包')
            name=(self.printers.currentData() or {}).get('name')
            if not name or not self.dpi.currentText():return self.error('请选择支持的系统打印机及 DPI')
            try:
                from system_print import preflight
                proof=preflight(name,self.bundle['template'],int(self.dpi.currentText()),self.image,self.options()['paper_mode'])
                self.log_message(f"驱动校验通过：{proof['width_px']} × {proof['height_px']} 点 / {proof['dpi_x']} dpi；单份，100% 尺寸。未提交打印。")
                self.state.setText('纸张配置已验证，未打印')
            except Exception as e:self.error(str(e))
        def save_settings(self):
            path=self.directory/'printer-settings.json';temporary=path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.options(),ensure_ascii=False),encoding='utf-8');temporary.replace(path)
        def load_calibration(self,*args):
            if not hasattr(self,'offset_x'):return
            self.loading_calibration=True
            try:
                key=printer_key(self.options());x,y=self.calibration.get(key) if key else (0,0)
                self.offset_x.setValue(x);self.offset_y.setValue(y)
                self.calibration_note.setText('微调已恢复；正数向右 / 向下，负数向左 / 向上。' if key else '选择打印机后，可保存左右 / 上下微调。')
            except Exception as e:self.offset_x.setValue(0);self.offset_y.setValue(0);self.log_message(str(e))
            finally:self.loading_calibration=False
        def offset_changed(self,*args):
            if self.loading_calibration:return
            old=dict(self.fields) if self.fields else None
            self.invalidate()
            try:
                self.calibration.save(printer_key(self.options()),self.offset_x.value(),self.offset_y.value());self.save_settings()
                self.calibration_note.setText(f'已保存：左右 {self.offset_x.value():+.2f} mm / 上下 {self.offset_y.value():+.2f} mm；正数右 / 下。')
                if old:self.render(old)
            except Exception as e:self.warning.setText(str(e))
        def restore_settings(self):
            path=self.directory/'printer-settings.json'
            if not path.is_file():return
            try:
                saved=json.loads(path.read_text(encoding='utf-8'))
                mode=self.paper_mode.findData(saved.get('paper_mode','gap'))
                if mode>=0:self.paper_mode.setCurrentIndex(mode)
                # Restore system queues only when still present; never select a new printer silently.
                if saved.get('interface')=='system':
                    index=next((i for i in range(self.printers.count()) if (self.printers.itemData(i) or {}).get('name')==saved.get('name')),-1)
                    self.printers.setCurrentIndex(max(0,index))
                    if self.dpi.findText(str(saved.get('dpi')))>=0:self.dpi.setCurrentText(str(saved['dpi']))
            except Exception:self.log_message('上次打印机设置无法恢复，请重新选择')
        def scan_printers(self):
            if self.adapter.currentData()=='system':
                from PySide6.QtPrintSupport import QPrinterInfo
                old=(self.printers.currentData() or {}).get('name');self.printers.clear();self.printers.addItem('请选择系统打印机',None)
                for info in QPrinterInfo.availablePrinters():self.printers.addItem(info.printerName(),{'name':info.printerName()})
                if old:
                    index=next((i for i in range(self.printers.count()) if (self.printers.itemData(i) or {}).get('name')==old),0);self.printers.setCurrentIndex(index)
            else:
                def ready(result):
                    self.printers.clear()
                    for p in result['printers']:self.printers.addItem(p['name']+' · '+p['serial'],p)
                    if not self.printers.count():self.printers.addItem('未发现 SDK USB 打印机',None)
                    self.log_message(f"SDK 枚举完成：{len(result['printers'])} 台")
                self.task(lambda:sdk_request({'action':'enumerate'}),ready)
        def options(self):
            chosen=self.printers.currentData() or {};opts={'interface':self.adapter.currentData(),'serial':chosen.get('serial',''),'name':chosen.get('name',''),'ip':self.ip.text().strip(),'dpi':int(self.dpi.currentText()) if self.dpi.currentText() else 0,'offset_x_mm':self.offset_x.value(),'offset_y_mm':self.offset_y.value()}
            if opts['interface']!='system':opts.update(speed=self.speed.value(),darkness=self.darkness.value(),gap=self.gap.value())
            opts['paper_mode']=self.paper_mode.currentData() if sys.platform=='win32' and opts['interface']=='system' else 'driver'
            return opts
        def request(self,action):
            image_path=self.directory/'pending-label.png';self.image.save(str(image_path),'PNG');return {**self.options(),'action':action,'image':str(image_path),'width':self.bundle['template']['width'],'height':self.bundle['template']['height'],'output':str(self.directory/'sdk-preview.bmp')}
        def native_preview(self):
            if self.busy:return
            if self.image is None:return self.error('请先准备标签')
            if sys.platform!='win32':return self.error('此校验功能使用 Windows 原生 SDK。Mac 采用系统驱动预览。')
            req=self.request('preview')
            def ready(r):
                img=QImage(r['output'])
                if img.isNull():return self.error('SDK 返回的图像不可读取')
                self.display_image(img);self.log_message(f'SDK 实际渲染：{img.width()} × {img.height()} px；请核对与标签预览一致')
                self.state.setText('已展示 SDK 渲染，尚未打印')
            self.task(lambda:sdk_request(req),ready)
        def toggle_auto(self,enabled):
            if not enabled:
                self.auto_enabled=False
                atomic_json(self.directory/'auto-settings.json',{'enabled':False,'vid':self.auto_vid,'pid':self.auto_pid})
                self.state.setText('自动生产已关闭')
                self.update_channel_controls();return
            try:
                if self.busy:raise ValueError('请等待当前任务完成再开启自动生产')
                if not self.bundle or self.bundle['workflow']['steps'][0]['type']!='read_esp32_mac':raise ValueError('自动生产需要 ESP32 MAC 流程，请先使用 RingHi 标签')
                opts=self.options()
                if opts['interface']!='system' or not opts['name']:raise ValueError('请先选择系统打印机；自动生产目前使用系统驱动通道')
                selected=self.serial.currentData()
                port=next((p for p in serial.tools.list_ports.comports() if p.device==selected),None)
                if port:
                    if port.vid not in (0x303a,0x10c4,0x1a86,0x0403) or port.pid is None:raise ValueError('请选择 ESP32 USB 或 CP210x / CH34x / FTDI 串口')
                    self.auto_vid,self.auto_pid=port.vid,port.pid
                from system_print import preflight
                preflight(opts['name'],self.bundle['template'],opts['dpi'],paper_mode=opts['paper_mode'])
                self.auto_gate=ArrivalGate(self.auto_vid,self.auto_pid);self.auto_enabled=True;self.save_settings()
                atomic_json(self.directory/'auto-settings.json',{'enabled':True,'vid':self.auto_vid,'pid':self.auto_pid})
                self.state.setText('自动生产已开启，等待插入 ESP32');self.log_message(f'自动监控 USB {self.auto_vid:04X}:{self.auto_pid:04X}；每个 MAC 仅自动提交一次')
                self.update_channel_controls()
            except Exception as e:self.pause_auto(str(e));self.warning.setText(str(e))
        def pause_auto(self,message):
            self.auto_enabled=False;self.auto_check.blockSignals(True);self.auto_check.setChecked(False);self.auto_check.blockSignals(False)
            atomic_json(self.directory/'auto-settings.json',{'enabled':False,'vid':self.auto_vid,'pid':self.auto_pid})
            self.state.setText(message);self.log_message(message);self.update_channel_controls()
        def auto_failure(self,message):
            self.pause_auto('自动生产暂停：'+message);self.warning.setText(message)
        def auto_poll(self):
            if not self.auto_enabled or self.busy or time.monotonic()<self.auto_cooldown:return
            try:
                port=self.auto_gate.poll(list(serial.tools.list_ports.comports()),time.monotonic())
                if port is None:return
                self.serial.blockSignals(True)
                index=self.serial.findData(port.device)
                if index<0:self.serial.addItem(f'{port.device} · {port.description}',port.device);index=self.serial.count()-1
                self.serial.setCurrentIndex(index);self.serial.blockSignals(False)
                self.fields=None;self.image=None;self.preview_options=None;self.print_button.setEnabled(False)
                self.state.setText('自动读取 '+port.device)
                self.task(lambda:read_mac(port.device),self.auto_mac_ready,self.auto_failure)
            except Exception as e:self.auto_failure(str(e))
        def auto_mac_ready(self,result):
            self.auto_cooldown=time.monotonic()+5;self.log_message(result['log'])
            try:
                self.render({'mac':result['mac'],'model':result.get('chip','ESP32')})
                if not self.auto_enabled:self.state.setText('自动已停止，标签已准备但未打印');return
                if self.current_warnings:raise ValueError('标签需人工检查：'+'；'.join(self.current_warnings))
                self.submit_prepared(automatic=True)
            except Exception as e:self.auto_failure(str(e))
        def print_label(self):
            if self.auto_enabled:self.pause_auto('已切换为手动打印')
            self.submit_prepared()
        def submit_prepared(self,automatic=False,test_reason=None):
            if self.busy or not self.bundle or self.image is None:return
            opts=self.options()
            if opts!=self.preview_options:return self.error('参数已改变，请重新准备标签')
            if opts['interface']=='usb' and not opts['serial']:return self.error('请扫描并选择目标 USB 打印机')
            if opts['interface']=='net':
                try:ipaddress.IPv4Address(opts['ip'])
                except ValueError:return self.error('请输入有效的打印机 IPv4 地址')
            if opts['interface']=='system' and not opts['name']:return self.error('请选择系统打印机')
            if opts['interface']=='system':
                try:
                    from system_print import preflight
                    self.system_proof=preflight(opts['name'],self.bundle['template'],opts['dpi'],self.image,opts['paper_mode'])
                    opts['driver_preflight']=self.system_proof
                except Exception as e:return self.error(str(e))
            digest=bundle_digest(self.bundle);reason=''
            if automatic and self.store.duplicate_mac(self.fields.get('mac','')):
                self.state.setText('该 MAC 已提交过，已跳过');self.log_message('跳过重复 MAC：'+self.fields.get('mac',''));self.print_button.setEnabled(False);return
            if self.store.duplicate(self.fields['device_id'],digest):
                if test_reason:reason=test_reason
                else:
                    reason,ok=QInputDialog.getText(self,'重复设备 / 补打','该设备已有提交或待核实记录，请检查纸面并填写补打原因：')
                    if not ok or not reason.strip():return
            elif test_reason:reason=test_reason
            msg=f"设备号：{self.fields['device_id']}\n打印机：{opts['name'] or opts['ip']}\n提交 1 张标签。\n"+'；'.join(self.current_warnings)
            if not automatic and not test_reason and QMessageBox.question(self,'确认提交标签',msg)!=QMessageBox.StandardButton.Yes:return
            req=self.request('print');job=self.store.start(self.fields,self.bundle,digest,opts,reason);self.last_job=job;self.state.setText('正在提交');self.refresh_history()
            def completed(r):
                self.last_submission=r
                status=r.get('status','unknown');self.store.finish(job,status,r.get('message',''));self.state.setText('已提交，待纸面确认' if status=='submitted' else '结果未知，请检查纸面');self.confirm_button.setEnabled(status in ('submitted','unknown'));self.print_button.setEnabled(False);self.refresh_history();self.log_message(r.get('message',''))
                self.auto_cooldown=time.monotonic()+5
                if automatic and status!='submitted':self.auto_failure('打印结果未知，请检查纸面')
            def failure(message):
                self.store.finish(job,'unknown',message);self.state.setText('结果未知，禁止自动重试');self.confirm_button.setEnabled(True);self.print_button.setEnabled(False);self.refresh_history()
                if automatic:self.auto_failure(message)
                else:self.error(message)
            if opts['interface']=='system':
                if sys.platform=='win32':self.task(lambda:self.system_print(opts),completed,failure)
                else:
                    try:completed(self.system_print(opts))
                    except Exception as e:failure(str(e))
            else:self.task(lambda:sdk_request(req),completed,failure)
        def system_print(self,opts):
            from system_print import submit
            return submit(opts['name'],self.bundle['template'],opts['dpi'],self.image,self.system_proof,opts['paper_mode'])
        def confirm_print(self):
            if self.busy:return
            if not self.last_job:return
            row=next((r for r in self.store.rows() if r['id']==self.last_job),None)
            if not row:return
            scanned,ok=QInputDialog.getText(self,'纸面确认','用扫码枪或手动输入实际标签设备号：')
            if not ok:return
            if scanned.strip()!=row['device_id'] and (not row.get('mac') or scanned.strip().upper()!=row['mac'].upper()):return self.error('输入与任务设备号 / MAC 不一致，未确认')
            self.store.finish(self.last_job,'confirmed','操作者核对纸面设备号完成');self.confirm_button.setEnabled(False);self.state.setText('纸面已确认');self.refresh_history()
        def refresh_history(self):
            rows=self.store.rows();self.history.setRowCount(len(rows));names={'sending':'提交中','submitted':'已提交，待确认','unknown':'结果未知','failed':'失败','confirmed':'人工确认完成','simulated':'软件演练，未打印'}
            for i,r in enumerate(rows):
                for j,key in enumerate(['created_at','device_id','status','message','reprint_reason']):self.history.setItem(i,j,QTableWidgetItem(names.get(r[key],r[key]) if key=='status' else r[key]))
        def save_image(self):
            if self.image is None:return self.error('请先准备标签')
            path,_=QFileDialog.getSaveFileName(self,'导出标签',self.fields['device_id']+'.png','PNG 图像 (*.png)')
            if path:self.image.save(path,'PNG')
        def export_records(self):
            path,_=QFileDialog.getSaveFileName(self,'导出生产记录','LabelDesk-records.csv','CSV (*.csv)')
            if not path:return
            keys=['created_at','device_id','mac','status','message','digest','reprint_reason']
            def safe(v):return "'"+str(v) if str(v).startswith(('=','+','-','@','\t','\r')) else str(v)
            with open(path,'w',encoding='utf-8-sig',newline='') as f:
                writer=csv.writer(f);writer.writerow(keys)
                for r in self.store.rows():writer.writerow([safe(r[k]) for k in keys])
        def closeEvent(self,event):
            if self.busy:self.error('操作执行中，请等待结果后关闭。');event.ignore()
            else:event.accept()
    app=QApplication(sys.argv);app.setApplicationName('LabelDesk');app.setOrganizationName('InfinLab')
    if sys.platform=='win32':
        for font in ['arial.ttf','arialbd.ttf','msyh.ttc']:
            path=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/font
            if path.exists():QFontDatabase.addApplicationFont(str(path))
    app.setStyleSheet('''QWidget{font-family:"Segoe UI","PingFang SC";font-size:13px;color:#293124;background:#f8f9f4}QLineEdit,QComboBox,QSpinBox,QPlainTextEdit,QTableWidget{background:white;border:1px solid #dce2d4;border-radius:5px;padding:7px}QPushButton{background:white;border:1px solid #d9dfd1;border-radius:6px;padding:10px 14px}QPushButton:hover{background:#eef2e6}QPushButton:disabled{color:#aab19f;background:#f2f4ed}QPushButton#primary{background:#df6537;color:white;border-color:#df6537}QPushButton#primary:disabled{background:#efc3b1;color:#fff}QLabel#brand{font-size:26px;font-weight:700}QLabel#heading{font-size:16px;font-weight:600}QLabel#state{background:#eaf0e1;color:#6f805f;padding:7px;border-radius:5px}QLabel#preview{background:#edf0e6;border:1px solid #e1e6d8;border-radius:8px}QLabel#muted{font-size:11px;color:#8b967c}QLabel#warning{color:#b57534}QTabWidget::pane{border:1px solid #e0e5d7;border-radius:7px}QTabBar::tab{padding:10px 20px}QTabBar::tab:selected{color:#df6537;background:white}''')
    if '--production-once' in sys.argv:
        def arg(flag):return sys.argv[sys.argv.index(flag)+1]
        import uuid
        token=str(uuid.UUID(arg('--request-id')))
        directory=Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation));directory.mkdir(parents=True,exist_ok=True)
        lock=QLockFile(str(directory/'workstation.lock'))
        if not lock.tryLock(100):raise ValueError('工站正在运行，请先关闭已有窗口再执行单张测试')
        marker=directory/'print-requests'/f'{token}.json'
        if marker.exists():
            previous=json.loads(marker.read_text(encoding='utf-8'));json_output({'replayed':True,**previous});lock.unlock();return 0 if previous.get('status')=='submitted' else 1
        atomic_json(marker,{'status':'pending','request_id':token,'paper_confirmed':False})
        output=Path(arg('--production-once'));output.mkdir(parents=True,exist_ok=True)
        result={};errors=[]
        try:
            window=Window(directory);window.error=lambda message:errors.append(message)
            window.load_bundle(json.loads(Path(arg('--bundle')).read_text(encoding='utf-8')))
            name=arg('--system-printer');index=next((i for i in range(window.printers.count()) if (window.printers.itemData(i) or {}).get('name')==name),-1)
            if index<0:raise ValueError('测试打印机不存在')
            window.printers.setCurrentIndex(index);port=arg('--port');window.serial.setCurrentIndex(window.serial.findData(port))
            read=read_mac(port);window.render({'mac':read['mac'],'model':read['chip']})
            if window.current_warnings:raise ValueError('标签需要检查：'+'；'.join(window.current_warnings))
            window.image.save(str(output/'label.png'));window.show();app.processEvents();window.display_image(window.image);app.processEvents();window.grab().save(str(output/'desktop-preview.png'))
            window.submit_prepared(test_reason=arg('--test-reason'))
            if errors or not window.last_job:raise ValueError('; '.join(errors) or '未提交任务')
            def check_result():
                if window.busy:return
                row=next(r for r in window.store.rows() if r['id']==window.last_job)
                result.update(status=row['status'],request_id=token,job_id=row['id'],mac=read['mac'],chip=read['chip'],
                              printer=name,options=window.options(),message=row['message'],submission=getattr(window,'last_submission',{}),paper_confirmed=False)
                timer.stop();app.quit()
            timer=QTimer();timer.timeout.connect(check_result);timer.start(150)
            QTimer.singleShot(60000,app.quit);app.exec()
            if not result:result={'status':'unknown','request_id':token,'message':'等待打印结果超时，请核对队列与纸面；禁止重试','paper_confirmed':False}
            if '--enable-auto-after' in sys.argv and result['status']=='submitted':
                atomic_json(directory/'auto-settings.json',{'enabled':True,'vid':0x303a,'pid':0x1001})
        except Exception as e:
            result={'status':'unknown','request_id':token,'message':str(e),'paper_confirmed':False}
        finally:
            atomic_json(marker,result);atomic_json(output/'print-result.json',result);lock.unlock()
        json_output(result);return 0 if result.get('status')=='submitted' else 1
    if '--smoke-test' in sys.argv:
        index=sys.argv.index('--smoke-test');directory=Path(sys.argv[index+1]);directory.mkdir(parents=True,exist_ok=True)
        def argument(flag,default=None):return sys.argv[sys.argv.index(flag)+1] if flag in sys.argv else default
        window=Window(directory)
        if '--receive-code-file' in sys.argv:bundle=receive_bundle(Path(argument('--receive-code-file')).read_text().strip())
        else:bundle=json.loads(Path(argument('--bundle',BASE/'examples'/'esp32-mac.labeljob')).read_text(encoding='utf-8'))
        window.load_bundle(bundle)
        if '--sdk-preview' in sys.argv:window.adapter.setCurrentIndex(0)
        if '--system-printer' in sys.argv:
            window.adapter.setCurrentIndex(2);window.scan_printers();name=argument('--system-printer')
            index=next((i for i in range(window.printers.count()) if (window.printers.itemData(i) or {}).get('name')==name),-1)
            if index<0:raise ValueError('测试打印机未找到')
            window.printers.setCurrentIndex(index)
        source=read_mac(argument('--port')) if '--port' in sys.argv else {'mac':'02:00:00:00:00:01','device_id':'000123'}
        if '--port' in sys.argv:window.serial.setCurrentIndex(window.serial.findData(argument('--port')))
        window.render(source);window.image.save(str(directory/'label.png'));window.show();app.processEvents();window.display_image(window.image);app.processEvents();window.grab().save(str(directory/'desktop-preview.png'))
        proof={'ok':True,'width':window.image.width(),'height':window.image.height(),'device_id':window.fields['device_id'],'warnings':window.current_warnings,'serial_ports':[p.device for p in serial.tools.list_ports.comports()],'physical_print':False,'bundle_digest':bundle_digest(bundle),'live_mac_read':'--port' in sys.argv}
        if '--system-printer' in sys.argv:
            from system_print import preflight
            opts=window.options();proof['driver']=preflight(opts['name'],window.bundle['template'],opts['dpi'],window.image,opts['paper_mode'])
            proof['sdk_controls_disabled']=all(not w.isEnabled() for w in (window.speed,window.darkness,window.gap))
            proof['driver_preferences_available']=window.driver_preferences.isVisible()
            assert proof['sdk_controls_disabled']
        if '--sdk-preview' in sys.argv:proof['sdk']=sdk_request(window.request('preview'))
        def finished(value):
            import threading
            proof['ui_callback_main_thread']=threading.current_thread() is threading.main_thread()
            if '--exercise-workstation' not in sys.argv:QTimer.singleShot(100,app.quit);return
            try:
                if '--system-printer' not in sys.argv:raise ValueError('集成测试需要指定系统打印机')
                window.auto_timer.stop();old=(window.offset_x.value(),window.offset_y.value())
                window.offset_x.setValue(.2);window.offset_y.setValue(-.2)
                restored=Window(directory);restored.auto_timer.stop()
                proof['calibration_restored']=(restored.offset_x.value(),restored.offset_y.value())==(.2,-.2)
                restored.close();window.offset_x.setValue(old[0]);window.offset_y.setValue(old[1])
                # Explicit test-only substitute. No production branch changes the print backend.
                window.system_print=lambda opts:{'status':'submitted','message':'集成测试模拟提交，未调用打印机'}
                window.auto_check.setChecked(True)
                if not window.auto_enabled:raise ValueError('自动模式未能开启')
                fake={'mac':source['mac'],'chip':'ESP32-S3','log':'集成测试：模拟插入后的 MAC 回调'}
                window.auto_mac_ready(fake)
                def verify_auto():
                    if window.busy:return
                    try:
                        before=len(window.store.rows());window.auto_mac_ready(fake)
                        proof['auto_dedup_verified']=before==1 and len(window.store.rows())==1 and not window.busy
                        if not proof['auto_dedup_verified']:raise ValueError('自动重复提交保护失败')
                        window.auto_check.setChecked(False)
                    except Exception as e:proof['integration_error']=str(e)
                    test_timer.stop();app.quit()
                test_timer=QTimer(window);test_timer.timeout.connect(verify_auto);test_timer.start(100)
            except Exception as e:proof['integration_error']=str(e);app.quit()
        window.task(lambda:'background-check',finished)
        QTimer.singleShot(10000,app.quit);app.exec()
        if not proof.get('ui_callback_main_thread'):raise RuntimeError('后台任务未在主线程完成：'+str(proof)+'; busy='+str(window.busy)+'; log='+window.log.toPlainText())
        if '--exercise-workstation' in sys.argv and (not proof.get('calibration_restored') or not proof.get('auto_dedup_verified') or proof.get('integration_error')):raise RuntimeError('工站集成测试失败：'+str(proof))
        (directory/'validation.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8');json_output(proof);return 0
    directory=Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation));directory.mkdir(parents=True,exist_ok=True)
    lock=QLockFile(str(directory/'workstation.lock'))
    if not lock.tryLock(100):
        QMessageBox.information(None,'Label Desk','本地生产工站已在运行，请使用已有窗口。');return 0
    window=Window();window.show();result=app.exec();lock.unlock();return result

if __name__=='__main__':
    try:
        if worker_entry():sys.exit(0)
        if not any(flag in sys.argv for flag in ('--local','--smoke-test','--production-once')):
            from studio_ui import main as agent_main
            sys.exit(agent_main())
        sys.exit(gui_main())
    except Exception as e:
        json_output({'ok':False,'error':str(e)});sys.exit(1)
