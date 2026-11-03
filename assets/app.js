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

    // 欄位 4 個以上的表格，在手機上改成卡片
    if (heads.length >= 4) {
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
