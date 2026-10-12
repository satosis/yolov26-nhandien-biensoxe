(() => {
  const SUCCESS_MESSAGE = 'Gửi thành công! Chúng tôi sẽ liên hệ lại trong vòng 30 phút.';
  const REQUEST_ERROR = 'Không thể kết nối. Vui lòng kiểm tra mạng và thử lại.';
  const SERVER_ERROR = 'Có lỗi xảy ra. Vui lòng thử lại hoặc gọi hotline.';

  function messageResult(response) {
    return response.json().then(
      (body) => {
        if (!body || typeof body !== 'object' || Array.isArray(body)) {
          console.error('Invalid contact response shape:', response.status);
        }
        const data = body && typeof body === 'object' && !Array.isArray(body) ? body : {};
        const ok = response.ok && data.ok === true;
        return {
          ok,
          message: typeof data.message === 'string' && data.message
            ? data.message : (ok ? SUCCESS_MESSAGE : SERVER_ERROR),
          fields: data.fields && typeof data.fields === 'object' && !Array.isArray(data.fields)
            ? data.fields : {},
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
    return fetch('/lien-he', { method: 'POST', body: new FormData(form) }).then(
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

  document.querySelectorAll('[data-contact-form]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const button = form.querySelector('[type="submit"]');
      const label = button.textContent;
      button.disabled = true;
      button.textContent = 'Đang gửi…';

      submitInquiry(form).then((result) => {
        setFieldErrors(form, result.fields);
        showToast(result.ok, result.message);
        if (result.ok) form.reset();
        button.disabled = false;
        button.textContent = label;
      });
    });
  });
})();
