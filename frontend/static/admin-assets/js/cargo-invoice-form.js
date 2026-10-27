function canonicalizeInvoiceLocation(value) {
  return value ? String(value).trim() : '';
}

function parseInvoiceDescription(raw, serviceNames = [], cargoNames = []) {
  const normRaw = String(raw || '').normalize('NFC');
  const source = normRaw.replace(/\s+/g, ' ').trim();
  const fold = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/đ/gi, 'd').toLocaleLowerCase('vi-VN').replace(/\s+/g, ' ').trim();
  const foldExact = s => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/gi, 'd').toLowerCase();
  const taxRegex = /^\s*(?:\([^\)]*\)|(?:[+(,]|\b(?:chua|da|khong|co|bao\s+gom|gom|truoc|sau|\+)\b|\s+)*(?:thue|vat|gtgt)\b(?:\s*\))?)/i;
  const moneyPattern = /(?:^|[\s,.;])(\d{1,3}(?:[., ]\d{3})+|\d+(?:[.,]\d+)?)\s*(triệu|tr|đồng|vnđ|vnd|đ)(?=\s|[.,;!?]|$)/gi;
  let price = null;
  for (const match of source.matchAll(moneyPattern)) {
    const after = source.slice(match.index + match[0].length);
    const foldedAfter = foldExact(after);
    const taxMatch = taxRegex.exec(foldedAfter);
    const taxText = taxMatch ? taxMatch[0] : '';
    const taxLength = taxMatch ? taxMatch[0].length : 0;
    const unit = fold(match[2]);
    const digits = match[1].replace(/\s/g, '');
    const number = unit === 'tr' || unit === 'trieu'
      ? Number(digits.replace(',', '.')) * 1000000
      : Number(digits.replace(/[.,]/g, ''));
    if (Number.isSafeInteger(number) && number > 0) {
      const includesVat = taxText
        ? (/\b(?:da|co|bao gom|sau)\b/.test(taxText) && !/\b(?:chua|khong|truoc)\b/.test(taxText))
        : false;
      price = {
        amount: includesVat ? null : number,
        start: match.index,
        end: match.index + match[0].length + taxLength,
        includesVat
      };
    }
  }
  let work = source;
  if (price) {
    const beforePrice = source.slice(0, price.start).replace(/[\s,.;:]+$/, '').trim();
    if (/\btừ\s+/i.test(beforePrice)) {
      work = beforePrice;
    } else {
      work = (source.slice(0, price.start) + ' ' + source.slice(price.end)).replace(/[\s,.;:]+$/, '').trim();
    }
  }
  const routeStart = /\btừ\s+/i.exec(work);
  const routeParts = routeStart
    ? work.slice(routeStart.index + routeStart[0].length)
      .split(/\s+(?:đi\s+qua|di\s+qua|rồi\s+đến|roi\s+den|đi|di|đến|den|tới|toi|qua|ghé|ghe)\s+/i)
      .map(part => part.trim().replace(/^[,.;\s]+|[,.;\s]+$/g, ''))
    : [];
  if (routeParts.length < 2 || routeParts.some(part => !part)) {
    return {description: work, origin: '', destination: '', via_points: [], amount: price?.amount ?? null,
            includesVat: price?.includesVat || false, service: '', cargo: ''};
  }

  const place = value => value.replace(/\s*,\s*/g, ', ').split(/(\s+|,\s*)/).map((part, index) => {
    if (!part.trim() || part.startsWith(',')) return part;
    const lower = part.toLocaleLowerCase('vi-VN');
    if (index && ['công', 'nghiệp', 'xã', 'huyện', 'tỉnh', 'phường', 'thị', 'trấn'].includes(lower)) return lower;
    return lower.charAt(0).toLocaleUpperCase('vi-VN') + lower.slice(1);
  }).join('').trim();
  const routePlaces = routeParts.map(part => canonicalizeInvoiceLocation(place(part)));
  const origin = routePlaces[0];
  const destination = routePlaces.at(-1);
  const viaPoints = routePlaces.slice(1, -1);
  const prefix = work.slice(0, routeStart.index).trim();
  const foldedPrefix = fold(prefix);
  const hasCrane = /\bcau\b/.test(foldedPrefix);
  const hasTransport = /\bvan chuyen\b/.test(foldedPrefix);
  const service = serviceNames.find(name => {
    const value = fold(name);
    return hasCrane && hasTransport && /\bcau\b/.test(value) && /\bvan chuyen\b/.test(value);
  }) || serviceNames.find(name => hasTransport && !hasCrane && /\bvan chuyen\b/.test(fold(name)) &&
                                   !/\bcau\b/.test(fold(name))) || '';
  const cargo = [...cargoNames].sort((a, b) => b.length - a.length)
    .find(name => fold(name) && foldedPrefix.includes(fold(name))) || '';
  const transportWord = /(?:vận\s+chuyển|van\s+chuyen)/i.exec(prefix);
  const rawCargo = transportWord ? prefix.slice(transportWord.index + transportWord[0].length).trim() : '';
  const workName = service ? service + (cargo || rawCargo ? ' ' + (cargo || rawCargo).toLocaleLowerCase('vi-VN') : '')
    : prefix;
  const numMatch = /(?:(?:hóa\s+đơn|hđ)\s*số|số\s*(?:hóa\s+đơn|hđ))\s*[:.]?\s*(\d{1,10})/i.exec(normRaw);
  const serialMatch = /(?:ký\s+hiệu|serial|mẫu\s+số)\s*[:.]?\s*([a-zA-Z0-9]{6,8})/i.exec(normRaw);
  const invoiceNumber = numMatch ? String(Number(numMatch[1])).padStart(8, '0') : null;
  const invoiceSerial = serialMatch ? serialMatch[1].toUpperCase() : null;

  return {
    description: `${workName} từ ${origin}${viaPoints.map(point => ` qua ${point}`).join('')} đến ${destination}`.trim(),
    origin, destination, via_points: viaPoints, amount: price?.amount ?? null,
    includesVat: price?.includesVat || false, service, cargo,
    invoice_number: invoiceNumber, invoice_serial: invoiceSerial
  };
}

function invoiceEditor(initialLines, vehicles, defaultVehicleId, initialPaymentStatus, initialPaymentMethod,
                       serviceNames = [], cargoNames = []) {
  const fallbackVehicleId = String(defaultVehicleId || (vehicles && vehicles.length > 0 ? vehicles[0].id : ''));
  const byId = new Map(vehicles.map(vehicle => [String(vehicle.id), vehicle]));
  const locationRequests = new Map();
  const officialLocations = (origin, destination, viaPoints = []) => {
    const key = JSON.stringify([origin, destination, viaPoints]);
    if (!locationRequests.has(key)) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      const request = fetch('/admin/vehicles/api/normalize-invoice-locations', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({origin, destination, via_points: viaPoints}), signal: controller.signal
      }).then(async response => {
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.message || 'Không đọc được danh mục địa danh.');
        return result;
      }).finally(() => clearTimeout(timeout));
      locationRequests.set(key, request);
      request.catch(() => locationRequests.delete(key));
    }
    return locationRequests.get(key);
  };
  let nextKey = 0;
  const blankLine = (vehicleId = '') => ({
    _key: ++nextKey, vehicle_id: String(vehicleId || fallbackVehicleId), cargo_description: '',
    cargo_weight_tons: '', description: '', origin: '', destination: '', via_points: [], amount: '',
    distance_km: '', fuel_liters: '', fuel_price_per_liter: '',
    calculating: false, distanceError: '', fuelError: '', distanceRequestId: 0, normalizationRequestId: 0
  });
  const asMoneyNumber = value => Number(String(value || '').replace(/\D/g, '')) || 0;
  const initialMoney = value => {
    const raw = String(value || '').trim();
    if (!raw) return '';
    const number = /^\d{1,3}(?:\.\d{3})+$/.test(raw)
      ? Number(raw.replace(/\./g, '')) : Number(raw.replace(',', '.'));
    return Number.isFinite(number) ? Math.round(number).toLocaleString('vi-VN') : '';
  };
  return {
    lines: [],
    serviceName: '',
    defaultFuelPrice: '',
    priceRequestId: 0,
    paymentStatus: initialPaymentStatus || 'debt',
    paymentMethod: initialPaymentMethod || 'CK',
    isCraneByShift() {
      const name = String(this.serviceName || '').trim().toLocaleLowerCase('vi-VN');
      return name === 'cước cẩu theo ca'
        || name === 'cước cẩu lắp dựng kết cấu nhà xưởng';
    },
    clearRouteFields(line) {
      line.distanceRequestId += 1;
      line._distanceController?.abort();
      line.calculating = false;
      line.distanceError = '';
      line.origin = '';
      line.destination = '';
      line.via_points = [];
      line.distance_km = '';
      line._autoDistanceValue = null;
      line.fuel_liters = '';
      line.fuel_price_per_liter = '';
      line._autoFuelValue = null;
      line.cargo_description = '';
      line.cargo_weight_tons = '';
    },
    setService(value) {
      this.serviceName = String(value || '').trim();
    },
    formatMoney(value) { return Number(value || 0).toLocaleString('vi-VN') + ' đ'; },
    init() {
      this.serviceName = document.getElementById('invoice-service')?.value || '';
      const defaultForNew = fallbackVehicleId;
      this.lines = (initialLines.length ? initialLines : [blankLine(defaultForNew)]).map(line => ({
        ...blankLine(line.vehicle_id || (initialLines.length ? '' : defaultForNew)), ...line, _key: ++nextKey,
        vehicle_id: String(line.vehicle_id || (initialLines.length ? '' : defaultForNew)),
        via_points: Array.isArray(line.via_points) ? [...line.via_points] : [],
        amount: initialMoney(line.amount),
        fuel_price_per_liter: initialMoney(line.fuel_price_per_liter),
        calculating: false, distanceError: '', fuelError: '', distanceRequestId: 0, normalizationRequestId: 0
      }));
      this.lines.forEach(line => {
        if (line.distance_km !== '' && !line.fuel_liters) this.autoFuel(line);
      });
      const debt = document.getElementById('invoice-debt');
      const initialDebt = debt ? initialMoney(debt.value) : '';
      this.debtManuallyEdited = Boolean(initialDebt && (!this.subtotal() || initialDebt !== this.gross().toLocaleString('vi-VN')));
      if (debt) {
        debt.value = initialDebt;
        if (!this.debtManuallyEdited && this.paymentStatus === 'debt' && this.subtotal()) {
          debt.value = this.gross().toLocaleString('vi-VN');
        }
        debt.addEventListener('input', event => {
          this.debtManuallyEdited = true;
          event.target.value = this.moneyInput(event);
        });
      }
      if (typeof document !== 'undefined' && document.querySelectorAll) {
        document.querySelectorAll('button[type="submit"][form="invoice-form"], #invoice-form button[type="submit"]').forEach(btn => {
          btn.addEventListener('click', () => {
            const debtEl = document.getElementById('invoice-debt');
            if (this.paymentStatus === 'debt' && debtEl && !debtEl.value.trim() && this.subtotal()) {
              debtEl.value = this.gross().toLocaleString('vi-VN');
            }
          });
        });
      }
      this.refreshDebtPlaceholder();
      this.fetchFuelPrice();
    },
    addLine() {
      if (this.lines.length >= 20) return;
      if (this.lines.length) {
        this.duplicateLine(this.lines.length - 1);
      } else {
        const line = blankLine(fallbackVehicleId);
        line.fuel_price_per_liter = this.defaultFuelPrice;
        this.lines.push(line);
      }
    },
    duplicateLine(index) {
      if (this.lines.length >= 20 || !this.lines[index]) return;
      const source = this.lines[index];
      const copy = blankLine(source.vehicle_id);
      for (const field of ['cargo_description', 'cargo_weight_tons', 'description', 'origin',
                           'destination', 'amount', 'distance_km', 'fuel_liters', 'fuel_price_per_liter']) {
        copy[field] = source[field];
      }
      copy.via_points = [...source.via_points];
      this.lines.splice(index + 1, 0, copy);
      this.refreshDebtPlaceholder();
    },
    removeLine(index) {
      if (this.lines.length > 1) this.lines.splice(index, 1);
      this.refreshDebtPlaceholder();
    },
    addWaypoint(line) {
      if (line.via_points.length < 8) line.via_points.push('');
    },
    removeWaypoint(line, index) {
      line.via_points.splice(index, 1);
      this.routeChanged(line);
    },
    routeChanged(line) {
      line.distanceRequestId += 1;
      line._distanceController?.abort();
      line.calculating = false;
      line.distance_km = '';
      line._autoDistanceValue = null;
      line.fuel_liters = '';
      line._autoFuelValue = null;
      const parsed = parseInvoiceDescription(line.description, serviceNames, cargoNames);
      if (parsed.origin && parsed.destination) {
        const suffix = ` từ ${parsed.origin}${parsed.via_points.map(point => ` qua ${point}`).join('')} đến ${parsed.destination}`;
        const prefix = parsed.description.endsWith(suffix)
          ? parsed.description.slice(0, -suffix.length) : parsed.description;
        const via = line.via_points.map(point => point.trim()).filter(Boolean);
        line.description = `${prefix} từ ${line.origin}${via.map(point => ` qua ${point}`).join('')} đến ${line.destination}`;
      }
      if (line.via_points.some(point => !point.trim())) return;
      if (parsed.origin && parsed.destination) this.normalizeLineDescription(line);
      else this.calculateDistance(line, true);
    },
    async normalizeLineDescription(line, notify = false) {
      const originalDescription = line.description;
      const originalVia = JSON.stringify(line.via_points || []);
      const requestId = line.normalizationRequestId = (line.normalizationRequestId || 0) + 1;
      const parsed = parseInvoiceDescription(line.description, serviceNames, cargoNames);
      if (!parsed.origin && parsed.amount === null) {
        if (notify) window.dispatchEvent(new CustomEvent('showToast', {
          detail: {msg: 'Nhập tuyến đường hoặc cước để điền thông tin.', type: 'warning'}
        }));
        return;
      }
      let locations = null;
      let locationLookupFailed = false;
      const lookupOrigin = parsed.origin || line.origin || '';
      const lookupDest = parsed.destination || line.destination || '';
      const lookupVia = (parsed.via_points && parsed.via_points.length) ? parsed.via_points : (line.via_points || []);
      if (lookupOrigin || lookupDest) {
        try {
          locations = await officialLocations(lookupOrigin, lookupDest, lookupVia);
        } catch (_) {
          locationLookupFailed = true;
        }
      }
      if (line.normalizationRequestId !== requestId || line.description !== originalDescription
          || JSON.stringify(line.via_points || []) !== originalVia) return;
      const origin = locations?.origin || lookupOrigin;
      const destination = locations?.destination || lookupDest;
      const viaPoints = locations?.via_points || lookupVia;
      line._autofill ||= {};
      const setLineField = (field, value) => {
        const knownLocationCorrection = (field === 'origin' || field === 'destination')
          && line[field] && line[field] !== value;
        const amountOverride = field === 'amount' && (line[field] === '0' || line[field] === '0 đ' || notify);
        if (value && (!line[field] || line[field] === line._autofill[field] || knownLocationCorrection || amountOverride)) {
          const changed = line[field] !== value;
          line[field] = value;
          line._autofill[field] = value;
          return changed;
        }
        return false;
      };
      const originChanged = setLineField('origin', origin);
      const destinationChanged = setLineField('destination', destination);
      const priorVia = Array.isArray(line.via_points) ? line.via_points : [];
      const viaChanged = JSON.stringify(priorVia) !== JSON.stringify(viaPoints);
      line.via_points = [...viaPoints];
      if (parsed.amount !== null) {
        setLineField('amount', parsed.amount.toLocaleString('vi-VN'));
        this.refreshDebtPlaceholder?.();
      }
      if (parsed.cargo && setLineField('cargo_description', parsed.cargo)) {
        this.$nextTick(() => {
          const select = document.querySelector(`[data-invoice-line-key="${line._key}"] .invoice-cargo-select`);
          if (select?.tomselect) select.tomselect.setValue(line.cargo_description, true);
        });
      }
      if (parsed.service) {
        const select = document.getElementById('invoice-service');
        if (select && !select.value) {
          if (select.tomselect) select.tomselect.setValue(parsed.service);
          else select.value = parsed.service;
        }
      }
      if (parsed.invoice_number) {
        const numEl = document.getElementById('invoice-number');
        if (numEl && (!numEl.value || notify)) numEl.value = parsed.invoice_number;
      }
      if (parsed.invoice_serial) {
        const serEl = document.getElementById('invoice-serial');
        if (serEl && (!serEl.value || notify)) serEl.value = parsed.invoice_serial;
      }
      if (parsed.origin && parsed.destination) {
        const routeSuffix = ` từ ${parsed.origin}${parsed.via_points.map(point => ` qua ${point}`).join('')} đến ${parsed.destination}`;
        const workName = parsed.description.endsWith(routeSuffix)
          ? parsed.description.slice(0, -routeSuffix.length) : parsed.description;
        line.description = `${workName} từ ${origin}${viaPoints.map(point => ` qua ${point}`).join('')} đến ${destination}`;
      }
      if (originChanged || destinationChanged || viaChanged) {
        line.distance_km = '';
        line._autoDistanceValue = null;
        line.fuel_liters = '';
        line._autoFuelValue = null;
        this.calculateDistance(line, true);
      } else if (parsed.origin && parsed.destination && !line.distance_km) {
        this.calculateDistance(line, true);
      }
      this.refreshDebtPlaceholder();
      if (notify) window.dispatchEvent(new CustomEvent('showToast', {
        detail: {msg: locationLookupFailed ? 'Không kiểm tra được danh mục địa danh; giữ nguyên nơi chưa xác thực.' :
          parsed.includesVat ? 'Giá đã gồm VAT; nhập cước trước VAT để lưu.' :
          'Đã điền thông tin xác định được. Kiểm tra lại trước khi lưu.',
          type: locationLookupFailed || parsed.includesVat ? 'warning' : 'success'}
      }));
    },
    moneyInput(event) {
      const digits = event.target.value.replace(/\D/g, '');
      const formatted = digits ? Number(digits).toLocaleString('vi-VN') : '';
      event.target.value = formatted;
      return formatted;
    },
    subtotal() { return this.lines.reduce((sum, line) => sum + asMoneyNumber(line.amount), 0); },
    gross() {
      const vat = Number(document.getElementById('invoice-vat')?.value || 0);
      return Math.round(this.subtotal() * (1 + (Number.isFinite(vat) ? vat : 0) / 100));
    },
    setPaymentStatus(value) {
      this.paymentStatus = value;
      if (value === 'debt') {
        const debt = document.getElementById('invoice-debt');
        if (debt && (!this.debtManuallyEdited || !debt.value.trim())) {
          debt.value = this.subtotal() ? this.gross().toLocaleString('vi-VN') : '';
        }
      }
      this.refreshDebtPlaceholder();
    },
    resetDebtToInvoice() {
      this.debtManuallyEdited = false;
      const debt = document.getElementById('invoice-debt');
      if (debt) debt.value = this.subtotal() ? this.gross().toLocaleString('vi-VN') : '';
      this.refreshDebtPlaceholder();
    },
    refreshDebtPlaceholder() {
      const debt = document.getElementById('invoice-debt');
      if (debt) {
        debt.placeholder = this.formatMoney(this.gross());
        if (!this.debtManuallyEdited && this.paymentStatus === 'debt') {
          debt.value = this.subtotal() ? this.gross().toLocaleString('vi-VN') : '';
        }
      }
    },
    fuelCost(line) {
      const liters = Number(line.fuel_liters || 0);
      const price = asMoneyNumber(line.fuel_price_per_liter);
      return liters && price ? this.formatMoney(liters * price) : '—';
    },
    profit(line) {
      const amount = asMoneyNumber(line.amount);
      const liters = Number(line.fuel_liters || 0);
      const price = asMoneyNumber(line.fuel_price_per_liter);
      return amount && liters && price ? this.formatMoney(amount - Math.round(liters * price)) : '—';
    },
    mapsUrl(line) {
      const origin = String(line.origin || '').trim();
      const destination = String(line.destination || '').trim();
      if (!origin || !destination) return '';
      const via = (line.via_points || []).map(point => point.trim()).filter(Boolean);
      return 'https://www.google.com/maps/dir/?api=1&origin='
        + encodeURIComponent(origin) + '&destination=' + encodeURIComponent(destination)
        + (via.length ? '&waypoints=' + encodeURIComponent(via.join('|')) : '');
    },
    autoFuel(line) {
      const rate = Number(byId.get(String(line.vehicle_id))?.rate || 0);
      const distance = Number(line.distance_km || 0);
      line.fuelError = '';
      if (line.distance_km === '') {
        if (line.fuel_liters === line._autoFuelValue) line.fuel_liters = '';
        return;
      }
      if (!line.vehicle_id || !(rate > 0)) {
        if (line.fuel_liters === line._autoFuelValue) line.fuel_liters = '';
        if (!line.fuel_liters) line.fuelError = 'Xe chưa có định mức tiêu hao dầu; nhập số lít dầu thủ công.';
        return;
      }
      if (Number.isFinite(distance) && distance >= 0) {
        line.fuel_liters = (distance * rate / 100).toFixed(2);
        line._autoFuelValue = line.fuel_liters;
      }
    },
    setManualDistance(line) {
      line.distanceRequestId += 1;
      line._distanceController?.abort();
      line.calculating = false;
      line.distanceError = '';
      line._autoDistanceValue = null;
      this.autoFuel(line);
    },
    async calculateDistance(line, silent = false) {
      const origin = String(line.origin || '').trim();
      const destination = String(line.destination || '').trim();
      const via = (line.via_points || []).map(point => point.trim()).filter(Boolean);
      const routeKey = JSON.stringify([origin, destination, via]);
      line.distanceError = '';
      if (!origin || !destination) {
        if (!silent) line.distanceError = 'Nhập điểm lấy và điểm giao hàng.';
        return;
      }
      line._distanceController?.abort();
      const requestId = ++line.distanceRequestId;
      const controller = new AbortController();
      line._distanceController = controller;
      const timeout = setTimeout(() => controller.abort(), 19000);
      if (line.distance_km === line._autoDistanceValue) {
        line.distance_km = '';
        this.autoFuel(line);
      }
      line.calculating = true;
      try {
        const url = '/admin/vehicles/api/calculate-distance-by-address?origin='
          + encodeURIComponent(origin) + '&destination=' + encodeURIComponent(destination)
          + '&waypoints=' + encodeURIComponent(JSON.stringify(via));
        const response = await fetch(url, {credentials: 'same-origin', signal: controller.signal});
        const result = await response.json();
        if (!response.ok || !result?.ok || result.distance_km == null
            || !Number.isFinite(Number(result.distance_km)) || Number(result.distance_km) < 0) {
          throw new Error(result?.message || 'Không tính được quãng đường bằng Google Maps.');
        }
        if (line.distanceRequestId !== requestId || !this.lines.includes(line)
            || JSON.stringify([line.origin.trim(), line.destination.trim(),
              (line.via_points || []).map(point => point.trim()).filter(Boolean)]) !== routeKey) return;
        line.distance_km = String(result.distance_km);
        line._autoDistanceValue = line.distance_km;
        this.autoFuel(line);
      } catch (error) {
        if (line.distanceRequestId === requestId) {
          line.distanceError = error.name === 'AbortError'
            ? 'Google Maps phản hồi quá lâu. Thử lại hoặc nhập số km thủ công.'
            : (error.message || 'Không tính được quãng đường bằng Google Maps.');
        }
      } finally {
        clearTimeout(timeout);
        if (line.distanceRequestId === requestId) {
          line.calculating = false;
          line._distanceController = null;
        }
      }
    },
    async fetchFuelPrice() {
      const date = document.getElementById('invoice-date')?.value;
      if (!date) return;
      const requestId = ++this.priceRequestId;
      try {
        const response = await fetch('/admin/vehicles/api/fuel-price?target_date=' + encodeURIComponent(date),
          {credentials: 'same-origin'});
        const result = await response.json();
        if (requestId !== this.priceRequestId || document.getElementById('invoice-date')?.value !== date) return;
        if (response.ok && result.ok && Number(result.price) > 0) {
          const formatted = Math.round(Number(result.price)).toLocaleString('vi-VN');
          const previous = this.defaultFuelPrice;
          this.defaultFuelPrice = formatted;
          this.lines.forEach(line => {
            if (!line.fuel_price_per_liter || line.fuel_price_per_liter === previous) {
              line.fuel_price_per_liter = formatted;
            }
          });
        }
      } catch (_) { /* Keep the entered price when the lookup is unavailable. */ }
    },
    prepareSubmit(event) {
      const debt = document.getElementById('invoice-debt');
      if (this.paymentStatus === 'debt' && debt && !debt.value.trim()) {
        debt.value = this.subtotal() ? this.gross().toLocaleString('vi-VN') : '0';
      }
      const numInput = document.getElementById('invoice-number');
      const serInput = document.getElementById('invoice-serial');
      if (numInput && !numInput.value.trim() && window._defaultInvoiceNumber) {
        numInput.value = window._defaultInvoiceNumber;
      }
      if (serInput && !serInput.value.trim() && window._defaultInvoiceSerial) {
        serInput.value = window._defaultInvoiceSerial;
      }
      this.$refs.linesPayload.value = JSON.stringify(this.lines.map(line => ({
        vehicle_id: line.vehicle_id,
        cargo_description: line.cargo_description,
        cargo_weight_tons: line.cargo_weight_tons,
        description: line.description,
        origin: line.origin,
        destination: line.destination,
        via_points: (line.via_points || []).map(point => point.trim()).filter(Boolean),
        amount: line.amount,
        distance_km: line.distance_km,
        fuel_liters: line.fuel_liters,
        fuel_price_per_liter: line.fuel_price_per_liter
      })));
    }
  };
}

if (typeof module !== 'undefined') module.exports = {parseInvoiceDescription, invoiceEditor};

if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', () => {
  // Auto-fill next invoice number & serial if fields are empty
  const numInput = document.getElementById('invoice-number');
  const serInput = document.getElementById('invoice-serial');
  if (numInput && (!numInput.value || !numInput.value.trim() || !serInput?.value?.trim())) {
    fetch('/admin/vehicles/api/next-invoice-number', {credentials: 'same-origin'})
      .then(r => r.json())
      .then(res => {
        if (res && res.ok) {
          if (res.invoice_number) window._defaultInvoiceNumber = res.invoice_number;
          if (res.invoice_serial) window._defaultInvoiceSerial = res.invoice_serial;
          if (numInput && !numInput.value.trim() && res.invoice_number) {
            numInput.value = res.invoice_number;
          }
          if (serInput && !serInput.value.trim() && res.invoice_serial) {
            serInput.value = res.invoice_serial;
          }
        }
      })
      .catch(() => {});
  }

  const companySelect = document.getElementById('invoice-company');
  if (!companySelect || companySelect.tomselect || typeof TomSelect === 'undefined') return;

  /** Normalize a raw company address for use as an invoice location.
   *  - Strips trailing ", Việt Nam" / ", Vietnam" (Google Maps artifact)
   *  - Strips Google Plus Code prefix, e.g. "G3XV+32W " at the start
   *  - Removes duplicate whitespace and stray leading/trailing commas
   */
  function normalizeCompanyAddress(raw) {
    if (!raw) return '';
    let s = String(raw).trim();
    // Remove trailing country name (added by Google Maps API)
    // Must match literal "Việt Nam" (ệ ≠ ê), "Viet Nam", or "Vietnam"
    s = s.replace(/,\s*(Việt\s*Nam|Viet\s*Nam|Vietnam)\s*$/i, '').trim();
    // Remove Google Plus Code prefix like "G3XV+32W " or "ABCD+12 "
    s = s.replace(/^[A-Z0-9]{4,8}\+[A-Z0-9]{2,4}\s*/i, '').trim();
    // Normalize multiple spaces / stray commas
    s = s.replace(/,\s*,/g, ',').replace(/\s{2,}/g, ' ').replace(/^[,\s]+|[,\s]+$/g, '').trim();
    return s;
  }

  /** Fill empty "Điểm lấy hàng" fields with the selected company's address, normalized via DB. */
  async function fillOriginFromCompany(companyId) {
    if (!companyId) return;
    const opt = companySelect.querySelector(`option[value="${CSS.escape(String(companyId))}"]`);
    const rawAddress = (opt && opt.dataset.address) ? opt.dataset.address.trim() : '';
    if (!rawAddress) return;
    const clientClean = normalizeCompanyAddress(rawAddress);
    if (!clientClean) return;
    // Alpine.js component root — find the x-data element wrapping the form
    const alpineRoot = companySelect.closest('[x-data]');
    if (!alpineRoot || !alpineRoot._x_dataStack) return;
    const data = alpineRoot._x_dataStack[0];
    if (!data || !Array.isArray(data.lines)) return;
    data.lines.forEach(line => {
      if (!line.origin || !line.origin.trim()) {
        line.origin = clientClean;
      }
    });

    // Query backend to normalize address against database administrative tables (tỉnh, xã)
    try {
      const resp = await fetch('/admin/vehicles/api/normalize-invoice-locations', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({origin: rawAddress, destination: '', via_points: []})
      });
      if (resp.ok) {
        const json = await resp.json();
        if (json.ok && json.origin) {
          const norm = json.origin;
          data.lines.forEach(line => {
            if (line.origin === clientClean || !line.origin || !line.origin.trim()) {
              line.origin = norm;
              if (line.destination && data.calculateDistance) {
                data.calculateDistance(line, true);
              }
            }
          });
        }
      }
    } catch (_) {}
  }

  const ts = new TomSelect(companySelect, {
    create: false,
    maxItems: 1,
    searchField: ['text'],
    placeholder: 'Tìm theo tên hoặc mã số thuế...',
    onChange(value) {
      fillOriginFromCompany(value);
    }
  });

  // Pre-fill on page load if a company is already selected
  if (companySelect.value) {
    // Wait a tick for Alpine to finish initialising
    setTimeout(() => fillOriginFromCompany(companySelect.value), 60);
  }
});

