(function () {
  'use strict';

  function enhanceSelect(select) {
    if (!(select instanceof HTMLSelectElement) || select.tomselect ||
        select.matches('[data-no-search], .d-none, [hidden], .ts-hidden-accessible') ||
        typeof TomSelect === 'undefined') return;

    select.classList.add('admin-search-select');

    const emptyOpt = select.querySelector('option[value=""]');
    const placeholder = select.getAttribute('placeholder') ||
                        (emptyOpt ? emptyOpt.textContent.trim() : undefined) ||
                        '-- Chọn hoặc tìm kiếm --';

    const isMultiple = select.multiple;
    const inScrollableTable = select.closest('.table-responsive, .cx-overflow-x-auto');
    const allowCreate = select.dataset.create === 'true' ||
                        select.id === 'saleCustomer' ||
                        select.hasAttribute('data-allow-create');

    let control;
    try {
      control = new TomSelect(select, {
        create: allowCreate,
        createOnBlur: allowCreate,
        maxItems: isMultiple ? null : 1,
        allowEmptyOption: true,
        searchField: ['text'],
        placeholder: placeholder,
        dropdownClass: 'ts-dropdown admin-search-dropdown',
        dropdownParent: inScrollableTable ? 'body' : null,
        render: {
          no_results: function () {
            return '<div class="no-results">Không tìm thấy kết quả</div>';
          },
          option_create: function (data, escape) {
            return '<div class="create" style="padding: 6px 12px; cursor: pointer; color: #2563eb; background: #eff6ff; font-weight: 500;"><i class="bx bx-plus mr-1"></i>Tạo khách hàng mới: <strong>' + escape(data.input) + '</strong></div>';
          }
        }
      });
    } catch (err) {
      console.warn('TomSelect init error on select:', select, err);
      return;
    }

    if (control && control.wrapper) {
      control.wrapper.classList.add('admin-search-select');
      if (select.classList.contains('form-control-sm')) control.wrapper.classList.add('form-control-sm');
      if (select.classList.contains('form-control-lg')) control.wrapper.classList.add('form-control-lg');

      for (const property of ['width', 'minWidth', 'maxWidth']) {
        if (select.style[property]) control.wrapper.style[property] = select.style[property];
      }

      if (select.disabled) {
        control.disable();
      }

      // Bidirectional sync between TomSelect and underlying native select
      let isSyncing = false;
      control.on('change', () => {
        if (isSyncing) return;
        isSyncing = true;
        try {
          select.dispatchEvent(new Event('input', { bubbles: true }));
          select.dispatchEvent(new Event('change', { bubbles: true }));
        } finally {
          isSyncing = false;
        }
      });

      select.addEventListener('change', () => {
        if (isSyncing) return;
        const currentVal = control.getValue();
        const nextVal = select.multiple
          ? Array.from(select.selectedOptions).map(o => o.value)
          : select.value;
        if (String(currentVal) !== String(nextVal)) {
          isSyncing = true;
          try {
            control.setValue(nextVal, true);
          } finally {
            isSyncing = false;
          }
        }
      });
    }
  }

  function enhanceIn(root) {
    if (!root) return;
    if (root instanceof HTMLSelectElement) enhanceSelect(root);
    if (root.querySelectorAll) root.querySelectorAll('select').forEach(enhanceSelect);
  }

  // Keep the visible control in step with options added by Alpine or page scripts.
  const changedSelects = new Set();
  let scheduled = false;
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    setTimeout(() => {
      scheduled = false;
      changedSelects.forEach(select => {
        if (!select.isConnected) {
          if (select.tomselect) select.tomselect.destroy();
          return;
        }
        if (select.tomselect) select.tomselect.sync();
        else enhanceSelect(select);
      });
      changedSelects.clear();
    }, 0);
  }

  function handleModalShown(e) {
    const modal = e.target;
    if (!modal || !modal.querySelectorAll) return;
    modal.querySelectorAll('select').forEach(select => {
      if (select.tomselect) {
        select.tomselect.sync();
      } else {
        enhanceSelect(select);
      }
    });
  }

  function start() {
    enhanceIn(document.body);

    document.addEventListener('reset', event => {
      const form = event.target;
      if (!(form instanceof HTMLFormElement)) return;
      setTimeout(() => form.querySelectorAll('select').forEach(select => select.tomselect?.sync()), 0);
    }, true);

    document.addEventListener('shown.bs.modal', handleModalShown);
    if (typeof jQuery !== 'undefined') {
      jQuery(document).on('shown.bs.modal', handleModalShown);
    }

    const observer = new MutationObserver(records => {
      for (const record of records) {
        const target = record.target;
        const select = target instanceof HTMLSelectElement ? target : target.closest?.('select');
        if (select) changedSelects.add(select);
        for (const node of record.addedNodes) {
          if (node.nodeType !== Node.ELEMENT_NODE) continue;
          if (node instanceof HTMLSelectElement) changedSelects.add(node);
          node.querySelectorAll?.('select').forEach(item => changedSelects.add(item));
        }
        for (const node of record.removedNodes) {
          if (node.nodeType !== Node.ELEMENT_NODE) continue;
          if (node instanceof HTMLSelectElement) changedSelects.add(node);
          node.querySelectorAll?.('select').forEach(item => changedSelects.add(item));
        }
      }
      if (changedSelects.size) schedule();
    });
    observer.observe(document.body, {childList: true, subtree: true});
  }

  window.setAdminSelectValue = function (select, value) {
    if (!select) return;
    const next = String(value ?? '');
    if (select.tomselect) select.tomselect.setValue(next, true);
    else select.value = next;
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(start, 0), {once: true});
  } else {
    setTimeout(start, 0);
  }
})();
