# "Trending now" horizontal snap-scroll strip for the homepage

## Placement

Insert in `build()` in the `# ---------- homepage ----------` section
(line 3104), directly after the `ticker` div and before `<section
class="hero">`, so the strip sits under the breaking-news ticker and above
the hero story:

```html
<div class="trend-strip" aria-label="Trending now">
  <div class="trend-head"><span class="trend-label">Trending now</span></div>
  <div class="trend-scroll">
    ... 8 items ...
  </div>
</div>
```

## Data

The 8 most recent entries from `parse_log()` (line 68): reuse
`all_sorted[:8]` (defined in `build()` at line ~2980), which is already
sorted newest-first by `(date, slot)` across news and blog posts.

Relative time: compute at build time in `IST` (module-level constant,
line 51). Map each row's slot to an approximate post time, then diff
against build time:

```python
_SLOT_HOURS = {"morning": 8, "midday": 11, "afternoon": 14, "evening": 17,
               "night": 20, "manual": 12}
def rel_time(p):
    day = datetime.strptime(p["date"], "%Y-%m-%d").replace(tzinfo=IST)
    m = re.match(r"(morning|midday|afternoon|evening|night)", p["slot"].lower())
    posted = day.replace(hour=_SLOT_HOURS[m.group(1)] if m else 12)
    delta = datetime.now(IST) - posted
    mins = max(0, int(delta.total_seconds() // 60))
    if mins < 60: return f"{max(mins, 1)}m ago"
    if mins < 1440: return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"
```

(The site is a static GitHub Pages build, so "now" means last-build time;
matches the existing behaviour of the ticker and `TODAY`.)

## Item markup

Pure HTML/CSS, no JS library. Thumbnails reuse `p["thumb"]` and headline
`p["art"]["title"]` (same fields `card_html()` uses at line 1863). Each
item links to its article URL via `page_url(p)`-style section path
(`posts/<slug>/` or `blog/<slug>/`, per `sec_of()` at line 2067).

```html
<a class="trend-item" href="posts/some-slug/">
  <img src="assets/thumbs/some-slug.jpg" alt="" loading="lazy" decoding="async">
  <div class="trend-body">
    <p>Headline text here</p>
    <span class="trend-time">2h ago</span>
  </div>
</a>
```

Image alt is intentionally empty because the headline already names the
story.

## CSS (append to BASE_CSS at line 298, before its closing `"""`)

Mobile-first, navy/saffron, works in both light and dark themes:

```css
.trend-strip{margin:1.4rem 0 .4rem}
.trend-head{display:flex;align-items:center;gap:.6rem;margin:0 .2rem .8rem}
.trend-label{background:var(--saffron);color:var(--navy);font-weight:800;
  font-size:.78rem;letter-spacing:.06em;text-transform:uppercase;
  padding:.3rem .8rem;border-radius:20px}
.trend-scroll{display:flex;gap:.9rem;overflow-x:auto;scroll-snap-type:x mandatory;
  -webkit-overflow-scrolling:touch;padding:.2rem .2rem 1rem;
  scrollbar-width:thin}
.trend-scroll::-webkit-scrollbar{height:6px}
.trend-scroll::-webkit-scrollbar-thumb{background:var(--saffron);border-radius:3px}
.trend-item{flex:0 0 240px;scroll-snap-align:start;display:flex;gap:.7rem;
  background:var(--card);border-radius:var(--radius);overflow:hidden;
  box-shadow:var(--shadow);text-decoration:none;color:inherit;
  border-left:4px solid var(--saffron)}
.trend-item img{width:86px;min-width:86px;height:86px;object-fit:cover;
  background:var(--navy)}
.trend-body{display:flex;flex-direction:column;justify-content:center;
  gap:.35rem;padding:.5rem .7rem .5rem 0;min-width:0}
.trend-body p{font-size:.82rem;line-height:1.35;font-weight:600;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;
  overflow:hidden;margin:0}
.trend-time{font-size:.72rem;color:var(--saffron);font-weight:700;
  text-transform:uppercase;letter-spacing:.05em}
.trend-item:hover{transform:translateY(-3px)}
@media(min-width:760px){.trend-item{flex-basis:280px}}
```

Uses the existing CSS variables `--saffron`, `--navy`, `--card`,
`--shadow`, `--radius`, which are defined in `BASE_CSS` and honoured in
dark mode (see `html[data-theme="dark"]` rules around styles.css
line 244-256). Line-clamp keeps all items the same height on small
screens; `-webkit-line-clamp` already relies on the `display:-webkit-box`
pattern used elsewhere in the stylesheet.

## Notes

- The strip is static per build, like the ticker in `build()` (line 3104).
  It refreshes on every scheduled site rebuild (the 21:00 IST
  `varta-samkara-site-sync-daily` cron rebuilds when new posts appear).
- Accessibility: the strip is one labelled region; items are plain links.
  No autoplay, no keyboard traps; `overflow-x:auto` is natively swipeable
  on touch devices.
