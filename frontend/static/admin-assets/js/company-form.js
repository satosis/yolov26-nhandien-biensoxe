/* Company forms (add, edit, enterprise edit modal): activity select, logo/stamp preview, tax code lookup. */
function initCompanyActivitySelect(select) {
  if (!select || select.tagName === 'INPUT') return;
  if (typeof TomSelect !== 'undefined' && !select.tomselect) {
    new TomSelect(select, {
      create: true,
      createOnBlur: true,
      placeholder: 'Chọn hoặc tìm/nhập ngành nghề hoạt động',
      maxOptions: 50,
      allowEmptyOption: true,
      shouldSort: false,
      score: function(search) {
        const normSearch = (search || '').trim().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D').toLowerCase();
        if (!normSearch) return () => 1;
        return function(item) {
          const text = (item.text || item.value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D').toLowerCase();
          const idx = text.indexOf(normSearch);
          if (idx === -1) return 0;
          return (100 - idx) + (text === normSearch ? 100 : 0);
        };
      },
      render: {
        option_create: function(data, escape) {
          return '<div class="create">Thêm mới: <strong>' + escape(data.input) + '</strong>...</div>';
        },
        no_results: function(data, escape) {
          return '<div class="no-results" style="padding: 8px 12px; color: #6c757d; font-size: 12px;">Không tìm thấy ngành nghề phù hợp</div>';
        }
      }
    });
  }
}

function setCompanyActivitySelect(select, value) {
  if (!select) return;
  const val = (value || '').trim();
  if (select.tagName === 'INPUT' || select.tagName === 'TEXTAREA') {
    select.value = val;
    return;
  }
  if (select.tomselect) {
    if (val && !select.tomselect.options[val]) {
      select.tomselect.addOption({value: val, text: val});
    }
    select.tomselect.setValue(val);
  } else {
    if (val && !Array.from(select.options).some(o => o.value === val)) {
      const opt = document.createElement('option');
      opt.value = val;
      opt.textContent = val;
      select.add(opt);
    }
    select.value = val;
  }
}

function previewUploadImage(input, type) {
  const card = document.getElementById('card-' + type);
  const previewImg = document.querySelector('#preview-' + type + ' img');
  const file = input.files && input.files[0];
  if (!card) return;

  if (file) {
    if (previewImg) {
      const reader = new FileReader();
      reader.onload = function(event) {
        if (input.files && input.files[0] === file) {
          previewImg.src = event.target.result;
          card.classList.add('has-new-image');
        }
      };
      reader.readAsDataURL(file);
    }
    const filenameEl = card.querySelector('.company-asset-filename');
    if (filenameEl) filenameEl.textContent = file.name;

    const delBtn = document.getElementById('btn-delete-' + type);
    if (delBtn) delBtn.style.display = '';

    const labelSpan = document.querySelector('#btn-label-' + type + ' span');
    if (labelSpan) labelSpan.textContent = 'Thay ảnh';
  }
}

async function lookupCompanyTaxCode(prefix) {
  const field = (suffix) => document.getElementById(prefix + '-' + suffix);
  const taxCode = field('tax-code').value.trim();
  const message = document.getElementById('lookup-message');
  const btn = document.getElementById('lookup-btn');
  if (!taxCode) {
    message.className = 'd-block mt-1 text-danger small';
    message.textContent = 'Vui lòng nhập mã số thuế.';
    return;
  }
  message.className = 'd-none';
  message.textContent = '';
  btn.disabled = true;
  try {
    const resp = await fetch('/admin/vehicles/api/invoice-companies/lookup?tax_code=' + encodeURIComponent(taxCode));
    const result = await resp.json();
    if (!resp.ok || !result.ok) throw new Error(result.message || 'Không tra cứu được.');
    field('name').value = result.name || '';
    const addr = (result.address || '').trim();
    field('address').value = addr && !addr.toLowerCase().endsWith('việt nam') ? addr + ', Việt Nam' : addr;
    if (result.business_activity) setCompanyActivitySelect(field('business-activity'), result.business_activity);
    if (result.email) field('email').value = result.email;
    if (result.phone) field('phone').value = result.phone;

    const dirSelect = field('director');
    if (dirSelect && result.director_user_id != null) {
      const directorId = String(result.director_user_id);
      const directorLabel = result.director_label || (result.director_name ? (result.director_username ? `${result.director_name} (${result.director_username})` : result.director_name) : `ID ${result.director_user_id}`);
      let option = Array.from(dirSelect.options).find(opt => opt.value === directorId);
      if (!option) {
        option = document.createElement('option');
        option.value = directorId;
        option.text = directorLabel;
        option.selected = true;
        if (result.director_email) option.setAttribute('data-email', result.director_email);
        if (result.director_phone) option.setAttribute('data-phone', result.director_phone);
        if (result.director_bank_account) option.setAttribute('data-bank-account', result.director_bank_account);
        if (result.director_bank_name) option.setAttribute('data-bank-name', result.director_bank_name);
        dirSelect.add(option);
      }
      if (dirSelect.tomselect) {
        if (!dirSelect.tomselect.options[directorId]) {
          dirSelect.tomselect.addOption({
            value: directorId,
            text: directorLabel,
            email: result.director_email || '',
            phone: result.director_phone || '',
            bank_account: result.director_bank_account || '',
            bank_name: result.director_bank_name || ''
          });
        }
        dirSelect.tomselect.sync();
        dirSelect.tomselect.setValue(directorId, false);
      } else {
        dirSelect.value = directorId;
        dirSelect.dispatchEvent(new Event('change', { bubbles: true }));
      }
      onCompanyDirectorSelect(dirSelect);
    }
    if (result.bank_account) field('bank-account').value = result.bank_account;
    if (result.bank_name) setCompanyBankSelect(field('bank-name'), result.bank_name);
    message.className = 'd-none';
    message.textContent = '';
  } catch (err) {
    message.className = 'd-block mt-1 text-danger small';
    message.textContent = err.message || 'Không tra cứu được mã số thuế.';
  } finally {
    btn.disabled = false;
  }
}
