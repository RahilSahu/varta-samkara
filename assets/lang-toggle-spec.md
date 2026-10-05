# Language toggle: EN / हिं integration spec

Adds an English/Hindi toggle to the Varta & Samkara static site (GitHub Pages, no backend).
The data files and toggle script already exist:

- `assets/i18n.json` - UI chrome strings, 48 keys each in `en` and `hi`
- `assets/headlines-hi.json` - Hindi headlines for the 30 most recent news posts (2026-10-01 to 2026-10-05), keyed by Instagram post URL, each with `en` (reference) and `hi`
- `assets/lang-toggle.js` - vanilla JS, no dependencies (full source already written; summary in section B below)

This spec tells you exactly what `generate_site.py` must emit. Do not edit the JSON/JS files unless a key is missing; add keys to `assets/i18n.json` instead.

Design constraints: navy/saffron identity, mobile-first, no em dashes in any user-facing copy, vanilla JS only.

---

## A. Toggle button

### HTML (add inside `.top-actions`, before the theme toggle)

```html
<button class="lang-toggle" id="lang-toggle" type="button" aria-label="Switch language / भाषा बदलें" data-lang="en"><span class="lt-en" aria-hidden="true">EN</span><span class="lt-sep" aria-hidden="true">|</span><span class="lt-hi" aria-hidden="true">हिं</span></button>
```

In `topbar()` the button goes here (generator f-string, depth-agnostic, no path needed):

```
<div class="top-actions">
<button class="lang-toggle" id="lang-toggle" ...>...</button>
<div class="top-search">...</div>
<button class="theme-toggle" ...>...</button>
<button class="hamburger" ...>...</button>
</div>
```

### CSS (append to styles.css, uses existing vars)

```css
.lang-toggle{display:inline-flex;align-items:center;gap:.3rem;background:transparent;
border:1.5px solid var(--saffron);color:#fff;border-radius:999px;padding:.3rem .7rem;
font:inherit;font-size:.82rem;font-weight:800;cursor:pointer;white-space:nowrap}
.lang-toggle .lt-en,.lang-toggle .lt-hi{opacity:.5;transition:opacity .2s}
.lang-toggle .lt-sep{opacity:.4}
.lang-toggle[data-lang="en"] .lt-en{opacity:1;color:var(--saffron)}
.lang-toggle[data-lang="hi"] .lt-hi{opacity:1;color:var(--saffron)}
.lang-toggle:hover{border-color:var(--saffron2)}
@media(max-width:720px){.lang-toggle{padding:.25rem .55rem;font-size:.75rem}}
```

Active language shows in saffron, inactive dimmed. Matches the existing `.theme-toggle` placement and the navy header.

### Script tag

Load on every page, `defer` so it runs after parsing. Depth-agnostic: the script resolves its own location via `document.currentScript.src` and fetches the JSONs from the same `assets/` directory, so no `{r}` prefix juggling is needed. In `head()` add:

```html
<script src="{r}assets/lang-toggle.js" defer></script>
```

---

## B. How the JS works (already implemented in `assets/lang-toggle.js`)

- **Storage:** `localStorage` key `vs-lang`, values `en`/`hi`, default `en`. Persists across pages and visits.
- **Swap static strings:** every `[data-i18n="key"]` gets `textContent` from `i18n.json[lang][key]`; falls back to English if a key is missing (never blank).
- **Placeholders:** `[data-i18n-ph="key"]` swaps the `placeholder` attribute (the top search box).
- **Numbered strings:** `[data-i18n-num="min_read" data-num="5"]` renders `5 min read` / `5 मिनट में पढ़ें`. The number stays Latin digits in both languages (standard in Hindi news).
- **Headlines:** every `[data-ig-url="<instagram post url>"]` container gets its headline swapped. The script looks for the headline node inside the container in this order: `[data-headline]`, `h3 a`, `h3`, `h2 a`, `h2`, else the container itself. English original is cached in a `data-en` attribute on first swap so toggling back is exact. URLs are normalized (trailing slash stripped on lookup) so JSON keys and `data-ig-url` values match either way.
- **Scope:** only headlines of the ~30 newest posts have Hindi versions; older cards and article bodies stay in English (no partial/garbled output). This is intentional: the toggle is "Hindi headlines + Hindi chrome", not machine translation.
- **Dynamic content:** exposes `window.VSapplyLang()`. The generator's inline JS must call it after injecting search results or any client-rendered list.
- **html lang:** sets `document.documentElement.lang` to `en`/`hi`.
- **Failure mode:** if either JSON 404s, the site silently stays in English. The button never appears broken.

---

## C. What the generator must emit

### 1. `topbar(depth, active)` - nav labels + search + button

Add `data-i18n` to each nav link (keep the `active` class logic untouched):

| current label | attribute |
|---|---|
| Home | `data-i18n="nav_home"` |
| News | `data-i18n="nav_news"` |
| Videos | `data-i18n="nav_videos"` |
| Blog | `data-i18n="nav_blog"` |
| Heroes | `data-i18n="nav_heroes"` |
| Scores | `data-i18n="nav_scores"` |
| Markets | `data-i18n="nav_markets"` |
| Policy | `data-i18n="nav_policy"` |
| Study | `data-i18n="nav_study"` |
| Today | `data-i18n="nav_today"` |
| Horoscope | `data-i18n="nav_horoscope"` |
| Tags | `data-i18n="nav_tags"` |

The `link()` helper is the natural place: `f'<a href="{r}{href}"{cls}{extra} data-i18n="{i18n_key}">{label}</a>'`. `nav_fact_check` exists in the JSON as a reserved key (no Fact Check page/nav item on the site yet).

Search input: `placeholder="Search stories..."` becomes `data-i18n-ph="search_placeholder"` (keep the English text as the attribute fallback). Burger: `aria-label="Menu"` becomes `aria-label` + `data-i18n="menu"` is for text nodes only, so leave the aria-label in English and add `title` handling if desired; minimal change is fine.

### 2. `card_html(p, depth=0)` - news cards (homepage, archive, tags, related)

- `<article class="card reveal" ...>` gains `data-ig-url="{p['url']}"` (use `html.escape`; `p['url']` is the Instagram post URL from the posts log, e.g. `https://www.instagram.com/reel/DeHnzg4Exc4/`). For cards without a post URL (heroes, opinion-only cards), emit no `data-ig-url` and the headline stays English.
- Reading time: `<span>{rt} min read</span>` becomes `<span data-i18n-num="min_read" data-num="{rt}">{rt} min read</span>`.
- `<a class="read" ...>Read full story &rarr;</a>` becomes `<a class="read" ...><span data-i18n="read_full_story">Read full story</span> &rarr;</a>` (arrow outside the swap span).
- Card headline needs no attribute: the JS finds `h3 a` inside the `data-ig-url` container automatically.

### 3. Homepage (`build()` index section)

- Ticker label: `<span class="ticker-label">Latest</span>` becomes `<span class="ticker-label" data-i18n="ticker_latest">Latest</span>`. Ticker items: add `data-ig-url="{p['url']}"` to each ticker `<a>` so the ticker headlines swap too (JS falls back to the anchor itself as the headline node).
- Hero: `<h2>{title}</h2>` becomes `<h2 data-ig-url="{hero['url']}">{title}</h2>`; kicker `Top story` becomes `<span data-i18n="top_story">Top story</span>`; hero CTA `Read full story` gets `data-i18n="read_full_story"`; `Watch on Instagram` gets `data-i18n="watch_instagram"`.
- Section heads: `Latest News` -> `data-i18n="latest_news"`; `From the Blog` -> `data-i18n="from_blog"`; `Forgotten Hero of the Day` -> `data-i18n="hero_of_day"`; `All news` / `All opinions` / `All heroes` links -> `data-i18n="all_news"` / `"all_opinions"` / `"all_heroes"`.

### 4. `footer(depth)` - newsletter

- `<strong>Newsletter</strong>` -> `data-i18n="newsletter"`; `Subscribe` button -> `data-i18n="subscribe"`.

### 5. Article pages (`article_page()`)

- Share label `Share:` -> `data-i18n="share"` (render `साझा करें:` in Hindi; keep the colon outside the span).
- `Sources:` label -> `data-i18n="sources"`; `Visuals:` label -> `data-i18n="visuals"`.
- `Related stories` heading -> `data-i18n="related_stories"`.
- `{rt} min read` meta -> `data-i18n-num="min_read" data-num="{rt}"` (same pattern as cards).
- Opinion tag `Opinion` -> `data-i18n="opinion"` (covers blog cards too).
- `On this page` TOC heading -> `data-i18n="on_this_page"`.
- `View on Instagram` button -> `data-i18n="watch_instagram"`.

### 6. Inline `JS` constant (search results)

The client-side search renders `Sources: ` at line ~2440. Wrap it: `'<div class="p-src"><span data-i18n="sources">Sources</span>: '`. After the search results are injected into the DOM, call `window.VSapplyLang()` so they swap to the current language.

### 7. Service worker

Add `assets/i18n.json`, `assets/headlines-hi.json`, `assets/lang-toggle.js` to the `sw.js` CORE cache list and bump the `CACHE` version string (existing convention: v7 -> v8 etc.), or returning visitors keep serving the old pages without the toggle.

---

## D. Mobile behavior

- The toggle lives in `.top-actions`, which is always visible (flex) next to the search box, theme toggle, and hamburger, on all breakpoints. It is reachable without opening the mobile menu. Compact CSS at `max-width:720px` (smaller padding/font) keeps it from crowding the search input.
- The mobile dropdown (`.navlinks.open`) needs no separate toggle: its links are the same nav links, which carry `data-i18n` and swap in place.
- Test checklist: 320px width, toggle tappable, menu open/close still works, headlines swap on homepage cards + hero + ticker, `localStorage vs-lang` persists across page navigations, toggling back to EN restores the exact original English headline (via `data-en` caching).

## E. Maintenance

- `headlines-hi.json` covers the 30 newest posts as of 2026-10-05 (log rows 2026-10-01 through 2026-10-05 with Instagram URLs). Older posts fall back to English headlines; that is by design.
- To extend: take the newest posts-log rows with Instagram URLs, write natural Hindi headlines faithful to the English originals, and append entries keyed by the exact post URL. No generator change is needed for new entries.
- If a headline must be corrected, edit the JSON and rebuild; bump the service-worker cache version so the new JSON reaches clients.
