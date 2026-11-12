/* Small input helpers used by several admin pages. */
function formatCurrencyInput(el) {
  if (!el) return;
  const digits = el.value.replace(/\D/g, '');
  el.value = digits ? new Intl.NumberFormat('vi-VN').format(BigInt(digits)) : '';
}

function _todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
