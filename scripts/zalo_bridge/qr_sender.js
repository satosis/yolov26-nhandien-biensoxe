// Older bridge processes load this module for every salary QR request.
// Keep this guard so code updates disable sending without restarting them.
export async function sendPayrollQr() {
  const error = new Error('Đã tắt gửi mã QR tạm ứng lên Zalo');
  error.stage = 'disabled';
  throw error;
}
