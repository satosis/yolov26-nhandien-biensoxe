import assert from 'node:assert/strict';
import test from 'node:test';
import { sendPayrollQr } from './qr_sender.js';

test('old bridge requests cannot send salary QR messages', async () => {
  let sent = false;
  const api = { sendMessage: async () => { sent = true; } };
  await assert.rejects(
    sendPayrollQr(api, 'group-id', 1, {
      qr_url: 'https://img.vietqr.io/image/ICB-0867416976-compact2.png?amount=3000000',
      caption: 'Salary advance',
    }),
    error => error.stage === 'disabled',
  );
  assert.equal(sent, false);
});
