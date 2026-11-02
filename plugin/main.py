# -*- coding: utf-8 -*-
"""TypeTale video plugin for the supplied SuanliAI unified video API."""

import base64
import importlib.util
import math
import mimetypes
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote, urlparse
import uuid

import requests

for _parent in (Path(__file__).resolve().parent.parent,
                Path(__file__).resolve().parent.parent.parent):
    if str(_parent) not in sys.path:
        sys.path.append(str(_parent))
from plugin_utils import load_plugin_config

_PLUGIN_FILE = __file__
_DEFAULTS = {
    'api_key': '', 'base_url': 'https://suanliai.top/v1', 'model': '专享sd2.0(fast933/4-15秒/720p)',
    'duration': 30, 'use_scene_duration': False, 'aspect_ratio': '9:16',
    'resolution': '720p', 'generation_mode': '多参考图生成视频',
    'generate_audio': False, 'face': False,
    'poll_interval': 5, 'timeout': 3600,
    'videos': '', 'audios': '',
}
_MAX_BYTES = 50 * 1024 * 1024
_DEFAULTS_REVISION_KEY = '_suanli_defaults_revision'
_DEFAULTS_REVISION = '1.1.4'
_MIGRATED_DEFAULTS_REVISIONS = ('1.1.1', '1.1.2', '1.1.3', _DEFAULTS_REVISION)
_LEGACY_DEFAULTS_HINT_KEY = '_suanli_legacy_defaults_pending'


def _allowed_seconds(value):
    """Only 15 or 30 seconds are supported; obsolete settings reset to 30."""
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return 30
        number = float(value)
        return int(number) if number in (15, 30) else 30
    except (TypeError, ValueError, OverflowError):
        return 30


def _uses_legacy_defaults(params):
    """Only a complete, unmarked old default set qualifies for migration."""
    if params.get(_DEFAULTS_REVISION_KEY) in _MIGRATED_DEFAULTS_REVISIONS:
        return False
    if not all(key in params for key in ('duration', 'aspect_ratio', 'timeout')):
        return False
    try:
        return (float(params['duration']) == 10
                and str(params['aspect_ratio']).strip() == '16:9'
                and float(params['timeout']) == 900)
    except (TypeError, ValueError, OverflowError):
        return False


def _effective_params(overrides=None, include_migration_hint=False):
    # Inspect raw fields before filling missing values with the new defaults.
    # The host may overlay disk config after get_params(), so only the UI writes
    # the revision marker; a transient read must never claim it was persisted.
    raw = dict(load_plugin_config(_PLUGIN_FILE) or {})
    raw.update(overrides or {})
    # This read-only hint describes the raw configuration before normalization.
    # Never trust or retain a stale hint supplied by config or generation context.
    raw.pop(_LEGACY_DEFAULTS_HINT_KEY, None)
    legacy_defaults_pending = _uses_legacy_defaults(raw)
    if legacy_defaults_pending:
        raw.update(duration=30, aspect_ratio='9:16', timeout=3600)
    params = dict(_DEFAULTS)
    params.update(raw)
    params['duration'] = _allowed_seconds(params['duration'])
    # Retain a false compatibility field so old host settings cannot re-enable
    # the removed scene-duration override, including in queued task snapshots.
    params['use_scene_duration'] = False
    if include_migration_hint:
        params[_LEGACY_DEFAULTS_HINT_KEY] = legacy_defaults_pending
    return params

# Load beside this file, including when the plugin directory contains a dot.
_catalog_spec = importlib.util.spec_from_file_location(
    'suanli_ai_v1_1_4_catalog', Path(__file__).resolve().parent / 'suanli_catalog.py')
_catalog = importlib.util.module_from_spec(_catalog_spec)
_catalog_spec.loader.exec_module(_catalog)

_updates_spec = importlib.util.spec_from_file_location(
    'suanli_ai_v1_1_4_updates', Path(__file__).resolve().parent / 'suanli_updates.py')
_updates = importlib.util.module_from_spec(_updates_spec)
_updates_spec.loader.exec_module(_updates)


def get_info():
    return {
        'name': '算力AI视频 v1.1.4', 'version': '1.1.4',
        'author': 'Custom',
        'description': '对接 suanliai.top 视频接口，支持文生、首帧、首尾帧和多图参考。',
    }


def get_params():
    return _effective_params(include_migration_hint=True)


def handle_action(action, data=None):
    """Manual plugin updates never read account settings or use the API token."""
    if action == 'check_plugin_update':
        return _updates.check_plugin_update()
    if action == 'download_plugin_update':
        return _updates.download_plugin_update()
    try:
        if action not in ('get_models', 'update_models'):
            return {'ok': False, 'error': '不支持的插件操作'}
        if data is not None and not isinstance(data, dict):
            return {'ok': False, 'error': '插件操作参数格式无效'}
        params = get_params()
        supplied = (data or {}).get('params') or {}
        if not isinstance(supplied, dict):
            return {'ok': False, 'error': '插件设置格式无效'}
        params.update(supplied)
        base = _catalog.normalize_base_url(params.get('base_url'))
        key = str(params.get('api_key') or '').strip()
        if action == 'get_models':
            return _catalog.get_models(base, key)
        return _catalog.update_models(base, key)
    except _catalog.CatalogError as exc:
        message = str(exc)
        if locals().get('key'):
            message = message.replace(key, '***')
        return {'ok': False, 'error': message}
    except Exception:
        return {'ok': False, 'error': '模型列表处理失败，已保留原列表；请检查接口或重新解压插件'}


def _flag(value):
    return value is True or str(value).strip().lower() in ('1', 'true', 'on', 'enabled')


def _number(value, name, minimum=0.01):
    try:
        result = float(value)
        if not math.isfinite(result) or result < minimum:
            raise ValueError()
        return result
    except (TypeError, ValueError):
        raise ValueError(f'{name} 必须是大于等于 {minimum} 的数字') from None


def _base_url(value):
    base = str(value).strip().rstrip('/')
    parsed = urlparse(base)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
        raise ValueError('接口地址必须是有效的 HTTPS 地址，不能包含密钥或查询参数')
    return base if base.endswith('/v1') else base + '/v1'


def _flatten(value):
    if value is None or value == '':
        return []
    if isinstance(value, dict):
        return [item for child in value.values() for item in _flatten(child)]
    if isinstance(value, (list, tuple)):
        return [item for child in value for item in _flatten(child)]
    if isinstance(value, (str, os.PathLike)):
        return [os.fspath(value)]
    raise ValueError('参考素材必须是路径、URL、列表或图片映射')


def _media(value, kind):
    text = str(value).strip()
    if text.lower().startswith(('https://', 'http://')):
        return text
    if text.startswith('data:'):
        header, separator, encoded = text.partition(',')
        if not separator or not header.startswith('data:' + kind + '/') or not header.endswith(';base64'):
            raise ValueError(f'无效的 {kind} Base64 素材')
        if len(encoded) > ((_MAX_BYTES + 2) // 3) * 4:
            raise ValueError('单个参考素材不能超过 50 MB')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError:
            raise ValueError('参考素材 Base64 编码无效') from None
        if not raw or len(raw) > _MAX_BYTES:
            raise ValueError('参考素材为空或超过 50 MB')
        return text
    path = Path(text)
    if not path.is_file():
        raise ValueError(f'参考素材不存在：{path.name}')
    if not 0 < path.stat().st_size <= _MAX_BYTES:
        raise ValueError(f'参考素材为空或超过 50 MB：{path.name}')
    mime = mimetypes.guess_type(path.name)[0]
    mime = mime or {'image': 'image/png', 'video': 'video/mp4', 'audio': 'audio/mpeg'}[kind]
    if not mime.startswith(kind + '/'):
        raise ValueError(f'参考素材类型不匹配：{path.name}')
    return f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def _images(context, mode):
    refs = context.get('reference_images') or {}
    mapping = refs if isinstance(refs, dict) else {}
    first = context.get('first_frame_path') or mapping.get('首帧')
    last = context.get('end_frame_path') or mapping.get('尾帧')
    if mode == '文生视频':
        return []
    if mode == '首尾帧':
        if not first or not last:
            raise ValueError('首尾帧模式需要同时提供首帧和尾帧')
        values = _flatten(first) + _flatten(last)
    elif mode in ('首帧生视频', '首帧生成视频'):
        values = _flatten(first) or _flatten(refs)[:1]
    elif mode in ('参考生视频', '多参考图生成视频'):
        values = _flatten(refs)
        if first:
            values = _flatten(first) + values
        if last:
            values += _flatten(last)
    else:
        raise ValueError('未知生成模式')
    values = list(dict.fromkeys(values))
    if not values:
        raise ValueError('当前生成模式需要参考图片')
    return [_media(item, 'image') for item in values]


def _json(response, action):
    with response:
        if not response.ok:
            # Keep diagnostics short and redact anything that looks like a token.
            detail = ''
            try:
                detail = str(response.json())
            except Exception:
                detail = str(getattr(response, 'text', '') or '')
            detail = ' '.join(detail.split())[:800]
            for marker in ('sk-', 'Bearer '):
                if marker in detail:
                    detail = detail.replace(detail[detail.find(marker):].split()[0], '***')
            if response.status_code == 503 and 'No available channel for model' in detail:
                raise RuntimeError(
                    f'{action}失败：算力平台当前没有该模型的可用渠道。'
                    '请更换模型或等待平台恢复该渠道。'
                )
            suffix = f'：{detail}' if detail else ''
            raise RuntimeError(f'{action}失败：HTTP {response.status_code}{suffix}')
        try:
            result = response.json()
        except ValueError:
            raise RuntimeError(f'{action}返回的不是 JSON') from None
    if not isinstance(result, dict):
        raise RuntimeError(f'{action}返回格式错误')
    return result


def _notify(context, label, progress=None):
    callback = context.get('progress_callback')
    if callback:
        if progress is None:
            callback(label)
        else:
            callback(label, progress)


def _download(base, task_id, key, output, timeout):
    temporary = output.with_suffix('.mp4.part')
    try:
        # Use the authenticated content endpoint; never forward the key to a public URL.
        with requests.get(f'{base}/videos/{task_id}/content',
                          headers={'Authorization': f'Bearer {key}'},
                          stream=True, timeout=(15, timeout), allow_redirects=True) as response:
            if not response.ok:
                raise RuntimeError(f'下载失败：HTTP {response.status_code}')
            content_type = response.headers.get('Content-Type', '').lower()
            if any(item in content_type for item in ('json', 'text/', 'html')):
                raise RuntimeError('下载接口返回了文本而不是视频')
            with temporary.open('wb') as target:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        target.write(chunk)
        if not temporary.stat().st_size:
            raise RuntimeError('下载的视频文件为空')
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def generate(context):
    task_id = None
    try:
        params = _effective_params(context.get('plugin_params') or {})
        key = str(params.get('api_key') or '').strip()
        model = str(params.get('model') or '').strip()
        prompt = str(context.get('prompt') or '').strip()
        if not key or not model or not prompt:
            raise ValueError('请填写 API 令牌、模型广场上的准确模型名和提示词')
        base = _base_url(params['base_url'])
        seconds = _allowed_seconds(params['duration'])
        payload = {
            'model': model, 'prompt': prompt,
            'seconds': seconds,
            'aspect_ratio': str(params['aspect_ratio']).strip(),
            'resolution': str(params['resolution']).strip().lower(),
            'generate_audio': _flag(params['generate_audio']), 'face': _flag(params['face']),
        }
        images = _images(context, params['generation_mode'])
        if images:
            payload['images'] = images
        for field, kind in (('videos', 'video'), ('audios', 'audio')):
            values = context.get('reference_' + field) or params.get(field)
            if isinstance(values, str) and not values.startswith('data:'):
                values = values.splitlines()
            items = [item for item in _flatten(values) if item.strip()]
            if items:
                payload[field] = [_media(item, kind) for item in items]
        interval = _number(params['poll_interval'], '查询间隔', 1)
        timeout = _number(params['timeout'], '等待时间', 1)
        output_dir = Path(context.get('output_dir') or context.get('project_path') or '.').resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        _notify(context, '准备中')
        headers = {'Authorization': f'Bearer {key}'}
        # Never retry POST: an ambiguous network failure may already have incurred a charge.
        try:
            job = _json(requests.post(f'{base}/videos', headers=headers, json=payload,
                                      timeout=(15, 120)), '提交任务')
        except requests.RequestException:
            raise RuntimeError('提交连接中断，任务可能已受理；请先到平台控制台核查，避免重复扣费') from None
        raw_id = next((value for value in (job.get('id'), job.get('task_id'))
                       if isinstance(value, str) and value.strip()), None)
        if raw_id is None:
            raise RuntimeError('提交响应缺少任务 ID，请在平台控制台核查')
        task_id = quote(raw_id.strip(), safe='')
        print(f'[SuanliAI] task_id={task_id}', flush=True)
        deadline = time.monotonic() + timeout
        failures = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('等待超时，平台任务可能仍在生成，请勿直接重复提交')
            try:
                state = _json(requests.get(f'{base}/videos/{task_id}', headers=headers,
                                          timeout=(min(15, remaining), min(30, remaining))), '查询任务')
                failures = 0
            except requests.RequestException:
                failures += 1
                if failures >= 3:
                    raise RuntimeError('连续三次查询连接失败，平台任务可能仍在生成') from None
                time.sleep(min(interval, max(0, deadline - time.monotonic())))
                continue
            status = state.get('status')
            if status == 'completed':
                break
            if status == 'failed':
                error = state.get('error') or {}
                message = error.get('message', '未知错误') if isinstance(error, dict) else str(error)
                raise RuntimeError(f'平台生成失败：{message}')
            if status not in ('queued', 'in_progress'):
                raise RuntimeError(f'平台返回未知任务状态：{status}')
            try:
                progress = max(0, min(99, int(float(state.get('progress') or 0))))
            except (ValueError, TypeError, OverflowError):
                progress = 0
            _notify(context, '排队中' if status == 'queued' else '生成中', progress)
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
        _notify(context, '下载中')
        output = output_dir / f'{int(context.get("viewer_index", 0)):04d}_video_{uuid.uuid4().hex}.mp4'
        _download(base, task_id, key, output, 300)
        _notify(context, '生成中', 100)
        return [str(output)]
    except Exception as exc:
        suffix = f'；任务 ID：{task_id}（可在平台控制台核查或取回）' if task_id else ''
        message = str(exc)
        for secret in (locals().get('key'),):
            if secret:
                message = message.replace(secret, '***')
        raise RuntimeError(f'PLUGIN_ERROR:::{message}{suffix}') from None
