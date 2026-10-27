"""Non-blocking release checks and explicit user-controlled installation."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QTimer, Qt
from PySide6.QtWidgets import QPushButton, QLabel, QProgressBar, QMessageBox, QApplication
from agent_runtime import VERSION
from update_core import check_latest, download_release, prepare_package, sha256, tree_digest, atomic_json


class Updates(QObject):
    result = Signal(str, object)
    progress = Signal(int, int)
    available = Signal(str)

    def __init__(self, window, engine, layout, automatic=True):
        super().__init__(window)
        self.window = window
        self.engine = engine
        self.release = None
        self.phase = 'idle'
        self.manual = False
        self.notice_version = None
        self.cancel = threading.Event()
        self.directory = None
        self.stage = None
        self.install_requested = False
        self.button = QPushButton('检查更新')
        self.button.clicked.connect(lambda: self.check(True))
        self.info = QLabel('当前版本 ' + VERSION)
        self.info.setWordWrap(True)
        self.info.setObjectName('muted')
        self.download = QPushButton('下载并更新')
        self.download.setObjectName('primary')
        self.download.clicked.connect(self.begin_download)
        self.download.hide()
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.hide()
        self.cancel_button = QPushButton('取消下载')
        self.cancel_button.clicked.connect(self.cancel.set)
        self.cancel_button.hide()
        for widget in (self.button, self.info, self.download, self.bar, self.cancel_button):
            layout.addWidget(widget)
        self.result.connect(self.finished)
        self.progress.connect(lambda n, total: self.bar.setValue(n * 100 // max(total, 1)))
        self.timer = QTimer(self)
        self.timer.setInterval(6 * 60 * 60 * 1000)
        self.timer.timeout.connect(lambda: self.check(False))
        if automatic:
            self.timer.start()
            QTimer.singleShot(6000, lambda: self.check(False))
        QApplication.instance().aboutToQuit.connect(self.cancel.set)
        try:
            last = json.loads((engine.dir / 'updates/last-result.json').read_text(encoding='utf-8'))
            if last['state'] in {'rolled_back', 'failed'}:
                self.info.setText('上次更新未完成，原有数据保留；可重新检查更新。')
        except (OSError, ValueError, KeyError):
            pass

    def task(self, name, action):
        def work():
            try:
                self.result.emit(name, action())
            except Exception as e:
                # HTTP failures can contain signed redirect URLs; don't display raw exceptions.
                message = str(e) if isinstance(e, ValueError) else '网络或文件操作失败，请检查网络及目录权限后重试。'
                self.result.emit('error', message[:200])
        threading.Thread(target=work, daemon=True).start()

    def check(self, manual):
        if self.phase != 'idle':
            return
        self.manual = manual
        self.phase = 'checking'
        self.button.setEnabled(False)
        self.info.setText('正在检查 GitHub 正式版本…')
        self.task('checked', lambda: check_latest(VERSION))

    def begin_download(self):
        if self.phase != 'idle' or not self.release:
            return
        if self.engine.running or self.engine.busy or self.engine.start_pending:
            self.info.setText('请先停止生产，再下载并更新。')
            return
        if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
            self.info.setText('自动安装仅支持打包后的 Windows 软件。')
            return
        answer = QMessageBox.question(self.window, '更新印序', '下载并安装 '+self.release['version']+'？\n更新完成后软件会重新启动，模板、绑定和微调设置都会保留。')
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.phase = 'downloading'
        self.button.setEnabled(False)
        self.download.setEnabled(False)
        self.cancel.clear()
        self.directory = self.engine.dir / 'updates' / str(uuid.uuid4())
        self.bar.setValue(0)
        self.bar.show()
        self.cancel_button.show()
        self.info.setText('正在从 GitHub 下载并校验…')
        app = Path(sys.executable).resolve().parent
        release = dict(self.release)
        def fetch():
            archive, digest = download_release(release, self.directory, self.progress.emit, self.cancel)
            if self.cancel.is_set():
                raise ValueError('已取消下载')
            stage = prepare_package(archive, digest, release['version'], app, self.engine.dir)
            if self.cancel.is_set():
                shutil.rmtree(stage)
                raise ValueError('已取消下载')
            return stage
        self.task('downloaded', fetch)

    def finished(self, kind, value):
        if kind == 'checked':
            self.phase = 'idle'
            self.button.setEnabled(True)
            self.release = value
            self.download.setVisible(bool(value))
            self.info.setText('发现新版 '+value['version'] if value else '已是最新正式版 '+VERSION)
            if value and self.notice_version != value['version']:
                self.notice_version = value['version']
                self.available.emit(value['version'])
        elif kind == 'downloaded':
            self.stage = value
            self.phase = 'ready'
            self.cancel_button.hide()
            self.info.setText('下载已校验，正在准备安全重启…')
            self.install_requested = True
            self.engine.enqueue('prepare-update')
        elif kind == 'error':
            self.phase = 'idle'
            self.button.setEnabled(True)
            self.download.setEnabled(True)
            self.cancel_button.hide()
            self.bar.hide()
            self.info.setText(value)

    def state_changed(self, state):
        if not self.install_requested:
            return
        if state.get('update_installing'):
            self.install_requested = False
            self.launch_helper()
        elif state.get('update_error'):
            self.install_requested = False
            if self.stage:
                shutil.rmtree(self.stage, ignore_errors=True)
            self.finished('error', state['update_error'])

    def launch_helper(self):
        app = Path(sys.executable).resolve().parent
        try:
            helper = self.directory / 'MarkFlowUpdater.exe'
            shutil.copy2(app / 'MarkFlowUpdater.exe', helper)
            plan = {'app': str(app), 'stage': str(self.stage), 'backup': str(app.parent / ('.markflow-backup-' + str(uuid.uuid4()))), 'parent_pid': os.getpid(), 'nonce': str(uuid.uuid4()), 'version': self.release['version'], 'current_exe_sha256': sha256(app / 'MarkFlow.exe'), 'files': tree_digest(self.stage)}
            path = self.directory / 'plan.json'
            atomic_json(path, plan)
            process = subprocess.Popen([str(helper), '--plan', str(path)], cwd=str(self.directory), creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
            self.phase = 'handoff'
            self.handoff = QTimer(self)
            deadline = time.monotonic() + 30
            def ready():
                try:
                    status = json.loads((self.directory / 'status.json').read_text(encoding='utf-8'))
                    if status['state'] == 'waiting' and status['version'] == self.release['version']:
                        self.handoff.stop();self.phase = 'installing';QApplication.instance().quit();return
                except (OSError, ValueError, KeyError):
                    pass
                if process.poll() is not None or time.monotonic() > deadline:
                    self.handoff.stop()
                    if process.poll() is None:process.terminate()
                    self.engine.enqueue('cancel-update')
                    self.finished('error', '更新程序未准备就绪，原有软件继续运行。')
            self.handoff.timeout.connect(ready)
            self.handoff.start(200)
        except Exception:
            self.engine.enqueue('cancel-update')
            self.finished('error', '无法启动更新程序，原有程序及数据未变动。')
