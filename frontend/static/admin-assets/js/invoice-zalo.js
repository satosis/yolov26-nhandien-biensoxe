(function () {
  let activeInvoiceGuid = null;

  function toast(msg, type = 'info') {
    window.dispatchEvent(new CustomEvent('showToast', { detail: { msg, type } }));
  }

  async function openInvoiceZalo(guid) {
    if (!guid) return;
    activeInvoiceGuid = guid;
    const select = document.getElementById('invoiceZaloGroup');
    if (select) {
      select.innerHTML = '<option value="">— Chọn nhóm —</option>';
    }

    try {
      const resp = await fetch('/admin/vehicles/cargo/invoices/' + encodeURIComponent(guid) + '/zalo-groups', {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin',
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok) {
        throw new Error(data.error || 'Không tải được danh sách nhóm Zalo.');
      }

      if (select) {
        select.innerHTML = '<option value="">— Chọn nhóm —</option>';
        const groups = Array.isArray(data.groups) ? data.groups : [];
        for (const g of groups) {
          const opt = document.createElement('option');
          opt.value = g.group_id;
          opt.textContent = g.name || g.group_id;
          select.appendChild(opt);
        }
        if (data.suggested_group_id) {
          select.value = data.suggested_group_id;
        }
      }

      if (window.jQuery) {
        window.jQuery('#invoiceZaloModal').modal('show');
      }
    } catch (err) {
      toast(err.message || 'Không thể mở gửi Zalo', 'error');
    }
  }

  async function submitInvoiceZalo() {
    if (!activeInvoiceGuid) return;
    const select = document.getElementById('invoiceZaloGroup');
    const groupId = select ? select.value.trim() : '';
    if (!groupId) {
      toast('Chọn nhóm Zalo nhận.', 'error');
      return;
    }
    const groupName = select && select.selectedIndex >= 0 ? select.options[select.selectedIndex].text : '';

    const btn = document.getElementById('invoiceZaloSubmitBtn');
    if (btn) btn.disabled = true;

    try {
      const resp = await fetch('/admin/vehicles/cargo/invoices/' + encodeURIComponent(activeInvoiceGuid) + '/send-zalo', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
        },
        credentials: 'same-origin',
        body: JSON.stringify({ group_id: groupId, group_name: groupName }),
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok) {
        throw new Error(data.error || 'Gửi PDF qua Zalo thất bại.');
      }

      toast(data.message || 'Đã gửi PDF hóa đơn qua Zalo.', 'success');
      if (window.jQuery) {
        window.jQuery('#invoiceZaloModal').modal('hide');
      }
      if (typeof window.onInvoiceZaloChanged === 'function') {
        window.onInvoiceZaloChanged(activeInvoiceGuid, true);
      }
    } catch (err) {
      toast(err.message || 'Lỗi gửi Zalo', 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function recallInvoiceZalo(guid) {
    if (!guid) return;
    if (!window.confirm('Thu hồi tin hóa đơn đã gửi Zalo?')) return;

    try {
      const resp = await fetch('/admin/vehicles/cargo/invoices/' + encodeURIComponent(guid) + '/recall-zalo', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
        },
        credentials: 'same-origin',
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok) {
        throw new Error(data.error || 'Thu hồi tin thất bại.');
      }

      toast(data.message || 'Đã thu hồi tin hóa đơn trên Zalo.', 'success');
      if (typeof window.onInvoiceZaloChanged === 'function') {
        window.onInvoiceZaloChanged(guid, false);
      }
    } catch (err) {
      toast(err.message || 'Lỗi thu hồi tin Zalo', 'error');
    }
  }

  window.openInvoiceZalo = openInvoiceZalo;
  window.submitInvoiceZalo = submitInvoiceZalo;
  window.recallInvoiceZalo = recallInvoiceZalo;
})();
