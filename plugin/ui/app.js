(function () {
  'use strict';
  var Search = window.SuanliSearch, sdk = window.PluginSDK;
  var byId = function (id) { return document.getElementById(id); };
  var controls = Array.from(document.querySelectorAll('[data-param]'));
  var builtin = Search.catalog(window.SUANLI_BUILTIN_MODELS || []), models = builtin.slice();
  var sdkReady = false, revision = 0, requestNumber = 0, accountTimer = null;
  var catalogSource = 'builtin', catalogUpdated = '', updating = false;
  var pluginVersion = '1.1.4';
  var defaultsRevisionKey = '_suanli_defaults_revision', defaultsRevision = pluginVersion;
  var pluginUpdateInfo = null, pluginBusy = false;

  function usesLegacyDefaults(params) {
    if (['1.1.1', '1.1.2', '1.1.3', '1.1.4'].indexOf(String(params[defaultsRevisionKey])) !== -1) return false;
    if (typeof params._suanli_legacy_defaults_pending === 'boolean') return params._suanli_legacy_defaults_pending;
    return ['duration', 'aspect_ratio', 'timeout'].every(function (key) { return Object.prototype.hasOwnProperty.call(params, key); }) &&
      Number(params.duration) === 10 && String(params.aspect_ratio).trim() === '16:9' && Number(params.timeout) === 900;
  }

  function selectedDuration(value) {
    if (typeof value !== 'number' && typeof value !== 'string') return 30;
    var seconds = Number(value);
    return seconds === 15 || seconds === 30 ? seconds : 30;
  }

  function restoreDuration(value) {
    var control = byId('duration'), seconds = selectedDuration(value);
    control.value = String(seconds);
    return seconds;
  }

  function collect() {
    var params = {};
    controls.forEach(function (control) {
      params[control.id] = control.id === 'duration' ? selectedDuration(control.value) : control.type === 'checkbox' ? control.checked : control.type === 'number' ? Number(control.value) : control.value;
    });
    params[defaultsRevisionKey] = defaultsRevision;
    params.use_scene_duration = false;
    return params;
  }
  function safeError(value) {
    var text = String(value || ''), key = byId('api_key').value;
    return key ? text.split(key).join('***') : text;
  }
  function status(message, state) { byId('modelStatus').textContent = safeError(message); byId('modelStatus').dataset.state = state || ''; }
  function save(control) {
    if (!sdkReady || !sdk || typeof sdk.saveParam !== 'function') return;
    if (control.type === 'number' && !control.checkValidity()) return;
    var value = control.id === 'duration' ? selectedDuration(control.value) : control.type === 'checkbox' ? control.checked : control.type === 'number' ? Number(control.value) : control.value;
    sdk.saveParam(control.id, value);
  }
  function appendHighlighted(parent, text, query) {
    var position = 0;
    Search.highlightRanges(text, query).forEach(function (range) {
      if (position < range[0]) parent.appendChild(document.createTextNode(text.slice(position, range[0])));
      var mark = document.createElement('mark'); mark.textContent = text.slice(range[0], range[1]); parent.appendChild(mark); position = range[1];
    });
    if (position < text.length) parent.appendChild(document.createTextNode(text.slice(position)));
  }
  function renderCurrent() {
    var current = byId('model').value; byId('currentModel').value = current;
    byId('currentSelection').setAttribute('data-selected', current ? 'true' : 'false');
    byId('currentModelBadge').hidden = !current;
    var exists = models.some(function (item) { return item.id === current; });
    byId('selectionStatus').textContent = !current ? '请点击列表中的模型完成选择。' : exists ? '搜索和分类不会改变当前选择。' : '当前配置模型未在此列表中找到；已保留原名称。';
  }
  function syncSelection() {
    renderCurrent();
    byId('modelResults').querySelectorAll('.model-option').forEach(function (option) {
      var selected = option.dataset.modelId === byId('model').value;
      option.setAttribute('aria-pressed', selected ? 'true' : 'false');
      var marker = option.querySelector('.selected-marker'); if (marker) marker.hidden = !selected;
    });
  }
  function renderResults() {
    var query = byId('modelSearch').value;
    var found = Search.filter(models, query, byId('familyFilter').value, byId('seriesFilter').value);
    byId('resultCount').textContent = '匹配 ' + found.length + ' / ' + models.length + ' 个模型'; byId('clearQuery').hidden = !query;
    var container = byId('modelResults'), fragment = document.createDocumentFragment();
    found.forEach(function (item) {
      var button = document.createElement('button'); button.type = 'button'; button.className = 'model-option';
      button.dataset.modelId = item.id;
      button.setAttribute('aria-pressed', item.id === byId('model').value ? 'true' : 'false');
      var meta = document.createElement('span'); meta.className = 'model-meta';
      [item.family, item.series].forEach(function (text) { var chip = document.createElement('span'); chip.className = 'chip'; chip.textContent = text; meta.appendChild(chip); });
      if (item.family === '其他' || item.classification === 'unknown') { var unknown = document.createElement('span'); unknown.textContent = '待识别，兼容性待确认'; meta.appendChild(unknown); }
      var selectedMarker = document.createElement('span'); selectedMarker.className = 'selected-marker'; selectedMarker.textContent = '✓ 已选'; selectedMarker.hidden = item.id !== byId('model').value; meta.appendChild(selectedMarker);
      var title = document.createElement('span'); title.className = 'model-name'; appendHighlighted(title, item.id, query);
      button.appendChild(meta); button.appendChild(title);
      button.addEventListener('click', function () {
        byId('model').value = item.id; save(byId('model')); syncSelection();
      });
      fragment.appendChild(button);
    });
    if (!found.length) { var empty = document.createElement('div'); empty.className = 'empty'; empty.textContent = '没有匹配的模型，请减少关键词或清空筛选。'; fragment.appendChild(empty); }
    container.replaceChildren(fragment);
  }
  function refreshFilters() {
    var family = byId('familyFilter'), series = byId('seriesFilter'), previousFamily = family.value, previousSeries = series.value;
    function populate(control, values, allLabel) {
      control.replaceChildren(); var all = document.createElement('option'); all.value = ''; all.textContent = allLabel; control.appendChild(all);
      values.forEach(function (value) { var option = document.createElement('option'); option.value = value; option.textContent = value; control.appendChild(option); });
    }
    populate(family, Search.families, '全部分类');
    populate(series, Array.from(new Set(models.map(function (item) { return item.series; }))).sort(function (a, b) { return a.localeCompare(b, 'zh-CN', {numeric:true}); }), '全部系列');
    family.value = Search.families.indexOf(previousFamily) !== -1 ? previousFamily : '';
    series.value = Array.from(series.options).some(function (option) { return option.value === previousSeries; }) ? previousSeries : '';
  }
  function renderCatalog() {
    var sourceNames = {builtin:'内置列表', cache:'本地缓存', online:'在线列表'};
    var text = (sourceNames[catalogSource] || '模型列表') + ' · ' + models.length + ' 个模型';
    if (catalogUpdated) { var date = new Date(catalogUpdated); text += ' · 更新于 ' + (Number.isNaN(date.getTime()) ? catalogUpdated : date.toLocaleString('zh-CN', {hour12:false})); }
    byId('catalogInfo').textContent = text; refreshFilters(); renderCurrent(); renderResults();
  }
  function setBusy(value) { updating = value; byId('updateModels').disabled = value; byId('updateModels').textContent = value ? '更新中…' : '更新模型'; }
  function action(name, params, timeout) {
    if (!sdkReady || !sdk || typeof sdk.sendAction !== 'function') return Promise.reject(new Error('尚未连接字字动画。请在软件中打开插件后更新；内置模型仍可搜索。'));
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () { reject(new Error('读取模型超时，已保留当前列表。请确认软件版本支持插件在线更新。')); }, timeout);
      Promise.resolve().then(function () { return sdk.sendAction(name, {params:params}, {timeoutMs:timeout}); }).then(function (result) { clearTimeout(timer); resolve(result); }, function (error) { clearTimeout(timer); reject(error); });
    });
  }
  function accept(result) {
    if (!result || result.ok !== true) throw new Error(result && result.error || '未收到有效的模型列表，已保留当前列表。');
    var next = Search.catalog(result.models); if (!next.length) throw new Error('返回的模型列表为空，已保留当前列表。');
    models = next; catalogSource = result.source || 'cache'; catalogUpdated = result.updated_at || ''; renderCatalog();
  }
  async function loadModels(update) {
    if (update && updating) return;
    if (update && !byId('api_key').value.trim()) { status('请先填写 API 令牌，再更新在线模型。', 'error'); byId('connectionSettings').open = true; byId('api_key').focus(); return; }
    if (update && (!byId('base_url').value.trim() || !byId('base_url').checkValidity())) { status('请填写有效的接口地址。', 'error'); byId('connectionSettings').open = true; byId('base_url').focus(); return; }
    var expectedRevision = revision, request = ++requestNumber;
    if (update) { setBusy(true); status('正在获取模型列表…'); }
    try {
      var result = await action(update ? 'update_models' : 'get_models', collect(), update ? 45000 : 8000);
      if (expectedRevision !== revision || request !== requestNumber) return;
      accept(result);
      if (update) {
        var message = '更新完成 · ' + models.length + ' 个模型';
        if (Number.isFinite(result.added)) message += ' · 新增 ' + result.added;
        if (Number.isFinite(result.removed)) message += ' · 移除 ' + result.removed;
        if (result.warning) message += '\n' + result.warning;
        status(message, 'success');
      } else if (result.warning) status(result.warning); else status('');
    } catch (error) {
      if (expectedRevision !== revision || request !== requestNumber) return;
      status(update ? (error.message || String(error)) + '\n当前列表和已选模型已保留。' : '当前使用内置列表。' + (error.message || String(error)), update ? 'error' : '');
    } finally { if (expectedRevision === revision && request === requestNumber) setBusy(false); }
  }
  function accountChanged() {
    revision++; requestNumber++; setBusy(false); clearTimeout(accountTimer);
    models = builtin.slice(); catalogSource = 'builtin'; catalogUpdated = ''; renderCatalog(); status('接口设置已更改，正在读取对应的本地列表。');
    if (sdkReady) accountTimer = setTimeout(function () { loadModels(false); }, 400);
  }
  function pluginStatus(message, state) {
    byId('pluginUpdateStatus').textContent = safeError(message);
    byId('pluginUpdateStatus').dataset.state = state || '';
  }
  function releaseUrl(value, asset) {
    if (typeof value !== 'string' || !value.trim()) return '';
    try {
      var url = new URL(value);
      var prefix = '/huangweif522-star/suanliai-plugin/releases/';
      if (url.protocol !== 'https:' || url.hostname !== 'github.com' || url.username || url.password || url.port || url.pathname.toLowerCase().indexOf(prefix) !== 0) return '';
      if (asset && (!/\/releases\/(?:download\/[^/]+\/|latest\/download\/)/i.test(url.pathname) || !/\.zip$/i.test(url.pathname))) return '';
      return url.href;
    } catch (_) { return ''; }
  }
  function clearPluginUpdateInfo() {
    pluginUpdateInfo = null;
    byId('downloadPluginUpdate').disabled = true;
    byId('pluginVersionInfo').textContent = '';
    byId('pluginReleaseNotes').textContent = '';
    byId('pluginReleaseLink').removeAttribute('href'); byId('pluginReleaseLink').hidden = true;
    byId('pluginReleaseDetails').hidden = true;
  }
  function pluginBusyState(kind) {
    pluginBusy = !!kind;
    byId('checkPluginUpdate').disabled = pluginBusy;
    byId('downloadPluginUpdate').disabled = pluginBusy || !pluginUpdateInfo || !pluginUpdateInfo.has_update;
    byId('checkPluginUpdate').textContent = kind === 'check' ? '检查中…' : '检查更新';
    byId('downloadPluginUpdate').textContent = kind === 'download' ? '下载中…' : '下载新版';
  }
  function pluginAction(name, timeout) {
    if (!sdkReady || !sdk || typeof sdk.sendAction !== 'function') return Promise.reject(new Error('尚未连接字字动画，请在软件中打开插件后重试。'));
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () { reject(new Error('请求超时，请稍后重新检查更新。')); }, timeout);
      // Plugin releases use their own public update source. Never attach the
      // model-service configuration or API token to either release action.
      Promise.resolve().then(function () { return sdk.sendAction(name, {}, {timeoutMs:timeout}); }).then(function (result) { clearTimeout(timer); resolve(result); }, function (error) { clearTimeout(timer); reject(error); });
    });
  }
  async function checkPluginUpdate() {
    if (pluginBusy) return;
    clearPluginUpdateInfo(); pluginBusyState('check'); pluginStatus('正在检查最新插件版本…');
    try {
      var result = await pluginAction('check_plugin_update', 45000);
      if (!result || result.ok !== true) throw new Error(result && result.error || '未收到有效的版本信息。');
      if (typeof result.has_update !== 'boolean' || typeof result.latest_version !== 'string' || !result.latest_version.trim()) throw new Error('返回的版本信息不完整。');
      var downloadUrl = releaseUrl(result.download_url, true), pageUrl = releaseUrl(result.release_url, false);
      var latest = result.latest_version.trim();
      byId('pluginVersionInfo').textContent = '最新版本 v' + latest;
      if (typeof result.published_at === 'string' && result.published_at.trim()) {
        var date = new Date(result.published_at);
        if (!Number.isNaN(date.getTime())) byId('pluginVersionInfo').textContent += ' · ' + date.toLocaleString('zh-CN', {hour12:false});
      }
      byId('pluginReleaseNotes').textContent = typeof result.notes === 'string' && result.notes.trim() ? result.notes.slice(0, 16000) : '本次发布暂未提供更新说明。';
      byId('pluginReleaseDetails').hidden = false;
      if (pageUrl) { byId('pluginReleaseLink').href = pageUrl; byId('pluginReleaseLink').hidden = false; }
      if (result.has_update && !downloadUrl) throw new Error('发现新版，但下载地址无效。请重新检查或前往发布页。');
      pluginUpdateInfo = {has_update:result.has_update, version:latest, download_url:downloadUrl};
      pluginStatus(result.has_update ? '发现新版 v' + latest + '，可点击“下载新版”保存安装包。' : '当前已是最新版本。', 'success');
    } catch (error) {
      pluginUpdateInfo = null;
      pluginStatus('检查失败：' + (error.message || String(error)) + '\n请重新检查更新。', 'error');
    } finally { pluginBusyState(''); }
  }
  async function downloadPluginUpdate() {
    if (pluginBusy) return;
    if (!pluginUpdateInfo || !pluginUpdateInfo.has_update || !releaseUrl(pluginUpdateInfo.download_url, true)) { pluginStatus('请先检查更新，确认有新版后再下载。', 'error'); return; }
    pluginBusyState('download'); pluginStatus('正在下载并校验新版安装包，请稍候…');
    try {
      var result = await pluginAction('download_plugin_update', 180000);
      if (!result || result.ok !== true) throw new Error(result && result.error || '未收到有效的下载结果。');
      if (result.has_update === false) {
        clearPluginUpdateInfo();
        byId('pluginVersionInfo').textContent = '最新版本 v' + (typeof result.version === 'string' && result.version.trim() ? result.version.trim() : pluginVersion);
        pluginStatus(typeof result.message === 'string' && result.message.trim() ? result.message : '当前已是最新版本，无需下载。', 'success');
        return;
      }
      if (typeof result.path !== 'string' || !result.path.trim()) throw new Error('下载结果缺少文件路径。');
      pluginStatus((typeof result.message === 'string' && result.message.trim() ? result.message : result.already_downloaded ? '新版安装包已下载。' : '新版安装包下载完成。') + '\n保存位置：' + result.path + '\n安装后请重启软件。', 'success');
    } catch (error) {
      pluginUpdateInfo = null;
      pluginStatus('下载失败：' + (error.message || String(error)) + '\n请重新检查更新后再试。', 'error');
    } finally { pluginBusyState(''); }
  }
  controls.forEach(function (control) { control.addEventListener('change', function () { save(control); }); });
  ['api_key', 'base_url'].forEach(function (id) { byId(id).addEventListener('input', accountChanged); });
  byId('modelSearch').addEventListener('input', renderResults);
  ['familyFilter', 'seriesFilter'].forEach(function (id) { byId(id).addEventListener('change', renderResults); });
  byId('clearQuery').addEventListener('click', function () { byId('modelSearch').value = ''; renderResults(); byId('modelSearch').focus(); });
  byId('resetFilters').addEventListener('click', function () { byId('modelSearch').value = ''; byId('familyFilter').value = ''; byId('seriesFilter').value = ''; renderResults(); });
  byId('updateModels').addEventListener('click', function () { clearTimeout(accountTimer); loadModels(true); });
  byId('checkPluginUpdate').addEventListener('click', checkPluginUpdate);
  byId('downloadPluginUpdate').addEventListener('click', downloadPluginUpdate);
  byId('copyModel').addEventListener('click', async function () {
    var text = byId('model').value;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(text);
      else { byId('currentModel').focus(); byId('currentModel').select(); if (!document.execCommand('copy')) throw new Error('copy'); }
      status('模型完整名称已复制。', 'success');
    } catch (_) { byId('currentModel').focus(); byId('currentModel').select(); status('已选中完整模型名称，可按 Ctrl+C 复制。'); }
  });
  renderCatalog();
  byId('pluginCurrentVersion').textContent = 'v' + pluginVersion;
  clearPluginUpdateInfo();
  if (sdk && typeof sdk.onRequestParams === 'function') sdk.onRequestParams(collect);
  if (sdk && typeof sdk.onReady === 'function') sdk.onReady(function (params) {
    params = params || {};
    var markDefaults = params[defaultsRevisionKey] !== defaultsRevision;
    var migratedDefaults = usesLegacyDefaults(params);
    if (migratedDefaults) params = Object.assign({}, params, {duration:30, aspect_ratio:'9:16', timeout:3600});
    var durationValue = Object.prototype.hasOwnProperty.call(params, 'duration') ? params.duration : byId('duration').value;
    var restoredDuration = restoreDuration(durationValue);
    var persistDuration = params.duration !== restoredDuration || params.use_scene_duration !== false;
    controls.forEach(function (control) {
      if (control.id === 'duration') return;
      if (params[control.id] == null) return;
      if (control.type === 'checkbox') control.checked = params[control.id] === true || ['true','1','enabled','on'].indexOf(String(params[control.id]).toLowerCase()) !== -1;
      else {
        var value = String(params[control.id]);
        if (control.tagName === 'SELECT' && !Array.from(control.options).some(function (option) { return option.value === value; })) { var option = document.createElement('option'); option.value = value; option.textContent = value + '（原配置）'; control.appendChild(option); }
        control.value = value;
      }
    });
    sdkReady = true;
    if ((markDefaults || persistDuration) && typeof sdk.saveParams === 'function') {
      var defaultsUpdate = {}; defaultsUpdate[defaultsRevisionKey] = defaultsRevision;
      // get_params() may already have migrated the visible values. Persist them
      // with the marker so old disk values cannot be marked as current by accident.
      defaultsUpdate.duration = restoredDuration;
      defaultsUpdate.use_scene_duration = false;
      defaultsUpdate.generation_mode = byId('generation_mode').value;
      defaultsUpdate.aspect_ratio = byId('aspect_ratio').value;
      defaultsUpdate.timeout = Number(byId('timeout').value);
      sdk.saveParams(defaultsUpdate);
    }
    renderCurrent(); renderResults(); loadModels(false);
  }); else status('尚未连接字字动画；内置模型仍可搜索。');
})();
