(function () {
  'use strict';

  var STORAGE_KEY = 'tsl_attribution_v1';
  var PARAMS = [
    'gclid', 'gbraid', 'wbraid', 'fbclid',
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'
  ];
  var TTL_MS = 30 * 24 * 60 * 60 * 1000; // 30 days

  var memoryStore = null;

  function safeGetStored() {
    try {
      var raw = null;
      try {
        if (typeof window !== 'undefined' && window.localStorage) {
          raw = window.localStorage.getItem(STORAGE_KEY);
        }
      } catch (e) {
        raw = memoryStore;
      }
      if (!raw && memoryStore) {
        raw = memoryStore;
      }
      if (!raw) return null;

      var parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object') {
        var now = Date.now();
        if (typeof parsed.ts === 'number' && (now - parsed.ts) < TTL_MS) {
          return parsed;
        }
      }
    } catch (e) {}
    return null;
  }

  function safeSaveStored(data) {
    try {
      var jsonStr = JSON.stringify(data);
      memoryStore = jsonStr;
      try {
        if (typeof window !== 'undefined' && window.localStorage) {
          window.localStorage.setItem(STORAGE_KEY, jsonStr);
        }
      } catch (e) {}
    } catch (e) {}
  }

  // 1. Capture attribution from URL or Referrer
  try {
    if (typeof window !== 'undefined' && window.location) {
      var searchStr = window.location.search || '';
      var foundParams = {};
      var hasParam = false;

      // Extract search params safely
      if (typeof URLSearchParams !== 'undefined') {
        var sp = new URLSearchParams(searchStr);
        for (var i = 0; i < PARAMS.length; i++) {
          var p = PARAMS[i];
          var val = sp.get(p);
          if (val !== null && val.trim() !== '') {
            foundParams[p] = val.trim();
            hasParam = true;
          }
        }
      } else {
        // Fallback search param parser
        for (var j = 0; j < PARAMS.length; j++) {
          var paramName = PARAMS[j];
          var match = new RegExp('[?&]' + paramName + '=([^&#]*)').exec(searchStr);
          if (match && match[1]) {
            foundParams[paramName] = decodeURIComponent(match[1].replace(/\+/g, ' ')).trim();
            hasParam = true;
          }
        }
      }

      var href = (window.location.href || '').slice(0, 500);
      var ref = (typeof document !== 'undefined' && document.referrer ? document.referrer : '').slice(0, 500);
      // Moving between pages of this site is not a traffic source, so a same-host referrer is ignored.
      try {
        if (ref && window.location.hostname && new URL(ref).hostname === window.location.hostname) {
          ref = '';
        }
      } catch (e) {}

      if (hasParam) {
        // New campaign/ad click: overwrites existing attribution
        var newRecord = Object.assign({}, foundParams, {
          landing_url: href,
          referrer: ref,
          ts: Date.now()
        });
        safeSaveStored(newRecord);
      } else {
        // No ad params: check if we have existing valid record
        var existing = safeGetStored();
        if (!existing && ref) {
          safeSaveStored({
            landing_url: href,
            referrer: ref,
            ts: Date.now()
          });
        }
      }
    }
  } catch (e) {}

  // 2. Global TSLTracking object
  try {
    if (typeof window !== 'undefined') {
      window.TSLTracking = {
        attribution: function () {
          try {
            var current = safeGetStored();
            if (!current) return {};
            var copy = Object.assign({}, current);
            delete copy.ts;
            return copy;
          } catch (e) {
            return {};
          }
        },
        push: function (eventName, data) {
          try {
            window.dataLayer = window.dataLayer || [];
            var payload = Object.assign({ event: eventName }, data || {});
            window.dataLayer.push(payload);
          } catch (e) {}
          // Google tag (gtag.js) used directly, without Tag Manager: send the event and, when a conversion
          // label is configured for it, the Google Ads conversion.
          try {
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
          } catch (e) {}
        }
      };
    }
  } catch (e) {}

  // 3. Click Tracking (tel and zalo) with 1000ms deduplication
  var lastEvents = {};

  function shouldTrigger(eventName, linkUrl) {
    try {
      var key = eventName + ':' + (linkUrl || '');
      var now = Date.now();
      if (lastEvents[key] && (now - lastEvents[key]) < 1000) {
        return false;
      }
      lastEvents[key] = now;
      return true;
    } catch (e) {
      return true;
    }
  }

  function findClosest(el, predicate) {
    var curr = el;
    while (curr && curr !== document && (curr.nodeType === 1 || curr.tagName)) {
      if (predicate(curr)) return curr;
      curr = curr.parentNode || curr.parentElement;
    }
    return null;
  }

  try {
    if (typeof document !== 'undefined' && document.addEventListener) {
      document.addEventListener(
        'click',
        function (e) {
          try {
            var target = e.target;
            if (!target) return;

            // Check tel link
            var telNode = findClosest(target, function (node) {
              var href = (node.getAttribute && node.getAttribute('href')) || '';
              return (node.tagName === 'A' || node.nodeName === 'A') && href.indexOf('tel:') === 0;
            });

            if (telNode) {
              var telHref = (telNode.getAttribute && telNode.getAttribute('href')) || '';
              if (shouldTrigger('click_call', telHref)) {
                if (window.TSLTracking && window.TSLTracking.push) {
                  window.TSLTracking.push('click_call', {
                    link_url: telHref,
                    page_path: (window.location && window.location.pathname) || ''
                  });
                }
                if (typeof window.fbq === 'function') {
                  window.fbq('track', 'Contact');
                }
              }
              return;
            }

            // Check Zalo link or data-track="zalo"
            var zaloNode = findClosest(target, function (node) {
              if (node.getAttribute && node.getAttribute('data-track') === 'zalo') {
                return true;
              }
              if (node.tagName === 'A' || node.nodeName === 'A') {
                var href = (node.getAttribute && node.getAttribute('href')) || '';
                var host = node.hostname || '';
                if (host === 'zalo.me' || (host.length > 8 && host.indexOf('.zalo.me') === host.length - 8) || href.indexOf('zalo.me') !== -1) {
                  return true;
                }
              }
              return false;
            });

            if (zaloNode) {
              var zaloUrl = (zaloNode.getAttribute && (zaloNode.getAttribute('href') || zaloNode.getAttribute('data-url'))) || 'zalo';
              if (shouldTrigger('click_zalo', zaloUrl)) {
                if (window.TSLTracking && window.TSLTracking.push) {
                  window.TSLTracking.push('click_zalo', {
                    link_url: zaloUrl,
                    page_path: (window.location && window.location.pathname) || ''
                  });
                }
                if (typeof window.fbq === 'function') {
                  window.fbq('track', 'Contact');
                }
              }
            }
          } catch (err) {}
        },
        true // capture phase
      );
    }
  } catch (e) {}
})();
