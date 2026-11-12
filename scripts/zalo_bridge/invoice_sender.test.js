import assert from 'node:assert/strict';
import test from 'node:test';
import { writeFile, unlink } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { createInvoiceSender } from './invoice_sender.js';

const mockThreadType = { Group: 1, User: 0 };

test('rememberSelfMessage and recall calls undo with correct cliMsgId', async () => {
  let undoCalledWith = null;
  const api = {
    undo: async (data, threadId, threadType) => {
      undoCalledWith = { data, threadId, threadType };
      return { ok: true };
    },
  };

  const sender = createInvoiceSender({
    api,
    ThreadType: mockThreadType,
    recallTimeoutMs: 100,
    pollIntervalMs: 10,
  });

  sender.rememberSelfMessage({
    isSelf: true,
    threadId: 'group-100',
    data: {
      msgId: 'msg-abc-123',
      cliMsgId: 'cli-xyz-789',
      ts: 1700000000,
    },
  });

  const results = await sender.recall({
    groupId: 'group-100',
    msgIds: ['msg-abc-123'],
  });

  assert.equal(results.length, 1);
  assert.equal(results[0].ok, true);
  assert.equal(results[0].msg_id, 'msg-abc-123');
  assert.deepEqual(undoCalledWith, {
    data: { msgId: 'msg-abc-123', cliMsgId: 'cli-xyz-789' },
    threadId: 'group-100',
    threadType: mockThreadType.Group,
  });
});

test('recall when cliMsgId is missing returns ok: false', async () => {
  const api = {
    undo: async () => {
      throw new Error('Should not be called');
    },
  };

  const sender = createInvoiceSender({
    api,
    ThreadType: mockThreadType,
    recallTimeoutMs: 30,
    pollIntervalMs: 10,
  });

  const results = await sender.recall({
    groupId: 'group-100',
    msgIds: ['msg-unknown'],
  });

  assert.equal(results.length, 1);
  assert.equal(results[0].ok, false);
  assert.equal(results[0].msg_id, 'msg-unknown');
  assert.equal(results[0].error, 'Không tìm thấy tin để thu hồi');
});

test('sendFile sends file attachment and returns msg_ids', async () => {
  const tempFile = path.join(os.tmpdir(), `test-file-${Date.now()}.pdf`);
  await writeFile(tempFile, 'fake pdf content');

  let sentPayload = null;
  let sentGroupId = null;

  const api = {
    sendMessage: async (payload, groupId, threadType) => {
      sentPayload = payload;
      sentGroupId = groupId;
      return {
        message: { msgId: 'msg-main-1' },
        attachment: [{ msgId: 'msg-att-2' }],
      };
    },
  };

  try {
    const sender = createInvoiceSender({
      api,
      ThreadType: mockThreadType,
    });

    const res = await sender.sendFile({
      groupId: 'group-200',
      filePath: tempFile,
    });

    assert.equal(sentGroupId, 'group-200');
    assert.deepEqual(sentPayload.attachments, [tempFile]);
    assert.deepEqual(res.msg_ids, ['msg-main-1', 'msg-att-2']);
  } finally {
    await unlink(tempFile).catch(() => {});
  }
});

test('sendFile sends the caption with the file', async () => {
  const tempFile = path.join(os.tmpdir(), `test-caption-${Date.now()}.png`);
  await writeFile(tempFile, 'fake png content');
  let sentPayload = null;
  const api = { sendMessage: async payload => { sentPayload = payload; return { message: { msgId: 'm1' } }; } };
  try {
    const sender = createInvoiceSender({ api, ThreadType: mockThreadType });
    await sender.sendFile({ groupId: 'group-1', filePath: tempFile, caption: '🤖 Hóa đơn nháp' });
    assert.equal(sentPayload.msg, '🤖 Hóa đơn nháp');
  } finally {
    await unlink(tempFile).catch(() => {});
  }
});

test('listGroups returns sorted groups by Vietnamese locale', async () => {
  const api = {
    getAllGroups: async () => ({
      gridVerMap: {
        'g3': 'v1',
        'g1': 'v1',
        'g2': 'v1',
      },
    }),
    getGroupInfo: async (ids) => ({
      gridInfoMap: {
        'g1': { name: 'Công ty Ánh Dương' },
        'g2': { name: 'Công ty Bình Minh' },
        'g3': { name: 'Công ty An Phát' },
      },
    }),
  };

  const sender = createInvoiceSender({
    api,
    ThreadType: mockThreadType,
  });

  const groups = await sender.listGroups();
  assert.equal(groups.length, 3);
  // 'An Phát' < 'Ánh Dương' < 'Bình Minh' in Vietnamese
  assert.equal(groups[0].name, 'Công ty An Phát');
  assert.equal(groups[0].group_id, 'g3');
  assert.equal(groups[1].name, 'Công ty Ánh Dương');
  assert.equal(groups[1].group_id, 'g1');
  assert.equal(groups[2].name, 'Công ty Bình Minh');
  assert.equal(groups[2].group_id, 'g2');
});
