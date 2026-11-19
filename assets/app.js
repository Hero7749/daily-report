// 報告頁面的小幫手：表格包裝、手機卡片化、漲紅跌綠自動上色
(function () {
  var body = document.querySelector('.report-body') || document.querySelector('.latest-excerpt');
  if (!body) return;

  // 帶正負號的數字，例如 +1.23%、-0.5%、−12 bp、▲3.2%、+120 億
  var SIGNED = /^[▲▼△▽]?\s*([+\-−–])\s*\d[\d,]*(\.\d+)?\s*(%|％|bp|bps|點|億|萬|張|元)?/i;
  var ARROW = /^([▲△]|[▼▽])\s*\d/;
  var NUMERIC = /^[▲▼△▽+\-−–$＄]?\s*\d[\d,]*(\.\d+)?\s*(%|％|bp|bps|點|億|萬|張|元|x|倍)?$/i;
  var PCT = /(\d+(\.\d+)?)\s*[%％]/;

  function classify(td) {
    var t = td.textContent.trim();
    if (!t || t === '-' || t === '—') return;
    if (NUMERIC.test(t)) td.classList.add('num');

    var dir = 0;
    var m = t.match(SIGNED);
    if (m) dir = (m[1] === '+') ? 1 : -1;
    else {
      var a = t.match(ARROW);
      if (a) dir = /[▲△]/.test(a[1]) ? 1 : -1;
    }
    if (!dir) return;

    td.classList.add(dir > 0 ? 'pos' : 'neg');
    var p = t.match(PCT);
    if (p && parseFloat(p[1]) >= 5) td.classList.add('big');
  }

  // ── 週曆 ───────────────────────────────────────────────
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  var SESSIONS = [
    { re: /盤前/, key: 'pre', label: '盤前', icon: '🌅' },
    { re: /盤中/, key: 'mid', label: '盤中', icon: '☀️' },
    { re: /盤後/, key: 'post', label: '盤後', icon: '🌙' },
    { re: /全天|未定|📌/, key: 'all', label: '全天／時段待確認', icon: '📌' }
  ];
  var TICKER = /^([A-Z][A-Z0-9.]{0,5}(?:\s*[、,／\/]\s*[A-Z][A-Z0-9.]{0,5})*)(?=\s|$|（)\s*(.*)$/;
  var TIME = /(\d{1,2}:\d{2}(?:\+1)?)/;

  function hue(s) {
    var h = 0;
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
    return h;
  }

  // 「📈 AMD 財報（會議 06:00+1）」→ [{icon, ticker, name, note, time, est}]
  function parseItem(raw) {
    var t = raw.replace(/\s+/g, ' ').trim();
    if (!t || t === '—' || t === '-') return [];
    var m = t.match(/^((?:[^\w\s\u3400-\u9fff（(]|\uFE0F|\u200D)+)\s*/);
    var icon = m ? m[1].trim() : '';
    var rest = m ? t.slice(m[0].length) : t;
    var tw = /🇹🇼/.test(icon);
    if (tw) icon = icon.replace(/🇹🇼/g, '').trim() || '🇹🇼';
    var est = /預估/.test(rest);
    var tm = rest.match(TIME);
    var time = tm ? tm[1] : '';
    var call = time && /會議/.test(rest);
    var clean = rest.replace(/（[^）]*）/g, function (x) {
      return /預估|會議|\d{1,2}:\d{2}/.test(x) ? '' : x;
    }).replace(TIME, '').replace(/\s+/g, ' ').trim();

    var tk = /🏛|💰|⚖️|🗳/.test(icon) ? null : clean.match(TICKER);
    if (tk) {
      var note = tk[2].trim();
      return tk[1].split(/\s*[、,／\/]\s*/).map(function (sym) {
        return { icon: icon, ticker: sym, name: note, time: time, call: call, est: est, tw: tw };
      });
    }
    return [{ icon: icon, ticker: '', name: clean, time: time, call: call, est: est, tw: tw }];
  }

  function renderItem(it) {
    var li = el('li', 'wc-item' + (it.est ? ' est' : '') + (it.ticker ? '' : ' event'));
    var logo = el('span', 'wc-logo');
    if (it.ticker) {
      logo.style.setProperty('--h', hue(it.ticker));
      var img = new Image();
      img.alt = '';
      img.loading = 'lazy';
      img.src = 'https://financialmodelingprep.com/image-stock/' + encodeURIComponent(it.ticker) + '.png';
      img.onload = function () { if (img.naturalWidth > 1) img.classList.add('ok'); };
      img.onerror = function () { img.remove(); logo.classList.add('mono'); };
      logo.textContent = it.ticker.slice(0, 2);
      logo.appendChild(img);
    } else {
      logo.classList.add('emoji');
      logo.textContent = it.tw ? '🇹🇼' : (it.icon || '•');
    }
    li.appendChild(logo);

    var main = el('span', 'wc-main');
    var row = el('span', 'wc-row');
    if (it.ticker) {
      var a = el('a', 'wc-sym', it.ticker);
      a.href = 'https://finance.yahoo.com/quote/' + encodeURIComponent(it.ticker);
      a.target = '_blank';
      a.rel = 'noopener';
      row.appendChild(a);
    } else {
      row.appendChild(el('span', 'wc-title', it.name));
    }
    if (it.est) row.appendChild(el('span', 'wc-est', '預估'));
    main.appendChild(row);
    var sub = el('span', 'wc-sub');
    if (it.ticker) sub.appendChild(el('span', 'wc-name', (it.icon ? it.icon + ' ' : '') + (it.name || '')));
    if (it.time) sub.appendChild(el('span', 'wc-time' + (/\+1/.test(it.time) ? ' next' : ''), (it.call ? '會議 ' : '') + it.time));
    if (sub.childNodes.length) main.appendChild(sub);
    li.appendChild(main);
    return li;
  }

  function buildCalendar(table) {
    var heads = Array.prototype.map.call(table.querySelectorAll('thead th'), function (th) { return th.textContent.trim(); });
    var days = heads.slice(1).map(function (h) {
      var d = h.match(/(\d{1,2}\/\d{1,2})/);
      var w = h.match(/[（(]([一二三四五六日])[）)]/);
      return { date: d ? d[1] : h, wd: w ? w[1] : '', today: /📍|今天/.test(h), sessions: {} };
    });
    var count = 0;
    table.querySelectorAll('tbody tr').forEach(function (tr) {
      var cells = tr.children;
      var label = cells[0] ? cells[0].textContent : '';
      var ses = SESSIONS.filter(function (s) { return s.re.test(label); })[0] || SESSIONS[3];
      for (var i = 1; i < cells.length && i - 1 < days.length; i++) {
        var parts = cells[i].innerHTML.split(/<br\s*\/?>/i).map(function (h) {
          var tmp = document.createElement('div');
          tmp.innerHTML = h;
          return tmp.textContent;
        });
        var items = [];
        parts.forEach(function (p) { items = items.concat(parseItem(p)); });
        if (items.length) {
          days[i - 1].sessions[ses.key] = (days[i - 1].sessions[ses.key] || []).concat(items);
          count += items.length;
        }
      }
    });
    if (!count) return null;

    var root = el('div', 'wcal');
    root.style.setProperty('--cols', days.length);
    days.forEach(function (d) {
      var col = el('section', 'wc-day' + (d.today ? ' today' : ''));
      var hd = el('header', 'wc-head');
      hd.appendChild(el('span', 'wc-wd', d.wd ? '週' + d.wd : ''));
      hd.appendChild(el('span', 'wc-date', d.date));
      if (d.today) hd.appendChild(el('span', 'wc-today', '今天'));
      col.appendChild(hd);
      var any = false;
      SESSIONS.forEach(function (s) {
        var items = d.sessions[s.key];
        if (!items || !items.length) return;
        any = true;
        var box = el('div', 'wc-ses wc-' + s.key);
        box.appendChild(el('h4', null, s.icon + ' ' + s.label));
        var ul = el('ul');
        items.forEach(function (it) { ul.appendChild(renderItem(it)); });
        box.appendChild(ul);
        col.appendChild(box);
      });
      if (!any) col.appendChild(el('p', 'wc-empty', '無重大事件'));
      root.appendChild(col);
    });
    return root;
  }

  // 週曆表格（首欄「時段」、其餘欄為日期）→ 轉成財報週曆卡片
  body.querySelectorAll('table').forEach(function (table) {
    var th = table.querySelectorAll('thead th');
    var dateHeads = Array.prototype.slice.call(th, 1).filter(function (h) { return /\d{1,2}\/\d{1,2}/.test(h.textContent); }).length;
    var sesRows = Array.prototype.filter.call(table.querySelectorAll('tbody tr'), function (tr) {
      return tr.children[0] && /盤前|盤中|盤後|全天/.test(tr.children[0].textContent);
    }).length;
    if (th.length >= 2 && dateHeads >= 1 && dateHeads === th.length - 1 && (/時段/.test(th[0].textContent) || sesRows >= 2)) {
      var cal = buildCalendar(table);
      if (cal) table.parentNode.replaceChild(cal, table);
    }
  });

  // ── 趨勢圖：首欄表頭以「📉」開頭的表格 → 每欄一張小圖（共用日期軸）──────────
  // 欄名含「買賣超」畫成紅綠柱狀（台股慣例：紅＝正、綠＝負），其餘畫折線；原表格收進「查看數據表」。
  var SVGNS = 'http://www.w3.org/2000/svg';

  function sv(tag, attrs, parent) {
    var e = document.createElementNS(SVGNS, tag);
    for (var k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function f1(n) { return (Math.round(n * 10) / 10).toString(); }
  function parseVal(t) {
    t = (t || '').replace(/[,\s]/g, '').replace(/[−–]/g, '-');
    var m = t.match(/^[+\-]?\d+(\.\d+)?/);
    return m ? parseFloat(m[0]) : null;
  }
  function decimalsOf(texts) {
    var d = 0;
    texts.forEach(function (t) { var m = (t || '').match(/\.(\d+)/); if (m) d = Math.max(d, Math.min(2, m[1].length)); });
    return d;
  }
  function fmtN(v, d, sign) {
    var s = Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
    return (v < 0 ? '-' : (sign && v > 0 ? '+' : '')) + s;
  }
  // 刻度：在資料範圍（上下各留 8%）內取 2–4 個整數刻度，不把軸硬撐到整數邊界
  function niceTicks(lo, hi) {
    var raw = (hi - lo) / 3, p = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10)), n = raw / p;
    var step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
    var out = [];
    for (var v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v / step) * step);
    return { ticks: out, step: step };
  }
  function textW(s) {
    var w = 0;
    for (var i = 0; i < s.length; i++) w += /[㐀-鿿]/.test(s.charAt(i)) ? 10.5 : 6.4;
    return w;
  }
  function barPath(x, yTop, w, h, r, up) {
    r = Math.min(r, h, w / 2);
    if (up) {
      return 'M' + f1(x) + ' ' + f1(yTop + h) + 'V' + f1(yTop + r) + 'Q' + f1(x) + ' ' + f1(yTop) + ' ' + f1(x + r) + ' ' + f1(yTop) +
        'H' + f1(x + w - r) + 'Q' + f1(x + w) + ' ' + f1(yTop) + ' ' + f1(x + w) + ' ' + f1(yTop + r) + 'V' + f1(yTop + h) + 'Z';
    }
    var yb = yTop + h;
    return 'M' + f1(x) + ' ' + f1(yTop) + 'V' + f1(yb - r) + 'Q' + f1(x) + ' ' + f1(yb) + ' ' + f1(x + r) + ' ' + f1(yb) +
      'H' + f1(x + w - r) + 'Q' + f1(x + w) + ' ' + f1(yb) + ' ' + f1(x + w) + ' ' + f1(yb - r) + 'V' + f1(yTop) + 'Z';
  }
  function lastIndex(vals) {
    for (var i = vals.length - 1; i >= 0; i--) if (vals[i] != null) return i;
    return -1;
  }

  // 週線／月線：讀籌碼歷史檔（assets/data/tw-chips.csv），只取到該篇報告當天，再按週／月彙整
  var WINS = { d: null, w: [[4, '4 週'], [52, '52 週']], m: [[3, '3 個月'], [12, '12 個月']] };
  var KEEP = { w: 104, m: 1000 };
  function histUrl() {
    var sc = document.querySelector('script[src*="assets/app.js"]');
    return sc ? sc.src.replace(/app\.js(\?.*)?$/, 'data/tw-chips.csv') : null;
  }
  function reportDate() {
    var t = document.querySelector('.report-meta time');
    return t ? (t.getAttribute('datetime') || '').slice(0, 10) : '';
  }
  function aggregate(rows, names, series, key) {
    var groups = [], idx = {};
    rows.forEach(function (r) {
      var d = r[0], k;
      if (key === 'm') k = d.slice(0, 7);
      else {
        var dt = new Date(d + 'T00:00:00Z'), back = (dt.getUTCDay() + 6) % 7;
        k = new Date(dt.getTime() - back * 864e5).toISOString().slice(0, 10);
      }
      if (!(k in idx)) { idx[k] = groups.length; groups.push({ last: d, rows: [] }); }
      var g = groups[idx[k]];
      g.last = d;
      g.rows.push(r);
    });
    groups = groups.slice(-KEEP[key]);
    var vals = series.map(function (s) {
      var col = names.indexOf(s.name);
      return groups.map(function (g) {
        if (col < 0) return null;
        var xs = g.rows.map(function (r) { return r[col]; }).filter(function (v) { return v != null; });
        if (!xs.length) return null;
        return s.bar ? xs.reduce(function (a, b) { return a + b; }, 0) : xs[xs.length - 1];
      });
    });
    var axis = groups.map(function (g) { return key === 'm' ? g.last.slice(0, 7).replace('-', '/') : g.last.replace(/-/g, '/'); });
    var tips = groups.map(function (g) { return key === 'm' ? g.last.slice(0, 7).replace('-', '/') + ' 月' : g.last.replace(/-/g, '/') + ' 當週'; });
    return { key: key, axis: axis, tips: tips, vals: vals };
  }

  function buildTrend(table) {
    var heads = Array.prototype.map.call(table.querySelectorAll('thead th'), function (th) { return th.textContent.trim(); });
    var trs = Array.prototype.slice.call(table.querySelectorAll('tbody tr'));
    var dates = trs.map(function (tr) {
      var t = tr.children[0] ? tr.children[0].textContent.trim() : '';
      var m = t.match(/(\d{1,2})[\/\-](\d{1,2})$/);
      return m ? ('0' + m[1]).slice(-2) + '/' + ('0' + m[2]).slice(-2) : t;
    });
    var series = [], dvals = [];
    heads.forEach(function (name, c) {
      if (c === 0) return;
      var texts = trs.map(function (tr) { return tr.children[c] ? tr.children[c].textContent.trim() : ''; });
      var vals = texts.map(parseVal);
      if (vals.filter(function (v) { return v != null; }).length < 2) return;
      var um = name.match(/[（(]([^）)]+)[）)]\s*$/);
      series.push({ name: name, short: name.replace(/\s*[（(][^）)]*[）)]\s*$/, ''), unit: um ? um[1] : '',
        d: decimalsOf(texts), bar: /買賣超/.test(name) });
      dvals.push(vals);
    });
    if (!series.length || dates.length < 2) return null;
    var views = { d: { key: 'd', axis: dates, tips: dates, vals: dvals } };
    var view = views.d;

    var fig = el('figure', 'trend');
    var bar = el('div', 'tr-views');
    bar.setAttribute('role', 'group');
    bar.setAttribute('aria-label', '週期');
    var btns = {};
    [['d', '日'], ['w', '週'], ['m', '月']].forEach(function (p) {
      var bt = el('button', 'tr-view', p[1]);
      bt.type = 'button';
      bt.setAttribute('aria-pressed', p[0] === 'd' ? 'true' : 'false');
      if (p[0] !== 'd') bt.disabled = true;
      bt.addEventListener('click', function () { setView(p[0], true); });
      btns[p[0]] = bt;
      bar.appendChild(bt);
    });
    var grid = el('div', 'trend-grid');
    var tip = el('div', 'tr-tip');
    tip.hidden = true;
    tip.setAttribute('aria-hidden', 'true');
    fig.appendChild(bar);
    fig.appendChild(grid);
    fig.appendChild(tip);
    var cur = null;

    var charts = series.map(function (s, k) {
      var box = el('section', 'tr-chart');
      box.tabIndex = 0;
      var head = el('header', 'tr-head');
      head.appendChild(el('span', 'tr-name', s.name));
      var lastEl = el('span', 'tr-last');
      head.appendChild(lastEl);
      var deltas = el('div', 'tr-deltas');
      head.appendChild(deltas);
      box.appendChild(head);
      var svg = sv('svg', { 'class': 'tr-svg', role: 'img', focusable: 'false' });
      box.appendChild(svg);
      grid.appendChild(box);
      return { s: s, k: k, box: box, svg: svg, lastEl: lastEl, deltas: deltas, li: -1, geom: null, cross: null, hdot: null, bars: [] };
    });

    function fillHead(c) {
      var s = c.s, vals = view.vals[c.k], li = lastIndex(vals), n = vals.length;
      c.li = li;
      c.lastEl.textContent = li >= 0 ? fmtN(vals[li], s.d, s.bar) : '—';
      if (s.unit) c.lastEl.appendChild(el('small', null, ' ' + s.unit));
      c.deltas.textContent = '';
      function addDelta(label, v) {
        if (v == null || isNaN(v)) return;
        var sp = el('span', 'tr-d' + (v > 0 ? ' pos' : v < 0 ? ' neg' : ''));
        sp.appendChild(el('span', 'k', label + ' '));
        sp.appendChild(document.createTextNode(fmtN(v, s.d, true)));
        c.deltas.appendChild(sp);
      }
      var wins = WINS[view.key] || [[5, '5 日'], [n, n + ' 日']];
      wins.forEach(function (w) {
        if (li < 0) return;
        if (s.bar) {
          if (li - w[0] + 1 < 0) return;
          var t = 0;
          for (var i = li - w[0] + 1; i <= li; i++) t += vals[i] || 0;
          addDelta(w[1] + '合計', t);
        } else {
          var j = li - w[0];
          if (w[0] === n && view.key === 'd') j = vals.findIndex(function (v) { return v != null; });
          if (j >= 0 && j < li && vals[j] != null) addDelta(w[1], vals[li] - vals[j]);
        }
      });
      c.svg.setAttribute('aria-label', s.name + '：' + view.axis[0] + ' 到 ' + view.axis[n - 1] +
        '，最新 ' + c.lastEl.textContent);
    }

    function draw(c) {
      var s = c.s, svg = c.svg, vals = view.vals[c.k], n = vals.length, axis = view.axis;
      var W = Math.max(200, Math.floor(svg.getBoundingClientRect().width || c.box.clientWidth - 24));
      var top = 8, plotH = 92, H = top + plotH + 20, axisY = top + plotH;
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
      svg.setAttribute('height', H);
      var present = vals.filter(function (v) { return v != null; });
      if (!present.length) { c.geom = null; return; }
      var min = Math.min.apply(null, present), max = Math.max.apply(null, present);
      if (s.bar) { min = Math.min(0, min); max = Math.max(0, max); }
      var pad = (max - min) * 0.08 || Math.abs(max) * 0.05 || 1;
      var lo = s.bar && min === 0 ? 0 : min - pad, hi = s.bar && max === 0 ? 0 : max + pad;
      var nt = niceTicks(lo, hi), ticks = nt.ticks;
      var wan = Math.max(Math.abs(lo), Math.abs(hi)) >= 100000;
      if (!ticks.length) ticks = [Math.round((lo + hi) / 2)];
      var labels = ticks.map(function (t) {
        return wan ? (t === 0 ? '0' : fmtN(t / 10000, nt.step % 10000 ? 1 : 0) + '萬') : fmtN(t, nt.step < 1 ? (nt.step < 0.1 ? 2 : 1) : 0);
      });
      var gutter = Math.ceil(Math.max.apply(null, labels.map(textW))) + 10;
      var plotW = W - gutter, band = plotW / n;
      var x = function (i) { return (i + 0.5) * band; };
      var y = function (v) { return top + (hi - v) / (hi - lo || 1) * plotH; };

      ticks.forEach(function (t, k) {
        var yy = Math.round(y(t)) + 0.5;
        sv('line', { x1: 0, x2: f1(plotW), y1: yy, y2: yy, 'class': t === 0 ? 'tr-grid zero' : 'tr-grid' }, svg);
        sv('text', { x: W - 1, y: yy + 3.5, 'text-anchor': 'end', 'class': 'tr-tick' }, svg).textContent = labels[k];
      });
      [0, Math.floor((n - 1) / 2), n - 1].forEach(function (i, k) {
        sv('text', { x: f1(k === 0 ? 0 : k === 2 ? plotW : x(i)), y: H - 4, 'text-anchor': k === 0 ? 'start' : k === 2 ? 'end' : 'middle', 'class': 'tr-tick' }, svg).textContent = axis[i];
      });

      c.bars = [];
      if (s.bar) {
        var bw = Math.max(1, Math.min(24, band - 2, band * 0.8)), y0 = y(0);
        vals.forEach(function (v, i) {
          if (v == null) return;
          var yv = y(v), h = Math.max(0.75, Math.abs(yv - y0));
          var p = sv('path', { d: barPath(x(i) - bw / 2, v >= 0 ? y0 - h : y0, bw, h, 4, v >= 0), 'class': 'tr-bar ' + (v >= 0 ? 'pos' : 'neg') }, svg);
          p.setAttribute('data-i', i);
          c.bars.push(p);
        });
      } else {
        var segs = [], seg = [];
        vals.forEach(function (v, i) {
          if (v == null) { if (seg.length) segs.push(seg); seg = []; } else seg.push([x(i), y(v)]);
        });
        if (seg.length) segs.push(seg);
        segs.forEach(function (pts) {
          var d = pts.map(function (p, k) { return (k ? 'L' : 'M') + f1(p[0]) + ' ' + f1(p[1]); }).join('');
          if (pts.length > 1) sv('path', { d: d + 'L' + f1(pts[pts.length - 1][0]) + ' ' + axisY + 'L' + f1(pts[0][0]) + ' ' + axisY + 'Z', 'class': 'tr-area' }, svg);
          sv('path', { d: d, 'class': 'tr-line' }, svg);
        });
        if (c.li >= 0) sv('circle', { cx: f1(x(c.li)), cy: f1(y(vals[c.li])), r: 4, 'class': 'tr-dot' }, svg);
      }
      c.cross = sv('line', { y1: top, y2: axisY, 'class': 'tr-cross', visibility: 'hidden' }, svg);
      c.hdot = s.bar ? null : sv('circle', { r: 4, 'class': 'tr-dot', visibility: 'hidden' }, svg);
      c.geom = { x: x, y: y, band: band, n: n };
    }

    function setHover(c, i) {
      if (!c.geom) return;
      var on = i != null;
      c.cross.setAttribute('visibility', on ? 'visible' : 'hidden');
      if (on) { c.cross.setAttribute('x1', f1(c.geom.x(i))); c.cross.setAttribute('x2', f1(c.geom.x(i))); }
      if (c.hdot) {
        var v = on ? view.vals[c.k][i] : null;
        c.hdot.setAttribute('visibility', v != null ? 'visible' : 'hidden');
        if (v != null) { c.hdot.setAttribute('cx', f1(c.geom.x(i))); c.hdot.setAttribute('cy', f1(c.geom.y(v))); }
      }
      c.bars.forEach(function (b) { b.classList.toggle('dim', on && +b.getAttribute('data-i') !== i); });
    }

    function show(i, src) {
      if (!src.geom) return;
      cur = i;
      charts.forEach(function (c) { setHover(c, i); });
      tip.textContent = '';
      tip.appendChild(el('div', 'd', view.tips[i]));
      series.forEach(function (s, k) {
        var v = view.vals[k][i], row = el('div', 'r');
        row.appendChild(el('span', 'key' + (s.bar ? ' bar ' + ((v || 0) >= 0 ? 'pos' : 'neg') : '')));
        row.appendChild(el('span', 'v', v == null ? '—' : fmtN(v, s.d, s.bar) + (s.unit ? ' ' + s.unit : '')));
        row.appendChild(el('span', 'n', s.short + (s.bar && view.key !== 'd' ? (view.key === 'w' ? '（週合計）' : '（月合計）') : '')));
        tip.appendChild(row);
      });
      tip.hidden = false;
      var fr = fig.getBoundingClientRect(), sr = src.svg.getBoundingClientRect();
      var gx = sr.left - fr.left + src.geom.x(i), tw = tip.offsetWidth;
      var left = gx + 14 + tw > fr.width ? gx - 14 - tw : gx + 14;
      tip.style.left = Math.max(0, Math.min(left, fr.width - tw)) + 'px';
      tip.style.top = (sr.top - fr.top + 2) + 'px';
    }
    function hide() {
      cur = null;
      charts.forEach(function (c) { setHover(c, null); });
      tip.hidden = true;
    }
    function idx(c, ev) {
      var r = c.svg.getBoundingClientRect();
      return Math.max(0, Math.min(c.geom.n - 1, Math.floor((ev.clientX - r.left) / c.geom.band)));
    }

    charts.forEach(function (c) {
      c.svg.addEventListener('pointermove', function (ev) { if (c.geom) show(idx(c, ev), c); });
      c.svg.addEventListener('pointerdown', function (ev) { if (c.geom) show(idx(c, ev), c); });
      c.svg.addEventListener('pointerleave', function (ev) { if (ev.pointerType === 'mouse') hide(); });
      c.box.addEventListener('focus', function () { show(cur == null ? c.li : cur, c); });
      c.box.addEventListener('blur', function () { if (!fig.contains(document.activeElement)) hide(); });
      c.box.addEventListener('keydown', function (ev) {
        var i = cur == null ? c.li : cur;
        if (ev.key === 'ArrowLeft') i = Math.max(0, i - 1);
        else if (ev.key === 'ArrowRight') i = Math.min(view.axis.length - 1, i + 1);
        else if (ev.key === 'Escape') { hide(); return; } else return;
        ev.preventDefault();
        show(i, c);
      });
    });
    document.addEventListener('pointerdown', function (ev) { if (!fig.contains(ev.target)) hide(); });

    var det = document.createElement('details');
    det.className = 'trend-data';
    det.appendChild(el('summary', null, '查看數據表'));
    table.parentNode.replaceChild(fig, table);
    det.appendChild(table);
    fig.appendChild(det);

    function render() {
      hide();
      charts.forEach(function (c) { fillHead(c); draw(c); });
    }
    function setView(key, remember) {
      if (!views[key]) return;
      view = views[key];
      Object.keys(btns).forEach(function (k) { btns[k].setAttribute('aria-pressed', k === key ? 'true' : 'false'); });
      render();
      if (remember) { try { localStorage.setItem('trendView', key); } catch (e) { /* 無痕模式 */ } }
    }
    render();
    var raf = 0, lastW = fig.clientWidth;
    window.addEventListener('resize', function () {
      if (fig.clientWidth === lastW) return;
      lastW = fig.clientWidth;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(function () { charts.forEach(draw); if (cur != null) charts.forEach(function (c) { setHover(c, cur); }); });
    });

    var url = histUrl();
    if (url && window.fetch) {
      fetch(url).then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); }).then(function (txt) {
        var lines = txt.trim().split(/\r?\n/), names = lines[0].split(','), cut = reportDate();
        var rows = lines.slice(1).map(function (l) {
          var cs = l.split(',');
          return [cs[0]].concat(cs.slice(1).map(function (v) { return v === '' ? null : parseFloat(v); }));
        }).filter(function (r) { return /^\d{4}-\d{2}-\d{2}$/.test(r[0]) && (!cut || r[0] <= cut); });
        if (rows.length < 20) throw new Error('too short');
        views.w = aggregate(rows, names, series, 'w');
        views.m = aggregate(rows, names, series, 'm');
        btns.w.disabled = btns.m.disabled = false;
        var saved = null;
        try { saved = localStorage.getItem('trendView'); } catch (e) { /* 無痕模式 */ }
        if (saved === 'w' || saved === 'm') setView(saved, false);
      }).catch(function () { btns.w.hidden = btns.m.hidden = true; });
    } else {
      btns.w.hidden = btns.m.hidden = true;
    }
    return fig;
  }

  body.querySelectorAll('table').forEach(function (table) {
    var th = table.querySelector('thead th');
    if (th && /^📉/.test(th.textContent.trim())) {
      try { buildTrend(table); } catch (e) { /* 畫圖失敗就保留原表格 */ }
    }
  });

  body.querySelectorAll('table').forEach(function (table) {
    var wrap = document.createElement('div');
    wrap.className = 'table-wrap';
    table.parentNode.insertBefore(wrap, table);
    wrap.appendChild(table);

    var heads = Array.prototype.map.call(table.querySelectorAll('thead th'), function (th) {
      return th.textContent.trim();
    });

    table.querySelectorAll('tbody tr').forEach(function (tr) {
      Array.prototype.forEach.call(tr.children, function (td, i) {
        if (heads[i]) td.setAttribute('data-label', heads[i]);
        classify(td);
      });
    });

    // 欄位 4 個以上的表格，在手機上改成卡片（趨勢圖的數據表維持表格，可橫向捲動）
    if (heads.length >= 4 && !table.closest('.trend-data')) {
      table.classList.add('cardify');
      wrap.classList.add('cards');
    }
  });

  // 標出「今日三重點」清單
  body.querySelectorAll('h2').forEach(function (h) {
    if (/三重點|本週重點|最需要留意/.test(h.textContent)) {
      var next = h.nextElementSibling;
      if (next && next.tagName === 'UL') next.classList.add('highlights');
    }
  });
})();
