# Story timeline view: clustering rule, layout, and `timeline.html` spec

## 1. Clustering rule: story threads

Goal: group news rows from `parse_log()` (line 68) into story threads, so
a reader can follow one developing story across days instead of scrolling
the archive.

### Thread rule

Two posts are **linked** when they share at least 2 keyword hits from the
existing `keywords()` helper (line 92), where each shared keyword is:

- length 5 or more (so short glue words do not count), and
- not in a generic-words blocklist: `{"india", "indian", "news", "today",
  "reel", "carousel", "post", "video", "watch", "read", "story", "full",
  "gold", "silver", "bronze", "medal"}`.

A **thread** is a transitively linked cluster of 2 or more posts whose
first and last post are at most 14 days apart.

### Implementation sketch

New function `cluster_threads(news)`, placed right after
`related_posts()` (lines 1564-1574), reusing `keywords()` and `sec_of()`:

```python
_THREAD_BLOCKLIST = {"india", "indian", "news", "today", "reel", "carousel",
    "post", "video", "watch", "read", "story", "full", "gold", "silver",
    "bronze", "medal", "golden"}

def _thread_keys(p):
    return {w for w in keywords(p["art"]["title"] + " " + p["excerpt"])
            if len(w) >= 5 and w not in _THREAD_BLOCKLIST}

def cluster_threads(news):
    keys = {p["slug"]: _thread_keys(p) for p in news}
    parent = {p["slug"]: p["slug"] for p in news}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    by_slug = {p["slug"]: p for p in news}
    for a in news:
        for b in news:
            if a["slug"] >= b["slug"]:
                continue
            da = datetime.strptime(a["date"], "%Y-%m-%d")
            db = datetime.strptime(b["date"], "%Y-%m-%d")
            if abs((da - db).days) > 14:
                continue
            if len(keys[a["slug"]] & keys[b["slug"]]) >= 2:
                parent[find(a["slug"])] = find(b["slug"])
    groups = {}
    for p in news:
        groups.setdefault(find(p["slug"]), []).append(p)
    threads = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda p: (p["date"], p["slot"]), reverse=True)
        # thread title: most common long keyword across members
        from collections import Counter
        cnt = Counter()
        for p in members:
            cnt.update(keys[p["slug"]])
        threads.append({"title": cnt.most_common(1)[0][0].title() + " thread",
                        "items": members})
    threads.sort(key=lambda t: len(t["items"]), reverse=True)
    return threads
```

Post-merge rule: after auto-clustering, if two threads share an item
title keyword that is clearly a proper noun (e.g. "machchhar"), they are
the same thread; the simple union-find above already handles this because
all same-story rows share many keywords.

### Thread titles (editorial override, optional)

Keyword-derived titles like "Jantar thread" are serviceable, but the
generator may override with hand-written titles per thread id. Keep the
auto title as the default.

## 2. `timeline.html` spec

### Page shape (depth 0)

New function `timeline_page(threads)` placed after `markets_page()` (line
2516), following the same `page_shell()` (line 2499) pattern with active
key `"timeline"`. Emit from `build()` right after the `# ---------- news
archive ----------` section (line 3166):

```python
with open(os.path.join(SITE, "timeline.html"), "w", encoding="utf-8") as f:
    f.write(timeline_page(cluster_threads(news)))
```

### "Developing stories" section (top of page)

The 5 most active threads: most items first, ties broken by most recent
item date. Rendered as horizontal cards, each showing the thread title,
item count, latest headline, and latest date, linking to the thread's
anchor further down the page (`#thread-<n>`):

```html
<section class="dev-stories">
  <div class="sec-head"><h2 class="sec-title">Developing stories</h2></div>
  <div class="dev-grid">
    <a class="dev-card" href="#thread-1">
      <span class="dev-count">4 updates</span>
      <h3>Asian Games 2026 thread</h3>
      <p>Latest headline here</p>
      <span class="dev-date">Oct 3, 2026</span>
    </a>
    ...
  </div>
</section>
```

### Vertical timeline layout

Below "Developing stories", one block per thread:

```html
<section class="thread" id="thread-1">
  <div class="sec-head"><h2 class="sec-title">Asian Games 2026 thread</h2>
  <span class="tag">4 updates, Oct 1 to Oct 3</span></div>
  <ol class="tl">
    <li class="tl-item">
      <span class="tl-dot" aria-hidden="true"></span>
      <div class="tl-card">
        <span class="tl-date">Oct 3</span>
        <h4><a href="posts/slug/">Headline</a></h4>
        <p>Excerpt</p>
      </div>
    </li>
    ...
  </ol>
</section>
```

Items within a thread are newest-first. Threads are ordered most-active
first, matching the Developing stories order.

### CSS (append to BASE_CSS at line 298)

Navy spine, saffron dots, reuse of `.card` shadow language:

```css
.dev-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:1.1rem}
.dev-card{display:block;background:var(--card);border-radius:var(--radius);
  box-shadow:var(--shadow);padding:1.1rem 1.2rem;text-decoration:none;color:inherit;
  border-top:4px solid var(--saffron)}
.dev-card:hover{transform:translateY(-4px)}
.dev-count{font-size:.72rem;font-weight:800;color:var(--saffron);
  text-transform:uppercase;letter-spacing:.06em}
.dev-card h3{font-size:1.02rem;margin:.3rem 0;color:var(--navy)}
html[data-theme="dark"] .dev-card h3{color:#f2f5fc}
.dev-card p{font-size:.85rem;color:var(--muted);margin:.2rem 0}
.dev-date{font-size:.74rem;color:var(--muted)}
.thread{margin-top:2.6rem}
.tl{list-style:none;margin:1.2rem 0 0;padding:0;position:relative}
.tl::before{content:"";position:absolute;left:9px;top:6px;bottom:6px;width:3px;
  background:var(--navy);border-radius:2px;opacity:.25}
.tl-item{position:relative;padding:0 0 1.3rem 2.4rem}
.tl-dot{position:absolute;left:2px;top:4px;width:17px;height:17px;border-radius:50%;
  background:var(--saffron);border:3px solid var(--navy)}
.tl-card{background:var(--card);border-radius:var(--radius);
  box-shadow:var(--shadow);padding:1rem 1.2rem}
.tl-card h4{font-size:.98rem;margin:.25rem 0 .4rem}
.tl-card p{font-size:.86rem;color:var(--muted);margin:0}
.tl-date{font-size:.74rem;font-weight:800;color:var(--saffron);
  text-transform:uppercase;letter-spacing:.06em}
```

## 3. Real example threads (identified from the posts log)

All quoted headlines are verbatim from
`~/workspace/job-campaign/varta-samkara-posts-log.md`. These five would be
the current "Developing stories" top 5:

**Thread 1. Asian Games 2026 (4 items, Oct 1 to Oct 3)**

- Oct 3: "Asian Games 2026 wrap: India 4th with 85 medals (21G/27S/37B);
  cricket beat Pakistan by 19 runs (211/6 vs 192/6, Abhishek 61 off 28,
  Tilak 51* off 22, Hasan Nawaz 96); hockey 5-1 vs Malaysia, LA 2028
  quota"
- Oct 3: "Kumkum Mohod wins historic women's individual recurve archery
  GOLD at Asian Games 2026 (Aichi-Nagoya, Oct 3 morning)"
- Oct 2: "GOLD FOR INDIA: Lovlina Borgohain wins women's 75kg boxing gold
  at Asian Games 2026 (Aichi-Nagoya), beating China's Bao Ziyi 5-0
  unanimous on her 29th birthday (Oct 2)"
- Oct 1: "Asian Games 2026 Oct 1: cricket beats Sri Lanka by 124 runs
  (169/7, Ishan Kishan 80* off 46; SL 45 all out) to set IND vs PAK
  gold-medal final; hockey beats Pakistan 4-3 (Abhishek 60th-min winner)
  to reach final"

**Thread 2. Captain Smit Machchhar / flydubai FZ1073 (3 items, Oct 1 to
Oct 3)**

- Oct 3: "PM Modi's Oct 2 video call with flydubai Captain Smit Machchhar
  (stabbed by Omani co-pilot in alleged bid to crash FZ1073 Dubai-Tel
  Aviv Sept 30; jet plunged ~14,000 ft in <30s)"
- Oct 1: "Same Machchhar/FZ1073 story as 30s reel (Ken Burns over
  carousel cards) with original synthesized uplifting EDM bed"
- Oct 1: "The Indian pilot who stopped what could have been Israel's
  9/11: Captain Smit Machchhar (flydubai FZ1073, Dubai to Tel Aviv, Sep
  30 2026); Omani co-pilot allegedly stabbed him and tried to crash the
  737 MAX 8 with 174 on board"

**Thread 3. Jantar Mantar anti-CEC protest (3 items, Oct 3 to Oct 5)**

- Oct 5: "3 women journalists allege sexual harassment by Delhi Police
  during Oct 3 Jantar Mantar anti-CEC protest: complaints transferred to
  Crime Branch (Kamla Market) for fair enquiry"
- Oct 5: "Thackeray cousins' joint march in Mumbai against EC (Oct 4):
  Uddhav + Raj Thackeray led march from Byculla to BMC HQ at CSMT
  demanding electoral roll revision scrapped and CEC Gyanesh Kumar's
  resignation"
- Oct 3: "Delhi + Mumbai protests demanding CEC Gyanesh Kumar's
  resignation: 700+ detained at Jantar Mantar Oct 2 (Atishi, Bharadwaj,
  Jha, Sanjay Singh, Yogendra Yadav, Neha Bora, Vinod Jakhar)"

**Thread 4. US-Iran standoff (3 items, Sep 27 to Oct 2)**

- Oct 2: "US sending 3rd aircraft carrier to Middle East as Trump weighs
  fresh Iran strikes (USS Theodore Roosevelt strike group from San Diego
  Sep 27 + USS Makin Island group with ~2,000 Marines of 13th MEU)"
- Oct 1: "Trump's ultimatum as US-Iran talks collapse: Oval Office 'We
  blow them up or make a deal', 'very soon, one way or the other'"
- Sep 27: "Trump rejects Iran's 7-day plan to reopen the Strait of Hormuz
  (explainer: Hormuz stakes, the plan, Trump's rejection, nuclear
  sticking point)"

**Thread 5. Brazil presidential election (2 items, Oct 3 to Oct 4)**

- Oct 4: "BRAZIL VOTES TODAY: Lula vs Flavio Bolsonaro first-round
  explainer (Oct 4 first round; Saturday final polls: Datafolha Lula
  45%/Flavio 42%, runoff 47%/46%)"
- Oct 3: "Brazil presidential election first round Oct 4: Lula (80,
  incumbent) vs Flavio Bolsonaro (44, eldest son of jailed ex-president
  Jair Bolsonaro, 27-yr coup-plot sentence)"

## 4. Generator wiring

1. `cluster_threads(news)` after `related_posts()` (line 1564).
2. `timeline_page(threads)` after `markets_page()` (line 2516).
3. `build()`: emit `timeline.html` right after the news archive section
   (line 3166).
4. `topbar()` (line 1492): add `{link('timeline.html','Timeline',
   'timeline')}` after the Topics link, so all three discovery features
   are one tap away.
5. Sitemap `sm_urls` (line 3580): add `"timeline.html"`.
6. Service worker `CORE` (line 3464): add `"timeline.html"`; bump
   `CACHE` to `"vs-cache-v10"` per the AGENTS.md cache-version lesson.
7. Copy check: no em dashes in any timeline user-facing copy (headings,
   ledes, card text).
