(function () {
  'use strict';

  // Ô chọn tháng gốc của trình duyệt hiển thị tháng bằng tiếng Anh ("October 2026").
  // Với input[type=month][data-vi-month] ta giữ nguyên input gốc (ẩn, vẫn nằm trong form và giữ nguyên id/name/giá trị
  // dạng YYYY-MM) và thêm hai ô chọn "Tháng" / "Năm" bằng tiếng Việt, đồng bộ hai chiều.

  var valueDesc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');

  function option(value, text, selected) {
    var o = document.createElement('option');
    o.value = value;
    o.textContent = text;
    if (selected) o.selected = true;
    return o;
  }

  function enhance(input) {
    if (input.__viMonth) return;
    input.__viMonth = true;

    var now = new Date();
    var wrap = document.createElement('div');
    wrap.className = 'vi-month-picker';
    wrap.style.cssText = 'display:flex; gap:6px; width:100%;';

    var monthSel = document.createElement('select');
    var yearSel = document.createElement('select');
    [monthSel, yearSel].forEach(function (sel) {
      sel.className = input.className || 'form-control form-control-sm';
      sel.setAttribute('data-no-search', '');
      sel.style.minWidth = '0';
      if (input.disabled) sel.disabled = true;
    });
    monthSel.style.flex = '1 1 55%';
    yearSel.style.flex = '1 1 45%';
    monthSel.setAttribute('aria-label', 'Tháng');
    yearSel.setAttribute('aria-label', 'Năm');

    function fillYears(extraYear) {
      var current = now.getFullYear();
      var years = [];
      for (var y = current + 2; y >= current - 8; y--) years.push(y);
      if (extraYear && years.indexOf(extraYear) === -1) {
        years.push(extraYear);
        years.sort(function (a, b) { return b - a; });
      }
      yearSel.innerHTML = '';
      yearSel.appendChild(option('', 'Năm'));
      years.forEach(function (y) { yearSel.appendChild(option(String(y), String(y))); });
    }

    monthSel.appendChild(option('', 'Tháng'));
    for (var m = 1; m <= 12; m++) {
      monthSel.appendChild(option(String(m).padStart(2, '0'), 'Tháng ' + m));
    }
    fillYears(0);

    function showValue() {
      var match = /^(\d{4})-(\d{2})$/.exec(valueDesc.get.call(input) || '');
      if (match) {
        var year = parseInt(match[1], 10);
        if (!yearSel.querySelector('option[value="' + year + '"]')) fillYears(year);
        yearSel.value = match[1];
        monthSel.value = match[2];
      } else {
        yearSel.value = '';
        monthSel.value = '';
      }
    }

    function pushValue() {
      if (monthSel.value && !yearSel.value) yearSel.value = String(now.getFullYear());
      if (yearSel.value && !monthSel.value) monthSel.value = String(now.getMonth() + 1).padStart(2, '0');
      var next = monthSel.value && yearSel.value ? yearSel.value + '-' + monthSel.value : '';
      valueDesc.set.call(input, next);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    monthSel.addEventListener('change', pushValue);
    yearSel.addEventListener('change', pushValue);

    // Mã trang gán input.value = '2026-10' trực tiếp thì hai ô chọn cũng phải theo.
    Object.defineProperty(input, 'value', {
      configurable: true,
      get: function () { return valueDesc.get.call(this); },
      set: function (v) { valueDesc.set.call(this, v); showValue(); },
    });

    wrap.appendChild(monthSel);
    wrap.appendChild(yearSel);
    input.style.display = 'none';
    input.parentNode.insertBefore(wrap, input.nextSibling);
    if (input.form) {
      input.form.addEventListener('reset', function () { setTimeout(showValue, 0); });
    }
    showValue();
  }

  function scan(root) {
    if (!root || !root.querySelectorAll) return;
    if (root.matches && root.matches('input[type="month"][data-vi-month]')) enhance(root);
    root.querySelectorAll('input[type="month"][data-vi-month]').forEach(enhance);
  }

  function start() {
    scan(document.body);
    new MutationObserver(function (records) {
      records.forEach(function (r) { r.addedNodes.forEach(scan); });
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
