import { spawn } from 'node:child_process';

// Mọi tin trong nhóm Zalo "AI" là yêu cầu cho Claude Code chạy trên thư mục project; kết quả
// được gửi lại vào nhóm. "stop" hủy lệnh đang chạy, "new" bắt đầu phiên mới ("/ai" phía trước tùy ý).
export const BOT_MARK = '🤖';
const ZALO_CHUNK = 1800;

export function parseAiCommand(text) {
  const trimmed = String(text || '').trim();
  if (!trimmed || trimmed.startsWith(BOT_MARK)) return null;
  // Ghi chú ghim link do bridge tự tạo/sửa quay lại nhóm dưới dạng "<tên> tạo ghi chú Link truy cập: ...".
  if (/Link truy cập:\s*https?:\/\//iu.test(trimmed)) return null;
  const prefixed = trimmed.match(/^\/ai(?:\s+([\s\S]*))?$/iu);
  const body = prefixed ? (prefixed[1] || '').trim() : trimmed;
  const keyword = body.toLocaleLowerCase('vi');
  if (!body || keyword === 'help') return { kind: 'help' };
  if (keyword === 'stop') return { kind: 'stop' };
  if (keyword === 'status') return { kind: 'status' };
  if (keyword === 'new') return { kind: 'new' };
  return { kind: 'run', prompt: body };
}

export function splitForZalo(text, size = ZALO_CHUNK) {
  const chunks = [];
  let rest = String(text || '').trim();
  while (rest.length > size) {
    let cut = rest.lastIndexOf('\n', size);
    if (cut < size / 2) cut = size;
    chunks.push(rest.slice(0, cut).trimEnd());
    rest = rest.slice(cut).trimStart();
  }
  if (rest) chunks.push(rest);
  return chunks;
}

function buildPrompt(request, senderName, files = []) {
  return [
    `Yêu cầu từ nhóm Zalo "AI" (người gửi: ${senderName || 'không rõ'}):`,
    request,
    ...(files.length ? ['', 'Ảnh/tệp đính kèm:', ...files,
      'Đọc nội dung tệp trước khi sửa code; với .xlsx dùng Python openpyxl để xem các sheet và cột.'] : []),
    '',
    // Claude điều phối, Antigravity viết code: tiết kiệm token Claude cho phần đọc hiểu và review.
    'Cách làm: câu hỏi, phân tích, hoặc sửa 1–2 dòng thì tự làm. Việc sửa/thêm code lớn hơn thì theo skill '
      + 'handoff-agy: tự đọc code và viết plan vào .ai/plans/, giao cho Antigravity bằng '
      + '`python scripts/agents/agy_exec.py .ai/plans/<plan>.md --full`, rồi review git diff và tự chạy test; '
      + 'agy làm sai thì --resume kèm --note, sau 2 vòng vẫn sai thì tự sửa phần còn lại. '
      + 'Không xóa hay hoàn tác code người khác đang thêm; không commit; không sửa dữ liệu thật trong DB camera_ai.',
    '',
    'Thực hiện yêu cầu trên project này. Kết quả sẽ được gửi nguyên văn vào Zalo cho người không đọc code, '
      + 'nên trả lời bằng tiếng Việt, văn bản thuần (không dùng Markdown), chỉ nêu NỘI DUNG THAY ĐỔI: '
      + 'bây giờ hệ thống làm khác trước thế nào, mỗi ý một dòng ngắn. '
      + 'Không kể quy trình và không liệt kê: ai làm (Antigravity hay Claude), tên file, nguyên nhân kỹ thuật, '
      + 'kết quả kiểm tra, các lần thử, gợi ý làm thêm. '
      + 'Chỉ thêm một dòng riêng khi việc chưa xong hoặc không làm được, hoặc khi người dùng phải làm gì đó '
      + '(ví dụ nhấn Ctrl+F5) thì thay đổi mới có tác dụng. '
      + 'Nếu yêu cầu là câu hỏi hoặc phân tích thì trả lời thẳng vào câu hỏi.',
  ].join('\n');
}

// Khi Claude báo các lỗi này (hết quota/credit, quá tải, hết hạn đăng nhập) thì chuyển sang Antigravity.
const QUOTA_MARKERS = [
  'credit balance', 'insufficient credit', 'quota', 'usage limit',
  'rate limit', 'rate_limit', 'too many requests', 'overloaded',
  'limit reached', '429', '529',
  'unauthorized', 'authentication_error', 'invalid api key',
  'invalid x-api-key', 'has expired', 'please run /login',
];

function isQuotaError(text) {
  const low = String(text || '').toLowerCase();
  return QUOTA_MARKERS.some(marker => low.includes(marker));
}

// Khi chạy thẳng Antigravity thì agy chính là người viết code, nên bỏ phần điều phối handoff của buildPrompt;
// lời dặn an toàn (không reset/xóa code phiên khác) nằm trong agy_direct.py.
function buildAgyPrompt(request, senderName, files = []) {
  return [
    `Yêu cầu từ nhóm Zalo "AI" (người gửi: ${senderName || 'không rõ'}):`,
    request,
    ...(files.length ? ['', 'Ảnh/tệp đính kèm:', ...files,
      'Đọc nội dung tệp trước khi sửa code; với .xlsx dùng Python openpyxl để xem các sheet và cột.'] : []),
    '',
    'Thực hiện yêu cầu trên project này. Kết quả sẽ được gửi nguyên văn vào Zalo cho người không đọc code, '
      + 'nên trả lời bằng tiếng Việt, văn bản thuần (không dùng Markdown), chỉ nêu NỘI DUNG THAY ĐỔI: '
      + 'bây giờ hệ thống làm khác trước thế nào, mỗi ý một dòng ngắn. '
      + 'Không kể quy trình và không liệt kê: tên file, nguyên nhân kỹ thuật, kết quả kiểm tra, các lần thử, gợi ý làm thêm. '
      + 'Chỉ thêm một dòng riêng khi việc chưa xong/không làm được, hoặc khi người dùng phải làm gì đó (ví dụ nhấn Ctrl+F5). '
      + 'Nếu yêu cầu là câu hỏi hoặc phân tích thì trả lời thẳng vào câu hỏi.',
  ].join('\n');
}

function killTree(child) {
  if (!child || child.exitCode !== null) return;
  if (process.platform === 'win32') {
    spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true });
  } else {
    child.kill('SIGTERM');
  }
}

export function createAiRunner({
  project, sendText, spawnFn = spawn,
  onBusyChange = async () => {},
  claudeBin = process.env.CLAUDE_BIN || 'claude',
  pythonBin = process.env.PYTHON_BIN || 'python',
  timeoutMs = Number(process.env.ZALO_AI_TIMEOUT_MS) || 60 * 60 * 1000, // đủ cho plan + agy (tối đa 30 phút) + review
  gatherMs = Number(process.env.ZALO_AI_GATHER_MS) || 5000,
  progressFirstMs = 60 * 1000,
  progressEveryMs = 2 * 60 * 1000,
}) {
  let current = null;   // { child, prompt, startedAt, stopped }
  let pending = [];     // { text, files, senderName } chờ gom thành lệnh tiếp theo
  let gatherTimer = null;
  let sessionId = null; // giữ ngữ cảnh giữa các lệnh cho đến "new"
  // Bridge nghe cả tin của chính tài khoản, nên tin bot vừa gửi sẽ quay lại: phải bỏ qua để
  // không tự trả lời mình (BOT_MARK là lớp chặn chính, danh sách này phòng khi Zalo đổi nội dung).
  const recentlySent = [];
  const unmarked = text => String(text || '').trim().replace(BOT_MARK, '').trim();

  async function reply(text) {
    for (const chunk of splitForZalo(`${BOT_MARK} ${text}`)) {
      recentlySent.push(unmarked(chunk));
      if (recentlySent.length > 50) recentlySent.shift();
      await sendText(chunk);
    }
  }

  function runClaude(prompt) {
    // Prompt đi qua stdin, tham số cố định: không có nội dung tin nhắn nào lọt vào dòng lệnh shell.
    const args = ['-p', '--output-format', 'json', '--dangerously-skip-permissions'];
    if (sessionId) args.push('--resume', sessionId);
    const child = spawnFn(claudeBin, args, {
      cwd: project, shell: process.platform === 'win32', windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    current.child = child;
    child.stdin.end(prompt);
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', data => { stdout += data; });
    child.stderr.on('data', data => { stderr += data; });
    return new Promise(resolve => {
      const timer = setTimeout(() => { current.timedOut = true; killTree(child); }, timeoutMs);
      child.on('error', error => { clearTimeout(timer); resolve({ code: -1, stdout, stderr: error.message }); });
      child.on('close', code => { clearTimeout(timer); resolve({ code, stdout, stderr }); });
    });
  }

  function runAgy(prompt) {
    // Fallback khi Claude hết hạn: chạy thẳng Antigravity. Prompt qua stdin, không shell → không lọt vào dòng lệnh.
    const child = spawnFn(pythonBin, ['scripts/agents/agy_direct.py', '--full'], {
      cwd: project, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
    });
    current.child = child;
    child.stdin.end(prompt);
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', data => { stdout += data; });
    child.stderr.on('data', data => { stderr += data; });
    return new Promise(resolve => {
      const timer = setTimeout(() => { current.timedOut = true; killTree(child); }, timeoutMs);
      child.on('error', error => { clearTimeout(timer); resolve({ ok: false, text: '', stderr: error.message }); });
      child.on('close', code => { clearTimeout(timer); resolve({ ok: code === 0, text: stdout.trim(), stderr }); });
    });
  }

  // Gom các tin gửi liên tiếp (Zalo tách chữ và từng ảnh thành tin riêng) thành một yêu cầu;
  // tin đến khi đang chạy được xếp hàng và chạy tiếp sau, không bị bỏ.
  function schedule() {
    clearTimeout(gatherTimer);
    gatherTimer = setTimeout(() => {
      gatherTimer = null;
      startNext();
    }, gatherMs);
  }

  function startNext() {
    if (current || !pending.length) return;
    const batch = pending;
    pending = [];
    void run(batch).catch(error => console.error('Lệnh AI:', error)).finally(() => {
      if (!gatherTimer) startNext();
    });
  }

  async function run(batch) {
    const request = batch.map(item => item.text).filter(Boolean).join('\n')
      || 'Xem tệp đính kèm và làm theo nội dung trong đó.';
    const files = batch.flatMap(item => item.files);
    const senderName = [...new Set(batch.map(item => item.senderName).filter(Boolean))].join(', ');
    current = { child: null, prompt: request, startedAt: Date.now(), stopped: false, timedOut: false };
    const attached = files.length ? ` (kèm ${files.length} ảnh/tệp)` : '';
    let progressTimer = null;
    let progressReply = Promise.resolve();
    let processing = true;
    const scheduleProgress = delay => {
      progressTimer = setTimeout(() => {
        if (!processing) return;
        const minutes = ((Date.now() - current.startedAt) / 60000).toFixed(1);
        progressReply = reply(`AI vẫn đang chạy (${minutes} phút). Gõ status để xem yêu cầu hoặc stop để hủy.`)
          .catch(error => console.error('Không gửi được tiến độ AI:', error));
        void progressReply.then(() => { if (processing) scheduleProgress(progressEveryMs); });
      }, delay);
    };
    try {
      await onBusyChange(true);
      await reply(`Đã nhận lệnh từ ${senderName || 'thành viên'}${attached}, đang xử lý...`);
      scheduleProgress(progressFirstMs);
      const { code, stdout, stderr } = await runClaude(buildPrompt(request, senderName, files));
      processing = false;
      clearTimeout(progressTimer);
      await progressReply;
      const minutes = ((Date.now() - current.startedAt) / 60000).toFixed(1);
      if (current.stopped) return await reply('Đã hủy lệnh theo yêu cầu.');
      if (current.timedOut) return await reply(`Hết thời gian (${Math.round(timeoutMs / 60000)} phút), đã dừng lệnh.`);
      let result = null;
      try { result = JSON.parse(stdout); } catch { /* không phải JSON: báo lỗi bên dưới */ }
      if (result?.session_id) sessionId = result.session_id;
      if (code !== 0 || !result || result.is_error) {
        const detail = (result?.result || stderr || stdout || `mã thoát ${code}`).trim();
        if (isQuotaError(detail)) {
          const agy = await runAgy(buildAgyPrompt(request, senderName, files));
          if (current.stopped) return await reply('Đã hủy lệnh theo yêu cầu.');
          if (current.timedOut) return await reply(`Hết thời gian (${Math.round(timeoutMs / 60000)} phút), đã dừng lệnh.`);
          if (agy.ok) {
            const total = ((Date.now() - current.startedAt) / 60000).toFixed(1);
            return await reply(`Xong (${total} phút):\n${agy.text || '(không có nội dung)'}`);
          }
        }
        return await reply(`Lỗi khi chạy AI (${minutes} phút):\n${detail.slice(-1500)}`);
      }
      return await reply(`Xong (${minutes} phút):\n${String(result.result || '').trim() || '(không có nội dung)'}`);
    } catch (error) {
      console.error('Không xử lý được lệnh AI:', error);
      try { await reply(`Lỗi xử lý AI: ${error.message}`); } catch (replyError) {
        console.error('Không gửi được lỗi AI về Zalo:', replyError);
      }
    } finally {
      processing = false;
      clearTimeout(progressTimer);
      await progressReply;
      current = null;
      try { await onBusyChange(false); } catch (error) {
        console.error('Không xóa được trạng thái AI đang chạy:', error);
      }
    }
  }

  // `files`: đường dẫn ảnh/tệp bridge đã tải về từ tin nhắn; ảnh không chú thích vẫn là một yêu cầu.
  async function handle(text, senderName, files = []) {
    if (recentlySent.includes(unmarked(text))) return;
    const botMarked = String(text || '').trim().startsWith(BOT_MARK);
    const command = parseAiCommand(text) || (files.length && !botMarked ? { kind: 'run', prompt: '' } : null);
    if (!command) return;
    console.log(`Lệnh AI từ ${senderName}: ${command.kind}`);
    if (command.kind === 'help') {
      return reply('Cách dùng: nhắn bất kỳ yêu cầu nào để sửa code / phân tích.\nstatus — xem lệnh đang chạy\n'
        + 'stop — hủy lệnh đang chạy\nnew — bắt đầu phiên mới (quên ngữ cảnh cũ)\nhelp — xem hướng dẫn');
    }
    if (command.kind === 'status') {
      const waiting = pending.length ? `\n${pending.length} tin đang chờ.` : '';
      return reply((current
        ? `Đang chạy ${((Date.now() - current.startedAt) / 60000).toFixed(1)} phút: ${current.prompt.slice(0, 200)}`
        : 'Không có lệnh nào đang chạy.') + waiting);
    }
    if (command.kind === 'stop') {
      const dropped = pending.length;
      pending = [];
      clearTimeout(gatherTimer);
      gatherTimer = null;
      if (!current) return reply(dropped ? `Đã bỏ ${dropped} tin đang chờ.` : 'Không có lệnh nào đang chạy.');
      current.stopped = true;
      killTree(current.child);
      return;
    }
    if (command.kind === 'new') {
      sessionId = null;
      return reply('Phiên mới sẽ bắt đầu từ lệnh tiếp theo.');
    }
    if (current && !pending.length) {
      await reply('Đã nhận thêm, sẽ làm tiếp sau lệnh hiện tại. Gõ stop để hủy.');
    }
    pending.push({ text: command.prompt, files, senderName });
    schedule();
  }

  // Cho test: chờ đến khi không còn lệnh đang chạy, đang gom hay đang chờ.
  async function whenIdle() {
    while (current || pending.length || gatherTimer) await new Promise(resolve => setTimeout(resolve, 1));
  }

  function cancelPendingFrom(senderName) {
    const before = pending.length;
    pending = pending.filter(item => item.senderName !== senderName);
    if (!pending.length) {
      clearTimeout(gatherTimer);
      gatherTimer = null;
    }
    return before - pending.length;
  }

  return { handle, whenIdle, cancelPendingFrom, isBusy: () => current !== null };
}
