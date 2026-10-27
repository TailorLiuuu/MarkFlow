"""Separate Windows helper: wait for the client, replace program files, verify startup."""
import ctypes
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from update_core import atomic_json, replace_transaction, sha256, tree_digest, version_tuple


def wait_for_process(pid, seconds=120):
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return
        raise RuntimeError('无法等待当前程序退出')
    try:
        if kernel.WaitForSingleObject(handle, seconds * 1000) != 0:
            raise RuntimeError('当前程序未退出，未执行更新')
    finally:
        kernel.CloseHandle(handle)


def validate_plan(path):
    path = Path(path).resolve()
    data = Path(os.environ['LOCALAPPDATA']).resolve() / 'InfinLab' / 'LabelDesk'
    if path.name != 'plan.json' or path.parent.parent != data / 'updates':
        raise ValueError('更新计划位置无效')
    plan = json.loads(path.read_text(encoding='utf-8'))
    uuid.UUID(plan['nonce'])
    version_tuple(plan['version'])
    app, stage, backup = (Path(plan[k]).resolve() for k in ('app', 'stage', 'backup'))
    if data == app or data.is_relative_to(app) or app.is_relative_to(data):
        raise ValueError('不能更新用户数据目录')
    if stage.parent != app.parent or backup.parent != app.parent or not stage.name.startswith('.markflow-update-') or not backup.name.startswith('.markflow-backup-'):
        raise ValueError('更新计划路径无效')
    if path.is_relative_to(app) or path.is_relative_to(stage):
        raise ValueError('更新计划必须位于用户数据目录')
    if sha256(app / 'MarkFlow.exe') != plan['current_exe_sha256']:
        raise ValueError('当前程序已变化，请重新检查更新')
    if tree_digest(stage) != plan['files']:
        raise ValueError('暂存的更新文件已变化')
    if json.loads((stage / 'version.json').read_text())['version'] != plan['version']:
        raise ValueError('暂存版本不匹配')
    return plan, app, stage, backup, data


def run(path):
    plan, app, stage, backup, data = validate_plan(path)
    folder = Path(path).resolve().parent
    health = folder / 'health.json'
    status = folder / 'status.json'
    health.unlink(missing_ok=True)
    child = None
    def record(state):
        value = {'state': state, 'version': plan['version'], 'time': int(time.time())}
        atomic_json(status, value)
        if state in {'success', 'rolled_back', 'failed'}:
            atomic_json(data / 'updates' / 'last-result.json', value)
    def launch(root, check=False):
        args = [str(root / 'MarkFlow.exe')]
        if check:
            args += ['--update-health', str(health), '--update-nonce', plan['nonce']]
        return subprocess.Popen(args, cwd=str(root), close_fds=True)
    def verify(root):
        nonlocal child
        child = launch(root, True)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and child.poll() is None:
            try:
                value = json.loads(health.read_text(encoding='utf-8'))
                if value == {'version': plan['version'], 'nonce': plan['nonce']}:
                    return True
            except (OSError, ValueError):
                pass
            time.sleep(.3)
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=15)
        return False
    try:
        record('waiting')
        wait_for_process(int(plan['parent_pid']))
        # Verify again after the old process exits, before touching the program directory.
        validate_plan(path)
        replace_transaction(app, stage, backup, verify, launch, record)
    except Exception:
        # No network URL, token or user path is written to error messages.
        if not status.exists() or json.loads(status.read_text())['state'] != 'rolled_back':
            record('failed')
        raise


if __name__ == '__main__':
    try:
        if sys.platform != 'win32' or len(sys.argv) != 3 or sys.argv[1] != '--plan':
            raise ValueError('Only a Windows update plan is supported')
        run(sys.argv[2])
    except Exception:
        sys.exit(1)
