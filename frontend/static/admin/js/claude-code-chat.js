// Nút chat Claude sửa code (partials/claude_code_chat.html). Mỗi tin nhắn là một lượt Claude Code chạy trên máy chủ;
// trang hỏi diễn biến mới mỗi 1,5 giây. Web tự khởi động lại mỗi khi Claude sửa file, nên lỗi mạng tạm thời chỉ làm
// trang hỏi lại chậm hơn chứ không dừng theo dõi.
(() => {
  const root = document.getElementById('cc-chat');
  if (!root || typeof window.apiRequest !== 'function') return;

  const API = root.dataset.api;
  const fab = root.querySelector('.cc-fab');
  const dot = root.querySelector('.cc-fab-dot');
  const panel = root.querySelector('.cc-panel');
  const log = root.querySelector('.cc-log');
  const notice = root.querySelector('.cc-notice');
  const form = root.querySelector('.cc-form');
  const input = root.querySelector('.cc-input');
  const sendButton = root.querySelector('.cc-send');
  const stopButton = root.querySelector('.cc-stop');
  const newButton = root.querySelector('.cc-new');
  const closeButton = root.querySelector('.cc-close');
  const filesTray = root.querySelector('.cc-files');
  const attachButton = root.querySelector('.cc-attach');
  const fileInput = root.querySelector('.cc-file');
  const shotButton = root.querySelector('.cc-shot');

  const FINAL = new Set(['done', 'error', 'stopped', 'timeout', 'interrupted']);
  const POLL_MS = 1500;
  const STORE_OPEN = 'claudeCodeChat.open';
  const STORE_DRAFT = 'claudeCodeChat.draft';
  const STATUS_ICONS = { ok: 'bx-check', error: 'bx-x', denied: 'bx-block' };
  const ENTER_SENDS = !(window.matchMedia && window.matchMedia('(pointer: coarse)').matches);

  const runs = new Map();
  let attachments = [];
  let activeRunId = null;
  let busyElsewhere = false;
  let sending = false;
  let unseenDone = false;
  let failures = 0;
  let pollTimer = null;
  let clockTimer = null;

  const store = {
    get(key) {
      try { return window.localStorage.getItem(key); } catch (error) { return null; }
    },
    set(key, value) {
      try {
        if (value) window.localStorage.setItem(key, value);
        else window.localStorage.removeItem(key);
      } catch (error) { /* trình duyệt chặn lưu trữ: chỉ mất phần ghi nhớ */ }
    },
  };

  // Dữ liệu trò chuyện chung theo tài khoản: đăng nhập máy nào cũng thấy cùng danh sách.
  const READ_HEADERS = { Accept: 'application/json' };
  const WRITE_HEADERS = { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' };

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // Markdown tối giản trên chuỗi đã escape: đoạn, danh sách, tiêu đề, `code`, **đậm**, khối ```code```.
  function inline(html) {
    return html.replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  }

  function textBlocks(text) {
    let html = '';
    let list = null;
    const closeList = () => {
      if (list) html += `</${list}>`;
      list = null;
    };
    escapeHtml(text).split('\n').forEach((raw) => {
      const line = raw.replace(/\s+$/, '');
      const bullet = line.match(/^\s*[-*]\s+(.*)$/);
      const number = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (bullet || number) {
        const kind = bullet ? 'ul' : 'ol';
        if (list !== kind) {
          closeList();
          html += `<${kind}>`;
          list = kind;
        }
        html += `<li>${inline((bullet || number)[1])}</li>`;
        return;
      }
      closeList();
      if (!line.trim()) return;
      const heading = line.match(/^#{1,6}\s+(.*)$/);
      html += heading ? `<p class="cc-h">${inline(heading[1])}</p>` : `<p>${inline(line)}</p>`;
    });
    closeList();
    return html;
  }

  function markdown(text) {
    return String(text).split('```').map((part, index) => {
      if (index % 2 === 0) return textBlocks(part);
      const code = part.replace(/^[^\n]*\n/, '').replace(/\n$/, '');
      return `<pre class="cc-pre"><code>${escapeHtml(code)}</code></pre>`;
    }).join('');
  }

  function formatDuration(seconds) {
    const total = Math.max(0, Math.round(seconds));
    const minutes = Math.floor(total / 60);
    return minutes ? `${minutes} phút ${total % 60} giây` : `${total} giây`;
  }

  function formatSize(bytes) {
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let value = Number(bytes) || 0;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    return `${unit ? value.toFixed(value < 10 ? 1 : 0) : value} ${units[unit]}`;
  }

  function renderFiles() {
    filesTray.innerHTML = '';
    attachments.forEach((item, index) => {
      const chip = document.createElement('div');
      chip.className = 'cc-file-chip';
      chip.title = item.file.name;
      if (item.url) {
        const img = document.createElement('img');
        img.src = item.url;
        img.alt = '';
        chip.appendChild(img);
      } else {
        chip.insertAdjacentHTML('beforeend', '<i class="bx bx-file"></i>');
      }
      const name = document.createElement('span');
      name.className = 'cc-file-name';
      name.textContent = item.file.name;
      const size = document.createElement('small');
      size.textContent = formatSize(item.file.size);
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'cc-file-x';
      remove.title = 'Bỏ file';
      remove.setAttribute('aria-label', 'Bỏ file');
      remove.innerHTML = '<i class="bx bx-x"></i>';
      remove.addEventListener('click', () => {
        if (item.url) URL.revokeObjectURL(item.url);
        attachments.splice(index, 1);
        renderFiles();
      });
      chip.append(name, size, remove);
      filesTray.appendChild(chip);
    });
    filesTray.hidden = !attachments.length;
  }

  function addFiles(list) {
    Array.from(list).forEach((file) => {
      attachments.push({ file, url: file.type.startsWith('image/') ? URL.createObjectURL(file) : '' });
    });
    renderFiles();
  }

  const nextFrame = () => new Promise((resolve) => requestAnimationFrame(resolve));

  // Chụp đúng những gì đang hiện trên trang (trình duyệt hỏi cho phép chia sẻ tab này), ẩn khung chat khỏi ảnh rồi đính kèm.
  async function capturePage() {
    shotButton.disabled = true;
    let stream;
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({ video: true, preferCurrentTab: true });
    } catch (error) {
      shotButton.disabled = false;
      if (error.name === 'NotAllowedError') return; // người dùng bấm Hủy
      throw error;
    }
    try {
      const video = document.createElement('video');
      video.muted = true;
      video.srcObject = stream;
      await video.play();
      root.style.visibility = 'hidden';
      await nextFrame();
      await nextFrame();
      await new Promise((resolve) => setTimeout(resolve, 250));
      const canvas = document.createElement('canvas');
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext('2d').drawImage(video, 0, 0);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
      const stamp = new Date().toISOString().slice(0, 19).replace(/[-:]/g, '').replace('T', '-');
      addFiles([new File([blob], `chup-man-hinh-${stamp}.png`, { type: 'image/png' })]);
    } finally {
      stream.getTracks().forEach((track) => track.stop());
      root.style.visibility = '';
      shotButton.disabled = false;
    }
    input.focus();
  }

  function clearFiles() {
    attachments.forEach((item) => item.url && URL.revokeObjectURL(item.url));
    attachments = [];
    renderFiles();
  }

  function clockText(seconds) {
    const total = Math.max(0, Math.floor(seconds));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
  }

  function toast(message) {
    if (window.toastr) {
      toastr.options.escapeHtml = true;
      toastr.error(message);
    } else {
      window.alert(message);
    }
  }

  function setNotice(text) {
    notice.textContent = text || '';
    notice.hidden = !text;
  }

  function nearBottom() {
    return log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  }

  function scrollToBottom() {
    log.scrollTop = log.scrollHeight;
  }

  function pageTitle() {
    const heading = document.querySelector('.page-title-box h4');
    const menu = document.querySelector('#sidebar-menu .sub-menu a.active');
    const text = (heading && heading.textContent.trim()) || (menu && menu.textContent.trim()) || document.title || '';
    return text.replace(/\s+/g, ' ').trim().slice(0, 200);
  }

  function toolElement(item) {
    const el = document.createElement('div');
    el.className = 'cc-tool is-pending';
    el.dataset.name = item.name || '';
    let body = `<span class="cc-tool-label">${escapeHtml(item.label || item.name || '')}</span>`;
    if (item.target) body += `<code>${escapeHtml(item.target)}</code>`;
    const rows = [];
    const lines = (text) => text.replace(/\n$/, '').split('\n');
    if (item.old) lines(item.old).forEach((line) => rows.push(`<div class="cc-del">- ${escapeHtml(line)}</div>`));
    if (item.new) lines(item.new).forEach((line) => rows.push(`<div class="cc-add">+ ${escapeHtml(line)}</div>`));
    if (rows.length) body += `<details><summary>Xem thay đổi</summary><div class="cc-diff">${rows.join('')}</div></details>`;
    if (Array.isArray(item.todos) && item.todos.length) {
      const todos = item.todos.map((todo) => `<li class="is-${escapeHtml(todo.status || '')}">${escapeHtml(todo.text)}</li>`);
      body += `<ul class="cc-todos">${todos.join('')}</ul>`;
    }
    el.innerHTML = `<i class="cc-tool-icon bx bx-loader-alt bx-spin"></i><div class="cc-tool-body">${body}</div>`;
    return el;
  }

  function applyToolStatus(run, item) {
    const el = run.tools.get(item.id);
    // Lệnh bị chặn còn kèm một kết quả lỗi ngay sau đó; giữ nhãn "bị chặn".
    if (!el || (el.classList.contains('is-denied') && item.status === 'error')) return;
    el.classList.remove('is-pending', 'is-ok', 'is-error', 'is-denied');
    el.classList.add(`is-${item.status}`);
    el.querySelector('.cc-tool-icon').className = `cc-tool-icon bx ${STATUS_ICONS[item.status] || 'bx-check'}`;
    if (item.status === 'ok') {
      if (el.dataset.name === 'Edit' || el.dataset.name === 'Write') run.edited = true;
      return;
    }
    let note = el.querySelector('.cc-tool-note');
    if (!note) {
      note = document.createElement('div');
      note.className = 'cc-tool-note';
      el.querySelector('.cc-tool-body').appendChild(note);
    }
    note.textContent = item.status === 'denied' ? 'Bị chặn' : (item.text || 'Lỗi');
    note.title = item.text || '';
  }

  function addItem(run, item) {
    if (item.kind === 'text') {
      const el = document.createElement('div');
      el.className = 'cc-text';
      el.innerHTML = markdown(item.text);
      run.steps.appendChild(el);
    } else if (item.kind === 'tool') {
      const el = toolElement(item);
      run.steps.appendChild(el);
      if (item.id) run.tools.set(item.id, el);
    } else if (item.kind === 'tool_status') {
      applyToolStatus(run, item);
    } else if (item.kind === 'result') {
      run.result = item;
    }
  }

  function updateClock(run) {
    const el = run.footer.querySelector('.cc-clock');
    if (el && run.elapsedBase != null) el.textContent = clockText(run.elapsedBase + (Date.now() - run.elapsedAt) / 1000);
  }

  function renderFooter(run) {
    const view = run.view;
    const footer = run.footer;
    footer.className = 'cc-footer';
    footer.title = '';
    if (!FINAL.has(view.state)) {
      footer.innerHTML = '<i class="bx bx-loader-alt bx-spin"></i><span>Claude đang làm… <span class="cc-clock"></span></span>';
      updateClock(run);
      return;
    }
    run.tools.forEach((el) => {
      if (!el.classList.contains('is-pending')) return;
      el.classList.remove('is-pending');
      el.querySelector('.cc-tool-icon').className = 'cc-tool-icon bx bx-stop';
    });
    const result = run.result || {};
    if (view.state === 'done') {
      const seconds = result.duration_ms != null ? result.duration_ms / 1000 : view.elapsed_s;
      footer.innerHTML = `<i class="bx bx-check-circle"></i><span>Xong${seconds != null ? ` · ${formatDuration(seconds)}` : ''}</span>`;
      const extra = [];
      if (result.num_turns != null) extra.push(`${result.num_turns} lượt`);
      if (result.cost_usd != null) extra.push(`~$${Number(result.cost_usd).toFixed(2)}`);
      footer.title = extra.join(' · ');
      if (run.edited) {
        const reload = document.createElement('button');
        reload.type = 'button';
        reload.className = 'cc-reload';
        reload.textContent = 'Tải lại trang';
        reload.addEventListener('click', () => window.location.reload());
        footer.appendChild(reload);
      }
    } else if (view.state === 'stopped') {
      footer.textContent = 'Đã dừng';
    } else {
      footer.classList.add('is-error');
      footer.textContent = view.error || result.text || 'Lỗi không rõ';
    }
  }

  function applyRun(view) {
    let run = runs.get(view.run_id);
    if (!run) {
      const el = document.createElement('div');
      el.className = 'cc-run';
      el.innerHTML = '<div class="cc-user"><div class="cc-atts"></div><div class="cc-bubble"></div><div class="cc-page"></div></div><div class="cc-steps"></div><div class="cc-footer"></div>';
      const attsEl = el.querySelector('.cc-atts');
      (view.attachments || []).forEach((att) => {
        const chip = document.createElement('span');
        chip.className = 'cc-att';
        chip.insertAdjacentHTML('beforeend', `<i class="bx ${att.image ? 'bx-image' : 'bx-file'}"></i>`);
        const label = document.createElement('span');
        label.textContent = `${att.name} · ${formatSize(att.size)}`;
        chip.appendChild(label);
        attsEl.appendChild(chip);
      });
      if (!attsEl.children.length) attsEl.remove();
      const bubbleEl = el.querySelector('.cc-bubble');
      if (view.message) bubbleEl.textContent = view.message;
      else bubbleEl.remove();
      const page = view.page_title || view.page_path || '';
      const pageEl = el.querySelector('.cc-page');
      if (page) pageEl.textContent = `Trang: ${page}`;
      else pageEl.remove();
      const empty = log.querySelector('.cc-empty');
      if (empty) empty.remove();
      log.appendChild(el);
      run = { steps: el.querySelector('.cc-steps'), footer: el.querySelector('.cc-footer'), tools: new Map(), next: 0, edited: false, result: null };
      runs.set(view.run_id, run);
    }
    const stick = nearBottom();
    (view.items || []).forEach((item) => addItem(run, item));
    run.next = view.next;
    run.view = view;
    if (view.elapsed_s != null) {
      run.elapsedBase = view.elapsed_s;
      run.elapsedAt = Date.now();
    }
    renderFooter(run);
    if (stick) scrollToBottom();
    return run;
  }

  function updateDot() {
    const open = root.classList.contains('is-open');
    dot.hidden = !(activeRunId || (unseenDone && !open));
    dot.classList.toggle('is-done', !activeRunId && unseenDone);
  }

  function setBusyUI() {
    const running = Boolean(activeRunId);
    sendButton.hidden = running;
    stopButton.hidden = !running;
    sendButton.disabled = sending || busyElsewhere;
    sendButton.firstElementChild.className = sending ? 'bx bx-loader-alt bx-spin' : 'bx bx-send';
    newButton.disabled = running || sending;
  }

  function startClock() {
    clearInterval(clockTimer);
    clockTimer = setInterval(() => {
      const run = activeRunId && runs.get(activeRunId);
      if (run) updateClock(run);
      else clearInterval(clockTimer);
    }, 1000);
  }

  function finishActive() {
    activeRunId = null;
    clearTimeout(pollTimer);
    clearInterval(clockTimer);
    if (!root.classList.contains('is-open')) unseenDone = true;
    setBusyUI();
    updateDot();
  }

  function schedulePoll(delay) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, delay);
  }

  function poll() {
    const runId = activeRunId;
    const run = runId && runs.get(runId);
    if (!run) return;
    apiRequest(`${API}/runs/${encodeURIComponent(runId)}?after=${run.next}`, { headers: READ_HEADERS }).then((result) => {
      if (runId !== activeRunId) return;
      if (result.ok && result.run) {
        failures = 0;
        setNotice(busyElsewhere ? notice.textContent : '');
        applyRun(result.run);
        if (FINAL.has(result.run.state)) finishActive();
        else schedulePoll(POLL_MS);
        return;
      }
      if ([401, 403, 404].includes(result.status)) {
        setNotice(result.message || 'Không đọc được diễn biến của Claude.');
        finishActive();
        return;
      }
      failures += 1;
      if (failures >= 2) setNotice('Đang chờ web khởi động lại…');
      schedulePoll(Math.min(POLL_MS * (1 + failures), 6000));
    });
  }

  function renderConversation(data) {
    clearTimeout(pollTimer);
    runs.clear();
    activeRunId = null;
    log.innerHTML = '';
    busyElsewhere = Boolean(data.busy_elsewhere);
    (data.runs || []).forEach((view) => {
      applyRun(view);
      if (!FINAL.has(view.state)) activeRunId = view.run_id;
    });
    if (!runs.size) log.innerHTML = '<div class="cc-empty">Chưa có tin nhắn</div>';
    setNotice(busyElsewhere ? 'Claude đang làm việc cho tài khoản khác.' : '');
    setBusyUI();
    updateDot();
    if (activeRunId) {
      schedulePoll(POLL_MS);
      startClock();
    }
    scrollToBottom();
  }

  function loadConversation() {
    apiRequest(`${API}/conversation`, { headers: READ_HEADERS }).then((result) => {
      if (result.ok) renderConversation(result);
      else setNotice(result.message || 'Không tải được cuộc trò chuyện.');
    });
  }

  function autosize() {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight + 2, 160)}px`;
  }

  function send() {
    const text = input.value.trim();
    if ((!text && !attachments.length) || sending || activeRunId || busyElsewhere) return;
    sending = true;
    setBusyUI();
    const body = new FormData();
    body.append('message', text);
    body.append('page_path', window.location.pathname + window.location.search);
    body.append('page_title', pageTitle());
    attachments.forEach((item) => body.append('files', item.file, item.file.name));
    apiRequest(`${API}/messages`, { method: 'POST', headers: WRITE_HEADERS, body }).then((result) => {
      sending = false;
      if (!result.ok || !result.run) {
        toast(result.message || 'Không gửi được tin nhắn.');
        setBusyUI();
        return;
      }
      input.value = '';
      store.set(STORE_DRAFT, '');
      clearFiles();
      autosize();
      applyRun(result.run);
      scrollToBottom();
      if (!FINAL.has(result.run.state)) {
        activeRunId = result.run.run_id;
        schedulePoll(POLL_MS);
        startClock();
      }
      setBusyUI();
      updateDot();
    });
  }

  function openPanel() {
    root.classList.add('is-open');
    panel.hidden = false;
    fab.setAttribute('aria-expanded', 'true');
    store.set(STORE_OPEN, '1');
    unseenDone = false;
    updateDot();
    scrollToBottom();
    autosize();
    input.focus();
  }

  function closePanel() {
    root.classList.remove('is-open');
    panel.hidden = true;
    fab.setAttribute('aria-expanded', 'false');
    store.set(STORE_OPEN, '');
    updateDot();
    fab.focus();
  }

  fab.addEventListener('click', openPanel);
  closeButton.addEventListener('click', closePanel);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    send();
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && ENTER_SENDS) {
      event.preventDefault();
      send();
    }
  });
  input.addEventListener('input', () => {
    autosize();
    store.set(STORE_DRAFT, input.value);
  });
  attachButton.addEventListener('click', () => fileInput.click());
  if (navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia) shotButton.addEventListener('click', capturePage);
  else shotButton.hidden = true;
  fileInput.addEventListener('change', () => {
    addFiles(fileInput.files);
    fileInput.value = '';
  });
  // Ctrl+V ảnh/file: clipboard chỉ có file (không có chữ) thì đính kèm, còn lại dán chữ như thường.
  panel.addEventListener('paste', (event) => {
    const data = event.clipboardData;
    if (!data || !data.files.length || data.getData('text/plain').trim()) return;
    event.preventDefault();
    addFiles(data.files);
  });
  const dragHasFiles = (event) => Array.from((event.dataTransfer && event.dataTransfer.types) || []).includes('Files');
  panel.addEventListener('dragover', (event) => {
    if (!dragHasFiles(event)) return;
    event.preventDefault();
    panel.classList.add('is-drag');
  });
  panel.addEventListener('dragleave', (event) => {
    if (!panel.contains(event.relatedTarget)) panel.classList.remove('is-drag');
  });
  panel.addEventListener('drop', (event) => {
    if (!dragHasFiles(event)) return;
    event.preventDefault();
    panel.classList.remove('is-drag');
    addFiles(event.dataTransfer.files);
  });
  stopButton.addEventListener('click', () => {
    if (!activeRunId) return;
    stopButton.disabled = true;
    apiRequest(`${API}/runs/${encodeURIComponent(activeRunId)}/stop`, { method: 'POST', headers: WRITE_HEADERS }).then((result) => {
      stopButton.disabled = false;
      if (!result.ok) toast(result.message || 'Không dừng được Claude.');
      schedulePoll(300);
    });
  });
  newButton.addEventListener('click', () => {
    if (activeRunId || sending) return;
    const start = () => apiRequest(`${API}/conversation/new`, { method: 'POST', headers: WRITE_HEADERS }).then((result) => {
      if (!result.ok) {
        toast(result.message || 'Không mở được cuộc trò chuyện mới.');
        return;
      }
      renderConversation(result);
      input.focus();
    });
    if (!runs.size) start();
    else if (window.appConfirm) {
      window.appConfirm('Claude sẽ không nhớ các tin nhắn trước.', { title: 'Cuộc trò chuyện mới', confirmText: 'Bắt đầu', confirmClass: 'btn-primary' }, start);
    } else if (window.confirm('Bắt đầu cuộc trò chuyện mới?')) start();
  });

  input.value = store.get(STORE_DRAFT) || '';
  loadConversation();
  if (store.get(STORE_OPEN) === '1') openPanel();
})();
