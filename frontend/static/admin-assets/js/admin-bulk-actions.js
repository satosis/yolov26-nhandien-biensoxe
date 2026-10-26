(function () {
  'use strict';

  const ACTION_HEADER = /thao\s*tac|hanh\s*dong|chuc\s*nang|xoa/i;
  let scanQueued = false;

  function normalize(value) {
    return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  }

  function rowCells(row) {
    return Array.from(row.children).filter((cell) => cell.matches('td,th'));
  }

  function actionIndex(table) {
    const headers = Array.from(table.tHead?.rows?.[0]?.cells || []);
    return headers.findIndex((cell) => ACTION_HEADER.test(normalize(cell.textContent)));
  }

  function rowActions(row) {
    const actions = new Map();
    row.querySelectorAll('[data-bulk-action][data-bulk-url]').forEach((element) => {
      const key = element.dataset.bulkAction;
      if (!actions.has(key)) actions.set(key, {
        url: element.dataset.bulkUrl,
        method: (element.dataset.bulkMethod || 'POST').toUpperCase(),
        label: element.dataset.bulkLabel || (key === 'delete' ? 'Xóa' : key),
        value: element.dataset.bulkValue || ''
      });
    });
    const table = row.closest('table');
    const actionCellIndex = Number(table?.dataset.bulkActionIndex || -1);
    const actionCell = actionCellIndex >= 0 ? rowCells(row)[actionCellIndex + 1] : null;
    const isCard = row.matches('[data-bulk-item]');
    if (!actionCell && !isCard) return actions;

    const rowForms = Array.from(row.querySelectorAll('form[action]')).filter((form) =>
      (actionCell && actionCell.contains(form)) ||
      /\/(delete|toggle)(?:\/|\?|$)/i.test(form.getAttribute('action') || '') ||
      (isCard && form.querySelector('[name="status"]'))
    );
    for (const form of rowForms) {
      const url = form.getAttribute('action') || '';
      const method = (form.getAttribute('method') || 'GET').toUpperCase();
      if (method === 'GET') continue;
      const parsed = new URL(url, window.location.href);
      const normalizedPath = parsed.pathname.replace(/\/\d+(?=\/|$)/g, '/{id}');
      const fields = Array.from(new FormData(form).entries())
        .map(([name, value]) => `${name}=${String(value)}`)
        .sort()
        .join('&');
      const key = /\/delete(?:\/|$)/i.test(parsed.pathname) ? 'delete' :
        /\/toggle(?:\/|$)/i.test(parsed.pathname) ? 'toggle' : `form:${method}:${normalizedPath}:${fields}`;
      const submit = form.querySelector('button[type="submit"],button:not([type]),input[type="submit"]');
      let label = submit?.innerText || submit?.value || submit?.getAttribute('aria-label') || submit?.title || '';
      label = label.trim().replace(/\s+/g, ' ');
      if (key === 'toggle') label = 'Đổi trạng thái';
      if (!label) label = key === 'delete' ? 'Xóa' : key === 'toggle' ? 'Bật/tắt' : 'Thực hiện';
      if (!actions.has(key)) actions.set(key, { url, method, form, label });
    }

    const htmxDelete = (actionCell || row).querySelector('[hx-delete]');
    if (htmxDelete && !actions.has('delete')) actions.set('delete', {
      url: htmxDelete.getAttribute('hx-delete'), method: 'DELETE', label: 'Xóa'
    });
    return actions;
  }

  function selectedRows(table) {
    if (table instanceof HTMLTableElement) {
      return Array.from(table.querySelectorAll('tbody tr[data-bulk-row]')).filter((row) =>
        row.querySelector(':scope > td:first-child input[data-bulk-check]:checked')
      );
    }
    return Array.from(table.querySelectorAll('[data-bulk-item]')).filter((item) =>
      item.querySelector('input[data-bulk-check]:checked')
    );
  }

  function emitToast(message, type) {
    window.dispatchEvent(new CustomEvent('showToast', { detail: { msg: message, type: type || 'info' } }));
  }

  function csvCell(value) {
    return '"' + String(value || '').replace(/\s+/g, ' ').trim().replace(/"/g, '""') + '"';
  }

  function exportRows(table, rows) {
    if (!(table instanceof HTMLTableElement)) {
      const data = [['Thông tin'], ...rows.map((item) => [item.innerText.replace(/\s+/g, ' ').trim()])];
      const blob = new Blob(['\ufeff' + data.map((line) => line.map(csvCell).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `danh-sach-da-chon-${new Date().toISOString().slice(0, 10)}.csv`;
      link.click();
      URL.revokeObjectURL(link.href);
      return;
    }
    const headers = Array.from(table.tHead?.rows?.[0]?.cells || []).slice(1).map((cell) => cell.innerText);
    const data = [headers.map(csvCell).join(',')];
    rows.forEach((row) => {
      const values = rowCells(row).slice(1).map((cell) => cell.innerText);
      data.push(values.map(csvCell).join(','));
    });
    const blob = new Blob(['\ufeff' + data.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `danh-sach-da-chon-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  async function runAction(rows, key, label) {
    if (!window.confirm(`${label} cho ${rows.length} dòng đã chọn?`)) return;
    const actions = rows.map((row) => rowActions(row).get(key));
    if (actions.some((action) => !action)) return;
    const results = await Promise.allSettled(actions.map(async (action) => {
      const options = {
        method: action.method,
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin'
      };
      if (action.form && action.method !== 'GET' && action.method !== 'DELETE') {
        options.headers['Content-Type'] = 'application/x-www-form-urlencoded;charset=UTF-8';
        options.body = new URLSearchParams(new FormData(action.form));
      } else if (action.value && action.method !== 'GET' && action.method !== 'DELETE') {
        options.headers['Content-Type'] = 'application/x-www-form-urlencoded;charset=UTF-8';
        options.body = new URLSearchParams({ action: action.value });
      }
      const response = await fetch(action.url, options);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      if ((response.headers.get('content-type') || '').includes('application/json')) {
        const result = await response.json();
        if (result.ok === false || result.success === false || result.api_status === 'error') {
          throw new Error(result.message || 'Thao tác thất bại');
        }
      }
      return response;
    }));
    const succeeded = results.filter((result) => result.status === 'fulfilled').length;
    const failed = results.length - succeeded;
    if (succeeded) {
      emitToast(`Đã xử lý ${succeeded}/${results.length} dòng.`, failed ? 'warning' : 'success');
      window.setTimeout(() => window.location.reload(), 300);
    } else {
      emitToast('Không thể thực hiện thao tác đã chọn.', 'error');
    }
  }

  function refreshToolbar(table) {
    const toolbar = document.getElementById(table.dataset.bulkToolbarId || '');
    if (!toolbar) return;
    const rows = selectedRows(table);
    const count = rows.length;
    toolbar.querySelector('[data-bulk-count]').textContent = `${count} đã chọn`;
    toolbar.querySelectorAll('[data-bulk-generated]').forEach((button) => button.remove());
    if (count) {
      const common = rowActions(rows[0]);
      common.forEach((action, key) => {
        if (!rows.every((row) => rowActions(row).has(key))) return;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = key === 'delete' ? 'btn btn-sm btn-outline-danger' : 'btn btn-sm btn-outline-primary';
        button.dataset.bulkRun = key;
        button.dataset.bulkLabel = action.label;
        button.dataset.bulkGenerated = '1';
        button.textContent = `${action.label} đã chọn`;
        toolbar.appendChild(button);
      });
    }
    const all = table instanceof HTMLTableElement
      ? table.querySelector('thead input[data-bulk-all]')
      : toolbar.querySelector('input[data-bulk-all]');
    if (all) {
      const pageRows = table instanceof HTMLTableElement
        ? Array.from(table.querySelectorAll('tbody tr[data-bulk-row] input[data-bulk-check]'))
        : Array.from(table.querySelectorAll('[data-bulk-item] input[data-bulk-check]')).filter((input) => input.closest('[data-bulk-item]').getClientRects().length > 0);
      const visibleSelected = pageRows.filter((input) => input.checked).length;
      all.checked = pageRows.length > 0 && visibleSelected === pageRows.length;
      all.indeterminate = visibleSelected > 0 && !all.checked;
    }
    toolbar.hidden = count === 0;
  }

  function setupTable(table) {
    if (!(table instanceof HTMLTableElement)) return;
    if (
      table.dataset.noBulk === '1' ||
      table.dataset.bulkDisable === '1' ||
      table.classList.contains('no-bulk') ||
      table.classList.contains('table-tax-htkk') ||
      table.closest('#supplierPage') ||
      table.closest('.supplier-modal') ||
      table.closest('#statementModal') ||
      window.location.pathname.includes('/admin/enterprise/suppliers')
    ) return;
    if (table.dataset.bulkReady === '1') {
      decorateTableRows(table, Number(table.dataset.bulkActionIndex));
      return;
    }
    if (table.querySelector('thead input[type="checkbox"]')) return;

    const actionCol = actionIndex(table);
    const pageArea = table.closest('.card') || table.parentElement;
    const hasPagination = Boolean(pageArea?.querySelector('.pagination'));
    if (actionCol < 0 && table.dataset.bulkSelect !== '1' && !hasPagination) return;
    const header = table.tHead?.rows?.[0];
    if (!header) return;
    const hasRecords = Array.from(table.tBodies).some((body) => Array.from(body.rows).some((row) => {
      const cells = rowCells(row);
      return cells.length && !cells.some((cell) => cell.colSpan > 1) &&
        (actionCol < 0 || cells[actionCol]?.querySelector('button,a,form,[hx-delete]'));
    }));
    if (!hasRecords) return;

    table.dataset.bulkActionIndex = String(actionCol);
    const selectHeader = document.createElement('th');
    selectHeader.className = 'admin-bulk-select-cell';
    selectHeader.innerHTML = '<input type="checkbox" data-bulk-all aria-label="Chọn các dòng trên trang">';
    header.insertBefore(selectHeader, header.firstElementChild);
    table.dataset.bulkReady = '1';
    decorateTableRows(table, actionCol);

    const toolbar = document.createElement('div');
    toolbar.id = `admin-bulk-${Math.random().toString(36).slice(2)}`;
    toolbar.className = 'admin-bulk-toolbar';
    toolbar.hidden = true;
    toolbar.innerHTML = '<span class="admin-bulk-count" data-bulk-count>0 đã chọn</span>';
    table.dataset.bulkToolbarId = toolbar.id;
    const anchor = table.closest('.cx-overflow-x-auto, .table-responsive') || table;
    anchor.parentNode.insertBefore(toolbar, anchor);
    refreshToolbar(table);
  }

  function decorateTableRows(table, actionCol) {
    Array.from(table.tHead?.rows || []).slice(1).forEach((row) => {
      if (row.dataset.bulkHeaderDecorated === '1') return;
      const cell = document.createElement('th');
      cell.className = 'admin-bulk-select-cell text-center text-muted';
      cell.style.verticalAlign = 'middle';
      cell.textContent = '--';
      row.insertBefore(cell, row.firstElementChild);
      row.dataset.bulkHeaderDecorated = '1';
    });
    Array.from(table.tBodies).flatMap((body) => Array.from(body.rows)).forEach((row) => {
      const cells = rowCells(row);
      if (!cells.length) return;
      if (cells.some((cell) => cell.colSpan > 1)) {
        if (cells.length === 1 && !row.dataset.bulkEmptyAdjusted) {
          cells[0].colSpan += 1;
          row.dataset.bulkEmptyAdjusted = '1';
        }
        return;
      }
      if (row.dataset.bulkRow === '1') return;
      const actionCell = actionCol >= 0 ? cells[actionCol] : null;
      if (actionCol >= 0 && !actionCell?.querySelector('button,a,form,[hx-delete]')) return;
      row.dataset.bulkRow = '1';
      const checkboxCell = document.createElement('td');
      checkboxCell.className = 'admin-bulk-select-cell';
      checkboxCell.innerHTML = '<input type="checkbox" data-bulk-check aria-label="Chọn dòng">';
      row.insertBefore(checkboxCell, row.firstElementChild);
    });
    Array.from(table.tFoot?.rows || []).forEach((row) => {
      if (row.dataset.bulkFooterDecorated === '1') return;
      const cell = document.createElement('td');
      cell.className = 'admin-bulk-select-cell';
      row.insertBefore(cell, row.firstElementChild);
      row.dataset.bulkFooterDecorated = '1';
    });
  }

  function setupCardList(list) {
    const items = Array.from(list.querySelectorAll(':scope > [data-bulk-item]'));
    if (!items.length) return;
    if (list.dataset.bulkReady !== '1') {
      list.dataset.bulkReady = '1';
      const toolbar = document.createElement('div');
      toolbar.id = `admin-bulk-${Math.random().toString(36).slice(2)}`;
      toolbar.className = 'admin-bulk-toolbar';
      toolbar.dataset.bulkTargetId = list.id || (list.id = `admin-bulk-list-${Math.random().toString(36).slice(2)}`);
      list.dataset.bulkToolbarId = toolbar.id;
      toolbar.innerHTML = '<label class="admin-bulk-count mb-0"><input type="checkbox" data-bulk-all aria-label="Chọn tất cả trên trang"> Chọn trang</label>' +
        '<span class="admin-bulk-count" data-bulk-count>0 đã chọn</span>';
      list.parentNode.insertBefore(toolbar, list);
    }
    items.forEach((item) => {
      if (item.querySelector('input[data-bulk-check]')) return;
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.dataset.bulkCheck = '';
      checkbox.setAttribute('aria-label', 'Chọn dòng');
      checkbox.className = 'admin-bulk-card-check';
      item.insertBefore(checkbox, item.firstChild);
    });
    refreshToolbar(list);
  }

  function scan() {
    scanQueued = false;
    document.querySelectorAll('table').forEach(setupTable);
    document.querySelectorAll('[data-bulk-list]').forEach(setupCardList);
  }

  function queueScan() {
    if (scanQueued) return;
    scanQueued = true;
    window.setTimeout(scan, 30);
  }

  document.addEventListener('change', (event) => {
    const table = event.target.closest('table[data-bulk-ready="1"]');
    const cardList = event.target.closest('[data-bulk-list][data-bulk-ready="1"]');
    const root = table || cardList;
    if (!root) return;
    if (event.target.matches('[data-bulk-all]')) {
      const selector = table ? 'tbody tr[data-bulk-row] input[data-bulk-check]' : '[data-bulk-item] input[data-bulk-check]';
      root.querySelectorAll(selector).forEach((input) => {
        const item = input.closest('[data-bulk-item]');
        input.checked = event.target.checked && (!item || item.getClientRects().length > 0);
      });
    }
    refreshToolbar(root);
  });

  document.addEventListener('click', (event) => {
    if (event.target.matches('input[data-bulk-check]')) event.stopPropagation();
  }, true);

  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-bulk-run]');
    if (!button) return;
    const toolbar = button.closest('.admin-bulk-toolbar');
    const targetTable = document.querySelector(`table[data-bulk-toolbar-id="${toolbar?.id || ''}"]`) ||
      document.getElementById(toolbar?.dataset.bulkTargetId || '');
    if (!targetTable) return;
    const rows = selectedRows(targetTable);
    if (button.dataset.bulkRun === 'export') exportRows(targetTable, rows);
    else runAction(rows, button.dataset.bulkRun, button.dataset.bulkLabel || 'Thực hiện thao tác');
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', scan);
  else scan();
  new MutationObserver((mutations) => {
    const changedList = mutations.some((mutation) => Array.from(mutation.addedNodes).some((node) => {
      if (!(node instanceof Element)) return false;
      return node.matches('table, tr, [data-bulk-list], [data-bulk-item]') || Boolean(node.querySelector('table, tr, [data-bulk-list], [data-bulk-item]'));
    }));
    if (changedList) queueScan();
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
