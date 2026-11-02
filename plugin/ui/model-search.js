(function (root) {
  'use strict';
  var families = ['SD2.0', 'SD2.5', 'SD2.5 Pro', 'MiniMax', 'Wan3', 'Grok', '视频编辑', '其他'];
  function normalizedMap(value) {
    var raw = String(value == null ? '' : value), chars = [], spans = [], offset = 0;
    Array.from(raw).forEach(function (character) {
      var start = offset; offset += character.length;
      Array.from(character.normalize('NFKC').toLowerCase()).forEach(function (letter) {
        if (/[\p{L}\p{N}]/u.test(letter)) { chars.push(letter); spans.push([start, offset]); }
      });
    });
    var text = chars.join(''), position = 0;
    while ((position = text.indexOf('过真人', position)) !== -1) { chars[position + 1] = '人'; chars[position + 2] = '脸'; position += 3; }
    return {text:chars.join(''), spans:spans};
  }
  function normalize(value) { return normalizedMap(value).text; }
  function queryTokens(query) {
    return String(query || '').normalize('NFKC').replace(/\bsd\s*(\d)\s*\.\s*(\d)/gi, 'sd$1.$2')
      .replace(/\bmini[\s_-]*max[\s_-]*h\s*3/gi, 'minimaxh3')
      .split(/[\s,，;；]+/).map(normalize).filter(Boolean);
  }
  function familyOf(id) {
    var n = normalize(id);
    if (/videoediting|视频编辑/.test(n)) return '视频编辑';
    if (/sd25pro/.test(n)) return 'SD2.5 Pro';
    if (/sd25/.test(n)) return 'SD2.5';
    if (/sd20/.test(n)) return 'SD2.0';
    if (/minimax/.test(n)) return 'MiniMax';
    if (/wan3/.test(n)) return 'Wan3';
    if (/grok/.test(n)) return 'Grok';
    return '其他';
  }
  function seriesOf(id) { var match = String(id).match(/^(\d+系列)\//); return match ? match[1] : /^专享/.test(id) ? '专享' : '其他'; }
  function catalog(items) {
    var seen = new Set(), result = [];
    (Array.isArray(items) ? items : []).forEach(function (item) {
      var id = typeof item === 'string' ? item : item && item.id;
      if (typeof id !== 'string' || !id.trim() || seen.has(id)) return;
      seen.add(id);
      var family = item && typeof item === 'object' && families.indexOf(item.family) !== -1 ? item.family : familyOf(id);
      var series = item && typeof item === 'object' && typeof item.series === 'string' && item.series ? item.series : seriesOf(id);
      result.push({id:id, name:typeof item.name === 'string' ? item.name : id, family:family, series:series,
        classification:item && item.classification || (family === '其他' ? 'unknown' : 'name')});
    });
    return result;
  }
  function tokenIndex(haystack, token, from) {
    var searchable = haystack;
    if (token.indexOf('卡脸') !== -1 && token.indexOf('不卡脸') === -1) searchable = searchable.replace(/不卡脸/g, '不\u0000\u0000');
    return searchable.indexOf(token, from || 0);
  }
  function filter(items, query, family, series) {
    var tokens = queryTokens(query), exact = normalize(query), rawQuery = String(query || '').trim();
    return items.map(function (item, index) { return {item:item, index:index}; }).filter(function (entry) {
      var item = entry.item;
      if ((family && item.family !== family) || (series && item.series !== series)) return false;
      var idText = normalize(item.id), nameText = normalize(item.name);
      return tokens.every(function (token) { return tokenIndex(idText, token) !== -1 || tokenIndex(nameText, token) !== -1; });
    }).sort(function (a, b) {
      var aExact = rawQuery && (a.item.id === rawQuery || a.item.name === rawQuery) ? 2 : exact && (normalize(a.item.id) === exact || normalize(a.item.name) === exact) ? 1 : 0;
      var bExact = rawQuery && (b.item.id === rawQuery || b.item.name === rawQuery) ? 2 : exact && (normalize(b.item.id) === exact || normalize(b.item.name) === exact) ? 1 : 0;
      return bExact - aExact || a.index - b.index;
    }).map(function (entry) { return entry.item; });
  }
  function highlightRanges(text, query) {
    var map = normalizedMap(text), ranges = [];
    queryTokens(query).forEach(function (token) {
      var position = 0, index;
      while ((index = tokenIndex(map.text, token, position)) !== -1) {
        var first = map.spans[index], last = map.spans[index + token.length - 1];
        if (first && last) ranges.push([first[0], last[1]]);
        position = index + Math.max(token.length, 1);
      }
    });
    ranges.sort(function (a, b) { return a[0] - b[0]; });
    return ranges.reduce(function (merged, range) {
      var previous = merged[merged.length - 1];
      if (previous && range[0] <= previous[1]) previous[1] = Math.max(previous[1], range[1]); else merged.push(range.slice());
      return merged;
    }, []);
  }
  var api = {families:families, normalize:normalize, queryTokens:queryTokens, familyOf:familyOf, seriesOf:seriesOf, catalog:catalog, filter:filter, highlightRanges:highlightRanges};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.SuanliSearch = api;
})(typeof window !== 'undefined' ? window : globalThis);
