/**
 * TypeTale Plugin SDK - postMessage 通信封装
 *
 * 在插件 HTML 中引入此文件，即可轻松与主应用通信。
 *
 * 用法:
 *   <script src="../../../plugin-sdk.js"></script>
 *   或: <script src="PLUGIN_SDK_JS"></script>
 *
 * API:
 *   PluginSDK.onReady(callback)        - 主应用初始化完成后回调，callback(params)
 *   PluginSDK.saveParam(key, value)     - 保存单个参数
 *   PluginSDK.saveParams(params)        - 批量保存参数 (Object)
 *   PluginSDK.getParams()               - 获取当前全部参数 (Promise)
 *   PluginSDK.sendAction(action, data)  - 向主应用发送自定义动作
 *   PluginSDK.onAction(action, callback)- 监听主应用发来的自定义动作
 *   PluginSDK.onRequestParams(callback) - 注册参数收集回调（弹窗确认场景）
 *   PluginSDK.onProgress(callback)      - 监听宿主进度，callback({percent, text})
 *   PluginSDK.onPartial(callback)       - 监听结构化分步更新，callback(data)
 *   PluginSDK.onHost(callback)          - 监听宿主只读数据，callback(host)
 *   PluginSDK.host                      - 宿主只读数据(模型族表等)
 *   PluginSDK.log(msg)                  - 在主应用控制台输出日志
 *
 * 模型族相关(细节见 GENERATION_PLUGIN_DEV_GUIDE.md §6.5):
 *   PluginSDK.getModelFamilies()        - 可选族表(完整定义，含用户自建族)
 *   PluginSDK.getModelFamily()          - 当前生效族的完整定义
 *   PluginSDK.getModelFamilyId()        - 当前生效族 id
 *   PluginSDK.setModelFamily(id)        - 选定族并落盘(等价 saveParam('model_family', id))
 *   PluginSDK.ownsModelFamilyUI()       - 族控件是否该由插件自己画
 *
 * 宿主只读数据 host 目前包含:
 *   model_family_kind      - 'image' | 'video'，该插件类型对应的族类别
 *   model_families         - 可选族表，每项是完整族定义:
 *                            {id, name, builtin, expand_chips, chip_tokens,
 *                             prefix_tokens, prefix_template, emit_prefix,
 *                             merge_labels, limits}
 *   model_family_inferred  - 按模型名推断的族(没手动选过时的默认值)
 *   model_family_stored    - 已落盘的族 id；空串表示用户没手动选过
 *   model_family_active    - 当前实际生效的族 id (stored || inferred)
 *   model_family           - 当前生效族的完整定义
 *   model_family_ui        - 'host' | 'self'，族控件由谁渲染。默认 'host'(宿主在
 *                            侧栏画一个下拉)；在插件 info.json 里写
 *                            "model_family_ui": "self" 可让宿主让位，由插件自己画
 *   model_name             - 宿主识别到的模型名；为空表示该插件没有模型字段
 */
;(function (root) {
  'use strict'

  var _params = {}
  var _readyCallbacks = []
  var _actionHandlers = {}
  var _pendingActions = {}
  var _pendingRequests = {}
  var _requestParamsCallback = null
  var _progressCallbacks = []
  var _partialCallbacks = []
  var _hostCallbacks = []
  var _reqId = 0
  var _ready = false
  var _theme = null
  var _host = {}

  function _postToHost(msg) {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage(msg, '*')
    }
  }

  function _generateId() {
    return '__psdk_' + (++_reqId) + '_' + Date.now()
  }

  function _settlePendingAction(requestId, result) {
    if (!requestId || !_pendingActions[requestId]) return false
    var pending = _pendingActions[requestId]
    if (pending.timer) clearTimeout(pending.timer)
    pending.resolve(result || {})
    delete _pendingActions[requestId]
    return true
  }

  function _settleFirstPendingAction(action, result) {
    if (!action) return false
    var keys = Object.keys(_pendingActions)
    for (var i = 0; i < keys.length; i++) {
      var pending = _pendingActions[keys[i]]
      if (pending && pending.action === action) {
        return _settlePendingAction(keys[i], result)
      }
    }
    return false
  }

  function _extractRequestId(data) {
    if (!data || typeof data !== 'object') return ''
    return data.requestId || data.request_id ||
      (data.request_meta && data.request_meta.requestId) ||
      (data.data && (data.data.requestId || data.data.request_id)) ||
      (data.result && (data.result.requestId || data.result.request_id)) ||
      ''
  }

  function _applyHost(host) {
    if (!host || typeof host !== 'object') return
    _host = host
    _hostCallbacks.forEach(function (cb) {
      try { cb(_host) } catch (e) { console.error('[PluginSDK] onHost error:', e) }
    })
  }

  function _applyTheme(theme) {
    if (!theme || typeof theme !== 'object') return
    _theme = theme
    var root = document.documentElement
    Object.keys(theme).forEach(function (key) {
      var value = theme[key]
      if (typeof value !== 'string' || !value.trim()) return
      root.style.setProperty(key, value)
    })
  }

  window.addEventListener('message', function (event) {
    var data = event.data
    if (!data || typeof data !== 'object') return
    if (data.__typetale_plugin !== true) return

    switch (data.type) {
      case 'init_params':
        _params = data.params || {}
        if (data.theme) _applyTheme(data.theme)
        if (data.host) _applyHost(data.host)
        _ready = true
        _readyCallbacks.forEach(function (cb) {
          try { cb(_params) } catch (e) { console.error('[PluginSDK] onReady error:', e) }
        })
        _readyCallbacks = []
        break

      case 'params_updated':
        _params = data.params || {}
        if (data.theme) _applyTheme(data.theme)
        if (data.host) _applyHost(data.host)
        break

      case 'theme_updated':
        _applyTheme(data.theme || {})
        break

      case 'host_data':
        _applyHost(data.host || {})
        break

      case 'response':
        var rid = data.requestId
        if (rid && _pendingRequests[rid]) {
          _pendingRequests[rid](data.result)
          delete _pendingRequests[rid]
        }
        break

      case 'action_result':
        _settlePendingAction(_extractRequestId(data), data.result || data.data || {})
        break

      case 'action':
        var action = data.action
        var actionResult = data.data || data.result || {}
        var actionRequestId = _extractRequestId(data)
        if (!_settlePendingAction(actionRequestId, actionResult)) {
          _settleFirstPendingAction(action, actionResult)
        }
        if (action && _actionHandlers[action]) {
          _actionHandlers[action].forEach(function (cb) {
            try { cb(actionResult) } catch (e) { console.error('[PluginSDK] action handler error:', e) }
          })
        }
        break

      case 'host_progress':
        var progressPayload = {
          percent: (typeof data.percent === 'number') ? data.percent : null,
          text: data.text || '',
          action: data.action || '',
          requestId: data.requestId || '',
          text_metrics: data.text_metrics || null
        }
        _progressCallbacks.forEach(function (cb) {
          try { cb(progressPayload) } catch (e) { console.error('[PluginSDK] onProgress error:', e) }
        })
        break

      case 'partial_update':
        var partialPayload = data.data || {}
        _partialCallbacks.forEach(function (cb) {
          try { cb(partialPayload) } catch (e) { console.error('[PluginSDK] onPartial error:', e) }
        })
        break

      case 'request_params':
        var result = {}
        if (typeof _requestParamsCallback === 'function') {
          try { result = _requestParamsCallback() || {} } catch (e) { console.error('[PluginSDK] onRequestParams error:', e) }
        } else {
          result = _params
        }
        _postToHost({
          __typetale_plugin: true,
          type: 'submit_params',
          params: result
        })
        break
    }
  })

  var PluginSDK = {
    onReady: function (callback) {
      if (typeof callback !== 'function') return
      if (_ready) {
        callback(_params)
      } else {
        _readyCallbacks.push(callback)
      }
    },

    saveParam: function (key, value) {
      _params[key] = value
      _postToHost({
        __typetale_plugin: true,
        type: 'save_param',
        key: key,
        value: value
      })
    },

    saveParams: function (params) {
      if (typeof params !== 'object') return
      Object.keys(params).forEach(function (k) { _params[k] = params[k] })
      _postToHost({
        __typetale_plugin: true,
        type: 'save_params',
        params: params
      })
    },

    getParams: function () {
      return new Promise(function (resolve) {
        var rid = _generateId()
        _pendingRequests[rid] = resolve
        _postToHost({
          __typetale_plugin: true,
          type: 'get_params',
          requestId: rid
        })
        setTimeout(function () {
          if (_pendingRequests[rid]) {
            _pendingRequests[rid](_params)
            delete _pendingRequests[rid]
          }
        }, 3000)
      })
    },

    sendAction: function (action, data, options) {
      return new Promise(function (resolve) {
        var requestId = _generateId()
        var rawTimeout = Number(options && options.timeoutMs)
        var timeoutMs = Number.isFinite(rawTimeout) ? rawTimeout : 600000
        _pendingActions[requestId] = {
          action: action,
          resolve: resolve,
          timer: timeoutMs > 0 ? setTimeout(function () {
            if (_pendingActions[requestId]) {
              delete _pendingActions[requestId]
              resolve({
                ok: false,
                error: '后端未返回插件动作结果：请确认宿主后端已启动，或稍后重试。'
              })
            }
          }, Math.max(1000, timeoutMs)) : null
        }
        _postToHost({
          __typetale_plugin: true,
          type: 'plugin_action',
          action: action,
          data: data || {},
          requestId: requestId
        })
      })
    },

    onAction: function (action, callback) {
      if (!_actionHandlers[action]) _actionHandlers[action] = []
      _actionHandlers[action].push(callback)
    },

    onRequestParams: function (callback) {
      _requestParamsCallback = callback
    },

    onProgress: function (callback) {
      if (typeof callback === 'function') _progressCallbacks.push(callback)
    },

    onPartial: function (callback) {
      if (typeof callback === 'function') _partialCallbacks.push(callback)
    },

    // 宿主数据可能在 onReady 之后才到（预设模式走单独的 host_data 补投），
    // 所以已有数据时立刻回调一次，之后每次更新再回调。
    onHost: function (callback) {
      if (typeof callback !== 'function') return
      _hostCallbacks.push(callback)
      if (_host && Object.keys(_host).length) {
        try { callback(_host) } catch (e) { console.error('[PluginSDK] onHost error:', e) }
      }
    },

    // ---- 模型族 ----
    // 族决定参考素材上限、@引用 token 格式、参考前缀怎么出。插件不要自己写死族表：
    // 用户可以在素材库里改内置族、也可以自建族，只有宿主知道当前有哪些。
    getModelFamilies: function () {
      return _host.model_families || []
    },

    // 参数里的值优先于宿主算出的 active：刚 setModelFamily 完、宿主还没回投
    // host_data 的那一刻，参数才是最新的。
    getModelFamilyId: function () {
      return (_params.model_family || '').trim() ||
        _host.model_family_active ||
        _host.model_family_inferred || 'none'
    },

    getModelFamily: function () {
      var id = PluginSDK.getModelFamilyId()
      if (_host.model_family && _host.model_family.id === id) return _host.model_family
      var list = _host.model_families || []
      for (var i = 0; i < list.length; i++) {
        if (list[i] && list[i].id === id) return list[i]
      }
      return null
    },

    setModelFamily: function (id) {
      PluginSDK.saveParam('model_family', id)
    },

    // 宿主默认会替没设计过族能力的插件画一个族下拉。插件在 info.json 里声明
    // "model_family_ui": "self" 后宿主让位，此函数返回 true，插件自己画。
    ownsModelFamilyUI: function () {
      return _host.model_family_ui === 'self'
    },

    log: function (msg) {
      _postToHost({
        __typetale_plugin: true,
        type: 'log',
        message: msg
      })
    },

    get params() {
      return _params
    },

    get theme() {
      return _theme
    },

    get host() {
      return _host
    }
  }

  // 构建即注入:宿主在 iframe URL 的 hash 里烤入了初始参数与主题(预设模式)。
  // 在脚本执行时同步读取,使初始参数成为“文档加载的一部分”,不依赖随后到达的
  // init_params 消息(其投递受 iframe 拆建/窗口身份/时机影响,曾导致表单空白)。
  try {
    var _initMatch = /(?:^#|&)__tt_init=([^&]*)/.exec(window.location.hash || '')
    if (_initMatch) {
      var _seed = JSON.parse(decodeURIComponent(_initMatch[1]))
      if (_seed && typeof _seed === 'object') {
        if (_seed.params && typeof _seed.params === 'object') _params = _seed.params
        if (_seed.theme) _applyTheme(_seed.theme)
        if (_seed.host && typeof _seed.host === 'object') _host = _seed.host
        _ready = true
      }
    }
  } catch (e) {}

  _postToHost({ __typetale_plugin: true, type: 'plugin_ready' })

  root.PluginSDK = PluginSDK
})(window)
