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

  // Khối lượng lý thuyết của một dòng hàng bán suy ra từ tên hàng, ví dụ "Hộp 40x80x3ly = 15c", "I400 = 1c",
  // "8ly 2mx6m = 3T". Trả về {pieces, pieceUnit, perPiece, total} hoặc null khi tên hàng không nhận ra được.
  var specData = {box: [], shape: []};
  var STEEL_DENSITY = 7.85;
  var PIECE_UNIT = {'c': 'Cây', 'cây': 'Cây', 't': 'Tấm', 'tấm': 'Tấm', 'bó': 'Bó'};
  var NUM = '\\d+(?:[.,]\\d+)?';

  function toNum(s) {
    return parseFloat(String(s).replace(',', '.'));
  }

  function round1(n) {
    return Math.round(n * 10) / 10;
  }

  function plateDims(rest) {
    // "2mx6m", "1m5x3m", "1m5x1m8", "2150x3m", "1500 3m": hai kích thước (m), chỉ lấy khi đủ hai.
    var tokens = rest.replace(/[x×*]/gi, ' ').split(/\s+/).filter(Boolean);
    var dims = [];
    for (var i = 0; i < tokens.length; i++) {
      var t = tokens[i], m;
      if ((m = t.match(/^(\d+)m(\d+)m$/i))) dims.push(toNum(m[1]), toNum(m[2]));
      else if ((m = t.match(/^(\d+)m(\d+)$/i))) dims.push(toNum(m[1] + '.' + m[2]));
      else if ((m = t.match(new RegExp('^(' + NUM + ')m$', 'i')))) dims.push(toNum(m[1]));
      else if (/^\d{3,4}$/.test(t)) dims.push(toNum(t) / 1000);
      else return null;
    }
    return dims.length === 2 ? dims : null;
  }

  function estimateFromName(text) {
    var eq = text.indexOf('=');
    var head = (eq >= 0 ? text.slice(0, eq) : text).trim();
    var tail = eq >= 0 ? text.slice(eq + 1) : '';
    var count = null, pieceUnit = '';
    var cm = tail.match(new RegExp('^\\s*(' + NUM + ')\\s*(c|cây|t|tấm|bó)(?![\\p{L}\\d])', 'iu'));
    if (cm) {
      count = toNum(cm[1]);
      pieceUnit = PIECE_UNIT[cm[2].toLowerCase()];
    }
    // Một bó gồm nhiều cây, bảng bazem chỉ có trọng lượng từng cây.
    if (head.indexOf('+') >= 0 || pieceUnit === 'Bó') return null;

    // Tấm: "8ly 2mx6m = 3T", "20ly 1500 3m 1T", "18ly 1m5x3m"
    var plate = head.match(new RegExp('^(' + NUM + ')\\s*ly\\b(.*)$', 'i'));
    if (plate) {
      var rest = plate[2];
      if (count === null) {
        var tm = rest.match(/\s(\d+)\s*(?:t|tấm)\s*$/iu);
        if (tm) {
          count = toNum(tm[1]);
          rest = rest.slice(0, tm.index);
        } else {
          count = 1;
        }
        pieceUnit = 'Tấm';
      }
      var dims = plateDims(rest);
      if (!dims || !(count > 0)) return null;
      var perPlate = round1(toNum(plate[1]) * dims[0] * dims[1] * STEEL_DENSITY);
      return {pieces: count, pieceUnit: pieceUnit, perPiece: perPlate, total: round1(count * perPlate)};
    }

    if (!(count > 0)) return null;
    var perPiece = null;

    // Trọng lượng ghi sẵn trong tên: "(28kg)", "(bz 109,5kg/1c)", "U10 (33)"
    var note = text.match(new RegExp('\\(\\s*(?:bz|bazem)?\\s*(' + NUM + ')\\s*kg\\s*(?:/\\s*1?\\s*(?:c|cây))?\\s*\\)', 'i'));
    if (note) {
      perPiece = toNum(note[1]);
    } else {
      var bare = head.match(new RegExp('^[a-zøφ]+\\s*' + NUM + '(?:x' + NUM + ')*\\s*\\(\\s*(' + NUM + ')\\s*\\)\\s*$', 'i'));
      if (bare) perPiece = toNum(bare[1]);
    }

    // Thép hộp "Hộp 40x80x3ly" và ống tròn "Ống 60x3ly" theo bảng bazem
    if (perPiece === null) {
      var box = head.match(new RegExp('(?:^|[^\\d.,x×*])(' + NUM + ')\\s*[x×*]\\s*(' + NUM + ')\\s*[x×*]\\s*(' + NUM + ')\\s*(?:ly)?\\s*$', 'i'));
      var tube = head.match(new RegExp('(?:^|\\s)ống\\s*(' + NUM + ')\\s*[x×*]\\s*(' + NUM + ')\\s*(?:ly)?\\s*$', 'iu'));
      var key = null, thick = null;
      if (box) {
        key = sizeKey(box[1] + 'x' + box[2]);
        thick = toNum(box[3]);
      } else if (tube) {
        key = normSize('ống ' + tube[1]);
        thick = toNum(tube[2]);
      }
      if (key !== null) {
        for (var i = 0; i < specData.box.length; i++) {
          var row = specData.box[i];
          if ((box ? sizeKey(row.name) : normSize(row.name)) === key && Math.abs(row.thickness - thick) < 1e-6) {
            perPiece = row.weight;
            break;
          }
        }
        if (perPiece === null) return null;
      }
    }

    // Thép hình I, H, U, V, Ø theo bảng bazem (cây 6m)
    if (perPiece === null) {
      var lengthM = head.match(new RegExp('\\b(' + NUM + ')\\s*m\\b', 'i'));
      if (lengthM && toNum(lengthM[1]) !== 6) return null;
      var shapeHead = head.replace(/\([^)]*\)/g, ' ').replace(/\b\d+\s*m\b/gi, ' ').replace(/(^|\s)(?:uc|đv|dv|vic)(?=\s|$)/giu, ' ').replace(/\s+/g, ' ').trim();
      if (!new RegExp('^[a-zøφ]+\\s*' + NUM + '(?:x' + NUM + ')*$', 'iu').test(shapeHead)) return null;
      var shapeQ = shapeKey(shapeHead);
      for (var j = 0; j < specData.shape.length; j++) {
        if (shapeKey(specData.shape[j].name) === shapeQ) {
          var kg = (specData.shape[j].weight_text || '').match(new RegExp('(' + NUM + ')\\s*kg', 'i'));
          if (kg) perPiece = toNum(kg[1]);
          break;
        }
      }
    }

    if (perPiece === null || !(perPiece > 0)) return null;
    return {pieces: count, pieceUnit: pieceUnit, perPiece: perPiece, total: round1(count * perPiece)};
  }

  window.goodsSpecEstimate = function (text) {
    text = String(text || '').trim();
    return text ? estimateFromName(text) : null;
  };

  window.initGoodsSpecLookup = function (data, opts) {
    data = data || {};
    opts = opts || {};
    var boxData = data.box || [];
    var shapeData = data.shape || [];
    var isFloating = !!opts.floating;
    specData = {box: boxData, shape: shapeData};

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
