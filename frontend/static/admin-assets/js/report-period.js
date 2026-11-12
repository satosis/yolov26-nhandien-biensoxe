/* Report filter bars: choosing a period fills the date range, then the form is submitted. */
function applyReportPeriod(val, ids) {
  const year = Number(document.getElementById(ids.year)?.value || ids.defaultYear);
  const fromEl = document.getElementById(ids.from);
  const toEl = document.getElementById(ids.to);
  const pad = (n) => String(n).padStart(2, '0');
  const months = (first, last) => {
    fromEl.value = `${year}-${pad(first)}-01`;
    toEl.value = `${year}-${pad(last)}-${pad(new Date(year, last, 0).getDate())}`;
  };

  if (val === 'today') {
    fromEl.value = year + '-01-01';
    toEl.value = ids.today;
  } else if (val === 'all' || val === 'year') {
    months(1, 12);
  } else if (/^q[1-4]$/.test(val)) {
    const quarter = Number(val[1]);
    months(3 * quarter - 2, 3 * quarter);
  } else if (val.startsWith('m') && !isNaN(parseInt(val.substring(1)))) {
    const month = parseInt(val.substring(1));
    months(month, month);
  }
  if (val !== 'custom') {
    document.getElementById(ids.form).submit();
  }
}
