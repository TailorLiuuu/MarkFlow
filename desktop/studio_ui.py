"""Native MarkFlow desktop studio, with a local-first production workflow."""
import json,os,sys,threading
from pathlib import Path
from PySide6.QtCore import Qt,QObject,Signal,QStandardPaths,QLockFile,QTimer,QSize
from PySide6.QtGui import QIcon,QPixmap,QColor,QPainter,QFont
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QFrame,QLabel,QPushButton,QVBoxLayout,QHBoxLayout,QListWidget,QListWidgetItem,QFileDialog,QDialog,QFormLayout,QComboBox,QDoubleSpinBox,QSpinBox,QDialogButtonBox,QTableWidget,QTableWidgetItem,QHeaderView,QTabWidget,QPlainTextEdit,QSystemTrayIcon,QMenu,QMessageBox)
from studio_engine import StudioEngine
from agent_runtime import VERSION
BASE=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))
QSS='''
QWidget{font-family:"Segoe UI","Microsoft YaHei UI";font-size:13px;color:#1c2940;background:#f4f6fa;}
QMainWindow{background:#f4f6fa} QFrame#sidebar{background:#fff;border-right:1px solid #e3e8ef} QFrame#card{background:white;border:1px solid #e3e8ef;border-radius:12px}
QLabel{background:transparent} QLabel#brand{font-size:23px;font-weight:700;color:#142642} QLabel#muted{color:#7c899c;font-size:12px} QLabel#heading{font-size:24px;font-weight:700} QLabel#section{font-size:14px;font-weight:600} QLabel#state{background:#e8f0ff;color:#1f58c7;border-radius:12px;padding:7px 14px;font-weight:600} QLabel#error{color:#b74d3a}
QPushButton{background:#fff;border:1px solid #dbe2ed;border-radius:7px;padding:10px 17px;font-weight:600} QPushButton:hover{background:#edf3ff;border-color:#a9c3f3} QPushButton:disabled{background:#f1f3f7;color:#a1aabb;border-color:#e4e8ef} QPushButton#primary{background:#2864e8;color:white;border-color:#2864e8} QPushButton#primary:hover{background:#1b52d0} QPushButton#stop{color:#a54335;background:#fff8f6;border-color:#edcfc8} QPushButton#quiet{border:none;background:transparent;color:#64738b;padding:6px}
QListWidget{background:white;border:none;outline:none;padding:0px} QListWidget::item{padding:13px 11px;margin:3px 8px;border-radius:8px;color:#56647b} QListWidget::item:selected{background:#eaf1ff;color:#2258c4} QListWidget::item:hover{background:#f2f6fc}
QPlainTextEdit,QTableWidget{background:white;border:0;gridline-color:#edf0f5;selection-background-color:#edf3ff;selection-color:#234b8c} QHeaderView::section{background:#f7f9fc;border:none;padding:9px;color:#728099;font-size:12px} QTableWidget::item{padding:8px}
QTabWidget::pane{border:1px solid #e3e8ef;background:white;border-radius:10px} QTabBar::tab{background:transparent;padding:10px 18px;color:#7c899c} QTabBar::tab:selected{color:#235ed9;font-weight:600;border-bottom:2px solid #2864e8}
QComboBox,QSpinBox,QDoubleSpinBox{background:white;border:1px solid #dbe2ed;border-radius:5px;padding:8px;min-height:18px} QDialog{background:#f7f9fc}
'''
def label(text,kind=None):
    w=QLabel(text)
    if kind:w.setObjectName(kind)
    return w

def card():
    w=QFrame();w.setObjectName('card');return w

class Preview(QWidget):
    def __init__(self):super().__init__();self.pix=QPixmap();self.setMinimumHeight(220)
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#eef2f8'));p.setPen(QColor('#dce3ed'))
        for x in range(12,self.width(),20):
            for y in range(12,self.height(),20):p.drawPoint(x,y)
        if not self.pix.isNull():
            size=self.pix.size();size.scale(max(1,self.width()-100),max(1,self.height()-65),Qt.AspectRatioMode.KeepAspectRatio)
            x=(self.width()-size.width())//2;y=(self.height()-size.height())//2
            p.fillRect(x+4,y+7,size.width(),size.height(),QColor('#d9e0ea'));p.fillRect(x-1,y-1,size.width()+2,size.height()+2,Qt.GlobalColor.white)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,False);p.drawPixmap(x,y,size.width(),size.height(),self.pix)
        else:p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'导入模板后显示预览')
        p.end()
    def load(self,path):self.pix=QPixmap(path);self.update()

class Window(QMainWindow):
    def __init__(self,engine):
        super().__init__();self.engine=engine;self.state={};self.items_key=None;self.preview_key=None;self.rows_key=None
        self.setWindowTitle('印序 MarkFlow');self.setWindowIcon(QIcon(str(BASE/'assets/markflow.ico')));self.resize(1180,790);self.setMinimumSize(960,680)
        root=QWidget();self.setCentralWidget(root);outer=QHBoxLayout(root);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        side=QFrame();side.setObjectName('sidebar');side.setFixedWidth(262);sl=QVBoxLayout(side);sl.setContentsMargins(16,26,16,20);sl.setSpacing(15)
        brand=QHBoxLayout();icon=label('');icon.setPixmap(QPixmap(str(BASE/'assets/markflow.png')).scaled(45,45,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation));brand.addWidget(icon)
        bl=QVBoxLayout();bl.addWidget(label('印序','brand'));bl.addWidget(label('MARKFLOW  /  标签工作室','muted'));brand.addLayout(bl);brand.addStretch();sl.addLayout(brand)
        sl.addSpacing(15);row=QHBoxLayout();row.addWidget(label('我的模板','section'));self.count=label('0','muted');row.addStretch();row.addWidget(self.count);sl.addLayout(row)
        imp=QPushButton('＋  导入模板');imp.setObjectName('primary');imp.clicked.connect(self.import_template);sl.addWidget(imp)
        self.templates=QListWidget();self.templates.currentItemChanged.connect(self.select_template);sl.addWidget(self.templates,1)
        self.connection=label('○  管理端连接中','muted');sl.addWidget(self.connection)
        self.pairing=label('');self.pairing.setWordWrap(True);self.pairing.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);sl.addWidget(self.pairing)
        sl.addWidget(label('本地导入 · 云端接收\nWindows 工站  '+VERSION,'muted'));outer.addWidget(side)
        body=QWidget();bodyl=QVBoxLayout(body);bodyl.setContentsMargins(26,22,26,18);bodyl.setSpacing(14);outer.addWidget(body,1)
        h=QHBoxLayout();head=QVBoxLayout();head.addWidget(label('标签生产','heading'));head.addWidget(label('选择一个模板，让每台设备都有自己的标识。','muted'));h.addLayout(head);h.addStretch();self.badge=label('就绪','state');h.addWidget(self.badge);bodyl.addLayout(h)
        summary=QHBoxLayout();self.printer_card=self.stat('打印机','尚未选择');self.device_card=self.stat('设备','等待连接 ESP32');self.result_card=self.stat('最近设备','—')
        for w in (self.printer_card,self.device_card,self.result_card):summary.addWidget(w)
        bodyl.addLayout(summary)
        previewcard=card();pl=QVBoxLayout(previewcard);pl.setContentsMargins(18,15,18,12);pr=QHBoxLayout();self.title=label('选择模板','section');pr.addWidget(self.title);pr.addStretch();self.size_label=label('','muted');pr.addWidget(self.size_label);pl.addLayout(pr)
        self.preview=Preview();pl.addWidget(self.preview,1);self.preview_note=label('预览示例 · 实际打印时自动读取设备 MAC','muted');self.preview_note.setAlignment(Qt.AlignmentFlag.AlignCenter);pl.addWidget(self.preview_note);bodyl.addWidget(previewcard,1)
        self.tabs=QTabWidget();self.tabs.setMaximumHeight(150);self.tabs.setMinimumHeight(120)
        self.records=QTableWidget(0,3);self.records.setHorizontalHeaderLabels(['时间','设备 MAC','结果']);self.records.verticalHeader().hide();self.records.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents);self.records.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.ResizeToContents);self.records.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch);self.records.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.logs=QPlainTextEdit();self.logs.setReadOnly(True);self.tabs.addTab(self.records,'最近记录');self.tabs.addTab(self.logs,'运行消息');bodyl.addWidget(self.tabs)
        controls=card();cl=QHBoxLayout(controls);cl.setContentsMargins(16,14,16,14);statusbox=QVBoxLayout();self.status=label('准备就绪','section');self.status.setWordWrap(True);self.error=label('','error');self.error.setWordWrap(True);statusbox.addWidget(self.status);statusbox.addWidget(self.error);cl.addLayout(statusbox,1)
        settings=QPushButton('打印设置');settings.clicked.connect(self.settings);cl.addWidget(settings)
        self.start=QPushButton('▶  开始');self.start.setObjectName('primary');self.start.setMinimumWidth(105);self.start.clicked.connect(lambda:engine.enqueue('start'));cl.addWidget(self.start)
        self.stop=QPushButton('■  停止');self.stop.setObjectName('stop');self.stop.clicked.connect(engine.halt);cl.addWidget(self.stop);bodyl.addWidget(controls)
        footer=QHBoxLayout();footer.addWidget(label('生产自动去重 · 测试打印单独记录','muted'));footer.addStretch();self.allow=QPushButton('允许远程测试');self.allow.setObjectName('quiet');self.allow.clicked.connect(lambda:engine.enqueue('allow-remote'));footer.addWidget(self.allow);bodyl.addLayout(footer)
    def stat(self,title,value):
        f=card();l=QVBoxLayout(f);l.setContentsMargins(16,12,16,12);l.addWidget(label(title,'muted'));f.value=label(value,'section');f.value.setWordWrap(True);l.addWidget(f.value);return f
    def import_template(self):
        path,_=QFileDialog.getOpenFileName(self,'导入模板或模板列表','','印序模板 (*.labeljob *.markflow *.json)')
        if path:self.engine.enqueue('import',path=path)
    def select_template(self,current,old):
        if current and current.data(Qt.ItemDataRole.UserRole)!=self.state.get('selected'):self.engine.enqueue('select',id=current.data(Qt.ItemDataRole.UserRole))
    def settings(self):
        d=QDialog(self);d.setWindowTitle('打印设置');d.setMinimumWidth(420);layout=QVBoxLayout(d);layout.addWidget(label('每台打印机独立保存，导入模板不会覆盖校准。','muted'));form=QFormLayout();cfg=dict(self.state.get('config',{}));widgets={}
        for key,title,items in [('name','打印机',[(s,s) for s in self.state.get('printers',[])]),('dpi','分辨率',[(str(s)+' dpi',s) for s in (203,300,600)]),('paper_mode','纸张类型',[('间隙 / 黑线纸','gap'),('连续纸','continuous'),('沿用驱动','driver')])]:
            w=QComboBox()
            for t,v in items:w.addItem(t,v)
            w.setCurrentIndex(max(0,w.findData(cfg.get(key))));widgets[key]=w;form.addRow(title,w)
        for key,title in [('offset_x_mm','左右微调 mm（＋向右）'),('offset_y_mm','上下微调 mm（＋向下）')]:
            w=QDoubleSpinBox();w.setRange(-10,10);w.setDecimals(2);w.setSingleStep(.1);w.setValue(cfg.get(key,0));widgets[key]=w;form.addRow(title,w)
        for key,title in [('vid','USB VID（十进制）'),('pid','USB PID（十进制）')]:
            w=QSpinBox();w.setRange(0,65535);w.setValue(cfg.get(key,0));widgets[key]=w;form.addRow(title,w)
        layout.addLayout(form);buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(d.accept);buttons.rejected.connect(d.reject);layout.addWidget(buttons)
        if d.exec():
            result={k:w.currentData() if isinstance(w,QComboBox) else w.value() for k,w in widgets.items()};self.engine.enqueue('config',config=result)
    def changed(self,s):
        self.state=s;key=json.dumps(s['templates'],ensure_ascii=False)
        if key!=self.items_key:
            self.templates.blockSignals(True);self.templates.clear()
            for item in s['templates']:
                w=QListWidgetItem(item['name']+'\n'+str(item['width'])+' × '+str(item['height'])+' mm  ·  '+item['origin']);w.setData(Qt.ItemDataRole.UserRole,item['id']);w.setSizeHint(QSize(205,68));self.templates.addItem(w)
            self.templates.blockSignals(False);self.items_key=key
        for i in range(self.templates.count()):
            if self.templates.item(i).data(Qt.ItemDataRole.UserRole)==s['selected']:
                self.templates.blockSignals(True);self.templates.setCurrentRow(i);self.templates.blockSignals(False)
        self.count.setText(str(len(s['templates'])));self.title.setText(s['assignment'] or '选择模板');item=next((v for v in s['templates'] if v['id']==s['selected']),{})
        self.size_label.setText(f"{item.get('width','—')} × {item.get('height','—')} mm")
        self.connection.setText('●  管理端已连接' if s['online'] else '○  本地模式 · 管理端未连接');self.pairing.setText('配对码  '+s['pair_code'] if s.get('pair_code') and not s['bound'] else '电脑  '+s['name'])
        self.badge.setText('正在执行' if s['busy'] else '管理端已暂停' if s['paused'] else '生产运行中' if s['running'] else '已停止' if s['local_pause'] else '准备就绪')
        self.printer_card.value.setText(s['config'].get('name') or '尚未选择');ports=[p['port'] for p in s['ports'] if p['vid']==s['config']['vid'] and p['pid']==s['config']['pid']];self.device_card.value.setText(' · '.join(ports) if ports else '等待连接 ESP32');self.result_card.value.setText(s['last_mac'] or '—')
        self.status.setText(s['message']);self.error.setText(s['error']);self.error.setVisible(bool(s['error']));self.start.setEnabled(not s['running'] and not s['busy'] and bool(s['selected']));self.stop.setEnabled(s['running'] or s['busy'] or not s['local_pause']);self.allow.setVisible(s['local_pause']);self.templates.setEnabled(not s['busy'])
        self.preview_note.setText('最近设备预览 · '+s['last_mac'] if s['last_mac'] else '预览示例 · 实际打印时自动读取设备 MAC')
        path=s['preview'];pk=(path,Path(path).stat().st_mtime_ns if path and Path(path).exists() else 0)
        if pk!=self.preview_key:self.preview.load(path);self.preview_key=pk
        rows=json.dumps(s['recent'])
        if rows!=self.rows_key:
            self.records.setRowCount(len(s['recent']))
            for i,r in enumerate(s['recent']):
                for col,val in enumerate([r['created'][11:],r['mac'],r['message']]):self.records.setItem(i,col,QTableWidgetItem(val))
            self.rows_key=rows
        logs='\n'.join(e['time']+'  '+e['text'] for e in s['events'])
        if self.logs.toPlainText()!=logs:self.logs.setPlainText(logs)

def main():
    app=QApplication.instance() or QApplication(sys.argv);app.setOrganizationName('InfinLab');app.setApplicationName('LabelDesk');app.setStyleSheet(QSS)
    if sys.platform=='win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('InfinLab.MarkFlow')
    directory=Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation));directory.mkdir(parents=True,exist_ok=True);lock=QLockFile(str(directory/'workstation.lock'))
    if not lock.tryLock(100):QMessageBox.information(None,'印序 MarkFlow','印序已在运行，请从系统托盘打开。');return 0
    class Bridge(QObject):changed=Signal(dict)
    bridge=Bridge();engine=StudioEngine(directory,bridge.changed.emit);window=Window(engine);bridge.changed.connect(window.changed);window.changed(engine.snapshot())
    tray=QSystemTrayIcon(QIcon(str(BASE/'assets/markflow.ico')),window);tray.setToolTip('印序 MarkFlow');menu=QMenu();menu.addAction('显示印序',window.show);menu.addAction('停止生产',engine.halt);menu.addAction('退出',app.quit);tray.setContextMenu(menu);tray.activated.connect(lambda _:window.show());tray.show();app.setQuitOnLastWindowClosed(False)
    if sys.platform=='win32' and getattr(sys,'frozen',False):
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:winreg.SetValueEx(key,'InfinLabLabelDesk',0,winreg.REG_SZ,'"'+sys.executable+'" --tray')
    worker=threading.Thread(target=engine.run,daemon=True);worker.start()
    if '--tray' not in sys.argv:window.show()
    if '--capture-ui' in sys.argv:
        target=Path(sys.argv[sys.argv.index('--capture-ui')+1]);target.parent.mkdir(parents=True,exist_ok=True);QTimer.singleShot(7000,lambda:window.grab().save(str(target)))
    result=app.exec();engine.running=False;engine.local_pause=True;engine.stop.set();worker.join(timeout=15);lock.unlock();return result
