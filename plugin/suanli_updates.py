# -*- coding: utf-8 -*-
"""Explicit, unauthenticated GitHub release checks and verified ZIP downloads.

Nothing here reads plugin account settings, installs files, or runs an update.
The download remains a ZIP for the user to install after closing the host.
"""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.parse import urljoin, urlsplit
import zipfile

import requests


REPOSITORY = 'huangweif522-star/SuanliAI-Plugin'
PLUGIN_ID = 'SuanliAI-Plugin'
API_LATEST = 'https://api.github.com/repos/' + REPOSITORY + '/releases/latest'
RELEASE_BASE = 'https://github.com/' + REPOSITORY + '/releases/'
PLUGIN_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = Path.home() / 'Downloads' / PLUGIN_ID
MAX_API_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
MAX_ZIP_BYTES = 32 * 1024 * 1024
MAX_UNPACKED_BYTES = 64 * 1024 * 1024
MAX_ENTRY_BYTES = 16 * 1024 * 1024
MAX_ZIP_ENTRIES = 512
CHECK_DEADLINE = 35
DOWNLOAD_DEADLINE = 120
_CDN_HOSTS = frozenset(('release-assets.githubusercontent.com', 'objects.githubusercontent.com',
                        'github-releases.githubusercontent.com'))
_VERSION = re.compile(r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$')
_SHA256 = re.compile(r'^[0-9a-fA-F]{64}$')
_RESERVED_WINDOWS_NAME = re.compile(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', re.I)


class UpdateError(ValueError):
    """A fixed, user-presentable message without request/response secrets."""


def _version(value):
    if not isinstance(value, str) or len(value) > 32 or not _VERSION.fullmatch(value):
        raise UpdateError('更新版本号格式无效，请到项目发布页核对安装包')
    parts = tuple(int(part) for part in value.split('.'))
    if any(part > 999999 for part in parts):
        raise UpdateError('更新版本号超出支持范围')
    return parts


def _current_version():
    try:
        path = PLUGIN_DIR / 'info.json'
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError()
        info = json.loads(path.read_text(encoding='utf-8-sig'))
        version = info.get('version')
        _version(version)
        return version
    except (OSError, ValueError, TypeError, AttributeError):
        raise UpdateError('本地插件版本信息读取失败，请重新解压完整安装包') from None


def _safe_url(url):
    if (not isinstance(url, str) or len(url) > 8192
            or any(ord(char) < 33 or ord(char) == 127 for char in url)
            or '\\' in url):
        raise UpdateError('更新地址格式无效，已停止下载')
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.fragment or parsed.port not in (None, 443)):
            raise ValueError()
    except ValueError:
        raise UpdateError('更新仅支持 GitHub 官方 HTTPS 下载地址') from None
    return parsed


def _canonical_url(url, expected):
    parsed = _safe_url(url)
    if url != expected or parsed.query:
        raise UpdateError('更新地址与独立插件仓库不一致，已停止下载')
    return url


def _redirect_url(url, original):
    parsed = _safe_url(url)
    if url == original:
        return url
    if parsed.hostname not in _CDN_HOSTS:
        raise UpdateError('下载跳转到了非 GitHub 官方地址，已停止下载')
    return url


def _remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise UpdateError('更新请求超时，请稍后重试；原安装包已保留')
    return seconds


@contextmanager
def _session():
    # A fresh session never inherits the Suanli bearer token. Disable .netrc and
    # ambient HTTP authentication as well; public releases require no account.
    session = requests.Session()
    session.trust_env = False
    session.auth = None
    session.headers.clear()
    session.headers.update({'User-Agent': 'SuanliAI-Plugin-Updater',
                            'X-GitHub-Api-Version': '2026-03-10'})
    try:
        yield session
    finally:
        session.close()


def _content_chunks(response):
    raw = response.raw
    if callable(getattr(raw, 'read1', None)) and hasattr(raw, 'decode_content'):
        # read1 returns after one underlying read, so a slow trickle cannot keep
        # a large iter_content chunk waiting past our wall-clock deadline.
        while True:
            chunk = raw.read1(64 * 1024, decode_content=True)
            if not chunk:
                return
            yield chunk
    else:
        # Older urllib3 versions lack read1; returning each received byte also
        # permits deadline checks during a slow response. Package ZIPs are small.
        yield from response.iter_content(1)


def _receive(session, url, limit, deadline, destination=None, api=False):
    """Read a bounded public resource, manually checking every redirect host."""
    original = url
    if api:
        _canonical_url(url, API_LATEST)
    else:
        _safe_url(url)
    for _ in range(6):
        remaining = _remaining(deadline)
        try:
            response = session.get(url, headers={'Accept': 'application/vnd.github+json' if api else 'application/octet-stream'},
                                   timeout=(min(8, remaining), min(10, remaining)),
                                   stream=True, allow_redirects=False)
        except requests.Timeout:
            raise UpdateError('连接 GitHub 超时，请检查网络后重试') from None
        except requests.RequestException:
            raise UpdateError('无法连接 GitHub，请检查网络后重试') from None
        with response:
            status = response.status_code
            if status in (301, 302, 303, 307, 308):
                if api:
                    raise UpdateError('插件仓库地址发生跳转，请到项目发布页核对')
                target = response.headers.get('Location')
                if not target:
                    raise UpdateError('GitHub 下载跳转缺少目标地址，请稍后重试')
                url = _redirect_url(urljoin(url, target), original)
                continue
            if status in (403, 429):
                raise UpdateError('GitHub 暂时限制访问频率，请稍后重试')
            if status == 404:
                raise UpdateError('尚未找到正式发布版本或安装包，请稍后重试')
            if status != 200:
                raise UpdateError(f'GitHub 更新服务暂不可用（HTTP {status}），请稍后重试')
            length = response.headers.get('Content-Length', '')
            if str(length).isdigit() and int(length) > limit:
                raise UpdateError('更新文件超过大小限制，已停止下载')
            size = 0
            chunks = []
            digest = hashlib.sha256()
            try:
                for chunk in _content_chunks(response):
                    _remaining(deadline)
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > limit:
                        raise UpdateError('更新文件超过大小限制，已停止下载')
                    digest.update(chunk)
                    if destination is None:
                        chunks.append(chunk)
                    else:
                        destination.write(chunk)
            except requests.Timeout:
                raise UpdateError('GitHub 下载超时，原安装包已保留，请稍后重试') from None
            except requests.RequestException:
                raise UpdateError('GitHub 下载中断，原安装包已保留，请稍后重试') from None
            if size == 0:
                raise UpdateError('GitHub 返回了空文件，请稍后重试')
            return b''.join(chunks) if destination is None else (size, digest.hexdigest())
    raise UpdateError('GitHub 下载跳转次数过多，已停止下载')


def _json(raw, label):
    try:
        value = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeError, ValueError):
        raise UpdateError(label + '格式无效，请稍后重试') from None
    if not isinstance(value, dict):
        raise UpdateError(label + '格式无效，请稍后重试')
    return value


def _asset(release, name, expected_url):
    assets = release.get('assets')
    if not isinstance(assets, list) or len(assets) > 1000:
        raise UpdateError('发布文件清单格式无效，请到项目发布页核对')
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get('name') == name]
    if len(matches) != 1:
        raise UpdateError('正式发布缺少唯一的 ' + name + ' 文件，请稍后重试')
    asset = matches[0]
    _canonical_url(asset.get('browser_download_url'), expected_url)
    if asset.get('state', 'uploaded') != 'uploaded':
        raise UpdateError('发布文件尚未上传完成，请稍后重试')
    return asset


def _asset_digest(asset):
    value = asset.get('digest')
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith('sha256:') or not _SHA256.fullmatch(value[7:]):
        raise UpdateError('GitHub 文件校验信息格式无效')
    return value[7:].lower()


def _check(session, current_version, deadline):
    release = _json(_receive(session, API_LATEST, MAX_API_BYTES, deadline, api=True), 'GitHub 发布信息')
    if release.get('draft') is not False or release.get('prerelease') is not False:
        raise UpdateError('更新源未提供正式稳定版本')
    tag = release.get('tag_name')
    if not isinstance(tag, str) or not tag.startswith('v'):
        raise UpdateError('正式发布标签格式无效')
    latest_version = tag[1:]
    latest_parts = _version(latest_version)
    release_url = RELEASE_BASE + 'tag/' + tag
    _canonical_url(release.get('html_url'), release_url)
    filename = 'Suanli_Ai_v' + latest_version + '.zip'
    download_url = RELEASE_BASE + 'download/' + tag + '/' + filename
    manifest_url = RELEASE_BASE + 'download/' + tag + '/latest.json'
    manifest_asset = _asset(release, 'latest.json', manifest_url)
    zip_asset = _asset(release, filename, download_url)
    for asset, limit in ((manifest_asset, MAX_MANIFEST_BYTES), (zip_asset, MAX_ZIP_BYTES)):
        size = asset.get('size')
        if isinstance(size, int) and (size <= 0 or size > limit):
            raise UpdateError('GitHub 发布文件大小异常，已停止更新')
    manifest_raw = _receive(session, manifest_url, MAX_MANIFEST_BYTES, deadline)
    manifest_digest = _asset_digest(manifest_asset)
    if manifest_digest and hashlib.sha256(manifest_raw).hexdigest() != manifest_digest:
        raise UpdateError('更新清单校验失败，已停止更新')
    manifest = _json(manifest_raw, '更新清单')
    if manifest.get('plugin') != PLUGIN_ID or manifest.get('version') != latest_version:
        raise UpdateError('更新清单与插件或发布版本不一致，已停止更新')
    _canonical_url(manifest.get('download_url'), download_url)
    digest = manifest.get('sha256')
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise UpdateError('更新清单缺少有效 SHA256，已停止更新')
    digest = digest.lower()
    zip_digest = _asset_digest(zip_asset)
    if zip_digest and digest != zip_digest:
        raise UpdateError('更新清单与 GitHub 安装包校验值不一致，已停止更新')
    notes = manifest.get('notes', release.get('body') or '')
    if not isinstance(notes, str) or len(notes) > 16000:
        raise UpdateError('更新说明格式无效')
    published_at = release.get('published_at') or ''
    if not isinstance(published_at, str) or len(published_at) > 80:
        raise UpdateError('发布时间格式无效')
    return {'ok': True, 'current_version': current_version, 'latest_version': latest_version,
            'has_update': latest_parts > _version(current_version), 'download_url': download_url,
            'release_url': release_url, 'notes': notes, 'published_at': published_at,
            '_sha256': digest, '_filename': filename}


def check_plugin_update():
    """User-triggered only. The Suanli account token is not an input."""
    try:
        current_version = _current_version()
        with _session() as session:
            result = _check(session, current_version, time.monotonic() + CHECK_DEADLINE)
        return {key: value for key, value in result.items() if not key.startswith('_')}
    except UpdateError as exc:
        return {'ok': False, 'error': str(exc)}
    except Exception:
        return {'ok': False, 'error': '插件更新检查失败，请稍后重试'}


def _validate_zip(path, version, deadline):
    root = 'Suanli_Ai_v' + version
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ZIP_ENTRIES:
                raise UpdateError('安装包文件数量异常，已停止下载')
            total = 0
            seen = set()
            files = set()
            for info in infos:
                _remaining(deadline)
                name = info.orig_filename
                if ('\\' in name or '\x00' in name or name.startswith('/')
                        or any(ord(char) < 32 or ord(char) == 127 for char in name)):
                    raise UpdateError('安装包含有不安全的路径，已停止下载')
                parts = (name[:-1] if info.is_dir() else name).split('/')
                if (parts[0] != root or any(part in ('', '.', '..') or ':' in part
                        or part.endswith(('.', ' ')) or _RESERVED_WINDOWS_NAME.match(part) for part in parts)):
                    raise UpdateError('安装包目录不符合版本要求，已停止下载')
                if not info.is_dir() and len(parts) < 2:
                    raise UpdateError('安装包顶层必须为版本文件夹，已停止下载')
                folded = name.rstrip('/').casefold()
                if folded in seen:
                    raise UpdateError('安装包含有重复文件名，已停止下载')
                seen.add(folded)
                mode = stat.S_IFMT(info.external_attr >> 16)
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or info.flag_bits & 1:
                    raise UpdateError('安装包含有不支持的特殊或加密文件，已停止下载')
                if mode == stat.S_IFDIR and not info.is_dir():
                    raise UpdateError('安装包文件与目录类型不一致，已停止下载')
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise UpdateError('安装包压缩格式不受支持，已停止下载')
                total += info.file_size
                if info.file_size > MAX_ENTRY_BYTES or total > MAX_UNPACKED_BYTES:
                    raise UpdateError('安装包解压大小异常，已停止下载')
                if not info.is_dir():
                    files.add('/'.join(parts[1:]))
            folded_files = {item.casefold() for item in files}
            for filename in files:
                parts = filename.split('/')
                if any('/'.join(parts[:index]).casefold() in folded_files
                       for index in range(1, len(parts))):
                    raise UpdateError('安装包文件与目录路径冲突，已停止下载')
            if not {'main.py', 'info.json', 'ui/index.html'}.issubset(files):
                raise UpdateError('安装包缺少必要插件文件，已停止下载')
            info_entry = archive.getinfo(root + '/info.json')
            if info_entry.file_size > MAX_MANIFEST_BYTES:
                raise UpdateError('安装包版本信息过大，已停止下载')
            info = _json(archive.read(info_entry), '安装包版本信息')
            if info.get('version') != version:
                raise UpdateError('安装包内部版本与发布版本不一致，已停止下载')
            # Read each entry with a deadline and CRC validation, without extraction.
            for info in infos:
                if info.is_dir():
                    continue
                with archive.open(info) as source:
                    while source.read(64 * 1024):
                        _remaining(deadline)
    except UpdateError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        raise UpdateError('安装包 ZIP 或 CRC 校验失败，原安装包已保留') from None


def _file_sha256(path, deadline):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ZIP_BYTES:
        return None
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(64 * 1024), b''):
            _remaining(deadline)
            digest.update(chunk)
    return digest.hexdigest()


def download_plugin_update():
    """Recheck the official manifest, download and verify; never extract/install."""
    temporary = None
    try:
        current_version = _current_version()
        deadline = time.monotonic() + DOWNLOAD_DEADLINE
        with _session() as session:
            result = _check(session, current_version, min(deadline, time.monotonic() + CHECK_DEADLINE))
            if not result['has_update']:
                return {'ok': True, 'has_update': False, 'version': current_version,
                        'message': '当前已是最新版本，无需下载'}
            DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            target = DOWNLOAD_DIR / result['_filename']
            if target.exists() and _file_sha256(target, deadline) == result['_sha256']:
                _validate_zip(target, result['latest_version'], deadline)
                return {'ok': True, 'path': str(target.resolve()), 'filename': target.name,
                        'version': result['latest_version'], 'already_downloaded': True,
                        'message': '已下载并校验过此版本；请关闭字字动画后手动解压安装'}
            with tempfile.NamedTemporaryFile(mode='wb', dir=DOWNLOAD_DIR, prefix='.' + target.name + '.',
                                             suffix='.part', delete=False) as output:
                temporary = Path(output.name)
                _, digest = _receive(session, result['download_url'], MAX_ZIP_BYTES, deadline, destination=output)
                output.flush()
                os.fsync(output.fileno())
            if digest != result['_sha256']:
                raise UpdateError('安装包 SHA256 校验失败，原安装包已保留，请稍后重试')
            _validate_zip(temporary, result['latest_version'], deadline)
            _remaining(deadline)
            os.replace(temporary, target)
            temporary = None
            return {'ok': True, 'path': str(target.resolve()), 'filename': target.name,
                    'version': result['latest_version'], 'already_downloaded': False,
                    'message': '安装包已下载并通过 SHA256 与 ZIP 校验；请关闭字字动画后手动解压安装'}
    except UpdateError as exc:
        return {'ok': False, 'error': str(exc)}
    except OSError:
        return {'ok': False, 'error': '安装包无法保存到下载目录，请检查磁盘空间或目录权限；原文件已保留'}
    except Exception:
        return {'ok': False, 'error': '插件更新下载失败，原安装包已保留，请稍后重试'}
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
