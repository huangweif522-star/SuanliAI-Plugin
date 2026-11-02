'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..', 'plugin');
const html = fs.readFileSync(path.join(root, 'ui', 'index.html'), 'utf8');
const code = fs.readFileSync(path.join(root, 'ui', 'app.js'), 'utf8');
const Search = require(path.join(root, 'ui', 'model-search.js'));
const builtin = JSON.parse(fs.readFileSync(path.join(root, 'models_builtin.json'), 'utf8'));
const marker = '_suanli_defaults_revision';
const current = '1.1.4';
const latest = '1.1.5';
const repository = 'https://github.com/huangweif522-star/SuanliAI-Plugin';
const downloadUrl = repository + '/releases/download/v' + latest + '/Suanli_Ai_v' + latest + '.zip';
const releaseUrl = repository + '/releases/tag/v' + latest;
const chosen = '专享sd2.0(fast933/4-15秒/720p)';
const clone = value => JSON.parse(JSON.stringify(value));

// The production app.js is executed unchanged in a DOM fixture. Elements model
// SELECT validity, fragments, nested queries and browser textContent semantics.
class Element {
  constructor(tag = 'DIV') {
    this.tagName = tag.toUpperCase(); this.attributes = {}; this.dataset = {};
    this.children = []; this.events = {}; this._value = ''; this._text = '';
    this.hidden = false; this.disabled = false; this.checked = false; this.type = 'text';
  }
  get className() { return this.attributes.class || ''; }
  set className(value) { this.setAttribute('class', value); }
  get href() { return this.attributes.href || ''; }
  set href(value) { this.setAttribute('href', value); }
  get options() { return this.children.filter(child => child.tagName === 'OPTION'); }
  get value() { return this._value; }
  set value(value) {
    value = String(value);
    this._value = this.tagName === 'SELECT' && !this.options.some(option => option.value === value) ? '' : value;
  }
  get textContent() { return this._text + this.children.map(child => child.textContent || '').join(''); }
  set textContent(value) { this._text = String(value); this.children = []; }
  appendChild(child) {
    if (child.tagName === 'FRAGMENT') { child.children.slice().forEach(item => this.appendChild(item)); child.children = []; return child; }
    this.children.push(child);
    if (this.tagName === 'SELECT' && this.options.length === 1) this._value = child.value;
    return child;
  }
  replaceChildren(...children) { this.children = []; this._text = ''; if (this.tagName === 'SELECT') this._value = ''; children.forEach(child => this.appendChild(child)); }
  setAttribute(key, value) {
    this.attributes[key] = String(value);
    if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
  }
  getAttribute(key) { return Object.hasOwn(this.attributes, key) ? this.attributes[key] : null; }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(type, callback) { (this.events[type] ||= []).push(callback); }
  emit(type) { return (this.events[type] || []).map(callback => callback({target: this, type})); }
  async click() { if (!this.disabled) await Promise.all(this.emit('click')); }
  querySelectorAll(selector) {
    const result = [];
    const matches = child => selector[0] === '.' ? child.className.split(/\s+/).includes(selector.slice(1)) : child.tagName === selector.toUpperCase();
    const visit = node => node.children.forEach(child => { if (matches(child)) result.push(child); visit(child); });
    visit(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  checkValidity() { return true; }
  focus() {}
  select() {}
}

function attributes(text) {
  const attrs = {};
  for (const match of text.matchAll(/([\w:-]+)(?:="([^"]*)")?/g)) attrs[match[1]] = match[2] == null ? '' : match[2];
  return attrs;
}
function domFromHtml() {
  const nodes = {}, controls = [];
  for (const match of html.matchAll(/<([a-z][\w-]*)\b([^>]*\bid="([^"]+)"[^>]*)>/gi)) {
    const element = new Element(match[1]), attrs = attributes(match[2]), id = match[3];
    assert.ok(!nodes[id], 'HTML IDs must be unique: ' + id);
    nodes[id] = element; element.id = id;
    Object.entries(attrs).forEach(([name, value]) => element.setAttribute(name, value));
    element.type = attrs.type || (element.tagName === 'SELECT' ? 'select-one' : 'text');
    element.hidden = Object.hasOwn(attrs, 'hidden'); element.disabled = Object.hasOwn(attrs, 'disabled'); element.checked = Object.hasOwn(attrs, 'checked');
    if (element.tagName === 'SELECT') {
      const inner = html.slice(match.index + match[0].length).split('</select>')[0];
      let selected;
      for (const optionMatch of inner.matchAll(/<option\b([^>]*)>([\s\S]*?)<\/option>/g)) {
        const option = new Element('OPTION'), optionAttrs = attributes(optionMatch[1]);
        option.textContent = optionMatch[2]; option.value = Object.hasOwn(optionAttrs, 'value') ? optionAttrs.value : optionMatch[2];
        element.appendChild(option); if (Object.hasOwn(optionAttrs, 'selected')) selected = option.value;
      }
      if (selected !== undefined) element.value = selected;
    } else {
      if (Object.hasOwn(attrs, 'value')) element.value = attrs.value;
      const inner = html.slice(match.index + match[0].length).split('</' + match[1] + '>')[0];
      if (['SPAN', 'DIV', 'BUTTON', 'TEXTAREA'].includes(element.tagName) && !inner.includes('<')) element.textContent = inner;
    }
    if (Object.hasOwn(attrs, 'data-param')) controls.push(element);
  }
  return {nodes, controls};
}
function deferred() { let resolve, reject; const promise = new Promise((res, rej) => { resolve = res; reject = rej; }); return {promise, resolve, reject}; }
const settle = async () => { await new Promise(resolve => setImmediate(resolve)); };
function release(overrides = {}) {
  return {ok: true, current_version: current, latest_version: latest, has_update: true, download_url: downloadUrl,
    release_url: releaseUrl, notes: '新增模型筛选\n优化已选模型提示', published_at: '2026-11-01T03:00:00Z', ...overrides};
}
async function boot(params = {}, config = {}) {
  const {nodes, controls} = domFromHtml(), saves = [], singleSaves = [], calls = [], timers = new Map();
  let onReady, collect, timerId = 0;
  const handlers = {...config.handlers};
  const sdk = {
    onReady(callback) { onReady = callback; }, onRequestParams(callback) { collect = callback; },
    saveParams(values) { saves.push(clone(values)); }, saveParam(key, value) { singleSaves.push({key, value}); },
    sendAction(name, data, options) {
      calls.push({name, data: clone(data), options: clone(options)});
      if (handlers[name]) return handlers[name](data, options);
      if (name === 'get_models' || name === 'update_models') return Promise.resolve({ok: true, source: 'builtin', models: builtin, updated_at: ''});
      throw new Error('Unexpected version action without a fixture: ' + name);
    }
  };
  const document = {
    getElementById(id) { assert.ok(nodes[id], 'app requested a real HTML ID: ' + id); return nodes[id]; },
    querySelectorAll(selector) { assert.equal(selector, '[data-param]'); return controls; },
    createElement(tag) { return new Element(tag); }, createTextNode(text) { const node = new Element('#TEXT'); node.textContent = text; return node; },
    createDocumentFragment() { return new Element('FRAGMENT'); }, execCommand() { return true; }
  };
  const context = {window: {SuanliSearch: Search, PluginSDK: sdk, SUANLI_BUILTIN_MODELS: builtin}, document, navigator: {}, URL, console,
    setTimeout(callback, milliseconds) { const id = ++timerId; timers.set(id, {callback, milliseconds}); return id; },
    clearTimeout(id) { timers.delete(id); }};
  vm.runInNewContext(code, context, {filename: 'production/ui/app.js'});
  if (config.ready !== false) { onReady({...params}); await settle(); }
  return {nodes, saves, singleSaves, calls, handlers, timers,
    collected: () => clone(collect()),
    rows: () => nodes.modelResults.querySelectorAll('.model-option'),
    runTimers(milliseconds) { for (const [id, timer] of [...timers]) if (timer.milliseconds === milliseconds) { timers.delete(id); timer.callback(); } }};
}

let checks = 0;
async function test(label, fn) { await fn(); checks++; console.log('PASS ' + label); }
(async () => {
  await test('actual HTML defaults remain 15/30 seconds, 30 default, reference mode and dark theme', async () => {
    const {nodes} = domFromHtml(); assert.deepEqual(nodes.duration.options.map(option => option.value), ['15', '30']); assert.equal(nodes.duration.value, '30');
    assert.equal(nodes.generation_mode.value, '多参考图生成视频'); assert.ok(!nodes.use_scene_duration); assert.ok(html.includes('--suanli-bg:#202124')); assert.ok(!html.includes('prefers-color-scheme:light'));
    assert.ok(html.includes('v1.1.4')); assert.equal(nodes.downloadPluginUpdate.disabled, true);
  });
  await test('startup never checks or downloads a plugin release', async () => {
    const app = await boot(); assert.deepEqual(app.calls.map(call => call.name), ['get_models']); assert.equal(app.nodes.pluginCurrentVersion.textContent, 'v1.1.4'); assert.equal(app.nodes.downloadPluginUpdate.disabled, true);
  });
  await test('check action is independent of API token and sends only an empty payload', async () => {
    const app = await boot({api_key: 'private-model-token'}, {handlers: {check_plugin_update: () => release()}});
    await app.nodes.checkPluginUpdate.click(); const call = app.calls.find(call => call.name === 'check_plugin_update');
    assert.deepEqual(call.data, {}); assert.deepEqual(call.options, {timeoutMs: 45000}); assert.ok(!JSON.stringify(call).includes('private-model-token'));
  });
  await test('explicit newer release enables download and safely presents notes and release page', async () => {
    const app = await boot({}, {handlers: {check_plugin_update: () => release()}}); await app.nodes.checkPluginUpdate.click();
    assert.equal(app.nodes.downloadPluginUpdate.disabled, false); assert.ok(app.nodes.pluginVersionInfo.textContent.includes(latest)); assert.equal(app.nodes.pluginReleaseNotes.textContent, release().notes);
    assert.equal(app.nodes.pluginReleaseLink.href, releaseUrl); assert.equal(app.nodes.pluginReleaseLink.hidden, false); assert.equal(app.nodes.pluginReleaseDetails.hidden, false);
  });
  await test('no newer release keeps download disabled and reports current status', async () => {
    const app = await boot({}, {handlers: {check_plugin_update: () => release({has_update: false, latest_version: current})}}); await app.nodes.checkPluginUpdate.click();
    assert.equal(app.nodes.downloadPluginUpdate.disabled, true); assert.equal(app.nodes.pluginUpdateStatus.dataset.state, 'success'); assert.ok(app.nodes.pluginUpdateStatus.textContent.includes('最新版本'));
  });
  await test('invalid version data never claims success or enables download', async () => {
    for (const result of [{ok: true}, release({has_update: 'true'}), release({latest_version: ''})]) {
      const app = await boot({}, {handlers: {check_plugin_update: () => result}}); await app.nodes.checkPluginUpdate.click();
      assert.equal(app.nodes.downloadPluginUpdate.disabled, true); assert.equal(app.nodes.pluginUpdateStatus.dataset.state, 'error');
    }
  });
  await test('unsafe or unrelated ZIP URLs cannot enable downloads', async () => {
    for (const url of ['javascript:alert(1)', 'http://github.com/huangweif522-star/SuanliAI-Plugin/releases/download/v1.1.5/a.zip', 'https://example.com/a.zip',
      'https://github.com/other/repo/releases/download/v1.1.5/a.zip', 'https://user:pass@github.com/huangweif522-star/SuanliAI-Plugin/releases/download/v1.1.5/a.zip',
      'https://github.com:8443/huangweif522-star/SuanliAI-Plugin/releases/download/v1.1.5/a.zip', releaseUrl, downloadUrl.replace('.zip', '.exe')]) {
      const app = await boot({}, {handlers: {check_plugin_update: () => release({download_url: url})}}); await app.nodes.checkPluginUpdate.click();
      assert.equal(app.nodes.downloadPluginUpdate.disabled, true, url); assert.equal(app.nodes.pluginUpdateStatus.dataset.state, 'error');
    }
  });
  await test('unsafe release-page URL is hidden while a valid ZIP remains downloadable', async () => {
    const app = await boot({}, {handlers: {check_plugin_update: () => release({release_url: 'javascript:alert(1)'})}}); await app.nodes.checkPluginUpdate.click();
    assert.equal(app.nodes.pluginReleaseLink.hidden, true); assert.equal(app.nodes.pluginReleaseLink.href, ''); assert.equal(app.nodes.downloadPluginUpdate.disabled, false);
  });
  await test('HTML in release notes remains plain text', async () => {
    const notes = '<img src=x onerror=alert(1)>\n<script>window.bad=true</script>';
    const app = await boot({}, {handlers: {check_plugin_update: () => release({notes})}}); await app.nodes.checkPluginUpdate.click();
    assert.equal(app.nodes.pluginReleaseNotes.textContent, notes); assert.equal(app.nodes.pluginReleaseNotes.children.length, 0);
  });
  await test('a failed recheck invalidates the previous download state and link', async () => {
    const app = await boot({}, {handlers: {check_plugin_update: () => release()}}); await app.nodes.checkPluginUpdate.click();
    app.handlers.check_plugin_update = () => ({ok: false, error: '模拟网络错误'}); await app.nodes.checkPluginUpdate.click();
    assert.equal(app.nodes.downloadPluginUpdate.disabled, true); assert.equal(app.nodes.pluginReleaseLink.href, ''); assert.equal(app.nodes.pluginReleaseDetails.hidden, true);
    assert.ok(app.nodes.pluginUpdateStatus.textContent.includes('重新检查')); const count = app.calls.length; await app.nodes.downloadPluginUpdate.click(); assert.equal(app.calls.length, count);
  });
  await test('checking disables both controls and prevents duplicate requests', async () => {
    const waiting = deferred(), app = await boot({}, {handlers: {check_plugin_update: () => waiting.promise}});
    const pending = app.nodes.checkPluginUpdate.emit('click'); await settle(); assert.equal(app.nodes.checkPluginUpdate.disabled, true); assert.equal(app.nodes.downloadPluginUpdate.disabled, true);
    await Promise.all(app.nodes.checkPluginUpdate.emit('click')); assert.equal(app.calls.filter(call => call.name === 'check_plugin_update').length, 1);
    waiting.resolve(release()); await Promise.all(pending); assert.equal(app.nodes.checkPluginUpdate.disabled, false); assert.equal(app.nodes.downloadPluginUpdate.disabled, false);
  });
  await test('check timeout clears stale permissions and late response cannot restore them', async () => {
    const waiting = deferred(), app = await boot({}, {handlers: {check_plugin_update: () => waiting.promise}});
    const pending = app.nodes.checkPluginUpdate.emit('click'); await settle(); app.runTimers(45000); await Promise.all(pending);
    assert.equal(app.nodes.downloadPluginUpdate.disabled, true); assert.equal(app.nodes.pluginUpdateStatus.dataset.state, 'error');
    waiting.resolve(release()); await settle(); assert.equal(app.nodes.downloadPluginUpdate.disabled, true);
  });
  await test('download action has an empty payload, 180 second timeout and full saved path', async () => {
    const saved = 'C:\\Users\\Example\\Downloads\\Suanli_Ai_v1.1.5.zip';
    const app = await boot({api_key: 'private-model-token'}, {handlers: {check_plugin_update: () => release(), download_plugin_update: () => ({ok: true, path: saved, filename: 'Suanli_Ai_v1.1.5.zip', version: latest})}});
    await app.nodes.checkPluginUpdate.click(); await app.nodes.downloadPluginUpdate.click();
    const call = app.calls.find(call => call.name === 'download_plugin_update'); assert.deepEqual(call.data, {}); assert.deepEqual(call.options, {timeoutMs: 180000});
    assert.ok(!JSON.stringify(call).includes('private-model-token')); assert.ok(app.nodes.pluginUpdateStatus.textContent.includes(saved)); assert.equal(app.nodes.pluginUpdateStatus.dataset.state, 'success');
  });
  await test('pending download disables both controls and duplicate clicks do not download twice', async () => {
    const waiting = deferred(), app = await boot({}, {handlers: {check_plugin_update: () => release(), download_plugin_update: () => waiting.promise}});
    await app.nodes.checkPluginUpdate.click(); const pending = app.nodes.downloadPluginUpdate.emit('click'); await settle();
    assert.equal(app.nodes.checkPluginUpdate.disabled, true); assert.equal(app.nodes.downloadPluginUpdate.disabled, true);
    await Promise.all(app.nodes.downloadPluginUpdate.emit('click')); assert.equal(app.calls.filter(call => call.name === 'download_plugin_update').length, 1);
    waiting.resolve({ok: true, path: 'C:\\Downloads\\plugin.zip'}); await Promise.all(pending); assert.equal(app.nodes.checkPluginUpdate.disabled, false);
  });
  await test('download failure revokes stale download permission and requests a new check', async () => {
    const app = await boot({}, {handlers: {check_plugin_update: () => release(), download_plugin_update: () => ({ok: false, error: '模拟校验失败'})}});
    await app.nodes.checkPluginUpdate.click(); await app.nodes.downloadPluginUpdate.click(); assert.equal(app.nodes.downloadPluginUpdate.disabled, true);
    assert.ok(app.nodes.pluginUpdateStatus.textContent.includes('重新检查')); assert.equal(app.nodes.checkPluginUpdate.disabled, false);
  });
  await test('download recheck finding no update is a normal success without a path', async () => {
    const app = await boot({}, {handlers: {check_plugin_update: () => release(), download_plugin_update: () => ({ok: true, has_update: false, version: current, message: '当前已是最新版本，无需下载'})}});
    await app.nodes.checkPluginUpdate.click(); await app.nodes.downloadPluginUpdate.click(); assert.equal(app.nodes.pluginUpdateStatus.dataset.state, 'success');
    assert.ok(app.nodes.pluginUpdateStatus.textContent.includes('无需下载')); assert.equal(app.nodes.downloadPluginUpdate.disabled, true);
  });
  await test('already downloaded release presents the verified existing file', async () => {
    const app = await boot({}, {handlers: {check_plugin_update: () => release(), download_plugin_update: () => ({ok: true, path: 'C:\\Downloads\\existing.zip', already_downloaded: true})}});
    await app.nodes.checkPluginUpdate.click(); await app.nodes.downloadPluginUpdate.click(); assert.ok(app.nodes.pluginUpdateStatus.textContent.includes('已下载')); assert.ok(app.nodes.pluginUpdateStatus.textContent.includes('existing.zip'));
  });
  await test('malformed download success without a path is rejected', async () => {
    const app = await boot({}, {handlers: {check_plugin_update: () => release(), download_plugin_update: () => ({ok: true})}});
    await app.nodes.checkPluginUpdate.click(); await app.nodes.downloadPluginUpdate.click(); assert.equal(app.nodes.pluginUpdateStatus.dataset.state, 'error'); assert.equal(app.nodes.downloadPluginUpdate.disabled, true);
  });
  await test('download timeout restores check control and blocks an unchecked retry', async () => {
    const waiting = deferred(), app = await boot({}, {handlers: {check_plugin_update: () => release(), download_plugin_update: () => waiting.promise}});
    await app.nodes.checkPluginUpdate.click(); const pending = app.nodes.downloadPluginUpdate.emit('click'); await settle(); app.runTimers(180000); await Promise.all(pending);
    assert.equal(app.nodes.pluginUpdateStatus.dataset.state, 'error'); assert.equal(app.nodes.checkPluginUpdate.disabled, false); assert.equal(app.nodes.downloadPluginUpdate.disabled, true);
    waiting.resolve({ok: true, path: 'C:\\late.zip'}); await settle(); assert.equal(app.nodes.downloadPluginUpdate.disabled, true);
  });
  await test('forcing a download event before checking never invokes the backend', async () => {
    const app = await boot(); const before = app.calls.length; await Promise.all(app.nodes.downloadPluginUpdate.emit('click')); assert.equal(app.calls.length, before); assert.ok(app.nodes.pluginUpdateStatus.textContent.includes('先检查'));
  });
  await test('initial chosen model has a highlighted current card and exactly one checked row', async () => {
    const app = await boot({model: chosen}); const selected = app.rows().filter(row => row.getAttribute('aria-pressed') === 'true');
    assert.equal(app.nodes.currentSelection.getAttribute('data-selected'), 'true'); assert.equal(app.nodes.currentModelBadge.hidden, false); assert.equal(selected.length, 1);
    assert.equal(selected[0].dataset.modelId, chosen); assert.equal(selected[0].querySelector('.selected-marker').hidden, false);
  });
  await test('choosing another row synchronizes card, tick, pressed state and raw saved ID', async () => {
    const app = await boot({model: chosen}); const row = app.rows()[0]; await row.click();
    assert.equal(app.nodes.currentModel.value, builtin[0]); assert.equal(app.nodes.model.value, builtin[0]); assert.equal(app.nodes.currentModelBadge.hidden, false);
    assert.deepEqual(app.singleSaves, [{key: 'model', value: builtin[0]}]);
    for (const item of app.rows()) { const expected = item.dataset.modelId === builtin[0]; assert.equal(item.getAttribute('aria-pressed'), String(expected)); assert.equal(item.querySelector('.selected-marker').hidden, !expected); }
  });
  await test('zero-result search preserves the selected card and reset restores its tick', async () => {
    const app = await boot({model: chosen}); app.nodes.modelSearch.value = 'no-such-model-12345'; await Promise.all(app.nodes.modelSearch.emit('input'));
    assert.equal(app.rows().length, 0); assert.equal(app.nodes.currentModel.value, chosen); assert.equal(app.nodes.currentModelBadge.hidden, false);
    await app.nodes.resetFilters.click(); const selected = app.rows().filter(row => row.getAttribute('aria-pressed') === 'true'); assert.equal(selected.length, 1); assert.equal(selected[0].dataset.modelId, chosen);
  });
  await test('catalog refresh preserves current selection when absent and rechecks it when it returns', async () => {
    let include = false;
    const app = await boot({model: chosen, api_key: 'preview-key'}, {handlers: {update_models: () => ({ok: true, source: 'online', models: include ? [builtin[0], chosen] : [builtin[0]], added: 0, removed: 1})}});
    await app.nodes.updateModels.click(); assert.equal(app.nodes.model.value, chosen); assert.ok(app.nodes.selectionStatus.textContent.includes('未在此列表')); assert.equal(app.nodes.currentModelBadge.hidden, false); assert.ok(app.rows().every(row => row.getAttribute('aria-pressed') === 'false'));
    include = true; await app.nodes.updateModels.click(); const selected = app.rows().filter(row => row.getAttribute('aria-pressed') === 'true'); assert.equal(selected.length, 1); assert.equal(selected[0].dataset.modelId, chosen);
  });
  await test('empty current selection hides the current badge without choosing a model', async () => {
    const app = await boot({model: ''}); assert.equal(app.nodes.currentSelection.getAttribute('data-selected'), 'false'); assert.equal(app.nodes.currentModelBadge.hidden, true); assert.ok(app.rows().every(row => row.getAttribute('aria-pressed') === 'false'));
  });
  await test('1.1.3 marker protects custom aspect and timeout and upgrades all defaults once', async () => {
    const app = await boot({duration: 15, aspect_ratio: '16:9', timeout: 900, generation_mode: '文生视频', use_scene_duration: false, [marker]: '1.1.3', _suanli_legacy_defaults_pending: true});
    assert.equal(app.collected().duration, 15); assert.equal(app.collected().aspect_ratio, '16:9'); assert.equal(app.collected().timeout, 900); assert.equal(app.collected().generation_mode, '文生视频'); assert.equal(app.collected()[marker], current);
    assert.equal(app.saves.length, 1); assert.equal(app.saves[0][marker], current);
  });
  await test('duration changes save numbers and never permit scene-duration override', async () => {
    const app = await boot({duration: 30, use_scene_duration: true, [marker]: current}); app.nodes.duration.value = '15'; await Promise.all(app.nodes.duration.emit('change'));
    assert.deepEqual(app.singleSaves, [{key: 'duration', value: 15}]); assert.equal(app.collected().duration, 15); assert.equal(app.collected().use_scene_duration, false);
    for (const id of ['modelSearch', 'familyFilter', 'seriesFilter', 'pluginUpdateInfo', 'pluginCurrentVersion', 'checkPluginUpdate']) assert.ok(!(id in app.collected()));
  });
  await test('checking before host initialization fails locally without backend calls', async () => {
    const app = await boot({}, {ready: false}); await app.nodes.checkPluginUpdate.click(); assert.equal(app.calls.length, 0); assert.equal(app.nodes.downloadPluginUpdate.disabled, true); assert.ok(app.nodes.pluginUpdateStatus.textContent.includes('尚未连接'));
  });
  await test('model IDs with markup remain literal text through selection and rendering', async () => {
    const id = '<img src=x onerror=alert(1)> sd2.5';
    const app = await boot({model: id}, {handlers: {get_models: () => ({ok: true, source: 'cache', models: [id]})}});
    const row = app.rows()[0]; assert.equal(row.querySelector('.model-name').textContent, id); assert.equal(row.querySelectorAll('IMG').length, 0); await row.click(); assert.equal(app.nodes.currentModel.value, id);
  });
  console.log('Completed ' + checks + ' production UI DOM checks.');
})().catch(error => { console.error(error); process.exitCode = 1; });
