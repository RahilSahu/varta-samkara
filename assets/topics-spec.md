# Topic hub pages: classification, page spec, and generator wiring

Audience: the parent agent integrating this into `generate_site.py` (3605
lines). Do not edit `generate_site.py` from this spec; copy the snippets.

## 1. `classify_topic(story_text, caption_text) -> str`

Keyword-rule classifier mapping each news row to exactly one of six topics:
`Politics`, `Economy`, `Science & Tech`, `Sports`, `World`, `India`.

Insertion point: directly after `assign_topic_tags()` (lines 1654-1668,
before `tag_slug()` at line 1671). It needs `re` and `html`, both already
imported at module level.

```python
TOPIC_KEYWORDS = {
    "Politics": ["election", "elections", "vote", "voter", "ballot", "poll",
        "lok sabha", "rajya sabha", "parliament", "minister", "cabinet",
        "government", "bjp", "congress", "aap", "shiv sena", "mns",
        "cec", "election commission", "satyagraha", "protest", "protests",
        "march", "detained", "detention", "police", "court", "supreme court",
        "high court", "fir", "bar association", "lawyers", "strike",
        "jail bharo", "rally", "dharna", "jantar mantar", "shivaji park",
        "suspended", "suspension", "resignation", "mp", "mla", "assembly",
        "ordinance", "regulation", "trai"],
    "Economy": ["economy", "gdp", "growth", "sensex", "nifty", "rupee",
        "rbi", "inflation", "budget", "msp", "crore", "lakh", "billion",
        "trade", "tariff", "jobs", "employment", "unemployment",
        "bank", "banking", "upi", "mdr", "gold", "silver", "steel",
        "investment", "investments", "funding", "startup", "acquisition",
        "merger", "profit", "revenue", "quarter", "oil", "crude",
        "diesel", "petrol", "barrels", "coal", "power plant", "price",
        "prices", "market", "markets", "stock"],
    "Science & Tech": ["space", "isro", "satellite", "orbital", "rocket",
        "falcon 9", "ai", "artificial intelligence", "quantum", "qkd",
        "data centre", "health", "vaccine", "vaccination", "hpv",
        "cervavac", "prequalification", "monsoon", "imd", "climate",
        "exoplanet", "radio signal", "meerkat", "chip", "chips",
        "oracle", "tencent", "iphone", "apple", "research", "discovery",
        "genome", "magnetic field", "nuclear"],
    "Sports": ["cricket", "olympics", "asian games", "athletes", "medal",
        "gold", "silver", "bronze", "hockey", "boxing", "archery",
        "recurve", "wrestling", "badminton", "football", "match",
        "tournament", "innings", "runs", "wickets", "bcci", "final",
        "semi-final", "semifinal", "shoot-off", "tally"],
    "World": ["trump", "white house", "xi jinping", "china", "united states",
        "us forces", "russia", "ukraine", "israel", "iran", "hormuz",
        "pakistan", "gaza", "nato", "unga", "un general assembly",
        "security council", "brazil", "lula", "bolsonaro", "japan",
        "okinawa", "swiss", "switzerland", "europe", "european", "iraq",
        "syria", "canada", "australia", "fbi", "fugitive", "bounty",
        "g7", "midterms"],
}
INDIA_MARKERS = ["india", "indian", "bharat", "delhi", "mumbai",
    "bengaluru", "kerala", "punjab", "gujarat", "jodhpur", "kanpur",
    "raigad", "tamil", "assam", "rajasthan", "kolkata", "hyderabad",
    "lucknow", "patna", "bhopal", "sagar", "indore", "nagpur", "pune",
    "amritsar", "jammu", "kashmir", "bihar", "odisha", "west bengal",
    "uttar pradesh", "madhya pradesh", "ladakh"]

_TOPIC_PRIORITY = ("Politics", "Economy", "Science & Tech", "Sports")

def classify_topic(story_text, caption_text):
    """Map one news row to exactly one of the six hub topics.

    Politics covers elections, EC, parties, protests, police, courts.
    Economy covers markets, RBI, budget, jobs, trade.
    Science & Tech covers space, ISRO, AI, health, vaccines.
    Sports covers cricket, Olympics, athletes.
    World covers non-India international stories.
    India is the fallback for India-centric stories fitting nowhere else.
    """
    text = (" " + (story_text or "") + " " + (caption_text or "") + " ").lower()

    def hits(kws):
        return sum(1 for k in kws
                   if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", text))

    scores = {t: hits(TOPIC_KEYWORDS[t]) for t in _TOPIC_PRIORITY}
    world = hits(TOPIC_KEYWORDS["World"])
    india = hits(INDIA_MARKERS)

    ranked = sorted(_TOPIC_PRIORITY, key=lambda t: (-scores[t],
                                                  _TOPIC_PRIORITY.index(t)))
    if scores[ranked[0]] >= 1:
        return ranked[0]
    # World needs either no India marker, or two independent world hits
    # (India-centric stories that mention Trump/Pakistan stay in India).
    if world >= 1 and (india == 0 or world >= 2):
        return "World"
    return "India"
```

Worked examples against real rows from `varta-samkara-posts-log.md`
(`~/workspace/job-campaign/varta-samkara-posts-log.md`):

- Oct 3 "Delhi + Mumbai protests demanding CEC Gyanesh Kumar's resignation:
  700+ detained at Jantar Mantar Oct 2" -> **Politics** (cec, protests,
  detained, jantar mantar)
- Oct 5 "Cervavac WHO prequalification: India's first indigenous
  gender-neutral HPV vaccine (Serum Institute of India, Pune) receives WHO
  prequalification" -> **Science & Tech** (vaccine, hpv, cervavac,
  prequalification; india markers present but the Science & Tech branch is
  checked first)
- Oct 2 "GOLD FOR INDIA: Lovlina Borgohain wins women's 75kg boxing gold at
  Asian Games 2026" -> **Sports** (boxing, gold, asian games)
- Sep 30 "FBI adds Goldy Brar to Ten Most Wanted Fugitives list, $1M
  bounty" -> **World** (fbi, fugitive, bounty = 3 hits, so the India-marker
  guard does not fire)
- Oct 4 "Border killing sparks diplomatic row: BSF shoots dead two men
  near Tarn Taran Oct 2" -> **India** (world hits: pakistan = 1 only, with
  "Indian envoy" as India marker, so it falls through to India)
- Oct 1 "Modi-Trump phone call: reviewed trade, defence, energy, critical
  tech" -> **Economy** (trade; no Politics keyword hits, so Economy wins)

## 2. Topic hub page spec

### URL pattern

- `topics/<slug>.html` for each topic. Slugs: `politics`, `economy`,
  `science-tech`, `sports`, `world`, `india`.
- `topics/index.html`: the "All topics" index listing all six hubs with
  live story counts.

### Hub page layout (depth 1)

- Use the existing shared helpers: `head()` (line 1517),
  `topbar(1, 'topics')`, `footer(1)` (line 1507), the `JS` block, and the
  back-to-top button, exactly as the tag pages do in `build()`.
- Header block: reuse the `.page-head` pattern from `markets_page()` (line
  2516):

```html
<div class="page-head">
  <h1>Politics</h1>
  <p class="lede">Elections, Parliament, protests and the courts. N stories.</p>
</div>
```

- Story list: cards newest-first, reusing the site's existing card markup
  by calling `card_html(p, depth=1)` (line 1863), inside `<div class="grid">`
  (styles already in `styles.css`).
- Topic ledes (user-facing copy, no em dashes):

| Topic | Lede |
|---|---|
| Politics | Elections, Parliament, protests and the courts. |
| Economy | Markets, the RBI, budgets, jobs and trade. |
| Science & Tech | Space, ISRO, AI, health and the research shaping tomorrow. |
| Sports | Cricket, the Olympics and every athlete making India proud. |
| World | International stories from beyond India's borders. |
| India | India-centric stories, from every corner of the country. |

- "All topics" index section: each hub page ends with a
  `<div class="tag-cloud">` (same class used by the tags index, styles.css
  line 301) of six chips linking to `topics/<slug>.html`, each labelled
  with the topic name and count, plus a link to `topics/index.html`.
- `topics/index.html` mirrors the tags index (`build()` lines 3440-3461):
  `<h2 class="sec-title">Browse by topic</h2>` followed by the same
  chip cloud. Chips reuse `.tagchip` (styles.css line 280).
- Include both news and blog posts in hubs (pass `news + blog_posts`,
  each classified via `classify_topic(p["story"], p["art"]["paras"][0]
  if p["art"]["paras"] else p["excerpt"])`).

Note: hub topics are separate from `assign_topic_tags()` topic tags
(lines 1654-1668). The six hub topics are a single-label-per-story
taxonomy; the tag pages at `tags/` remain multi-label and untouched.

### CSS additions

Append to `BASE_CSS` (line 298, before its closing `"""` around line 923):

```css
.topic-cloud{display:flex;flex-wrap:wrap;gap:.7rem;margin:2rem 0 1rem}
.topic-cloud .tagchip{font-size:.85rem;padding:.4rem 1rem}
```

(`.tag-cloud` already exists and can be reused as-is.)

## 3. Generator wiring

1. In `build()`, right after the `# ---------- tag pages ----------`
   section (lines 3379-3462, i.e. after the tags-index write at
   `with open(os.path.join(tags_root, "index.html"), ...)`), add a
   `# ---------- topic hub pages ----------` block:
   - classify every post in `news + blog_posts`, group by topic, sort
     each group newest-first by `(p["date"], p["slot"])` (same key as the
     tag pages),
   - write `topics/<slug>.html` for each of the six topics and
     `topics/index.html`,
   - stale-page cleanup mirrors the tag dirs: drop
     `topics/<old-slug>.html` files not in the current six.
2. `topbar()` (line 1492): add `{link('topics/','Topics','topics')}` after
   the Tags link so the hub index is reachable from the nav.
3. `footer()` (line 1507): append topic links to the `.foot-links` div,
   e.g. `<a href="{r}topics/politics.html">Politics</a>` for all six.
4. Sitemap `sm_urls` (line 3580): add `"topics/"` and
   `[f"topics/{slug}.html" for slug in TOPIC_SLUGS]` to the URL list.
5. Service worker `CORE` list (line 3464): add `"topics/index.html"` (hub
   pages themselves are cache-as-you-go). Per the AGENTS.md lesson on
   cache versioning, bump `CACHE = "vs-cache-v9"` to `"vs-cache-v10"`.
