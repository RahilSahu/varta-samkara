(function(){
'use strict';
/* Varta & Samkara reader experience: bookmarks, reading history, reading
   progress bar, and estimated read times. Vanilla JS, no dependencies.
   Works with localStorage guarded for private/incognito mode, and degrades
   silently when markup or storage is unavailable. */

var BOOKMARK_KEY = 'vs-bookmarks';
var HISTORY_KEY = 'vs-history';
var HISTORY_MAX = 20;
var RECENT_MAX = 6;
var WPM = 200;

/* All user-facing strings live here, ready for translation. Keep them free
   of em dashes (use commas, colons, or hyphens instead). */
var STRINGS = {
  bookmarkSave: 'Save this story',
  bookmarkSaved: 'Saved',
  bookmarkRemove: 'Remove bookmark',
  bookmarkClearAll: 'Clear all bookmarks',
  bookmarksEmpty: 'No bookmarks yet. Tap the bookmark icon on any story.',
  historyEmpty: 'Stories you read will appear here.',
  readTime: function(mins){ return mins + ' min read'; }
};

/* Safe JSON storage. Returns the fallback value on any failure, so the
   page keeps working in private mode or when storage is disabled. */
function makeStore(key){
  return {
    read: function(fallback){
      try{
        var raw = window.localStorage.getItem(key);
        if(raw === null || raw === undefined) return fallback;
        var val = JSON.parse(raw);
        return (val === null || val === undefined) ? fallback : val;
      }catch(e){ return fallback; }
    },
    write: function(val){
      try{
        window.localStorage.setItem(key, JSON.stringify(val));
        return true;
      }catch(e){ return false; }
    }
  };
}
var bookmarkStore = makeStore(BOOKMARK_KEY);
var historyStore = makeStore(HISTORY_KEY);

/* Bookmark SVG: outline by default, filled (via CSS .on) when saved. */
var BOOKMARK_SVG =
  '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">' +
  '<path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4.2L5 21V4a1 1 0 0 1 1-1z" ' +
  'fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>';

function isBookmarked(id){
  var marks = bookmarkStore.read({});
  return Object.prototype.hasOwnProperty.call(marks, id);
}

function setBookmark(id, data){
  var marks = bookmarkStore.read({});
  if(data){
    marks[id] = { title: data.title || id, url: data.url || '#', ts: Date.now() };
  }else{
    delete marks[id];
  }
  bookmarkStore.write(marks);
}

/* Update every toggle button for one article id, site-wide on this page. */
function paintBookmarkButtons(id){
  var saved = isBookmarked(id);
  var slots = document.querySelectorAll('[data-bookmark-id="' + id + '"]');
  Array.prototype.forEach.call(slots, function(slot){
    var btn = slot.querySelector('.vs-bookmark');
    if(!btn) return;
    btn.classList.toggle('on', saved);
    btn.setAttribute('aria-pressed', saved ? 'true' : 'false');
    btn.setAttribute('aria-label', saved ? STRINGS.bookmarkRemove : STRINGS.bookmarkSave);
  });
}

function toggleBookmark(id, slot){
  if(isBookmarked(id)){
    setBookmark(id, null);
  }else{
    setBookmark(id, {
      title: slot.getAttribute('data-bookmark-title'),
      url: slot.getAttribute('data-bookmark-url')
    });
  }
  paintBookmarkButtons(id);
  renderBookmarks();
}

function initBookmarkToggles(){
  var slots = document.querySelectorAll('[data-bookmark-id]');
  Array.prototype.forEach.call(slots, function(slot){
    if(slot.querySelector('.vs-bookmark')) return; /* already injected */
    var id = slot.getAttribute('data-bookmark-id');
    if(!id) return;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'vs-bookmark';
    btn.innerHTML = BOOKMARK_SVG;
    btn.addEventListener('click', function(ev){
      ev.preventDefault();
      ev.stopPropagation();
      toggleBookmark(id, slot);
    });
    slot.appendChild(btn);
    paintBookmarkButtons(id);
  });
}

/* Render the <section id="bookmarks"> view: saved stories as cards with
   remove buttons, or the empty state. */
function renderBookmarks(){
  var section = document.getElementById('bookmarks');
  if(!section) return;
  var marks = bookmarkStore.read({});
  var ids = Object.keys(marks).sort(function(a, b){
    return (marks[b].ts || 0) - (marks[a].ts || 0);
  });
  var list = section.querySelector('.vs-bookmark-list');
  if(!list){
    list = document.createElement('div');
    list.className = 'vs-bookmark-list grid';
    section.appendChild(list);
  }
  if(!ids.length){
    list.innerHTML = '<p class="muted">' + escapeHtml(STRINGS.bookmarksEmpty) + '</p>';
    return;
  }
  var html = ids.map(function(id){
    var m = marks[id] || {};
    return '<article class="card">' +
      '<div class="card-body">' +
      '<h3><a href="' + escapeAttr(m.url || '#') + '">' + escapeHtml(m.title || id) + '</a></h3>' +
      '<button class="vs-remove" type="button" data-remove-id="' + escapeAttr(id) + '">' +
      escapeHtml(STRINGS.bookmarkRemove) + '</button>' +
      '</div></article>';
  }).join('');
  html += '<div class="vs-bookmark-actions"><button class="vs-clear" type="button">' +
    escapeHtml(STRINGS.bookmarkClearAll) + '</button></div>';
  list.innerHTML = html;
  Array.prototype.forEach.call(list.querySelectorAll('[data-remove-id]'), function(btn){
    btn.addEventListener('click', function(){
      var id = btn.getAttribute('data-remove-id');
      setBookmark(id, null);
      paintBookmarkButtons(id);
      renderBookmarks();
    });
  });
  var clear = list.querySelector('.vs-clear');
  if(clear){
    clear.addEventListener('click', function(){
      bookmarkStore.write({});
      renderBookmarks();
      /* Repaint every toggle currently on the page. */
      var slots = document.querySelectorAll('[data-bookmark-id]');
      Array.prototype.forEach.call(slots, function(slot){
        paintBookmarkButtons(slot.getAttribute('data-bookmark-id'));
      });
    });
  }
}

/* Reading history: record this article, deduped, most recent first. */
function currentArticle(){
  var el = document.querySelector('[data-article-id]');
  var title, url;
  if(el){
    return {
      id: el.getAttribute('data-article-id'),
      title: el.getAttribute('data-article-title') ||
             ((document.querySelector('.article h1') || {}).textContent || '').trim(),
      url: el.getAttribute('data-article-url') || window.location.href
    };
  }
  /* Fallback for article pages without data attributes. */
  var article = document.querySelector('.article');
  if(!article) return null;
  var h1 = article.querySelector('h1');
  title = h1 ? h1.textContent.trim() : document.title;
  var canon = document.querySelector('link[rel="canonical"]');
  url = canon ? canon.getAttribute('href') : window.location.href;
  return { id: url, title: title, url: url };
}

function recordHistory(){
  var art = currentArticle();
  if(!art || !art.id) return;
  var hist = historyStore.read([]);
  if(!Array.isArray(hist)) hist = [];
  hist = hist.filter(function(item){ return item && item.id !== art.id; });
  hist.unshift({ id: art.id, title: art.title, url: art.url, ts: Date.now() });
  historyStore.write(hist.slice(0, HISTORY_MAX));
}

/* Render the <section id="recently-viewed"> view: the 6 most recent
   stories, excluding the page being read. Hides the section when empty. */
function renderRecentlyViewed(){
  var section = document.getElementById('recently-viewed');
  if(!section) return;
  var hist = historyStore.read([]);
  if(!Array.isArray(hist)) hist = [];
  var cur = currentArticle();
  var curId = cur ? cur.id : null;
  var items = hist.filter(function(item){
    return item && item.id && item.id !== curId;
  }).slice(0, RECENT_MAX);
  var list = section.querySelector('.vs-recent-list');
  if(!list){
    list = document.createElement('div');
    list.className = 'vs-recent-list grid';
    section.appendChild(list);
  }
  if(!items.length){
    section.hidden = true;
    return;
  }
  section.hidden = false;
  list.innerHTML = items.map(function(item){
    return '<article class="card">' +
      '<div class="card-body">' +
      '<h3><a href="' + escapeAttr(item.url) + '">' + escapeHtml(item.title) + '</a></h3>' +
      '</div></article>';
  }).join('');
}

/* Reading progress bar: fills #read-progress by scroll percent of the
   article body (not the whole document). */
function initProgressBar(){
  var bar = document.getElementById('read-progress');
  if(!bar) return;
  var body = document.querySelector('[data-article-body]') || document.querySelector('.article');
  if(!body) return;
  var ticking = false;
  function update(){
    ticking = false;
    var rectTop = body.getBoundingClientRect().top + window.pageYOffset;
    var bodyH = body.offsetHeight;
    var vh = window.innerHeight || document.documentElement.clientHeight;
    var span = bodyH - vh;
    var pct = 0;
    if(span > 0){
      pct = (window.pageYOffset - rectTop) / span;
    }else{
      pct = window.pageYOffset >= rectTop ? 1 : 0;
    }
    pct = Math.max(0, Math.min(1, pct));
    bar.style.width = (pct * 100).toFixed(2) + '%';
  }
  function onScroll(){
    if(!ticking){ ticking = true; window.requestAnimationFrame(update); }
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll);
  update();
}

/* Estimated read time: <span data-readtime data-words="N"> becomes
   "N min read" at 200 wpm, minimum 1 minute. */
function initReadTimes(){
  var spans = document.querySelectorAll('[data-readtime]');
  Array.prototype.forEach.call(spans, function(span){
    var words = parseInt(span.getAttribute('data-words'), 10);
    if(isNaN(words) || words < 0) words = 0;
    var mins = Math.max(1, Math.round(words / WPM));
    span.textContent = STRINGS.readTime(mins);
  });
}

function escapeHtml(s){
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
function escapeAttr(s){
  return escapeHtml(s).replace(/'/g, '&#39;');
}

/* Keep tabs in sync when bookmarks change elsewhere. */
function initCrossTabSync(){
  window.addEventListener('storage', function(ev){
    if(ev.key === BOOKMARK_KEY){
      renderBookmarks();
      var slots = document.querySelectorAll('[data-bookmark-id]');
      Array.prototype.forEach.call(slots, function(slot){
        paintBookmarkButtons(slot.getAttribute('data-bookmark-id'));
      });
    }
    if(ev.key === HISTORY_KEY){ renderRecentlyViewed(); }
  });
}

function init(){
  try{
    initBookmarkToggles();
    renderBookmarks();
    recordHistory();
    renderRecentlyViewed();
    initProgressBar();
    initReadTimes();
    initCrossTabSync();
  }catch(e){ /* never break the page */ }
}

if(document.readyState === 'loading'){
  document.addEventListener('DOMContentLoaded', init);
}else{
  init();
}
})();
