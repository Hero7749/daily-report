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
