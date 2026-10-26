import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { unlinkSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'node:http';
import { timingSafeEqual } from 'node:crypto';
import sharp from 'sharp';
import { Zalo, ThreadType, LoginQRCallbackEventType } from 'zca-js';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const runtime = path.join(project, 'frontend', '.runtime', 'zalo_inbox');
const qrPath = path.join(runtime, 'qr.png');
const sessionPath = path.join(runtime, 'session.json');
const statusPath = path.join(runtime, 'bridge-status.json');
const portPath = path.join(runtime, 'bridge-port');
const ingestUrl = 'http://127.0.0.1:8000/admin/zalo_inbox/ingest';
const companiesUrl = 'http://127.0.0.1:8000/admin/zalo_inbox/companies';
const maxFileBytes = 20 * 1024 * 1024;

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

function allowedAttachmentHost(hostname) {
  return ['zalo.me', 'zaloapp.com', 'zdn.vn'].some(domain =>
    hostname === domain || hostname.endsWith(`.${domain}`));
}

async function downloadAttachment(href) {
  let url = new URL(href);
  for (let redirect = 0; redirect <= 3; redirect++) {
    if (url.protocol !== 'https:' || !allowedAttachmentHost(url.hostname)) {
      throw new Error('Link tệp không thuộc máy chủ Zalo');
    }
    const response = await fetch(url, { redirect: 'manual', signal: AbortSignal.timeout(30000) });
    if (response.status >= 300 && response.status < 400) {
      url = new URL(response.headers.get('location'), url);
      continue;
    }
    if (!response.ok) throw new Error(`Tải tệp: HTTP ${response.status}`);
    if (Number(response.headers.get('content-length') || 0) > maxFileBytes) {
      throw new Error('Tệp lớn hơn 20 MB');
    }
    const chunks = [];
    let size = 0;
    for await (const chunk of response.body) {
      size += chunk.length;
      if (size > maxFileBytes) {
        throw new Error('Tệp lớn hơn 20 MB');
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
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
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

async function fetchCompanies(secret) {
  try {
    const response = await fetch(companiesUrl, {
      headers: { 'X-Zalo-Bridge-Secret': secret },
      signal: AbortSignal.timeout(5000),
    });
    if (response.ok) {
      const data = await response.json();
      if (Array.isArray(data.companies) && data.companies.length > 0) {
        return data.companies;
      }
    }
  } catch (error) {
    console.warn(`Không tải được danh sách công ty: ${error.message}`);
  }
  return [];
}

async function main() {
  await mkdir(runtime, { recursive: true });
  const { api, credentials } = await login();
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

  const accountName = account.displayName || account.zaloName || account.name || '';
  async function updateStatusConnected() {
    const names = Array.from(matchedGroups.values()).map(g => g.groupName);
    const detail = names.length > 0
      ? `Tài khoản ${accountName}: đang nhận tin từ nhóm ${names.join(', ')}`
      : `Tài khoản ${accountName}: chưa tìm thấy nhóm Zalo trùng tên công ty`;
    await status('connected', detail);
  }

  const sendServer = createServer(async (request, response) => {
    const supplied = Buffer.from(String(request.headers['x-zalo-bridge-secret'] || ''));
    const expected = Buffer.from(secret);
    if (request.method !== 'POST' || request.url !== '/send-invoice-notice'
        || supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
      response.writeHead(404).end();
      return;
    }
    try {
      const payload = await readRequestJson(request);
      try {
        let targetGroup = null;
        if (payload.group_id && matchedGroups.has(String(payload.group_id))) {
          targetGroup = matchedGroups.get(String(payload.group_id));
        } else if (payload.company_id && companyGroups.has(Number(payload.company_id))) {
          targetGroup = companyGroups.get(Number(payload.company_id));
        } else if (payload.company_name) {
          const comp = matchGroupToCompany(payload.company_name, knownCompanies);
          if (comp && companyGroups.has(Number(comp.id))) {
            targetGroup = companyGroups.get(Number(comp.id));
          }
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

  async function ingestMessage(message) {
    if (message.type !== ThreadType.Group) return;
    const threadIdStr = String(message.threadId);
    let target = matchedGroups.get(threadIdStr);
    if (!target) {
      await refreshMatchedGroups();
      target = matchedGroups.get(threadIdStr);
    }
    if (!target) return;

    const content = message.data.content;
    const text = typeof content === 'string' ? content : (content?.description || '');
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
    if (message.type !== ThreadType.Group) return;
    void (async () => {
      await ingestMessage(message);
    })().catch(error => console.error(`Tin nhắn ${message.data?.msgId}: ${error.message}`));
  });
  api.listener.on('connected', () => {
    void updateStatusConnected();
  });
  api.listener.on('disconnected', (code, reason) => {
    void status('disconnected', `${code}: ${reason}`);
  });
  api.listener.on('error', error => console.error('Kết nối Zalo:', error));
  await api.listener.start();
}

main().catch(error => {
  console.error('Zalo bridge:', error);
  void status('error', error.message).finally(() => process.exit(1));
});
