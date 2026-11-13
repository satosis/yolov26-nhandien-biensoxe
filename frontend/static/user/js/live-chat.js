/**
 * ══════════════════════════════════════════════════════════════
 * HỆ THỐNG LIÊN HỆ ĐA KÊNH & LIVE CHAT (ZALO · FB · CHATBOT)
 * Module JS Độc Lập — Tách khỏi HTML để tối ưu hiệu năng
 * Hỗ trợ A11y, Loading State, Form Validation, Landscape Mode
 * ══════════════════════════════════════════════════════════════
 */
(function initFriendlyContactWidget() {
  var rootEl     = document.getElementById('slContactRoot');
  var drawer     = document.getElementById('slChatDrawer');
  var trigger    = document.getElementById('slChatTrigger');
  var closeBtn   = document.getElementById('slChatCloseBtn');
  var form       = document.getElementById('slChatForm');
  var input      = document.getElementById('slChatInput');
  var contactInp = document.getElementById('slChatContact');
  var messages   = document.getElementById('slChatMessages');
  var badge      = document.getElementById('slChatServiceBadge');
  var unreadPill = document.getElementById('slChatUnreadBadge');
  var agentTitle = document.getElementById('slChatTitle') || document.getElementById('slAgentTitle');
  var sendBtn    = document.getElementById('slChatSendBtn');
  var sendIcon   = sendBtn ? sendBtn.querySelector('.sl-send-icon') : null;
  var spinner    = document.getElementById('slChatSpinner');
  var contactErr = document.getElementById('slContactError');

  var contactPrompt  = document.getElementById('slContactPrompt');
  var saveContactBtn = document.getElementById('slSaveContactBtn');
  var skipContactBtn = document.getElementById('slSkipContactBtn');
  var contactToast   = document.getElementById('slContactToast');
  var backdrop       = document.getElementById('slChatBackdrop');
  var handleWrap     = document.getElementById('slSheetHandleWrap');
  var chipsWrap      = document.getElementById('slQuickChipsWrap');
  var isAuthenticated = rootEl ? rootEl.getAttribute('data-authenticated') === '1' : false;
  var toastTimer     = null;

  function getSavedContact() {
    try { return (localStorage.getItem('sl_user_contact') || '').trim(); } catch(e) { return ''; }
  }

  function isContactDismissed() {
    try { return sessionStorage.getItem('sl_contact_dismissed') === '1'; } catch(e) { return false; }
  }

  function validateContactString(val) {
    if (!val) return true;
    var rawPhone = val.replace(/[\s.-]/g, '');
    var phoneRegex = /^(0|\+)[0-9]{8,15}$/;
    var emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return phoneRegex.test(rawPhone) || emailRegex.test(val);
  }

  function showContactError(msg) {
    if (contactInp) contactInp.setAttribute('aria-invalid', 'true');
    if (contactErr) {
      contactErr.textContent = msg;
      contactErr.classList.add('visible');
      contactErr.style.display = 'block';
    }
  }

  function clearContactError() {
    if (contactInp) contactInp.removeAttribute('aria-invalid');
    if (contactErr) {
      contactErr.classList.remove('visible');
      contactErr.style.display = 'none';
    }
  }

  function syncContactPrompt() {
    if (!contactPrompt) return;
    var hasContact = !!getSavedContact();
    var userMsgCount = messages ? messages.querySelectorAll('.sl-msg-user').length : 0;
    var shouldShow = !isAuthenticated && !hasContact && !isContactDismissed() && userMsgCount >= 1;
    contactPrompt.style.display = shouldShow ? 'flex' : 'none';
  }

  function restoreSavedContact() {
    var savedContact = getSavedContact();
    if (savedContact && !validateContactString(savedContact)) {
      savedContact = '';
      try { localStorage.removeItem('sl_user_contact'); } catch(e) {}
    }
    if (contactInp && !contactInp.value && savedContact) {
      contactInp.value = savedContact;
    }
    syncContactPrompt();
  }

  function flashContactToast() {
    if (!contactToast) return;
    clearTimeout(toastTimer);
    contactToast.style.display = 'block';
    toastTimer = setTimeout(function() {
      contactToast.style.display = 'none';
    }, 3000);
  }

  function saveContactEdits() {
    var c = contactInp ? contactInp.value.trim() : '';
    if (!c) {
      if (contactInp) contactInp.focus();
      return false;
    }
    if (!validateContactString(c)) {
      var dict = CHAT_I18N[getCurrentLang()] || CHAT_I18N.vi;
      showContactError(dict.contactErr);
      if (contactInp) contactInp.focus();
      return false;
    }
    clearContactError();
    try { localStorage.setItem('sl_user_contact', c); } catch(e) {}
    syncContactPrompt();
    flashContactToast();
    if (sessionToken) {
      var syncFd = new FormData();
      syncFd.append('session_token', sessionToken);
      syncFd.append('customer_name', '');
      syncFd.append('customer_contact', c);
      syncFd.append('service_name', currentService ? currentService.name : '');
      syncFd.append('service_group', currentService ? currentService.group : '');
      syncFd.append('service_category', currentService ? currentService.category : '');
      syncFd.append('current_url', currentService ? currentService.url : window.location.href);
      fetch('/api/chat/session', { method: 'POST', body: syncFd }).catch(function() {});
    }
    if (input) input.focus();
    return true;
  }

  if (saveContactBtn) {
    saveContactBtn.addEventListener('click', saveContactEdits);
  }
  if (skipContactBtn) {
    skipContactBtn.addEventListener('click', function() {
      clearContactError();
      if (contactInp && !validateContactString(contactInp.value.trim())) {
        contactInp.value = '';
      }
      try { sessionStorage.setItem('sl_contact_dismissed', '1'); } catch(e) {}
      syncContactPrompt();
      if (input) input.focus();
    });
  }
  if (contactInp) {
    contactInp.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        saveContactEdits();
      }
    });
  }

  if (!rootEl || !drawer || !trigger) return;

  // ══ TỰ ĐỘNG NHẬN DIỆN MÀU NỀN TRANG ĐỂ ÁP DỤNG THEME SÁNG / TỐI HÀI HÒA ══
  function applyAdaptiveTheme() {
    var path = window.location.pathname;
    var isDark = false;

    // Phân hệ bán tài khoản (/tai-khoan) hoặc trang cố định dark mode
    if (path.startsWith('/tai-khoan')) {
      isDark = true;
    } else if (document.documentElement.classList.contains('dark') || document.body.classList.contains('dark')) {
      isDark = true;
    }

    rootEl.classList.remove('sl-theme-light', 'sl-theme-dark');
    rootEl.classList.add(isDark ? 'sl-theme-dark' : 'sl-theme-light');
    if (drawer) {
      drawer.classList.remove('sl-theme-light', 'sl-theme-dark');
      drawer.classList.add(isDark ? 'sl-theme-dark' : 'sl-theme-light');
    }
  }
  applyAdaptiveTheme();

  // ══ HỖ TRỢ ĐA NGÔN NGỮ (VIỆT - TRUNG) CHO LIVE CHAT DÀNH CHO FDI ══
  var CHAT_I18N = {
    vi: {
      dockChat: 'Tư vấn 24/7',
      servicePrefix: 'Đang xem:',
      senderName: function(brand) { return 'Chuyên viên ' + brand; },
      contactPlaceholder: 'SĐT / Zalo nhận báo giá…',
      saveContact: 'Lưu',
      skipContact: 'Bỏ qua',
      contactSavedToast: '✓ Đã lưu SĐT / Zalo liên hệ',
      inputPlaceholder: 'Nhập nội dung cần tư vấn…',
      contactErr: 'Số điện thoại hoặc Email không hợp lệ. Vui lòng kiểm tra lại.',
      justNow: 'Vừa xong',
      sendingStatus: 'Đang gửi…',
      sentStatus: '✓ Đã gửi',
      sendFailed: 'Chưa gửi được',
      retryBtn: 'Thử lại',
      consultant: function(brand) { return 'Tư vấn viên ' + brand; },
      you: 'Bạn',
      consultPrefix: 'Tôi cần tư vấn về ',
      callLine: function(phone) { return 'Cần gấp, mình gọi em theo số ' + phone + ' nhé ạ 📞'; }
    },
    zh: {
      dockChat: '24/7 在线咨询',
      servicePrefix: '当前咨询:',
      senderName: function(brand) { return brand + ' 专业顾问'; },
      contactPlaceholder: '电话 / Zalo（选填）…',
      saveContact: '保存',
      skipContact: '跳过',
      contactSavedToast: '✓ 已保存您的联系方式',
      inputPlaceholder: '在此输入您的咨询需求…',
      contactErr: '请输入有效的联系电话（至少8位数字）或电子邮箱。',
      justNow: '刚刚',
      sendingStatus: '发送中…',
      sentStatus: '✓ 已发送',
      sendFailed: '发送失败',
      retryBtn: '重试',
      consultant: function(brand) { return brand + ' 顾问'; },
      you: '您',
      consultPrefix: '我想咨询关于 ',
      callLine: function(phone) { return '如需加急，请致电 ' + phone + ' 📞'; }
    }
  };

  // ══ GỢI Ý GỌN NHẸ THEO TRANG (tối đa 2 gợi ý và tự ẩn sau tin nhắn đầu) ══
  var TOPICS = {
    crane: {
      brand: 'ĐẠI LỘ XANH',
      phone: '0919497568',
      vi: {
        greeting: 'Cần thuê xe cẩu? Nhắn em nhé!',
        welcome: 'Dạ em chào anh/chị ạ! 👋 Em có thể giúp mình báo giá thuê xe cẩu tự hành 8 – 10 tấn theo ca, theo chuyến hoặc trọn gói. Anh/chị cứ nhắn nhu cầu, em phản hồi ngay nhé!',
        chips: [['Báo giá thuê xe cẩu', 'Tư vấn và báo giá thuê xe cẩu tự hành'],
                ['Xe cẩu có sẵn không', 'Xe cẩu tự hành bên mình có sẵn phục vụ ngay không?']]
      },
      zh: {
        greeting: '需要租用随车吊吗？欢迎联系！',
        welcome: '您好！👋 我们为您提供 8–10 吨随车起重机租赁咨询，可按班次、按趟或整体打包报价。',
        chips: [['索取吊车报价', '请提供随车起重机租赁报价'],
                ['可立即出车吗', '请问随车起重机现在可以立即安排吗？']]
      }
    },
    weights: {
      brand: 'ĐẠI LỘ XANH',
      phone: '0919497568',
      vi: {
        greeting: 'Cần thuê quả cân? Nhắn em nhé!',
        welcome: 'Dạ em chào anh/chị ạ! 👋 Em có thể giúp mình báo giá thuê quả cân chuẩn, tính số quả cân cho trạm cân 60 – 120 tấn và kiểm định cân điện tử. Anh/chị cứ nhắn nhu cầu, em phản hồi ngay nhé!',
        chips: [['Báo giá thuê quả cân', 'Tư vấn và báo giá thuê quả cân chuẩn'],
                ['Cần bao nhiêu quả cân', 'Trạm cân của tôi cần thuê bao nhiêu quả cân chuẩn?']]
      },
      zh: {
        greeting: '需要租用标准砝码吗？欢迎联系！',
        welcome: '您好！👋 我们为您提供标准砝码租赁、60–120 吨地磅所需砝码数量及电子秤检定咨询。',
        chips: [['索取砝码报价', '请提供标准砝码租赁报价'],
                ['需要多少砝码', '我们的地磅需要租用多少标准砝码？']]
      }
    },
    container: {
      brand: 'ĐẠI LỘ XANH',
      phone: '0919497568',
      vi: {
        greeting: 'Cần thuê container? Nhắn em nhé!',
        welcome: 'Dạ em chào anh/chị ạ! 👋 Em có thể giúp mình báo giá thuê container văn phòng, container kho, kèm giao và cẩu hạ tận nơi. Anh/chị cứ nhắn nhu cầu, em phản hồi ngay nhé!',
        chips: [['Báo giá thuê container', 'Tư vấn và báo giá thuê container'],
                ['Container có sẵn không', 'Container văn phòng và container kho bên mình có sẵn giao ngay không?']]
      },
      zh: {
        greeting: '需要租用集装箱吗？欢迎联系！',
        welcome: '您好！👋 我们为您提供办公集装箱、仓储集装箱租赁及送货吊装到位咨询。',
        chips: [['索取集装箱报价', '请提供集装箱租赁报价'],
                ['有现货可送达吗', '办公和仓储集装箱现在有货并可立即送达吗？']]
      }
    },
    solar: {
      brand: 'ĐẠI LỘ XANH',
      phone: '0919497568',
      vi: {
        greeting: 'Cần điện mặt trời? Nhắn em nhé!',
        welcome: 'Dạ em chào anh/chị ạ! 👋 Em có thể giúp mình chọn thiết bị điện mặt trời và báo giá theo công suất cho nhà xưởng. Anh/chị cứ nhắn nhu cầu, em phản hồi ngay nhé!',
        chips: [['Báo giá điện mặt trời', 'Tư vấn và báo giá thiết bị điện mặt trời'],
                ['Tư vấn công suất', 'Tôi cần tư vấn chọn công suất điện mặt trời cho nhà xưởng']]
      },
      zh: {
        greeting: '需要光伏设备吗？欢迎联系！',
        welcome: '您好！👋 我们为您提供光伏设备选型及按功率报价咨询。',
        chips: [['索取光伏报价', '请提供光伏设备报价'],
                ['功率选型咨询', '我们需要为厂房选择合适的光伏功率']]
      }
    },
    photo: {
      brand: 'ĐẠI LỘ XANH',
      phone: '0919497568',
      vi: {
        greeting: 'Cần thuê máy ảnh? Nhắn em nhé!',
        welcome: 'Dạ em chào anh/chị! 👋 Em sẵn sàng tư vấn thuê máy ảnh, ống kính và photobooth cho sự kiện của mình nhé ạ!',
        chips: [['Báo giá thuê máy ảnh', 'Tư vấn và báo giá thuê máy ảnh'],
                ['Thuê photobooth', 'Tôi muốn thuê photobooth cho sự kiện']]
      },
      zh: {
        greeting: '需要租用相机吗？欢迎联系！',
        welcome: '您好！👋 我们为您提供相机、镜头及拍照亭租赁咨询。',
        chips: [['索取相机租赁报价', '请提供相机租赁报价'],
                ['租用拍照亭', '我们想为活动租用拍照亭']]
      }
    },
    install: {
      brand: 'ĐẠI LỘ XANH',
      phone: '0919497568',
      vi: {
        greeting: 'Cần cẩu lắp đặt thiết bị? Nhắn em nhé!',
        welcome: 'Dạ em chào anh/chị ạ! 👋 Em có thể giúp mình báo giá cẩu hạ, định vị và lắp đặt máy móc thiết bị trong nhà xưởng. Anh/chị cứ nhắn nhu cầu, em phản hồi ngay nhé!',
        chips: [['Báo giá cẩu lắp đặt', 'Tư vấn và báo giá cẩu lắp đặt thiết bị'],
                ['Khảo sát hiện trường', 'Tôi muốn bên mình khảo sát hiện trường trước khi báo giá']]
      },
      zh: {
        greeting: '需要设备吊装就位吗？欢迎联系！',
        welcome: '您好！👋 我们为您提供厂房内机器设备吊装、定位及安装咨询。',
        chips: [['索取吊装报价', '请提供设备吊装安装报价'],
                ['预约现场勘察', '我们希望报价前先到现场勘察']]
      }
    },
    scale: {
      brand: 'ĐẠI LỘ XANH',
      phone: '0912559868',
      vi: {
        greeting: 'Cần kiểm định cân? Nhắn em nhé!',
        welcome: 'Dạ em chào anh/chị ạ! 👋 Em có thể giúp mình báo giá kiểm định cân, cấp giấy chứng nhận và cho thuê quả cân chuẩn. Anh/chị cứ nhắn nhu cầu, em phản hồi ngay nhé!',
        chips: [['Báo giá kiểm định cân', 'Tư vấn và báo giá kiểm định cân'],
                ['Đặt lịch kiểm định', 'Tôi muốn đặt lịch kiểm định cân tại chỗ']]
      },
      zh: {
        greeting: '需要电子秤检定吗？欢迎联系！',
        welcome: '您好！👋 我们为您提供电子秤检定、出具合格证书及标准砝码租赁咨询。',
        chips: [['索取检定报价', '请提供电子秤检定报价'],
                ['预约上门检定', '我们想预约上门检定时间']]
      }
    },
    factory: {
      brand: 'THÉP SƠN LÂM',
      phone: '0986565666',
      vi: {
        greeting: 'Cần tư vấn nhà xưởng? Nhắn em nhé!',
        welcome: 'Dạ em chào anh/chị ạ! 👋 Em có thể giúp mình báo giá nhà xưởng, diện tích cho thuê và trạm biến áp. Anh/chị cứ nhắn nhu cầu, em phản hồi ngay nhé!',
        chips: [['Báo giá chi tiết', 'Tư vấn và báo giá chi tiết nhà xưởng này'],
                ['Hẹn xem xưởng', 'Tôi muốn kiểm tra xưởng trống và hẹn lịch xem xưởng']]
      },
      zh: {
        greeting: '需要厂房租赁咨询吗？欢迎联系！',
        welcome: '您好！👋 我们为您提供现成厂房租赁、电力配套报价及参观预约咨询。',
        chips: [['索取详细报价', '请提供该项目的详细租金标准与厂房技术参数报价'],
                ['预约看厂', '请问现有厂房可交付吗？我想预约实地参观']]
      }
    },
    accshop: {
      brand: 'ACCSHOP',
      phone: '0949561668',
      vi: {
        greeting: 'Cần chọn gói tài khoản? Nhắn em nhé!',
        welcome: 'Dạ em chào anh/chị ạ! 👋 Em có thể giúp mình chọn gói tài khoản, thanh toán và bảo hành. Anh/chị cứ nhắn nhu cầu, em phản hồi ngay nhé!',
        chips: [['Tư vấn chọn gói', 'Tôi cần tư vấn chọn gói tài khoản phù hợp'],
                ['Bảo hành & Thanh toán', 'Chính sách bảo hành 1 đổi 1 và cách thanh toán QR như thế nào?']]
      },
      zh: {
        greeting: '需要选择账号套餐吗？欢迎联系！',
        welcome: '您好！👋 我们为您提供账号套餐选择、付款及售后咨询。',
        chips: [['套餐选择咨询', '我需要咨询选择合适的账号套餐'],
                ['售后与付款方式', '店铺的1换1售后政策及扫码付款方式是什么？']]
      }
    },
    general: {
      brand: 'THÉP SƠN LÂM',
      phone: '0986565666',
      vi: {
        greeting: 'Cần tư vấn? Nhắn em nhé!',
        welcome: 'Dạ em chào anh/chị ạ! 👋 Em có thể giúp mình tư vấn và báo giá dịch vụ. Anh/chị cứ nhắn nhu cầu, em phản hồi ngay nhé!',
        chips: [['Báo giá chi tiết', 'Tư vấn và báo giá chi tiết dịch vụ này'],
                ['Hỗ trợ ngay không?', 'Dịch vụ này bên mình có sẵn và hỗ trợ ngay không?']]
      },
      zh: {
        greeting: '需要咨询吗？欢迎联系！',
        welcome: '您好！👋 我们为您提供咨询与报价服务。',
        chips: [['索取详细报价', '请提供该项目的详细报价'],
                ['现在可以提供吗', '请问该服务现在可以立即提供吗？']]
      }
    }
  };

  function pageTopic() {
    var path = window.location.pathname;
    if (path.indexOf('/tai-khoan') === 0) return 'accshop';
    if (path.indexOf('container') !== -1) return 'container';
    if (path.indexOf('qua-can') !== -1) return 'weights';
    if (path.indexOf('dien-mat-troi') !== -1) return 'solar';
    if (path.indexOf('thue-may-anh') !== -1) return 'photo';
    if (path.indexOf('cau-lap-dat') !== -1) return 'install';
    if (path.indexOf('/xe-cau') === 0 || path.indexOf('/xecau') === 0 || path.indexOf('/thue-xe-cau') === 0 || path.indexOf('/dich-vu-xe-cau') === 0) return 'crane';
    if (path.indexOf('/kiem-dinh-can') === 0) return 'scale';
    if (path.indexOf('/nhaxuong') === 0 || path.indexOf('/nha-xuong') === 0) return 'factory';
    return 'general';
  }
  var topic = TOPICS[pageTopic()];

  // Số của trang dịch vụ đang xem (cài đặt của trang, đang hiện ở nút Gọi), không có thì dùng số mặc định của chủ đề
  function servicePhone() {
    var link = document.querySelector('a[href^="tel:"]');
    var digits = (rootEl.getAttribute('data-phone') || (link && link.getAttribute('href').replace(/^tel:/, '')) || topic.phone).replace(/\D/g, '');
    return digits.length === 10 ? digits.replace(/(\d{4})(\d{3})(\d{3})/, '$1 $2 $3') : digits;
  }

  function getCurrentLang() {
    try {
      var l = localStorage.getItem('sl_lang');
      if (l === 'zh' || l === 'vi') return l;
      var m = document.cookie.match(/lang_pref=([^;]+)/);
      if (m && (m[1] === 'zh' || m[1] === 'vi')) return m[1];
      var browserLang = (navigator.language || '').toLowerCase();
      if (browserLang.startsWith('zh')) return 'zh';
    } catch(e) {}
    return 'vi';
  }

  // Nhận diện dịch vụ theo trang hiện tại
  function detectPageService(lang) {
    var activeLang = lang || getCurrentLang();
    var isZh = (activeLang === 'zh');
    var path = window.location.pathname;
    var search = window.location.search;
    var urlParams = new URLSearchParams(search);

    var group = urlParams.get('group') || '';
    var cat = urlParams.get('category') || '';
    var q = urlParams.get('q') || '';

    var svcName = '';
    if (path.startsWith('/tai-khoan')) {
      if (isZh) {
        if (cat && group) svcName = '数字账号 ' + cat + ' (' + group + ')';
        else if (cat) svcName = '账号服务 ' + cat;
        else if (group) svcName = '账号分类: ' + group;
        else if (q) svcName = '搜索账号: ' + q;
        else svcName = 'ACCSHOP 数字账号商城';
      } else {
        if (cat && group) svcName = 'Tài khoản ' + cat + ' (Nhóm: ' + group + ')';
        else if (cat) svcName = 'Dịch vụ ' + cat;
        else if (group) svcName = 'Nhóm tài khoản: ' + group;
        else if (q) svcName = 'Tìm tài khoản: ' + q;
        else svcName = 'Siêu thị tài khoản số (ACCSHOP)';
      }
    } else if (path.includes('container')) {
      svcName = isZh ? '集装箱仓库与办公租赁服务' : 'Dịch vụ Cho thuê Container Kho & Văn phòng';
    } else if (path.includes('qua-can')) {
      svcName = isZh ? '标准计量砝码租赁与检定服务' : 'Cho thuê Quả cân chuẩn & Kiểm định cân';
    } else if (path.includes('dien-mat-troi')) {
      svcName = isZh ? '工商业屋顶光伏投资解决方案' : 'Điện mặt trời áp mái Nhà xưởng & KCN';
    } else if (path.includes('thue-may-anh')) {
      svcName = isZh ? '相机与拍照亭租赁服务' : 'Cho thuê Máy ảnh & Photobooth';
    } else if (path.startsWith('/dich-vu-xe-cau') || path.startsWith('/xe-cau') || path.startsWith('/thue-xe-cau')) {
      if (isZh) {
        svcName = '随车起重机 24/7 租赁与吊装服务';
      } else {
        svcName = 'Dịch vụ Cho thuê xe cẩu tự hành';
        var pageHeading = document.querySelector('h1');
        if (pageHeading && pageHeading.textContent) {
          svcName = pageHeading.textContent.trim().substring(0, 75);
        }
      }
    } else if (path.startsWith('/nhaxuong')) {
      svcName = isZh ? '富寿省中河工业区标准厂房 (4,032 m²)' : 'Nhà xưởng KCN Trung Hà (Phú Thọ)';
    } else if (path.startsWith('/nha-xuong-co-tiet')) {
      svcName = isZh ? '富寿省古节工业区标准厂房' : 'Nhà xưởng KCN Cổ Tiết (Phú Thọ)';
    } else if (path.startsWith('/tin-tuc')) {
      if (isZh) {
        svcName = '工业园区新闻与投资动态';
      } else {
        var newsTitle = document.querySelector('h1');
        svcName = newsTitle ? 'Tin tức: ' + newsTitle.textContent.trim().substring(0, 70) : 'Trang Tin tức & Sự kiện';
      }
    } else if (path.startsWith('/du-an')) {
      svcName = isZh ? '重点工业园区与厂房项目' : 'Dự án & Năng lực thi công';
    } else if (path.startsWith('/dich-vu')) {
      svcName = isZh ? '工业基础设施与配套服务' : 'Dịch vụ Cơ khí & Công nghiệp';
    } else if (path.startsWith('/kho-bai')) {
      svcName = isZh ? '现代仓储与电子地磅服务' : 'Dịch vụ Kho bãi & Cân điện tử';
    } else if (path.startsWith('/lien-he')) {
      svcName = isZh ? '联系我们与快速报价' : 'Trang Liên hệ & Báo giá';
    } else if (path === '/' || path === '') {
      svcName = isZh ? 'THÉP SƠN LÂM 工业核心官网' : 'Trang chủ Thép Sơn Lâm & Đại Lộ Xanh';
    } else {
      svcName = isZh ? 'THÉP SƠN LÂM 在线服务系统' : (document.title || 'Dịch vụ hệ thống').split('|')[0].split('-')[0].trim();
    }

    return {
      name: svcName,
      group: group,
      category: cat,
      url: window.location.href
    };
  }

  var currentService = detectPageService(getCurrentLang());

  function updatePageServiceBadge(lang) {
    currentService = detectPageService(lang);
    if (badge) {
      badge.textContent = currentService.name;
      badge.title = currentService.url;
    }
  }

  function applyChatLanguage(lang) {
    var dict = CHAT_I18N[lang] || CHAT_I18N.vi;
    var words = topic[lang] || topic.vi;
    var dockChatTxt = rootEl.querySelector('.sl-btn-chat .sl-dock-text');
    if (dockChatTxt) dockChatTxt.textContent = dict.dockChat;
    if (agentTitle) agentTitle.textContent = topic.brand;

    var svcLabel = drawer.querySelector('.sl-svc-label');
    if (svcLabel) svcLabel.textContent = dict.servicePrefix;

    var senderNameEl = messages ? messages.querySelector('.sl-msg-bubble.sl-msg-bot:first-child .sl-msg-sender-name') : null;
    if (senderNameEl) senderNameEl.textContent = dict.senderName(topic.brand);

    if (contactInp) contactInp.setAttribute('placeholder', dict.contactPlaceholder);
    if (saveContactBtn) saveContactBtn.textContent = dict.saveContact;
    if (skipContactBtn) {
      skipContactBtn.setAttribute('title', dict.skipContact);
      skipContactBtn.setAttribute('aria-label', dict.skipContact);
    }
    if (contactToast) contactToast.textContent = dict.contactSavedToast;
    if (input) input.setAttribute('placeholder', dict.inputPlaceholder);

    var firstBotMsg = messages.querySelector('.sl-msg-bubble.sl-msg-bot:first-child .sl-msg-text');
    if (firstBotMsg && !firstBotMsg.hasAttribute('data-custom')) {
      firstBotMsg.textContent = words.welcome + '\n' + dict.callLine(servicePhone());
    }
    var chipBox = document.getElementById('slQuickChips') || messages.querySelector('.sl-quick-chips');
    if (chipBox) {
      chipBox.textContent = '';
      words.chips.forEach(function(chip) {
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'sl-chip-btn';
        button.setAttribute('data-quick-chip', chip[1]);
        var label = document.createElement('span');
        label.textContent = chip[0];
        button.appendChild(label);
        chipBox.appendChild(button);
      });
    }

    updatePageServiceBadge(lang);
    syncContactPrompt();
  }

  window.addEventListener('sl-lang-changed', function(e) {
    var lang = (e.detail && e.detail.lang) || getCurrentLang();
    applyChatLanguage(lang);
  });
  restoreSavedContact();
  applyChatLanguage(getCurrentLang());

  var renderedIds = {};
  var sessionToken = localStorage.getItem('accshop_live_chat_token') || '';

  function syncQuickChipsState() {
    if (!messages) return;
    var realMsgCount = messages.querySelectorAll('.sl-msg-bubble:not(.sl-msg-welcome):not(.sl-typing-bubble)').length;
    if (chipsWrap) {
      chipsWrap.classList.toggle('is-hidden', realMsgCount >= 1);
    }
    syncContactPrompt();
  }

  function openDrawer() {
    drawer.classList.add('open');
    if (backdrop) backdrop.classList.add('open');
    rootEl.classList.add('sl-drawer-active');
    trigger.setAttribute('aria-expanded', 'true');
    clearUnread();
    syncQuickChipsState();
    requestAnimationFrame(scrollDown);
    pollMessages();
    scheduleNextPoll();
    if (input) {
      setTimeout(function() { input.focus(); }, 120);
    }
  }

  function closeDrawer() {
    drawer.style.transform = '';
    drawer.classList.remove('open');
    if (backdrop) backdrop.classList.remove('open');
    rootEl.classList.remove('sl-drawer-active');
    trigger.setAttribute('aria-expanded', 'false');
    scheduleNextPoll();
  }

  if (backdrop) {
    backdrop.addEventListener('click', closeDrawer);
  }
  if (handleWrap) {
    handleWrap.addEventListener('click', closeDrawer);
  }
  // Vuốt xuống trên tiêu đề / thanh kéo để đóng Bottom Sheet trên mobile
  (function bindMobileSwipeClose() {
    var swipeTarget = handleWrap || drawer.querySelector('.sl-chat-header');
    if (!swipeTarget) return;
    var startY = 0;
    var deltaY = 0;
    var dragging = false;
    [handleWrap, drawer.querySelector('.sl-chat-header')].forEach(function(el) {
      if (!el) return;
      el.addEventListener('touchstart', function(e) {
        if (window.innerWidth > 640 || !drawer.classList.contains('open')) return;
        if (!e.touches || !e.touches.length) return;
        startY = e.touches[0].clientY;
        deltaY = 0;
        dragging = true;
      }, { passive: true });
      el.addEventListener('touchmove', function(e) {
        if (!dragging || !e.touches || !e.touches.length) return;
        deltaY = e.touches[0].clientY - startY;
        if (deltaY > 0) {
          drawer.style.transition = 'none';
          drawer.style.transform = 'translateY(' + deltaY + 'px)';
        }
      }, { passive: true });
      el.addEventListener('touchend', function() {
        if (!dragging) return;
        dragging = false;
        drawer.style.transition = '';
        if (deltaY > 70) {
          closeDrawer();
        } else {
          drawer.style.transform = '';
        }
      }, { passive: true });
    });
  })();

  // Tự động làm mờ dock khi người dùng cuộn trang để không che nội dung bên dưới
  var scrollTimer = null;
  window.addEventListener('scroll', function() {
    if (drawer.classList.contains('open')) return;
    rootEl.classList.add('sl-scrolling');
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(function() {
      rootEl.classList.remove('sl-scrolling');
    }, 600);
  }, { passive: true });

  function toggle() {
    if (drawer.classList.contains('open')) {
      closeDrawer();
    } else {
      openDrawer();
    }
  }

  trigger.addEventListener('click', toggle);
  closeBtn.addEventListener('click', closeDrawer);

  // Đóng drawer khi nhấn ESC
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && drawer.classList.contains('open')) {
      closeDrawer();
    }
  });

  function scrollDown() {
    if (messages) {
      if (messages.scrollHeight > messages.clientHeight + 2) {
        messages.scrollTop = messages.scrollHeight;
      } else {
        messages.scrollTop = 0;
      }
    }
  }

  function appendBubble(sender, senderName, text, timeStr, msgId, attachment) {
    if (msgId) {
      if (renderedIds[msgId]) return null;
      renderedIds[msgId] = true;
    }
    var bubble = document.createElement('div');
    var isUser = (sender === 'customer' || sender === 'user');
    bubble.className = 'sl-msg-bubble ' + (isUser ? 'sl-msg-user' : 'sl-msg-bot');

    if (text || !attachment) {
      var content = document.createElement('div');
      content.className = 'sl-msg-text';
      content.textContent = text;
      bubble.appendChild(content);
    }
    if (attachment) bubble.appendChild(ChatComposer.attachmentNode(attachment));

    var meta = document.createElement('div');
    meta.className = 'sl-msg-meta';
    var curDict = CHAT_I18N[getCurrentLang()] || CHAT_I18N.vi;
    meta.textContent = (isUser ? '' : ((senderName || curDict.consultant(topic.brand)) + ' · ')) + (timeStr || curDict.justNow);
    bubble.appendChild(meta);

    messages.appendChild(bubble);
    syncQuickChipsState();
    scrollDown();
    return { bubble: bubble, meta: meta };
  }

  function serverAttachment(m) {
    return m.attachment_url ? { url: m.attachment_url, name: m.attachment_name, type: m.attachment_type } : null;
  }

  // ══ HIỆU ỨNG LOADING KHI CHATBOT ĐANG GỬI / PHẢN HỒI ══
  var typingEl = null;
  function showTypingIndicator() {
    if (typingEl) return;
    typingEl = document.createElement('div');
    typingEl.className = 'sl-msg-bubble sl-msg-bot sl-typing-bubble';
    typingEl.id = 'slChatTyping';
    typingEl.innerHTML = '<div class="sl-typing-dots"><span></span><span></span><span></span></div>';
    messages.appendChild(typingEl);
    scrollDown();
  }

  function hideTypingIndicator() {
    if (typingEl) {
      typingEl.remove();
      typingEl = null;
    }
  }

  function setUnread(count) {
    if (!unreadPill) return;
    if (count > 0) {
      unreadPill.textContent = count;
      unreadPill.style.display = 'inline-block';
    } else {
      unreadPill.style.display = 'none';
    }
  }

  function clearUnread() {
    unreadTotal = 0;
    setUnread(0);
  }

  // Tải lịch sử tin nhắn
  function loadHistory() {
    if (!sessionToken) return;
    fetch('/api/chat/messages?session_token=' + encodeURIComponent(sessionToken))
      .then(function(r) { return r.json(); })
      .then(function(res) {
        if (res.ok && res.messages && res.messages.length > 0) {
          res.messages.forEach(function(m) {
            appendBubble(m.sender, m.sender_name, m.message, m.time, m.id, serverAttachment(m));
          });
        }
      })
      .catch(function() {});
  }
  loadHistory();

  // ══ KIỂM TRA TIN NHẮN TỪ ADMIN THÔNG MINH (TIẾT KIỆM TÀI NGUYÊN) ══
  var unreadTotal = 0;
  var pollTimer = null;

  function pollMessages() {
    sessionToken = sessionToken || localStorage.getItem('accshop_live_chat_token') || '';
    if (!sessionToken || document.hidden) return;
    fetch('/api/chat/messages?session_token=' + encodeURIComponent(sessionToken))
      .then(function(r) { return r.json(); })
      .then(function(res) {
        if (res.ok && res.messages && res.messages.length > 0) {
          var hasNewAdmin = false;
          res.messages.forEach(function(m) {
            if (!renderedIds[m.id]) {
              appendBubble(m.sender, m.sender_name, m.message, m.time, m.id, serverAttachment(m));
              if (m.sender === 'admin' || m.sender === 'system') {
                hasNewAdmin = true;
              }
            }
          });
          if (hasNewAdmin && !drawer.classList.contains('open')) {
            unreadTotal++;
            setUnread(unreadTotal);
          }
        }
      })
      .catch(function() {});
  }

  function scheduleNextPoll() {
    clearTimeout(pollTimer);
    // Khi mở hộp chat: poll mỗi 3.5s để đàm thoại mượt mà
    // Khi đóng hộp chat: chỉ poll kiểm tra badge mỗi 25s để tiết kiệm pin và băng thông
    var delay = drawer.classList.contains('open') ? 3500 : 25000;
    pollTimer = setTimeout(function() {
      pollMessages();
      scheduleNextPoll();
    }, delay);
  }
  scheduleNextPoll();

  document.addEventListener('visibilitychange', function() {
    if (!document.hidden) {
      pollMessages();
      scheduleNextPoll();
    }
  });

  window.slSendQuickMsg = function(txt) {
    if (txt) dispatchChatMessage(txt);
  };

  document.addEventListener('click', function(e) {
    var chip = e.target.closest('[data-quick-chip]');
    if (chip) {
      var txt = chip.getAttribute('data-quick-chip');
      if (txt) window.slSendQuickMsg(txt);
    }
  });

  window.slOpenChatWithContext = function(serviceTitle) {
    if (!drawer.classList.contains('open')) {
      openDrawer();
    }
    if (badge && serviceTitle) {
      badge.textContent = serviceTitle;
      currentService.name = serviceTitle;
    }
    if (input) {
      var curDict = CHAT_I18N[getCurrentLang()] || CHAT_I18N.vi;
      input.value = curDict.consultPrefix + serviceTitle;
      fitInput();
      input.focus();
    }
  };

  // Nút tư vấn của từng mục dịch vụ (partials/consult_button.html): mở hộp chat kèm tên mục
  document.addEventListener('click', function(e) {
    var btn = e.target.closest('[data-consult]');
    if (btn) window.slOpenChatWithContext(btn.getAttribute('data-consult'));
  });

  // Đơn vừa thanh toán xong: mở hộp chat; tin báo đơn vào chat sau khi tiền về nên hỏi lại sau ít giây
  window.slOpenChatForOrder = function() {
    if (!drawer.classList.contains('open')) openDrawer();
    [1500, 4000].forEach(function(ms) { setTimeout(pollMessages, ms); });
  };

  // ══ VALIDATION TRƯỜNG LIÊN HỆ KHI GÕ ══
  if (contactInp) {
    contactInp.addEventListener('input', function() {
      clearContactError();
    });
  }

  function setSendLoading(isLoading) {
    if (!sendBtn) return;
    sendBtn.disabled = isLoading;
    if (isLoading) {
      sendBtn.classList.add('is-loading');
      if (sendIcon) sendIcon.style.display = 'none';
      if (spinner) {
        spinner.classList.add('visible');
        spinner.style.display = 'inline-block';
      }
    } else {
      sendBtn.classList.remove('is-loading');
      if (sendIcon) sendIcon.style.display = 'inline-block';
      if (spinner) {
        spinner.classList.remove('visible');
        spinner.style.display = 'none';
      }
    }
  }

  function dispatchChatMessage(text, files) {
    files = files || [];
    var contactVal = (contactInp ? contactInp.value.trim() : '') || getSavedContact();
    var curDict = CHAT_I18N[getCurrentLang()] || CHAT_I18N.vi;

    // Validate phone hoặc email nếu người dùng có nhập
    if (!validateContactString(contactVal)) {
      showContactError(curDict.contactErr);
      if (contactInp) contactInp.focus();
      return false;
    }
    clearContactError();

    // Hiển thị tin nhắn lập tức với trạng thái đang gửi: chữ một bong bóng, mỗi tệp một bong bóng (như máy chủ lưu)
    var rendered = [];
    if (text) rendered.push(appendBubble('customer', curDict.you, text, curDict.sendingStatus));
    files.forEach(function(file) {
      rendered.push(appendBubble('customer', curDict.you, '', curDict.sendingStatus, null, {
        url: URL.createObjectURL(file), name: file.name, type: /^image\//.test(file.type) ? 'image' : 'file'
      }));
    });
    if (input && input.value.trim() === text) {
      input.value = '';
      fitInput();
    }

    // ══ LOADING STATE: Khóa nút gửi & hiển thị loading spinner ══
    setSendLoading(true);
    showTypingIndicator();

    var fd = new FormData();
    fd.append('session_token', sessionToken);
    fd.append('message', text);
    files.forEach(function(file) { fd.append('files', file, file.name); });
    fd.append('customer_name', '');
    fd.append('customer_contact', contactVal);
    fd.append('service_name', currentService.name);
    fd.append('service_group', currentService.group);
    fd.append('service_category', currentService.category);
    fd.append('current_url', currentService.url);

    function markFailed() {
      if (!rendered.length) return;
      rendered.forEach(function(item) {
        item.bubble.classList.add('is-failed');
        item.meta.textContent = '';
      });
      var errSpan = document.createElement('span');
      errSpan.textContent = curDict.sendFailed + ' · ';
      var retryBtn = document.createElement('button');
      retryBtn.type = 'button';
      retryBtn.className = 'sl-msg-retry-btn';
      retryBtn.textContent = curDict.retryBtn;
      retryBtn.addEventListener('click', function() {
        rendered.forEach(function(item) { item.bubble.remove(); });
        dispatchChatMessage(text, files);
      });
      rendered[0].meta.appendChild(errSpan);
      rendered[0].meta.appendChild(retryBtn);
    }

    fetch('/api/chat/send', { method: 'POST', body: fd })
      .then(function(r) { return r.json(); })
      .then(function(res) {
        hideTypingIndicator();
        setSendLoading(false);
        if (res.ok) {
          rendered.forEach(function(item) {
            item.meta.textContent = curDict.sentStatus + ' · ' + curDict.justNow;
          });
          if (contactVal) {
            try { localStorage.setItem('sl_user_contact', contactVal); } catch(e) {}
          }
          syncContactPrompt();

          if (res.session_token && res.session_token !== sessionToken) {
            sessionToken = res.session_token;
            localStorage.setItem('accshop_live_chat_token', sessionToken);
          }
          (res.messages || []).forEach(function(m) { renderedIds[m.id] = true; });
          if (res.message_id) {
            renderedIds[res.message_id] = true;
          }
        } else {
          markFailed();
        }
      })
      .catch(function(err) {
        hideTypingIndicator();
        setSendLoading(false);
        markFailed();
        console.error(err);
      });
    return true;
  }

  // Ảnh/tệp đính kèm (nút, kéo thả, Ctrl+V) và emoji: chat-composer.js dùng chung với trang quản trị
  var composer = ChatComposer({
    input: input,
    fileInput: document.getElementById('slChatFile'),
    attachButton: document.getElementById('slChatAttach'),
    emojiButton: document.getElementById('slChatEmoji'),
    emojiPanel: document.getElementById('slChatEmojiPanel'),
    tray: document.getElementById('slChatTray'),
    dropTarget: messages
  });

  // Ô nhập cao dần theo nội dung để thấy hết chữ đã gõ; Enter gửi, Shift+Enter xuống dòng
  function fitInput() {
    if (!input) return;
    input.style.height = '';
    if (input.value) {
      input.style.height = Math.min(input.scrollHeight, 160) + 'px';
    }
  }
  input.addEventListener('input', fitInput);
  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      form.dispatchEvent(new Event('submit', { cancelable: true }));
    }
  });

  form.addEventListener('submit', function(e) {
    e.preventDefault();
    var text = input.value.trim();
    var files = composer.files();
    if (!text && !files.length) {
      input.focus();
      return;
    }
    if (dispatchChatMessage(text, files) !== false) composer.clear();
  });

})();

