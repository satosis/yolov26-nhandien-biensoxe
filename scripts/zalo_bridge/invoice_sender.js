import { readFileSync, promises as fsPromises } from 'node:fs';

export function createInvoiceSender({
  api,
  ThreadType,
  storePath,
  now = () => Date.now(),
  recallTimeoutMs = 5000,
  pollIntervalMs = 250,
}) {
  const sentMap = new Map(); // msgId -> { msgId, cliMsgId, threadId, ts }

  if (storePath) {
    try {
      const raw = readFileSync(storePath, 'utf8');
      const items = JSON.parse(raw);
      if (Array.isArray(items)) {
        for (const item of items) {
          if (item && item.msgId && item.cliMsgId) {
            sentMap.set(String(item.msgId), {
              msgId: String(item.msgId),
              cliMsgId: String(item.cliMsgId),
              threadId: String(item.threadId || ''),
              ts: Number(item.ts) || now(),
            });
          }
        }
      }
    } catch {
      // bỏ qua nếu file chưa tồn tại hoặc không hợp lệ
    }
  }

  function rememberSelfMessage(message) {
    if (!message || !message.isSelf || !message.data) return;
    const msgId = message.data.msgId != null ? String(message.data.msgId) : '';
    const cliMsgId = message.data.cliMsgId != null ? String(message.data.cliMsgId) : '';
    if (!msgId || !cliMsgId) return;

    const entry = {
      msgId,
      cliMsgId,
      threadId: message.threadId != null ? String(message.threadId) : '',
      ts: Number(message.data.ts) || now(),
    };

    if (sentMap.has(msgId)) {
      sentMap.delete(msgId);
    }
    sentMap.set(msgId, entry);

    while (sentMap.size > 500) {
      const firstKey = sentMap.keys().next().value;
      sentMap.delete(firstKey);
    }

    if (storePath) {
      void (async () => {
        try {
          await fsPromises.writeFile(
            storePath,
            JSON.stringify(Array.from(sentMap.values())),
            'utf8',
          );
        } catch {
          // ghi lỗi thì bỏ qua
        }
      })();
    }
  }

  let cachedGroups = null;
  let cacheExpiry = 0;

  async function listGroups() {
    const currentTime = now();
    if (cachedGroups && currentTime < cacheExpiry) {
      return cachedGroups;
    }

    const rawGroups = await api.getAllGroups();
    let groupIds = [];
    if (Array.isArray(rawGroups)) {
      groupIds = rawGroups.map(String);
    } else if (rawGroups && typeof rawGroups === 'object') {
      if (rawGroups.gridVerMap) {
        groupIds = Object.keys(rawGroups.gridVerMap);
      } else if (Array.isArray(rawGroups.groupIds)) {
        groupIds = rawGroups.groupIds.map(String);
      } else {
        groupIds = Object.keys(rawGroups);
      }
    }

    const result = [];
    if (groupIds.length > 0) {
      const details = await api.getGroupInfo(groupIds);
      const gridInfoMap = details?.gridInfoMap || (details && typeof details === 'object' ? details : {});
      for (const gid of groupIds) {
        const info = gridInfoMap[gid];
        const name = (info && typeof info === 'object' && info.name != null)
          ? String(info.name)
          : (typeof info === 'string' ? info : String(gid));
        result.push({ group_id: String(gid), name });
      }
    }

    result.sort((a, b) => a.name.localeCompare(b.name, 'vi'));
    cachedGroups = result;
    cacheExpiry = currentTime + 30 * 1000;
    return result;
  }

  async function sendFile({ groupId, filePath, caption = '' }) {
    if (!filePath) {
      throw new Error('Đường dẫn file không được để trống');
    }
    await fsPromises.access(filePath);
    const res = await api.sendMessage({ msg: caption, attachments: [filePath] }, groupId, ThreadType.Group);
    const attachments = Array.isArray(res?.attachment)
      ? res.attachment
      : (Array.isArray(res?.attachments) ? res.attachments : []);
    const rawIds = [res?.message?.msgId, ...attachments.map(a => a?.msgId)];
    const msg_ids = rawIds.filter(Boolean).map(String);
    return { msg_ids };
  }

  async function recall({ groupId, msgIds }) {
    const ids = Array.isArray(msgIds) ? msgIds.map(String).filter(Boolean) : [];
    const results = [];

    for (const msgId of ids) {
      const start = now();
      let cliMsgId = null;

      while (now() - start <= recallTimeoutMs) {
        const entry = sentMap.get(msgId);
        if (entry && entry.cliMsgId) {
          cliMsgId = entry.cliMsgId;
          break;
        }
        if (recallTimeoutMs <= 0) break;
        await new Promise(r => setTimeout(r, pollIntervalMs));
      }

      if (!cliMsgId) {
        results.push({
          msg_id: msgId,
          ok: false,
          error: 'Không tìm thấy tin để thu hồi',
        });
        continue;
      }

      try {
        await api.undo({ msgId, cliMsgId }, groupId, ThreadType.Group);
        results.push({ msg_id: msgId, ok: true });
      } catch (e) {
        results.push({
          msg_id: msgId,
          ok: false,
          error: e?.message || String(e),
        });
      }
    }

    return results;
  }

  return {
    rememberSelfMessage,
    listGroups,
    sendFile,
    recall,
  };
}
