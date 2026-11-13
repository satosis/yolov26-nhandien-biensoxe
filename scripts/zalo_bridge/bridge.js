import { readFile, writeFile, mkdir, unlink, stat } from 'node:fs/promises';
import { unlinkSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'node:http';
import { timingSafeEqual } from 'node:crypto';
import sharp from 'sharp';
import { Zalo, ThreadType, LoginQRCallbackEventType } from 'zca-js';
import { createAiRunner, BOT_MARK } from './ai_commands.js';
import { createInvoiceSender } from './invoice_sender.js';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const runtime = path.join(project, 'frontend', '.runtime', 'zalo_inbox');
const qrPath = path.join(runtime, 'qr.png');
const sessionPath = path.join(runtime, 'session.json');
const statusPath = path.join(runtime, 'bridge-status.json');
const portPath = path.join(runtime, 'bridge-port');
const aiBusyPath = path.join(runtime, 'ai-running.pid');
const pendingAiPath = path.join(runtime, 'pending_ai_requests.json');
const ingestUrl = 'http://127.0.0.1:8001/api/zalo_bridge/ingest';
const companiesUrl = 'http://127.0.0.1:8001/api/zalo_bridge/companies';
const maxFileBytes = 40 * 1024 * 1024;
// Nhóm nhận lệnh "/ai ..." để Claude Code sửa code; đổi tên bằng file ai-group-name trong runtime.
const defaultAiGroupName = 'AI';

async function imageMetadataGetter(filePath) {
  const data = await readFile(filePath);
  const metadata = await sharp(data).metadata();
  return {
    height: metadata.height,
    width: metadata.width,
    size: metadata.size || data.length,
  };
}

async function status(state, detail = '') {
  await writeFile(statusPath, JSON.stringify({ state, detail, updated_at: new Date().toISOString() }));
}

async function login() {
  const zalo = new Zalo({ selfListen: true, imageMetadataGetter });
  try {
    const session = JSON.parse(await readFile(sessionPath, 'utf8'));
    return { api: await zalo.login(session), credentials: null };
  } catch {
    await status('qr', 'Mở trang Yêu cầu Zalo và quét mã QR bằng tài khoản cần kết nối.');
    let credentials = null;
    const api = await zalo.loginQR({ qrPath }, event => {
      if (event.type === LoginQRCallbackEventType.QRCodeGenerated) {
        void event.actions.saveToFile(qrPath).then(async () => {
          await status('qr', 'Đang chờ quét mã QR');
          console.log(`QR đã tạo: ${qrPath}`);
        }).catch(error => console.error('Không lưu được QR:', error.message));
      } else if (event.type === LoginQRCallbackEventType.QRCodeExpired) {
        event.actions.retry();
      } else if (event.type === LoginQRCallbackEventType.GotLoginInfo) {
        credentials = event.data;
      }
    });
    return { api, credentials };
  }
}

function normalizePhone(value) {
  const digits = String(value || '').replace(/\D/g, '');
  return digits.startsWith('84') ? `0${digits.slice(2)}` : digits;
}

async function downloadAttachment(href) {
  let url = new URL(href);
  for (let redirect = 0; redirect <= 3; redirect++) {
    if (url.protocol !== 'https:') {
      throw new Error('Link tệp phải dùng HTTPS');
    }
    const response = await fetch(url, { redirect: 'manual', signal: AbortSignal.timeout(30000) });
    if (response.status >= 300 && response.status < 400) {
      url = new URL(response.headers.get('location'), url);
      continue;
    }
    if (!response.ok) throw new Error(`Tải tệp: HTTP ${response.status}`);
    if (Number(response.headers.get('content-length') || 0) > maxFileBytes) {
      throw new Error('Tệp lớn hơn 40 MB');
    }
    const chunks = [];
    let size = 0;
    for await (const chunk of response.body) {
      size += chunk.length;
      if (size > maxFileBytes) {
        throw new Error('Tệp lớn hơn 40 MB');
      }
      chunks.push(chunk);
    }
    return { data: Buffer.concat(chunks), type: response.headers.get('content-type') || 'application/octet-stream' };
  }
  throw new Error('Quá nhiều lần chuyển hướng tệp');
}

async function readRequestJson(request) {
  const chunks = [];
  let total = 0;
  for await (const chunk of request) {
    total += chunk.length;
    if (total > 65536) throw new Error('Yêu cầu gửi tin quá dài');
    chunks.push(chunk);
  }
  const text = Buffer.concat(chunks).toString('utf8').trim();
  if (!text) return {};
  return JSON.parse(text);
}

function matchGroupToCompany(groupName, companies) {
  const gName = String(groupName || '').trim().toLocaleLowerCase('vi');
  if (!gName) return null;

  for (const comp of companies) {
    const cName = String(comp.name || '').trim().toLocaleLowerCase('vi');
    // PHẢI MATCH 100% TÊN NHÓM MỚI LÀM VIỆC, KHÔNG THÌ THÔI
    if (gName === cName) {
      return comp;
    }
  }
  return null;
}

let lastCompaniesWarning = '';

async function fetchCompanies(secret) {
  try {
    const response = await fetch(companiesUrl, {
      headers: { 'X-Zalo-Bridge-Secret': secret },
      signal: AbortSignal.timeout(5000),
    });
    if (response.ok) {
      const data = await response.json();
      if (Array.isArray(data.companies) && data.companies.length > 0) {
        lastCompaniesWarning = '';
        return data.companies;
      }
    }
  } catch (error) {
    if (error.message !== lastCompaniesWarning) {
      console.warn(`Không tải được danh sách công ty: ${error.message}`);
      lastCompaniesWarning = error.message;
    }
  }
  return [];
}

async function main() {
  await mkdir(runtime, { recursive: true });
  const { api, credentials } = await login();
  const invoiceSender = createInvoiceSender({
    api,
    ThreadType,
    storePath: path.join(runtime, 'sent-messages.json'),
  });
  const account = (await api.fetchAccountInfo()).profile;
  const expectedPhone = await readFile(path.join(runtime, 'expected-phone'), 'utf8').catch(() => '');
  const actualPhone = normalizePhone(account.phoneNumber);
  if (expectedPhone.trim() && actualPhone.length === 10 && actualPhone !== normalizePhone(expectedPhone)) {
    throw new Error('Tài khoản quét QR không trùng số điện thoại đã chọn');
  }
  if (credentials) await writeFile(sessionPath, JSON.stringify(credentials), { mode: 0o600 });

  const secret = (await readFile(path.join(runtime, 'bridge-secret'), 'ascii')).trim();

  let knownCompanies = await fetchCompanies(secret);
  const matchedGroups = new Map(); // groupId -> { groupId, groupName, companyId, companyName }
  const companyGroups = new Map(); // companyId -> entry
  const aiGroupName = ((await readFile(path.join(runtime, 'ai-group-name'), 'utf8').catch(() => ''))
    .trim() || defaultAiGroupName).toLocaleLowerCase('vi');
  let aiGroupId = null;
  // Khi bridge khởi động lại, tin của người dùng đến trong lúc gián đoạn được lấy lại từ lịch sử nhóm
  // và trả lời (xem replayMissed). aiSeenMsgIds tránh xử lý trùng một tin giữa lịch sử và listener
  // trong cùng một phiên (nối lại tạm thời khi đang xử lý).
  const aiSeenMsgIds = new Set();
  // Ảnh bridge tự gửi vào nhóm AI quay lại qua listener; không phải lệnh của người dùng.
  const botFileIds = new Set();
  let botFilesSending = 0;

  async function refreshMatchedGroups() {
    try {
      const comps = await fetchCompanies(secret);
      if (comps.length > 0) knownCompanies = comps;
      const groups = await api.getAllGroups();
      const groupIds = Object.keys(groups.gridVerMap || {});
      if (groupIds.length === 0) return;
      const details = await api.getGroupInfo(groupIds);
      const gridInfoMap = details.gridInfoMap || {};

      matchedGroups.clear();
      companyGroups.clear();

      for (const [gid, ginfo] of Object.entries(gridInfoMap)) {
        const gname = ginfo.name || '';
        if (gname.trim().toLocaleLowerCase('vi') === aiGroupName) {
          if (aiGroupId !== String(gid)) console.log(`Nhóm lệnh AI: "${gname}" (${gid})`);
          aiGroupId = String(gid);
          continue;
        }
        const comp = matchGroupToCompany(gname, knownCompanies);
        if (comp) {
          const entry = {
            groupId: String(gid),
            groupName: gname,
            companyId: comp.id,
            companyName: comp.name,
          };
          matchedGroups.set(String(gid), entry);
          if (!companyGroups.has(Number(comp.id))) {
            companyGroups.set(Number(comp.id), entry);
          }
          console.log(`Đã khớp nhóm Zalo "${gname}" (${gid}) -> Công ty [${comp.id}] ${comp.name}`);
        }
      }
    } catch (err) {
      console.error('Lỗi khi tải nhóm Zalo:', err.message);
    }
  }

  await refreshMatchedGroups();

  // A forced restart cannot finish or answer a running AI command. Tell the
  // group to resend it instead of leaving the earlier acknowledgement hanging.
  const previousAiPid = Number((await readFile(aiBusyPath, 'ascii').catch(() => '')).trim());
  if (previousAiPid && previousAiPid !== process.pid) {
    let previousBridgeAlive = false;
    try { process.kill(previousAiPid, 0); previousBridgeAlive = true; } catch { /* exited */ }
    if (!previousBridgeAlive) {
      const markerAgeMs = Date.now() - (await stat(aiBusyPath).catch(() => ({ mtimeMs: 0 }))).mtimeMs;
      await unlink(aiBusyPath).catch(() => {});
      if (aiGroupId && markerAgeMs < 60 * 60 * 1000) {
        await api.sendMessage({ msg: '🤖 Lệnh AI trước bị gián đoạn khi cầu nối khởi động lại. Vui lòng gửi lại yêu cầu; lệnh cũ không tự chạy tiếp.' }, aiGroupId, ThreadType.Group);
      }
    }
  }

  const accountName = account.displayName || account.zaloName || account.name || '';
  async function updateStatusConnected() {
    const names = Array.from(matchedGroups.values()).map(g => g.groupName);
    const detail = names.length > 0
      ? `Tài khoản ${accountName}: đang nhận tin từ nhóm ${names.join(', ')}`
      : `Tài khoản ${accountName}: chưa tìm thấy nhóm Zalo trùng tên công ty`;
    await status('connected', detail);
  }

  function findTargetGroup(payload) {
    if (payload.group_id && String(payload.group_id) === aiGroupId) {
      return { groupId: aiGroupId, groupName: 'AI' };
    }
    if (payload.group_id && matchedGroups.has(String(payload.group_id))) {
      return matchedGroups.get(String(payload.group_id));
    }
    if (payload.company_id && companyGroups.has(Number(payload.company_id))) {
      return companyGroups.get(Number(payload.company_id));
    }
    if (payload.company_name) {
      const comp = matchGroupToCompany(payload.company_name, knownCompanies);
      if (comp && companyGroups.has(Number(comp.id))) return companyGroups.get(Number(comp.id));
    }
    return null;
  }

  // Backend thường khởi động bridge trước khi web (cổng 8001) sẵn sàng; khi chưa tải được
  // danh sách công ty thì thử lại mỗi phút để nhóm công ty được nhận ra mà không cần khởi động lại.
  setInterval(() => {
    if (knownCompanies.length > 0) return;
    void refreshMatchedGroups().then(updateStatusConnected).catch(() => {});
  }, 60 * 1000).unref();

  const sendServer = createServer(async (request, response) => {
    const supplied = Buffer.from(String(request.headers['x-zalo-bridge-secret'] || ''));
    const expected = Buffer.from(secret);
    const allowedUrls = ['/send-invoice-notice', '/list-groups', '/send-invoice-file', '/recall-messages'];
    if (request.method !== 'POST' || !allowedUrls.includes(request.url)
        || supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
      response.writeHead(404).end();
      return;
    }

    if (request.url === '/list-groups') {
      try {
        const groups = await invoiceSender.listGroups();
        response.writeHead(200, { 'Content-Type': 'application/json' })
          .end(JSON.stringify({ ok: true, groups }));
      } catch (error) {
        console.error('Không tải được danh sách nhóm:', error);
        response.writeHead(502, { 'Content-Type': 'application/json' })
          .end(JSON.stringify({ ok: false, error: error.message }));
      }
      return;
    }

    if (request.url === '/send-invoice-file') {
      try {
        const payload = await readRequestJson(request);
        const groupId = payload.group_id;
        const filePath = payload.file_path;
        if (!groupId || !filePath) {
          response.writeHead(400, { 'Content-Type': 'application/json' })
            .end(JSON.stringify({ ok: false, error: 'Thiếu group_id hoặc file_path' }));
          return;
        }
        const caption = typeof payload.caption === 'string' ? payload.caption : '';
        const toAiGroup = String(groupId) === aiGroupId;
        if (toAiGroup) botFilesSending += 1;
        let res;
        try {
          res = await invoiceSender.sendFile({ groupId, filePath, caption });
        } finally {
          if (toAiGroup) botFilesSending -= 1;
        }
        if (toAiGroup) res.msg_ids.forEach(id => botFileIds.add(String(id)));
        response.writeHead(200, { 'Content-Type': 'application/json' })
          .end(JSON.stringify({ ok: true, group_id: groupId, msg_ids: res.msg_ids }));
      } catch (error) {
        console.error('Không gửi được file hóa đơn:', error);
        const status = error.code === 'ENOENT' ? 400 : 502;
        response.writeHead(status, { 'Content-Type': 'application/json' })
          .end(JSON.stringify({ ok: false, error: error.message }));
      }
      return;
    }

    if (request.url === '/recall-messages') {
      try {
        const payload = await readRequestJson(request);
        const groupId = payload.group_id;
        const msgIds = payload.msg_ids;
        if (!groupId || !Array.isArray(msgIds) || msgIds.length === 0) {
          response.writeHead(400, { 'Content-Type': 'application/json' })
            .end(JSON.stringify({ ok: false, error: 'Thiếu group_id hoặc danh sách msg_ids' }));
          return;
        }
        const results = await invoiceSender.recall({ groupId, msgIds });
        const ok = results.every(r => r.ok);
        response.writeHead(200, { 'Content-Type': 'application/json' })
          .end(JSON.stringify({ ok, results }));
      } catch (error) {
        console.error('Không thu hồi được tin nhắn:', error);
        response.writeHead(502, { 'Content-Type': 'application/json' })
          .end(JSON.stringify({ ok: false, error: error.message }));
      }
      return;
    }
    try {
      const payload = await readRequestJson(request);
      try {
        let targetGroup = findTargetGroup(payload);
        if (!targetGroup) {
          // Nhóm mới đổi tên/vừa thêm, hoặc lúc khởi động chưa tải được danh sách công ty: tải lại rồi thử lần nữa.
          await refreshMatchedGroups();
          await updateStatusConnected();
          targetGroup = findTargetGroup(payload);
        }

        if (!targetGroup || !targetGroup.groupId) {
          response.writeHead(400, { 'Content-Type': 'application/json' })
            .end(JSON.stringify({ ok: false, stage: 'validate', error: 'Không tìm thấy nhóm Zalo khớp 100% với tên công ty' }));
          return;
        }

        const targetGroupId = targetGroup.groupId;
        const text = typeof payload.text === 'string' ? payload.text.trim() : '';
        const rawImages = Array.isArray(payload.images)
          ? payload.images
          : (payload.image_path ? [payload.image_path] : []);
        const images = rawImages.filter(p => typeof p === 'string' && p.trim());

        if (!text && images.length === 0) {
          response.writeHead(400, { 'Content-Type': 'application/json' })
            .end(JSON.stringify({ ok: false, stage: 'validate', error: 'Cần có nội dung tin nhắn hoặc hình ảnh' }));
          return;
        }

        if (images.length > 0) {
          for (let i = 0; i < images.length; i++) {
            const imgPath = images[i];
            const caption = (i === images.length - 1) ? text : '';
            await api.sendMessage({ msg: caption, attachments: [imgPath] }, targetGroupId, ThreadType.Group);
          }
        } else if (text) {
          await api.sendMessage({ msg: text }, targetGroupId, ThreadType.Group);
        }
      } catch (error) {
        console.error('Không gửi được tin/ảnh vào nhóm:', error);
        response.writeHead(502, { 'Content-Type': 'application/json' })
          .end(JSON.stringify({ ok: false, stage: error.stage || 'send', error: error.message }));
        return;
      }
      response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({ ok: true }));
    } catch (error) {
      console.error('Không gửi được tin vào nhóm:', error);
      response.writeHead(502, { 'Content-Type': 'application/json' }).end(JSON.stringify({ ok: false, stage: 'send' }));
    }
  });

  await new Promise((resolve, reject) => {
    sendServer.once('error', reject);
    sendServer.listen(0, '127.0.0.1', resolve);
  });
  await writeFile(portPath, String(sendServer.address().port), { mode: 0o600 });
  process.once('exit', () => {
    try { unlinkSync(portPath); } catch { /* already removed */ }
  });

  await updateStatusConnected();

  async function savePendingAi(item) {
    try {
      const existing = JSON.parse(await readFile(pendingAiPath, 'utf8').catch(() => '[]'));
      existing.push(item);
      await writeFile(pendingAiPath, JSON.stringify(existing, null, 2));
    } catch {}
  }

  async function clearPendingAi(batch) {
    try {
      let existing = JSON.parse(await readFile(pendingAiPath, 'utf8').catch(() => '[]'));
      const batchTexts = new Set(batch.map(b => b.text));
      existing = existing.filter(e => !batchTexts.has(e.text));
      await writeFile(pendingAiPath, JSON.stringify(existing, null, 2));
    } catch {}
  }

  const aiRunner = createAiRunner({
    project,
    onBusyChange: async busy => {
      if (busy) await writeFile(aiBusyPath, String(process.pid));
      else await unlink(aiBusyPath).catch(error => { if (error.code !== 'ENOENT') throw error; });
    },
    onBatchDone: async batch => {
      await clearPendingAi(batch);
    },
    sendText: async text => {
      if (!aiGroupId) throw new Error('Chưa tìm thấy nhóm lệnh AI');
      await api.sendMessage({ msg: text }, aiGroupId, ThreadType.Group);
    },
  });

  async function recoverPendingAi() {
    try {
      const items = JSON.parse(await readFile(pendingAiPath, 'utf8').catch(() => '[]'));
      const valid = items.filter(it => Date.now() - (Number(it.ts) || 0) < 2 * 60 * 60 * 1000);
      if (valid.length > 0) {
        console.log(`Gom ${valid.length} tin nhắn AI chưa hoàn thành từ phiên trước để xử lý lại...`);
        for (const item of valid) {
          await aiRunner.handle(item.text, item.senderName, item.files || []);
        }
      }
    } catch (e) {
      console.warn('Không phục hồi được tin nhắn AI dở dang:', e.message);
    }
  }
  void recoverPendingAi();
  async function handleAiMessage(message, content) {
    const msgId = String(message.data.msgId);
    if (aiSeenMsgIds.has(msgId)) return;
    if (aiSeenMsgIds.size > 500) aiSeenMsgIds.clear();
    aiSeenMsgIds.add(msgId);
    if (message.isSelf && content && typeof content === 'object'
        && (botFileIds.delete(String(message.data.msgId)) || botFilesSending > 0)) return;
    // Ảnh có chú thích: chú thích nằm ở title hoặc description tùy loại tin.
    const text = typeof content === 'string' ? content : (content?.title || content?.description || '');
    const senderName = message.data.dName || (message.isSelf ? account.displayName : '');
    const files = [];
    let imageFilePath = null;
    if (content && typeof content === 'object' && content.href) {
      try {
        const file = await downloadAttachment(content.href);
        const imageExtension = file.type.match(/^image\/(png|jpe?g|gif|webp)/)?.[1]?.replace('jpeg', 'jpg');
        const titleExtension = path.extname(String(content.title || '')).slice(1).toLowerCase();
        const extension = imageExtension
          || (['xlsx', 'xls', 'csv', 'pdf', 'docx', 'txt'].includes(titleExtension) ? titleExtension : 'bin');
        const filePath = path.join(runtime, 'ai_uploads', `${message.data.msgId}.${extension}`);
        await mkdir(path.dirname(filePath), { recursive: true });
        await writeFile(filePath, file.data);
        files.push(filePath);
        if (imageExtension) {
          imageFilePath = filePath;
        }
      } catch (error) {
        console.error(`Không tải được tệp của lệnh AI ${message.data.msgId}: ${error.message}`);
        aiRunner.cancelPendingFrom(senderName);
        await api.sendMessage({ msg: `🤖 Không tải được tệp "${text.slice(0, 80)}": ${error.message}. Vui lòng gửi lại tệp hoặc ảnh.` }, aiGroupId, ThreadType.Group);
        return;
      }
    }
    if (imageFilePath) {
      await fetch('http://127.0.0.1:8001/api/zalo_bridge/company-card', {
        method: 'POST',
        headers: { 'X-Zalo-Bridge-Secret': secret, 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: imageFilePath })
      }).catch(error => console.error(`Không đọc được thông tin công ty từ ảnh ${message.data.msgId}: ${error.message}`));
    }
    console.log(`Tin nhóm AI ${message.data.msgId} (${message.data.msgType}): ${text.slice(0, 80)} [${files.length} tệp]`);
    await savePendingAi({ msgId, text, senderName, files, ts: Date.now() });
    await aiRunner.handle(text, senderName, files);
  }

  // Trả lời những tin của người dùng đến trong lúc bridge gián đoạn mà chưa được trả lời. Nguồn tin cũ
  // lấy qua websocket (sự kiện old_messages, xin bằng requestOldMessages) vì endpoint lịch sử HTTP 404.
  // Hai điều kiện BẮT BUỘC để replay một tin:
  //   1) Trong RECENT_MS gần đây. Khoảng trống khi bridge restart chỉ vài giây–phút; chặn cứng này để
  //      KHÔNG BAO GIỜ chạy lại tin cũ (old_messages có thể trả về một lát lịch sử cũ, không đáng tin).
  //   2) Sau tin gần nhất của chính bot (tin tự gửi / cùng uid / có dấu 🤖, kể cả "đã nhận lệnh"/"đang
  //      chạy"): bot đã nhắn gì đó sau một tin = đã nhận và xử lý (gián đoạn giữa chừng đã có thông báo
  //      "gửi lại yêu cầu" lo). Không lưu mốc ra file để tránh đánh dấu "đã xem" rồi bị kill trước khi
  //      kịp trả lời (sẽ mất tin); nhờ (1) nên cũng không cần.
  // 2 giờ: đủ gom các tin nhắn lỗi cũ / chưa xử lý trong phiên làm việc
  const RECENT_MS = 2 * 60 * 60 * 1000;
  const ERROR_MARKERS = ['lỗi khi chạy ai', 'lỗi xử lý ai', 'lỗi ai', '5-hour limit', 'hết thời gian', 'limit reached'];
  async function replayMissed(messages) {
    if (!aiGroupId) return;
    const group = messages.filter(m => String(m.threadId) === aiGroupId);
    if (!group.length) return;
    const botUid = String(api.getOwnId());
    const textOf = message => {
      const content = message.data.content;
      return (typeof content === 'string' ? content : (content?.title || content?.description || '')).trim();
    };
    const isBot = message => message.isSelf
      || String(message.data.uidFrom) === botUid
      || textOf(message).startsWith(BOT_MARK);
    // Tin bot báo lỗi hoặc tin thông báo tạm ("đang xử lý...") thì KHÔNG tính là đã xử lý thành công.
    const isBotErrorOrAck = message => {
      const text = textOf(message).toLowerCase();
      return isBot(message) && (
        ERROR_MARKERS.some(marker => text.includes(marker))
        || text.includes('đang xử lý')
        || text.includes('vẫn đang chạy')
        || text.includes('đã nhận thêm')
      );
    };
    const msgs = group.slice().sort((a, b) => (Number(a.data.ts) || 0) - (Number(b.data.ts) || 0));
    // Chỉ lấy mốc của tin bot hoàn tất thành công (hoặc người dùng chủ động hủy)
    const lastSuccessBotTs = msgs.reduce(
      (max, m) => (isBot(m) && !isBotErrorOrAck(m) ? Math.max(max, Number(m.data.ts) || 0) : max),
      0
    );
    const cutoff = Math.max(lastSuccessBotTs, Date.now() - RECENT_MS);
    const missed = msgs.filter(m => !isBot(m) && textOf(m)
      && !aiSeenMsgIds.has(String(m.data.msgId))
      && (Number(m.data.ts) || 0) > cutoff)
      .slice(-10); // gom tối đa 10 tin gần nhất chưa xử lý thành công
    if (missed.length > 0) {
      console.log(`Gom ${missed.length} tin nhắn chưa xử lý / lỗi cũ trong nhóm AI để chạy lại...`);
      for (const message of missed) {
        await handleAiMessage(message, message.data.content);
      }
    }
  }

  async function ingestMessage(message) {
    if (message.type !== ThreadType.Group) return;
    const threadIdStr = String(message.threadId);
    let target = matchedGroups.get(threadIdStr);
    if (!target && threadIdStr !== aiGroupId) {
      await refreshMatchedGroups();
      target = matchedGroups.get(threadIdStr);
    }

    const content = message.data.content;
    const text = typeof content === 'string' ? content : (content?.description || '');
    if (threadIdStr === aiGroupId) {
      await handleAiMessage(message, content);
      return;
    }
    if (!target) return;

    // Ignore legacy QR echoes, invoice notices, and transfer info; none is a new customer request.
    if (/^QR tạm ứng lương\b/iu.test(text.trim())
        || text.trim().startsWith('Đã tạo hóa đơn nháp trên BKAV')
        || /thông tin (thanh toán|chuyển khoản)/iu.test(text.trim())) return;
    const attachment = typeof content === 'object' && content !== null ? content : null;
    const event = {
      group_id: target.groupId,
      group_name: target.groupName,
      company_id: target.companyId,
      company_name: target.companyName,
      message_id: message.data.msgId,
      sender_id: message.data.uidFrom,
      sender_name: message.data.dName || (message.isSelf ? account.displayName : ''),
      sent_at: new Date(Number(message.data.ts)).toISOString(),
      text,
      attachment: attachment ? {
        title: attachment.title || '',
        type: attachment.type || message.data.msgType,
        href: attachment.href || ''
      } : null
    };
    const form = new FormData();
    form.append('event', JSON.stringify(event));
    if (attachment?.href) {
      try {
        const file = await downloadAttachment(attachment.href);
        form.append('file', new Blob([file.data], { type: file.type }), attachment.title || 'zalo-file');
      } catch (error) {
        console.error(`Không tải được tệp tin ${event.message_id}: ${error.message}`);
      }
    }
    const response = await fetch(ingestUrl, {
      method: 'POST', headers: { 'X-Zalo-Bridge-Secret': secret }, body: form
    });
    if (!response.ok) throw new Error(`Lưu yêu cầu: HTTP ${response.status}`);
  }

  api.listener.on('message', message => {
    try {
      invoiceSender.rememberSelfMessage(message);
    } catch (err) {
      console.warn('Lỗi ghi nhớ tin nhắn tự gửi:', err.message);
    }
    if (message.type !== ThreadType.Group) return;
    void (async () => {
      await ingestMessage(message);
    })().catch(error => console.error(`Tin nhắn ${message.data?.msgId}: ${error.message}`));
  });
  api.listener.on('old_messages', (messages, threadType) => {
    if (threadType !== ThreadType.Group) return;
    void replayMissed(messages).catch(error => console.error('Lỗi trả lời tin cũ nhóm AI:', error.message));
  });
  api.listener.on('connected', () => {
    void updateStatusConnected();
    // Khi vừa kết nối lại, xin máy chủ đẩy lại các tin nhóm gần đây để bắt tin bị bỏ lỡ lúc gián đoạn.
    try { api.listener.requestOldMessages(ThreadType.Group, null); }
    catch (error) { console.error('Không xin được tin cũ nhóm AI:', error.message); }
  });
  api.listener.on('disconnected', (code, reason) => {
    void status('disconnected', `${code}: ${reason}`);
  });
  api.listener.on('error', error => console.error('Kết nối Zalo:', error));
  // Hết lần nối lại thì thoát: backend khởi động lại bridge (process còn sống nhưng mất kết nối thì không ai nối lại).
  api.listener.on('closed', (code, reason) => {
    console.error(`Kết nối Zalo đã đóng (${code}: ${reason}), bridge thoát để khởi động lại.`);
    void status('disconnected', `${code}: ${reason}`).finally(() => process.exit(1));
  });
  await api.listener.start({ retryOnClose: true });
}

main().catch(error => {
  console.error('Zalo bridge:', error);
  void status('error', error.message).finally(() => process.exit(1));
});
