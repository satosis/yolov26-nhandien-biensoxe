/* Tra cứu BAZEM: thép hộp (Kích thước + Độ ly) và I/H (Tên hàng). Dùng ở trang Quy cách hàng hóa và Sổ bán hàng. */
(function () {
  function fold(str) {
    return (str || '')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[đĐ]/g, 'd')
      .toLowerCase()
      .trim();
  }

  function normSize(s) {
    return fold(s).replace(/[*×]/g, 'x').replace(/\s+/g, '');
  }

  function sizeKey(s) {
    // "100x200" và "200x100" coi là một; chuỗi khác giữ nguyên
    var n = normSize(s);
    var m = n.match(/^(\d+(?:[.,]\d+)?)x(\d+(?:[.,]\d+)?)$/);
    if (!m) return n;
    var a = parseFloat(m[1].replace(',', '.')), b = parseFloat(m[2].replace(',', '.'));
    return Math.min(a, b) + 'x' + Math.max(a, b);
  }

  function parseThickness(s) {
    var v = (s || '').trim().replace(/ly$/i, '').trim().replace(',', '.');
    if (!v) return null;
    var n = parseFloat(v);
    return isNaN(n) ? NaN : n;
  }

  function fmtThickness(n) {
    return n.toLocaleString('vi-VN', {minimumFractionDigits: 1, maximumFractionDigits: 3});
  }

  function fmtWeight(n) {
    return n.toLocaleString('vi-VN', {maximumFractionDigits: 3});
  }

  function shapeKey(s) {
    // bỏ dấu, bỏ khoảng trắng, * và × thành x, "phi"/"fi"/"φ"/"Φ" ở đầu thành "ø"
    var n = fold(s).replace(/\s+/g, '').replace(/[*×]/g, 'x');
    return n.replace(/^(phi|fi|φ)/, 'ø');
  }

  function shapeCore(s) {
    // bỏ "ø" ở đầu để "70" khớp "Ø70"
    return shapeKey(s).replace(/^ø/, '');
  }

  window.initGoodsSpecLookup = function (data, opts) {
    data = data || {};
    opts = opts || {};
    var boxData = data.box || [];
    var shapeData = data.shape || [];
    var isFloating = !!opts.floating;

    var lookupSizeEl = document.getElementById('lookupSize');
    var lookupThicknessEl = document.getElementById('lookupThickness');
    var lookupResultWrap = document.getElementById('lookupResultWrap');
    var lookupResultBody = document.getElementById('lookupResultBody');
    var sizeList = document.getElementById('lookupSizeList');
    var thickList = document.getElementById('lookupThicknessList');

    if (lookupSizeEl) {
      if (sizeList && thickList && boxData) {
        var seenNames = {};
        var thicknesses = [];
        var seenThick = {};

        for (var i = 0; i < boxData.length; i++) {
          var item = boxData[i];
          if (!seenNames[item.name]) {
            seenNames[item.name] = true;
            var optSize = document.createElement('option');
            optSize.value = item.name;
            sizeList.appendChild(optSize);
          }
          if (!seenThick[item.thickness]) {
            seenThick[item.thickness] = true;
            thicknesses.push(item.thickness);
          }
        }

        thicknesses.sort(function (a, b) { return a - b; });
        for (var j = 0; j < thicknesses.length; j++) {
          var optThick = document.createElement('option');
          optThick.value = fmtThickness(thicknesses[j]);
          thickList.appendChild(optThick);
        }
      }

      function runBoxLookup() {
        if (!lookupSizeEl || !lookupThicknessEl || !lookupResultWrap || !lookupResultBody) return;

        var sizeRaw = lookupSizeEl.value || '';
        var thickRaw = lookupThicknessEl.value || '';

        if (!sizeRaw.trim() && !thickRaw.trim()) {
          lookupResultWrap.style.display = 'none';
          lookupResultBody.textContent = '';
          return;
        }

        var sizeQ = sizeKey(sizeRaw);
        var tQ = parseThickness(thickRaw);

        var rows = boxData || [];

        if (sizeRaw.trim()) {
          var exactMatches = [];
          for (var i = 0; i < rows.length; i++) {
            if (sizeKey(rows[i].name) === sizeQ) {
              exactMatches.push(rows[i]);
            }
          }
          if (exactMatches.length > 0) {
            rows = exactMatches;
          } else {
            var normInput = normSize(sizeRaw);
            var partialMatches = [];
            for (var j = 0; j < rows.length; j++) {
              if (normSize(rows[j].name).indexOf(normInput) !== -1) {
                partialMatches.push(rows[j]);
              }
            }
            rows = partialMatches;
          }
        }

        if (tQ !== null) {
          if (isNaN(tQ)) {
            rows = [];
          } else {
            var thickMatches = [];
            for (var m = 0; m < rows.length; m++) {
              if (Math.abs(rows[m].thickness - tQ) < 1e-6) {
                thickMatches.push(rows[m]);
              }
            }
            rows = thickMatches;
          }
        }

        lookupResultWrap.style.display = '';
        lookupResultBody.textContent = '';

        if (rows.length === 0) {
          var emptyTr = document.createElement('tr');
          var emptyTd = document.createElement('td');
          emptyTd.colSpan = 3;
          emptyTd.className = 'text-center text-muted';
          emptyTd.textContent = 'Không tìm thấy';
          emptyTr.appendChild(emptyTd);
          lookupResultBody.appendChild(emptyTr);
        } else {
          for (var k = 0; k < rows.length; k++) {
            var row = rows[k];
            var tr = document.createElement('tr');

            var td1 = document.createElement('td');
            td1.className = 'font-weight-bold text-nowrap';
            td1.textContent = row.name;
            tr.appendChild(td1);

            var td2 = document.createElement('td');
            td2.className = 'text-right text-nowrap';
            td2.textContent = fmtThickness(row.thickness);
            tr.appendChild(td2);

            var td3 = document.createElement('td');
            td3.className = 'text-right text-nowrap font-weight-bold';
            td3.textContent = fmtWeight(row.weight);
            tr.appendChild(td3);

            lookupResultBody.appendChild(tr);
          }
        }
      }

      lookupSizeEl.addEventListener('input', runBoxLookup);
      if (lookupThicknessEl) lookupThicknessEl.addEventListener('input', runBoxLookup);

      if (isFloating) {
        lookupSizeEl.addEventListener('focus', runBoxLookup);
        lookupSizeEl.addEventListener('keydown', function (e) {
          if (e.key === 'Escape' || e.keyCode === 27) {
            if (lookupResultWrap) lookupResultWrap.style.display = 'none';
          }
        });

        if (lookupThicknessEl) {
          lookupThicknessEl.addEventListener('focus', runBoxLookup);
          lookupThicknessEl.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' || e.keyCode === 27) {
              if (lookupResultWrap) lookupResultWrap.style.display = 'none';
            }
          });
        }
      }
    }

    var lookupShapeEl = document.getElementById('lookupShape');
    var lookupShapeWrap = document.getElementById('lookupShapeWrap');
    var lookupShapeBody = document.getElementById('lookupShapeBody');
    var shapeList = document.getElementById('lookupShapeList');

    if (lookupShapeEl) {
      if (shapeList && shapeData) {
        for (var si = 0; si < shapeData.length; si++) {
          var opt = document.createElement('option');
          opt.value = shapeData[si].name;
          shapeList.appendChild(opt);
        }
      }

      function runShapeLookup() {
        if (!lookupShapeEl || !lookupShapeWrap || !lookupShapeBody) return;

        var q = lookupShapeEl.value || '';
        if (!q.trim()) {
          lookupShapeWrap.style.display = 'none';
          lookupShapeBody.textContent = '';
          return;
        }

        var rows = shapeData || [];
        var matched = [];
        var qCore = shapeCore(q);
        var qKey = shapeKey(q);

        for (var i = 0; i < rows.length; i++) {
          if (shapeCore(rows[i].name) === qCore) {
            matched.push(rows[i]);
          }
        }

        if (matched.length === 0) {
          for (var j = 0; j < rows.length; j++) {
            if (shapeKey(rows[j].name).indexOf(qKey) !== -1 || shapeCore(rows[j].name).indexOf(qCore) !== -1) {
              matched.push(rows[j]);
            }
          }
        }

        lookupShapeWrap.style.display = '';
        lookupShapeBody.textContent = '';

        if (matched.length === 0) {
          var emptyTr = document.createElement('tr');
          var emptyTd = document.createElement('td');
          emptyTd.colSpan = 3;
          emptyTd.className = 'text-center text-muted';
          emptyTd.textContent = 'Không tìm thấy';
          emptyTr.appendChild(emptyTd);
          lookupShapeBody.appendChild(emptyTr);
        } else {
          for (var k = 0; k < matched.length; k++) {
            var row = matched[k];
            var tr = document.createElement('tr');

            var td1 = document.createElement('td');
            td1.className = 'font-weight-bold text-nowrap';
            td1.textContent = row.name;
            tr.appendChild(td1);

            var td2 = document.createElement('td');
            td2.className = 'text-nowrap font-weight-bold';
            td2.textContent = row.weight_text;
            tr.appendChild(td2);

            var td3 = document.createElement('td');
            td3.className = 'text-nowrap';
            td3.textContent = row.spec_text || '';
            tr.appendChild(td3);

            lookupShapeBody.appendChild(tr);
          }
        }
      }

      lookupShapeEl.addEventListener('input', runShapeLookup);

      if (isFloating) {
        lookupShapeEl.addEventListener('focus', runShapeLookup);
        lookupShapeEl.addEventListener('keydown', function (e) {
          if (e.key === 'Escape' || e.keyCode === 27) {
            if (lookupShapeWrap) lookupShapeWrap.style.display = 'none';
          }
        });
      }
    }

    if (isFloating) {
      document.addEventListener('mousedown', function (e) {
        var target = e.target;
        if (lookupResultWrap && lookupResultWrap.style.display !== 'none') {
          var inSize = lookupSizeEl && (lookupSizeEl === target || lookupSizeEl.contains(target));
          var inThick = lookupThicknessEl && (lookupThicknessEl === target || lookupThicknessEl.contains(target));
          var inResult = lookupResultWrap === target || lookupResultWrap.contains(target);
          if (!inSize && !inThick && !inResult) {
            lookupResultWrap.style.display = 'none';
          }
        }
        if (lookupShapeWrap && lookupShapeWrap.style.display !== 'none') {
          var inShape = lookupShapeEl && (lookupShapeEl === target || lookupShapeEl.contains(target));
          var inShapeWrap = lookupShapeWrap === target || lookupShapeWrap.contains(target);
          if (!inShape && !inShapeWrap) {
            lookupShapeWrap.style.display = 'none';
          }
        }
      });
    }
  };
})();
