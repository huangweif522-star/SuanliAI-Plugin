# -*- coding: utf-8 -*-
"""Bounded model-directory reads and account-scoped, last-good local caching.

This module never submits generation requests. Unknown model families remain
visible as unclassified entries rather than being declared video-compatible.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import requests


PLUGIN_DIR = Path(__file__).resolve().parent
CACHE_DIR = Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.cache') / 'TypeTalePlugins' / 'Suanli_Ai'
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_MODELS = 20000
MAX_ID_LENGTH = 2048
REQUEST_DEADLINE = 30
_CACHE_LOCK = threading.RLock()
try:
    _KNOWN_IDS = frozenset(json.loads((PLUGIN_DIR / 'models_builtin.json').read_text(encoding='utf-8-sig')))
except (OSError, ValueError, TypeError):
    _KNOWN_IDS = frozenset()


class CatalogError(ValueError):
    """A user-presentable directory error, without remote body or credentials."""


def normalize_base_url(value):
    value = str(value or '').strip().rstrip('/')
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise CatalogError('接口地址格式无效') from None
    if (parts.scheme.lower() != 'https' or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment):
        raise CatalogError('接口地址必须是有效 HTTPS 地址，不能包含令牌或查询参数')
    host = parts.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    if port and port != 443:
        host += ':' + str(port)
    path = parts.path.rstrip('/')
    if not path.endswith('/v1'):
        path += '/v1'
    return urlunsplit(('https', host, path, '', ''))


def _identity(base, key):
    # Store only a one-way account-and-root fingerprint; the token never enters JSON.
    return hashlib.sha256((normalize_base_url(base) + '\0' + str(key or '')).encode('utf-8')).hexdigest()


def _cache_path(base, key):
    return CACHE_DIR / ('models_' + _identity(base, key) + '.json')


def _id(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > MAX_ID_LENGTH
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise CatalogError('模型列表含有无效模型名称，已保留原列表')
    return value


def classify(model_id, declared_video=False):
    """Derive display labels only; never rewrite an API model ID."""
    normalized = unicodedata.normalize('NFKC', model_id).lower()
    compact = re.sub(r'[\s_-]+', '', normalized)
    if 'videoediting' in compact:
        family = '视频编辑'
    elif re.search(r'sd2\.5pro', compact):
        family = 'SD2.5 Pro'
    elif 'sd2.5' in compact:
        family = 'SD2.5'
    elif 'sd2.0' in compact:
        family = 'SD2.0'
    elif 'minimax' in compact and (declared_video or model_id in _KNOWN_IDS
                                   or re.search(r'minimaxh\d', compact)
                                   or any(word in compact for word in ('video', '视频', '漫剧'))):
        family = 'MiniMax'
    elif 'wan3' in compact:
        family = 'Wan3'
    elif 'grok' in compact and (declared_video or model_id in _KNOWN_IDS
                                or any(word in compact for word in ('video', 'imagine', '视频'))):
        family = 'Grok'
    else:
        family = '其他'
    match = re.match(r'^(\d+系列)/', model_id)
    series = match.group(1) if match else ('专享' if model_id.startswith('专享') else '其他')
    classification = 'metadata' if declared_video else ('name' if family != '其他' else 'unclassified')
    return {'id': model_id, 'name': model_id, 'family': family, 'series': series,
            'classification': classification}


def _declared_video(row):
    """Honor explicit output/kind fields; generic `object: model` is not a type."""
    if not isinstance(row, dict):
        return None
    values = []
    for field in ('kind', 'type', 'modality', 'modalities', 'output_modality', 'output_modalities', 'model_type'):
        raw = row.get(field)
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, list):
            values.extend(item for item in raw if isinstance(item, str))
    video = {'video', 'videos', 'video-generation', 'video_generation', 'text-to-video',
             'image-to-video', 'video-to-video', 't2v', 'i2v', 'v2v', '视频'}
    other = {'text', 'chat', 'llm', 'language', 'completion', 'completions', 'embedding',
             'embeddings', 'image', 'images', 'image-generation', 'image_generation',
             'text-to-image', 'audio', 'speech', 'music', 'tts', 'asr', '文本', '图片', '音频'}
    values = {value.strip().lower() for value in values}
    if values & video:
        return True
    if values & other:
        return False
    return None


def _rows(payload):
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get('data', payload.get('models'))
        if isinstance(rows, dict):
            rows = rows.get('models', rows.get('data'))
        if not isinstance(rows, list):
            raise CatalogError('平台模型列表格式暂不支持，已保留原列表')
        # An unimplemented pagination protocol must not silently truncate a catalog.
        pagination_sources = [payload]
        for field in ('pagination', 'meta', 'data', 'links', '_links'):
            if isinstance(payload.get(field), dict):
                pagination_sources.append(payload[field])
        for page in pagination_sources:
            for field in ('has_more', 'has_next', 'has_next_page'):
                if page.get(field) not in (None, False, 0, '', 'false', 'False', '0'):
                    raise CatalogError('平台返回分页模型列表，当前未完整读取，已保留原列表')
            for field in ('next', 'next_page', 'next_cursor', 'next_page_token'):
                if page.get(field) not in (None, False, 0, ''):
                    raise CatalogError('平台返回分页模型列表，当前未完整读取，已保留原列表')
            for field in ('total', 'total_count', 'total_models'):
                total = page.get(field)
                if isinstance(total, str) and total.isdigit():
                    total = int(total)
                if isinstance(total, (int, float)) and not isinstance(total, bool) and total > len(rows):
                    raise CatalogError('平台模型列表未返回完整内容，已保留原列表')
            for field in ('total_pages', 'last_page'):
                pages = page.get(field)
                if isinstance(pages, str) and pages.isdigit():
                    pages = int(pages)
                if isinstance(pages, (int, float)) and not isinstance(pages, bool) and pages > 1:
                    raise CatalogError('平台返回分页模型列表，当前未完整读取，已保留原列表')
    else:
        raise CatalogError('平台模型列表格式暂不支持，已保留原列表')
    if not rows:
        raise CatalogError('平台返回空模型列表，已保留原列表')
    if len(rows) > MAX_MODELS:
        raise CatalogError('平台模型列表过大，已保留原列表')
    return rows


def parse_models(payload):
    result = []
    seen = set()
    for row in _rows(payload):
        if isinstance(row, str):
            model_id = _id(row)
        elif isinstance(row, dict):
            model_id = _id(row.get('id', row.get('model')))
        else:
            raise CatalogError('平台模型列表含有无效条目，已保留原列表')
        declared = _declared_video(row)
        if declared is False:
            continue
        if model_id not in seen:
            result.append(classify(model_id, declared is True))
            seen.add(model_id)
    if not result:
        raise CatalogError('列表中未找到视频或待识别模型，已保留原列表')
    return result


def _result(models, source, updated_at='', added=0, removed=0):
    unclassified = sum(item['classification'] == 'unclassified' for item in models)
    result = {'ok': True, 'models': models, 'source': source, 'updated_at': updated_at,
              'added': added, 'removed': removed, 'unclassified': unclassified}
    if unclassified:
        result['warning'] = f'{unclassified} 个模型的用途尚未确认，已放入“其他／待识别”；请按平台说明选择。'
    else:
        result['warning'] = '分类依据平台类型或模型名称，具体生成能力以平台说明为准。'
    return result


def _builtin():
    try:
        return parse_models(json.loads((PLUGIN_DIR / 'models_builtin.json').read_text(encoding='utf-8-sig')))
    except (OSError, ValueError):
        raise CatalogError('内置模型列表读取失败，请重新解压完整插件') from None


def get_models(base, key):
    base = normalize_base_url(base)
    path = _cache_path(base, key)
    try:
        with _CACHE_LOCK:
            if path.stat().st_size > MAX_RESPONSE_BYTES:
                raise ValueError('cache too large')
            cached = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(cached, dict) or cached.get('schema') != 1 or cached.get('identity') != _identity(base, key):
            raise ValueError('cache identity mismatch')
        rows = cached.get('models')
        if not isinstance(rows, list):
            raise ValueError('invalid cached models')
        # Preserve a server-confirmed video type across the local cache round-trip.
        rows = [dict(item, kind='video') if isinstance(item, dict) and item.get('classification') == 'metadata'
                else item for item in rows]
        models = parse_models(rows)
        updated = cached.get('updated_at')
        if not isinstance(updated, str) or len(updated) > 80:
            raise ValueError('invalid timestamp')
        return _result(models, 'cache', updated)
    except (OSError, ValueError, TypeError):
        return _result(_builtin(), 'builtin')


def _fetch(base, key):
    if not isinstance(key, str) or not key.strip():
        raise CatalogError('请先填写 API 令牌，再更新模型列表')
    if '\r' in key or '\n' in key:
        raise CatalogError('API 令牌包含无效换行，请重新填写')
    started = time.monotonic()
    try:
        with requests.get(base + '/models', headers={'Authorization': 'Bearer ' + key},
                          timeout=(8, 12), stream=True, allow_redirects=False) as response:
            status = response.status_code
            if status in (401, 403):
                raise CatalogError('模型更新失败：令牌无效或无权读取模型列表，已保留原列表')
            if status == 404:
                raise CatalogError('模型更新失败：接口未提供 /models 列表，请核对接口地址')
            if 300 <= status < 400:
                raise CatalogError('模型列表接口要求跳转，请填写最终 HTTPS 接口地址后重试')
            if not 200 <= status < 300:
                raise CatalogError(f'模型更新失败：HTTP {status}，已保留原列表')
            length = response.headers.get('Content-Length', '')
            if length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
                raise CatalogError('平台模型列表响应过大，已保留原列表')
            chunks = []
            size = 0
            for chunk in response.iter_content(64 * 1024):
                if time.monotonic() - started > REQUEST_DEADLINE:
                    raise CatalogError('模型列表读取超时，已保留原列表')
                if chunk:
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise CatalogError('平台模型列表响应过大，已保留原列表')
                    chunks.append(chunk)
            try:
                return json.loads(b''.join(chunks).decode('utf-8-sig'))
            except (ValueError, UnicodeError):
                raise CatalogError('模型列表接口未返回有效 JSON，已保留原列表') from None
    except requests.Timeout:
        raise CatalogError('连接模型列表超时，已保留原列表') from None
    except requests.RequestException:
        # Exception strings may contain request headers/URLs; never expose them.
        raise CatalogError('模型列表连接失败，请检查网络及接口地址；已保留原列表') from None


def _write_cache(base, key, models, updated_at):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(base, key)
    payload = {'schema': 1, 'identity': _identity(base, key), 'updated_at': updated_at,
               'models': models}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise CatalogError('处理后的模型目录过大，已保留原列表')
    temporary = None
    try:
        with _CACHE_LOCK:
            with tempfile.NamedTemporaryFile(mode='wb', dir=CACHE_DIR, prefix='models_', suffix='.tmp', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
    except OSError:
        raise CatalogError('模型列表保存失败，请检查用户目录写入权限；已保留原列表') from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def update_models(base, key):
    base = normalize_base_url(base)
    before = get_models(base, key)
    models = parse_models(_fetch(base, key))
    updated_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
    _write_cache(base, key, models, updated_at)
    old_ids = {item['id'] for item in before['models']}
    new_ids = {item['id'] for item in models}
    return _result(models, 'online', updated_at, len(new_ids - old_ids), len(old_ids - new_ids))
