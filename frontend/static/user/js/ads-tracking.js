(function () {
  'use strict';

  var STORAGE_KEY = 'tsl_attribution_v1';
  var PARAMS = [
    'gclid', 'gbraid', 'wbraid', 'fbclid',
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'
  ];
  var TTL_MS = 30 * 24 * 60 * 60 * 1000; // 30 days

  // Trình duyệt chặn localStorage (chế độ riêng tư, webview): giữ tạm trong bộ nhớ của trang.
  var memoryStore = null;

  function getStored() {
    var raw = memoryStore;
    try {
      raw = window.localStorage.getItem(STORAGE_KEY) || memoryStore;
    } catch (e) {}
    if (!raw) return null;
    var parsed = JSON.parse(raw);
    return parsed && typeof parsed.ts === 'number' && Date.now() - parsed.ts < TTL_MS ? parsed : null;
  }

  function saveStored(data) {
    memoryStore = JSON.stringify(data);
    try {
      window.localStorage.setItem(STORAGE_KEY, memoryStore);
    } catch (e) {}
  }

  // 1. Capture attribution from URL or Referrer
  var search = new URLSearchParams(window.location.search);
  var foundParams = {};
  var hasParam = false;
  PARAMS.forEach(function (name) {
    var val = (search.get(name) || '').trim();
    if (val) {
      foundParams[name] = val;
      hasParam = true;
    }
  });

  var href = window.location.href.slice(0, 500);
  var ref = document.referrer.slice(0, 500);
  // Moving between pages of this site is not a traffic source, so a same-host referrer is ignored.
  if (ref && new URL(ref).hostname === window.location.hostname) {
    ref = '';
  }

  if (hasParam) {
    // New campaign/ad click: overwrites existing attribution
    saveStored(Object.assign({}, foundParams, { landing_url: href, referrer: ref, ts: Date.now() }));
  } else if (!getStored() && ref) {
    saveStored({ landing_url: href, referrer: ref, ts: Date.now() });
  }

  // 2. Global TSLTracking object
  window.TSLTracking = {
    attribution: function () {
      var current = getStored();
      if (!current) return {};
      var copy = Object.assign({}, current);
      delete copy.ts;
      return copy;
    },
    push: function (eventName, data) {
      window.dataLayer = window.dataLayer || [];
      window.dataLayer.push(Object.assign({ event: eventName }, data || {}));
      // Google tag (gtag.js) used directly, without Tag Manager: send the event and, when a conversion
      // label is configured for it, the Google Ads conversion.
      var gads = window.TSL_GADS;
      if (gads && typeof window.gtag === 'function') {
        window.gtag('event', eventName, data || {});
        var label = gads.labels && gads.labels[eventName];
        if (label && gads.tag) {
          var conversion = { send_to: gads.tag + '/' + label };
          if (data && data.event_id) conversion.transaction_id = data.event_id;
          window.gtag('event', 'conversion', conversion);
        }
      }
    }
  };

  // 3. Click Tracking (tel and zalo) with 1000ms deduplication
  var lastEvents = {};

  function track(eventName, linkUrl) {
    var key = eventName + ':' + linkUrl;
    var now = Date.now();
    if (lastEvents[key] && now - lastEvents[key] < 1000) return;
    lastEvents[key] = now;
    window.TSLTracking.push(eventName, { link_url: linkUrl, page_path: window.location.pathname });
    if (typeof window.fbq === 'function') {
      window.fbq('track', 'Contact');
    }
  }

  document.addEventListener(
    'click',
    function (e) {
      var telNode = e.target.closest('a[href^="tel:"]');
      if (telNode) {
        track('click_call', telNode.getAttribute('href'));
        return;
      }
      var zaloNode = e.target.closest('[data-track="zalo"], a[href*="zalo.me"]');
      if (zaloNode) {
        track('click_zalo', zaloNode.getAttribute('href') || zaloNode.getAttribute('data-url') || 'zalo');
      }
    },
    true // capture phase
  );
})();
