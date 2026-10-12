(() => {
  const NETWORK_ERROR = 'Không thể kết nối tới máy chủ. Vui lòng thử lại.';
  const SERVER_ERROR = 'Máy chủ không thể xử lý yêu cầu. Vui lòng thử lại.';

  function resultFrom(response) {
    if (response.redirected && new URL(response.url).pathname === '/login') {
      return Promise.resolve({
        ok: false,
        message: 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.',
        error_code: 'login_required',
        login_url: '/login',
        status: 401,
      });
    }
    return response.json().then(
      (body) => {
        if (!body || typeof body !== 'object' || Array.isArray(body)) {
          console.error('Invalid API response shape:', response.status);
          return { ok: false, message: SERVER_ERROR, status: response.status };
        }
        const data = body;
        const apiStatus = typeof data.status === 'string' ? data.status : undefined;
        const hasError = (data.error !== undefined && data.error !== null
          && data.error !== false && data.error !== '') || apiStatus === 'error';
        const message = [data.message, data.msg, data.detail, data.error]
          .find((value) => typeof value === 'string' && value.trim()) || '';
        const ok = response.ok && (data.ok === undefined || data.ok === true) && !hasError;
        return {
          ...data,
          ok,
          api_status: apiStatus,
          message: message || (ok ? '' : SERVER_ERROR),
          status: response.status,
        };
      },
      () => {
        console.error('Invalid API response:', response.status);
        return { ok: false, message: SERVER_ERROR, status: response.status };
      },
    );
  }

  // The only HTTP boundary: callers always receive a normalized result.
  window.apiRequest = (url, options) => fetch(url, options).then(
    resultFrom,
    (error) => {
      console.error('API network failure:', error && error.name || 'UnknownError');
      return { ok: false, message: NETWORK_ERROR, status: 0 };
    },
  );

  window.cameraRequest = (constraints) => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      console.error('Camera access unsupported');
      return Promise.resolve({
        ok: false,
        code: 'unsupported',
        message: 'Trình duyệt không hỗ trợ truy cập camera.',
      });
    }
    return navigator.mediaDevices.getUserMedia(constraints).then(
      (stream) => ({ ok: true, stream }),
      (error) => {
        const code = error && (
          error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError'
        )
          ? 'permission_denied'
          : 'camera_unavailable';
        console.error('Camera access failed:', error && error.name || 'UnknownError');
        return {
          ok: false,
          code,
          message: code === 'permission_denied'
            ? 'Bạn đã từ chối quyền mở camera.'
            : 'Không thể mở camera. Vui lòng kiểm tra thiết bị.',
        };
      },
    );
  };

  window.cameraDevicesRequest = () => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
      console.error('Camera enumeration unsupported');
      return Promise.resolve({ ok: false, message: 'Trình duyệt không hỗ trợ liệt kê camera.' });
    }
    return navigator.mediaDevices.enumerateDevices().then(
      (devices) => ({ ok: true, devices: devices.filter((device) => device.kind === 'videoinput') }),
      (error) => {
        console.error('Camera enumeration failed:', error && error.name || 'UnknownError');
        return { ok: false, message: 'Không thể lấy danh sách camera.' };
      },
    );
  };
})();
