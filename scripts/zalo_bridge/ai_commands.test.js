import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { createAiRunner, parseAiCommand, splitForZalo } from './ai_commands.js';

function fakeSpawn(output, calls, delayMs = 0) {
  return (bin, args) => {
    const child = new EventEmitter();
    child.stdin = new PassThrough();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.exitCode = null;
    let prompt = '';
    child.stdin.on('data', data => { prompt += data; });
    child.stdin.on('finish', () => {
      calls.push({ bin, args, prompt });
      setTimeout(() => {
        child.stdout.end(JSON.stringify(output));
        child.exitCode = 0;
        child.emit('close', 0);
      }, delayMs);
    });
    return child;
  };
}

function runner(calls, sent, options = {}) {
  return createAiRunner({
    project: '.', gatherMs: 0, sendText: async text => { sent.push(text); },
    spawnFn: fakeSpawn({ result: 'Đã sửa file x.py', session_id: 'abc', is_error: false }, calls, options.delayMs),
    ...options,
  });
}

test('every message is a command except empty text and bot replies', () => {
  assert.deepEqual(parseAiCommand('ok anh'), { kind: 'run', prompt: 'ok anh' });
  assert.equal(parseAiCommand('   '), null);
  assert.equal(parseAiCommand('🤖 Xong (0.1 phút)'), null);
  assert.equal(parseAiCommand('Hung tạo ghi chú Link truy cập: https://econ.trycloudflare.com'), null);
  assert.equal(parseAiCommand('Link truy cập: https://econ.trycloudflare.com'), null);
  assert.deepEqual(parseAiCommand('/ai'), { kind: 'help' });
  assert.deepEqual(parseAiCommand('Stop'), { kind: 'stop' });
  assert.deepEqual(parseAiCommand(' /AI stop '), { kind: 'stop' });
  assert.deepEqual(parseAiCommand('/ai sửa lỗi OCR\nchi tiết'), { kind: 'run', prompt: 'sửa lỗi OCR\nchi tiết' });
});

test('long replies are split under the Zalo limit', () => {
  const chunks = splitForZalo('a'.repeat(4000), 1800);
  assert.equal(chunks.join(''), 'a'.repeat(4000));
  assert.ok(chunks.every(chunk => chunk.length <= 1800));
});

test('run sends prompt via stdin, replies with result and resumes the session', async () => {
  const calls = [];
  const sent = [];
  const ai = runner(calls, sent);
  await ai.handle('sửa lỗi "; rm -rf /', 'Hung');
  await ai.whenIdle();
  assert.equal(calls.length, 1);
  assert.ok(!calls[0].args.some(arg => arg.includes('rm -rf')));
  assert.ok(calls[0].prompt.includes('sửa lỗi "; rm -rf /'));
  assert.match(sent.at(-1), /Đã sửa file x\.py/);

  await ai.handle('tiếp tục', 'Hung');
  await ai.whenIdle();
  assert.deepEqual(calls[1].args.slice(-2), ['--resume', 'abc']);
  await ai.handle('new', 'Hung');
  await ai.handle('lần nữa', 'Hung');
  await ai.whenIdle();
  assert.ok(!calls[2].args.includes('--resume'));
});

test('the prompt asks for the content of the change only', async () => {
  const calls = [];
  const ai = runner(calls, []);
  await ai.handle('sửa lỗi OCR', 'Hung');
  await ai.whenIdle();
  const prompt = calls[0].prompt;
  assert.ok(prompt.includes('chỉ nêu NỘI DUNG THAY ĐỔI'));
  assert.ok(prompt.includes('Không kể quy trình'));
  assert.ok(!prompt.includes('phần nào Antigravity làm'));
  assert.ok(!prompt.includes('điều gì còn cần người xem lại'));
});

test('the bot ignores its own replies when they come back from Zalo', async () => {
  const calls = [];
  const sent = [];
  const ai = runner(calls, sent);
  await ai.handle('sửa lỗi', 'Hung');
  await ai.whenIdle();
  for (const text of [...sent]) await ai.handle(text, 'Hung');
  await ai.handle(sent.at(-1).replace('🤖 ', ''), 'Hung');
  await ai.whenIdle();
  assert.equal(calls.length, 1);
});

test('a photo the bot posted with its own mark is not a command', async () => {
  const calls = [];
  const ai = runner(calls, []);
  await ai.handle('🤖 Hóa đơn nháp để kiểm tra', 'Hung', ['/tmp/invoice.png']);
  await ai.whenIdle();
  assert.equal(calls.length, 0);
});

test('text followed by separate photo messages becomes one command', async () => {
  const calls = [];
  const ai = runner(calls, [], { gatherMs: 30 });
  await ai.handle('sửa lại như ảnh', 'Hung');
  await ai.handle('', 'Hung', ['C:\\uploads\\1.jpg']);
  await ai.handle('', 'Hung', ['C:\\uploads\\2.jpg']);
  await ai.whenIdle();
  assert.equal(calls.length, 1);
  assert.ok(calls[0].prompt.includes('sửa lại như ảnh'));
  assert.ok(calls[0].prompt.includes('C:\\uploads\\1.jpg') && calls[0].prompt.includes('C:\\uploads\\2.jpg'));
});

test('messages arriving during a run are queued and run next, not dropped', async () => {
  const calls = [];
  const sent = [];
  const ai = runner(calls, sent, { delayMs: 30 });
  await ai.handle('việc 1', 'A');
  await new Promise(resolve => setTimeout(resolve, 5));
  await ai.handle('việc 2', 'B');
  await ai.handle('', 'B', ['C:\\uploads\\3.jpg']);
  await ai.whenIdle();
  assert.equal(calls.length, 2);
  assert.ok(calls[1].prompt.includes('việc 2') && calls[1].prompt.includes('3.jpg'));
  assert.equal(sent.filter(text => text.includes('Đã nhận thêm')).length, 1);
});

test('stop cancels the running command and drops queued messages', async () => {
  const calls = [];
  const sent = [];
  const ai = runner(calls, sent, { delayMs: 30 });
  await ai.handle('việc 1', 'A');
  await new Promise(resolve => setTimeout(resolve, 5));
  await ai.handle('việc 2', 'A');
  await ai.handle('stop', 'A');
  await ai.whenIdle();
  assert.equal(calls.length, 1);
});

test('busy marker remains active until the final reply has been sent', async () => {
  const calls = [];
  const states = [];
  const replies = [];
  let busy = false;
  const ai = createAiRunner({
    project: '.', gatherMs: 0,
    spawnFn: fakeSpawn({ result: 'Đã hoàn tất', session_id: 'abc', is_error: false }, calls),
    onBusyChange: async value => { busy = value; states.push(value); },
    sendText: async text => { replies.push({ text, busy }); },
  });
  await ai.handle('sửa code', 'Hung');
  await ai.whenIdle();
  assert.deepEqual(states, [true, false]);
  assert.equal(replies.length, 2);
  assert.ok(replies.every(reply => reply.busy));
});

test('a failed attachment can cancel its sender pending text before execution', async () => {
  const calls = [];
  const ai = runner(calls, [], { gatherMs: 30 });
  await ai.handle('sửa theo file', 'Bich');
  assert.equal(ai.cancelPendingFrom('Bich'), 1);
  await ai.whenIdle();
  assert.equal(calls.length, 0);
});

test('long running commands send progress and stop updating after completion', async () => {
  const calls = [];
  const sent = [];
  const ai = runner(calls, sent, {
    delayMs: 35,
    progressFirstMs: 5,
    progressEveryMs: 10,
  });
  await ai.handle('sửa form', 'Hung');
  await ai.whenIdle();
  assert.ok(sent.some(text => text.includes('AI vẫn đang chạy')));
  assert.match(sent.at(-1), /Xong/);
  const count = sent.length;
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(sent.length, count);
});
