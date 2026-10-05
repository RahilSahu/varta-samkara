/* Varta & Samkara: EN/HI language toggle (vanilla JS, no dependencies).
   Loads ../assets/i18n.json and ../assets/headlines-hi.json relative to this
   file's own location, so it works at any page depth (/, /posts/x/, /tags/y/).
   Generator integration: see assets/lang-toggle-spec.md. */
(function () {
  'use strict';
  var KEY = 'vs-lang';
  var I18N = null;      /* {en:{}, hi:{}} from i18n.json */
  var HEADLINES = null; /* {igUrl:{en,hi}} from headlines-hi.json */

  function assetsBase() {
    try {
      var s = document.currentScript && document.currentScript.src;
      if (s) return s.replace(/lang-toggle\.js([?#].*)?$/, '');
    } catch (e) {}
    /* fallback: derive from the stylesheet href the generator emits */
    var css = document.querySelector('link[rel="stylesheet"]');
    if (css) return (css.getAttribute('href') || 'styles.css').replace(/styles\.css$/, '') + 'assets/';
    return 'assets/';
  }

  function getJSON(url, cb) {
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(cb)
      .catch(function () { cb(null); });
  }

  function getLang() {
    try {
      var v = localStorage.getItem(KEY);
      if (v === 'hi' || v === 'en') return v;
    } catch (e) {}
    return 'en';
  }

  function setLang(lang) {
    try { localStorage.setItem(KEY, lang); } catch (e) {}
    applyLang(lang);
  }

  /* Normalize IG URLs so JSON keys (with trailing slash) match whatever
     the generator emits in data-ig-url (with or without trailing slash). */
  function normUrl(u) {
    return String(u || '').trim().replace(/\/+$/, '');
  }

  function headlineText(el) {
    return el.querySelector('[data-headline]') ||
           el.querySelector('h3 a') || el.querySelector('h3') ||
           el.querySelector('h2 a') || el.querySelector('h2') || el;
  }

  function applyLang(lang) {
    if (!I18N) return;
    var dict = I18N[lang] || {}, en = I18N.en || {};

    document.documentElement.setAttribute('lang', lang === 'hi' ? 'hi' : 'en');

    /* static UI strings */
    var els = document.querySelectorAll('[data-i18n]');
    for (var i = 0; i < els.length; i++) {
      var k = els[i].getAttribute('data-i18n');
      els[i].textContent = (k in dict) ? dict[k] : (en[k] || els[i].textContent);
    }
    /* placeholder attributes */
    var phs = document.querySelectorAll('[data-i18n-ph]');
    for (var p = 0; p < phs.length; p++) {
      var kp = phs[p].getAttribute('data-i18n-ph');
      phs[p].setAttribute('placeholder', (kp in dict) ? dict[kp] : (en[kp] || ''));
    }
    /* number-prefixed strings, e.g. "5 min read" -> "5 मिनट में पढ़ें" */
    var nums = document.querySelectorAll('[data-i18n-num]');
    for (var n = 0; n < nums.length; n++) {
      var kn = nums[n].getAttribute('data-i18n-num');
      var num = nums[n].getAttribute('data-num') || '';
      var word = (kn in dict) ? dict[kn] : (en[kn] || '');
      nums[n].textContent = num ? (num + ' ' + word) : word;
    }

    /* headlines keyed by Instagram post URL */
    if (HEADLINES) {
      var cards = document.querySelectorAll('[data-ig-url]');
      for (var c = 0; c < cards.length; c++) {
        var nu = normUrl(cards[c].getAttribute('data-ig-url'));
        var entry = HEADLINES[nu] || HEADLINES[nu + '/'];
        var hnode = headlineText(cards[c]);
        if (lang === 'hi' && entry && entry.hi) {
          if (!hnode.getAttribute('data-en')) hnode.setAttribute('data-en', hnode.textContent);
          hnode.textContent = entry.hi;
        } else if (hnode.getAttribute('data-en')) {
          hnode.textContent = hnode.getAttribute('data-en');
        }
      }
    }

    /* toggle button state */
    var btn = document.getElementById('lang-toggle');
    if (btn) {
      btn.setAttribute('data-lang', lang);
      btn.setAttribute('aria-pressed', lang === 'hi' ? 'true' : 'false');
    }
  }

  function init() {
    var base = assetsBase();
    getJSON(base + 'i18n.json', function (i18n) {
      if (!i18n) return; /* JSON missing: site stays in English */
      I18N = i18n;
      getJSON(base + 'headlines-hi.json', function (hl) {
        HEADLINES = hl || {};
        applyLang(getLang());
      });
    });
    var btn = document.getElementById('lang-toggle');
    if (btn) {
      btn.addEventListener('click', function () {
        setLang(getLang() === 'hi' ? 'en' : 'hi');
      });
    }
  }

  /* public API: call after injecting dynamic content (search results, etc.) */
  window.VSapplyLang = function () { applyLang(getLang()); };
  window.VSsetLang = setLang;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
