(() => {
  const SUCCESS_MESSAGE = 'Gửi thành công! Chúng tôi sẽ liên hệ lại trong vòng 30 phút.';
  const REQUEST_ERROR = 'Không thể kết nối. Vui lòng kiểm tra mạng và thử lại.';
  const SERVER_ERROR = 'Có lỗi xảy ra. Vui lòng thử lại hoặc gọi hotline.';
  const CONSENT_LABEL = 'Tôi đồng ý để công ty xử lý họ tên, số điện thoại và nội dung tôi gửi nhằm liên hệ báo giá, theo ';

  // Every contact form asks for the visitor's consent before the data is sent; the server rejects requests without it.
  function ensureConsent(form) {
    if (form.querySelector('[name="consent"]')) return;
    const button = form.querySelector('[type="submit"]');
    if (!button || !button.parentNode) return;
    const label = document.createElement('label');
    label.className = 'contact-consent';
    label.style.cssText = 'display:flex;gap:8px;align-items:flex-start;margin:12px 0;font-size:12px;line-height:1.5;text-align:left;cursor:pointer';
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.name = 'consent';
    box.value = '1';
    box.style.cssText = 'margin-top:3px;flex:none;width:auto;height:auto';
    box.addEventListener('change', () => box.removeAttribute('aria-invalid'));
    const text = document.createElement('span');
    const link = document.createElement('a');
    link.href = '/privacy';
    link.target = '_blank';
    link.rel = 'noopener';
    link.style.textDecoration = 'underline';
    link.textContent = 'Chính sách quyền riêng tư';
    text.append(CONSENT_LABEL, link, '.');
    label.append(box, text);
    button.parentNode.insertBefore(label, button);
  }

  function messageResult(response) {
    return response.json().then(
      (body) => {
        if (!body || typeof body !== 'object' || Array.isArray(body)) {
          console.error('Invalid contact response shape:', response.status);
        }
        const data = body && typeof body === 'object' && !Array.isArray(body) ? body : {};
        const ok = response.ok && data.ok === true;
        let inquiryId = undefined;
        if (typeof data.inquiry_id === 'number' || (typeof data.inquiry_id === 'string' && /^\d+$/.test(data.inquiry_id.trim()))) {
          inquiryId = data.inquiry_id;
        }
        return {
          ok,
          message: typeof data.message === 'string' && data.message
            ? data.message : (ok ? SUCCESS_MESSAGE : SERVER_ERROR),
          fields: data.fields && typeof data.fields === 'object' && !Array.isArray(data.fields)
            ? data.fields : {},
          inquiryId,
        };
      },
      (error) => {
        console.error('Invalid contact response:', error && error.name || 'UnknownError');
        return { ok: false, message: SERVER_ERROR, fields: {} };
      },
    );
  }

  // This is the only transport boundary. It always resolves to the same result shape.
  function submitInquiry(form) {
    const data = new FormData(form);
    const attr = (typeof window !== 'undefined' && window.TSLTracking && typeof window.TSLTracking.attribution === 'function')
      ? window.TSLTracking.attribution()
      : null;
    if (attr && typeof attr === 'object') {
      for (const key in attr) {
        if (Object.prototype.hasOwnProperty.call(attr, key)) {
          if (!data.has(key)) {
            data.append(key, attr[key]);
          }
        }
      }
    }
    return fetch('/lien-he', { method: 'POST', body: data }).then(
      messageResult,
      (error) => {
        console.error('Contact request failed:', error && error.name || 'UnknownError');
        return { ok: false, message: REQUEST_ERROR, fields: {} };
      },
    );
  }

  function showToast(ok, message) {
    document.getElementById('contact-toast')?.remove();

    const toast = document.createElement('div');
    toast.id = 'contact-toast';
    toast.setAttribute('role', 'alert');
    toast.style.cssText = [
      'position:fixed', 'top:24px', 'right:24px', 'z-index:9999',
      'max-width:360px', 'width:calc(100% - 48px)', 'color:#fff',
      `background:${ok ? '#16a34a' : '#e11d48'}`, 'border-radius:12px',
      'box-shadow:0 10px 30px rgba(0,0,0,.25)', 'padding:16px 20px',
      'transform:translateX(120%)', 'transition:transform .3s ease-out',
    ].join(';');
    toast.textContent = message;
    document.body.appendChild(toast);
    requestAnimationFrame(() => { toast.style.transform = 'translateX(0)'; });
    setTimeout(() => {
      toast.style.transform = 'translateX(120%)';
      setTimeout(() => toast.remove(), 350);
    }, 4500);
  }

  function setFieldErrors(form, fields) {
    form.querySelectorAll('[name]').forEach((field) => {
      if (Object.prototype.hasOwnProperty.call(fields, field.name)) {
        field.setAttribute('aria-invalid', 'true');
      } else {
        field.removeAttribute('aria-invalid');
      }
    });
  }

  function showFieldError(inputEl, msg) {
    if (!inputEl) return;
    inputEl.setAttribute('aria-invalid', 'true');
    let errEl = inputEl.parentElement.querySelector('.field-error-msg');
    if (!errEl) {
      errEl = document.createElement('span');
      errEl.className = 'field-error-msg';
      errEl.setAttribute('role', 'alert');
      errEl.style.cssText = 'display:block;color:#ef4444;font-size:12px;margin-top:4px;font-weight:500;';
      inputEl.parentElement.appendChild(errEl);
    }
    errEl.textContent = msg;
    errEl.style.display = 'block';
  }

  function clearFieldError(inputEl) {
    if (!inputEl) return;
    inputEl.removeAttribute('aria-invalid');
    const errEl = inputEl.parentElement.querySelector('.field-error-msg');
    if (errEl) errEl.style.display = 'none';
  }

  document.querySelectorAll('[data-contact-form]').forEach((form) => {
    ensureConsent(form);

    // Xóa trạng thái lỗi khi người dùng gõ
    form.querySelectorAll('input, select, textarea').forEach((el) => {
      el.addEventListener('input', () => clearFieldError(el));
    });

    // Real-time validation khi rời trường nhập (blur)
    const phoneEl = form.querySelector('[name="phone"]');
    if (phoneEl) {
      phoneEl.addEventListener('blur', () => {
        const val = phoneEl.value.replace(/[\s.-]/g, '');
        if (val && !/^(0|\+)[0-9]{8,15}$/.test(val)) {
          showFieldError(phoneEl, 'Số điện thoại không hợp lệ (8–15 chữ số, bắt đầu bằng 0 hoặc mã vùng +).');
        } else {
          clearFieldError(phoneEl);
        }
      });
    }

    const emailEl = form.querySelector('[name="email"]');
    if (emailEl) {
      emailEl.addEventListener('blur', () => {
        const val = emailEl.value.trim();
        if (val && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(val)) {
          showFieldError(emailEl, 'Địa chỉ email không đúng định dạng (VD: example@domain.com).');
        } else {
          clearFieldError(emailEl);
        }
      });
    }

    form.addEventListener('submit', (event) => {
      event.preventDefault();

      // Client-side validation: Kiểm tra họ tên
      const nameInput = form.querySelector('[name="full_name"]');
      if (nameInput && !nameInput.value.trim()) {
        showFieldError(nameInput, 'Vui lòng nhập họ và tên của bạn.');
        nameInput.focus();
        showToast(false, 'Vui lòng nhập họ và tên của bạn.');
        return;
      }

      // Client-side validation: Kiểm tra số điện thoại (VN hoặc quốc tế FDI)
      const phoneInput = form.querySelector('[name="phone"]');
      if (phoneInput) {
        const rawPhone = phoneInput.value.replace(/[\s.-]/g, '');
        const phoneRegex = /^(0|\+)[0-9]{8,15}$/;
        if (!rawPhone || !phoneRegex.test(rawPhone)) {
          showFieldError(phoneInput, 'Số điện thoại không đúng định dạng (VD: 0912345678 hoặc +86...).');
          phoneInput.focus();
          showToast(false, 'Số điện thoại không đúng định dạng. Vui lòng nhập số điện thoại hợp lệ (VD: 0912345678 hoặc +86...).');
          return;
        }
      }

      // Client-side validation: Kiểm tra email nếu có
      const emailInput = form.querySelector('[name="email"]');
      if (emailInput && emailInput.value.trim()) {
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!emailRegex.test(emailInput.value.trim())) {
          showFieldError(emailInput, 'Địa chỉ email không đúng định dạng (VD: example@domain.com).');
          emailInput.focus();
          showToast(false, 'Địa chỉ email không đúng định dạng (VD: example@domain.com).');
          return;
        }
      }

      // Client-side validation: Kiểm tra đồng ý điều khoản nếu có
      const consentBox = form.querySelector('[name="consent"]');
      if (consentBox && !consentBox.checked) {
        consentBox.setAttribute('aria-invalid', 'true');
        consentBox.focus();
        showToast(false, 'Vui lòng xác nhận đồng ý chính sách quyền riêng tư để tiếp tục.');
        return;
      }

      const button = form.querySelector('[type="submit"]');
      const label = button ? button.textContent : 'Gửi';
      if (button) {
        button.disabled = true;
        button.textContent = 'Đang gửi…';
      }

      submitInquiry(form).then((result) => {
        setFieldErrors(form, result.fields);
        showToast(result.ok, result.message);
        if (result.ok) {
          const sourcePageInput = form.querySelector('[name="source_page"]');
          const sourcePage = sourcePageInput ? sourcePageInput.value : '';
          window.TSLTracking?.push('generate_lead', {
            source_page: sourcePage,
            inquiry_id: result.inquiryId,
            event_id: 'lead-' + result.inquiryId,
          });
          if (typeof window.fbq === 'function') {
            window.fbq('track', 'Lead', {}, { eventID: 'lead-' + result.inquiryId });
          }
          form.reset();
        }
        if (button) {
          button.disabled = false;
          button.textContent = label;
        }
      });
    });
  });
})();
