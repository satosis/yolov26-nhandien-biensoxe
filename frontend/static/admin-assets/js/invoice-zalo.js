(function () {
  let activeInvoiceGuid = null;

  async function openInvoiceZalo(guid) {
    if (!guid) return;
    activeInvoiceGuid = guid;
    const select = document.getElementById('invoiceZaloGroup');
    if (select) {
      select.innerHTML = '<option value="">— Chọn nhóm —</option>';
    }

    const data = await apiRequest('/admin/vehicles/cargo/invoices/' + encodeURIComponent(guid) + '/zalo-groups');
    if (!data.ok) {
      window.showNotify(data.message || 'Không tải được danh sách nhóm Zalo.', 'error');
      return;
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

    $('#invoiceZaloModal').modal('show');
  }

  async function submitInvoiceZalo() {
    if (!activeInvoiceGuid) return;
    const select = document.getElementById('invoiceZaloGroup');
    const groupId = select ? select.value.trim() : '';
    if (!groupId) {
      window.showNotify('Chọn nhóm Zalo nhận.', 'error');
      return;
    }
    const groupName = select && select.selectedIndex >= 0 ? select.options[select.selectedIndex].text : '';

    const btn = document.getElementById('invoiceZaloSubmitBtn');
    if (btn) btn.disabled = true;

    try {
      const data = await apiRequest('/admin/vehicles/cargo/invoices/' + encodeURIComponent(activeInvoiceGuid) + '/send-zalo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group_id: groupId, group_name: groupName }),
      });
      if (!data.ok) {
        window.showNotify(data.message || 'Gửi PDF qua Zalo thất bại.', 'error');
        return;
      }

      window.showNotify(data.message || 'Đã gửi PDF hóa đơn qua Zalo.', 'success');
      $('#invoiceZaloModal').modal('hide');
      window.onInvoiceZaloChanged?.(activeInvoiceGuid, true);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function recallInvoiceZalo(guid) {
    if (!guid) return;
    if (!window.confirm('Thu hồi tin hóa đơn đã gửi Zalo?')) return;

    const data = await apiRequest('/admin/vehicles/cargo/invoices/' + encodeURIComponent(guid) + '/recall-zalo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    if (!data.ok) {
      window.showNotify(data.message || 'Thu hồi tin thất bại.', 'error');
      return;
    }

    window.showNotify(data.message || 'Đã thu hồi tin hóa đơn trên Zalo.', 'success');
    window.onInvoiceZaloChanged?.(guid, false);
  }

  window.openInvoiceZalo = openInvoiceZalo;
  window.submitInvoiceZalo = submitInvoiceZalo;
  window.recallInvoiceZalo = recallInvoiceZalo;
})();
