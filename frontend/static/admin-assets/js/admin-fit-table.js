(function () {
  'use strict';

  // Bảng sổ/ma trận của trang TSCĐ và CCDC: nếu vẫn rộng hơn vùng chứa thì thu nhỏ toàn bộ bảng (chữ + cột) cho vừa chiều ngang.
  // Chỉ thu nhỏ khi cần thu nhỏ ít (tỉ lệ >= MIN_RATIO) để chữ còn to, đọc được; bảng quá rộng thì giữ cỡ chữ và để vùng chứa cuộn ngang.
  var MIN_RATIO = 0.92;
  var SELECTOR = '.table-responsive > table.depr-table, .table-responsive > table.pe-table, .table-responsive > table.fit-table';

  function fit(table) {
    var wrap = table.parentElement;
    if (!wrap || !wrap.clientWidth) return;
    table.style.zoom = '';
    var natural = table.getBoundingClientRect().width;
    var available = wrap.clientWidth;
    if (natural > available + 1) {
      var ratio = Math.floor((available / natural) * 0.985 * 1000) / 1000;
      if (ratio >= (parseFloat(table.getAttribute('data-fit-min')) || MIN_RATIO)) table.style.zoom = String(ratio);
    }
  }

  function fitAll() {
    document.querySelectorAll(SELECTOR).forEach(fit);
  }

  var timer = null;
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(fitAll, 120);
  }

  window.addEventListener('resize', schedule);
  window.addEventListener('load', fitAll);
  if (document.readyState === 'complete') fitAll();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitAll);
})();
