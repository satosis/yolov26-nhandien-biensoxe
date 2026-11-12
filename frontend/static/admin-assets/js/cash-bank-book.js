/* Cash book and bank book pages: shared behaviour. Pages provide `companiesData`. */
async function renumberVouchers(btn, bookType) {
  if (!confirm('Đánh lại số chứng từ của sổ này?')) {
    return;
  }
  var origHtml = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i class=\'bx bx-loader-alt bx-spin mr-1\'></i>Đang đánh số...';
  }
  try {
    var compEl = document.querySelector('input[name=\'company_id\']');
    var companyId = compEl ? compEl.value : '';
    var fd = new FormData();
    fd.append('book_type', bookType);
    if (companyId) {
      fd.append('company_id', companyId);
    }
    var res = await fetch('/admin/transport/api/cash-bank/renumber-vouchers', {
      method: 'POST',
      body: fd,
    });
    if (res.status === 401 || res.redirected) {
      alert('Phiên làm việc đã hết hạn. Đang chuyển về trang đăng nhập.');
      window.location.href = '/login';
      return;
    }
    var text = await res.text();
    var data;
    try {
      data = JSON.parse(text);
    } catch (parseErr) {
      throw new Error(res.ok ? 'Dữ liệu phản hồi không đúng định dạng.' : ('Máy chủ phản hồi lỗi (' + res.status + '): ' + text.slice(0, 150)));
    }
    if (data.ok) {
      alert(data.message || 'Đã đánh lại số chứng từ thành công.');
      window.location.reload();
    } else {
      alert('Lỗi: ' + (data.message || 'Không thể đánh lại số chứng từ.'));
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = origHtml;
      }
    }
  } catch (err) {
    alert('Lỗi: ' + (err.message || 'Không thể kết nối đến máy chủ.'));
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
}

function bindPartnerSync(codeId, nameId, onPicked) {
    const codeEl = document.getElementById(codeId);
    const nameEl = document.getElementById(nameId);
    if (!codeEl || !nameEl) return;

    function cleanTax(tc) {
      return String(tc || '').replace(/[^0-9A-Za-z]/g, '').toLowerCase();
    }

    function foldName(s) {
      return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
        .replace(/đ/g, 'd').replace(/\s+/g, ' ').trim();
    }

    function findByTax(raw) {
      const val = cleanTax(raw);
      if (!val) return null;
      const exact = companiesData.find(c => cleanTax(c.tax_code) === val);
      if (exact) return exact;
      if (val.length < 4) return null;
      const list = companiesData.filter(c => cleanTax(c.tax_code).includes(val));
      return list.length === 1 ? list[0] : null;
    }

    function findByName(raw) {
      const val = foldName(raw);
      if (!val) return null;
      const exact = companiesData.find(c => foldName(c.name) === val);
      if (exact) return exact;
      if (val.length < 2) return null;
      const tokens = val.split(' ');
      const list = companiesData.filter(c => {
        const full = foldName(c.name);
        return tokens.every(t => full.includes(t));
      });
      return list.length === 1 ? list[0] : null;
    }

    function fill(target, value, source) {
      target.value = value;
      target.dataset.syncAuto = value;
      source.dataset.syncAuto = '';
    }

    function clearAuto(target) {
      if (target.dataset.syncAuto && target.value === target.dataset.syncAuto) {
        target.value = '';
      }
      target.dataset.syncAuto = '';
    }

    codeEl.addEventListener('input', function() {
      this.dataset.syncAuto = '';
      const found = findByTax(this.value);
      if (found && found.name) {
        fill(nameEl, found.name, this);
        if (typeof onPicked === 'function') onPicked(found);
      } else {
        clearAuto(nameEl);
      }
    });

    codeEl.addEventListener('change', function() {
      const found = findByTax(this.value);
      if (found) {
        if (found.tax_code) this.value = found.tax_code;
        if (found.name) fill(nameEl, found.name, this);
        if (typeof onPicked === 'function') onPicked(found);
      }
    });

    nameEl.addEventListener('input', function() {
      this.dataset.syncAuto = '';
      const found = findByName(this.value);
      if (found && found.tax_code) {
        fill(codeEl, found.tax_code, this);
        if (typeof onPicked === 'function') onPicked(found);
      } else {
        clearAuto(codeEl);
      }
    });

    nameEl.addEventListener('change', function() {
      const found = findByName(this.value);
      if (found) {
        if (found.tax_code) codeEl.value = found.tax_code;
        this.value = found.name;
        if (typeof onPicked === 'function') onPicked(found);
      }
    });
  }

async function submitEditOpening(e) {
  e.preventDefault();
  const form = document.getElementById('editOpeningForm');
  const formData = new FormData(form);
  const btn = document.getElementById('btnSubmitOpening');

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm mr-1"></span>Đang lưu...';

  try {
    const res = await fetch('/admin/transport/api/cash-bank/opening-balance', {
      method: 'POST',
      body: formData,
    });
    if (res.status === 401 || res.redirected) {
      alert('Phiên làm việc đã hết hạn. Đang chuyển về trang đăng nhập.');
      window.location.href = '/login';
      return;
    }
    const data = await res.json();
    if (data.ok) {
      alert(data.message || 'Cập nhật số dư đầu kỳ thành công!');
      window.location.reload();
    } else {
      alert('Lỗi: ' + (data.message || 'Không thể cập nhật số dư đầu kỳ'));
      btn.disabled = false;
      btn.innerHTML = '<i class="bx bx-save mr-1"></i>Lưu số dư';
    }
  } catch (err) {
    alert('Lỗi kết nối máy chủ: ' + err.message);
    btn.disabled = false;
    btn.innerHTML = '<i class="bx bx-save mr-1"></i>Lưu số dư';
  }
}

function openEditOpeningModal(bookType, currentVal) {
  document.getElementById('editOpeningBookType').value = bookType;
  const title = bookType === 'bank' ? 'Sửa Số dư đầu kỳ - Ngân hàng VietinBank' : 'Sửa Số dư đầu kỳ - Quỹ tiền mặt';
  document.getElementById('editOpeningModalTitle').innerHTML = `<i class="bx bx-edit mr-1"></i>${title}`;
  const amt = currentVal ? new Intl.NumberFormat('vi-VN').format(currentVal) : '0';
  document.getElementById('editOpeningAmount').value = amt;
  $('#editOpeningModal').modal('show');
}

function updateFileName(input, labelId) {
  if (input.files && input.files[0]) {
    document.getElementById(labelId).innerText = input.files[0].name;
  }
}

function bookShortCompanyName(fullName) {
  const raw = String(fullName || '').trim();
  if (!raw) return '';

  let s = raw;
  s = s.replace(/^(công\s+ty|cty)(?:\s+|$)/i, '').trim();

  const typeRegex = /^(trách\s+nhiệm\s+hữu\s+hạn\s+một\s+thành\s+viên|tnhh\s+một\s+thành\s+viên|trách\s+nhiệm\s+hữu\s+hạn|tnhh\s+mtv|cổ\s+phần|tư\s+nhân|tnhh|mtv|cp)(?:\s+|$)/i;
  s = s.replace(typeRegex, '').trim();

  if (!s) s = raw;

  if (raw === raw.toUpperCase()) {
    s = s.split(/\s+/).map(function(w) {
      if (!w) return '';
      if (/\d/.test(w)) return w.toUpperCase();
      const lower = w.toLocaleLowerCase('vi');
      return lower.charAt(0).toLocaleUpperCase('vi') + lower.slice(1);
    }).join(' ');
  }

  return s;
}

function bookIsGenericDesc(s) {
  const clean = String(s || '').trim().toLowerCase().replace(/\.+$/, '');
  if (!clean) return true;
  return clean === 'gửi vào tài khoản ngân hàng' ||
         clean === 'rút ra thanh toán tiền gửi nh' ||
         clean === 'thu tiền mặt' ||
         clean === 'chi tiền mặt';
}

function bookEscapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
