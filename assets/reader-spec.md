# vs-reader.js integration spec

One self-contained vanilla JS file powers four reader features, with no
backend and no dependencies:

- `assets/js/vs-reader.js`

It activates purely from markup hooks. Pages that do not include the
hooks are unaffected, and the script never breaks a page when hooks or
localStorage are missing.

## 1. Script wiring

Add one line, with `defer`, to every generated page, right next to the
existing script includes:

- In `article_page()` (generate_site.py, next to the readaloud.js line):

  `<script src="{r}assets/js/vs-reader.js" defer></script>`

- In `page_shell()` (after the `{JS}` inline block, before `{extra_js}`):

  `<script src="assets/js/vs-reader.js" defer></script>`

Add `assets/js/vs-reader.js` to the `CORE` list in `sw.js` and bump the
`CACHE` version string (per the standing rule: new cached asset means a
version bump, or clients keep serving the old files).

Note: the generator's CSS lives inline in generate_site.py (the block
that starts around the `.navlinks` rules); the CSS additions in section 5
go there, or into `styles.css` if the build writes it from that block.

## 2. Bookmark toggles: `data-bookmark-id`

The generator marks a bookmark slot; the JS injects the button into it.
Slot markup (attributes must be HTML-escaped):

```html
<span class="vs-bookmark-slot" data-bookmark-id="news/my-slug/"
      data-bookmark-title="Story title"
      data-bookmark-url="news/my-slug/"></span>
```

- `data-bookmark-id`: stable unique id for the story. Use the section
  plus slug, e.g. `news/my-slug/`, `blog/my-post/`, `heroes/my-hero/`.
  For video cards, use the Instagram post URL.
- `data-bookmark-title` / `data-bookmark-url`: what gets stored in
  `vs-bookmarks` as `{id: {title, url, ts}}`. URLs can be relative; the
  generator's relative-path helper (`rel(depth)`) already produces the
  right form.

Where to put the slots:

- **Article pages** (`article_page()`): one slot in the byline/meta row
  next to `{rt} min read`, so readers can save while reading.
- **News cards** (`card_html()`): one slot in `.card-body`, next to the
  meta line. This covers homepage cards, archive cards, tag pages, and
  related-story cards automatically, since they all use `card_html()`.
- **Video cards** (`videos_page()`): one slot per `.video-card`,
  `data-bookmark-id` set to the reel URL. Bookmarking a reel stores its
  title and Instagram URL.
- **Homepage hero top story** (if it does not use `card_html()`): add
  the slot there too, same pattern.

Clicking a toggle calls `preventDefault()` and `stopPropagation()`, so
the slot can sit inside a card without triggering the card's link.
`aria-pressed` flips between true/false, and the label switches between
"Save this story" and "Remove bookmark". Toggling repaints every button
with the same id on the page, and cross-tab changes sync via the
`storage` event.

## 3. Bookmarks view and recently viewed

The JS renders into sections the generator emits; both are optional and
hidden gracefully when there is nothing to show.

- `<section id="bookmarks">`: renders saved bookmarks as `.card` cards
  with a "Remove bookmark" button each, plus a "Clear all bookmarks"
  button. Empty state: "No bookmarks yet. Tap the bookmark icon on any
  story." The generator should supply the section heading, e.g.
  `<h2>Your bookmarks</h2>`; the JS appends the list container.

- `<section id="recently-viewed">`: renders the 6 most recent history
  entries (max 20 stored in `vs-history`, deduped, newest first),
  excluding the page currently open. When empty, the section is hidden
  (`hidden` attribute), so the generator can emit it unconditionally.

Suggested placement:

- A "Your reading" block on the homepage containing both sections
  (`<section id="bookmarks">` and `<section id="recently-viewed">`),
  under the main news grid. This gives bookmarks and history a stable
  home without a new page.
- Alternatively, a dedicated `bookmarks.html` page reusing
  `page_shell()` with just `<section id="bookmarks">`, linked from the
  top nav. Pick one; the homepage block is simpler.

## 4. Article pages: history, progress bar, read time

History recording needs the article identified. Add data attributes to
the article container in `article_page()`:

```html
<div class="article-col article" data-article-id="news/my-slug/"
     data-article-title="Story title" data-article-url="news/my-slug/"
     data-article-body>
```

`data-article-id` is the history key (falls back to the canonical URL,
then the title from `.article h1`). `data-article-body` marks the
element the progress bar measures; without it, the JS falls back to
`.article`.

Progress bar: place this as the first element inside `<body>`, before
the topbar, in `article_page()` only:

```html
<div id="read-progress" role="progressbar" aria-label="Reading progress"
     aria-valuemin="0" aria-valuemax="100"></div>
```

The JS fills its width by scroll percent of the article body (not the
whole document). The generator already emits
`<div class="progress" id="progress"></div>` with an inline scroll
handler that fills by whole-document scroll. To avoid two bars,
replace that div with `<div id="read-progress">` on article pages and
remove the `bar`/`progress` handling from the inline `JS` scroll
function for those pages (keep it on listing pages, where it can fill
by document scroll, or keep the old div there unchanged).

Read time: replace the static `{rt} min read` spans in `card_html()`
and `article_page()` with:

```html
<span data-readtime data-words="1234">5 min read</span>
```

`data-words` is the article word count (the existing `reading_time()`
already counts words at 200 wpm; emit the raw count instead). The JS
converts to "X min read" (200 wpm, minimum 1 minute). The static text
inside is the no-JS fallback.

## 5. CSS additions (navy/saffron theme)

```css
/* Reading progress bar: fixed at the very top, saffron gradient */
#read-progress{position:fixed;top:0;left:0;height:3px;width:0;z-index:70;
  background:linear-gradient(90deg,var(--saffron),var(--saffron2));}

/* Bookmark toggle button: 44px touch target, saffron when saved */
.vs-bookmark-slot{display:inline-flex;vertical-align:middle;}
.vs-bookmark{display:inline-flex;align-items:center;justify-content:center;
  min-width:44px;min-height:44px;padding:0;border:0;background:transparent;
  color:var(--navy);cursor:pointer;border-radius:8px;}
.vs-bookmark:hover{background:rgba(255,153,51,.12);}
.vs-bookmark svg{display:block;}
.vs-bookmark.on{color:var(--saffron);}
.vs-bookmark.on svg path{fill:currentColor;}

/* Bookmarks view: reuse .card and .grid, add remove/clear buttons */
.vs-bookmark-list .vs-remove,
.vs-bookmark-actions .vs-clear{display:inline-block;margin-top:.6rem;
  padding:.6rem 1rem;min-height:44px;border:1px solid var(--saffron);
  background:transparent;color:var(--saffron);border-radius:8px;
  font-size:.85rem;cursor:pointer;}
.vs-bookmark-list .vs-remove:hover,
.vs-bookmark-actions .vs-clear:hover{background:rgba(255,153,51,.12);}
.vs-bookmark-actions{margin-top:1rem;}
```

Dark mode inherits automatically through the CSS variables.

## 6. Search index

The search index (`assets/search-index.json`) should not treat
bookmarked state at all. Bookmarks and history are purely client-side,
stored per device in localStorage under `vs-bookmarks` and
`vs-history`. No bookmark data goes into the index, no index field is
needed, and search results never reflect bookmark state.

## 7. Copy rules

All user-facing strings live in the `STRINGS` object at the top of
`vs-reader.js`, in English, ready for translation. Current strings:

- "Save this story", "Saved", "Remove bookmark",
  "Clear all bookmarks"
- "No bookmarks yet. Tap the bookmark icon on any story."
- "Stories you read will appear here."
- "X min read" (X computed at 200 wpm, min 1)

No em dashes in any user-facing copy.

## 8. Failure behavior

- localStorage unavailable (private mode, disabled storage): reads
  return empty state, writes are no-ops, page works normally.
- Missing hooks: no `#read-progress`, no `[data-bookmark-id]`, no
  sections, no crash. Each feature checks for its elements first.
- The whole `init()` is wrapped in try/catch; the script can never
  break page rendering.
