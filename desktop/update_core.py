"""GitHub-only update transport, package validation, and directory transaction."""
import hashlib
import json
import os
import re
import shutil
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = 'TailorLiuuu/MarkFlow'
API = 'https://api.github.com/repos/' + REPOSITORY + '/releases/latest'
WEB = 'https://github.com/' + REPOSITORY
ASSET = 'MarkFlow-Windows-x64.zip'
MAX_ARCHIVE = 180 * 1024 * 1024
MAX_EXPANDED = 700 * 1024 * 1024


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value):
        raise ValueError('版本号格式无效')
    return tuple(map(int, value.lstrip('v').split('.')))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)


def safe_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443):
        raise ValueError('更新地址不安全')
    if p.hostname not in {'api.github.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}:
        raise ValueError('更新地址不属于 GitHub')
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url):
    safe_url(url)
    request = urllib.request.Request(url, headers={'User-Agent': 'MarkFlow-Updater', 'Accept': 'application/vnd.github+json' if url == API else 'application/octet-stream'})
    # No machine credentials, cookies or GitHub account tokens are used.
    return urllib.request.build_opener(SafeRedirect()).open(request, timeout=20)


def read_small(url, limit):
    with open_url(url) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('更新信息超过大小限制')
    return data


def parse_release(data, current):
    if data.get('draft') is not False or data.get('prerelease') is not False:
        raise ValueError('仅支持正式发布版本')
    tag = data.get('tag_name', '')
    version = version_tuple(tag)
    if version <= version_tuple(current):
        return None
    assets = data.get('assets', [])
    selected = {}
    for name in (ASSET, 'SHA256SUMS'):
        matches = [a for a in assets if a.get('name') == name and a.get('state') == 'uploaded']
        if len(matches) != 1:
            raise ValueError('此版本缺少完整的 Windows 更新文件')
        a = matches[0]
        expected = WEB + '/releases/download/' + tag + '/' + name
        if a.get('browser_download_url') != expected:
            raise ValueError('更新文件来自其他仓库或版本')
        if type(a.get('size')) is not int or not 0 < a['size'] <= (MAX_ARCHIVE if name == ASSET else 16384):
            raise ValueError('更新文件大小无效')
        selected[name] = a
    digest = selected[ASSET].get('digest')
    if digest is not None and not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('GitHub 文件校验值无效')
    return {'version': tag.lstrip('v'), 'url': selected[ASSET]['browser_download_url'], 'size': selected[ASSET]['size'], 'digest': digest, 'checksums': selected['SHA256SUMS']['browser_download_url'], 'notes': str(data.get('body') or '')[:6000]}


def check_latest(current):
    return parse_release(json.loads(read_small(API, 1024 * 1024)), current)


def checksum_for(text):
    matches = []
    for line in text.splitlines():
        m = re.fullmatch(r'([0-9a-fA-F]{64})\s+\*?' + re.escape(ASSET), line.strip())
        if m:
            matches.append(m[1].lower())
    if len(matches) != 1:
        raise ValueError('无法验证更新包的 SHA256')
    return matches[0]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def download_release(release, directory, progress, cancel):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    expected = checksum_for(read_small(release['checksums'], 16384).decode('ascii'))
    if release.get('digest') and release['digest'] != 'sha256:' + expected:
        raise ValueError('GitHub 与发布文件的校验值不一致')
    partial = directory / 'download.part'
    deadline = time.monotonic() + 600
    total = 0
    try:
        with open_url(release['url']) as response, partial.open('wb') as f:
            while True:
                if cancel.is_set():
                    raise ValueError('已取消下载')
                if time.monotonic() > deadline:
                    raise ValueError('下载超时，请重试')
                block = response.read(128 * 1024)
                if not block:
                    break
                total += len(block)
                if total > release['size'] or total > MAX_ARCHIVE:
                    raise ValueError('下载大小与发布记录不一致')
                f.write(block)
                progress(total, release['size'])
        if total != release['size'] or sha256(partial) != expected:
            raise ValueError('下载包不完整或 SHA256 校验失败，请重试')
        result = directory / ASSET
        partial.replace(result)
        return result, expected
    finally:
        partial.unlink(missing_ok=True)


def package_entries(z):
    entries = []
    seen = set()
    expanded = 0
    reserved = re.compile(r'(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)')
    roots = {'MarkFlow.exe', 'LabelDeskWorker.exe', 'MarkFlowUpdater.exe', 'README.md', 'NOTICE.md', 'version.json'}
    private = {'agent-identity.json', 'agent-status.json', 'printer-settings.json', 'calibration.json', 'studio-state.json', 'active.labeljob'}
    for info in z.infolist():
        name = info.filename
        parts = name.rstrip('/').split('/')
        if '\\' in name or len(parts) < 2 or parts[0] != 'MarkFlow' or any(not p or p in {'.', '..'} or ':' in p or p.rstrip(' .') != p or reserved.match(p) for p in parts):
            raise ValueError('更新包包含不安全路径')
        relative = PurePosixPath(*parts[1:])
        if len(parts) == 2 and not info.is_dir() and parts[1] not in roots:
            raise ValueError('更新包包含非程序文件')
        if len(parts) > 2 and parts[1] not in {'_internal', 'docs'}:
            raise ValueError('更新包包含非程序目录')
        key = str(relative).casefold()
        if key in seen or stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError('更新包包含重复路径或符号链接')
        seen.add(key)
        if any(p.casefold() in private or p.casefold().startswith('agent-') for p in parts) or relative.suffix in {'.db', '.sqlite', '.csv', '.log'}:
            raise ValueError('更新包不得覆盖用户数据')
        expanded += info.file_size
        if expanded > MAX_EXPANDED or len(seen) > 12000:
            raise ValueError('更新包展开后过大')
        entries.append((info, relative))
    if not {'markflow.exe', 'labeldeskworker.exe', 'markflowupdater.exe', 'version.json'}.issubset(seen):
        raise ValueError('更新包缺少程序文件')
    return entries


def prepare_package(archive, expected_hash, version, app_dir, data_dir):
    app_dir = Path(app_dir).resolve()
    data_dir = Path(data_dir).resolve()
    if data_dir == app_dir or data_dir.is_relative_to(app_dir) or app_dir.is_relative_to(data_dir):
        raise ValueError('程序目录与用户数据目录必须分开')
    if not (app_dir / 'MarkFlow.exe').is_file():
        raise ValueError('找不到当前程序目录')
    if sha256(archive) != expected_hash:
        raise ValueError('更新包校验失败')
    stage = app_dir.parent / ('.markflow-update-' + str(uuid.uuid4()))
    stage.mkdir()
    try:
        with zipfile.ZipFile(archive) as z:
            entries = package_entries(z)
            metadata = json.loads(z.read('MarkFlow/version.json'))
            if metadata != {'product': 'MarkFlow', 'version': version, 'platform': 'windows-x64'}:
                raise ValueError('包内版本或平台不匹配')
            for info, relative in entries:
                target = stage / relative
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as source, target.open('wb') as dest:
                        shutil.copyfileobj(source, dest, 1024 * 1024)
        return stage
    except Exception:
        shutil.rmtree(stage)
        raise


def tree_digest(root):
    return {str(p.relative_to(root)): sha256(p) for p in Path(root).rglob('*') if p.is_file()}


def replace_transaction(app, stage, backup, launch_and_verify, restore_launch, record):
    """No user-data paths are accepted by this transaction."""
    app, stage, backup = (Path(p).resolve() for p in (app, stage, backup))
    if app.parent != stage.parent or app.parent != backup.parent or backup.exists():
        raise ValueError('更新目录布局无效')
    record('prepared')
    moved = False
    try:
        app.rename(backup)
        moved = True
        record('backed_up')
        stage.rename(app)
        record('installed')
        if not launch_and_verify(app):
            raise RuntimeError('新版启动未通过检查')
        record('success')
    except Exception:
        if moved:
            if app.exists():
                failed = stage.with_name(stage.name + '-failed')
                app.rename(failed)
            backup.rename(app)
            record('rolled_back')
            restore_launch(app)
        raise
