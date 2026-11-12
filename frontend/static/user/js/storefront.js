/**
 * ══════════════════════════════════════════════════════════════
 * ACCSHOP Storefront Client Script
 * Xử lý tìm kiếm sidebar, đặt hàng, VietQR polling, validation
 * ══════════════════════════════════════════════════════════════
 */
(function(){
  // Sidebar Search
  var sbSearch = document.getElementById('sbSearch');
  if (sbSearch) {
    sbSearch.addEventListener('input', function(){
      var q = this.value.trim().toLowerCase();
      document.querySelectorAll('#sbList .sb-group').forEach(function(g){
        g.style.display = (!q || (g.dataset.name || '').includes(q)) ? '' : 'none';
      });
    });
  }

  // Copy helper (works reliably even inside top-layer <dialog>)
  function writeClipboard(text) {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      return navigator.clipboard.writeText(text).catch(function() { return fallbackCopy(text); });
    }
    return fallbackCopy(text);
  }
  function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (_) {}
    document.body.removeChild(ta);
    return Promise.resolve();
  }
  function flashCopiedButton(btn) {
    if (!btn) return;
    if (!btn.dataset.origLabel) btn.dataset.origLabel = btn.textContent;
    btn.textContent = '✓ Đã chép';
    btn.classList.add('copied');
    if (btn._copyTimer) clearTimeout(btn._copyTimer);
    btn._copyTimer = setTimeout(function() {
      btn.textContent = btn.dataset.origLabel || 'Sao chép';
      btn.classList.remove('copied');
    }, 1700);
  }
  window.copyText = function(text, triggerBtn) {
    if (!text) return;
    writeClipboard(String(text)).then(function() {
      flashCopiedButton(triggerBtn);
      var t = document.getElementById('toastMsg');
      if (t && !document.querySelector('dialog[open]')) {
        t.textContent = 'Đã sao chép vào bộ nhớ tạm!';
        t.classList.add('show');
        setTimeout(function() { t.classList.remove('show'); }, 2000);
      }
    });
  };

  // Order Dialog
  var dlg         = document.getElementById('dlg');
  var form        = document.getElementById('orderForm');
  var dForm       = document.getElementById('dlgForm');
  var dDone       = document.getElementById('dlgDone');
  var submit      = document.getElementById('dlgSubmit');
  var payClose    = document.getElementById('payClose');
  var payCheckBtn = document.getElementById('payCheckBtn');
  var payQrDl     = document.getElementById('payQrDl');
  var dlgOk       = document.getElementById('dlgOk');
  var activeOrderCode = '';
  var activeOrderToken = '';
  var activeQrUrl     = '';
  var isOrderPaid     = false;
  var lastOrderMeta   = { name: '', quantity: 1 };

  function openOrder(btn) {
    if (!form || !dlg) return;
    stopPoll();
    activeOrderCode = '';
    activeOrderToken = '';
    activeQrUrl = '';
    isOrderPaid = false;
    lastOrderMeta = { name: btn.dataset.name || '', quantity: 1 };
    dlg.classList.remove('is-pay');
    form.reset();
    dForm.hidden = false;
    dDone.hidden = true;
    submit.disabled = false;
    submit.innerHTML = 'Tiến Hành Thanh Toán';
    document.getElementById('dlgTitle').textContent = btn.dataset.name + ' (' + btn.dataset.price + ')';
    document.getElementById('dlgPid').value = btn.dataset.id;
    document.getElementById('dlgQty').max = btn.dataset.stock || 10;
    dlg.showModal();
  }

  var poll = null;
  function stopPoll() { if (poll) { clearInterval(poll); poll = null; } }
  function closeOrder() {
    if (dlg && dlg.open && activeOrderCode && !isOrderPaid) flashToast('Đã thêm vào giỏ hàng');
    stopPoll();
    if (dlg) {
      dlg.classList.remove('is-pay');
      dlg.close();
    }
  }

  function markOrderPaid() {
    if (isOrderPaid) return;
    isOrderPaid = true;
    stopPoll();
    removeFromCart(activeOrderCode);
    renderCart();
    closeOrder();
    flashToast('Đã nhận thanh toán');
    if (typeof window.slOpenChatForOrder === 'function') window.slOpenChatForOrder();
  }

  function pollPaymentStatus(manualTrigger) {
    if (!activeOrderCode || isOrderPaid) return;
    if (manualTrigger && payCheckBtn) {
      payCheckBtn.disabled = true;
      payCheckBtn.textContent = 'Đang kiểm tra…';
    }
    fetch('/tai-khoan/don/' + encodeURIComponent(activeOrderCode), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: activeOrderToken || '' })
    })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d && d.paid) {
          markOrderPaid();
          return;
        }
        if (manualTrigger && payCheckBtn) {
          payCheckBtn.disabled = false;
          payCheckBtn.textContent = 'Chưa thấy giao dịch mới — thử lại';
          setTimeout(function() {
            if (payCheckBtn && !isOrderPaid) payCheckBtn.textContent = 'Tôi đã chuyển khoản';
          }, 2200);
        }
      })
      .catch(function() {
        if (manualTrigger && payCheckBtn) {
          payCheckBtn.disabled = false;
          payCheckBtn.textContent = 'Tôi đã chuyển khoản';
        }
      });
  }

  function showPayment(order) {
    activeOrderCode = order.code || '';
    activeOrderToken = order.token || '';
    activeQrUrl = order.qr_url || '';
    isOrderPaid = false;
    dlg.classList.add('is-pay');

    document.getElementById('payTitle').textContent = 'Quét Mã VietQR Để Nhận Tài Khoản';
    var metaEl = document.getElementById('payOrderMeta');
    var itemName = order.product_name || lastOrderMeta.name;
    var itemQty = order.quantity || lastOrderMeta.quantity || 1;
    if (metaEl) {
      if (itemName) {
        metaEl.textContent = itemName + ' × ' + itemQty;
        metaEl.hidden = false;
      } else {
        metaEl.hidden = true;
      }
    }

    document.getElementById('payQr').src = activeQrUrl;
    document.getElementById('payBank').textContent = order.bank || 'MB Bank';
    document.getElementById('payAccount').textContent = order.account || '';
    document.getElementById('payName').textContent = order.account_name || '';
    document.getElementById('payAmount').textContent = Number(order.amount || 0).toLocaleString('vi-VN') + 'đ';
    document.getElementById('payCode').textContent = activeOrderCode;

    var copyAmtBtn = document.getElementById('copyAmountBtn');
    if (copyAmtBtn) copyAmtBtn.dataset.copyRaw = String(Math.round(Number(order.amount || 0)));

    if (payCheckBtn) {
      payCheckBtn.disabled = false;
      payCheckBtn.textContent = 'Tôi đã chuyển khoản';
    }

    dForm.hidden = true;
    dDone.hidden = false;

    stopPoll();
    poll = setInterval(function() { pollPaymentStatus(false); }, 3500);
    addToCart(order);
  }

  document.querySelectorAll('.pbuy.active').forEach(function(b) {
    b.addEventListener('click', function() { openOrder(b); });
  });

  if (dDone) {
    dDone.querySelectorAll('.copy-pill').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var raw = btn.dataset.copyRaw;
        var elId = btn.dataset.copyEl;
        var text = raw || (elId && document.getElementById(elId) ? document.getElementById(elId).textContent.trim() : '');
        window.copyText(text, btn);
      });
    });
  }

  if (payQrDl) {
    payQrDl.addEventListener('click', function() {
      if (!activeQrUrl) return;
      var fname = (activeOrderCode || 'VietQR') + '.png';
      fetch(activeQrUrl, { mode: 'cors' })
        .then(function(r) {
          if (!r.ok) throw new Error('status');
          return r.blob();
        })
        .then(function(blob) {
          var a = document.createElement('a');
          var objUrl = URL.createObjectURL(blob);
          a.href = objUrl;
          a.download = fname;
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          setTimeout(function() { URL.revokeObjectURL(objUrl); }, 1000);
        })
        .catch(function() {
          window.open(activeQrUrl, '_blank', 'noopener');
        });
    });
  }

  var dlgClose = document.getElementById('dlgClose');
  var dlgCancel = document.getElementById('dlgCancel');
  if (dlgClose) dlgClose.addEventListener('click', closeOrder);
  if (dlgCancel) dlgCancel.addEventListener('click', closeOrder);
  if (payClose) payClose.addEventListener('click', closeOrder);
  if (dlgOk) dlgOk.addEventListener('click', closeOrder);
  if (payCheckBtn) payCheckBtn.addEventListener('click', function() { pollPaymentStatus(true); });
  if (dlg) {
    dlg.addEventListener('click', function(e) { if (e.target === dlg) closeOrder(); });
    dlg.addEventListener('cancel', function(e) { e.preventDefault(); closeOrder(); });
  }

  if (form) {
    form.addEventListener('submit', function(e) {
      e.preventDefault();

      // ══ CLIENT FORM VALIDATION: Kiểm tra thông tin người mua ══
      var qtyInp = document.getElementById('dlgQty');
      var nameInp = form.querySelector('[name="buyer_name"]');
      var contactInp = form.querySelector('[name="buyer_contact"]');
      var consentInp = form.querySelector('[name="consent"]');

      if (qtyInp) lastOrderMeta.quantity = Math.max(1, parseInt(qtyInp.value || '1', 10) || 1);

      if (nameInp && !nameInp.value.trim()) {
        nameInp.setAttribute('aria-invalid', 'true');
        nameInp.focus();
        alert('Vui lòng nhập họ tên của bạn để đặt hàng.');
        return;
      }

      if (contactInp) {
        var rawVal = contactInp.value.trim();
        var rawPhone = rawVal.replace(/[\s.-]/g, '');
        var phoneRegex = /^(0|\+84)[0-9]{9,10}$/;
        var emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

        if (!rawVal || (!phoneRegex.test(rawPhone) && !emailRegex.test(rawVal))) {
          contactInp.setAttribute('aria-invalid', 'true');
          contactInp.focus();
          alert('Số điện thoại hoặc Email liên hệ chưa hợp lệ (10 chữ số đối với SĐT hoặc định dạng email đúng).');
          return;
        }
      }

      if (consentInp && !consentInp.checked) {
        consentInp.setAttribute('aria-invalid', 'true');
        consentInp.focus();
        alert('Vui lòng tích đồng ý điều khoản giao dịch trước khi thanh toán.');
        return;
      }

      // Mã cuộc trò chuyện của người mua: đơn thanh toán xong sẽ được ghi vào đúng cuộc trò chuyện này
      var chatTokenInp = document.getElementById('dlgChatToken');
      var chatToken = localStorage.getItem('accshop_live_chat_token');
      if (!chatToken) {
        chatToken = Array.from(crypto.getRandomValues(new Uint8Array(16)), function(b) { return ('0' + b.toString(16)).slice(-2); }).join('');
        localStorage.setItem('accshop_live_chat_token', chatToken);
      }
      chatTokenInp.value = chatToken;

      submit.disabled = true;
      submit.innerHTML = '⏳ Đang khởi tạo đơn hàng…';
      fetch(form.action, { method: 'POST', body: new FormData(form) })
        .then(function(r) {
          return r.json().then(function(data) {
            if (!r.ok || !data || !data.ok) {
              throw new Error((data && (data.detail || data.error)) || 'Không thể tạo đơn hàng.');
            }
            return data;
          });
        })
        .then(showPayment)
        .catch(function(err) {
          submit.disabled = false;
          submit.innerHTML = 'Tiến Hành Thanh Toán';
          alert('Lỗi tạo đơn hàng: ' + (err && err.message ? err.message : err));
        });
    });
  }

  // ════════════════════════════════════════════
  // NÚT SỐ LƯỢNG
  // ════════════════════════════════════════════
  var qtyInput = document.getElementById('dlgQty');
  function stepQty(delta) {
    var max = parseInt(qtyInput.max, 10) || 10;
    qtyInput.value = Math.min(max, Math.max(1, (parseInt(qtyInput.value, 10) || 1) + delta));
  }
  document.getElementById('dlgQtyMinus').addEventListener('click', function() { stepQty(-1); });
  document.getElementById('dlgQtyPlus').addEventListener('click', function() { stepQty(1); });

  // ════════════════════════════════════════════
  // GIỎ HÀNG: ĐƠN CHƯA THANH TOÁN LƯU Ở TRÌNH DUYỆT (KHÔNG LƯU DB)
  // ════════════════════════════════════════════
  var CART_ITEMS_KEY  = 'accshop_cart_items';
  var LEGACY_CART_KEY = 'accshop_cart_codes';
  var cartBtn      = document.getElementById('cartBtn');
  var cartCount    = document.getElementById('cartCount');
  var cartDlg      = document.getElementById('cartDlg');
  var cartList     = document.getElementById('cartList');
  var cartHeadBadge = document.getElementById('cartHeadBadge');
  var cartHeadSub  = document.getElementById('cartHeadSub');
  var cartFooter   = document.getElementById('cartFooter');
  var cartTotalEl  = document.getElementById('cartTotal');
  var cartShopBtn  = document.getElementById('cartShopBtn');
  var cartOrders   = [];
  var legacyOrders = [];
  var cartLoading  = false;

  function makeSvgIcon(size, viewBox, innerMarkup) {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', String(size));
    svg.setAttribute('height', String(size));
    svg.setAttribute('viewBox', viewBox);
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    svg.innerHTML = innerMarkup;
    return svg;
  }

  function readClientCart() {
    try {
      var raw = JSON.parse(localStorage.getItem(CART_ITEMS_KEY) || '[]');
      return Array.isArray(raw) ? raw.filter(function(x) { return x && x.code; }) : [];
    } catch (_) {
      return [];
    }
  }
  function readLegacyCodes() {
    try {
      var raw = JSON.parse(localStorage.getItem(LEGACY_CART_KEY) || '[]');
      return Array.isArray(raw) ? raw.filter(function(c) { return typeof c === 'string' && c; }) : [];
    } catch (_) {
      return [];
    }
  }
  function mergedCartOrders() {
    var client = readClientCart();
    var seen = {};
    var out = [];
    client.forEach(function(item) {
      seen[item.code] = true;
      out.push(item);
    });
    legacyOrders.forEach(function(item) {
      if (item && item.code && !seen[item.code]) {
        seen[item.code] = true;
        out.push(item);
      }
    });
    return out;
  }
  function updateCartBadge() {
    var n = mergedCartOrders().length;
    cartCount.textContent = n;
    cartCount.hidden = !n;
  }
  function saveClientCart(items) {
    localStorage.setItem(CART_ITEMS_KEY, JSON.stringify(items));
    cartOrders = mergedCartOrders();
    updateCartBadge();
  }
  function addToCart(order) {
    if (!order || !order.code) return;
    var items = readClientCart().filter(function(x) { return x.code !== order.code; });
    var entry = {
      code: order.code,
      token: order.token || '',
      product_name: order.product_name || lastOrderMeta.name || 'Gói tài khoản',
      quantity: order.quantity || lastOrderMeta.quantity || 1,
      amount: Number(order.amount || 0),
      qr_url: order.qr_url || '',
      bank: order.bank || 'MB Bank',
      account: order.account || '',
      account_name: order.account_name || ''
    };
    saveClientCart([entry].concat(items));
  }
  function removeFromCart(code) {
    if (!code) return;
    saveClientCart(readClientCart().filter(function(x) { return x.code !== code; }));
    var legacy = readLegacyCodes().filter(function(c) { return c !== code; });
    if (legacy.length) {
      localStorage.setItem(LEGACY_CART_KEY, JSON.stringify(legacy));
    } else {
      localStorage.removeItem(LEGACY_CART_KEY);
    }
    legacyOrders = legacyOrders.filter(function(o) { return o.code !== code; });
    cartOrders = mergedCartOrders();
    updateCartBadge();
  }
  function flashToast(msg) {
    var t = document.getElementById('toastMsg');
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(function() { t.classList.remove('show'); }, 2000);
  }

  function cartRow(order) {
    var row = document.createElement('div');
    row.className = 'cart-item';

    var iconWrap = document.createElement('div');
    iconWrap.className = 'cart-item-icon';
    iconWrap.appendChild(makeSvgIcon(18, '0 0 24 24', '<path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/>'));

    var info = document.createElement('div');
    info.className = 'cart-item-info';

    var top = document.createElement('div');
    top.className = 'cart-item-top';
    var name = document.createElement('div');
    name.className = 'cart-item-name';
    name.textContent = order.product_name || 'Gói tài khoản';
    var qty = document.createElement('span');
    qty.className = 'cart-item-qty';
    qty.textContent = '×' + (order.quantity || 1);
    top.append(name, qty);

    var meta = document.createElement('div');
    meta.className = 'cart-item-meta';
    var amount = document.createElement('div');
    amount.className = 'cart-item-amount';
    amount.textContent = Number(order.amount || 0).toLocaleString('vi-VN') + 'đ';
    meta.appendChild(amount);
    if (order.code) {
      var codeEl = document.createElement('span');
      codeEl.className = 'cart-item-code';
      codeEl.textContent = '#' + order.code;
      meta.appendChild(codeEl);
    }

    info.append(top, meta);

    var actions = document.createElement('div');
    actions.className = 'cart-item-actions';

    var pay = document.createElement('button');
    pay.type = 'button';
    pay.className = 'cart-pay';
    pay.appendChild(makeSvgIcon(14, '0 0 24 24', '<rect x="3" y="3" width="7" height="7" rx="1"></rect><rect x="14" y="3" width="7" height="7" rx="1"></rect><rect x="3" y="14" width="7" height="7" rx="1"></rect><path d="M14 14h3v3h-3zM17 17h4v4h-4z"/>'));
    var payLabel = document.createElement('span');
    payLabel.textContent = 'Thanh toán QR';
    pay.appendChild(payLabel);
    pay.addEventListener('click', function() { payCartOrder(order); });

    var del = document.createElement('button');
    del.type = 'button';
    del.className = 'cart-del';
    del.setAttribute('aria-label', 'Xóa đơn khỏi giỏ hàng');
    del.setAttribute('title', 'Xóa khỏi giỏ');
    del.appendChild(makeSvgIcon(15, '0 0 24 24', '<polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path><path d="M10 11v6M14 11v6M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>'));
    del.addEventListener('click', function() {
      removeFromCart(order.code);
      cartOrders = cartOrders.filter(function(o) { return o.code !== order.code; });
      renderCart();
      flashToast('Đã xóa đơn khỏi giỏ hàng');
    });

    actions.append(pay, del);
    row.append(iconWrap, info, actions);
    return row;
  }

  function renderCart() {
    cartList.textContent = '';
    var hasItems = cartOrders.length > 0;
    if (cartHeadBadge) {
      cartHeadBadge.textContent = cartOrders.length + ' đơn';
      cartHeadBadge.hidden = !hasItems;
    }
    if (cartHeadSub) cartHeadSub.hidden = !hasItems;
    if (cartFooter) cartFooter.hidden = !hasItems;

    if (cartLoading && !hasItems) {
      var loading = document.createElement('div');
      loading.className = 'cart-loading';
      var spin = document.createElement('span');
      spin.className = 'cart-spinner';
      spin.setAttribute('aria-hidden', 'true');
      var msg = document.createElement('span');
      msg.textContent = 'Đang kiểm tra đơn hàng…';
      loading.append(spin, msg);
      cartList.appendChild(loading);
      return;
    }

    if (!hasItems) {
      var empty = document.createElement('div');
      empty.className = 'cart-empty';
      var eIcon = document.createElement('div');
      eIcon.className = 'cart-empty-icon';
      eIcon.appendChild(makeSvgIcon(24, '0 0 24 24', '<circle cx="9" cy="21" r="1"></circle><circle cx="20" cy="21" r="1"></circle><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"></path>'));
      var eTitle = document.createElement('div');
      eTitle.className = 'cart-empty-title';
      eTitle.textContent = 'Giỏ hàng của bạn đang trống';
      var eSub = document.createElement('div');
      eSub.className = 'cart-empty-sub';
      eSub.textContent = 'Đơn đặt mua chưa quét mã QR sẽ được giữ lại tại đây để bạn thanh toán sau.';
      var eBtn = document.createElement('button');
      eBtn.type = 'button';
      eBtn.className = 'cart-empty-btn';
      eBtn.id = 'cartEmptyShopBtn';
      eBtn.textContent = 'Khám phá tài khoản';
      eBtn.addEventListener('click', function() { cartDlg.close(); });
      empty.append(eIcon, eTitle, eSub, eBtn);
      cartList.appendChild(empty);
      return;
    }

    var total = 0;
    cartOrders.forEach(function(order) {
      total += Number(order.amount || 0);
      cartList.appendChild(cartRow(order));
    });
    if (cartTotalEl) cartTotalEl.textContent = total.toLocaleString('vi-VN') + 'đ';
  }

  // Giỏ hàng giữ hoàn toàn phía trình duyệt; chỉ đồng bộ với máy chủ để tự gỡ đơn đã thanh toán đủ.
  function loadCart() {
    var clientItems = readClientCart();
    var clientCodeSet = {};
    clientItems.forEach(function(item) { clientCodeSet[item.code] = true; });
    var legacyCodes = readLegacyCodes().filter(function(c) { return !clientCodeSet[c]; });

    cartOrders = mergedCartOrders();
    updateCartBadge();

    if (!clientItems.length && !legacyCodes.length) {
      cartLoading = false;
      legacyOrders = [];
      cartOrders = [];
      updateCartBadge();
      renderCart();
      return;
    }

    cartLoading = !clientItems.length && legacyCodes.length > 0 && !legacyOrders.length;
    renderCart();

    fetch('/tai-khoan/gio-hang', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        items: clientItems.map(function(x) { return { code: x.code, token: x.token || '' }; }),
        legacy_codes: legacyCodes
      })
    })
      .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function(data) {
        cartLoading = false;
        var paidSet = {};
        if (Array.isArray(data && data.paid_codes)) {
          data.paid_codes.forEach(function(c) { paidSet[String(c).toUpperCase()] = true; });
        }
        var nextClient = readClientCart().filter(function(x) {
          return !paidSet[String(x.code || '').toUpperCase()];
        });
        legacyOrders = Array.isArray(data && data.orders) ? data.orders : [];
        var nextLegacy = legacyOrders.map(function(o) { return o.code; });
        if (nextLegacy.length) {
          localStorage.setItem(LEGACY_CART_KEY, JSON.stringify(nextLegacy));
        } else {
          localStorage.removeItem(LEGACY_CART_KEY);
        }
        saveClientCart(nextClient);
        renderCart();
      })
      .catch(function() {
        cartLoading = false;
        renderCart();
      });
  }

  function payCartOrder(order) {
    cartDlg.close();
    stopPoll();
    lastOrderMeta = { name: order.product_name, quantity: order.quantity };
    showPayment(order);
    dlg.showModal();
  }

  cartBtn.addEventListener('click', function() {
    cartDlg.showModal();
    loadCart();
  });
  document.getElementById('cartClose').addEventListener('click', function() { cartDlg.close(); });
  if (cartShopBtn) cartShopBtn.addEventListener('click', function() { cartDlg.close(); });
  cartDlg.addEventListener('click', function(e) { if (e.target === cartDlg) cartDlg.close(); });
  loadCart();

  // ════════════════════════════════════════════
  // MỞ CHAT VỚI NGỮ CẢNH SẢN PHẨM (BRIDGE ĐẾN LIVE CHAT WIDGET)
  // ════════════════════════════════════════════
  window.openChatWithProduct = function(productName) {
    if (typeof window.slOpenChatWithContext === 'function') {
      window.slOpenChatWithContext('Gói ' + productName);
    }
  };

  // ════════════════════════════════════════════
  // CHỈNH SỬA HỒ SƠ NGƯỜI DÙNG (TOPBAR MODAL)
  // ════════════════════════════════════════════
  var accountMenuWrap = document.getElementById('accountMenuWrap');
  var accountChipBtn = document.getElementById('accountChipBtn');
  var accountDropdown = document.getElementById('accountDropdown');
  var menuDisplayNameEl = document.getElementById('menuDisplayName');

  function closeAccountMenu() {
    if (!accountDropdown || !accountChipBtn) return;
    accountDropdown.hidden = true;
    accountChipBtn.setAttribute('aria-expanded', 'false');
  }

  function toggleAccountMenu() {
    if (!accountDropdown || !accountChipBtn) return;
    var nextOpen = accountDropdown.hidden;
    accountDropdown.hidden = !nextOpen;
    accountChipBtn.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
  }

  if (accountChipBtn && accountDropdown) {
    accountChipBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      toggleAccountMenu();
    });
    document.addEventListener('click', function(e) {
      if (accountMenuWrap && !accountMenuWrap.contains(e.target)) {
        closeAccountMenu();
      }
    });
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && !accountDropdown.hidden) {
        closeAccountMenu();
        accountChipBtn.focus();
      }
    });
  }

  var btnEditProfile = document.getElementById('btnEditProfile');
  var profileDlg = document.getElementById('profileDlg');
  if (btnEditProfile && profileDlg) {
    var pfClose = document.getElementById('pfClose');
    var pfCancelInfo = document.getElementById('pfCancelInfo');
    var pfCancelPwd = document.getElementById('pfCancelPwd');
    var pfTabs = profileDlg.querySelectorAll('.pf-tab-btn');
    var pfPanelInfo = document.getElementById('pfPanelInfo');
    var pfPanelPwd = document.getElementById('pfPanelPwd');
    var pfInfoForm = document.getElementById('pfInfoForm');
    var pfPwdForm = document.getElementById('pfPwdForm');
    var pfInfoAlert = document.getElementById('pfInfoAlert');
    var pfPwdAlert = document.getElementById('pfPwdAlert');
    var pfAvatarInput = document.getElementById('pfAvatarInput');
    var pfAvatarPreviewWrap = document.getElementById('pfAvatarPreviewWrap');
    var pfDisplayName = document.getElementById('pfDisplayName');
    var pfInfoSubmit = document.getElementById('pfInfoSubmit');
    var pfPwdSubmit = document.getElementById('pfPwdSubmit');

    var chipNameEl = document.getElementById('userChipName');
    var chipWrapEl = document.getElementById('userChipAvatarWrap');
    var chipImgEl = document.getElementById('userChipAvatarImg');
    var pfImgEl = document.getElementById('pfAvatarImg');
    var pfFullNameEl = document.getElementById('pfFullName');
    var pfPhoneEl = document.getElementById('pfPhone');
    var pfEmailEl = document.getElementById('pfEmail');
    var savedProfile = {
      full_name: pfFullNameEl ? pfFullNameEl.value : '',
      phone: pfPhoneEl ? pfPhoneEl.value : '',
      email: pfEmailEl ? pfEmailEl.value : '',
      display_name: pfDisplayName ? pfDisplayName.textContent.trim() : '',
      avatar: chipImgEl ? chipImgEl.getAttribute('src') : (pfImgEl ? pfImgEl.getAttribute('src') : '')
    };
    var activePreviewUrl = null;

    function revokePreviewUrl() {
      if (activePreviewUrl) {
        URL.revokeObjectURL(activePreviewUrl);
        activePreviewUrl = null;
      }
    }

    function showToast(msg) {
      var t = document.getElementById('toastMsg');
      if (!t) return;
      t.textContent = msg || 'Đã lưu!';
      t.classList.add('show');
      setTimeout(function() { t.classList.remove('show'); }, 2400);
    }

    function setPfAlert(el, msg, kind) {
      if (!el) return;
      if (!msg) {
        el.hidden = true;
        el.textContent = '';
        el.className = 'pf-alert';
        return;
      }
      el.textContent = msg;
      el.className = 'pf-alert ' + (kind || 'error');
      el.hidden = false;
    }

    function renderCircleAvatar(container, imgClass, phClass, avatarUrl, displayName) {
      if (!container) return;
      container.textContent = '';
      if (avatarUrl) {
        var img = document.createElement('img');
        img.src = avatarUrl;
        img.alt = 'Ảnh đại diện';
        img.className = imgClass;
        img.addEventListener('error', function() {
          renderCircleAvatar(container, imgClass, phClass, '', displayName);
        });
        container.appendChild(img);
      } else {
        var ph = document.createElement('span');
        ph.className = phClass;
        var initial = ((displayName || 'U').trim().charAt(0) || 'U').toUpperCase();
        ph.textContent = initial;
        container.appendChild(ph);
      }
    }

    if (chipImgEl) {
      chipImgEl.addEventListener('error', function() {
        renderCircleAvatar(chipWrapEl, 'user-avatar', 'user-avatar-placeholder', '', savedProfile.display_name || savedProfile.full_name);
      });
    }
    if (pfImgEl) {
      pfImgEl.addEventListener('error', function() {
        renderCircleAvatar(pfAvatarPreviewWrap, 'pf-avatar-img', 'pf-avatar-placeholder', '', savedProfile.display_name || savedProfile.full_name);
      });
    }

    function switchPfTab(tabName) {
      pfTabs.forEach(function(b) {
        var active = b.dataset.pfTab === tabName;
        b.classList.toggle('active', active);
        b.setAttribute('aria-selected', active ? 'true' : 'false');
      });
      if (pfPanelInfo) pfPanelInfo.hidden = tabName !== 'info';
      if (pfPanelPwd) pfPanelPwd.hidden = tabName !== 'password';
      setPfAlert(pfInfoAlert, '');
      setPfAlert(pfPwdAlert, '');
    }

    pfTabs.forEach(function(b) {
      b.addEventListener('click', function() { switchPfTab(b.dataset.pfTab); });
    });

    function openProfileDialog() {
      closeAccountMenu();
      revokePreviewUrl();
      if (pfFullNameEl) pfFullNameEl.value = savedProfile.full_name;
      if (pfPhoneEl) pfPhoneEl.value = savedProfile.phone;
      if (pfEmailEl) pfEmailEl.value = savedProfile.email;
      if (pfAvatarInput) pfAvatarInput.value = '';
      if (pfDisplayName) pfDisplayName.textContent = savedProfile.display_name || savedProfile.full_name;
      renderCircleAvatar(
        pfAvatarPreviewWrap,
        'pf-avatar-img',
        'pf-avatar-placeholder',
        savedProfile.avatar,
        savedProfile.display_name || savedProfile.full_name
      );
      if (pfPwdForm) pfPwdForm.reset();
      switchPfTab('info');
      profileDlg.showModal();
      if (pfFullNameEl) pfFullNameEl.focus();
    }

    function closeProfileDialog() {
      revokePreviewUrl();
      profileDlg.close();
    }

    btnEditProfile.addEventListener('click', openProfileDialog);
    if (pfClose) pfClose.addEventListener('click', closeProfileDialog);
    if (pfCancelInfo) pfCancelInfo.addEventListener('click', closeProfileDialog);
    if (pfCancelPwd) pfCancelPwd.addEventListener('click', closeProfileDialog);
    profileDlg.addEventListener('click', function(e) {
      if (e.target === profileDlg) closeProfileDialog();
    });

    if (pfAvatarInput) {
      pfAvatarInput.addEventListener('change', function() {
        setPfAlert(pfInfoAlert, '');
        if (!pfAvatarInput.files || !pfAvatarInput.files.length) return;
        var file = pfAvatarInput.files[0];
        if (file.size > 5 * 1024 * 1024) {
          setPfAlert(pfInfoAlert, 'Ảnh đại diện tối đa 5MB.');
          pfAvatarInput.value = '';
          return;
        }
        revokePreviewUrl();
        activePreviewUrl = URL.createObjectURL(file);
        var nameVal = (pfFullNameEl || {}).value || savedProfile.display_name || '';
        renderCircleAvatar(
          pfAvatarPreviewWrap,
          'pf-avatar-img',
          'pf-avatar-placeholder',
          activePreviewUrl,
          nameVal
        );
      });
    }

    if (pfInfoForm) {
      pfInfoForm.addEventListener('submit', function(e) {
        e.preventDefault();
        setPfAlert(pfInfoAlert, '');
        var fullNameEl = pfFullNameEl;
        var phoneEl = pfPhoneEl;
        var emailEl = pfEmailEl;
        var nameVal = fullNameEl ? fullNameEl.value.trim() : '';
        var phoneVal = phoneEl ? phoneEl.value.trim() : '';
        var emailVal = emailEl ? emailEl.value.trim() : '';

        if (!nameVal) {
          setPfAlert(pfInfoAlert, 'Vui lòng nhập họ và tên.');
          if (fullNameEl) fullNameEl.focus();
          return;
        }
        if (phoneVal) {
          var cleanPhone = phoneVal.replace(/[\s.\-()]/g, '');
          if (!/^(0\d{9,10}|(?:\+?84)\d{9,10})$/.test(cleanPhone)) {
            setPfAlert(pfInfoAlert, 'Số điện thoại không hợp lệ (VD 0912345678).');
            if (phoneEl) phoneEl.focus();
            return;
          }
        }
        if (emailVal && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailVal)) {
          setPfAlert(pfInfoAlert, 'Email không hợp lệ.');
          if (emailEl) emailEl.focus();
          return;
        }

        pfInfoSubmit.disabled = true;
        pfInfoSubmit.textContent = 'Đang lưu…';
        fetch(pfInfoForm.action, {
          method: 'POST',
          headers: {
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
          },
          body: new FormData(pfInfoForm)
        })
          .then(function(r) {
            return r.json().then(function(data) {
              return { status: r.status, data: data };
            });
          })
          .then(function(res) {
            pfInfoSubmit.disabled = false;
            pfInfoSubmit.textContent = 'Lưu thay đổi';
            if (!res.data || !res.data.ok) {
              setPfAlert(pfInfoAlert, (res.data && (res.data.error || res.data.detail)) || 'Không thể lưu hồ sơ.');
              return;
            }
            var u = res.data.user || {};
            var shownName = u.full_name || u.username || nameVal;
            savedProfile = {
              full_name: u.full_name || nameVal,
              phone: u.phone || '',
              email: u.email || '',
              display_name: shownName,
              avatar: u.avatar || ''
            };
            if (pfFullNameEl) pfFullNameEl.defaultValue = savedProfile.full_name;
            if (pfPhoneEl) pfPhoneEl.defaultValue = savedProfile.phone;
            if (pfEmailEl) pfEmailEl.defaultValue = savedProfile.email;
            if (chipNameEl) chipNameEl.textContent = shownName;
            if (pfDisplayName) pfDisplayName.textContent = shownName;
            if (menuDisplayNameEl) menuDisplayNameEl.textContent = shownName;
            renderCircleAvatar(chipWrapEl, 'user-avatar', 'user-avatar-placeholder', savedProfile.avatar, shownName);
            renderCircleAvatar(pfAvatarPreviewWrap, 'pf-avatar-img', 'pf-avatar-placeholder', savedProfile.avatar, shownName);

            if (form) {
              var bName = form.querySelector('[name="buyer_name"]');
              var bContact = form.querySelector('[name="buyer_contact"]');
              if (bName) {
                bName.value = shownName;
                bName.defaultValue = shownName;
                bName.setAttribute('value', shownName);
              }
              var nextContact = savedProfile.phone || savedProfile.email || '';
              if (bContact) {
                bContact.value = nextContact;
                bContact.defaultValue = nextContact;
                bContact.setAttribute('value', nextContact);
              }
            }
            if (pfAvatarInput) pfAvatarInput.value = '';
            closeProfileDialog();
            showToast(res.data.msg || 'Đã lưu thông tin hồ sơ!');
          })
          .catch(function() {
            pfInfoSubmit.disabled = false;
            pfInfoSubmit.textContent = 'Lưu thay đổi';
            setPfAlert(pfInfoAlert, 'Lỗi kết nối. Vui lòng thử lại.');
          });
      });
    }

    if (pfPwdForm) {
      pfPwdForm.addEventListener('submit', function(e) {
        e.preventDefault();
        setPfAlert(pfPwdAlert, '');
        var curEl = document.getElementById('pfCurPwd');
        var newEl = document.getElementById('pfNewPwd');
        var cfmEl = document.getElementById('pfConfirmPwd');
        var newPwd = newEl ? newEl.value : '';
        var cfmPwd = cfmEl ? cfmEl.value : '';
        if (newPwd.trim().length < 6) {
          setPfAlert(pfPwdAlert, 'Mật khẩu mới phải có ít nhất 6 ký tự.');
          if (newEl) newEl.focus();
          return;
        }
        if (newPwd !== cfmPwd) {
          setPfAlert(pfPwdAlert, 'Mật khẩu mới nhập lại không khớp.');
          if (cfmEl) cfmEl.focus();
          return;
        }

        pfPwdSubmit.disabled = true;
        pfPwdSubmit.textContent = 'Đang cập nhật…';
        fetch(pfPwdForm.action, {
          method: 'POST',
          headers: {
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
          },
          body: new FormData(pfPwdForm)
        })
          .then(function(r) {
            return r.json().then(function(data) {
              return { status: r.status, data: data };
            });
          })
          .then(function(res) {
            pfPwdSubmit.disabled = false;
            pfPwdSubmit.textContent = 'Cập nhật mật khẩu';
            if (!res.data || !res.data.ok) {
              setPfAlert(pfPwdAlert, (res.data && (res.data.error || res.data.detail)) || 'Không thể đổi mật khẩu.');
              return;
            }
            if (curEl) curEl.value = '';
            if (newEl) newEl.value = '';
            if (cfmEl) cfmEl.value = '';
            closeProfileDialog();
            showToast(res.data.msg || 'Đã đổi mật khẩu thành công!');
          })
          .catch(function() {
            pfPwdSubmit.disabled = false;
            pfPwdSubmit.textContent = 'Cập nhật mật khẩu';
            setPfAlert(pfPwdAlert, 'Lỗi kết nối. Vui lòng thử lại.');
          });
      });
    }
  }

})();

