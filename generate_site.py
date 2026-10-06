#!/usr/bin/env python3
"""Generate the Varta & Samkara static news website from the posts log.

Outputs:
  index.html            homepage (animated hero + latest news + blog strip)
  archive.html          news archive grouped by date
  blog.html             blog / opinions index
  posts/<slug>/        one full article page per news post
  blog/<slug>/         one full article page per opinion post
  assets/thumbs/       card thumbnails
  assets/articles/     article images (hero + galleries)
  styles.css

Content sources (in priority order) for article bodies:
  content/<slug>.md    full human-written article (lede first, ## subheads)
  caption.txt          fallback: caption paragraphs

User blogs: drop a markdown file in blogs/ with frontmatter:
  ---
  title: My blog title
  date: 2026-10-02
  ---
  Body...

Idempotent: assets are only recopied when the source is newer; stale
article directories are removed. Safe to run on a schedule.
"""
import os, re, shutil, html, json, urllib.parse, textwrap
from datetime import datetime, timezone, timedelta
from PIL import Image, ImageDraw, ImageFont

# Always run from the website root: many content lookups use relative paths
# (content-study/, assets-src/, blogs/). Without this, a run from any other
# working directory silently drops cards and pages that fail os.path.exists.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

WS = "/home/hatch/workspace/job-campaign"
LOG = os.path.join(WS, "varta-samkara-posts-log.md")
POSTS = os.path.join(WS, "varta-samkara", "posts")
SITE = "/home/hatch/workspace/varta-samkara-website"
CONTENT = os.path.join(SITE, "content")
BLOGSDIR = os.path.join(SITE, "blogs")
HEROESDIR = os.path.join(SITE, "heroes")
IST = timezone(timedelta(hours=5, minutes=30))
TODAY = datetime.now(IST).strftime("%Y-%m-%d")
THUMBS = os.path.join(SITE, "assets", "thumbs")
ARTICLES = os.path.join(SITE, "assets", "articles")
IG = "https://www.instagram.com/vartaandsamkaraindia/"
CONTACT_EMAIL = "rahilsahu2000@gmail.com"

BLOG_PREFIXES = ("OPINION:", "ANALYSIS:", "FROM THE HEART:")

# story-prefix -> post directory, for cases the fuzzy matcher gets wrong
MANUAL_DIRS = {
    "OPINION: Wealth is concentrated everywhere": "2026-09-25-inequality-opinion",
    "OPINION: I love my country. India is the best.": "2026-09-25-patriotism-opinion",
    "OPINION: Has India \"bent\" before the US?": "2026-09-25-india-us-opinion",
    "TDB backs GalaxEye with Rs 63.84 cr": "2026-09-26-galaxeye",
    "NEWS: IAF Tarang Shakti 2026 with US F-35s": "2026-09-26-tarang-shakti",
    "ANALYSIS: India's growth in numbers": "2026-09-25-india-growth-numbers",
    "FROM THE HEART: Saare jahan se accha": "2026-09-25-saare-jahan-se-accha-extended",
    "Same Machchhar/FZ1073 story": "2026-10-01-machchhar-hero",
    "NEWS: India's Russian oil imports fall": "2026-09-25-extra-news-3",
}

# ---------------------------------------------------------------- parsing

def parse_log():
    rows = []
    for line in open(LOG, encoding="utf-8"):
        line = line.strip()
        if not line.startswith("|") or line.startswith("| Date"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 7:
            continue
        date, slot, fmt, story, sources, media, urlcell = cells[:7]
        m = re.search(r"https://www\.instagram\.com/[^\s|)]+", urlcell)
        if not m:
            continue
        rows.append({
            "date": date, "slot": slot, "format": fmt, "story": story,
            "sources": sources, "media": media, "url": m.group(0),
            "is_blog": story.startswith(BLOG_PREFIXES),
        })
    return rows

STOP = {"the", "a", "an", "of", "on", "in", "to", "for", "and", "with", "amid",
        "over", "from", "by", "s", "is", "are", "was", "as", "at", "its",
        "opinion", "analysis", "full", "extended", "news"}

def keywords(s):
    return {w.lower() for w in re.findall(r"[A-Za-z0-9&]+", s)
            if w.lower() not in STOP and len(w) > 2}

_SLOT_HOURS = {"morning": 8, "midday": 11, "afternoon": 14, "evening": 17,
               "night": 20, "manual": 12}

def rel_time(p):
    day = datetime.strptime(p["date"], "%Y-%m-%d").replace(tzinfo=IST)
    m = re.match(r"(morning|midday|afternoon|evening|night)", p["slot"].lower())
    posted = day.replace(hour=_SLOT_HOURS[m.group(1)] if m else 12)
    delta = datetime.now(IST) - posted
    mins = max(0, int(delta.total_seconds() // 60))
    if mins < 60:
        return f"{max(mins, 1)}m ago"
    if mins < 1440:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"

def _cap_title(d):
    p = os.path.join(POSTS, d, "caption.txt")
    if not os.path.exists(p):
        return ""
    t = open(p, encoding="utf-8").read().strip()
    return t.split("\n\n")[0].replace("\n", " ")

def find_post_dir(row):
    for prefix, dname in MANUAL_DIRS.items():
        if row["story"].startswith(prefix):
            return dname
    story = re.sub(r"^(OPINION|ANALYSIS|FROM THE HEART|EDUCATIONAL|NEWS):\s*",
                   "", row["story"])
    skw = keywords(story)
    cands = [d for d in os.listdir(POSTS) if d.startswith(row["date"])]
    # pass 1: match against the caption headline (most reliable)
    best, best_score = None, 0
    for d in cands:
        score = len(skw & keywords(_cap_title(d)))
        if score > best_score:
            best, best_score = d, score
    if best_score >= 2:
        return best
    # pass 2: match against directory-name keywords
    best, best_score = None, 0
    for d in cands:
        slug = d[len(row["date"]) + 1:]
        score = len(skw & keywords(slug.replace("-", " ")))
        if score > best_score:
            best, best_score = d, score
    return best if best_score >= 2 else None

def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:60] or "post"

def load_caption(dpath):
    cp = os.path.join(dpath, "caption.txt")
    if dpath and os.path.exists(cp):
        return open(cp, encoding="utf-8").read().strip()
    return ""

def parse_article(caption, row):
    """Split a caption into title, body paragraphs, source, visuals, hashtags."""
    title = re.sub(r"^(OPINION|ANALYSIS|FROM THE HEART|EDUCATIONAL|NEWS):\s*",
                   "", row["story"]).strip()
    paras, tags = [], []
    source, visuals = "", ""
    if caption:
        blocks = [b.strip() for b in caption.split("\n\n") if b.strip()]
        if blocks:
            title = blocks[0].replace("\n", " ").strip()
            blocks = blocks[1:]
        for b in blocks:
            bl = b.strip()
            if bl.startswith("Source:"):
                source = bl[len("Source:"):].strip()
            elif bl.startswith("Visuals:"):
                visuals = bl[len("Visuals:"):].strip()
            elif bl.startswith("#"):
                tags = re.findall(r"#\w+", bl)
            else:
                paras.append(bl)
    if not source:
        source = re.sub(r"\s*\(opened[^)]*\)", "", row["sources"]).strip()
    if not visuals:
        visuals = row["media"]
    # drop lingering source/visual lines from the body (shown in sourcebox)
    paras = [p for p in paras
             if not p.strip().startswith(("Source:", "Visuals:"))]
    return {"title": title, "paras": paras, "source": source,
            "visuals": visuals, "tags": tags}

def load_body_md(slug):
    """Full article body from content/<slug>.md. Returns list of blocks."""
    p = os.path.join(CONTENT, slug + ".md")
    if not os.path.exists(p):
        return None
    text = open(p, encoding="utf-8").read().strip()
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    return blocks or None

IMG_MD_RE = re.compile(r"^!\[(.*?)\]\((.*?)\)$")
SEP_CELL_RE = re.compile(r"^:?-{1,}:?$")

def _split_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]

def is_table_block(lines):
    lines = [l for l in lines if l.strip()]
    if len(lines) < 2:
        return False
    if not all(l.strip().startswith("|") for l in lines):
        return False
    sep = _split_row(lines[1])
    return bool(sep) and all(SEP_CELL_RE.match(c) for c in sep)

def render_table(lines):
    rows = [_split_row(l) for l in lines if l.strip()]
    header, sep, body = rows[0], rows[1], rows[2:]
    n = len(header)
    aligns = []
    for c in sep:
        if c.startswith(":") and c.endswith(":") and len(c) > 2:
            aligns.append("center")
        elif c.endswith(":"):
            aligns.append("right")
        else:
            aligns.append("left")
    def _cells(row, tag):
        tds = []
        for i in range(n):
            v = html.escape(row[i]) if i < len(row) else ""
            a = aligns[i] if i < len(aligns) else "left"
            tds.append(f"<{tag} style=\"text-align:{a}\">{v}</{tag}>")
        return "".join(tds)
    thead = f"<thead><tr>{_cells(header, 'th')}</tr></thead>"
    tbody = "".join(f"<tr>{_cells(r, 'td')}</tr>" for r in body)
    return f"<div class=\"tbl-wrap\"><table>{thead}<tbody>{tbody}</tbody></table></div>"

def is_list_block(lines):
    lines = [l for l in lines if l.strip()]
    return bool(lines) and all(re.match(r"^[-*]\s+", l.strip()) for l in lines)

def render_list(lines):
    items = "".join(
        f"<li>{html.escape(re.sub(r'^[-*]\s+', '', l.strip(), count=1))}</li>"
        for l in lines if l.strip())
    return f"<ul class=\"md-list\">{items}</ul>"

def render_body(blocks):
    out = []
    lede_done = False
    sec = 0
    for b in blocks:
        if b.startswith("## "):
            sec += 1
            out.append(f"<h2 id=\"sec-{sec}\">{html.escape(b[3:].strip())}</h2>")
        elif b.startswith("# "):
            continue  # title line, already rendered as h1
        elif (m := IMG_MD_RE.match(b)):
            alt, src = m.group(1).strip(), m.group(2).strip()
            out.append(f"<figure><img src=\"{html.escape(src)}\" alt=\"{html.escape(alt)}\""
                       f" loading=\"lazy\" decoding=\"async\"><figcaption>{html.escape(alt)}</figcaption></figure>")
            lede_done = True
        elif is_table_block(b.split("\n")):
            out.append(render_table(b.split("\n")))
        elif is_list_block(b.split("\n")):
            out.append(render_list(b.split("\n")))
        else:
            cls = "lede" if not lede_done else "body"
            lede_done = True
            out.append(f"<p class='{cls}'>{html.escape(b)}</p>")
    return "\n".join(out)

IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")

def post_images(dpath):
    """Return (hero_path, gallery_paths) for a post directory."""
    if not dpath:
        return None, []
    files = os.listdir(dpath)
    imgs = sorted(f for f in files if f.lower().endswith(IMG_EXTS))
    if not imgs:
        return None, []
    def pick(names):
        for n in names:
            if n in files:
                return os.path.join(dpath, n)
        return None
    hero = (pick(["cover.jpg", "cover.png", "slide-1.jpg", "slide-1.png",
                  "card-01.png", "slide1.png", "story.png"])
            or os.path.join(dpath, imgs[0]))
    gallery = [os.path.join(dpath, f) for f in imgs
               if f.startswith(("slide-", "slide", "card-")) and os.path.join(dpath, f) != hero]
    return hero, gallery

# ---------------------------------------------------------------- assets

def copy_image(src, rel_dst, max_w):
    dst = os.path.join(SITE, rel_dst)
    if os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
        return rel_dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    im = Image.open(src).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, int(im.height * max_w / im.width)), Image.LANCZOS)
    im.save(dst, quality=82)
    return rel_dst

PLACEHOLDER_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360">'
    '<rect width="640" height="360" fill="#0a1a3c"/>'
    '<text x="320" y="200" font-size="120" fill="#ff9933" text-anchor="middle" '
    'font-family="sans-serif">&#2357;</text></svg>')

# ---------------------------------------------------------------- css

BASE_CSS = """\
:root{
  --navy:#0a1a3c; --navy2:#12295c; --navy3:#1b3a7a;
  --saffron:#ff9933; --saffron2:#ffb35c;
  --ink:#1b2432; --muted:#5b6472; --bg:#f6f7fb; --card:#ffffff;
  --line:#e7eaf2; --radius:14px;
  --shadow:0 2px 12px rgba(10,26,60,.08);
  --shadow-lg:0 12px 32px rgba(10,26,60,.16);
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth;overflow-x:clip}
body{font-family:'Segoe UI',system-ui,-apple-system,Roboto,'Noto Sans',Arial,sans-serif;
  background:var(--bg);color:var(--ink);line-height:1.65;
  -webkit-font-smoothing:antialiased;overflow-x:clip}
img{max-width:100%}
a{color:inherit;text-decoration:none}

/* ---------- top bar ---------- */
.topbar{background:rgba(10,26,60,.96);backdrop-filter:blur(10px);color:#fff;
  padding:.65rem 1.2rem;display:flex;align-items:center;justify-content:space-between;
  position:sticky;top:0;z-index:50;box-shadow:0 2px 14px rgba(0,0,0,.25)}
.brand{display:flex;align-items:center;gap:.7rem}
.brand .wm{width:42px;height:42px;border-radius:50%;background:linear-gradient(135deg,var(--saffron),var(--saffron2));
  color:var(--navy);display:flex;align-items:center;justify-content:center;
  font-size:1.5rem;font-weight:700;box-shadow:0 0 0 3px rgba(255,153,51,.25)}
.brand h1{font-size:1.05rem;letter-spacing:.08em;line-height:1.15}
.brand h1 span{color:var(--saffron)}
.navlinks{display:flex;align-items:center;gap:1.4rem}
.navlinks a{font-size:.95rem;opacity:.92;position:relative;padding:.2rem 0;transition:color .2s;white-space:nowrap}
@media(max-width:1100px){.navlinks{gap:.9rem;overflow-x:auto;scrollbar-width:none}.navlinks::-webkit-scrollbar{display:none}}
.navlinks a::after{content:'';position:absolute;left:0;bottom:-2px;height:2px;width:0;
  background:var(--saffron);border-radius:2px;transition:width .25s}
.navlinks a:hover{color:var(--saffron)}
.navlinks a:hover::after{width:100%}
.navlinks a.active{color:var(--saffron)}
.navlinks a.active::after{width:100%}
.hamburger{display:none;background:none;border:0;color:#fff;font-size:1.6rem;cursor:pointer;padding:.3rem .5rem}

/* ---------- hero ---------- */
.hero{background:linear-gradient(120deg,var(--navy) 0%,var(--navy2) 55%,var(--navy3) 100%);
  background-size:200% 200%;animation:heroShift 14s ease-in-out infinite alternate;
  color:#fff;padding:clamp(2.5rem,6vw,4.5rem) 1.2rem;position:relative;overflow:hidden}
@keyframes heroShift{from{background-position:0% 40%}to{background-position:100% 60%}}
.hero::after{content:'\\0935';position:absolute;right:-40px;top:-80px;font-size:24rem;
  opacity:.055;color:#fff;pointer-events:none;animation:floaty 9s ease-in-out infinite}
@keyframes floaty{0%,100%{transform:translateY(0) rotate(-4deg)}50%{transform:translateY(26px) rotate(2deg)}}
.hero-inner{max-width:1100px;margin:0 auto;position:relative;z-index:1}
.hero .kicker{color:var(--saffron);text-transform:uppercase;letter-spacing:.22em;
  font-size:.78rem;margin-bottom:.9rem;font-weight:700;
  animation:fadeUp .7s ease both}
.hero h2{font-size:clamp(1.7rem,4.5vw,2.9rem);line-height:1.22;max-width:700px;
  margin-bottom:1rem;animation:fadeUp .7s .12s ease both}
.hero p{max-width:660px;opacity:.88;margin-bottom:1.6rem;font-size:1.02rem;
  animation:fadeUp .7s .24s ease both}
.hero .cta-row{display:flex;gap:.9rem;flex-wrap:wrap;animation:fadeUp .7s .36s ease both}
@keyframes fadeUp{from{opacity:0;transform:translateY(22px)}to{opacity:1;transform:none}}
.btn{display:inline-block;background:linear-gradient(135deg,var(--saffron),var(--saffron2));
  color:var(--navy);font-weight:700;padding:.75rem 1.5rem;border-radius:10px;
  transition:transform .18s,box-shadow .18s;box-shadow:0 4px 14px rgba(255,153,51,.35)}
.btn:hover{transform:translateY(-2px);box-shadow:0 8px 22px rgba(255,153,51,.45)}
.btn.ghost{background:rgba(255,255,255,.06);color:var(--saffron);
  border:2px solid var(--saffron);box-shadow:none}
.btn.ghost:hover{background:rgba(255,153,51,.14)}

/* ---------- layout ---------- */
.wrap{max-width:1100px;margin:0 auto;padding:2.4rem 1.2rem}
.wrap.narrow{max-width:780px}
.sec-head{display:flex;align-items:baseline;justify-content:space-between;
  gap:1rem;margin-bottom:1.3rem;flex-wrap:wrap}
.sec-title{font-size:clamp(1.3rem,3vw,1.6rem);display:flex;align-items:center;gap:.6rem;color:var(--navy)}
.sec-title::before{content:'';width:6px;height:1.35em;background:linear-gradient(var(--saffron),var(--saffron2));
  border-radius:3px;display:inline-block}
.sec-link{color:#c05f00;font-weight:700;font-size:.92rem}
.sec-link:hover{text-decoration:underline}

/* ---------- cards ---------- */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:1.4rem}
.card{background:var(--card);border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow);
  display:flex;flex-direction:column;transition:transform .22s,box-shadow .22s;border:1px solid var(--line)}
.card:hover{transform:translateY(-6px);box-shadow:var(--shadow-lg)}
.card .thumb{overflow:hidden;position:relative;display:block}
.card .thumb img{width:100%;aspect-ratio:16/9;object-fit:cover;background:var(--navy);
  transition:transform .5s ease;display:block}
.card:hover .thumb img{transform:scale(1.06)}
.card-body{padding:1.05rem 1.15rem 1.25rem;display:flex;flex-direction:column;gap:.55rem;flex:1}
.meta{font-size:.74rem;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;
  display:flex;gap:.55rem;align-items:center;flex-wrap:wrap}
.meta .tag{background:#eef1f7;border-radius:20px;padding:.12rem .65rem;color:var(--navy2);font-weight:700}
.meta .tag.opinion{background:#fff3e2;color:#b25e00}
.card h3{font-size:1.06rem;line-height:1.42}
.card h3 a{background-image:linear-gradient(var(--saffron),var(--saffron));
  background-size:0% 2px;background-repeat:no-repeat;background-position:left 96%;
  transition:background-size .3s}
.card h3 a:hover{background-size:100% 2px}
.card p{font-size:.9rem;color:var(--muted);flex:1}
.read{color:#c05f00;font-weight:700;font-size:.9rem}
.read:hover{color:var(--saffron)}

/* ---------- reveal on scroll ---------- */
.reveal{opacity:0;transform:translateY(26px);transition:opacity .6s ease,transform .6s ease}
.reveal.visible{opacity:1;transform:none}

/* ---------- about / footer ---------- */
.about{background:linear-gradient(135deg,var(--navy),var(--navy2));color:#fff;border-radius:18px;
  padding:clamp(1.6rem,4vw,2.6rem);margin-top:3rem;position:relative;overflow:hidden;box-shadow:var(--shadow-lg)}
.about::after{content:'\\0935';position:absolute;right:6px;bottom:-96px;font-size:17rem;opacity:.06;pointer-events:none}
.about h2{margin-bottom:.9rem;font-size:clamp(1.3rem,3vw,1.7rem)}
.about h2 span{color:var(--saffron)}
.about p{opacity:.9;max-width:720px;margin-bottom:.7rem}
footer{background:#060d20;color:#aab4cc;padding:2.2rem 1.2rem;margin-top:3.5rem;font-size:.88rem}
.foot-inner{max-width:1100px;margin:0 auto;display:flex;justify-content:space-between;gap:1rem;flex-wrap:wrap;align-items:center}
footer a{color:var(--saffron)}
footer a:hover{text-decoration:underline}

/* ---------- archive list ---------- */
.day-group{margin-bottom:2.4rem}
.day-group h3{font-size:1.12rem;color:var(--navy);margin-bottom:1rem;
  border-bottom:3px solid var(--saffron);display:inline-block;padding-bottom:.25rem}
.list-item{display:flex;gap:1rem;background:var(--card);border-radius:12px;padding:.8rem;
  margin-bottom:.85rem;box-shadow:var(--shadow);align-items:center;border:1px solid var(--line);
  transition:transform .2s,box-shadow .2s}
.list-item:hover{transform:translateX(4px);box-shadow:var(--shadow-lg)}
.list-item img{width:132px;height:92px;object-fit:cover;border-radius:9px;flex-shrink:0;background:var(--navy)}
.list-item .li-body{flex:1;min-width:0}
.list-item h4{font-size:1rem;line-height:1.4;margin-bottom:.25rem}
.list-item .meta{margin-bottom:.1rem}

/* ---------- article ---------- */
.progress{position:fixed;top:0;left:0;height:3px;width:0;z-index:60;
  background:linear-gradient(90deg,var(--saffron),var(--saffron2))}
.article-hero{width:100%;max-height:480px;object-fit:cover;border-radius:16px;
  background:var(--navy);margin:1.3rem 0 .6rem;box-shadow:var(--shadow-lg);
  animation:fadeUp .6s ease both}
.photo-credit{font-size:.78rem;color:var(--muted);margin:0 0 1.4rem;text-align:right}
.article h1{font-size:clamp(1.55rem,4vw,2.35rem);line-height:1.28;margin:.7rem 0 1.1rem;
  color:var(--navy);animation:fadeUp .6s .1s ease both}
.article h2{font-size:1.3rem;color:var(--navy);margin:1.9rem 0 .8rem;
  padding-left:.7rem;border-left:4px solid var(--saffron)}
.article p.lede{font-size:1.16rem;color:#2c3648;margin-bottom:1.3rem;font-weight:500}
.article p.body{margin-bottom:1.15rem;font-size:1.03rem;color:#2a3342}
.article .tbl-wrap{overflow-x:auto;margin:1.2rem 0;border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow)}
.article table{width:100%;border-collapse:collapse;font-size:.95rem;background:#fff}
.article th{background:var(--navy);color:#fff;padding:.65rem .9rem;font-weight:700;white-space:nowrap}
.article td{padding:.6rem .9rem;border-top:1px solid var(--line);vertical-align:top}
.article tbody tr:nth-child(even) td{background:#f8fafd}
.article ul.md-list{margin:0 0 1.15rem 1.2rem;font-size:1.03rem;color:#2a3342}
.article ul.md-list li{margin-bottom:.45rem}
html[data-theme="dark"] .article .tbl-wrap{border-color:#24355f}
html[data-theme="dark"] .article table{background:#101c3a}
html[data-theme="dark"] .article td{border-color:#24355f;color:#c2cadd}
html[data-theme="dark"] .article tbody tr:nth-child(even) td{background:#0c1730}
html[data-theme="dark"] .article ul.md-list{color:#c2cadd}
.byline{display:flex;align-items:center;gap:.7rem;margin:1rem 0 0;color:var(--muted);font-size:.9rem}
.byline .avatar{width:40px;height:40px;border-radius:50%;background:linear-gradient(135deg,var(--saffron),var(--saffron2));
  color:var(--navy);display:flex;align-items:center;justify-content:center;font-weight:800}
.opinion-badge{display:inline-block;background:#fff3e2;color:#b25e00;font-weight:800;
  font-size:.78rem;letter-spacing:.1em;text-transform:uppercase;border-radius:20px;padding:.3rem .9rem;margin-bottom:.4rem}
.sourcebox{background:#fff;border-left:5px solid var(--saffron);border-radius:10px;
  padding:1.1rem 1.3rem;margin:2rem 0;box-shadow:var(--shadow);font-size:.92rem}
.sourcebox div{margin-bottom:.35rem}
.sourcebox .lbl{font-weight:700;color:var(--navy2)}
.tagrow{display:flex;flex-wrap:wrap;gap:.5rem;margin:1.5rem 0}
.tagrow span{background:#eef1f7;color:var(--navy2);border-radius:20px;padding:.28rem .85rem;
  font-size:.82rem;font-weight:600}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:1rem;margin:1.8rem 0}
.gallery img{width:100%;border-radius:12px;box-shadow:var(--shadow);transition:transform .25s}
.gallery img:hover{transform:scale(1.03)}
.ig-cta{display:flex;gap:1.1rem;align-items:center;background:linear-gradient(135deg,var(--navy),var(--navy2));
  color:#fff;border-radius:14px;padding:1.3rem 1.5rem;margin:2.2rem 0;flex-wrap:wrap;box-shadow:var(--shadow-lg)}
.ig-cta p{flex:1;min-width:200px;margin:0}
.prevnext{display:flex;justify-content:space-between;gap:1rem;margin:2.4rem 0 1rem;flex-wrap:wrap}
.prevnext a{background:#fff;border-radius:12px;padding:1rem 1.15rem;box-shadow:var(--shadow);
  flex:1;min-width:230px;font-size:.93rem;border:1px solid var(--line);transition:transform .2s,border-color .2s}
.prevnext a:hover{transform:translateY(-3px);border-color:var(--saffron)}
.prevnext .dir{display:block;font-size:.74rem;color:var(--muted);text-transform:uppercase;
  letter-spacing:.09em;margin-bottom:.25rem;font-weight:700}
.totop{position:fixed;right:1.1rem;bottom:1.1rem;width:46px;height:46px;border-radius:50%;
  background:var(--navy);color:var(--saffron);border:0;font-size:1.3rem;cursor:pointer;
  box-shadow:var(--shadow-lg);opacity:0;pointer-events:none;transition:opacity .3s,transform .2s;z-index:55}
.totop.show{opacity:1;pointer-events:auto}
.totop:hover{transform:translateY(-3px)}

/* ---------- responsive ---------- */
@media(max-width:900px){
  .grid{grid-template-columns:repeat(auto-fill,minmax(260px,1fr))}
}
@media(max-width:720px){
  .hamburger{display:block}
  .navlinks{position:absolute;top:100%;left:0;right:0;background:rgba(10,26,60,.98);
    flex-direction:column;align-items:stretch;gap:0;padding:.4rem 1.2rem 1rem;
    display:none;box-shadow:0 12px 24px rgba(0,0,0,.3)}
  .navlinks.open{display:flex}
  .navlinks a{padding:.7rem 0;border-bottom:1px solid rgba(255,255,255,.08)}
  .navlinks a::after{display:none}
  .brand h1{font-size:.92rem}
  .hero{padding:2.2rem 1.1rem}
  .wrap{padding:1.8rem 1rem}
  .list-item img{width:104px;height:78px}
  .list-item h4{font-size:.93rem}
  .prevnext a{min-width:100%}
  .ig-cta{padding:1.1rem}
}
@media(max-width:420px){
  .grid{grid-template-columns:1fr}
  .card h3{font-size:1rem}
}
/* ---------- very small phones: keep the topbar inside 360px ---------- */
@media(max-width:480px){
  .topbar{padding:.55rem .7rem}
  .topbar>*{min-width:0}
  .brand{gap:.5rem}
  .brand .wm{width:34px;height:34px;font-size:1.2rem;flex-shrink:0}
  .brand h1{font-size:.78rem;white-space:nowrap}
  .top-actions{gap:.35rem}
  .top-search{display:none}
  .theme-toggle{width:34px;height:34px;font-size:.95rem}
  .hamburger{padding:.25rem .4rem}
  .lang-toggle{padding:.22rem .5rem;font-size:.72rem}
}
@media(prefers-reduced-motion:reduce){
  *,*::before,*::after{animation:none!important;transition:none!important}
  .reveal{opacity:1;transform:none}
  html{scroll-behavior:auto}
}

/* ---------- toc / share / related / search ---------- */
.toc{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:1rem 1.3rem;margin:0 0 1.6rem;box-shadow:var(--shadow)}
.toc strong{font-size:.85rem;text-transform:uppercase;letter-spacing:.08em;color:var(--saffron)}
.toc ul{margin:.6rem 0 0;padding-left:1.2rem}
.toc li{margin:.3rem 0;font-size:.95rem}
.toc a{color:var(--navy3)}
.toc a:hover{color:var(--saffron)}
.share-row{display:flex;align-items:center;gap:.55rem;flex-wrap:wrap;margin:1.8rem 0 0}
.share-row .lbl{font-size:.82rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:700}
.share-btn{display:inline-block;padding:.42rem .9rem;border-radius:999px;border:1px solid var(--line);
  background:var(--card);color:var(--navy);font-size:.88rem;font-weight:600;cursor:pointer;
  font-family:inherit;transition:all .2s;box-shadow:var(--shadow)}
.share-btn:hover{border-color:var(--saffron);color:var(--saffron);transform:translateY(-1px)}
.related{margin:2.6rem 0 0}
.related h2{font-size:1.35rem;margin-bottom:1rem;color:var(--navy)}
.related .grid{grid-template-columns:repeat(3,1fr)}
.search{margin-left:auto;padding:.55rem 1rem;border:1px solid var(--line);border-radius:999px;
  font-size:.9rem;font-family:inherit;background:var(--card);color:var(--ink);min-width:220px;
  box-shadow:var(--shadow);outline:none}
.search:focus{border-color:var(--saffron)}
.sec-head .search{margin-top:.4rem}
@media(max-width:640px){.search{margin-left:0;width:100%}.sec-head{flex-wrap:wrap}.related .grid{grid-template-columns:1fr}}

/* ---------- dark mode ---------- */
html[data-theme="dark"]{
  --bg:#0b1226; --card:#121b36; --ink:#e9edf6; --muted:#9aa4bd; --line:#223052;
  --shadow:0 2px 12px rgba(0,0,0,.45); --shadow-lg:0 12px 32px rgba(0,0,0,.55);
}
html[data-theme="dark"] .article h1,html[data-theme="dark"] .article h2,
html[data-theme="dark"] .sec-title,html[data-theme="dark"] .day-group h3,
html[data-theme="dark"] .latest-sidebar h2{color:#f2f5fc}
html[data-theme="dark"] .article p.lede{color:#cfd7ec}
html[data-theme="dark"] .article p.body{color:#c2cadd}
html[data-theme="dark"] .sourcebox{background:#121b36}
html[data-theme="dark"] .prevnext a{background:#121b36}
html[data-theme="dark"] .share-btn{background:#121b36;color:#e9edf6}
html[data-theme="dark"] .toc a{color:#9db8e8}
html[data-theme="dark"] .meta .tag{background:#1b2a52;color:#cfd7ec}
html[data-theme="dark"] .meta .tag.opinion{background:#3a2a12;color:#ffb35c}
html[data-theme="dark"] .tagrow span,html[data-theme="dark"] .tagchip{background:#1b2a52;color:#cfd7ec}
html[data-theme="dark"] .search{background:#121b36;color:#e9edf6}
html[data-theme="dark"] .opinion-badge{background:#3a2a12;color:#ffb35c}
html[data-theme="dark"] .sec-link,html[data-theme="dark"] .read{color:#ffb35c}
html[data-theme="dark"] .card p{color:#9aa4bd}
html[data-theme="dark"] .byline{color:#9aa4bd}
.theme-toggle{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.22);
  color:#fff;border-radius:50%;width:38px;height:38px;font-size:1.05rem;cursor:pointer;
  display:flex;align-items:center;justify-content:center;transition:background .2s,transform .2s}
.theme-toggle:hover{background:rgba(255,153,51,.25);transform:rotate(12deg)}
.top-actions{display:flex;align-items:center;gap:.5rem}

/* ---------- breaking news ticker ---------- */
.ticker{display:flex;align-items:stretch;background:#060d20;color:#fff;overflow:hidden;
  border-bottom:2px solid var(--saffron);max-width:100%}
.ticker-label{background:var(--saffron);color:var(--navy);font-weight:800;font-size:.78rem;
  text-transform:uppercase;letter-spacing:.1em;display:flex;align-items:center;
  padding:.5rem .9rem;flex-shrink:0}
.ticker-view{overflow:hidden;flex:1;min-width:0;display:flex;align-items:center}
.ticker-track{display:inline-block;white-space:nowrap;max-width:max-content;
  padding:.5rem 0;animation:tickmove 25s linear infinite;color:#ffd9a3;font-size:.92rem;font-weight:600}
.ticker-track:hover{animation-play-state:paused;color:var(--saffron)}
.ticker-track a{color:inherit;text-decoration:none}
.ticker-track a:hover{color:var(--saffron)}
.tick-sep{color:var(--saffron);font-weight:800}
@keyframes tickmove{from{transform:translateX(0)}to{transform:translateX(-50%)}}

/* ---------- topic tag chips ---------- */
.tagchip{background:#eef1f7;color:var(--navy2);border-radius:20px;padding:.12rem .65rem;
  font-size:.74rem;font-weight:700;letter-spacing:.04em}
.tagchip:hover{background:var(--saffron);color:var(--navy)}
.tagrow .tagchip{font-size:.82rem;padding:.28rem .85rem}

/* ---------- article layout + latest sidebar ---------- */
.article-layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:2.4rem;align-items:start}
.latest-sidebar{position:sticky;top:74px;background:var(--card);border:1px solid var(--line);
  border-radius:var(--radius);padding:1.1rem 1.1rem .6rem;box-shadow:var(--shadow)}
.latest-sidebar h2{font-size:1.02rem;color:var(--navy);margin-bottom:.8rem;
  display:flex;align-items:center;gap:.5rem}
.latest-sidebar h2::before{content:'';width:5px;height:1.2em;
  background:linear-gradient(var(--saffron),var(--saffron2));border-radius:3px}
.side-item{display:flex;gap:.7rem;align-items:center;padding:.55rem 0;border-top:1px solid var(--line)}
.side-item img{width:76px;height:56px;object-fit:cover;border-radius:8px;flex-shrink:0;background:var(--navy)}
.side-item h4{font-size:.84rem;line-height:1.35;font-weight:600}
.side-item a:hover h4{color:var(--saffron)}
.side-item .sdate{font-size:.72rem;color:var(--muted)}
@media(max-width:960px){.article-layout{grid-template-columns:1fr}.latest-sidebar{position:static}}

/* ---------- tags pages ---------- */
.tag-cloud{display:flex;flex-wrap:wrap;gap:.7rem;margin-top:1rem}
.tag-cloud .tagchip{font-size:.95rem;padding:.5rem 1.1rem}
.tag-count{opacity:.65;font-weight:400;font-size:.85em}

/* ---------- footer links ---------- */
.foot-links{display:flex;gap:1.1rem;flex-wrap:wrap}
@media(prefers-reduced-motion:reduce){.ticker-track{animation:none}}

/* ---------- scores center ---------- */
.scores-sub{color:var(--muted);font-size:.92rem;margin:-.6rem 0 1rem}
.scores-tabs{display:flex;gap:.5rem;flex-wrap:wrap;margin:0 0 1.1rem}
.scores-tab{background:var(--card);border:1px solid var(--line);color:var(--ink);
  border-radius:999px;padding:.5rem 1.15rem;font-size:.9rem;font-weight:700;cursor:pointer;font-family:inherit;
  transition:border-color .2s,color .2s,background .2s}
.scores-tab:hover{border-color:var(--saffron);color:var(--saffron)}
.scores-tab.active{background:var(--navy);color:#fff;border-color:var(--navy)}
.scores-bar{display:flex;align-items:center;gap:1rem;flex-wrap:wrap;margin-bottom:1.2rem}
.scores-bar:empty{display:none}
.league-select{padding:.5rem 1rem;border:1px solid var(--line);border-radius:999px;
  font-size:.9rem;font-family:inherit;background:var(--card);color:var(--ink);
  box-shadow:var(--shadow);outline:none;cursor:pointer}
.league-select:focus{border-color:var(--saffron)}
.updated{font-size:.82rem;color:var(--muted)}
.score-card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:1.05rem 1.2rem;margin-bottom:1rem;box-shadow:var(--shadow)}
.score-card.flash{border-color:var(--saffron)}
.score-head{display:flex;justify-content:space-between;gap:.8rem;align-items:center;margin-bottom:.6rem;flex-wrap:wrap}
.comp-name{font-size:.76rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:700}
.badge{font-size:.72rem;font-weight:800;border-radius:20px;padding:.24rem .8rem;
  letter-spacing:.06em;text-transform:uppercase;white-space:nowrap}
.badge.live{background:#d92d20;color:#fff;display:inline-flex;align-items:center;gap:.4rem}
.badge.upcoming{background:#eef1f7;color:var(--navy2)}
.badge.done{background:#e6f4ea;color:#1a7f37}
.live-pulse{width:8px;height:8px;border-radius:50%;background:#fff;animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.2}}
.live-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#ff4d4d;
  margin-left:.4rem;vertical-align:middle;animation:pulse 1.2s ease-in-out infinite}
.team-row{display:flex;align-items:center;gap:.85rem;padding:.5rem 0;border-top:1px solid var(--line)}
.team-row:first-of-type{border-top:0}
.team-logo{width:40px;height:40px;flex-shrink:0;border-radius:50%;background:var(--navy);
  display:flex;align-items:center;justify-content:center;overflow:hidden;border:1px solid var(--line)}
.team-logo img{width:100%;height:100%;object-fit:contain}
.team-logo.noimg img{display:none}
.team-init{display:none;color:#fff;font-weight:800;font-size:.8rem}
.team-logo.noimg .team-init{display:flex}
.team-name{flex:1;font-weight:600;font-size:.95rem}
.team-score{font-weight:800;font-size:1.02rem;font-variant-numeric:tabular-nums;white-space:nowrap}
.score-meta{font-size:.85rem;color:var(--muted);margin-top:.45rem}
.score-result{font-size:.9rem;font-weight:700;color:var(--navy2);margin-top:.4rem}
.session-row{display:flex;justify-content:space-between;gap:1rem;align-items:center;
  padding:.5rem 0;border-top:1px solid var(--line);font-size:.9rem;flex-wrap:wrap}
.session-row .sess{font-weight:700}
.empty-note,.error-note{background:var(--card);border:1px dashed var(--line);border-radius:var(--radius);
  padding:2.2rem 1.2rem;text-align:center;color:var(--muted);margin-bottom:1rem}
.empty-note strong,.error-note strong{color:var(--ink);display:block;margin-bottom:.4rem;font-size:1.02rem}
.retry-btn{margin-top:1rem;background:linear-gradient(135deg,var(--saffron),var(--saffron2));color:var(--navy);
  font-weight:700;border:0;border-radius:10px;padding:.6rem 1.4rem;cursor:pointer;font-family:inherit;font-size:.92rem}
.scores-loading{text-align:center;color:var(--muted);padding:2rem 0}
.scores-subhead{font-size:1.05rem;color:var(--navy);margin:1.6rem 0 .8rem;
  display:flex;align-items:center;gap:.55rem}
.scores-subhead::before{content:'';width:5px;height:1.2em;
  background:linear-gradient(var(--saffron),var(--saffron2));border-radius:3px}
.rider-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:.9rem}
.rider-card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:.9rem 1rem;box-shadow:var(--shadow);display:flex;gap:.9rem;align-items:center}
.rider-num{font-size:1.7rem;font-weight:800;color:var(--saffron);min-width:2.6rem;text-align:center;
  font-variant-numeric:tabular-nums}
.rider-name{font-weight:700;font-size:.95rem}
.rider-team{font-size:.82rem;color:var(--muted)}
.rider-country{font-size:.8rem;color:var(--muted);display:flex;align-items:center;gap:.4rem;margin-top:.15rem}
.rider-country img{width:20px;height:14px;object-fit:cover;border-radius:2px}
.honest-note{background:var(--card);border-left:5px solid var(--saffron);border-radius:10px;
  padding:1rem 1.2rem;margin:0 0 1.2rem;box-shadow:var(--shadow);font-size:.9rem;color:var(--muted)}
.wwe-card .score-result{color:var(--ink)}
.countdown{font-size:.85rem;color:var(--muted);font-weight:600}
@media(max-width:420px){
  .team-name{font-size:.88rem}
  .team-score{font-size:.94rem}
  .scores-tab{padding:.45rem .95rem;font-size:.84rem}
}
html[data-theme="dark"] .badge.upcoming{background:#1b2a52;color:#cfd7ec}
html[data-theme="dark"] .scores-tab.active{background:#1b2a52;border-color:#1b2a52}
html[data-theme="dark"] .score-result{color:#e9edf6}
html[data-theme="dark"] .scores-subhead{color:#f2f5fc}
  .team-name{font-size:.88rem}
  .team-score{font-size:.94rem}
  .scores-tab{padding:.45rem .95rem;font-size:.84rem}
}
html[data-theme="dark"] .badge.upcoming{background:#1b2a52;color:#cfd7ec}
html[data-theme="dark"] .scores-tab.active{background:#1b2a52;border-color:#1b2a52}
html[data-theme="dark"] .score-result{color:#e9edf6}
html[data-theme="dark"] .scores-subhead{color:#f2f5fc}
@media(prefers-reduced-motion:reduce){.live-pulse,.live-dot{animation:none}}

/* ---------- markets page ---------- */
.tv-wrap{background:#131722;border:1px solid var(--line,#e2e2e2);border-radius:14px;padding:.55rem .55rem .2rem;margin:1rem 0}
.tv-cap{color:#9aa3b2;font-size:.75rem;text-align:right;padding:.15rem .3rem .4rem}
.pm-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:.9rem;margin:1rem 0}
.pm-card{border:1px solid var(--line,#e2e2e2);border-radius:14px;background:var(--card,#fff);padding:1.1rem 1.2rem}
.pm-name{font-size:.8rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted,#666)}
.pm-inr{font-size:1.7rem;font-weight:800;margin:.3rem 0 .1rem}
.pm-usd{font-size:.85rem;color:var(--muted,#666)}
.mkt-badge{display:inline-block;font-size:.75rem;font-weight:700;padding:.2rem .7rem;border-radius:999px;background:#eee;color:#555;vertical-align:middle}
.mkt-badge.open{background:#e5f6ec;color:#0d7a3f}
.mkt-badge.closed{background:#f3e8e8;color:#a33}
html[data-theme="dark"] .mkt-badge.open{background:#123b26;color:#7be2a8}
html[data-theme="dark"] .mkt-badge.closed{background:#3b1a1a;color:#e08a8a}
.fx-box{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center;border:1px solid var(--line,#e2e2e2);border-radius:14px;background:var(--card,#fff);padding:1rem 1.2rem;margin:1rem 0}
.fx-box input,.fx-box select{font:inherit;padding:.5rem .8rem;border:1px solid var(--line,#e2e2e2);border-radius:10px;background:var(--bg,#fff);color:inherit}
.fx-box input{width:120px}
.fx-arrow{font-size:1.2rem;color:var(--muted,#666)}
.fx-out{font-weight:800;font-size:1.1rem;flex-basis:100%}

/* ---------- policy tracker ---------- */
.p-tools{display:flex;flex-wrap:wrap;gap:.7rem;align-items:center;margin:1rem 0}
.p-tabs{display:flex;flex-wrap:wrap;gap:.45rem}
.p-tab{border:1px solid var(--line,#e2e2e2);background:var(--card,#fff);color:inherit;border-radius:999px;padding:.4rem .9rem;font-size:.83rem;cursor:pointer;font-family:inherit;-webkit-user-select:none;user-select:none;touch-action:manipulation;-webkit-tap-highlight-color:transparent}
.p-tab.active{background:#1a1a1a;color:#fff;border-color:#1a1a1a}
@media (pointer:coarse){
  .p-tab{min-height:44px;padding:.65rem 1.15rem;font-size:.92rem}
  .p-tabs{touch-action:manipulation}
}
.p-search{margin-left:auto;border:1px solid var(--line,#e2e2e2);border-radius:999px;padding:.45rem 1rem;font-size:.85rem;background:var(--card,#fff);color:inherit;min-width:200px;font-family:inherit}
.p-list{display:grid;gap:.9rem}
.p-card{border:1px solid var(--line,#e2e2e2);border-radius:14px;background:var(--card,#fff);padding:1.1rem 1.25rem}
.p-card h3{margin:.4rem 0 .5rem;font-size:1.08rem}
.p-card p{margin:.4rem 0;color:var(--ink,#222);line-height:1.6}
.p-top{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}
.p-date{font-size:.78rem;color:var(--muted,#666)}
.p-status{font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;background:#eef3ff;color:#2b4acb;border-radius:999px;padding:.18rem .7rem}
.p-sectors{display:flex;flex-wrap:wrap;gap:.4rem;margin:.6rem 0 .2rem}
.chip{font-size:.74rem;background:var(--chip,#f1f1f1);border-radius:999px;padding:.2rem .7rem;color:var(--muted,#555)}
.p-src{font-size:.8rem;color:var(--muted,#666);margin-top:.4rem}
.p-src a{margin-right:.6rem}
html[data-theme="dark"] .p-tab.active{background:#e8e8e8;color:#111;border-color:#e8e8e8}
html[data-theme="dark"] .p-status{background:#1b2a52;color:#cfd7ec}

/* ---------- constitution articles browser ---------- */
.a-list{display:grid;gap:.9rem}
.a-card{border:1px solid var(--line,#e2e2e2);border-radius:14px;background:var(--card,#fff);padding:1.1rem 1.25rem}
.a-card h3{margin:.4rem 0 .5rem;font-size:1.08rem}
.a-card p{margin:.4rem 0;color:var(--ink,#222);line-height:1.6}
.a-top{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}
.a-num{font-size:.78rem;font-weight:700;color:#fff;background:var(--saffron,#e07b00);border-radius:999px;padding:.18rem .7rem}
.a-part{font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;background:#eef3ff;color:#2b4acb;border-radius:999px;padding:.18rem .7rem}
.a-rep{font-size:.72rem;font-weight:700;background:#fde8e8;color:#b3261e;border-radius:999px;padding:.18rem .7rem}
.a-ver{font-size:.72rem;font-weight:700;background:#fff4d6;color:#8a5a00;border-radius:999px;padding:.18rem .7rem}
.a-det{margin-top:.5rem}
.a-det summary{cursor:pointer;color:var(--saffron,#e07b00);font-weight:700;font-size:.85rem}
html[data-theme="dark"] .a-part{background:#1b2a52;color:#cfd7ec}
html[data-theme="dark"] .a-rep{background:#3a1d1d;color:#f2a8a0}
html[data-theme="dark"] .a-ver{background:#3a2f14;color:#f2d38a}

/* ---------- current affairs ---------- */
.ca-day{font-size:.85rem;color:var(--muted,#666)}
.ca-point{border:1px solid var(--line,#e2e2e2);border-radius:14px;background:var(--card,#fff);padding:1rem 1.25rem;margin-bottom:.8rem}
.ca-point h3{margin:.2rem 0 .4rem;font-size:1.02rem}
.ca-point p{margin:.3rem 0;color:var(--ink,#222);line-height:1.6;font-size:.94rem}
.ca-why{font-size:.88rem;color:var(--muted,#555)}
.ca-src{font-size:.8rem;margin-top:.3rem}

/* ---------- study hub ---------- */
.study-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1rem;margin:1.2rem 0}
.study-card{display:block;border:1px solid var(--line,#e2e2e2);border-radius:14px;background:var(--card,#fff);padding:1.3rem 1.4rem;text-decoration:none;color:inherit}
.study-card h3{margin:.2rem 0 .6rem}
.study-card p{color:var(--muted,#555);line-height:1.6;margin:.4rem 0 .8rem}
.study-card .go{font-weight:700;font-size:.88rem}
.study-card:hover{border-color:#999}
/* ---------- today page ---------- */
.wx-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:1rem;margin:1.2rem 0}
.wx-card{border:1px solid var(--line,#e2e2e2);border-radius:14px;background:var(--card,#fff);padding:1rem 1.1rem}
.wx-city{font-weight:800}
.wx-temp{font-size:1.6rem;font-weight:800;margin:.3rem 0}
.wx-cond{font-size:.9rem;color:var(--muted,#666)}
.wx-meta{font-size:.8rem;color:var(--muted,#666);margin-top:.3rem}
.aqi{display:inline-block;margin-top:.5rem;font-size:.78rem;font-weight:700;padding:.2rem .6rem;border-radius:999px}
.aqi.good{background:#dff5e1;color:#166534}.aqi.mod{background:#fef9c3;color:#854d0e}.aqi.usg{background:#ffedd5;color:#9a3412}.aqi.unh{background:#fecaca;color:#991b1b}.aqi.vun{background:#e9d5ff;color:#6b21a8}.aqi.haz{background:#7f1d1d;color:#fff}
.otd-list{display:grid;gap:.9rem;margin:1.2rem 0}
.otd-item{border:1px solid var(--line,#e2e2e2);border-radius:14px;background:var(--card,#fff);padding:1rem 1.2rem}
.otd-year{font-weight:800;color:var(--saffron);font-size:1.05rem}
.otd-item p{margin:.3rem 0 .5rem}
/* ---------- quiz ---------- */
.quiz-wrap{max-width:680px;margin:1.2rem 0;border:1px solid var(--line,#e2e2e2);border-radius:16px;background:var(--card,#fff);padding:1.6rem}
.quiz-progress{font-size:.85rem;color:var(--muted,#666);margin-bottom:.4rem}
.quiz-bar{height:8px;background:var(--line,#e2e2e2);border-radius:4px;overflow:hidden;margin-bottom:1rem}
.quiz-bar i{display:block;height:100%;background:var(--saffron);transition:width .3s}
.quiz-q{font-size:1.15rem;margin:.4rem 0 1rem}
.quiz-opts{display:grid;gap:.6rem;margin-bottom:1rem}
.quiz-opt{text-align:left;font:inherit;padding:.7rem 1rem;border:1px solid var(--line,#e2e2e2);border-radius:10px;background:var(--bg,#fff);color:inherit;cursor:pointer}
.quiz-opt:hover:not(:disabled){border-color:var(--saffron)}
.quiz-opt.correct{background:#dff5e1;border-color:#166534}
.quiz-opt.wrong{background:#fecaca;border-color:#991b1b}
.quiz-explain{background:var(--bg,#f7f7f7);border-radius:10px;padding:.8rem 1rem;font-size:.92rem}
.quiz-done{text-align:center}
.quiz-score{font-size:2.6rem;font-weight:800;color:var(--saffron)}

/* ---------- horoscope page ---------- */
.sign-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:.6rem;margin:1.2rem 0}
.sign-card{border:1px solid var(--line,#e2e2e2);border-radius:12px;background:var(--card,#fff);padding:.7rem .4rem;text-align:center;cursor:pointer;transition:transform .15s,border-color .15s;font:inherit}
.sign-card:hover{transform:translateY(-2px)}
.sign-card.active{border-color:var(--saffron,#e07b00);box-shadow:0 0 0 2px var(--saffron,#e07b00) inset}
.sign-card .sym{font-size:1.7rem;line-height:1}
.sign-card .sname{font-size:.82rem;font-weight:700;margin-top:.25rem}
.sign-card .sdates{font-size:.68rem;color:var(--muted,#666)}
.day-tabs{display:flex;gap:.5rem;margin:1rem 0}
.day-tab{border:1px solid var(--line,#e2e2e2);background:var(--card,#fff);border-radius:999px;padding:.45rem 1.1rem;font:inherit;font-size:.88rem;cursor:pointer;color:inherit}
.day-tab.active{background:var(--saffron,#e07b00);border-color:var(--saffron,#e07b00);color:#fff;font-weight:700}
.horo-card{border:1px solid var(--line,#e2e2e2);border-radius:14px;background:var(--card,#fff);padding:1.4rem;margin:1rem 0}
.horo-card h2{margin:0 0 .3rem;font-size:1.5rem}
.horo-card .horo-date{font-size:.85rem;color:var(--muted,#666);margin-bottom:.8rem}
.horo-card p.horo-text{font-size:1.05rem;line-height:1.75}
.horo-meta{font-size:.8rem;color:var(--muted,#666);margin-top:1rem}
.horo-note{font-size:.82rem;color:var(--muted,#666);font-style:italic;margin-top:.4rem}
html[data-theme="dark"] .sign-card,html[data-theme="dark"] .day-tab,html[data-theme="dark"] .horo-card{background:#131a2c;border-color:#263050}
html[data-theme="dark"] .sign-card .sdates,html[data-theme="dark"] .horo-card .horo-date,html[data-theme="dark"] .horo-meta,html[data-theme="dark"] .horo-note{color:#9aa3b8}

/* ---------- read-aloud player ---------- */
.listen-cta{display:inline-flex;align-items:center;gap:.45rem;border:1px solid var(--saffron,#e07b00);background:transparent;color:var(--saffron,#e07b00);border-radius:999px;padding:.5rem 1.15rem;font:inherit;font-size:.9rem;font-weight:700;cursor:pointer;margin:.6rem 0 0}
.listen-cta:hover{background:var(--saffron,#e07b00);color:#fff}
.listen-cta .spk{font-size:1.05rem}
.readaloud{position:fixed;left:0;right:0;bottom:0;z-index:60;background:var(--card,#fff);border-top:2px solid var(--saffron,#e07b00);box-shadow:0 -6px 24px rgba(0,0,0,.18);padding:.7rem 1rem calc(.7rem + env(safe-area-inset-bottom));display:none}
.readaloud.open{display:block}
.ra-inner{max-width:720px;margin:0 auto}
.ra-title{font-size:.82rem;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:.4rem}
.ra-controls{display:flex;align-items:center;gap:.55rem;flex-wrap:wrap}
.ra-btn{border:1px solid var(--line,#e2e2e2);background:var(--bg,#f7f7f7);color:inherit;border-radius:50%;width:42px;height:42px;font-size:1.1rem;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;flex:none}
.ra-btn.primary{background:var(--saffron,#e07b00);border-color:var(--saffron,#e07b00);color:#fff;width:52px;height:52px;font-size:1.35rem}
.ra-select{font:inherit;font-size:.82rem;border:1px solid var(--line,#e2e2e2);border-radius:8px;padding:.35rem .5rem;background:var(--bg,#f7f7f7);color:inherit;max-width:170px}
.ra-progress{flex:1 1 120px;height:6px;background:var(--line,#e2e2e2);border-radius:3px;overflow:hidden;min-width:90px}
.ra-progress i{display:block;height:100%;width:0;background:var(--saffron,#e07b00);transition:width .3s}
.ra-count{font-size:.78rem;color:var(--muted,#666);white-space:nowrap}
.ra-close{margin-left:auto;border:none;background:transparent;font-size:1.2rem;cursor:pointer;color:var(--muted,#666)}
.ra-sent{background:rgba(224,123,0,.18);border-radius:3px}
html[data-theme="dark"] .readaloud{background:#131a2c;border-top-color:var(--saffron,#e07b00)}
html[data-theme="dark"] .ra-btn{background:#0f1526;border-color:#263050;color:#e9edf6}
html[data-theme="dark"] .ra-btn.primary{background:var(--saffron,#e07b00);border-color:var(--saffron,#e07b00);color:#fff}
html[data-theme="dark"] .ra-select{background:#0f1526;border-color:#263050;color:#e9edf6}
html[data-theme="dark"] .ra-sent{background:rgba(224,123,0,.32)}

/* ---------- 2026 redesign: typography, cards, hero, dark polish ---------- */
h1,h2,h3{letter-spacing:-.015em;line-height:1.25;text-wrap:balance}
body{font-size:1rem}
.wrap{max-width:1120px}
.hero{border-bottom:1px solid rgba(255,153,51,.25)}
.hero::before{content:'';position:absolute;left:-120px;bottom:-160px;width:420px;height:420px;
  background:radial-gradient(circle,rgba(255,153,51,.16),transparent 65%);pointer-events:none}
.hero .kicker{display:flex;align-items:center;gap:.6rem}
.hero .kicker::before{content:'';width:26px;height:2px;background:var(--saffron);border-radius:2px}
.hero h2{font-weight:800;letter-spacing:-.02em}
.card{border-top:3px solid transparent}
.card:hover{border-color:var(--line);border-top-color:var(--saffron)}
.card .thumb::after{content:'';position:absolute;inset:auto 0 0 0;height:38%;
  background:linear-gradient(transparent,rgba(6,13,32,.28));opacity:0;transition:opacity .3s;pointer-events:none}
.card:hover .thumb::after{opacity:1}
.card h3{font-size:1.08rem;font-weight:700}
.card p{line-height:1.6}
.grid>.reveal:nth-child(2){transition-delay:.06s}
.grid>.reveal:nth-child(3){transition-delay:.12s}
.grid>.reveal:nth-child(4){transition-delay:.18s}
.grid>.reveal:nth-child(5){transition-delay:.24s}
.grid>.reveal:nth-child(6){transition-delay:.3s}
.article p.body{font-size:1.05rem;line-height:1.78;color:#272f3e}
.article p.lede{line-height:1.6}
.sec-title{font-weight:800}
html[data-theme="dark"] .card{border-color:#223052}
html[data-theme="dark"] .card:hover{border-top-color:var(--saffron)}
html[data-theme="dark"] .article p.body{color:#c9d1e4}
html[data-theme="dark"] .hero{border-bottom-color:rgba(255,153,51,.2)}

/* ---------- topbar search ---------- */
.top-search{position:relative}
.top-search input{width:170px;max-width:38vw;min-height:38px;padding:.45rem .9rem;border-radius:999px;
  border:1px solid rgba(255,255,255,.25);background:rgba(255,255,255,.1);color:#fff;
  font-size:.85rem;font-family:inherit;outline:none;transition:border-color .2s,background .2s}
.top-search input::placeholder{color:rgba(255,255,255,.65)}
.top-search input:focus{border-color:var(--saffron);background:rgba(255,255,255,.16)}
.search-results{position:absolute;top:calc(100% + 10px);right:0;width:min(360px,84vw);
  background:var(--card);border:1px solid var(--line);border-radius:12px;
  box-shadow:var(--shadow-lg);overflow:hidden;z-index:80;max-height:60vh;overflow-y:auto}
.sr-item{display:block;padding:.7rem .95rem;border-bottom:1px solid var(--line);color:var(--ink)}
.sr-item:last-child{border-bottom:0}
.sr-item:hover{background:rgba(255,153,51,.08)}
.sr-type{display:block;font-size:.68rem;text-transform:uppercase;letter-spacing:.09em;
  color:var(--saffron);font-weight:800;margin-bottom:.15rem}
.sr-title{font-size:.9rem;font-weight:600;line-height:1.4}
.sr-empty{padding:.9rem 1rem;color:var(--muted);font-size:.88rem}
html[data-theme="dark"] .top-search input{background:rgba(255,255,255,.07)}

/* ---------- videos page ---------- */
.video-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:1.4rem}
.video-card .vthumb{display:block;position:relative;aspect-ratio:4/5;overflow:hidden;background:var(--navy)}
.video-card .vthumb img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .5s ease}
.video-card:hover .vthumb img{transform:scale(1.05)}
.vthumb-fallback{display:flex;align-items:center;justify-content:center;width:100%;height:100%;
  background:linear-gradient(135deg,var(--navy),var(--navy3));color:var(--saffron);font-size:3rem}
.video-card .play{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  width:58px;height:58px;border-radius:50%;background:rgba(255,153,51,.92);color:var(--navy);
  display:flex;align-items:center;justify-content:center;font-size:1.4rem;
  box-shadow:0 6px 20px rgba(0,0,0,.35);transition:transform .2s;pointer-events:none}
.video-card:hover .play{transform:translate(-50%,-50%) scale(1.1)}
.page-head{margin-bottom:1.6rem}
.page-head h1{font-size:clamp(1.7rem,4vw,2.4rem);color:var(--navy);font-weight:800;margin-bottom:.5rem}
.page-head .lede{color:var(--muted);max-width:640px;font-size:1.02rem}
html[data-theme="dark"] .page-head h1{color:#f2f5fc}

/* ---------- newsletter in footer ---------- */
.nl-block{flex:1 1 280px;max-width:360px}
.nl-block strong{color:#fff;font-size:.95rem}
.nl-block p{margin:.35rem 0 .6rem;font-size:.85rem}
.nl-form{display:flex;gap:.5rem;flex-wrap:wrap}
.nl-form input{flex:1;min-width:170px;min-height:44px;padding:.55rem .95rem;border-radius:10px;
  border:1px solid #2a3a63;background:#0d1730;color:#e9edf6;font-size:.9rem;font-family:inherit;outline:none}
.nl-form input:focus{border-color:var(--saffron)}
.nl-form .btn{padding:.55rem 1.2rem;min-height:44px;border:0;cursor:pointer;font-size:.9rem}
.nl-note{font-size:.76rem!important;opacity:.75}

/* ---------- tap targets on touch ---------- */
@media(pointer:coarse){
  .share-btn{min-height:44px;display:inline-flex;align-items:center}
  .navlinks a{padding:.75rem 0}
  .top-search input{min-height:42px}
  .sec-link{min-height:44px;display:inline-flex;align-items:center}
}
/* ---------- language toggle ---------- */
.lang-toggle{display:inline-flex;align-items:center;gap:.3rem;background:transparent;
border:1.5px solid var(--saffron);color:#fff;border-radius:999px;padding:.3rem .7rem;
font:inherit;font-size:.82rem;font-weight:800;cursor:pointer;white-space:nowrap}
.lang-toggle .lt-en,.lang-toggle .lt-hi{opacity:.5;transition:opacity .2s}
.lang-toggle .lt-sep{opacity:.4}
.lang-toggle[data-lang="en"] .lt-en{opacity:1;color:var(--saffron)}
.lang-toggle[data-lang="hi"] .lt-hi{opacity:1;color:var(--saffron)}
.lang-toggle:hover{border-color:var(--saffron2)}
@media(max-width:720px){.lang-toggle{padding:.25rem .55rem;font-size:.75rem}}
/* ---------- trending strip ---------- */
.trend-strip{margin:1.4rem 0 .4rem;min-width:0;max-width:100%}
.trend-head{display:flex;align-items:center;gap:.6rem;margin:0 .2rem .8rem}
.trend-label{background:var(--saffron);color:var(--navy);font-weight:800;
  font-size:.78rem;letter-spacing:.06em;text-transform:uppercase;
  padding:.3rem .8rem;border-radius:20px}
.trend-scroll{display:flex;gap:.9rem;overflow-x:auto;scroll-snap-type:x mandatory;
  -webkit-overflow-scrolling:touch;padding:.2rem .2rem 1rem;
  scrollbar-width:thin;max-width:100%}
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
/* ---------- fact check verdicts ---------- */
.verdict{display:inline-block;font-size:.72rem;font-weight:800;letter-spacing:.08em;
  text-transform:uppercase;border-radius:999px;padding:.25rem .8rem;color:#fff}
.verdict-true{background:#1e7f3f}
.verdict-misleading,.verdict-unverified{background:#b25f09}
.verdict-false{background:#b3212c}
html[data-theme="dark"] .verdict-true{color:#2fa85c;background:rgba(47,168,92,.15);border:1px solid #2fa85c}
html[data-theme="dark"] .verdict-misleading,html[data-theme="dark"] .verdict-unverified{color:#f5a623;background:rgba(245,166,35,.12);border:1px solid #f5a623}
html[data-theme="dark"] .verdict-false{color:#ff6b6b;background:rgba(255,107,107,.12);border:1px solid #ff6b6b}
.verdict-legend{display:flex;flex-wrap:wrap;gap:.6rem;margin:1rem 0 1.6rem}
.fc-card{margin-bottom:1.4rem}
.fc-card details{margin-top:.8rem}
.fc-card summary{cursor:pointer;font-weight:700;color:var(--saffron);min-height:44px;display:inline-flex;align-items:center}
.fc-card ul{margin:.5rem 0}
/* ---------- timeline ---------- */
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
/* ---------- topics ---------- */
.topic-cloud{display:flex;flex-wrap:wrap;gap:.7rem;margin:2rem 0 1rem}
.topic-cloud .tagchip{font-size:.85rem;padding:.4rem 1rem}
/* ---------- reader: bookmarks + progress ---------- */
#read-progress{position:fixed;top:0;left:0;height:3px;width:0;z-index:70;
  background:linear-gradient(90deg,var(--saffron),var(--saffron2));}
.vs-bookmark-slot{display:inline-flex;vertical-align:middle;}
.vs-bookmark{display:inline-flex;align-items:center;justify-content:center;
  min-width:44px;min-height:44px;padding:0;border:0;background:transparent;
  color:var(--navy);cursor:pointer;border-radius:8px;}
.vs-bookmark:hover{background:rgba(255,153,51,.12);}
.vs-bookmark svg{display:block;}
.vs-bookmark.on{color:var(--saffron);}
.vs-bookmark.on svg path{fill:currentColor;}
.vs-bookmark-list .vs-remove,
.vs-bookmark-actions .vs-clear{display:inline-block;margin-top:.6rem;
  padding:.6rem 1rem;min-height:44px;border:1px solid var(--saffron);
  background:transparent;color:var(--saffron);border-radius:8px;
  font-size:.85rem;cursor:pointer;}
.vs-bookmark-list .vs-remove:hover,
.vs-bookmark-actions .vs-clear:hover{background:rgba(255,153,51,.12);}
.vs-bookmark-actions{margin-top:1rem;}
/* ---------- Hindi (Devanagari) ---------- */
html[lang="hi"] body{font-family:'Noto Sans Devanagari','Noto Sans','Segoe UI',system-ui,-apple-system,Roboto,Arial,sans-serif;}
"""

JS = """\
<script>
(function(){
  var nav=document.getElementById('navlinks'),burger=document.getElementById('burger');
  if(burger&&nav){burger.addEventListener('click',function(){nav.classList.toggle('open')});}
  var io=('IntersectionObserver' in window)?new IntersectionObserver(function(es){
    es.forEach(function(e){if(e.isIntersecting){e.target.classList.add('visible');io.unobserve(e.target);}});
  },{threshold:.12}):null;
  document.querySelectorAll('.reveal').forEach(function(el){if(io)io.observe(el);else el.classList.add('visible');});
  var bar=document.getElementById('progress'),top=document.getElementById('totop');
  function onScroll(){
    var h=document.documentElement,sc=h.scrollTop/(h.scrollHeight-h.clientHeight||1);
    if(bar)bar.style.width=(sc*100)+'%';
    if(top)top.classList.toggle('show',h.scrollTop>600);
  }
  window.addEventListener('scroll',onScroll,{passive:true});onScroll();
  if(top)top.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'});});
  var themeBtn=document.getElementById('theme-toggle'),themeIcon=document.getElementById('theme-icon');
  function paintThemeIcon(){var dark=document.documentElement.getAttribute('data-theme')==='dark';
    if(themeIcon)themeIcon.innerHTML=dark?'&#9788;':'&#9789;';}
  if(themeBtn){paintThemeIcon();themeBtn.addEventListener('click',function(){
    var cur=document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark';
    document.documentElement.setAttribute('data-theme',cur);
    try{localStorage.setItem('vs-theme',cur);}catch(e){}
    paintThemeIcon();});}
})();
function copyPageLink(btn){
  var done=function(){btn.textContent='Copied!';setTimeout(function(){btn.textContent='Copy link';},1500);};
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(location.href).then(done,function(){done();});
  }else{
    var t=document.createElement('textarea');t.value=location.href;
    document.body.appendChild(t);t.select();
    try{document.execCommand('copy');}catch(e){}
    document.body.removeChild(t);done();
  }
}
document.addEventListener('input',function(e){
  if(e.target&&e.target.id==='sitesearch'){
    var q=e.target.value.trim().toLowerCase();
    document.querySelectorAll('.card,.list-item').forEach(function(el){
      var t=(el.getAttribute('data-search')||el.textContent).toLowerCase();
      el.style.display=(!q||t.indexOf(q)>-1)?'':'none';
    });
  }
});
/* Site-wide search: topbar box searches assets/search-index.json client-side. */
(function(){
  var inp=document.getElementById('topsearch'),box=document.getElementById('search-results');
  if(!inp||!box||!('fetch' in window))return;
  var idx=null;
  function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function load(){
    if(idx!==null)return Promise.resolve(idx);
    return fetch(inp.getAttribute('data-index'),{cache:'force-cache'}).then(function(r){
      if(!r.ok)throw new Error('http '+r.status);return r.json();
    }).then(function(j){idx=j;return idx;}).catch(function(){idx=[];return idx;});
  }
  function render(q){
    var words=q.toLowerCase().split(/\s+/).filter(Boolean);
    var res=idx.filter(function(e){
      var t=((e.title||'')+' '+(e.excerpt||'')).toLowerCase();
      return words.every(function(w){return t.indexOf(w)>-1;});
    }).slice(0,8);
    box.innerHTML=res.length?res.map(function(e){
      return '<a class="sr-item" href="'+esc(e.url)+'"><span class="sr-type">'+esc(e.type)+'</span><span class="sr-title">'+esc(e.title)+'</span></a>';
    }).join(''):'<div class="sr-empty">No matches found.</div>';
    box.hidden=false;
  }
  var t=null;
  inp.addEventListener('input',function(){
    var q=inp.value.trim();
    clearTimeout(t);
    if(!q){box.hidden=true;box.innerHTML='';return;}
    t=setTimeout(function(){load().then(function(){render(q);});},120);
  });
  document.addEventListener('click',function(e){
    if(!e.target.closest||!e.target.closest('.top-search'))box.hidden=true;
  });
  inp.addEventListener('keydown',function(e){if(e.key==='Escape'){box.hidden=true;inp.blur();}});
})();
/* Newsletter: static-friendly mailto with prefilled subscribe request. */
document.addEventListener('submit',function(e){
  var f=e.target;
  if(f&&f.id==='nl-form'){
    e.preventDefault();
    var em=f.querySelector('input[type=email]').value.trim();
    var to=f.getAttribute('data-email')||'';
    var href='mailto:'+to+'?subject='+encodeURIComponent('Subscribe to Varta & Samkara')+
      '&body='+encodeURIComponent('Hi, please subscribe '+em+' to the Varta & Samkara newsletter. Thanks!');
    window.location.href=href;
  }
});
/* Scores Center: pulsing dot on the Scores nav link when something is live.
   Cheap by design: two small scoreboard fetches, once per page load. */
window.addEventListener('load',function(){
  setTimeout(function(){
    var link=document.querySelector('[data-scores-nav]');
    if(!link||!('fetch' in window))return;
    function anyLive(url){
      return fetch(url).then(function(r){return r.json();}).then(function(d){
        return (d.events||[]).some(function(e){
          return e.status&&e.status.type&&e.status.type.state==='in';
        });
      }).catch(function(){return false;});
    }
    Promise.all([
      anyLive('https://site.api.espn.com/apis/site/v2/sports/cricket/8048/scoreboard'),
      anyLive('https://site.api.espn.com/apis/site/v2/sports/soccer/ind.1/scoreboard')
    ]).then(function(r){
      if(r[0]||r[1]){
        var s=document.createElement('span');
        s.className='live-dot';s.title='Live now';
        link.appendChild(s);
      }
    });
  },1600);
});
</script>"""

# ---------------------------------------------------------------- scores center JS

SCORES_JS = """\
(function(){
'use strict';
var ESPN='https://site.api.espn.com/apis/site/v2/sports';
var IST=new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',weekday:'short',day:'numeric',month:'short',hour:'numeric',minute:'2-digit',hour12:true});

/* WWE Premium Live Events, verified against multiple reports (sportsbrackets.net,
   khelnow.com, sacnilk.com, Sep-Oct 2026). Dates can shift; check wwe.com. */
var WWE_EVENTS=[
  {name:'Money in the Bank',date:'2026-10-10T22:00:00Z',venue:'Smoothie King Center, New Orleans, Louisiana'},
  {name:'Crown Jewel',date:'2026-11-07T18:00:00Z',venue:'Riyadh Season Stadium at KAFD, Riyadh, Saudi Arabia'},
  {name:'Survivor Series: WarGames',date:'2026-11-28T23:00:00Z',venue:'Daikin Park, Houston, Texas'}
];
/* Boxing has no free live-score feed. Next major bouts, verified from
   Reuters, DAZN, Bad Left Hook and British Boxing News (Oct 2026). */
var BOXING=[
  {bout:'Daniel Dubois vs Fabio Wardley 2',title:'WBO heavyweight title rematch',date:'17 Oct 2026',venue:'O2 Arena, London',note:'DAZN PPV'},
  {bout:'Canelo Alvarez vs Christian Mbilli',title:'WBC super middleweight title',date:'31 Oct 2026',venue:'Venue TBA',note:'Halloween night'},
  {bout:'Agit Kabayel vs Nelson Hysa',title:'WBC heavyweight title',date:'28 Nov 2026',venue:'Merkur Spiel-Arena, Dusseldorf',note:'DAZN'}
];

var LEAGUES={
  cricket:[{id:'8048',name:'IPL'},{id:'8044',name:'Big Bash League'},{id:'8046',name:'State League T20'},{id:'8043',name:'Sheffield Shield'}],
  football:[{id:'ind.1',name:'Indian Super League'},{id:'eng.1',name:'Premier League'},{id:'esp.1',name:'La Liga'},{id:'uefa.champions',name:'Champions League'}]
};
var TAB_IDS=['cricket','football','f1','ufc','motogp','wwe'];
var panel,bar,leagueSel,updatedEl;
var current='cricket',leaguePick={},timer=null,lastFetch=0,lastAnyLive=false,agoTimer=null;

function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function fmtIST(iso){try{return IST.format(new Date(iso));}catch(e){return '';}}
function stateOf(e){return (e.status&&e.status.type)||{};}

function badgeFor(st){
  if(st.state==='in')return '<span class="badge live"><span class="live-pulse"></span>Live</span>';
  if(st.state==='post')return '<span class="badge done">'+esc(st.description||'Final')+'</span>';
  return '<span class="badge upcoming">'+esc(st.description||'Scheduled')+'</span>';
}
function teamRow(c){
  var t=c.team||{},name=t.displayName||'TBD',abbr=t.abbreviation||'',
      score=(c.score==null||c.score==='')?'':c.score,logo=t.logo||'',
      init=esc((abbr||name).slice(0,2).toUpperCase());
  return '<div class="team-row"><span class="team-logo'+(logo?'':' noimg')+'">'+
    (logo?'<img src="'+esc(logo)+'" alt="" loading="lazy" onerror="this.parentNode.classList.add(\\'noimg\\');this.remove()">':'')+
    '<span class="team-init">'+init+'</span></span>'+
    '<span class="team-name">'+esc(name)+'</span>'+
    '<span class="team-score">'+esc(String(score))+'</span></div>';
}
function emptyNote(){return '<div class="empty-note"><strong>No matches right now.</strong>Check back soon, the board refreshes automatically.</div>';}
function errorNote(){return '<div class="error-note"><strong>Could not load scores.</strong>Check your connection and try again.<br><button class="retry-btn" id="scores-retry" type="button">Retry</button></div>';}

function eventCard(e,compName){
  var st=stateOf(e),comp=(e.competitions&&e.competitions[0])||{};
  var rows=((comp.competitors)||[]).map(teamRow).join('');
  var when=st.state==='pre'?'<div class="score-meta">'+esc(fmtIST(e.date))+' IST</div>':'';
  var venue=(comp.venue&&comp.venue.fullName)?'<div class="score-meta">'+esc(comp.venue.fullName)+'</div>':'';
  var result='';
  if(st.state==='post'){
    var w=(comp.competitors||[]).filter(function(c){return c.winner;})[0];
    if(w&&w.team)result='<div class="score-result">'+esc(w.team.displayName)+' won</div>';
  }
  return '<article class="score-card"><div class="score-head"><span class="comp-name">'+
    esc(compName||e.shortName||e.name||'')+'</span>'+badgeFor(st)+'</div>'+rows+result+when+venue+'</article>';
}

function renderGeneric(d){
  var evs=d.events||[];
  if(!evs.length)return emptyNote();
  return evs.map(function(e){return eventCard(e,e.name);}).join('');
}
function renderF1(d){
  var evs=d.events||[];
  if(!evs.length)return emptyNote();
  return evs.map(function(e){
    var st=stateOf(e),cir=e.circuit||{},addr=cir.address||{};
    var place=esc(cir.fullName||'')+((addr.city||addr.country)?', '+esc([addr.city,addr.country].filter(Boolean).join(', ')):'');
    var sess=(e.competitions||[]).map(function(c){
      var t=c.type||{},cst=(c.status&&c.status.type)||{};
      return '<div class="session-row"><span class="sess">'+esc(t.abbreviation||'Session')+'</span><span>'+
        esc(fmtIST(c.date))+' IST</span>'+badgeFor(cst)+'</div>';
    }).join('');
    return '<article class="score-card"><div class="score-head"><span class="comp-name">'+
      esc(e.name||'Grand Prix')+'</span>'+badgeFor(st)+'</div><div class="score-meta">'+place+'</div>'+sess+'</article>';
  }).join('');
}
function renderUFC(d){
  var evs=d.events||[];
  var out=evs.length?evs.map(function(e){
    var st=stateOf(e),comp=(e.competitions&&e.competitions[0])||{};
    var rows=(comp.competitors||[]).map(function(c){
      var a=c.athlete||{},rec=(c.records&&c.records[0]&&c.records[0].summary)||'',
          flag=(a.flag&&a.flag.href)||'',
          init=esc((a.displayName||'?').slice(0,2).toUpperCase());
      return '<div class="team-row"><span class="team-logo'+(flag?'':' noimg')+'">'+
        (flag?'<img src="'+esc(flag)+'" alt="" loading="lazy" onerror="this.parentNode.classList.add(\\'noimg\\');this.remove()">':'')+
        '<span class="team-init">'+init+'</span></span>'+
        '<span class="team-name">'+esc(a.fullName||'TBD')+(c.winner?' <span class="badge done">Winner</span>':'')+'</span>'+
        '<span class="team-score">'+esc(rec)+'</span></div>';
    }).join('');
    var when=st.state==='pre'?'<div class="score-meta">'+esc(fmtIST(e.date))+' IST</div>':'';
    var venue=(comp.venue&&comp.venue.fullName)?'<div class="score-meta">'+esc(comp.venue.fullName)+'</div>':'';
    return '<article class="score-card"><div class="score-head"><span class="comp-name">'+
      esc(e.name||'UFC')+'</span>'+badgeFor(st)+'</div>'+rows+when+venue+'</article>';
  }).join(''):emptyNote();
  out+='<h3 class="scores-subhead">Boxing: next major bouts</h3>'+
    '<div class="honest-note">Boxing has no free live-score feed, so these are the next big fights as reported, not live results.</div>'+
    BOXING.map(function(b){
      return '<article class="score-card"><div class="score-head"><span class="comp-name">'+
        esc(b.title)+'</span><span class="badge upcoming">'+esc(b.date)+'</span></div>'+
        '<div class="score-result">'+esc(b.bout)+'</div>'+
        '<div class="score-meta">'+esc(b.venue)+' &middot; '+esc(b.note)+'</div></article>';
    }).join('');
  return out;
}
function renderMotoGP(d){
  var riders=d.riders||[];
  var head='<div class="honest-note">'+esc(d.note||'2026 MotoGP rider lineup.')+
    ' <span class="countdown">Updated '+(d.updated?esc(d.updated):'daily')+'.</span></div>';
  if(!riders.length)return head+emptyNote();
  return head+'<div class="rider-grid">'+riders.map(function(r){
    return '<div class="rider-card"><div class="rider-num">'+esc(String(r.number==null?'':r.number))+'</div><div>'+
      '<div class="rider-name">'+esc(r.name)+'</div><div class="rider-team">'+esc(r.team)+'</div>'+
      '<div class="rider-country">'+(r.flag?'<img src="'+esc(r.flag)+'" alt="" loading="lazy">':'')+esc(r.country)+'</div></div></div>';
  }).join('')+'</div>';
}
function daysUntil(iso){return Math.max(0,Math.ceil((new Date(iso).getTime()-Date.now())/864e5));}
function renderWWE(){
  var cards=WWE_EVENTS.map(function(w){
    var n=daysUntil(w.date);
    var when=n===0?'Today':(n===1?'Tomorrow':'In '+n+' days');
    return '<article class="score-card wwe-card"><div class="score-head"><span class="comp-name">Premium Live Event</span>'+
      '<span class="badge upcoming">'+esc(when)+'</span></div>'+
      '<div class="score-result">'+esc(w.name)+'</div>'+
      '<div class="score-meta">'+esc(fmtIST(w.date))+' IST &middot; '+esc(w.venue)+'</div></article>';
  }).join('');
  return '<div class="honest-note">WWE is scripted entertainment, so there are no live scores anywhere. '+
    'These are the next confirmed Premium Live Events; dates can shift, check <a href="https://www.wwe.com" target="_blank" rel="noopener">wwe.com</a>.</div>'+cards;
}

function endpointFor(tab){
  if(tab==='cricket'){var l=leaguePick.cricket||LEAGUES.cricket[0];return {url:ESPN+'/cricket/'+l.id+'/scoreboard',label:l.name};}
  if(tab==='football'){var f=leaguePick.football||LEAGUES.football[0];return {url:ESPN+'/soccer/'+f.id+'/scoreboard',label:f.name};}
  if(tab==='f1')return {url:ESPN+'/racing/f1/scoreboard',label:'Formula 1'};
  if(tab==='ufc')return {url:ESPN+'/mma/ufc/scoreboard',label:'UFC'};
  return null;
}

function paintUpdated(){
  if(!lastFetch){updatedEl.textContent='';return;}
  var s=Math.max(0,Math.round((Date.now()-lastFetch)/1000));
  updatedEl.textContent=s<10?'Updated just now':'Updated '+(s<60?s+' sec':Math.round(s/60)+' min')+' ago';
}
function scheduleNext(){
  clearTimeout(timer);
  timer=setTimeout(function(){
    if(document.hidden){scheduleNext();return;}
    load(current,true);
  },lastAnyLive?30000:60000);
}

function load(tab,silent){
  var ep=endpointFor(tab);
  if(!ep){ /* static tabs */
    panel.innerHTML=tab==='motogp'?'<div class="scores-loading">Loading MotoGP lineup...</div>':'';
    if(tab==='motogp'){
      fetch('assets/motogp.json').then(function(r){return r.json();}).then(function(d){
        panel.innerHTML=renderMotoGP(d);lastFetch=Date.now();paintUpdated();
      }).catch(function(){panel.innerHTML=errorNote();});
    }else if(tab==='wwe'){
      panel.innerHTML=renderWWE();lastFetch=Date.now();paintUpdated();
    }
    clearTimeout(timer);
    return;
  }
  if(!silent)panel.innerHTML='<div class="scores-loading">Loading '+esc(ep.label)+'...</div>';
  fetch(ep.url).then(function(r){
    if(!r.ok)throw new Error('http '+r.status);
    return r.json();
  }).then(function(d){
    var evs=d.events||[];
    lastAnyLive=evs.some(function(e){return stateOf(e).state==='in';});
    var html=tab==='f1'?renderF1(d):(tab==='ufc'?renderUFC(d):renderGeneric(d));
    panel.innerHTML=html;
    lastFetch=Date.now();paintUpdated();scheduleNext();
  }).catch(function(){
    panel.innerHTML=errorNote();clearTimeout(timer);
  });
}

function showLeagueBar(tab){
  var leagues=LEAGUES[tab];
  if(!leagues){bar.style.display='none';return;}
  bar.style.display='flex';
  leagueSel.innerHTML=leagues.map(function(l,i){
    var cur=leaguePick[tab]||leagues[0];
    return '<option value="'+esc(l.id)+'"'+(l.id===cur.id?' selected':'')+'>'+esc(l.name)+'</option>';
  }).join('');
}
function activate(tab,push){
  if(TAB_IDS.indexOf(tab)<0)tab='cricket';
  current=tab;lastAnyLive=false;clearTimeout(timer);
  document.querySelectorAll('.scores-tab').forEach(function(b){
    var on=b.getAttribute('data-tab')===tab;
    b.classList.toggle('active',on);b.setAttribute('aria-selected',on?'true':'false');
  });
  showLeagueBar(tab);load(tab,false);
  if(push!==false&&location.hash!=='#'+tab)location.hash=tab;
}

function init(){
  panel=document.getElementById('scores-panel');
  bar=document.getElementById('scores-bar');
  leagueSel=document.getElementById('league-select');
  updatedEl=document.getElementById('scores-updated');
  if(!panel)return;
  document.querySelectorAll('.scores-tab').forEach(function(b){
    b.addEventListener('click',function(){activate(b.getAttribute('data-tab'));});
  });
  leagueSel.addEventListener('change',function(){
    var leagues=LEAGUES[current]||[];
    var pick=leagues.filter(function(l){return l.id===leagueSel.value;})[0];
    if(pick){leaguePick[current]=pick;load(current,false);}
  });
  panel.addEventListener('click',function(e){
    if(e.target&&e.target.id==='scores-retry')load(current,false);
  });
  document.addEventListener('visibilitychange',function(){
    if(!document.hidden)load(current,true);
  });
  agoTimer=setInterval(paintUpdated,20000);
  window.addEventListener('hashchange',function(){
    var h=location.hash.replace('#','');
    if(h&&h!==current)activate(h,false);
  });
  var start=location.hash.replace('#','');
  activate(TAB_IDS.indexOf(start)>=0?start:'cricket',false);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
else init();
})();"""

# ---------------------------------------------------------------- read-aloud JS

READALOUD_JS = r"""(function(){
'use strict';
/* Read-aloud: text-to-speech for article pages using the browser's built-in
   Web Speech API (no backend, works offline where voices are installed).
   Background playback: tab-backgrounded playback keeps going; the Media
   Session API wires lock-screen/notification controls on Android. Whether
   audio continues with the screen fully locked depends on the browser and OS,
   this integration is the supported route to enable it where available. */
var btn=document.getElementById('listen-btn');
if(!btn||!('speechSynthesis' in window)){if(btn)btn.style.display='none';return;}
var synth=window.speechSynthesis;
var article=document.querySelector('.article');
if(!article){btn.style.display='none';return;}
var paras=article.querySelectorAll('p.body,p.lede');
if(!paras.length){btn.style.display='none';return;}

/* Split paragraphs into sentences and wrap them for highlighting. */
var sents=[];
var SENT_RE=/[^.!?\u0964\u0965]+[.!?\u0964\u0965]+["\u201d']?|\S(?:.*\S)?$/g;
paras.forEach(function(p){
  var txt=p.textContent;
  var parts=txt.match(SENT_RE)||[txt];
  p.textContent='';
  parts.forEach(function(part){
    var s=document.createElement('span');
    s.className='ra-sent-src';
    s.textContent=part.trim();
    p.appendChild(s);
    p.appendChild(document.createTextNode(' '));
    if(s.textContent)sents.push(s);
  });
});
if(!sents.length){btn.style.display='none';return;}

var idx=0,playing=false,rate=1,voice=null,voices=[];
var title=(document.querySelector('.article h1')||{}).textContent||'Varta & Samkara';

/* Player UI */
var bar=document.createElement('div');
bar.className='readaloud';
bar.setAttribute('role','region');
bar.setAttribute('aria-label','Audio player');
bar.innerHTML=
 '<div class="ra-inner">'+
 '<div class="ra-title"></div>'+
 '<div class="ra-controls">'+
 '<button class="ra-btn" data-act="prev" aria-label="Previous sentence">&#9664;&#9664;</button>'+
 '<button class="ra-btn primary" data-act="toggle" aria-label="Play or pause">&#9654;</button>'+
 '<button class="ra-btn" data-act="next" aria-label="Next sentence">&#9654;&#9654;</button>'+
 '<select class="ra-select" data-act="rate" aria-label="Playback speed">'+
 '<option value="0.75">0.75x</option><option value="1" selected>1x</option>'+
 '<option value="1.25">1.25x</option><option value="1.5">1.5x</option></select>'+
 '<select class="ra-select" data-act="voice" aria-label="Voice"></select>'+
 '<div class="ra-progress" aria-hidden="true"><i></i></div>'+
 '<span class="ra-count"></span>'+
 '<button class="ra-close" data-act="close" aria-label="Close player">&times;</button>'+
 '</div></div>';
document.body.appendChild(bar);
var titleEl=bar.querySelector('.ra-title');
var toggleBtn=bar.querySelector('[data-act="toggle"]');
var prog=bar.querySelector('.ra-progress i');
var count=bar.querySelector('.ra-count');
var rateSel=bar.querySelector('[data-act="rate"]');
var voiceSel=bar.querySelector('[data-act="voice"]');
titleEl.textContent='Listening: '+title;

function highlight(){
  sents.forEach(function(s){s.classList.remove('ra-sent');});
  if(sents[idx])sents[idx].classList.add('ra-sent');
}
function updateUI(){
  toggleBtn.innerHTML=playing?'&#10074;&#10074;':'&#9654;';
  toggleBtn.setAttribute('aria-label',playing?'Pause':'Play');
  var pct=sents.length?Math.round(idx/sents.length*100):0;
  prog.style.width=pct+'%';
  count.textContent=sents.length?((playing||idx>0)?(Math.min(idx+1,sents.length)+' / '+sents.length):''):'' ;
}
function scrollInto(){
  var el=sents[idx];
  if(!el)return;
  var r=el.getBoundingClientRect();
  if(r.top<70||r.bottom>window.innerHeight-140){
    el.scrollIntoView({block:'center',behavior:'smooth'});
  }
}
function speakCurrent(){
  if(idx>=sents.length){finish();return;}
  var u=new SpeechSynthesisUtterance(sents[idx].textContent);
  u.rate=rate;
  if(voice)u.voice=voice;
  u.onend=function(){
    if(!playing)return;
    idx++;
    highlight();updateUI();scrollInto();
    if(idx<sents.length)speakCurrent();else finish();
  };
  u.onerror=function(){if(playing){idx++;if(idx<sents.length){highlight();updateUI();speakCurrent();}else finish();}};
  synth.speak(u);
}
function play(){
  if(idx>=sents.length)idx=0;
  synth.cancel();
  playing=true;
  bar.classList.add('open');
  highlight();updateUI();scrollInto();
  speakCurrent();
  setMediaSession();
}
function pause(){
  playing=false;
  synth.cancel();
  updateUI();
}
function stop(){
  playing=false;
  synth.cancel();
  idx=0;
  highlight();updateUI();
}
function finish(){
  playing=false;
  idx=sents.length;
  highlight();updateUI();
}
function setMediaSession(){
  if(!('mediaSession' in navigator))return;
  try{
    navigator.mediaSession.metadata=new MediaMetadata({
      title:title,artist:'Varta & Samkara',album:'Varta & Samkara'
    });
    navigator.mediaSession.setActionHandler('play',play);
    navigator.mediaSession.setActionHandler('pause',pause);
    navigator.mediaSession.setActionHandler('stop',stop);
    navigator.mediaSession.setActionHandler('previoustrack',function(){
      idx=Math.max(0,idx-1);if(playing)play();else{highlight();updateUI();}
    });
    navigator.mediaSession.setActionHandler('nexttrack',function(){
      idx=Math.min(sents.length-1,idx+1);if(playing)play();else{highlight();updateUI();}
    });
  }catch(e){}
}
function pickVoice(){
  var saved=null;
  try{saved=localStorage.getItem('vs-voice');}catch(e){}
  if(saved){
    var found=voices.filter(function(v){return v.voiceURI===saved;})[0];
    if(found)return found;
  }
  var pref=voices.filter(function(v){return /^en[-_]IN/i.test(v.lang);})[0]
    ||voices.filter(function(v){return /^en/i.test(v.lang);})[0]
    ||voices[0];
  return pref||null;
}
function loadVoices(){
  voices=synth.getVoices();
  if(!voices.length)return;
  voice=pickVoice();
  voiceSel.innerHTML='';
  voices.forEach(function(v){
    var o=document.createElement('option');
    o.value=v.voiceURI;
    o.textContent=v.name+' ('+v.lang+')';
    if(voice&&v.voiceURI===voice.voiceURI)o.selected=true;
    voiceSel.appendChild(o);
  });
}
if(synth.onvoiceschanged!==undefined)synth.onvoiceschanged=loadVoices;
loadVoices();

bar.addEventListener('click',function(e){
  var act=e.target.closest('[data-act]');
  if(!act)return;
  var a=act.getAttribute('data-act');
  if(a==='toggle'){playing?pause():play();}
  else if(a==='prev'){idx=Math.max(0,idx-1);if(playing)play();else{highlight();updateUI();}}
  else if(a==='next'){idx=Math.min(sents.length-1,idx+1);if(playing)play();else{highlight();updateUI();}}
  else if(a==='close'){stop();bar.classList.remove('open');}
});
rateSel.addEventListener('change',function(){
  rate=parseFloat(rateSel.value)||1;
  if(playing){synth.cancel();speakCurrent();}
});
voiceSel.addEventListener('change',function(){
  var v=voices.filter(function(x){return x.voiceURI===voiceSel.value;})[0];
  if(v){voice=v;try{localStorage.setItem('vs-voice',v.voiceURI);}catch(e){}}
  if(playing){synth.cancel();speakCurrent();}
});
btn.addEventListener('click',function(){
  if(bar.classList.contains('open')&&(playing||idx>0)){playing?pause():play();}
  else{idx=0;play();}
});
document.addEventListener('keydown',function(e){
  if(e.key==='Escape'&&bar.classList.contains('open')){stop();bar.classList.remove('open');}
});
window.addEventListener('beforeunload',function(){synth.cancel();});
updateUI();
})();"""

# ---------------------------------------------------------------- templates

def rel(depth):
    return "../" * depth

def topbar(depth, active):
    r = rel(depth)
    def link(href, label, key, extra="", i18n=""):
        cls = ' class="active"' if active == key else ""
        i18n_attr = f' data-i18n="{i18n}"' if i18n else ""
        return f'<a href="{r}{href}"{cls}{extra}{i18n_attr}>{label}</a>'
    return f"""<header class="topbar">
<div class="brand"><div class="wm">&#2357;</div><h1>VARTA <span>&amp;</span> SAMKARA</h1></div>
<nav class="navlinks" id="navlinks">{link('index.html','Home','home', i18n='nav_home')}{link('archive.html','News','news', i18n='nav_news')}{link('videos.html','Videos','videos', i18n='nav_videos')}{link('blog.html','Blog','blog', i18n='nav_blog')}{link('factcheck.html','Fact Check','factcheck', i18n='nav_fact_check')}{link('heroes.html','Heroes','heroes', i18n='nav_heroes')}{link('scores.html','Scores','scores',' data-scores-nav', i18n='nav_scores')}{link('markets.html','Markets','markets', i18n='nav_markets')}{link('policy.html','Policy','policy', i18n='nav_policy')}{link('study.html','Study','study', i18n='nav_study')}{link('today.html','Today','today', i18n='nav_today')}{link('topics/','Topics','topics', i18n='nav_topics')}{link('timeline.html','Timeline','timeline', i18n='nav_timeline')}{link('horoscope.html','Horoscope','horoscope', i18n='nav_horoscope')}{link('tags/','Tags','tags', i18n='nav_tags')}<a href="{IG}" target="_blank" rel="noopener">Instagram</a></nav>
<div class="top-actions">
<button class="lang-toggle" id="lang-toggle" type="button" aria-label="Switch language" data-lang="en"><span class="lt-en" aria-hidden="true">EN</span><span class="lt-sep" aria-hidden="true">|</span><span class="lt-hi" aria-hidden="true">&#2361;&#2367;&#2306;</span></button>
<div class="top-search"><input id="topsearch" type="search" placeholder="Search stories..." data-i18n-ph="search_placeholder" aria-label="Search the site" autocomplete="off" data-index="{r}assets/search-index.json"><div class="search-results" id="search-results" hidden></div></div>
<button class="theme-toggle" id="theme-toggle" aria-label="Toggle dark mode"><span id="theme-icon">&#9789;</span></button>
<button class="hamburger" id="burger" aria-label="Menu">&#9776;</button>
</div>
</header>"""

def footer(depth):
    r = rel(depth)
    return f"""<footer><div class="foot-inner">
<div>&copy; 2026 Varta &amp; Samkara. News verified, opinions owned.</div>
<div class="nl-block"><strong data-i18n="newsletter">Newsletter</strong><p data-i18n="newsletter_sub">Get the top stories by email. No spam, unsubscribe anytime.</p>
<form class="nl-form" id="nl-form" data-email="{CONTACT_EMAIL}"><input type="email" required data-i18n-ph="email_placeholder" placeholder="you@example.com" aria-label="Email address"><button class="btn" type="submit" data-i18n="subscribe">Subscribe</button></form>
<p class="nl-note" data-i18n="newsletter_note">Opens your email app with a prefilled subscribe request.</p></div>
<div class="foot-links"><a href="{r}videos.html" data-i18n="nav_videos">Videos</a><a href="{r}heroes.html" data-i18n="nav_heroes">Heroes</a><a href="{r}scores.html" data-i18n="nav_scores">Scores</a><a href="{r}markets.html" data-i18n="nav_markets">Markets</a><a href="{r}policy.html" data-i18n="nav_policy">Policy</a><a href="{r}study.html" data-i18n="nav_study">Study</a><a href="{r}today.html" data-i18n="nav_today">Today</a><a href="{r}horoscope.html" data-i18n="nav_horoscope">Horoscope</a><a href="{r}factcheck.html" data-i18n="nav_fact_check">Fact Check</a><a href="{r}topics/" data-i18n="nav_topics">Topics</a><a href="{r}timeline.html" data-i18n="nav_timeline">Timeline</a><a href="{r}tags/" data-i18n="nav_tags">Tags</a><a href="{r}archive.html">Archive</a><a href="{r}feed.xml">RSS</a><a href="{r}sitemap.xml">Sitemap</a><a href="{IG}" target="_blank" rel="noopener">Instagram</a></div>
</div></footer>"""

def head(title, desc, depth, og_image="", extra_jsonld="", canonical="", og_type="article"):
    r = rel(depth)
    canon = f'<link rel="canonical" href="{html.escape(canonical)}">' if canonical else ""
    og_img = html.escape(og_image) if og_image else SITE_URL + "assets/og-default.png"
    og = f'<meta property="og:image" content="{og_img}">\n<meta property="og:image:width" content="1200">\n<meta property="og:image:height" content="630">'
    og_url = f'<meta property="og:url" content="{html.escape(canonical)}">' if canonical else ""
    tw = (f'<meta name="twitter:card" content="summary_large_image">\n'
          f'<meta name="twitter:title" content="{html.escape(title)}">\n'
          f'<meta name="twitter:description" content="{html.escape(desc)}">\n'
          f'<meta name="twitter:image" content="{og_img}">')
    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0a1a3c">
<title>{html.escape(title)} | Varta &amp; Samkara</title>
<script>(function(){{try{{var t=localStorage.getItem('vs-theme');if(!t){{t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}}document.documentElement.setAttribute('data-theme',t);var l=localStorage.getItem('vs-lang')||'en';document.documentElement.setAttribute('lang',l);}}catch(e){{}}}})();</script>
<meta name="description" content="{html.escape(desc)}">
{canon}
<meta property="og:site_name" content="Varta &amp; Samkara">
<meta property="og:locale" content="en_IN">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(desc)}">
<meta property="og:type" content="{og_type}">
{og_url}
{og}
{tw}
<link rel="manifest" href="{r}manifest.json">
<link rel="stylesheet" href="{r}styles.css">
<link rel="preconnect" href="https://s3.tradingview.com">
<link rel="dns-prefetch" href="https://www.instagram.com">
<link rel="alternate" type="application/rss+xml" title="Varta &amp; Samkara" href="{r}feed.xml">
<script>if('serviceWorker' in navigator){{window.addEventListener('load',function(){{navigator.serviceWorker.register('{r}sw.js').catch(function(){{}});}});}}</script>
<script src="{r}assets/lang-toggle.js" defer></script>
<script src="{r}assets/js/vs-reader.js" defer></script>
{extra_jsonld}"""

def excerpt_of(blocks, story):
    if blocks:
        for b in blocks:
            if b.startswith("#") or IMG_MD_RE.match(b):
                continue
            txt = re.sub(r"\s+", " ", b).strip()
            if txt:
                return txt[:150] + ("..." if len(txt) > 150 else "")
    return story[:150]

def reading_time(blocks):
    words = sum(len(re.findall(r"[A-Za-z0-9']+", b)) for b in (blocks or []))
    return max(1, round(words / 200))

def toc_of(blocks):
    items = []
    for b in (blocks or []):
        if b.startswith("## "):
            t = b[3:].strip()
            if t:
                items.append((f"sec-{len(items)+1}", t))
    return items

def related_posts(p, pool, n=3):
    pk = keywords(p["art"]["title"])
    scored, rest = [], []
    for q in pool:
        if q["slug"] == p["slug"]:
            continue
        s = len(pk & keywords(q["art"]["title"]))
        (scored if s else rest).append((s, q["date"], q))
    scored.sort(key=lambda x: (-x[0], x[1]), reverse=True)
    rest.sort(key=lambda x: x[1], reverse=True)
    out = [q for _, _, q in scored] + [q for _, _, q in rest]
    return out[:n]

def page_url(p):
    section = sec_of(p)
    return f"https://rahilsahu.github.io/varta-samkara/{section}/{p['slug']}/"

# ---------------------------------------------------------------- json-ld

SITE_URL = "https://rahilsahu.github.io/varta-samkara/"

def jsonld_article(p):
    atype = "BlogPosting" if p["is_blog"] else "NewsArticle"
    img = f"{SITE_URL}{p['og']}" if p.get("og") else ""
    data = {
        "@context": "https://schema.org",
        "@type": atype,
        "headline": p["art"]["title"],
        "datePublished": f"{p['date']}T00:00:00+05:30",
        "dateModified": f"{p['date']}T00:00:00+05:30",
        "inLanguage": "en-IN",
        "author": {"@type": "Organization", "name": "Varta & Samkara",
                   "url": SITE_URL},
        "publisher": {"@type": "Organization", "name": "Varta & Samkara",
                      "url": SITE_URL,
                      "logo": {"@type": "ImageObject",
                               "url": SITE_URL + "assets/icon-512.png",
                               "width": 512, "height": 512}},
        "description": p["excerpt"],
        "mainEntityOfPage": {"@type": "WebPage", "@id": page_url(p)},
    }
    if img:
        data["image"] = {"@type": "ImageObject", "url": img}
    return ('<script type="application/ld+json">\n'
            + json.dumps(data, ensure_ascii=False) + '\n</script>')

def jsonld_home():
    data = [
        {"@context": "https://schema.org", "@type": "WebSite",
         "name": "Varta & Samkara", "url": SITE_URL,
         "description": "Verified news, sharp explainers and honest opinion from India."},
        {"@context": "https://schema.org", "@type": "Organization",
         "name": "Varta & Samkara", "url": SITE_URL,
         "sameAs": [IG]},
    ]
    return ('<script type="application/ld+json">\n'
            + json.dumps(data, ensure_ascii=False) + '\n</script>')

# ---------------------------------------------------------------- topic tags

TAG_KEYWORDS = {
    "History": ["history", "ancient", "harappan", "mohenjo-daro", "mohenjodaro",
                "vedic", "rig veda", "rigveda", "sarasvati", "manusmriti",
                "aryan", "invasion", "colonial", "british raj", "mughal", "sati",
                "witch hunt", "civilization", "heritage", "manuscript",
                "archaeology", "sultan", "empire"],
    "Politics": ["modi", "trump", "election", "cabinet", "parliament",
                 "minister", "government", "bjp", "congress", "policy",
                 "president", "prime minister", "vote", "democracy"],
    "World": ["america", "united states", "china", "pakistan", "syria",
              "iraq", "russia", "ukraine", "israel", "iran", "europe",
              "un general assembly", "unga", "global"],
    "India": ["india", "indian", "bharat", "delhi", "mumbai", "bengaluru",
              "kerala", "punjab", "gujarat"],
    "Economy": ["economy", "gdp", "growth", "rupee", "oil", "trade",
                "tariff", "msp", "rabi", "startup", "funding", "crore",
                "lakh", "billion", "market", "stock", "investment", "budget",
                "inflation"],
    "Culture": ["culture", "festival", "diwali", "tradition", "yoga",
                "temple", "music", "cinema", "film", "art"],
    "Myth-busting": ["myth", "busted", "misinformation", "propaganda",
                     "debunk", "fake news", "hoax"],
    "Defence": ["iaf", "army", "navy", "defence", "defense", "missile",
                "fighter", "pilot", "war", "armed forces", "tarang shakti",
                "f-35"],
    "Sports": ["cricket", "asian games", "olympics", "football", "match",
               "final", "tournament", "medal", "gold"],
    "Technology": [" ai ", "tech", "apple", "iphone", "satellite", "space",
                   "galaxeye", "digital", "software", "internet"],
    "Science": ["science", "research", "dna", "genome", "study",
                "scientists", "discovery"],
}

_TAG_RES = {t: [re.compile(r"(?<![a-z])" + re.escape(k.strip()) + r"(?![a-z])")
                for k in kws]
            for t, kws in TAG_KEYWORDS.items()}

def assign_topic_tags(p):
    text = (p["art"]["title"] + " " + p.get("story", p.get("excerpt", ""))).lower()
    scored = []
    for tag, res in _TAG_RES.items():
        s = sum(1 for rx in res if rx.search(text))
        if s:
            scored.append((s, tag))
    scored.sort(key=lambda x: -x[0])
    tags = [t for _, t in scored[:4]]
    if p["is_blog"] and "Opinion" not in tags:
        tags.insert(0, "Opinion")
    fallbacks = ["India", "World"] if not p["is_blog"] else ["Opinion", "India"]
    for fb in fallbacks:
        if len(tags) < 2 and fb not in tags:
            tags.append(fb)
    return tags[:4]


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
TOPIC_SLUGS = {
    "Politics": "politics", "Economy": "economy",
    "Science & Tech": "science-tech", "Sports": "sports",
    "World": "world", "India": "india",
}
TOPIC_LEDES = {
    "Politics": "Elections, Parliament, protests and the courts.",
    "Economy": "Markets, the RBI, budgets, jobs and trade.",
    "Science & Tech": "Space, ISRO, AI, health and the research shaping tomorrow.",
    "Sports": "Cricket, the Olympics and every athlete making India proud.",
    "World": "International stories from beyond India's borders.",
    "India": "India-centric stories, from every corner of the country.",
}

def classify_topic(story_text, caption_text):
    text = (" " + (story_text or "") + " " + (caption_text or "") + " ").lower()
    def hits(kws):
        return sum(1 for k in kws
                   if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", text))
    scores = {t: hits(TOPIC_KEYWORDS[t]) for t in _TOPIC_PRIORITY}
    world = hits(TOPIC_KEYWORDS["World"])
    india = hits(INDIA_MARKERS)
    ranked = sorted(_TOPIC_PRIORITY, key=lambda t: (-scores[t], _TOPIC_PRIORITY.index(t)))
    if scores[ranked[0]] >= 1:
        return ranked[0]
    if world >= 1 and (india == 0 or world >= 2):
        return "World"
    return "India"


_THREAD_BLOCKLIST = {"india", "indian", "news", "today", "reel", "carousel",
    "post", "video", "watch", "read", "story", "full", "gold", "silver",
    "bronze", "medal", "golden"}

def _thread_keys(p):
    return {w for w in keywords(p["art"]["title"] + " " + p["excerpt"])
            if len(w) >= 5 and w not in _THREAD_BLOCKLIST}

def cluster_threads(news):
    from collections import Counter
    keys = {p["slug"]: _thread_keys(p) for p in news}
    parent = {p["slug"]: p["slug"] for p in news}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
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
        cnt = Counter()
        for p in members:
            cnt.update(keys[p["slug"]])
        title = cnt.most_common(1)[0][0].title() + " thread" if cnt else "Story thread"
        threads.append({"title": title, "items": members})
    threads.sort(key=lambda t: (len(t["items"]), t["items"][0]["date"]), reverse=True)
    return threads

def tag_slug(t):
    return slugify(t)

# ---------------------------------------------------------------- og images

def _og_font(size, bold=True):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for p in (f"/usr/share/fonts/truetype/dejavu/{name}",
              f"/usr/share/fonts/TTF/{name}"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def make_og_image(p):
    """Render a 1200x630 title card into assets/og/<slug>.png.
    Returns the site-relative path, or '' if it could not be made."""
    relp = f"assets/og/{p['slug']}.png"
    dst = os.path.join(SITE, relp)
    srcs = [s for s in p.get("og_srcs", []) if os.path.exists(s)]
    if os.path.exists(dst) and srcs and \
            os.path.getmtime(dst) >= max(os.path.getmtime(s) for s in srcs):
        return relp
    try:
        W, H = 1200, 630
        im = Image.new("RGB", (W, H), "#0a1a3c")
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, W, 30], fill="#ff9933")
        d.rectangle([0, H - 30, W, H], fill="#ff9933")
        d.ellipse([W - 240, -200, W + 40, 80], fill="#12295c")
        d.ellipse([W - 200, -160, W, 40], outline="#ff9933", width=5)
        fk, fb, fs = _og_font(34), _og_font(70), _og_font(38)
        d.text((70, 72), "VARTA & SAMKARA", font=fk, fill="#ff9933")
        max_w = W - 140
        lines, cur = [], ""
        for word in p["art"]["title"].split():
            trial = (cur + " " + word).strip()
            if d.textlength(trial, font=fb) <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = word
            if len(lines) == 4:
                cur = ""
                break
        if cur and len(lines) < 4:
            lines.append(cur)
        y = 168
        for ln in lines:
            d.text((70, y), ln, font=fb, fill="#ffffff")
            y += 88
        d.text((70, H - 108), p["date"], font=fs, fill="#9aa4bd")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        im.save(dst)
        return relp
    except Exception as e:  # PIL present but something odd; keep the build going
        print(f"OG image skipped for {p['slug']}: {e}")
        return ""

def make_icon(size):
    dst = os.path.join(SITE, f"assets/icon-{size}.png")
    if os.path.exists(dst):
        return
    im = Image.new("RGB", (size, size), "#0a1a3c")
    d = ImageDraw.Draw(im)
    pad = size // 7
    d.ellipse([pad, pad, size - pad, size - pad],
              outline="#ff9933", width=max(4, size // 18))
    fnt = _og_font(size // 3)
    d.text((size / 2, size / 2), "VS", font=fnt, fill="#ffffff", anchor="mm")
    im.save(dst)

# ---------------------------------------------------------------- motogp data (server-side; api.motogp.com sends no CORS headers)

def fetch_motogp():
    """Bake the 2026 MotoGP premier-class rider lineup into assets/motogp.json.

    The MotoGP API has no CORS headers, so browsers cannot fetch it directly;
    this runs server-side at build time instead. The championship standings and
    calendar endpoints return 403, so only the rider lineup is available.
    Graceful: keep the previous file if the fetch fails."""
    import urllib.request
    dst = os.path.join(SITE, "assets", "motogp.json")
    empty = {"updated": "", "riders": [],
             "note": "2026 MotoGP premier-class rider lineup."}
    try:
        req = urllib.request.Request(
            "https://api.motogp.com/riders-api/season/2026/riders",
            headers={"User-Agent": "Mozilla/5.0",
                     "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            riders = json.load(resp)
        out = []
        for r in riders:
            step = r.get("current_career_step") or {}
            cat = (step.get("category") or {}).get("name")
            # Official = full-time grid; skip Substitute/Wildcard test riders
            if cat != "MotoGP" or step.get("type") != "Official":
                continue
            team = step.get("sponsored_team") or (step.get("team") or {}).get("name") or ""
            country = (r.get("country") or {}).get("name") or ""
            flag = (r.get("country") or {}).get("flag") or ""
            out.append({
                "number": step.get("number"),
                "name": f"{r.get('name', '')} {r.get('surname', '')}".strip(),
                "team": team,
                "country": country,
                "flag": flag,
            })
        out.sort(key=lambda x: (x["number"] is None, x["number"] or 0))
        data = {
            "updated": datetime.now(timezone.utc).astimezone(
                timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M IST"),
            "riders": out,
            "note": ("2026 MotoGP premier-class rider lineup. Championship "
                     "standings are not published through a free data feed, "
                     "so points are not shown here."),
        }
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"motogp.json baked: {len(out)} riders")
    except Exception as e:
        if os.path.exists(dst):
            print(f"motogp fetch failed ({e}); kept previous motogp.json")
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as f:
                json.dump(empty, f, ensure_ascii=False, indent=2)
            print(f"motogp fetch failed ({e}); wrote empty placeholder")

SIGNS = [
    ("aries", "Aries", "&#9800;", "Mar 21 - Apr 19"),
    ("taurus", "Taurus", "&#9801;", "Apr 20 - May 20"),
    ("gemini", "Gemini", "&#9802;", "May 21 - Jun 20"),
    ("cancer", "Cancer", "&#9803;", "Jun 21 - Jul 22"),
    ("leo", "Leo", "&#9804;", "Jul 23 - Aug 22"),
    ("virgo", "Virgo", "&#9805;", "Aug 23 - Sep 22"),
    ("libra", "Libra", "&#9806;", "Sep 23 - Oct 22"),
    ("scorpio", "Scorpio", "&#9807;", "Oct 23 - Nov 21"),
    ("sagittarius", "Sagittarius", "&#9808;", "Nov 22 - Dec 21"),
    ("capricorn", "Capricorn", "&#9809;", "Dec 22 - Jan 19"),
    ("aquarius", "Aquarius", "&#9810;", "Jan 20 - Feb 18"),
    ("pisces", "Pisces", "&#9811;", "Feb 19 - Mar 20"),
]

def fetch_horoscope():
    """Bake daily horoscopes (12 signs x yesterday/today/tomorrow) into
    assets/horoscope.json.

    freehoroscopeapi.com sends no CORS headers, so browsers cannot fetch it
    directly; this runs server-side at build time instead. Graceful: keep the
    previous file if the fetch fails."""
    import urllib.request, time
    dst = os.path.join(SITE, "assets", "horoscope.json")
    sign_ids = [s for s, _, _, _ in SIGNS]
    days = ["yesterday", "today", "tomorrow"]
    try:
        data = {}
        for s in sign_ids:
            data[s] = {}
            for d in days:
                url = (f"https://freehoroscopeapi.com/api/v1/get-horoscope/"
                       f"daily?sign={s}&day={d}")
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0",
                                  "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    payload = json.load(resp).get("data") or {}
                data[s][d] = {"date": payload.get("date", ""),
                              "horoscope": payload.get("horoscope", "")}
                time.sleep(0.2)
        if any(not data[s][d]["horoscope"] for s in sign_ids for d in days):
            raise ValueError("incomplete horoscope payload")
        out = {
            "fetched_at": datetime.now(timezone.utc).astimezone(
                timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M IST"),
            "signs": data,
        }
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"horoscope.json baked: {len(sign_ids)} signs x {len(days)} days")
    except Exception as e:
        if os.path.exists(dst):
            print(f"horoscope fetch failed ({e}); kept previous horoscope.json")
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as f:
                json.dump({"fetched_at": "", "signs": {}}, f, ensure_ascii=False)
            print(f"horoscope fetch failed ({e}); wrote empty placeholder")

def card_html(p, depth=0):
    r = rel(depth)
    tag = ('<span class="tag opinion">Opinion</span>' if p["is_blog"]
           else f'<span class="tag">{html.escape(p["format"])}</span>')
    chips = "".join(
        f'<a class="tagchip" href="{r}tags/{tag_slug(t)}/">{html.escape(t)}</a>'
        for t in p.get("topic_tags", [])[:2])
    section = sec_of(p)
    rt = reading_time(p.get("blocks"))
    words = sum(len(b.split()) for b in (p.get("blocks") or []))
    data_ig = f' data-ig-url="{html.escape(p["ig_url"])}"' if p.get("ig_url") else ""
    bm = (f'<span class="vs-bookmark-slot" data-bookmark-id="{html.escape(p["ig_url"] if p.get("ig_url") else section + "/" + p["slug"] + "/")}"'
          f' data-bookmark-title="{html.escape(p["art"]["title"])}" data-bookmark-url="{html.escape(section + "/" + p["slug"] + "/")}"></span>')
    return f"""<article class="card reveal" data-search="{html.escape(p['art']['title'])} {html.escape(p['excerpt'])}"{data_ig}>
<a class="thumb" href="{r}{section}/{p['slug']}/"><img src="{r}{p['thumb']}" alt="{html.escape(p['art']['title'])}" loading="lazy" decoding="async"></a>
<div class="card-body">
<div class="meta">{tag}{chips}<span>{p['date']}</span><span data-readtime data-words="{words}" data-i18n-num="min_read">{rt} min read</span>{bm}</div>
<h3><a href="{r}{section}/{p['slug']}/">{html.escape(p['art']['title'])}</a></h3>
<p>{html.escape(p['excerpt'])}</p>
<a class="read" href="{r}{section}/{p['slug']}/" data-i18n="read_full_story">Read full story &rarr;</a>
</div></article>"""

def article_page(p, prev_p, next_p, related=None, latest=None):
    art = p["art"]
    body = render_body(p["blocks"]) if p["blocks"] else "".join(
        f"<p class='body'>{html.escape(pa)}</p>" for pa in art["paras"])
    if not body.strip():
        body = f"<p class='body'>{html.escape(p['story'])}</p>"
    r = rel(2)
    rt = reading_time(p.get("blocks"))
    words = sum(len(b.split()) for b in (p.get("blocks") or []))
    toc = toc_of(p["blocks"])
    toc_html = ""
    if len(toc) >= 3:
        lis = "".join(f'<li><a href="#{a}">{html.escape(t)}</a></li>' for a, t in toc)
        toc_html = f"""<nav class="toc" aria-label="On this page"><strong data-i18n="on_this_page">On this page</strong><ul>{lis}</ul></nav>"""
    purl = page_url(p)
    share_txt = urllib.parse.quote(f"{art['title']} - Varta & Samkara")
    share_url = urllib.parse.quote(purl, safe="")
    share_html = f"""<div class="share-row"><span class="lbl">Share:</span>
<a class="share-btn" href="https://wa.me/?text={share_txt}%20{share_url}" target="_blank" rel="noopener">WhatsApp</a>
<a class="share-btn" href="https://t.me/share/url?url={share_url}&text={share_txt}" target="_blank" rel="noopener">Telegram</a>
<a class="share-btn" href="https://twitter.com/intent/tweet?text={share_txt}&url={share_url}" target="_blank" rel="noopener">X</a>
<a class="share-btn" href="https://www.facebook.com/sharer/sharer.php?u={share_url}" target="_blank" rel="noopener">Facebook</a>
<a class="share-btn" href="https://mail.google.com/mail/?view=cm&fs=1&su={share_txt}&body={share_txt}%20{share_url}" target="_blank" rel="noopener">Gmail</a>
<a class="share-btn" href="{p['url'] if p['url'] and p['url'] != IG else IG}" target="_blank" rel="noopener" title="Open this story on Instagram">Instagram</a>
<button class="share-btn" type="button" onclick="copyPageLink(this)">Copy link</button></div>"""
    gallery = ""
    if p["gallery"]:
        figs = "".join(f'<img src="{r}{g}" alt="{html.escape(art["title"])}" loading="lazy" decoding="async">' for g in p["gallery"])
        gallery = f"<h2>In pictures</h2><div class='gallery'>{figs}</div>"
    topic_chips = "".join(
        f'<a class="tagchip" href="{r}tags/{tag_slug(t)}/">{html.escape(t)}</a>'
        for t in p.get("topic_tags", []))
    hash_spans = "".join(f"<span>{html.escape(t)}</span>" for t in art["tags"][:12])
    tags = (f"<div class='tagrow'>{topic_chips}{hash_spans}</div>"
            if (topic_chips or hash_spans) else "")
    nav = "<div class='prevnext'>"
    if prev_p:
        psec = sec_of(prev_p)
        nav += f"<a href='{r}{psec}/{prev_p['slug']}/'><span class='dir'>&larr; Newer</span>{html.escape(prev_p['art']['title'][:70])}</a>"
    else:
        nav += "<span></span>"
    if next_p:
        nsec = sec_of(next_p)
        nav += f"<a href='{r}{nsec}/{next_p['slug']}/' style='text-align:right'><span class='dir'>Older &rarr;</span>{html.escape(next_p['art']['title'][:70])}</a>"
    else:
        nav += "<span></span>"
    nav += "</div>"
    desc = html.escape(p["excerpt"][:160])
    badge = '<span class="opinion-badge">Opinion</span>' if p["is_blog"] else ""
    byline = ("""<div class="byline"><div class="avatar">R</div><div><strong>Rahil Sahu</strong><br>"""
              """<span style="font-size:.82rem">Founder, Varta &amp; Samkara</span></div></div>""" if p["is_blog"] else "")
    meta_tag = (f'<span class="tag opinion">Opinion</span>' if p["is_blog"]
                else f'<span class="tag">{html.escape(p["format"])}</span>')
    if p.get("og"):
        og_img = f"{SITE_URL}{p['og']}"
    elif not p["hero"].endswith(".svg"):
        og_img = f"{SITE_URL}{p['hero']}"
    else:
        og_img = ""
    if p["url"] and p["url"] != IG:
        ig_cta = f"""<div class="ig-cta">
<p>See the original reel / carousel with motion graphics on Instagram.</p>
<a class="btn" href="{p['url']}" target="_blank" rel="noopener">View on Instagram</a>
</div>"""
    else:
        ig_cta = f"""<div class="ig-cta">
<p>Follow the daily five-post slate on Instagram.</p>
<a class="btn" href="{IG}" target="_blank" rel="noopener">Follow on Instagram</a>
</div>"""
    related_html = ""
    if related:
        cards = []
        for q in related:
            qsec = sec_of(q)
            cards.append(f"""<article class="card">
<a class="thumb" href="{r}{qsec}/{q['slug']}/"><img src="{r}{q['thumb']}" alt="{html.escape(q['art']['title'])}" loading="lazy" decoding="async"></a>
<div class="card-body"><div class="meta"><span>{q['date']}</span></div>
<h3><a href="{r}{qsec}/{q['slug']}/">{html.escape(q['art']['title'])}</a></h3></div></article>""")
        related_html = f"""<section class="related"><h2 data-i18n="related_stories">Keep reading</h2><div class="grid">{"".join(cards)}</div></section>"""
    sidebar_html = ""
    if latest:
        items = []
        for q in latest:
            qsec = sec_of(q)
            items.append(f"""<a class="side-item" href="{r}{qsec}/{q['slug']}/">
<img src="{r}{q['thumb']}" alt="" loading="lazy" decoding="async">
<div><h4>{html.escape(q['art']['title'])}</h4><span class="sdate">{q['date']}</span></div></a>""")
        sidebar_html = f"""<aside class="latest-sidebar"><h2 data-i18n="latest_news">Latest stories</h2>{"".join(items)}</aside>"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{head(art['title'], desc, 2, og_img, jsonld_article(p), canonical=purl)}
</head>
<body>
<div id="read-progress" role="progressbar" aria-label="Reading progress" aria-valuemin="0" aria-valuemax="100"></div>
{topbar(2, 'heroes' if p.get('is_hero') else ('blog' if p['is_blog'] else 'news'))}
<main class="wrap article-layout">
<div class="article-col article" data-article-id="{sec_of(p)}/{p['slug']}/" data-article-title="{html.escape(art['title'])}" data-article-url="{sec_of(p)}/{p['slug']}/" data-article-body>
<div class="meta" style="margin-top:1rem">{meta_tag}<span>{p['date']}</span><span>{html.escape(p['slot'])}</span><span data-readtime data-words="{words}" data-i18n-num="min_read">{rt} min read</span><span class="vs-bookmark-slot" data-bookmark-id="{sec_of(p)}/{p['slug']}/" data-bookmark-title="{html.escape(art['title'])}" data-bookmark-url="{sec_of(p)}/{p['slug']}/"></span></div>
{badge}
<h1>{html.escape(art['title'])}</h1>
{byline}
<button class="listen-cta" id="listen-btn" type="button"><span class="spk">&#9836;</span> Listen to this article</button>
<img class="article-hero" src="{r}{p['hero']}" alt="{html.escape(art['title'])}" decoding="async">
{"<div class='photo-credit'>Portrait: " + html.escape(p['photo_credit']) + "</div>" if p.get('photo_credit') else ""}
{toc_html}
{body}
{gallery}
{share_html}
<div class="sourcebox">
<div><span class="lbl">Source:</span> {html.escape(art['source'])}</div>
<div><span class="lbl">Visuals:</span> {html.escape(art['visuals'])}</div>
</div>
{tags}
{ig_cta}
{related_html}
{nav}
</div>
{sidebar_html}
</main>
{footer(2)}
<button class="totop" id="totop" aria-label="Back to top">&uarr;</button>
<script src="{r}assets/readaloud.js" defer></script>
{JS}
</body>
</html>"""

def blog_page_from_file(path):
    """Parse blogs/<name>.md with frontmatter -> dict.
    Supports frontmatter keys: title, date, image (hero file in blogs/),
    source, visuals. Local ![alt](file) refs are copied to
    assets/blogs/<slug>/ and rewritten to ../../-relative paths."""
    text = open(path, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    meta, body = {}, text
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                meta[k.strip()] = v
        body = m.group(2)
    name = os.path.splitext(os.path.basename(path))[0]
    date = meta.get("date", name[:10])
    title = meta.get("title", name)
    slug = slugify(name)
    adir = os.path.join(SITE, "assets", "blogs", slug)
    os.makedirs(adir, exist_ok=True)

    def use_local_image(src):
        """Copy blogs/<src> into assets/blogs/<slug>/; return site-relative path."""
        if src.startswith(("http://", "https://", "data:")):
            return src
        src_path = os.path.join(BLOGSDIR, src)
        if not os.path.isfile(src_path):
            return src
        dest = os.path.join(adir, os.path.basename(src))
        shutil.copy2(src_path, dest)
        return f"assets/blogs/{slug}/{os.path.basename(src)}"

    # rewrite ![alt](src) refs to copied asset paths (blog pages sit at depth 2)
    def _rw_figure(mm):
        p = use_local_image(mm.group(2).strip())
        if not p.startswith(("http://", "https://", "data:", "../../")):
            p = "../../" + p
        return f"![{mm.group(1)}]({p})"
    body = re.sub(r"!\[(.*?)\]\((.*?)\)", _rw_figure, body)

    blocks = [b.strip() for b in body.strip().split("\n\n") if b.strip()]
    hero_img = meta.get("image", "").strip()
    if hero_img:
        hero_site = use_local_image(hero_img)
        hero = hero_site
        thumb = hero_site
    else:
        hero = thumb = "assets/placeholder.svg"
    return {"slug": slug, "date": date, "slot": "blog",
            "format": "blog", "is_blog": True, "user_blog": True,
            "art": {"title": title, "paras": [], "source": meta.get("source", "Author's own analysis"),
                    "visuals": meta.get("visuals", "AI-generated illustrations"), "tags": []},
            "blocks": blocks,
            "excerpt": excerpt_of(blocks, title),
            "url": IG, "hero": hero,
            "thumb": thumb, "gallery": [], "og_srcs": [path]}

def sec_of(p):
    """Site section for an article dict: heroes / blog / posts."""
    if p.get("is_hero"):
        return "heroes"
    return "blog" if p["is_blog"] else "posts"


def hero_date_of(path):
    """Publish date of a heroes/*.md file (frontmatter date, else filename)."""
    try:
        with open(path, encoding="utf-8") as f:
            head_txt = f.read(2000)
        m = re.match(r"^---\n(.*?)\n---\n", head_txt, re.S)
        if m:
            for line in m.group(1).splitlines():
                if line.startswith("date:"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return os.path.basename(path)[:10]


def hero_page_from_file(path):
    """Parse heroes/<name>.md with frontmatter -> dict.
    Supports frontmatter keys: title, date, image (portrait file in heroes/),
    photo (portrait credit), source, visuals. Local ![alt](file) refs are copied
    to assets/heroes/<slug>/ and rewritten to ../../-relative paths."""
    text = open(path, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    meta, body = {}, text
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                meta[k.strip()] = v
        body = m.group(2)
    name = os.path.splitext(os.path.basename(path))[0]
    date = meta.get("date", name[:10])
    title = meta.get("title", name)
    slug = slugify(name)
    adir = os.path.join(SITE, "assets", "heroes", slug)
    os.makedirs(adir, exist_ok=True)

    def use_local_image(src):
        """Copy heroes/<src> into assets/heroes/<slug>/; return site-relative path."""
        if src.startswith(("http://", "https://", "data:")):
            return src
        src_path = os.path.join(HEROESDIR, src)
        if not os.path.isfile(src_path):
            return src
        dest = os.path.join(adir, os.path.basename(src))
        shutil.copy2(src_path, dest)
        return f"assets/heroes/{slug}/{os.path.basename(src)}"

    # rewrite ![alt](src) refs to copied asset paths (hero pages sit at depth 2)
    def _rw_figure(mm):
        p = use_local_image(mm.group(2).strip())
        if not p.startswith(("http://", "https://", "data:", "../../")):
            p = "../../" + p
        return f"![{mm.group(1)}]({p})"
    body = re.sub(r"!\[(.*?)\]\((.*?)\)", _rw_figure, body)

    blocks = [b.strip() for b in body.strip().split("\n\n") if b.strip()]
    hero_img = meta.get("image", "").strip()
    if hero_img:
        hero_site = use_local_image(hero_img)
        hero = hero_site
        thumb = hero_site
    else:
        hero = thumb = "assets/hero-placeholder.jpg"
    return {"slug": slug, "date": date, "slot": "Forgotten Heroes",
            "format": "Hero", "is_blog": False, "is_hero": True,
            "photo_credit": meta.get("photo", "").strip(),
            "art": {"title": title, "paras": [], "source": meta.get("source", "Author's own analysis"),
                    "visuals": meta.get("visuals", "Archival portrait"), "tags": []},
            "blocks": blocks,
            "excerpt": excerpt_of(blocks, title),
            "url": "", "hero": hero,
            "thumb": thumb, "gallery": [], "og_srcs": [path]}


def jsonld_scores():
    data = {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": "Varta & Samkara Scores Center",
        "url": f"{SITE_URL}scores.html",
        "description": ("Live scores and results: cricket, football, F1, UFC, "
                        "MotoGP and WWE. All times in IST."),
    }
    return ('<script type="application/ld+json">\n'
            + json.dumps(data, ensure_ascii=False) + '\n</script>')

def scores_page():
    tabs = "".join(
        f'<button class="scores-tab" data-tab="{tid}" role="tab" aria-selected="false">{label}</button>'
        for tid, label in [
            ("cricket", "Cricket"), ("football", "Football"), ("f1", "F1"),
            ("ufc", "UFC & Boxing"), ("motogp", "MotoGP"), ("wwe", "WWE")])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{head('Scores Center', 'Live scores and results: cricket, football, F1, UFC, MotoGP and WWE. All times in IST.', 0, '', jsonld_scores())}
</head>
<body>
{topbar(0, 'scores')}
<main class="wrap">
<div class="sec-head"><h2 class="sec-title">Scores Center</h2><span class="updated" id="scores-updated"></span></div>
<p class="scores-sub">Live scores refresh automatically every minute (every 30 seconds when something is live). All times in IST.</p>
<div class="scores-tabs" role="tablist">{tabs}</div>
<div class="scores-bar" id="scores-bar"><select class="league-select" id="league-select" aria-label="Choose league"></select></div>
<div id="scores-panel" aria-live="polite"></div>
</main>
{footer(0)}
<button class="totop" id="totop" aria-label="Back to top">&uarr;</button>
{JS}
<script src="assets/scores.js"></script>
</body>
</html>"""

# ---------------------------------------------------------------- build

def jsonld_horoscope():
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": "Daily Horoscope - Varta & Samkara",
        "url": f"{SITE_URL}horoscope.html",
        "description": ("Daily horoscope readings for all twelve zodiac signs, "
                        "refreshed every day. For entertainment."),
    }, ensure_ascii=False)

HOROSCOPE_JS = """\
<script>
(function(){
'use strict';
var SIGNS={};
try{SIGNS=JSON.parse(document.getElementById('sign-data').textContent);}catch(e){}
var DAY_LABELS={yesterday:'Yesterday',today:'Today',tomorrow:'Tomorrow'};
var state={sign:'aries',day:'today'};
try{var s=localStorage.getItem('vs-horoscope-sign');if(s&&SIGNS[s])state.sign=s;}catch(e){}
var data=null;
var grid=document.getElementById('sign-grid');
var tabs=document.getElementById('day-tabs');
var card=document.getElementById('horo-card');
var updated=document.getElementById('horo-updated');

function signMeta(id){return SIGNS[id]||{name:id,dates:''};}
function render(){
  grid.querySelectorAll('.sign-card').forEach(function(b){
    b.classList.toggle('active',b.getAttribute('data-sign')===state.sign);
  });
  tabs.querySelectorAll('.day-tab').forEach(function(b){
    b.classList.toggle('active',b.getAttribute('data-day')===state.day);
  });
  var m=signMeta(state.sign);
  if(!data||!data.signs||!data.signs[state.sign]||!data.signs[state.sign][state.day]
     ||!data.signs[state.sign][state.day].horoscope){
    card.innerHTML='<h2>'+m.name+'</h2><p class="horo-text">Today\\'s reading is not available right now. Please check back later.</p>';
    return;
  }
  var entry=data.signs[state.sign][state.day];
  var d=entry.date||'';
  card.innerHTML='<h2>'+m.name+'</h2>'
    +'<div class="horo-date">'+m.dates+(d?' &bull; '+d:'')+' &bull; '+DAY_LABELS[state.day]+'</div>'
    +'<p class="horo-text"></p>';
  card.querySelector('.horo-text').textContent=entry.horoscope;
}
grid.addEventListener('click',function(e){
  var b=e.target.closest('.sign-card');
  if(!b)return;
  state.sign=b.getAttribute('data-sign');
  try{localStorage.setItem('vs-horoscope-sign',state.sign);}catch(e2){}
  render();
});
tabs.addEventListener('click',function(e){
  var b=e.target.closest('.day-tab');
  if(!b)return;
  state.day=b.getAttribute('data-day');
  render();
});
render();
fetch('assets/horoscope.json',{cache:'no-cache'}).then(function(r){
  if(!r.ok)throw new Error('http '+r.status);
  return r.json();
}).then(function(j){
  data=j;
  if(j&&j.fetched_at)updated.textContent='Updated '+j.fetched_at;
  render();
}).catch(function(){
  card.innerHTML='<h2>'+signMeta(state.sign).name+'</h2><p class="horo-text">Could not load today\\'s readings. Please check your connection and try again.</p>';
});
})();
</script>"""

def horoscope_page():
    cards = "".join(
        f'<button class="sign-card" type="button" data-sign="{s}">'
        f'<div class="sym">{sym}</div><div class="sname">{name}</div>'
        f'<div class="sdates">{dr}</div></button>'
        for s, name, sym, dr in SIGNS)
    sign_data = json.dumps(
        {s: {"name": name, "dates": dr} for s, name, sym, dr in SIGNS},
        ensure_ascii=False)
    tabs = "".join(
        f'<button class="day-tab" type="button" data-day="{d}">{lbl}</button>'
        for d, lbl in [("yesterday", "Yesterday"), ("today", "Today"),
                       ("tomorrow", "Tomorrow")])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{head("Daily Horoscope", "Daily horoscope readings for all twelve zodiac signs, refreshed every day. For entertainment.", 0, "", f'<script type="application/ld+json">{jsonld_horoscope()}</script>')}
</head>
<body>
{topbar(0, 'horoscope')}
<section class="hero" style="padding:2.6rem 1.2rem">
<div class="hero-inner">
<div class="kicker">Stars &amp; Signs</div>
<h2>Daily Horoscope</h2>
<p>Pick your sign and see what the day holds. Refreshed every morning.</p>
</div>
</section>
<main class="wrap">
<script id="sign-data" type="application/json">{sign_data}</script>
<div class="sign-grid" id="sign-grid">{cards}</div>
<div class="day-tabs" id="day-tabs" role="tablist">{tabs}</div>
<div class="horo-card" id="horo-card"><h2>Aries</h2><p class="horo-text">Loading today's reading...</p></div>
<div class="horo-meta" id="horo-updated"></div>
<p class="horo-note">For entertainment only.</p>
</main>
{footer(0)}
<button class="totop" id="totop" aria-label="Back to top">&uarr;</button>
{JS}
{HOROSCOPE_JS}
</body>
</html>"""

# ---------------------------------------------------------------- Policy tracker
# Curated, source-linked entries. No public API exists for Indian bills,
# compliances or judgments, so this dataset is maintained by hand and baked
# into assets/policy.json at build time. Add new entries here; the page
# renders them automatically with category tabs and search.
POLICY_ENTRIES = [
    {"cat": "policy", "date": "2026-09-30", "title": "Cabinet approves Rs 1.86 lakh crore PM-DHARA green energy transmission scheme", "summary": "The Union Cabinet approved the PM-DHARA (PM-Developing Harmonized and Accelerated Renewable-energy Access) scheme, effectively Green Energy Corridor Phase-III, to build intra-state transmission for evacuating 135 GW of renewable energy plus 50 GWh of battery storage by FY 2032-33. Total outlay Rs 1,86,405 crore with Rs 54,082 crore of central financial support, aimed at the 500 GW non-fossil target by 2030.", "sectors": ["energy", "infrastructure", "climate"], "status": "Announced", "sources": [["Millennium Post", "https://www.millenniumpost.in/business/cabinet-approves-rs-186-lakh-crore-scheme-for-green-energy-evacuation-678054"]]},
    {"cat": "judgment", "date": "2026-09-24", "title": "SC: praying for poll victory is not a corrupt religious appeal; AIMIM MLA's election upheld", "summary": "A three-judge bench (Justices Vikram Nath, Augustine George Masih, Sandeep Mehta) dismissed the election petition against AIMIM MLA Mufti Mohammad Ismail from Malegaon Central, holding that saying a dua for electoral victory is communication with God, not solicitation of votes in the name of religion under Section 123(3) of the Representation of the People Act, 1951. The 72-vote victory stands.", "sectors": ["elections", "judiciary"], "status": "Decided", "sources": [["LawChakra", "https://lawchakra.in/supreme-court/supreme-court-dua-poll-victory-not-soliciting-votes-religion-aimim-mla-election/"]]},
    {"cat": "judgment", "date": "2026-09-23", "title": "SC split verdict on challenge to CEC appointment law; matter to CJI for Constitution Bench", "summary": "A bench of Justices Dipankar Datta and Satish Chandra Sharma split on whether petitions challenging the Chief Election Commissioner and Other Election Commissioners (Appointment, Conditions of Service and Term of Office) Act, 2023 should go to a Constitution Bench. Justice Datta rejected the reference plea and prima facie questioned whether the PM-plus-minister-plus-LoP selection panel inspires public confidence; Justice Sharma favoured reference. Papers placed before the CJI to consider a Constitution Bench.", "sectors": ["polity", "elections", "judiciary"], "status": "Decided (split verdict)", "sources": [["Indian Express", "https://indianexpress.com/article/legal-news/supreme-court-split-verdict-petition-election-commission-sir-chief-appointment-constitution-bench-10890533/"]]},
    {"cat": "policy", "date": "2026-09-08", "title": "SEBI eases FPI compliance for government securities", "summary": "FPIs investing exclusively in government securities no longer need to furnish investor group details, effective immediately. Follows the RBI's 5 Jun 2026 circular withdrawing concentration limits for FPIs in G-Secs via the General Route, and the expansion of the Fully Accessible Route to 15, 30 and 40-year securities.", "sectors": ["BFSI", "Capital markets"], "status": "In force", "sources": [["Economic Times", "https://economictimes.indiatimes.com/markets/bonds/sebi-eases-fpi-compliance-rules-for-g-sec-bets/articleshow/133902425.cms"]]},
    {"cat": "law", "date": "2026-08-14", "title": "Kerala (Alteration of Name) Bill, 2026", "summary": "Concerns the alteration of the name of the state. Passed by both Houses in the Monsoon Session; assented 14 Aug 2026.", "sectors": ["Governance"], "status": "Act (assented)", "sources": [["Sarkari List", "https://sarkarilist.in/list-of-recent-bills-passed-in-parliament/"]]},
    {"cat": "law", "date": "2026-08-13", "title": "MSME Development (Amendment) Bill, 2026", "summary": "National digital platform for free and voluntary MSME registration; improves access to finance and speeds up resolution of delayed-payment disputes. Assented 13 Aug 2026.", "sectors": ["MSME", "Banking"], "status": "Act (assented)", "sources": [["Business Standard", "https://www.business-standard.com/markets/capital-market-news/parliament-monsoon-session-ends-with-12-bills-passed-amid-disruptions-126081400121_1.html"]]},
    {"cat": "law", "date": "2026-08-13", "title": "Bankers' Books Evidence Bill, 2026", "summary": "Expands the definition of bankers' books to cover physical, electronic, digital, virtual and cloud-based records. Assented 13 Aug 2026.", "sectors": ["Banking", "Legal"], "status": "Act (assented)", "sources": [["Business Standard", "https://www.business-standard.com/markets/capital-market-news/parliament-monsoon-session-ends-with-12-bills-passed-amid-disruptions-126081400121_1.html"]]},
    {"cat": "law", "date": "2026-08-11", "title": "Supreme Court (Number of Judges) Amendment Bill, 2026", "summary": "Amends the law fixing the number of judges of the Supreme Court. Assented 11 Aug 2026.", "sectors": ["Legal"], "status": "Act (assented)", "sources": [["Sarkari List", "https://sarkarilist.in/list-of-recent-bills-passed-in-parliament/"]]},
    {"cat": "law", "date": "2026-08-07", "title": "Prevention of Insults to National Honour (Amendment) Bill, 2026", "summary": "Amendments to the 1971 Act dealing with insults to national honour. Assented 7 Aug 2026.", "sectors": ["Governance"], "status": "Act (assented)", "sources": [["Deccan Herald", "https://www.deccanherald.com/india/parliament-monsoon-session-2026-12-bills-passed-in-both-houses-4110671"]]},
    {"cat": "law", "date": "2026-08-06", "title": "Registration of Births and Deaths (Amendment) Bill, 2026", "summary": "Amends the 1969 Act governing birth and death registration. Assented 6 Aug 2026.", "sectors": ["Governance"], "status": "Act (assented)", "sources": [["Deccan Herald", "https://www.deccanherald.com/india/parliament-monsoon-session-2026-12-bills-passed-in-both-houses-4110671"]]},
    {"cat": "law", "date": "2026-08-01", "title": "Tribunals Reforms Bill, 2026", "summary": "Improves the independence, transparency and efficiency of tribunals; provides for a National Tribunals Commission. Passed by both Houses in the Monsoon Session (20 Jul to 13 Aug 2026).", "sectors": ["Legal", "Governance"], "status": "Passed by Parliament", "sources": [["Business Standard", "https://www.business-standard.com/markets/capital-market-news/parliament-monsoon-session-ends-with-12-bills-passed-amid-disruptions-126081400121_1.html"]]},
    {"cat": "law", "date": "2026-08-01", "title": "Mines and Minerals (Development and Regulation) Amendment Bill, 2026", "summary": "Brings greater certainty and predictability to the fiscal regime for the mineral sector. Passed by both Houses in the Monsoon Session.", "sectors": ["Mining", "Industry"], "status": "Passed by Parliament", "sources": [["Business Standard", "https://www.business-standard.com/markets/capital-market-news/parliament-monsoon-session-ends-with-12-bills-passed-amid-disruptions-126081400121_1.html"]]},
    {"cat": "law", "date": "2026-08-01", "title": "Taxation and Other Laws (Amendment) Bill, 2026", "summary": "Amendments to taxation and related laws. Passed by both Houses in the Monsoon Session.", "sectors": ["Finance", "Tax"], "status": "Passed by Parliament", "sources": [["Business Standard", "https://www.business-standard.com/india-news/parliament-monsoon-session-12-bills-introduced-11-passed-33-hrs-spent-126081300890_1.html"]]},
    {"cat": "law", "date": "2026-08-01", "title": "National Co-operative Development Corporation (Amendment) Bill, 2026", "summary": "Amends the NCDC Act governing cooperative development. Passed by both Houses in the Monsoon Session.", "sectors": ["Co-operatives", "Agriculture"], "status": "Passed by Parliament", "sources": [["Deccan Herald", "https://www.deccanherald.com/india/parliament-monsoon-session-2026-12-bills-passed-in-both-houses-4110671"]]},
    {"cat": "law", "date": "2026-07-31", "title": "Public Examinations (Prevention of Unfair Means) Amendment Bill, 2026", "summary": "Passed in the Monsoon Session and received presidential assent on 31 Jul 2026. Speedy investigations and time-bound trials for paper-leak offences, higher penalties, Special Fast Track Courts and Special Public Prosecutors. Debated for over 17 hours across both Houses, the longest debate of the session.", "sectors": ["Education", "Students"], "status": "Act (assented)", "sources": [["Business Standard", "https://www.business-standard.com/india-news/parliament-monsoon-session-12-bills-introduced-11-passed-33-hrs-spent-126081300890_1.html"]]},
    {"cat": "policy", "date": "2026-07-31", "title": "Samudra Manthan: Rs 84,084 crore national offshore exploration scheme approved", "summary": "The Union Cabinet approved the Central Sector Scheme for offshore oil and gas exploration with a Phase-I outlay of Rs 84,084 crore up to FY 2030-31. Covers offshore seismic surveys, drilling of 60 deepwater exploration wells, common offshore infrastructure and oil and gas manufacturing/services zones, with government support up to 50% of eligible deepwater drilling cost subject to a Rs 675-crore-per-well ceiling.", "sectors": ["energy", "oil and gas"], "status": "Announced", "sources": [["GKBooks (citing PIB)", "https://gkbooks.in/central-government-scheme-current-affairs-latest-updates/"]]},
    {"cat": "judgment", "date": "2026-07-02", "title": "SC: AI-generated fake judgments are professional misconduct", "summary": "Setting aside an NCLT order in the Essel Infraprojects insolvency case that relied on hallucinated precedents, the Court called fake citations catastrophic for justice delivery and directed zero tolerance for unverified AI-generated legal material.", "sectors": ["Law", "Technology"], "status": "Decided", "sources": [["Dynamite News", "https://www.dynamitenews.com/technology/supreme-court-slams-ai-generated-fake-judgments-calls-unverified-citations-misconduct"]]},
    {"cat": "judgment", "date": "2026-05-27", "title": "Supreme Court upholds Special Intensive Revision of electoral rolls", "summary": "A bench headed by CJI Surya Kant (with Justice Joymalya Bagchi) upheld the Election Commission's Special Intensive Revision of electoral rolls, holding it advances the constitutional imperative of free and fair elections and sits within ECI's powers under Article 324 and Section 21(3) of the Representation of the People Act, 1950. The Court clarified that being struck off the rolls does not terminate citizenship. The ruling stemmed from petitions against the Bihar SIR ordered on June 24, 2025.", "sectors": ["elections", "polity"], "status": "Decided", "sources": [["The Hindu", "https://www.thehindu.com/news/national/as-supreme-court-upholds-sirs-legality-chief-election-commissioner-says-eci-will-always-be-with-voters/article71029425.ece"]]},
    {"cat": "compliance", "date": "2026-05-08", "title": "Central Rules under the four Labour Codes notified", "summary": "Code on Wages 2019, Industrial Relations Code 2020, Social Security Code 2020 and the OSH Code 2020: 29 central laws consolidated into 4 codes, in force since 21 Nov 2025. Wages (basic plus DA) must be at least 50 percent of total remuneration; single registration, single return, 351 rules instead of 1,436. States must notify their own rules; the Centre expects all states and UTs done by 31 Oct 2026.", "sectors": ["Employers", "HR", "Manufacturing", "IT services"], "status": "In force, states phasing in", "sources": [["Business Standard", "https://www.business-standard.com/india-news/all-states-uts-may-notify-labour-code-rules-by-oct-31-labour-secretary-126092401119_1.html"]]},
    {"cat": "law", "date": "2026-04-18", "title": "Jan Vishwas (Amendment of Provisions) Bill, 2026 passed", "summary": "Passed during the Budget Session 2026 (Jan 28 to Apr 18). Amends 80 central Acts to decriminalise minor offences and rationalise penalties, continuing the trust-based governance and ease-of-doing-business push. Businesses should review compliance exposure across the amended Acts as criminal provisions convert to civil penalties.", "sectors": ["business", "compliance", "ease of doing business"], "status": "Passed by Parliament", "sources": [["PRS India", "https://prsindia.org/files/parliament/session_track/2026/session_wrap/Wrap_BS_2026.pdf"]]},
    {"cat": "law", "date": "2026-04-18", "title": "Insolvency and Bankruptcy Code (Amendment) Bill, 2025 passed", "summary": "Passed during the Budget Session 2026 after committee scrutiny; it was the only Bill of the session referred to a parliamentary committee. Introduces a framework for cross-border insolvency and group insolvency, plus a creditor-initiated insolvency resolution process. Significant for banks, NBFCs and corporate borrowers dealing with multinational defaults.", "sectors": ["banking", "finance", "corporate"], "status": "Passed by Parliament", "sources": [["PRS India", "https://prsindia.org/files/parliament/session_track/2026/session_wrap/Wrap_BS_2026.pdf"], ["The Week", "https://www.theweek.in/wire-updates/national/2026/04/02/unusual-extension-of-budget-session-that-saw-passage-of-key-bills-despite-oppn-uproar.html"]]},
    {"cat": "law", "date": "2026-04-18", "title": "Transgender Persons (Protection of Rights) Amendment Bill, 2026 passed", "summary": "Passed during the Budget Session 2026 amid opposition demands for select-committee scrutiny. Narrows the definition of transgender person to exclude persons with different sexual orientations and self-perceived identities, and requires a medical board's recommendation for a transgender certificate. Affects identity documentation and access to welfare benefits.", "sectors": ["social justice", "rights"], "status": "Passed by Parliament", "sources": [["PRS India", "https://prsindia.org/files/parliament/session_track/2026/session_wrap/Wrap_BS_2026.pdf"], ["The Week", "https://www.theweek.in/wire-updates/national/2026/04/02/unusual-extension-of-budget-session-that-saw-passage-of-key-bills-despite-oppn-uproar.html"]]},
    {"cat": "law", "date": "2026-04-18", "title": "Andhra Pradesh Reorganisation (Amendment) Bill, 2026 passed", "summary": "Passed during the Budget Session 2026. Amends the 2014 parent Act to specify Amaravati as the official capital of Andhra Pradesh with effect from June 2, 2024, settling the state's decade-long capital dispute. Relevant for businesses and individuals dealing with AP government jurisdiction, registrations and filings.", "sectors": ["governance", "states"], "status": "Passed by Parliament", "sources": [["PRS India", "https://prsindia.org/files/parliament/session_track/2026/session_wrap/Wrap_BS_2026.pdf"]]},
    {"cat": "law", "date": "2026-04-18", "title": "Finance Bill, 2026 passed: STT hiked on F&O, TCS rationalised", "summary": "Gives effect to the Union Budget 2026-27 tax proposals. Securities Transaction Tax on futures raised from 0.02% to 0.05% and on options premium from 0.10% to 0.15%, with no change in STCG/LTCG rates or income tax slabs. TCS on overseas tour packages and on education/medical remittances under LRS cut to 2%. Interest awarded by Motor Accident Claims Tribunals made fully tax-exempt.", "sectors": ["taxation", "markets", "business"], "status": "Passed by Parliament", "sources": [["PRS India", "https://prsindia.org/files/parliament/session_track/2026/session_wrap/Wrap_BS_2026.pdf"], ["Economic Times", "https://economictimes.indiatimes.com/news/economy/policy/budget-2026-highlights-nirmala-sitharaman-budget-speech-announcement-income-tax-middle-class-fiscal-deficit-jobs-subsidy-defence-tariff-msp-agri-trains-in-union-budget-2026-27/articleshow/127831266.cms"]]},
    {"cat": "compliance", "date": "2026-04-01", "title": "Income-tax Act, 2025 comes into force", "summary": "Replaces the six-decade-old Income-tax Act, 1961. 536 sections and 23 chapters; a single \"Tax Year\" concept replaces the Previous Year and Assessment Year framework; Form 16 replaced by system-generated Form 130; CBDT notified the Income-tax Rules, 2026. Tax slabs and rates unchanged.", "sectors": ["All businesses", "Individuals", "Finance"], "status": "In force", "sources": [["TaxGuru", "https://taxguru.in/income-tax/income-tax-act-2025-key-features-effective-date.html"], ["Drishti IAS", "https://www.drishtiias.com/daily-updates/daily-news-analysis/income-tax-act-2025"]]},
    {"cat": "judgment", "date": "2026-03-11", "title": "Harish Rana v. Union of India: SC permits passive euthanasia", "summary": "In a historic first, the Supreme Court allowed withdrawal of life-sustaining treatment, including clinically assisted nutrition and hydration, for a patient in a persistent vegetative state for 13 years, applying the Common Cause guidelines. The right to die with dignity was held inseparable from quality palliative and end-of-life care; the Court urged a legislative framework.", "sectors": ["Healthcare", "Law"], "status": "Decided", "sources": [["Legal Services India", "https://www.legalservicesindia.com/harish-rana-v-union-of-india-2026-passive-euthanasia-canh-right-to-die-with-dignity/"]]},
    {"cat": "compliance", "date": "2026-02-06", "title": "RBI February 2026 policy: digital fraud compensation up to Rs 25,000, mis-selling curbs", "summary": "Keeping the repo rate at 5.25% with a neutral stance, the RBI's first 2026 policy centred on consumer protection. Proposed framework to compensate customers for losses up to Rs 25,000 from small-value digital frauds, tighter mis-selling norms, harmonised loan-recovery practices, and revised customer liability in unauthorised transactions. Collateral-free MSME loan limit raised from Rs 10 lakh to Rs 20 lakh; ECB framework overhauled with higher borrowing limits and the Rs 2.5 lakh crore VRR cap removed.", "sectors": ["banking", "finance", "consumer protection"], "status": "Announced", "sources": [["Moneylife", "https://Moneylife.in/article/rbi-keeps-repo-rate-unchanged-at-525-percentage-announces-digital-fraud-compensation-rules-against-misselling/79589.html"], ["TaxGuru", "https://taxguru.in/rbi/repo-rate-unchanged-rbi-rolls-sweeping-financial-reforms.html"]]},
    {"cat": "policy", "date": "2026-02-01", "title": "Union Budget 2026-27: Rs 12.2 lakh crore capex, manufacturing push", "summary": "Presented by Finance Minister Nirmala Sitharaman with fiscal deficit estimated at 4.3% of GDP and public capex raised to Rs 12.2 lakh crore. New schemes: Biopharma SHAKTI (Rs 10,000 crore over five years), India Semiconductor Mission 2.0, Electronics Components Manufacturing Scheme outlay raised to Rs 40,000 crore, Rs 10,000 crore SME Growth Fund, and a container manufacturing scheme. Seven high-speed rail corridors, one dedicated freight corridor and 20 new waterways announced.", "sectors": ["economy", "infrastructure", "manufacturing"], "status": "Announced", "sources": [["The Hindu BusinessLine", "https://www.thehindubusinessline.com/economy/budget/union-budget-2026-highlights-key-announcements-and-takeaways/article70577287.ece"], ["Economic Times", "https://economictimes.indiatimes.com/news/economy/policy/budget-2026-highlights-nirmala-sitharaman-budget-speech-announcement-income-tax-middle-class-fiscal-deficit-jobs-subsidy-defence-tariff-msp-agri-trains-in-union-budget-2026-27/articleshow/127831266.cms"]]},
    {"cat": "judgment", "date": "2026-01-28", "title": "SC: civil courts' jurisdiction not fully ousted under Waqf Act", "summary": "A bench of Justices Sanjay Kumar and K. Vinod Chandran held there is no absolute ouster of civil court jurisdiction even under Section 85 of the Waqf Act, 1995. Disputes over whether a property is waqf vest in the Waqf Tribunal only for properties in the published list of auqaf; unlisted, unregistered properties can be agitated before civil courts. Relevant for property litigation involving claimed waqf assets.", "sectors": ["judiciary", "property"], "status": "Decided", "sources": [["New Indian Express", "https://www.newindianexpress.com/nation/2026/Jan/28/civil-courts-jurisdiction-not-completely-barred-under-waqf-act-sc"]]},
    {"cat": "compliance", "date": "2025-11-14", "title": "DPDP Rules, 2025 notified; phased enforcement through 2026", "summary": "Digital Personal Data Protection framework: the Data Protection Board of India is active; the Consent Manager framework becomes operational around 13 Nov 2026; full substantive obligations (consent, breach notification, data principal rights) become enforceable around May 2027. Penalties go up to Rs 250 crore. 2026 is the build year for compliance programmes.", "sectors": ["Technology", "SaaS", "D2C", "BFSI"], "status": "Phased rollout", "sources": [["Compliance guide", "https://www.buildbyravirai.com/blog/dpdp-act-compliance-india-2026-website-saas-guide"]]},
    {"cat": "policy", "date": "2025-10-01", "title": "India-EFTA Trade and Economic Partnership Agreement (TEPA) in force", "summary": "Entered into force on 1 October 2025; India's first free trade agreement with four developed European nations (Iceland, Liechtenstein, Norway, Switzerland), signed 10 March 2024 after about 16 years of talks. First Indian trade deal with a dedicated investment and jobs chapter: EFTA states aim to invest $100 billion in India over 15 years and facilitate 1 million direct jobs. EFTA offered concessions on 92.2% of tariff lines covering 99.6% of India's export value.", "sectors": ["trade", "investment", "foreign policy"], "status": "In force", "sources": [["The Hindu", "https://www.thehindu.com/opinion/lead/the-india-efta-partnership-one-plus-one-equals-three/article71529585.ece"], ["The Tribune", "https://www.tribuneindia.com/news/business/india-efta-trade-pact-completes-one-year-usd-100-bn-investment-commitment-to-support-jobs-growth-goyal/amp/"]]},
    {"cat": "policy", "date": "2025-09-22", "title": "GST rate rationalisation (GST 2.0)", "summary": "At its 56th meeting on 3 September 2025, the GST Council merged the 5%, 12%, 18% and 28% slabs into two main slabs of 5% and 18%, plus a 40% rate for sin and luxury goods; effective 22 September 2025. Individual life and health insurance exempt; 33 lifesaving medicines exempt; small cars, ACs, TVs and cement moved to 18%.", "sectors": ["taxation", "economy"], "status": "In force", "sources": [["ABP Live", "https://news.abplive.com/business/gst-council-meet-october-7-penalties-refunds-1868743"], ["Economic Times", "https://m.economictimes.com/opinion/et-commentary/gst-2-0-revived-demand-now-industry-wants-its-tax-credit-unstuck/amp_articleshow/134572203.cms"]]},
    {"cat": "judgment", "date": "2025-09-15", "title": "Supreme Court interim order on Waqf (Amendment) Act, 2025", "summary": "Bench of CJI B.R. Gavai and Justice A.G. Masih refused to stay the Waqf (Amendment) Act 2025 in its entirety, citing the presumption of constitutionality. Stayed the clause requiring five years of practicing Islam before creating a waqf (Section 3(r)) until states frame rules, and stayed the collector's power to adjudicate whether waqf property encroaches on government land. Capped non-Muslim members at 4 in the Central Waqf Council and 3 in state boards; CEO should 'as far as possible' be Muslim.", "sectors": ["religion", "property", "constitutional law"], "status": "Decided (interim)", "sources": [["Akashvani", "https://newsonair.gov.in/supreme-court-puts-stay-on-certain-provisions-of-waqf-amendment-act-2025/"], ["DT Next", "https://www.dtnext.in/news/national/2025-waqf-law-sc-stalls-key-provisions-but-refuses-to-stay-entire-law-846658"]]},
    {"cat": "law", "date": "2025-09-01", "title": "Immigration and Foreigners Act, 2025", "summary": "Introduced in Lok Sabha on 11 March, passed by Lok Sabha on 27 March and Rajya Sabha on 2 April; presidential assent on 4 April; brought into force on 1 September 2025. Repeals four colonial-era laws: the Passport (Entry into India) Act 1920, Registration of Foreigners Act 1939, Foreigners Act 1946 and Immigration (Carriers Liability) Act 2000. Forged passports and visas punishable with 2 to 7 years jail and Rs 1 to 10 lakh fine. Mandates foreigner reporting by hotels, universities and hospitals; carriers must submit advance passenger manifests.", "sectors": ["immigration", "borders", "security"], "status": "In force", "sources": [["TaxTMI (PTI)", "https://www.taxtmi.com/news?id=54235"], ["Ada Derana", "http://www.adaderana.lk/news.php?nid=112089"]]},
    {"cat": "law", "date": "2025-08-22", "title": "Promotion and Regulation of Online Gaming Act, 2025", "summary": "Passed by Lok Sabha on 20 August and Rajya Sabha on 21 August (voice vote); presidential assent on 22 August 2025. Imposes a blanket ban on online money games, including fantasy sports, rummy and poker, while formally recognising esports as a sport. Penalties: up to 3 years jail plus Rs 1 crore fine for offering money games; up to 2 years plus Rs 50 lakh for advertising them; banks and payment systems barred from processing related transactions. Dream11, MPL, Zupee and PokerBaazi shut their real-money operations.", "sectors": ["gaming", "technology", "consumer"], "status": "In force", "sources": [["Economic Times", "https://economictimes.indiatimes.com/news/economy/policy/new-online-gaming-law-takes-effect-money-games-banned-from-today/articleshow/124255401.cms?from=mdr"], ["Business Standard", "https://www.business-standard.com/industry/news/govt-blocks-242-illegal-betting-gaming-websites-7-800-banned-so-far-126011601235_1.html"]]},
    {"cat": "law", "date": "2025-08-18", "title": "National Sports Governance Act, 2025", "summary": "Passed by Lok Sabha on 11 August and Rajya Sabha on 12 August; presidential assent on 18 August 2025 (No. 25 of 2025). Creates a National Sports Board, a National Sports Tribunal and a National Sports Election Panel. RTI applies to sports bodies receiving government funding; the BCCI is effectively outside its ambit.", "sectors": ["sports", "governance"], "status": "Act (assented)", "sources": [["The Hindu", "https://www.thehindu.com/news/national/national-sports-governance-bill-gets-president-droupadi-murmus-assent/article69950568.ece"], ["NDTV", "https://www.ndtv.com/india-news/national-sports-governance-bill-gets-presidents-nod-becomes-an-act-9114075.html"]]},
    {"cat": "judgment", "date": "2025-08-11", "title": "Supreme Court stray dogs orders", "summary": "On 11 August 2025, a two-judge bench (Justices Pardiwala and Mahadevan) ordered all stray dogs in Delhi-NCR to be picked up and moved to shelters within 8 weeks, triggering large public protests. On 22 August, a three-judge bench modified the order: dogs must be released after sterilisation and vaccination, with only aggressive or rabies-suspect dogs kept confined.", "sectors": ["animal welfare", "public health", "municipal"], "status": "Decided", "sources": [["Bhaskar English", "https://www.bhaskarenglish.in/originals/news/supreme-court-stray-dogs-order-explained-shelter-home-delhi-noida-135740490.html?_branch_match_id=1493607294229995&utm_campaign=135740490&utm_medium=sharing&utm_source=braze"], ["CSR Journal", "http://thecsrjournal.in/tag/stray-dogs-in-delhi"]]},
    {"cat": "policy", "date": "2025-07-24", "title": "India-UK Comprehensive Economic and Trade Agreement signed", "summary": "Signed in London on 24 July 2025 by Commerce Minister Piyush Goyal and UK Business Secretary Jonathan Reynolds, in the presence of PM Narendra Modi and PM Keir Starmer, after 14 rounds of negotiation. 99% of Indian exports get zero-duty access to the UK; average UK import tariff into India falls from 15% to 3%; target of $120 billion bilateral trade by 2030.", "sectors": ["trade", "economy", "foreign policy"], "status": "Signed", "sources": [["GOV.UK", "https://www.gov.uk/government/news/historic-uk-india-free-trade-agreement-is-now-in-effect"], ["Hindustan Times", "https://www.hindustantimes.com/india-news/pm-modi-uk-pm-andy-burnham-discuss-trade-deal-west-asia-crisis-101785515281875.html"]]},
    {"cat": "policy", "date": "2025-07-01", "title": "Employment Linked Incentive Scheme (Pradhan Mantri Viksit Bharat Rozgar Yojana)", "summary": "Cabinet approved on 1 July 2025 with an outlay of Rs 99,446 crore, targeting 3.5 crore jobs between 1 August 2025 and 31 July 2027. Part A: first-time employees get one month's wage up to Rs 15,000 via DBT. Part B: employers get up to Rs 3,000 per month for two years for each additional hire (extended to the 3rd and 4th year in manufacturing). Implemented by the Ministry of Labour and Employment through EPFO.", "sectors": ["employment", "labour", "economy"], "status": "Approved", "sources": [["DT Next", "https://www.dtnext.in/news/national/cabinet-okays-rs-1-lakh-crore-employment-linked-incentive-scheme-to-create-35-crore-jobs-838799"], ["IMPRI Insights", "https://impriinsights.in/pradhan-mantri-viksit-bharat-rozgar-yojana-pmvbry-one-year-of-employment-linked-incentives-under-ministry-of-labour-and-employment-impri-impact-and-policy-research-institute/"]]},
    {"cat": "policy", "date": "2025-04-30", "title": "Caste enumeration in Census 2027 approved", "summary": "The Cabinet Committee on Political Affairs decided on 30 April 2025 to include caste enumeration in the upcoming decennial census, the first nationwide caste count since 1931. Census 2027 runs in two phases, house-listing then population enumeration, with a central outlay of Rs 11,718.24 crore. Reversed the government's earlier stand and conceded a long-pending demand.", "sectors": ["census", "social justice", "governance"], "status": "Approved", "sources": [["Indian Express", "https://indianexpress.com/article/political-pulse/note-on-caste-census-meeting-rewritten-after-social-justice-ministry-pushback-10860920/"], ["Asianet Newsable", "https://newsable.asianetnews.com/india/census-2027-first-phase-almost-done-over-32-crore-families-recorded-articleshow-2g5dqlu"]]},
    {"cat": "law", "date": "2025-04-15", "title": "Banking Laws (Amendment) Act, 2025", "summary": "Act No. 16 of 2025; presidential assent on 15 April 2025. Amends the RBI Act 1934, Banking Regulation Act 1949, SBI Act 1955 and Banking Companies (Acquisition and Transfer of Undertakings) Acts of 1970 and 1980. Allows multiple and successive nominations for deposits and lockers; unclaimed dividends, shares and bonds transferred to the IEPF after 7 years; co-operative bank director tenure raised from 8 to 10 years; fortnightly reporting to RBI.", "sectors": ["banking", "finance"], "status": "Act (assented)", "sources": [["Gazette of India (via thc.nic.in)", "https://thc.nic.in/Central%20Governmental%20Acts/Banking%20Laws%20(Amendment)%20Act,%202025.pdf"], ["TaxGuru", "https://taxguru.in/rbi/banking-laws-amendment-act-2025.html"]]},
    {"cat": "law", "date": "2025-04-08", "title": "Waqf (Amendment) Act, 2025", "summary": "Lok Sabha passed it on 3 April (288 to 232) and Rajya Sabha on 4 April (128 to 95); presidential assent on 5 April; in force from 8 April 2025. Renames the parent law to the UWMEED Act 1995. Key changes: trusts separated from waqf entities, waqf dedication restricted to practicing Muslims, digital property management with a central portal, protection for 'waqf by user' properties, women's rights in family waqf, non-Muslim members in waqf bodies. Drew over 72 petitions challenging it before the Supreme Court.", "sectors": ["religion", "property", "minorities"], "status": "In force", "sources": [["Moneycontrol (PTI)", "https://www.moneycontrol.com/news/india/waqf-amendment-act-comes-into-force-from-april-8-12988903.html"], ["Akashvani", "https://newsonair.gov.in/president-droupadi-murmu-gives-assent-to-waqf-amendment-act-2025/"]]},
    {"cat": "judgment", "date": "2025-04-08", "title": "State of Tamil Nadu v. Governor of Tamil Nadu", "summary": "Two-judge bench (Justices J.B. Pardiwala and R. Mahadevan) held Governor R.N. Ravi's withholding of 10 state bills 'illegal and erroneous in law' and used Article 142 to deem them assented to. Laid down timelines for governors: 1 month to decide on assent or reservation on ministerial advice, 3 months against ministerial advice, and 1 month to grant assent after a bill is reconsidered by the legislature.", "sectors": ["constitutional law", "federalism"], "status": "Decided", "sources": [["Supreme Court Observer", "https://www.scobserver.in/reports/pendency-of-bills-before-tamil-nadu-governor-judgement-summary/"], ["Moneycontrol (PTI)", "https://www.moneycontrol.com/news/india/supreme-court-slams-tamil-nadu-governor-for-sitting-on-bills-passed-by-assembly-illegal-erroneous-in-law-12988325.html"]]},
    {"cat": "policy", "date": "2025-04-01", "title": "Unified Pension Scheme (UPS) operational", "summary": "Notified on 24 January 2025 and operational from 1 April 2025 as an option under NPS for central government employees. Assured pension of 50% of average basic pay for 25 years of service, Rs 10,000 per month minimum after 10 years, and family pension at 60% of the admissible payout. Expected to benefit 23 lakh employees; about 31,555 had opted in by July 2025; enrolment deadline extended to 30 November 2025.", "sectors": ["pensions", "government employees"], "status": "In force", "sources": [["HRKatha", "https://www.hrkatha.com/news/railways-notifies-unified-pension-scheme-rules-for-nps-covered-employees/"], ["Angel One", "https://www.angelone.in/news/market-updates/unified-pension-scheme-sees-over-1-18-lakh-enrolments-government-says-no-plan-to-replace-it"]]},
    {"cat": "policy", "date": "2025-01-16", "title": "8th Central Pay Commission announced", "summary": "Cabinet approved the formation of the 8th Central Pay Commission on 16 January 2025, announced by I&B Minister Ashwini Vaishnaw. Covers over 45 lakh central government employees and more than 68 lakh pensioners, as the 7th CPC tenure ends in 2026. Formally constituted on 3 November 2025 under chairperson Justice Ranjana Prakash Desai, with an 18-month deadline to submit recommendations.", "sectors": ["government employees", "finance"], "status": "Announced", "sources": [["Akashvani", "https://newsonair.gov.in/ib-minister-ashwini-vaishnaw-briefing-media-about-cabinets-decisions/"]]},
    {"cat": "judgment", "date": "2024-11-13", "title": "Supreme Court lays down pan-India guidelines against 'bulldozer justice'", "summary": "Justices B.R. Gavai and K.V. Viswanathan held that demolishing homes of accused or convicted persons without due process is totally unconstitutional and issued Article 142 guidelines applicable across India: 15-day show-cause notice, personal hearing, reasoned order, videography of demolitions, and contempt proceedings plus personal restitution costs for violations.", "sectors": ["Due process", "Constitutional law"], "status": "Decided", "sources": [["Indian Express", "https://indianexpress.com/article/explained/explained-law/sc-guidelines-illegal-demolitions-9667414/"]]},
    {"cat": "judgment", "date": "2024-11-08", "title": "Supreme Court clears the decks for AMU to claim minority status", "summary": "A seven-judge bench ruled 4:3 that institutions incorporated by statute can claim minority status under Article 30, overruling the 1967 Azeez Basha verdict. The Court held the test is who established the institution and sent the factual question of whether Aligarh Muslim University was established by Muslims to a regular three-judge bench.", "sectors": ["Education", "Constitutional law"], "status": "Decided", "sources": [["The Hindu", "https://www.thehindu.com/news/national/how-the-supreme-court-cleared-the-decks-for-amu-to-claim-its-minority-status-explained/article68852808.ece"]]},
    {"cat": "judgment", "date": "2024-11-06", "title": "Supreme Court: LMV licence holders may drive transport vehicles up to 7,500 kg", "summary": "A five-judge Constitution bench unanimously held that holders of light motor vehicle licences may drive transport vehicles with unladen weight up to 7,500 kg without a separate endorsement. The Court upheld its 2017 Mukund Dewangan ruling and found no empirical evidence linking LMV licence holders to higher road accident rates.", "sectors": ["Motor vehicles", "Labour"], "status": "Decided", "sources": [["Supreme Court Observer", "https://www.scobserver.in/reports/separate-licence-for-light-motor-vehicles-and-transport-vehicles-judgement-summary/"]]},
    {"cat": "judgment", "date": "2024-11-05", "title": "Supreme Court rules not all private property is 'material resources of the community' under Article 39(b)", "summary": "A nine-judge bench ruled 8:1 in Property Owners Association v. State of Maharashtra that the phrase in Article 39(b) does not cover all privately owned property, overruling socialist-leaning verdicts from after 1978. The Court held that whether a resource qualifies depends on factors like its nature, scarcity and effect on public welfare; Justice Sudhanshu Dhulia dissented.", "sectors": ["Property law", "Constitutional law"], "status": "Decided", "sources": [["The Hindu", "https://www.thehindu.com/news/national/the-supreme-court-verdict-on-private-property-rights-and-its-implications-explained/article68835847.ece"]]},
    {"cat": "judgment", "date": "2024-10-17", "title": "Supreme Court upholds Section 6A of the Citizenship Act, 1955", "summary": "A five-judge bench ruled 4:1 that Section 6A, which grants citizenship to migrants who entered Assam before the 25 March 1971 cut-off under the Assam Accord, is constitutionally valid. The majority found the cut-off reasonable given the Bangladesh liberation war context; Justice J.B. Pardiwala dissented, holding the provision unconstitutional with prospective effect.", "sectors": ["Citizenship", "Constitutional law"], "status": "Decided", "sources": [["The Hindu", "https://www.thehindu.com/news/national/assam-accord-hearing-live-sc-verdict-on-pleas-challenging-section-6a-of-citizenship-act/article68763241.ece"]]},
    {"cat": "policy", "date": "2024-09-18", "title": "Union Cabinet approves One Nation One Election proposal", "summary": "The Union Cabinet unanimously approved the recommendations of the high-level committee led by former President Ram Nath Kovind, which submitted its report in March 2024. The panel recommended simultaneous Lok Sabha and Assembly elections as the first step, followed by synchronised local body polls within 100 days, along with up to 18 constitutional amendments and a common electoral roll.", "sectors": ["Electoral reforms"], "status": "Announced", "sources": [["The Financial Express", "https://www.financialexpress.com/india-news/one-nation-one-election-gets-union-cabinet-nod-kovind-report/3614451/"], ["The Hindu", "https://www.thehindu.com/news/national/one-nation-one-election-the-kovind-panels-recommendations-explained/article67950654.ece/amp/"]]},
    {"cat": "policy", "date": "2024-09-11", "title": "Ayushman Bharat extended to all senior citizens aged 70 and above", "summary": "The Union Cabinet approved health coverage under Ayushman Bharat PM-JAY for all senior citizens aged 70 and above irrespective of income. About 4.5 crore families with six crore senior citizens will get Rs 5 lakh free health insurance cover on a family basis, with an additional Rs 5 lakh top-up for those already covered; those on CGHS, ECHS or CAPF may choose either scheme.", "sectors": ["Health", "Senior citizens"], "status": "Announced", "sources": [["PIB", "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2053881&reg=48&lang=2"], ["The Hindu", "http://www.thehindu.com/sci-tech/health/all-senior-citizens-above-70-brought-under-ayushman-bharat/article68631362.ece"]]},
    {"cat": "policy", "date": "2024-08-24", "title": "Unified Pension Scheme (UPS) approved for central government employees", "summary": "The Union Cabinet approved the Unified Pension Scheme as an alternative to the National Pension System for about 23 lakh central government employees. UPS assures 50% of the average basic pay of the last 12 months as pension after 25 years of qualifying service, plus assured family pension at 60%, a minimum pension of Rs 10,000 per month, inflation indexation and a lump-sum superannuation payout.", "sectors": ["Pensions", "Government employment"], "status": "Announced", "sources": [["PIB", "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2048607&s=09&reg=3&lang=3"], ["The Hindu", "https://www.thehindu.com/news/national/union-cabinet-meeting-on-august-24-2024/article68563115.ece"]]},
    {"cat": "law", "date": "2024-08-08", "title": "Waqf (Amendment) Bill, 2024 introduced and referred to Joint Parliamentary Committee", "summary": "Introduced in the Lok Sabha on 8 August 2024 by Minister Kiren Rijiju and referred the same day to a 31-member Joint Parliamentary Committee chaired by Jagdambika Pal. The Bill requires that only practising Muslims of five years standing may declare waqf, removes the concept of waqf by user, replaces the Survey Commissioner with the Collector, and provides that government property identified as waqf ceases to be waqf.", "sectors": ["Minority affairs", "Property law"], "status": "Bill (referred to JPC)", "sources": [["PRS India", "https://prsindia.org/files/bills_acts/bills_parliament/2024/Legislative_Brief_Waqf_(Amendment)_Bill_2024.pdf"], ["The Hindu", "https://www.thehindu.com/news/national/waqf-bill-referred-to-joint-parliamentary-panel-after-opposition-calls-it-draconian-and-an-attack-on-the-constitution/article68502262.ece/amp/"]]},
    {"cat": "judgment", "date": "2024-08-01", "title": "Supreme Court permits sub-classification of SCs and STs (State of Punjab v. Davinder Singh)", "summary": "A seven-judge bench ruled 6:1 that states may sub-classify Scheduled Castes and Scheduled Tribes for reservation benefits, overruling the 2004 E.V. Chinnaiah verdict. The majority held that sub-classification does not tinker with the Presidential List and recognised that the creamy layer principle can apply, with Justice Bela Trivedi dissenting.", "sectors": ["Constitutional law", "Reservation"], "status": "Decided", "sources": [["LiveLaw", "https://www.livelaw.in/articles/sub-classification-scheduled-castes-reservation-creamy-layer-debate-comment-state-punjab-davinder-singh-7-judge-bench-judgment-266418"]]},
    {"cat": "policy", "date": "2024-07-23", "title": "Union Budget 2024-25 presented", "summary": "Finance Minister Nirmala Sitharaman presented the first Budget of the Modi 3.0 government on 23 July 2024. Key announcements: angel tax abolished for all investors, LTCG reduced to 12.5% with indexation removed for real estate, STCG on equities raised to 20%, STT hiked on F&O trades, standard deduction raised to Rs 75,000, customs duty cut to 6% on gold and silver, and nine priorities including the PM Internship scheme.", "sectors": ["Taxation", "Economy"], "status": "Announced", "sources": [["The Economic Times", "https://economictimes.indiatimes.com/news/economy/policy/budget-2024-highlights-india-nirmala-sitharaman-capex-fiscal-deficit-tax-slab-key-announcement-in-union-budget-2024-25/articleshow/111942707.cms?from=mdr"]]},
    {"cat": "law", "date": "2024-07-01", "title": "Bharatiya Nyaya Sanhita, Bharatiya Nagarik Suraksha Sanhita and Bharatiya Sakshya Adhiniyamam come into force", "summary": "The three new criminal laws replaced the IPC, CrPC and Indian Evidence Act from 1 July 2024. The BNS has 358 sections and introduces offences of organised crime, terrorism and mob lynching plus community service as punishment. The BNSS mandates forensic investigation for offences punishable with seven or more years and allows e-FIRs, while the BSA treats electronic and digital records as equivalent to paper records.", "sectors": ["Criminal justice"], "status": "In force", "sources": [["Frontline", "https://frontline.thehindu.com/the-nation/indian-criminal-law-reform-criminal-justice-bns-bnss-bsa/article68397331.ece"], ["Hindustan Times", "https://www.hindustantimes.com/india-news/new-criminal-laws-will-be-rolled-out-on-july-1-101708798320215.html"]]},
    {"cat": "law", "date": "2024-06-26", "title": "Telecommunications Act, 2023 partially brought into force", "summary": "Sections 1, 2, 10 to 30, 42 to 44, 46, 47, 50 to 58, 61 and 62 of the Telecommunications Act, 2023 came into force on 26 June 2024. The Act replaces the Indian Telegraph Act of 1885 and the Indian Wireless Telegraphy Act of 1933, creates an authorisation regime for telecom services, lets the government take control of networks in emergencies, and converts the Universal Service Obligation Fund into the Digital Bharat Nidhi.", "sectors": ["Telecom"], "status": "In force (partial)", "sources": [["The Hindu", "https://www.thehindu.com/news/national/government-to-partially-implement-new-telecommunications-act-from-june-26/article68334870.ece/amp/"]]},
    {"cat": "law", "date": "2024-06-21", "title": "Public Examinations (Prevention of Unfair Means) Act, 2024 comes into force", "summary": "The anti-paper-leak law, passed by Parliament in February 2024 and assented on 12 February, came into force on 21 June 2024. It covers public examinations such as UPSC, SSC, NEET, JEE and CUET, with 3 to 5 years imprisonment and fines up to Rs 10 lakh, rising to 10 years and Rs 1 crore for organised crime, with offences made cognizable and non-bailable.", "sectors": ["Education", "Employment", "Criminal justice"], "status": "In force", "sources": [["Bar & Bench", "https://www.barandbench.com/news/centre-notifies-law-to-tackle-unfair-practices-punish-paper-leaks-public-exams"], ["LiveLaw", "https://www.livelaw.in/amp/top-stories/law-punishing-paper-leak-unfair-means-from-june-21-public-examinations-prevention-of-unfair-means-act-261101"]]},
    {"cat": "judgment", "date": "2024-04-26", "title": "Supreme Court rejects pleas for 100% VVPAT verification", "summary": "Justices Sanjiv Khanna and Dipankar Datta rejected petitions seeking 100% cross-verification of EVM votes with VVPAT slips and a return to ballot papers. The Court issued two directions: Symbol Loading Units must be sealed for 45 days after elections, and candidates ranked second or third may seek verification of burnt microcontroller memory within seven days of results.", "sectors": ["Electoral law"], "status": "Decided", "sources": [["The Telegraph", "https://www.telegraphindia.com/amp/india/supreme-court-dismisses-all-petitions-seeking-100-per-cent-verification-of-vvpat-slips-during-elections/cid/2015892"]]},
    {"cat": "policy", "date": "2024-02-29", "title": "PM-Surya Ghar: Muft Bijli Yojana approved", "summary": "The Union Cabinet approved the rooftop solar scheme with an outlay of Rs 75,021 crore to provide free electricity of up to 300 units per month to one crore households; the Prime Minister had launched it on 13 February 2024. Central financial assistance covers 60% of system cost for 2 kW systems and 40% of additional cost for 2 to 3 kW systems, meaning Rs 30,000, Rs 60,000 and Rs 78,000 subsidies for 1 kW, 2 kW and 3 kW systems.", "sectors": ["Energy", "Solar"], "status": "Announced", "sources": [["PIB", "https://www.pib.gov.in/PressReleaseIframePage.aspx?PRID=2010130&reg=48&lang=2"], ["ThePrint", "https://theprint.in/india/cabinet-okays-rs-75k-cr-rooftop-solar-scheme-1-cr-households-to-get-subsidy-of-up-to-rs-78k/1984237/"]]},
    {"cat": "judgment", "date": "2024-02-15", "title": "Supreme Court strikes down Electoral Bonds scheme", "summary": "A five-judge Constitution bench unanimously held the anonymous electoral bonds scheme unconstitutional, ruling it violates voters' right to information under Article 19(1)(a). The Court quashed the related amendments to the Companies Act, Income Tax Act and Representation of the People Act, ordered SBI to stop issuing bonds and to disclose all donor and recipient details to the Election Commission of India.", "sectors": ["Electoral law", "Constitutional law"], "status": "Decided", "sources": [["The Hindu", "https://www.thehindu.com/news/national/electoral-bonds-scheme-verdict-live-updates-february-15-2024/article67847980.ece"]]},
    {"cat": "judgment", "date": "", "title": "SC strikes down limits on maternity benefits for adoptive mothers", "summary": "The Court struck down Section 60(4) of the Code on Social Security, 2020 to the extent it limited maternity benefits for adoptive mothers, and favoured legal recognition of paternity leave.", "sectors": ["Employment", "Law"], "status": "Decided", "sources": [["SCC Times", "https://www.scconline.com/blog/post/2026/08/12/know-thy-judge-justice-jb-pardiwala-supreme-court-of-india/"]]},
    {"cat": "licence", "date": "", "title": "FSSAI Food Licence", "summary": "Mandatory for food businesses in India. Basic, State or Central licence depending on turnover and scale; applied through the FoSCoS portal with periodic renewal.", "sectors": ["Food", "Retail"], "status": "Ongoing", "sources": [["FSSAI", "https://www.fssai.gov.in/"]]},
    {"cat": "licence", "date": "", "title": "GST Registration", "summary": "Mandatory above the turnover threshold (Rs 40 lakh for goods, Rs 20 lakh for services in most states; lower in special-category states). Applied on the GST portal.", "sectors": ["All businesses"], "status": "Ongoing", "sources": [["GST Portal", "https://www.gst.gov.in/"]]},
    {"cat": "licence", "date": "", "title": "Shops and Establishment Registration", "summary": "State-level registration for shops, offices and commercial establishments, governing working hours, leave and employment conditions. Applied through the respective state labour department.", "sectors": ["All businesses"], "status": "Ongoing", "sources": []},
    {"cat": "licence", "date": "", "title": "Import Export Code (IEC)", "summary": "Mandatory for any import or export business, issued by the Directorate General of Foreign Trade. One-time registration, valid for life of the entity.", "sectors": ["Trade", "Manufacturing"], "status": "Ongoing", "sources": [["DGFT", "https://www.dgft.gov.in/"]]},
    {"cat": "bill", "date": "", "title": "Winter Session 2026: legislative agenda awaited", "summary": "The government's legislative agenda for the Winter Session is announced through the Parliamentary Bulletin before the session begins. This tracker will list bills for introduction, consideration and passing as soon as the bulletin is released.", "sectors": ["Governance"], "status": "To be announced", "sources": [["PRS Legislative Research", "https://prsindia.org/"]]}
]

MARKETS_JS = r"""(function(){
var OZ=31.1034768;
function $(id){return document.getElementById(id);}
function fmt(n,d){return Number(n).toLocaleString('en-IN',{minimumFractionDigits:d,maximumFractionDigits:d});}
function istTime(){try{return new Date().toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit'});}catch(e){return '';}}
function setStatus(){
  try{
    var now=new Date(new Date().toLocaleString('en-US',{timeZone:'Asia/Kolkata'}));
    var d=now.getDay(), mins=now.getHours()*60+now.getMinutes();
    var open=d>=1&&d<=5&&mins>=555&&mins<930;
    var el=$('mkt-status');
    el.textContent=open?'NSE/BSE open now':'Markets closed';
    el.className='mkt-badge '+(open?'open':'closed');
  }catch(e){}
}
function fail(msg){$('pm-note').textContent=msg;}
async function load(){
  try{
    var r=await Promise.all([
      fetch('https://api.gold-api.com/price/XAU').then(function(x){return x.json();}),
      fetch('https://api.gold-api.com/price/XAG').then(function(x){return x.json();}),
      fetch('https://open.er-api.com/v6/latest/USD').then(function(x){return x.json();})
    ]);
    var xau=r[0].price, xag=r[1].price, usdinr=r[2].rates&&r[2].rates.INR;
    if(!(xau>0&&xag>0&&usdinr>0))throw new Error('bad feed data');
    $('gold-usd').textContent='$'+fmt(xau,2)+' / oz';
    $('gold-inr').textContent='\u20B9'+fmt(xau/OZ*10*usdinr,0)+' / 10g';
    $('silver-usd').textContent='$'+fmt(xag,2)+' / oz';
    $('silver-inr').textContent='\u20B9'+fmt(xag/OZ*1000*usdinr,0)+' / kg';
    $('fx-rate').textContent='1 USD = \u20B9'+fmt(usdinr,2);
    $('pm-note').textContent='International spot prices converted to INR at the interbank rate \u00B7 updated '+istTime()+' IST \u00B7 refreshes every minute. Not investment advice.';
  }catch(e){fail('Could not reach the live price feeds. Indices tape below still streams. Not investment advice.');}
}
setStatus(); setInterval(setStatus,60000); load(); setInterval(load,60000);
var fxRates=null;
function fxCalc(){
  var amt=parseFloat($('fx-amt').value)||0, from=$('fx-from').value, to=$('fx-to').value;
  if(!fxRates){$('fx-out').textContent='Loading rates...';return;}
  var eur=fxRates.rates, out;
  try{out=amt/eur[from]*eur[to];}catch(e){$('fx-out').textContent='Rate unavailable';return;}
  $('fx-out').textContent=fmt(amt,2)+' '+from+' = '+fmt(out,2)+' '+to;
}
fetch('https://open.er-api.com/v6/latest/EUR').then(function(r){return r.json();})
.then(function(j){fxRates=j;['fx-amt','fx-from','fx-to'].forEach(function(id){$(id).addEventListener('input',fxCalc);$(id).addEventListener('change',fxCalc);});fxCalc();})
.catch(function(){$('fx-out').textContent='Converter offline';});
})();"""

POLICY_JS = r"""(function(){
var CATS=[['all','All'],['compliance','Compliances'],['licence','Licences'],['law','Laws & Bills'],['judgment','Court Judgments'],['policy','Policies'],['bill','Upcoming Bills']];
var data=[], cur='all', q='';
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');}
function card(e){
  var src=(e.sources||[]).map(function(s){return '<a href="'+esc(s[1])+'" target="_blank" rel="noopener">'+esc(s[0])+'</a>';}).join(' ');
  var sec=(e.sectors||[]).map(function(s){return '<span class="chip">'+esc(s)+'</span>';}).join('');
  var dt=e.date?'<span class="p-date">'+esc(e.date)+'</span>':'';
  return '<article class="p-card"><div class="p-top">'+dt+'<span class="p-status">'+esc(e.status)+'</span></div>'
    +'<h3>'+esc(e.title)+'</h3><p>'+esc(e.summary)+'</p>'
    +'<div class="p-sectors">'+sec+'</div>'
    +(src?'<div class="p-src">Sources: '+src+'</div>':'')+'</article>';
}
function counts(){
  var n={all:data.length};
  CATS.forEach(function(c){n[c[0]]=c[0]==='all'?data.length:data.filter(function(e){return e.cat===c[0];}).length;});
  return n;
}
function paintTabs(){
  var n=counts(), tabs=document.getElementById('p-tabs');
  Array.prototype.forEach.call(tabs.querySelectorAll('button'),function(b){
    var c=b.getAttribute('data-cat');
    b.classList.toggle('active',cur===c);
    var sp=b.querySelector('span');
    if(sp)sp.textContent=CATS.filter(function(x){return x[0]===c;})[0][1]+' ('+n[c]+')';
  });
}
function render(){
  var list=data.filter(function(e){
    if(cur!=='all'&&e.cat!==cur)return false;
    if(q){var t=(e.title+' '+e.summary+' '+(e.sectors||[]).join(' ')).toLowerCase(); if(t.indexOf(q)<0)return false;}
    return true;
  });
  document.getElementById('p-count').textContent=list.length+' entr'+(list.length===1?'y':'ies');
  document.getElementById('p-list').innerHTML=list.length?list.map(card).join(''):'<p class="muted">No entries match.</p>';
  paintTabs();
}
function buildTabs(){
  var tabs=document.getElementById('p-tabs');
  tabs.innerHTML=CATS.map(function(c){
    return '<button type="button" class="p-tab'+(cur===c[0]?' active':'')+'" data-cat="'+c[0]+'"><span>'+esc(c[1])+' (0)</span></button>';
  }).join('');
  tabs.addEventListener('click',function(e){
    var b=e.target.closest?e.target.closest('button'):null;
    if(!b||!tabs.contains(b))return;
    cur=b.getAttribute('data-cat');render();
  });
}
function load(){
  fetch('assets/policy.json',{cache:'no-cache'}).then(function(r){if(!r.ok)throw new Error('http '+r.status);return r.json();}).then(function(j){data=j;render();})
  .catch(function(){
    document.getElementById('p-count').textContent='';
    document.getElementById('p-list').innerHTML='<p class="muted">Could not load the tracker data. <button type="button" class="p-tab" id="p-retry">Tap to retry</button></p>';
    document.getElementById('p-retry').addEventListener('click',function(){
      document.getElementById('p-list').innerHTML='<p class="muted">Loading tracker...</p>';load();
    });
  });
}
buildTabs();
document.getElementById('p-search').addEventListener('input',function(e){q=e.target.value.trim().toLowerCase();render();});
load();
})();"""

def jsonld_page(name, desc, url):
    data = {"@context": "https://schema.org", "@type": "WebPage",
            "name": name, "url": url, "description": desc,
            "publisher": {"@type": "Organization", "name": "Varta & Samkara"}}
    return ('<script type="application/ld+json">\n'
            + json.dumps(data, ensure_ascii=False) + '\n</script>')

def page_shell(title, desc, active, main_html, extra_js="", depth=0, canonical=None):
    url = canonical or (SITE_URL + ("" if active == "home" else active + ".html"))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{head(title, desc, depth, "", jsonld_page(title, desc, url), canonical=url, og_type="website")}
</head>
<body>
{topbar(depth, active)}
<main class="wrap">{main_html}</main>
{footer(depth)}
<button class="totop" id="totop" aria-label="Back to top">&uarr;</button>
{JS}
{extra_js}
</body>
</html>"""


def factcheck_page(entries):
    cards = []
    for e in entries:
        verdict_class = "verdict-" + e["verdict"].lower().replace(" ", "-")
        details = "".join(f"<li>{html.escape(d)}</li>" for d in e["details"])
        sources = "".join(f'<li><a href="{html.escape(s["url"])}" target="_blank" rel="noopener">{html.escape(s["name"])}</a></li>' for s in e["sources"])
        rel = " ".join(f'<a class="tagchip" href="{html.escape(u)}">{html.escape(t)}</a>' for t, u in zip(e.get("related_titles", []), e.get("related_urls", [])))
        cards.append(f"""<article class="card fc-card" data-search="{html.escape(e['claim'])} {html.escape(e['summary'])}">
<div class="card-body"><span class="verdict {verdict_class}">{html.escape(e['verdict'])}</span>
<h3>{html.escape(e['claim'])}</h3>
<p><strong>Verdict:</strong> {html.escape(e['summary'])}</p>
<details><summary data-i18n="fc_details">Why this verdict</summary><ul>{details}</ul></details>
<div class="sources"><h4 data-i18n="sources">Sources</h4><ul>{sources}</ul></div>
{f'<div class="rel-links">{rel}</div>' if rel else ""}
<div class="card-meta"><span>{html.escape(e['date'])}</span></div></div></article>""")
    legend = """<div class="verdict-legend"><span class="verdict verdict-true">True</span><span class="verdict verdict-misleading">Misleading</span><span class="verdict verdict-unverified">Unverified</span><span class="verdict verdict-false">False</span></div>"""
    main = f"""<div class="page-head"><h1 data-i18n="fc_title">Fact Check</h1>
<p class="lede" data-i18n="fc_lede">Claims checked against our own verified reporting. No rumours, no forwards, just evidence.</p></div>{legend}<div class="grid">{"".join(cards)}</div>"""
    return page_shell("Fact Check", "Fact-checked claims from Varta & Samkara reporting. True, Misleading, Unverified and False verdicts with evidence.", "factcheck", main)


def timeline_page(threads):
    dev = []
    for i, t in enumerate(threads[:5], 1):
        latest = t["items"][0]
        dev.append(f"""<a class="dev-card" href="#thread-{i}"><span class="dev-count">{len(t['items'])} updates</span>
<h3>{html.escape(t['title'])}</h3><p>{html.escape(latest['art']['title'])}</p><span class="dev-date">{html.escape(latest['date'])}</span></a>""")
    dev_html = f"""<section class="dev-stories"><div class="sec-head"><h2 class="sec-title" data-i18n="dev_stories">Developing stories</h2></div>
<div class="dev-grid">{"".join(dev)}</div></section>""" if dev else ""
    blocks = []
    for i, t in enumerate(threads, 1):
        items = []
        for p in t["items"]:
            sec = sec_of(p)
            items.append(f"""<li class="tl-item"><span class="tl-dot" aria-hidden="true"></span>
<div class="tl-card"><span class="tl-date">{html.escape(p['date'])}</span>
<h4><a href="{sec}/{p['slug']}/">{html.escape(p['art']['title'])}</a></h4>
<p>{html.escape(p['excerpt'])}</p></div></li>""")
        date_range = f"{t['items'][-1]['date']} to {t['items'][0]['date']}" if len(t['items']) > 1 else t['items'][0]['date']
        blocks.append(f"""<section class="thread" id="thread-{i}"><div class="sec-head"><h2 class="sec-title">{html.escape(t['title'])}</h2>
<span class="tag">{len(t['items'])} updates, {html.escape(date_range)}</span></div>
<ol class="tl">{"".join(items)}</ol></section>""")
    main = f"""<div class="page-head"><h1 data-i18n="timeline_title">Timeline</h1>
<p class="lede" data-i18n="timeline_lede">Follow developing stories across days, newest update first.</p></div>
{dev_html}{"".join(blocks)}"""
    return page_shell("Timeline", "Developing stories on Varta & Samkara, tracked across days in timeline view.", "timeline", main)


def topic_page(topic, posts, counts):
    slug = TOPIC_SLUGS[topic]
    cards = "".join(card_html(p, depth=1) for p in posts)
    cloud = "".join(f'<a class="tagchip" href="{TOPIC_SLUGS[t]}.html">{html.escape(t)} ({counts[t]})</a>' for t in TOPIC_SLUGS)
    main = f"""<div class="page-head"><h1>{html.escape(topic)}</h1>
<p class="lede">{html.escape(TOPIC_LEDES[topic])} {len(posts)} stories.</p></div>
<div class="grid">{cards}</div>
<div class="topic-cloud">{cloud}<a class="tagchip" href="index.html">All topics</a></div>"""
    return page_shell(topic, f"{topic} news from Varta & Samkara: {TOPIC_LEDES[topic]}", "topics", main,
                      depth=1, canonical=SITE_URL + "topics/" + TOPIC_SLUGS[topic] + ".html")


def topics_index_page(counts):
    chips = "".join(f'<a class="tagchip" href="{TOPIC_SLUGS[t]}.html">{html.escape(t)} ({counts[t]})</a>' for t in TOPIC_SLUGS)
    main = f"""<div class="page-head"><h1>Topics</h1>
<p class="lede">Browse every story by topic.</p></div>
<h2 class="sec-title">Browse by topic</h2>
<div class="tag-cloud">{chips}</div>"""
    return page_shell("Topics", "Browse Varta & Samkara stories by topic: Politics, Economy, Science & Tech, Sports, World, India.", "topics", main,
                      depth=1, canonical=SITE_URL + "topics/")

def markets_page():
    tape = """<div class="tv-wrap"><div class="tradingview-widget-container"><div class="tradingview-widget-container__widget"></div><script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js" async>
{"symbols":[{"proName":"NSE:NIFTY","title":"Nifty 50"},{"proName":"BSE:SENSEX","title":"Sensex"},{"proName":"NSE:BANKNIFTY","title":"Bank Nifty"},{"proName":"NSE:INDIAVIX","title":"India VIX"},{"proName":"SP:SPX","title":"S&P 500"},{"proName":"NASDAQ:NDX","title":"Nasdaq 100"},{"proName":"DJ:DJI","title":"Dow 30"},{"proName":"TVC:UKX","title":"FTSE 100"},{"proName":"TVC:DEU40","title":"DAX"},{"proName":"TVC:NI225","title":"Nikkei 225"},{"proName":"TVC:HSI","title":"Hang Seng"},{"proName":"FX:USDINR","title":"USD/INR"}],"showSymbolLogo":true,"colorTheme":"dark","isTransparent":true,"displayMode":"adaptive","locale":"en"}
</script></div><div class="tv-cap">Live streaming indices via TradingView</div></div>"""
    body = f"""<div class="page-head"><h1>Markets</h1><p class="lede">Live Indian and global market indices, plus international gold and silver spot prices converted to rupees. <span id="mkt-status" class="mkt-badge">Checking market hours</span></p></div>
{tape}
<h2>Gold and Silver <span class="live-dot"></span></h2>
<div class="pm-grid">
<div class="pm-card"><div class="pm-name">Gold</div><div class="pm-inr" id="gold-inr">...</div><div class="pm-usd" id="gold-usd"></div></div>
<div class="pm-card"><div class="pm-name">Silver</div><div class="pm-inr" id="silver-inr">...</div><div class="pm-usd" id="silver-usd"></div></div>
<div class="pm-card"><div class="pm-name">US Dollar</div><div class="pm-inr" id="fx-rate">...</div><div class="pm-usd">Interbank reference rate</div></div>
</div>
<p class="muted" id="pm-note">Loading live prices...</p>
<h2>Currency Converter</h2>
<div class="fx-box"><input id="fx-amt" type="number" value="100" min="0" aria-label="Amount">
<select id="fx-from" aria-label="From currency"><option>USD</option><option>EUR</option><option>GBP</option><option selected>INR</option><option>JPY</option><option>AED</option></select>
<span class="fx-arrow">&rarr;</span>
<select id="fx-to" aria-label="To currency"><option selected>USD</option><option>EUR</option><option>GBP</option><option>INR</option><option>JPY</option><option>AED</option></select>
<div class="fx-out" id="fx-out">...</div></div>
<p class="muted">Rates via the European Central Bank feed, refreshed daily. Not investment advice.</p>"""
    return page_shell("Markets",
        "Live Sensex, Nifty and global indices with gold and silver prices in rupees.",
        "markets", body, "<script>" + MARKETS_JS + "</script>")

ARTICLES_JS = r"""(function(){
var DATA={articles:[],schedules:[]};
var PART_ORDER=['I','II','III','IV','IVA','V','VI','VII','VIII','IX','IXA','IXB','X','XI','XII','XIII','XIV','XIVA','XV','XVI','XVII','XVIII','XIX','XX','XXI','XXII'];
var state={view:'articles',part:'all',q:''};
function $(id){return document.getElementById(id);}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');}
function artKey(n){var m=String(n).match(/^(\d+)(.*)$/);return [m?parseInt(m[1],10):0,m?m[2]:''];}
function card(a){
  var det=a.detail?'<details class="a-det"><summary>Read detailed note</summary><p>'+esc(a.detail)+'</p></details>':'';
  var rep=a.repealed?' <span class="a-rep">Repealed</span>':'';
  var ver=a.verify?' <span class="a-ver">Needs verification</span>':'';
  return '<article class="a-card"><div class="a-top"><span class="a-num">Article '+esc(a.n)+'</span><span class="a-part">Part '+esc(a.part)+'</span>'+rep+ver+'</div>'
    +'<h3>'+esc(a.title)+'</h3><p>'+esc(a.s)+'</p>'+det+'</article>';
}
function paintPartChips(){
  var seen={},tabs=$('a-parts');PART_ORDER.forEach(function(p){seen[p]=false;});
  DATA.articles.forEach(function(a){if(a.part in seen)seen[a.part]=true;});
  var html='<button type="button" class="p-tab'+(state.part==='all'?' active':'')+'" data-part="all">All Parts</button>';
  PART_ORDER.forEach(function(p){if(seen[p])html+='<button type="button" class="p-tab'+(state.part===p?' active':'')+'" data-part="'+p+'">Part '+p+'</button>';});
  tabs.innerHTML=html;
  Array.prototype.forEach.call(tabs.querySelectorAll('button'),function(b){
    b.addEventListener('click',function(){state.part=b.getAttribute('data-part');render();});
  });
}
function render(){
  if(state.view==='schedules'){
    $('a-list').innerHTML=DATA.schedules.map(function(s){
      return '<article class="a-card"><div class="a-top"><span class="a-num">'+esc(s.n)+' Schedule</span></div><h3>'+esc(s.title)+'</h3><p>'+esc(s.s)+'</p></article>';
    }).join('')||'<p class="muted">No schedules found.</p>';
    $('a-count').textContent=DATA.schedules.length+' schedules';
    $('a-parts').style.display='none';
    return;
  }
  $('a-parts').style.display='';
  var list=DATA.articles.filter(function(a){
    if(state.part!=='all'&&a.part!==state.part)return false;
    if(state.q){var t=(a.n+' '+a.title+' '+a.s).toLowerCase();if(t.indexOf(state.q)<0)return false;}
    return true;
  });
  list.sort(function(x,y){var kx=artKey(x.n),ky=artKey(y.n);return kx[0]-ky[0]||(kx[1]<ky[1]?-1:kx[1]>ky[1]?1:0);});
  $('a-count').textContent=list.length+' of '+DATA.articles.length+' articles';
  $('a-list').innerHTML=list.length?list.map(card).join(''):'<p class="muted">No articles match.</p>';
  paintPartChips();
}
function buildViewTabs(){
  var tabs=$('a-views');
  tabs.innerHTML='<button type="button" class="p-tab active" data-view="articles">Articles</button>'
    +'<button type="button" class="p-tab" data-view="schedules">Schedules</button>';
  Array.prototype.forEach.call(tabs.querySelectorAll('button'),function(b){
    b.addEventListener('click',function(){
      state.view=b.getAttribute('data-view');
      Array.prototype.forEach.call(tabs.querySelectorAll('button'),function(x){x.classList.toggle('active',x===b);});
      render();
    });
  });
}
function load(){
  fetch('assets/constitution-articles.json',{cache:'no-cache'}).then(function(r){if(!r.ok)throw new Error('http '+r.status);return r.json();}).then(function(j){DATA=j;render();})
  .catch(function(){
    $('a-count').textContent='';
    $('a-list').innerHTML='<p class="muted">Could not load the articles data. <button type="button" class="p-tab" id="a-retry">Tap to retry</button></p>';
    $('a-retry').addEventListener('click',function(){$('a-list').innerHTML='<p class="muted">Loading articles...</p>';load();});
  });
}
buildViewTabs();
$('a-search').addEventListener('input',function(e){state.q=e.target.value.trim().toLowerCase();if(state.view==='articles')render();});
load();
})();"""

def articles_page():
    body = """<div class="page-head"><h1>Constitution of India: All Articles</h1><p class="lede">Every article of the Constitution, in plain language, with its Part and short title. Search by article number or keyword, or filter by Part. For legal purposes always consult the official constitutional text.</p></div>
<div class="p-tools"><div class="p-tabs" id="a-views"></div><input id="a-search" class="p-search" type="search" placeholder="Search articles: try 21, equality, governor..." aria-label="Search articles"></div>
<div class="p-tabs" id="a-parts" style="margin-bottom:1rem"></div>
<p class="muted" id="a-count"></p><div class="a-list" id="a-list"><p class="muted">Loading articles...</p></div>"""
    return page_shell("Constitution of India: All Articles Explained Simply",
        "All articles of the Indian Constitution explained in simple language, searchable and filterable by Part, for UPSC, law and student exam preparation.",
        "study", body, "<script>" + ARTICLES_JS + "</script>")

CA_JS = r"""(function(){
function $(id){return document.getElementById(id);}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');}
function fmtDate(d){var p=d.split('-');var M=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];return p[2]+' '+M[parseInt(p[1],10)-1]+' '+p[0];}
function pointHTML(p){
  var src=p.src?'<div class="ca-src"><a href="'+esc(p.src[1])+'" target="_blank" rel="noopener">'+esc(p.src[0])+'</a></div>':'';
  var why=p.why?'<p class="ca-why"><strong>Why it matters for exams:</strong> '+esc(p.why)+'</p>':'';
  return '<div class="ca-point"><h3>'+esc(p.t)+'</h3><p>'+esc(p.s)+'</p>'+why+src+'</div>';
}
function renderDay(day){
  $('ca-date').textContent=fmtDate(day.date);
  $('ca-list').innerHTML=day.points.map(pointHTML).join('');
}
function load(){
  fetch('assets/current-affairs.json',{cache:'no-cache'}).then(function(r){if(!r.ok)throw new Error('http '+r.status);return r.json();}).then(function(j){
    var days=j.days||[];
    if(!days.length){$('ca-list').innerHTML='<p class="muted">No briefs yet. Check back soon.</p>';return;}
    var sel=$('ca-sel');
    sel.innerHTML=days.map(function(d,i){return '<option value="'+i+'">'+fmtDate(d.date)+'</option>';}).join('');
    sel.addEventListener('change',function(){renderDay(days[parseInt(sel.value,10)]);});
    renderDay(days[0]);
  }).catch(function(){
    $('ca-list').innerHTML='<p class="muted">Could not load current affairs. <button type="button" class="p-tab" id="ca-retry">Tap to retry</button></p>';
    $('ca-retry').addEventListener('click',function(){$('ca-list').innerHTML='<p class="muted">Loading...</p>';load();});
  });
}
load();
})();"""

def ca_page():
    body = """<div class="page-head"><h1>Current Affairs for Government Exams</h1><p class="lede">A fresh brief every day: the developments that matter for UPSC, SSC, Banking and state exams, each with a note on why it matters and a source link.</p></div>
<div class="p-tools"><label class="ca-day" for="ca-sel">Brief for</label><select id="ca-sel" class="p-search" style="margin-left:0"></select></div>
<h2 id="ca-date" style="margin-top:0"></h2><div id="ca-list"><p class="muted">Loading...</p></div>
<p class="muted">Compiled for exam preparation from public reporting. Follow the source links for full stories.</p>"""
    return page_shell("Daily Current Affairs for Government Exams",
        "Daily current affairs brief for UPSC, SSC, Banking and state government exam preparation, with exam-relevance notes and sources.",
        "study", body, "<script>" + CA_JS + "</script>")

def parse_article_details(md_path):
    """Parse 'Article N: ...' lines from content-study/articles.md into {number: text}."""
    det, cur = {}, None
    if not os.path.exists(md_path):
        return det
    for line in open(md_path, encoding="utf-8"):
        m = re.match(r"Article\s+([0-9]+[A-Z]*)\s*:\s*(.*)", line.strip())
        if m:
            cur = m.group(1)
            det[cur] = m.group(2).strip()
        elif cur and line.strip() and not line.strip().startswith("#"):
            det[cur] += " " + line.strip()
    return det

def policy_page():
    body = """<div class="page-head"><h1>Law and Policy Tracker</h1><p class="lede">Compliances, licences, laws and bills, court judgments, policies and upcoming parliamentary business from 2024 to today. Each entry carries its source; verify at the official source before acting on anything here.</p></div>
<div class="p-tools"><div class="p-tabs" id="p-tabs"></div><input id="p-search" class="p-search" type="search" placeholder="Search the tracker..." aria-label="Search the tracker"></div>
<p class="muted" id="p-count"></p><div class="p-list" id="p-list"><p class="muted">Loading tracker...</p></div>"""
    return page_shell("Law and Policy Tracker: 2024 to Today",
        "Track compliances, licences, laws and bills, court judgments, policies and upcoming parliamentary bills from 2024 to today.",
        "policy", body, "<script>" + POLICY_JS + "</script>")

def videos_page(videos):
    cards = []
    for v in videos:
        if v["thumb"]:
            thumb_html = (f'<img src="{v["thumb"]}" alt="{html.escape(v["story"])}" '
                          'loading="lazy" decoding="async">')
        else:
            thumb_html = '<span class="vthumb-fallback">&#9654;</span>'
        cards.append(f"""<article class="card video-card">
<a class="vthumb" href="{v['url']}" target="_blank" rel="noopener">{thumb_html}<span class="play" aria-hidden="true">&#9654;</span></a>
<div class="card-body"><div class="meta"><span class="tag">Reel</span><span>{html.escape(v['date'])}</span></div>
<h3><a href="{v['url']}" target="_blank" rel="noopener">{html.escape(v['story'])}</a></h3>
<a class="read" href="{v['url']}" target="_blank" rel="noopener">Watch on Instagram &rarr;</a>
</div></article>""")
    grid = "".join(cards) if cards else '<p class="muted">No reels published yet.</p>'
    body = f"""<div class="page-head"><h1>Videos</h1><p class="lede">Every Instagram reel from Varta &amp; Samkara, newest first. Tap a card to watch it on Instagram.</p></div>
<div class="video-grid">{grid}</div>"""
    return page_shell("Videos",
        "Watch every Varta & Samkara Instagram reel, newest first.",
        "videos", body)

def study_hub_page(cards):
    grid = "".join(
        f'<a class="study-card" href="{u}"><h3>{t}</h3><p>{d}</p><span class="go">Start reading &rarr;</span></a>'
        for u, t, d in cards)
    quiz = ('<a class="study-card" href="quiz.html"><h3>Constitution Quiz</h3>'
            '<p>Test yourself with multiple-choice questions on the Constitution, with answers and explanations. Your best score is saved on this device.</p>'
            '<span class="go">Take the quiz &rarr;</span></a>')
    body = f"""<div class="page-head"><h1>Study</h1><p class="lede">Free exam-prep reading and practice for UPSC, UPPSC, SSC, Banking and state exams: Indian history ancient to modern, the Constitution article by article, economics, geography, the complete UPSC syllabus, daily current affairs, and a practice quiz.</p></div><div class="study-grid">{grid}{quiz}</div>
<p class="muted">Study summaries for exam preparation. For legal purposes always consult the official constitutional text. For the UPSC syllabus, always verify against the latest official notification.</p>"""
    return page_shell("Study for Government Exams: History, Constitution, Economics, Geography, UPSC Syllabus",
        "Free study material for UPSC, UPPSC, SSC, Banking and state exams: Indian history, Constitution articles, economics, geography, complete UPSC syllabus, daily current affairs and a practice quiz.",
        "study", body)

def study_article_page(slug, title, desc, md_path):
    raw = [l for l in open(md_path, encoding="utf-8").read().splitlines()
           if l.strip()]
    # Group consecutive table rows / list items into single blocks so
    # render_body can detect them; every other line stays its own block
    # (preserves existing paragraph rendering exactly).
    blocks = []
    buf, buf_kind = [], None
    for l in raw:
        s = l.strip()
        if s.startswith("|"):
            k = "table"
        elif re.match(r"^[-*]\s+", s):
            k = "list"
        else:
            k = None
        if k is not None and k == buf_kind:
            buf.append(l)
            continue
        if buf:
            blocks.append("\n".join(buf))
            buf, buf_kind = [], None
        if k is not None:
            buf, buf_kind = [l], k
        else:
            blocks.append(l)
    if buf:
        blocks.append("\n".join(buf))
    body_html = render_body(blocks)
    mins = reading_time(blocks)
    toc = toc_of(blocks)
    toc_html = ""
    if len(toc) >= 3:
        lis = "".join(f'<li><a href="#{a}">{html.escape(t)}</a></li>' for a, t in toc)
        toc_html = f"""<nav class="toc" aria-label="On this page"><strong data-i18n="on_this_page">On this page</strong><ul>{lis}</ul></nav>"""
    url = SITE_URL + slug + ".html"
    share_txt = urllib.parse.quote(title)
    share_url = urllib.parse.quote(url, safe="")
    share_html = f"""<div class="share-row"><span class="lbl">Share:</span>
<a class="share-btn" href="https://wa.me/?text={share_txt}%20{share_url}" target="_blank" rel="noopener">WhatsApp</a>
<a class="share-btn" href="https://t.me/share/url?url={share_url}&text={share_txt}" target="_blank" rel="noopener">Telegram</a>
<a class="share-btn" href="https://twitter.com/intent/tweet?text={share_txt}&url={share_url}" target="_blank" rel="noopener">X</a>
<a class="share-btn" href="https://www.facebook.com/sharer/sharer.php?u={share_url}" target="_blank" rel="noopener">Facebook</a>
<a class="share-btn" href="https://mail.google.com/mail/?view=cm&fs=1&su={share_txt}&body={share_txt}%20{share_url}" target="_blank" rel="noopener">Gmail</a>
<a class="share-btn" href="{IG}" target="_blank" rel="noopener" title="Open on Instagram">Instagram</a>
<button class="share-btn" type="button" onclick="copyPageLink(this)">Copy link</button></div>"""
    content = f"""<article class="post"><p class="kicker">Study</p><h1>{html.escape(title)}</h1>
<p class="meta"><span>{mins} min read</span></p>
<button class="listen-cta" id="listen-btn" type="button"><span class="spk">&#9836;</span> Listen to this article</button>
{toc_html}
{body_html}
{share_html}
<p class="muted">Study summary for exam preparation. For legal purposes consult the official constitutional text.</p></article>
<script src="assets/readaloud.js"></script>"""
    return page_shell(title, desc, "study", content)

# ---------------------------------------------------------------- Today page
# Weather + AQI: Open-Meteo (free, no key, CORS-enabled). On-this-day:
# Wikipedia REST API (free, no key, CORS-enabled).
TODAY_CITIES = [
    ("Sagar", 23.84, 78.74), ("Indore", 22.72, 75.86), ("Bhopal", 23.26, 77.41),
    ("Delhi", 28.61, 77.23), ("Mumbai", 19.07, 72.87), ("Kolkata", 22.57, 88.36),
    ("Chennai", 13.08, 80.27), ("Bengaluru", 12.97, 77.59), ("Hyderabad", 17.38, 78.48),
    ("Ahmedabad", 23.03, 72.58), ("Pune", 18.52, 73.85), ("Jaipur", 26.91, 75.79),
    ("Lucknow", 26.85, 80.95), ("Patna", 25.59, 85.13),
]

TODAY_JS = r"""(function(){
var CITIES=[["Sagar",23.84,78.74],["Indore",22.72,75.86],["Bhopal",23.26,77.41],["Delhi",28.61,77.23],["Mumbai",19.07,72.87],["Kolkata",22.57,88.36],["Chennai",13.08,80.27],["Bengaluru",12.97,77.59],["Hyderabad",17.38,78.48],["Ahmedabad",23.03,72.58],["Pune",18.52,73.85],["Jaipur",26.91,75.79],["Lucknow",26.85,80.95],["Patna",25.59,85.13]];
function wmo(c){if(c===0)return["\u2600","Clear sky"];if(c<=3)return["\u26C5","Partly cloudy"];if(c<=48)return["\uD83C\uDF2B","Fog"];if(c<=57)return["\uD83C\uDF26","Drizzle"];if(c<=67)return["\uD83C\uDF27","Rain"];if(c<=77)return["\uD83C\uDF28","Snow"];if(c<=82)return["\uD83C\uDF27","Showers"];if(c<=86)return["\uD83C\uDF28","Snow showers"];return["\u26C8","Thunderstorm"];}
function aqiBand(a){if(a==null)return["",""];if(a<=50)return["good","Good"];if(a<=100)return["mod","Moderate"];if(a<=150)return["usg","Unhealthy (SG)"];if(a<=200)return["unh","Unhealthy"];if(a<=300)return["vun","Very unhealthy"];return["haz","Hazardous"];}
function esc(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;");}
var lat=CITIES.map(function(c){return c[1];}).join(","), lon=CITIES.map(function(c){return c[2];}).join(",");
Promise.all([
  fetch("https://api.open-meteo.com/v1/forecast?latitude="+lat+"&longitude="+lon+"&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&daily=temperature_2m_max,temperature_2m_min&timezone=Asia%2FKolkata&forecast_days=1").then(function(r){return r.json();}),
  fetch("https://air-quality-api.open-meteo.com/v1/air-quality?latitude="+lat+"&longitude="+lon+"&current=us_aqi").then(function(r){return r.json();}).catch(function(){return null;})
]).then(function(res){
  var wx=res[0], aq=res[1], list=Array.isArray(wx)?wx:[wx];
  document.getElementById("wx-grid").innerHTML=list.map(function(w,i){
    var c=w.current||{}, d=(w.daily||{}), wm=wmo(c.weather_code||0);
    var aqi=aq&&aq[i]&&aq[i].current?aq[i].current.us_aqi:null, band=aqiBand(aqi);
    var hi=d.temperature_2m_max?d.temperature_2m_max[0]:"-", lo=d.temperature_2m_min?d.temperature_2m_min[0]:"-";
    return '<div class="wx-card"><div class="wx-city">'+esc(CITIES[i][0])+'</div>'
      +'<div class="wx-temp">'+wm[0]+" "+Math.round(c.temperature_2m||0)+"\u00B0C</div>"
      +'<div class="wx-cond">'+wm[1]+" \u00B7 H:"+Math.round(hi)+"\u00B0 L:"+Math.round(lo)+"\u00B0</div>"
      +'<div class="wx-meta">Humidity '+(c.relative_humidity_2m||"-")+"% \u00B7 Wind "+Math.round(c.wind_speed_10m||0)+" km/h</div>"
      +(aqi!=null?'<div class="aqi '+band[0]+'">AQI '+aqi+" \u00B7 "+band[1]+"</div>":'<div class="wx-meta">AQI unavailable</div>')
      +"</div>";
  }).join("");
}).catch(function(){document.getElementById("wx-grid").innerHTML='<p class="muted">Weather feed unreachable right now.</p>';});
var dstr=new Date().toLocaleDateString("en-CA",{timeZone:"Asia/Kolkata"});
var md=dstr.slice(5,7)+"/"+dstr.slice(8,10);
fetch("https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/"+md).then(function(r){return r.json();}).then(function(j){
  var ev=(j.events||[]).slice(0,10);
  document.getElementById("otd-list").innerHTML=ev.map(function(e){
    var title=e.pages&&e.pages[0]?e.pages[0].title:"";
    var link=title?'<a href="https://en.wikipedia.org/wiki/'+encodeURIComponent(title.replace(/ /g,"_"))+'" target="_blank" rel="noopener">Read more</a>':"";
    return '<div class="otd-item"><span class="otd-year">'+e.year+"</span><p>"+esc(e.text)+"</p>"+link+"</div>";
  }).join("")||'<p class="muted">Nothing found for today.</p>';
}).catch(function(){document.getElementById("otd-list").innerHTML='<p class="muted">History feed unreachable right now.</p>';});
})();"""

def today_page():
    body = """<div class="page-head"><h1>Today in India</h1><p class="lede">Live weather and air quality across major Indian cities, plus what happened on this day in history.</p></div>
<h2>Weather and Air Quality <span class="live-dot"></span></h2>
<div class="wx-grid" id="wx-grid"><p class="muted">Loading weather...</p></div>
<p class="muted">Weather by Open-Meteo. AQI is the US EPA index.</p>
<h2>On This Day</h2>
<div class="otd-list" id="otd-list"><p class="muted">Loading history...</p></div>
<p class="muted">Events via Wikipedia.</p>"""
    return page_shell("Today in India",
        "Live weather and air quality across Indian cities, plus on-this-day history.",
        "today", body, "<script>" + TODAY_JS + "</script>")

# ---------------------------------------------------------------- Constitution quiz
QUIZ_QUESTIONS = [
 {"q":"The Preamble begins with 'We, the people of India'. This means the ultimate source of authority is","o":["The Parliament","The people of India","The President","The Supreme Court"],"a":1,"e":"The Preamble declares the people as the source of the Constitution's authority."},
 {"q":"The Constitution of India was adopted on","o":["26 January 1950","15 August 1947","26 November 1949","9 December 1946"],"a":2,"e":"Adopted on 26 November 1949; it came into force on 26 January 1950."},
 {"q":"Who chaired the Drafting Committee of the Constituent Assembly?","o":["Jawaharlal Nehru","Dr. Rajendra Prasad","Sardar Vallabhbhai Patel","Dr. B. R. Ambedkar"],"a":3,"e":"Ambedkar chaired the seven-member Drafting Committee."},
 {"q":"The words 'Socialist', 'Secular' and 'Integrity' were added to the Preamble by the","o":["44th Amendment, 1978","42nd Amendment, 1976","52nd Amendment, 1985","86th Amendment, 2002"],"a":1,"e":"The 42nd Amendment (1976) is often called the mini-Constitution."},
 {"q":"Article 14 guarantees","o":["Freedom of religion","Equality before law and equal protection of laws","Right against exploitation","Cultural and educational rights"],"a":1,"e":"Article 14 is the foundation of the Right to Equality (Articles 14 to 18)."},
 {"q":"Dr. Ambedkar called this Article the 'heart and soul' of the Constitution","o":["Article 32","Article 21","Article 14","Article 19"],"a":0,"e":"Article 32 gives the Right to Constitutional Remedies."},
 {"q":"Article 21 protects the","o":["Right to equality","Right to freedom of speech","Right to life and personal liberty","Right to property"],"a":2,"e":"The Supreme Court has read dignity, privacy and livelihood into Article 21."},
 {"q":"The Fundamental Duties are listed in","o":["Article 51","Article 51A","Article 48A","Article 39A"],"a":1,"e":"Added by the 42nd Amendment; there are 11 duties today."},
 {"q":"The Directive Principles of State Policy are","o":["Enforceable in courts","Non-justiciable guidelines for the state","Fundamental rights","Emergency provisions"],"a":1,"e":"Part IV of the Constitution; fundamental in governance but not justiciable."},
 {"q":"The anti-defection law is contained in the","o":["Ninth Schedule","Tenth Schedule","Eighth Schedule","Twelfth Schedule"],"a":1,"e":"Added by the 52nd Amendment in 1985."},
 {"q":"The voting age was lowered from 21 to 18 by the","o":["61st Amendment, 1989","42nd Amendment, 1976","73rd Amendment, 1992","86th Amendment, 2002"],"a":0,"e":"The 61st Amendment (1989) amended Article 326."},
 {"q":"Panchayati Raj institutions got constitutional status through the","o":["74th Amendment","73rd Amendment","72nd Amendment","71st Amendment"],"a":1,"e":"The 73rd Amendment (1992) added Part IX."},
 {"q":"The Right to Education (Article 21A) was added by the","o":["86th Amendment, 2002","93rd Amendment, 2005","97th Amendment, 2011","103rd Amendment, 2019"],"a":0,"e":"Free and compulsory education for children aged 6 to 14."},
 {"q":"The Goods and Services Tax was introduced by the","o":["100th Amendment","101st Amendment","102nd Amendment","103rd Amendment"],"a":1,"e":"The 101st Amendment (2016) enabled GST."},
 {"q":"The 10 percent reservation for Economically Weaker Sections came via the","o":["102nd Amendment","103rd Amendment","104th Amendment","105th Amendment"],"a":1,"e":"The 103rd Amendment (2019) added Articles 15(6) and 16(6)."},
 {"q":"One-third reservation for women in legislatures was provided by the","o":["105th Amendment","106th Amendment","107th Amendment","104th Amendment"],"a":1,"e":"The 106th Amendment (2023), to take effect after delimitation."},
 {"q":"The Constitution originally had how many Schedules?","o":["8","10","12","14"],"a":0,"e":"It began with 8 Schedules; there are 12 today."},
 {"q":"The Union, State and Concurrent Lists are in the","o":["Sixth Schedule","Seventh Schedule","Ninth Schedule","Tenth Schedule"],"a":1,"e":"The Seventh Schedule distributes legislative subjects."},
 {"q":"The procedure for amending the Constitution is laid down in","o":["Article 356","Article 360","Article 368","Article 370"],"a":2,"e":"Article 368 provides for three types of amendment."},
 {"q":"Article 17 deals with the","o":["Abolition of titles","Abolition of untouchability","Protection of life","Freedom of speech"],"a":1,"e":"Untouchability is abolished and its practice forbidden."},
]

QUIZ_JS = r"""(function(){
var qs=[],idx=0,score=0,answered=false;
function esc(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;");}
function shuffle(a){for(var i=a.length-1;i>0;i--){var j=Math.floor(Math.random()*(i+1));var t=a[i];a[i]=a[j];a[j]=t;}return a;}
function render(){
  answered=false;
  var q=qs[idx], box=document.getElementById("quiz-box");
  box.innerHTML='<div class="quiz-progress">Question '+(idx+1)+" of "+qs.length+'</div>'
    +'<div class="quiz-bar"><i style="width:'+Math.round(idx/qs.length*100)+'%"></i></div>'
    +'<h3 class="quiz-q">'+esc(q.q)+"</h3>"
    +'<div class="quiz-opts">'+q.o.map(function(o,i){return '<button class="quiz-opt" data-i="'+i+'">'+esc(o)+"</button>";}).join("")+"</div>"
    +'<p class="quiz-explain" id="quiz-explain" hidden></p>'
    +'<button class="btn" id="quiz-next" hidden>'+(idx+1===qs.length?"See result":"Next question")+"</button>";
  Array.prototype.forEach.call(box.querySelectorAll(".quiz-opt"),function(b){
    b.addEventListener("click",function(){
      if(answered)return;answered=true;
      var pick=parseInt(b.getAttribute("data-i"),10), ok=pick===q.a;
      if(ok)score++;
      Array.prototype.forEach.call(box.querySelectorAll(".quiz-opt"),function(x){
        var i=parseInt(x.getAttribute("data-i"),10);
        if(i===q.a)x.classList.add("correct");else if(i===pick)x.classList.add("wrong");
        x.disabled=true;
      });
      var ex=document.getElementById("quiz-explain");
      ex.hidden=false;ex.innerHTML=(ok?"Correct. ":"Not quite. ")+esc(q.e||"");
      document.getElementById("quiz-next").hidden=false;
    });
  });
  document.getElementById("quiz-next").addEventListener("click",function(){
    idx++;if(idx<qs.length)render();else finish();
  });
}
function finish(){
  var best=0;try{best=parseInt(localStorage.getItem("vs-quiz-best")||"0",10)||0;}catch(e){}
  if(score>best){best=score;try{localStorage.setItem("vs-quiz-best",String(best));}catch(e){}}
  var msg=score===qs.length?"Perfect score. Constitution master.":score>=qs.length*0.7?"Strong. A little revision and you are exam-ready.":score>=qs.length*0.4?"Decent start. Read the summaries and try again.":"Start with the Constitution summary, then retry.";
  document.getElementById("quiz-box").innerHTML='<div class="quiz-done"><div class="quiz-score">'+score+" / "+qs.length+'</div><p>'+msg+'</p><p class="muted">Best score on this device: '+best+" / "+qs.length+'</p><button class="btn" id="quiz-restart">Try again</button></div>';
  document.getElementById("quiz-restart").addEventListener("click",function(){idx=0;score=0;qs=shuffle(qs.slice());render();});
}
fetch("assets/quiz.json").then(function(r){return r.json();}).then(function(j){qs=shuffle(j);render();})
.catch(function(){document.getElementById("quiz-box").innerHTML='<p class="muted">Could not load the quiz.</p>';});
})();"""

def quiz_page():
    body = """<div class="page-head"><h1>Constitution Quiz</h1><p class="lede">Twenty multiple-choice questions on the Constitution of India, with answers and explanations. Your best score is saved on this device.</p></div>
<div class="quiz-wrap" id="quiz-box"><p class="muted">Loading quiz...</p></div>
<p><a class="btn ghost" href="study.html">&larr; Back to Study</a></p>"""
    return page_shell("Constitution Quiz",
        "Test your knowledge of the Constitution of India with 20 multiple-choice questions and explanations.",
        "study", body, "<script>" + QUIZ_JS + "</script>")

def build():
    os.makedirs(SITE, exist_ok=True)
    os.makedirs(CONTENT, exist_ok=True)
    os.makedirs(BLOGSDIR, exist_ok=True)
    fetch_motogp()  # server-side; api.motogp.com has no CORS headers for browsers
    fetch_horoscope()  # server-side; freehoroscopeapi.com has no CORS headers
    rows = parse_log()
    posts = []
    used_slugs = set()

    # user blogs first: their file slugs, so caption-based duplicates can merge into them
    def _file_slug(fn):
        return slugify(os.path.splitext(fn)[0])
    user_files = sorted(fn for fn in os.listdir(BLOGSDIR)
                        if fn.endswith(".md") and not fn.upper().startswith("README"))
    user_file_slugs = [_file_slug(fn) for fn in user_files]
    # row slug -> user file slug it was absorbed into (full article replaces thin caption page)
    absorbed = {}
    for r in rows:
        dname = find_post_dir(r)
        dpath = os.path.join(POSTS, dname) if dname else None
        slug = dname or (slugify(r["date"] + "-" + r["story"]) or "post")
        if r["is_blog"] and dname:
            merged_into = next((ufs for ufs in user_file_slugs
                                if ufs.startswith(slug + "-")), None)
            if merged_into:
                absorbed[merged_into] = r["url"]
                continue  # merged into the full user blog; skip the thin caption page
        base = slug
        n = 2
        while slug in used_slugs:
            slug = f"{base}-{n}"
            n += 1
        used_slugs.add(slug)
        caption = load_caption(dpath) if dpath else ""
        art = parse_article(caption, r)
        blocks = load_body_md(slug)
        if blocks is None and dname:
            blocks = load_body_md(dname)
        hero_src, gallery_srcs = post_images(dpath)
        if hero_src:
            hero = copy_image(hero_src, f"assets/articles/{slug}/hero.jpg", 1200)
            thumb = copy_image(hero_src, f"assets/thumbs/{slug}.jpg", 640)
        else:
            hero = thumb = "assets/placeholder.svg"
        gallery = [copy_image(g, f"assets/articles/{slug}/{os.path.basename(g)}", 1080)
                   for g in gallery_srcs]
        if blocks is None:
            blocks = art["paras"]
        og_srcs = []
        if dpath:
            cp = os.path.join(dpath, "caption.txt")
            if os.path.exists(cp):
                og_srcs.append(cp)
        for mdp in (os.path.join(CONTENT, slug + ".md"),
                    os.path.join(CONTENT, (dname or "") + ".md")):
            if os.path.exists(mdp):
                og_srcs.append(mdp)
        posts.append({**r, "slug": slug, "art": art, "blocks": blocks,
                      "hero": hero, "thumb": thumb, "gallery": gallery,
                      "excerpt": excerpt_of(blocks, r["story"]),
                      "og_srcs": og_srcs})
    # website-only articles: content/<date>-<slug>.md with no posts-log row
    # (the daily 20-news website edition). First line = title, rest = body.
    for fn in sorted(os.listdir(CONTENT)):
        if not fn.endswith(".md"):
            continue
        wslug = fn[:-3]
        if wslug in used_slugs:
            continue
        dm = re.match(r"(\d{4}-\d{2}-\d{2})-", wslug)
        if not dm:
            continue
        wblocks = load_body_md(wslug)
        if not wblocks or len(wblocks) < 2:
            continue
        wtitle = wblocks[0].strip()
        wbody = wblocks[1:]
        wsource = ""
        wparas = []
        for b in wbody:
            if b.startswith("Source:"):
                wsource = b[len("Source:"):].strip()
            else:
                wparas.append(b)
        used_slugs.add(wslug)
        wart = {"title": wtitle, "paras": wparas, "source": wsource,
                "visuals": "", "tags": []}
        posts.append({"date": dm.group(1), "slot": "website", "format": "article",
                      "story": wtitle, "sources": wsource, "media": "",
                      "url": "", "is_blog": False, "slug": wslug, "art": wart,
                      "blocks": wparas,
                      "hero": "assets/placeholder.svg",
                      "thumb": "assets/placeholder.svg", "gallery": [],
                      "excerpt": excerpt_of(wbody, wtitle),
                      "og_srcs": [os.path.join(CONTENT, fn)]})
    # user blogs from blogs/*.md (full articles; absorb matching thin caption pages)
    user_blogs = []
    for fn in user_files:
        b = blog_page_from_file(os.path.join(BLOGSDIR, fn))
        if b["slug"] in absorbed:
            b["url"] = absorbed[b["slug"]]
        if b["slug"] in used_slugs:
            b["slug"] += "-blog"
        used_slugs.add(b["slug"])
        user_blogs.append(b)

    news = sorted([p for p in posts if not p["is_blog"]],
                  key=lambda p: (p["date"], p["slot"]), reverse=True)
    blog_posts = sorted([p for p in posts if p["is_blog"]] + user_blogs,
                        key=lambda p: (p["date"], p["slot"]), reverse=True)

    # topic tags + og title cards for every article
    for p in news + blog_posts:
        p["topic_tags"] = assign_topic_tags(p)
        p["og"] = make_og_image(p)
    all_sorted = sorted(news + blog_posts,
                        key=lambda p: (p["date"], p["slot"]), reverse=True)

    def latest_for(p, n=5):
        return [q for q in all_sorted if q["slug"] != p["slug"]][:n]

    # article pages (news)
    posts_root = os.path.join(SITE, "posts")
    os.makedirs(posts_root, exist_ok=True)
    for d in os.listdir(posts_root):
        if os.path.isdir(os.path.join(posts_root, d)) and d not in {p["slug"] for p in news}:
            shutil.rmtree(os.path.join(posts_root, d))
    for i, p in enumerate(news):
        adir = os.path.join(posts_root, p["slug"])
        os.makedirs(adir, exist_ok=True)
        with open(os.path.join(adir, "index.html"), "w", encoding="utf-8") as f:
            f.write(article_page(p,
                                 news[i - 1] if i > 0 else None,
                                 news[i + 1] if i < len(news) - 1 else None,
                                 related_posts(p, news), latest_for(p)))
    # blog pages
    blog_root = os.path.join(SITE, "blog")
    os.makedirs(blog_root, exist_ok=True)
    for d in os.listdir(blog_root):
        if os.path.isdir(os.path.join(blog_root, d)) and d not in {p["slug"] for p in blog_posts}:
            shutil.rmtree(os.path.join(blog_root, d))
    for i, p in enumerate(blog_posts):
        adir = os.path.join(blog_root, p["slug"])
        os.makedirs(adir, exist_ok=True)
        with open(os.path.join(adir, "index.html"), "w", encoding="utf-8") as f:
            f.write(article_page(p,
                                 blog_posts[i - 1] if i > 0 else None,
                                 blog_posts[i + 1] if i < len(blog_posts) - 1 else None,
                                 related_posts(p, blog_posts), latest_for(p)))

    # forgotten heroes (heroes/*.md; only those with date <= today publish)
    hero_posts = []
    if os.path.isdir(HEROESDIR):
        for fn in sorted(os.listdir(HEROESDIR)):
            if not fn.endswith(".md"):
                continue
            hpath = os.path.join(HEROESDIR, fn)
            if hero_date_of(hpath) > TODAY:
                continue
            h = hero_page_from_file(hpath)
            if h["slug"] in used_slugs:
                h["slug"] += "-hero"
            used_slugs.add(h["slug"])
            hero_posts.append(h)
    hero_posts.sort(key=lambda p: (p["date"], p["slug"]), reverse=True)
    for p in hero_posts:
        p["og"] = make_og_image(p)
    hero_root = os.path.join(SITE, "heroes")
    os.makedirs(hero_root, exist_ok=True)
    for d in os.listdir(hero_root):
        if os.path.isdir(os.path.join(hero_root, d)) and d not in {p["slug"] for p in hero_posts}:
            shutil.rmtree(os.path.join(hero_root, d))
    for i, p in enumerate(hero_posts):
        adir = os.path.join(hero_root, p["slug"])
        os.makedirs(adir, exist_ok=True)
        latest_heroes = [q for q in hero_posts if q["slug"] != p["slug"]][:5]
        with open(os.path.join(adir, "index.html"), "w", encoding="utf-8") as f:
            f.write(article_page(p,
                                 hero_posts[i - 1] if i > 0 else None,
                                 hero_posts[i + 1] if i < len(hero_posts) - 1 else None,
                                 related_posts(p, hero_posts), latest_heroes))

    # ---------- heroes index ----------
    hgroups = {}
    for p in hero_posts:
        hgroups.setdefault(p["date"], []).append(p)
    hblocks = []
    for d in sorted(hgroups, reverse=True):
        items = "".join(card_html(p) for p in hgroups[d])
        hblocks.append(f'<div class="day-group"><h3>{d}</h3><div class="grid">{items}</div></div>')
    heroidx = f"""<!DOCTYPE html>
<html lang="en">
<head>
{head("Forgotten Heroes", "One freedom fighter a day, whose name history forgot. Verified portraits and stories of India's unsung heroes.", 0)}
</head>
<body>
{topbar(0, 'heroes')}
<section class="hero" style="padding:2.6rem 1.2rem">
<div class="hero-inner">
<div class="kicker">Forgotten Heroes</div>
<h2>The names history forgot</h2>
<p>One freedom fighter every day. Not the names in every textbook, but the ones who bled for India and were left out of the story. Every portrait and every fact verified.</p>
</div>
</section>
<main class="wrap">
<div class="sec-head"><h2 class="sec-title">All heroes ({len(hero_posts)})</h2>
<input class="search" id="sitesearch" type="search" placeholder="Search heroes..." aria-label="Search heroes"></div>
{''.join(hblocks)}
</main>
{footer(0)}
<button class="totop" id="totop" aria-label="Back to top">&uarr;</button>
{JS}
</body>
</html>"""
    with open(os.path.join(SITE, "heroes.html"), "w", encoding="utf-8") as f:
        f.write(heroidx)

    # drop orphaned per-article assets (from merged/removed pages)
    live_slugs = {p["slug"] for p in news} | {p["slug"] for p in blog_posts}
    art_root = os.path.join(SITE, "assets", "articles")
    if os.path.isdir(art_root):
        for d in os.listdir(art_root):
            if os.path.isdir(os.path.join(art_root, d)) and d not in live_slugs:
                shutil.rmtree(os.path.join(art_root, d))
    th_root = os.path.join(SITE, "assets", "thumbs")
    if os.path.isdir(th_root):
        for fn in os.listdir(th_root):
            if fn.endswith(".jpg") and fn[:-4] not in live_slugs:
                os.remove(os.path.join(th_root, fn))

    with open(os.path.join(SITE, "styles.css"), "w", encoding="utf-8") as f:
        f.write(BASE_CSS)
    os.makedirs(os.path.join(SITE, "assets"), exist_ok=True)
    with open(os.path.join(SITE, "assets", "placeholder.svg"), "w") as f:
        f.write(PLACEHOLDER_SVG)

    # ---------- homepage ----------
    hero = news[0] if news else blog_posts[0]
    latest_news = news[1:10]
    latest_blog = blog_posts[:3]
    if hero_posts:
        doy = datetime.now(IST).timetuple().tm_yday
        hp = hero_posts[doy % len(hero_posts)]
        hero_spot = f"""<div class="sec-head" style="margin-top:2.8rem"><h2 class="sec-title">Forgotten Hero of the Day</h2><a class="sec-link" href="heroes.html">All heroes &rarr;</a></div>
<div class="grid">{card_html(hp)}</div>"""
    else:
        hero_spot = ""
    hsec = "blog" if hero["is_blog"] else "posts"
    ht = html.escape(hero["art"]["title"])
    tick_seq = "".join(
        f"""<a href="{'blog' if p['is_blog'] else 'posts'}/{p['slug']}/">{html.escape(p['art']['title'])}</a><span class="tick-sep">&nbsp;&bull;&nbsp;</span>"""
        for p in latest_news[:5])
    ticker = f"""<div class="ticker" aria-label="Latest headlines"><span class="ticker-label" data-i18n="ticker_latest">Latest</span><div class="ticker-view"><div class="ticker-track">{tick_seq}{tick_seq}</div></div></div>"""
    trend_items = "".join(
        f"""<a class="trend-item" href="{'blog' if p['is_blog'] else 'posts'}/{p['slug']}/"{f' data-ig-url="{html.escape(p["ig_url"])}"' if p.get('ig_url') else ''}>
<img src="{p['thumb']}" alt="" loading="lazy" decoding="async">
<div class="trend-body"><p>{html.escape(p['art']['title'])}</p><span class="trend-time">{rel_time(p)}</span></div></a>"""
        for p in all_sorted[:8])
    trend_strip = f"""<div class="trend-strip" aria-label="Trending now"><div class="trend-head"><span class="trend-label" data-i18n="trending_now">Trending now</span></div><div class="trend-scroll">{trend_items}</div></div>"""
    reading_html = """<section aria-label="Your reading" style="margin-top:2.8rem"><div class="sec-head"><h2 class="sec-title" data-i18n="your_reading">Your reading</h2></div><section id="bookmarks"><h2 class="sec-title" data-i18n="bookmarks">Your bookmarks</h2></section><section id="recently-viewed"><h2 class="sec-title" data-i18n="recently_viewed">Recently viewed</h2></section></section>"""
    index = f"""<!DOCTYPE html>
<html lang="en">
<head>
{head("News, Analysis & Opinions from India",
      "Varta and Samkara: verified news reels, explainers and opinion pieces from India. Every story verified against at least two independent sources.", 0, "", jsonld_home(), canonical=SITE_URL, og_type="website")}
</head>
<body>
{topbar(0, 'home')}
{ticker}
{trend_strip}
<section class="hero">
<div class="hero-inner">
<div class="kicker"{f' data-ig-url="{html.escape(hero["ig_url"])}"' if hero.get('ig_url') else ''}><span data-i18n="top_story">Top story</span> &bull; {hero['date']}</div>
<h2>{html.escape(hero['art']['title'])}</h2>
<p>{html.escape(hero['excerpt'])}</p>
<div class="cta-row">
<a class="btn" href="posts/{hero['slug']}/" data-i18n="read_full_story">Read full story</a>
<a class="btn ghost" href="{hero['url']}" target="_blank" rel="noopener" data-i18n="watch_instagram">Watch on Instagram</a>
</div>
</div>
</section>
<main class="wrap">
<div class="sec-head"><h2 class="sec-title" data-i18n="latest_news">Latest News</h2><a class="sec-link" href="archive.html"><span data-i18n="all_news">All news</span> &rarr;</a></div>
<div class="grid">
{''.join(card_html(p) for p in latest_news)}
</div>
<div class="sec-head" style="margin-top:2.8rem"><h2 class="sec-title" data-i18n="from_blog">From the Blog</h2><a class="sec-link" href="blog.html"><span data-i18n="all_opinions">All opinions</span> &rarr;</a></div>
<div class="grid">
{''.join(card_html(p) for p in latest_blog)}
</div>
{hero_spot}
{reading_html}
<section class="about reveal">
<h2>About <span>Varta &amp; Samkara</span></h2>
<p><strong>Varta</strong> means discourse, <strong>Samkara</strong> means impression. Verified news, sharp explainers and honest opinion from India, five posts a day.</p>
<p>Every news story is verified against at least two independent sources before it goes up. Opinion pieces are labelled as opinion and carry the author's name. AI-generated visuals are always disclosed.</p>
<p>Follow the daily slate on Instagram: <a href="{IG}" target="_blank" rel="noopener" style="color:var(--saffron);font-weight:700">@vartaandsamkaraindia</a></p>
</section>
</main>
{footer(0)}
<button class="totop" id="totop" aria-label="Back to top">&uarr;</button>
{JS}
</body>
</html>"""
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(index)

    # ---------- news archive ----------
    groups = {}
    for p in news:
        groups.setdefault(p["date"], []).append(p)
    blocks = []
    for d in sorted(groups, reverse=True):
        items = []
        for p in groups[d]:
            items.append(f"""<div class="list-item reveal" data-search="{html.escape(p['art']['title'])} {html.escape(p['slot'])}">
<a href="posts/{p['slug']}/"><img src="{p['thumb']}" alt="" loading="lazy" decoding="async"></a>
<div class="li-body">
<div class="meta"><span class="tag">{html.escape(p['format'])}</span><span>{html.escape(p['slot'])}</span></div>
<h4><a href="posts/{p['slug']}/">{html.escape(p['art']['title'])}</a></h4>
</div></div>""")
        blocks.append(f'<div class="day-group"><h3>{d}</h3>{"".join(items)}</div>')
    archive = f"""<!DOCTYPE html>
<html lang="en">
<head>
{head("News Archive", "Every verified news story published by Varta and Samkara, grouped by date.", 0)}
</head>
<body>
{topbar(0, 'news')}
<main class="wrap">
<div class="sec-head"><h2 class="sec-title">News Archive ({len(news)} stories)</h2>
<input class="search" id="sitesearch" type="search" placeholder="Search stories..." aria-label="Search stories"></div>
{''.join(blocks)}
</main>
{footer(0)}
<button class="totop" id="totop" aria-label="Back to top">&uarr;</button>
{JS}
</body>
</html>"""
    with open(os.path.join(SITE, "archive.html"), "w", encoding="utf-8") as f:
        f.write(archive)

    # ---------- timeline ----------
    with open(os.path.join(SITE, "timeline.html"), "w", encoding="utf-8") as f:
        f.write(timeline_page(cluster_threads(news)))

    # ---------- blog index ----------
    bgroups = {}
    for p in blog_posts:
        bgroups.setdefault(p["date"], []).append(p)
    bblocks = []
    for d in sorted(bgroups, reverse=True):
        items = "".join(card_html(p) for p in bgroups[d])
        bblocks.append(f'<div class="day-group"><h3>{d}</h3><div class="grid">{items}</div></div>')
    blogidx = f"""<!DOCTYPE html>
<html lang="en">
<head>
{head("Blog: Opinions & Analysis", "Opinions, analysis and notes from the heart by Rahil Sahu, founder of Varta and Samkara.", 0)}
</head>
<body>
{topbar(0, 'blog')}
<section class="hero" style="padding:2.6rem 1.2rem">
<div class="hero-inner">
<div class="kicker">The Blog</div>
<h2>Opinions, analysis &amp; notes from the heart</h2>
<p>What I think about India, the world, and everything in between. These are personal views, not news reports.</p>
</div>
</section>
<main class="wrap">
<div class="sec-head"><h2 class="sec-title">All opinions ({len(blog_posts)})</h2>
<input class="search" id="sitesearch" type="search" placeholder="Search opinions..." aria-label="Search opinions"></div>
{''.join(bblocks)}
</main>
{footer(0)}
<button class="totop" id="totop" aria-label="Back to top">&uarr;</button>
{JS}
</body>
</html>"""
    with open(os.path.join(SITE, "blog.html"), "w", encoding="utf-8") as f:
        f.write(blogidx)

    # ---------- scores center ----------
    with open(os.path.join(SITE, "assets", "scores.js"), "w", encoding="utf-8") as f:
        f.write(SCORES_JS)
    with open(os.path.join(SITE, "scores.html"), "w", encoding="utf-8") as f:
        f.write(scores_page())

    # ---------- markets, policy tracker, study ----------
    with open(os.path.join(SITE, "assets", "policy.json"), "w", encoding="utf-8") as f:
        json.dump(POLICY_ENTRIES, f, ensure_ascii=False, indent=1)
    with open(os.path.join(SITE, "markets.html"), "w", encoding="utf-8") as f:
        f.write(markets_page())
    with open(os.path.join(SITE, "policy.html"), "w", encoding="utf-8") as f:
        f.write(policy_page())

    # ---------- videos page (Instagram reels) ----------
    videos = []
    for i, r_ in enumerate(rows):
        if r_["format"].lower() != "reel" or "/reel/" not in r_["url"]:
            continue
        thumb = ""
        dname = find_post_dir(r_)
        if dname:
            dpath = os.path.join(POSTS, dname)
            for cn in ("cover.jpg", "cover.jpeg", "cover.png"):
                cp = os.path.join(dpath, cn)
                if os.path.isfile(cp):
                    vslug = slugify(r_["date"] + "-" + r_["story"])[:48] or f"reel-{i}"
                    thumb = copy_image(cp, f"assets/videos/{vslug}.jpg", 640)
                    break
        videos.append({"story": r_["story"], "date": r_["date"],
                       "url": r_["url"], "thumb": thumb})
    videos.sort(key=lambda v: v["date"], reverse=True)
    with open(os.path.join(SITE, "videos.html"), "w", encoding="utf-8") as f:
        f.write(videos_page(videos))

    # ---------- site-wide search index ----------
    search_entries = []
    def _add_search(title, url, typ, excerpt, date=""):
        search_entries.append({"title": title, "url": url, "type": typ,
                               "excerpt": (excerpt or "")[:220], "date": date})
    for p in news:
        _add_search(p["art"]["title"], f"posts/{p['slug']}/", "news", p["excerpt"], p["date"])
    for p in blog_posts:
        _add_search(p["art"]["title"], f"blog/{p['slug']}/",
                    "opinion" if p["is_blog"] else "news", p["excerpt"], p["date"])
    for p in hero_posts:
        _add_search(p["art"]["title"], f"heroes/{p['slug']}/", "hero", p["excerpt"], p["date"])
    try:
        with open(os.path.join(SITE, "assets", "factchecks.json"), encoding="utf-8") as jf:
            fc_entries = json.load(jf)
    except Exception:
        fc_entries = []
    for e in POLICY_ENTRIES:
        _add_search(e["title"], "policy.html", "policy", e["summary"], e["date"])
    for e in fc_entries:
        _add_search(f"Fact Check: {e['claim']}", "factcheck.html", "factcheck", e["summary"], e["date"])
    ca_src = "assets-src/constitution-articles.json"
    if os.path.exists(ca_src):
        try:
            _cadata = json.load(open(ca_src, encoding="utf-8"))
            for a in _cadata.get("articles", []):
                _add_search(f"Article {a.get('n')}: {a.get('title', '')}",
                            "articles.html", "constitution", a.get("s", ""))
        except Exception:
            pass
    with open(os.path.join(SITE, "assets", "search-index.json"), "w", encoding="utf-8") as f:
        json.dump(search_entries, f, ensure_ascii=False)
    study_defs = [
        ("constitution.html", "Constitution of India: Complete Summary",
         "Preamble, salient features, all 22 Parts, 12 Schedules, rights, duties and landmark amendments, summarised for UPSC and law aspirants.",
         "content-study/constitution.md",
         "Constitution of India: Complete Summary for UPSC and Law Aspirants",
         "Preamble, salient features, Parts, Schedules, rights, duties and landmark amendments, summarised for UPSC and law aspirants."),
        ("economics.html", "Economics: Fundamentals and Indian Economy",
         "Micro and macro basics plus the Indian economy: RBI, inflation, budget, taxes, banking and key terms, built for government exams.",
         "content-study/economics.md",
         "Economics for Government Exams: Fundamentals and Indian Economy",
         "Fundamentals of economics and the Indian economy summarised for UPSC, SSC, Banking and state exam preparation."),
        ("geography.html", "Geography: Physical and Indian Geography",
         "Earth, atmosphere, oceans, Indian rivers, soils, climate, agriculture and high-yield facts, built for government exams.",
         "content-study/geography.md",
         "Geography for Government Exams: Physical and Indian Geography",
         "Physical geography and Indian geography summarised for UPSC, SSC, Banking and state exam preparation."),
        ("history-ancient.html", "Ancient Indian History",
         "Harappa, the Vedas, Mahajanapadas, Buddhism and Jainism, Mauryas, Guptas and the southern kingdoms, with timelines and most-asked one-liners.",
         "content-study/history-ancient.md",
         "Ancient Indian History for Government Exams",
         "Ancient Indian history from prehistory to the Cholas, summarised for UPSC, UPPSC, SSC and state exam preparation."),
        ("history-medieval.html", "Medieval Indian History",
         "Delhi Sultanate, Vijayanagara, the Mughals, Marathas, Bhakti and Sufi movements, with timelines and most-asked one-liners.",
         "content-study/history-medieval.md",
         "Medieval Indian History for Government Exams",
         "Medieval Indian history from the Sultanate to the Marathas, summarised for UPSC, UPPSC, SSC and state exam preparation."),
        ("history-modern.html", "Modern Indian History",
         "British conquest, 1857, the freedom movement phase by phase, constitutional developments and independence, with timelines and most-asked one-liners.",
         "content-study/history-modern.md",
         "Modern Indian History for Government Exams",
         "Modern Indian history from Plassey to independence, summarised for UPSC, UPPSC, SSC and state exam preparation."),
        ("upsc-syllabus.html", "UPSC Syllabus: Complete",
         "The full CSE syllabus: Prelims papers, all nine Mains papers, interview, marks and qualifying rules, mapped to the Study section.",
         "content-study/upsc-syllabus.md",
         "UPSC Syllabus: Complete Prelims and Mains",
         "The complete UPSC Civil Services syllabus with paper-wise topics, marks and exam pattern, mapped to the Study section."),
    ]
    study_cards = []
    for fname, card_t, card_d, md_path, page_t, page_d in study_defs:
        if os.path.exists(md_path):
            slug = fname[:-5]
            with open(os.path.join(SITE, fname), "w", encoding="utf-8") as f:
                f.write(study_article_page(slug, page_t, page_d, md_path))
            study_cards.append((fname, card_t, card_d))
    ca_src = "assets-src/constitution-articles.json"
    if os.path.exists(ca_src):
        data = json.load(open(ca_src, encoding="utf-8"))
        det = parse_article_details("content-study/articles.md")
        for a in data.get("articles", []):
            if a.get("n") in det:
                a["detail"] = det[a["n"]]
        with open(os.path.join(SITE, "assets", "constitution-articles.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        with open(os.path.join(SITE, "articles.html"), "w", encoding="utf-8") as f:
            f.write(articles_page())
        study_cards.append(("articles.html", "Constitution: All Articles",
            "Every article of the Constitution in plain language. Search by number or keyword, filter by Part, with detailed notes on the most-asked articles."))
    caf_src = "content-study/current-affairs.json"
    if os.path.exists(caf_src):
        shutil.copy2(caf_src, os.path.join(SITE, "assets", "current-affairs.json"))
        with open(os.path.join(SITE, "current-affairs.html"), "w", encoding="utf-8") as f:
            f.write(ca_page())
        study_cards.append(("current-affairs.html", "Daily Current Affairs",
            "A fresh exam-focused brief every morning: what happened, why it matters for your exam, and source links."))
    with open(os.path.join(SITE, "study.html"), "w", encoding="utf-8") as f:
        f.write(study_hub_page(study_cards))

    # ---------- today page + constitution quiz ----------
    with open(os.path.join(SITE, "assets", "quiz.json"), "w", encoding="utf-8") as f:
        json.dump(QUIZ_QUESTIONS, f, ensure_ascii=False, indent=1)
    with open(os.path.join(SITE, "today.html"), "w", encoding="utf-8") as f:
        f.write(today_page())
    with open(os.path.join(SITE, "quiz.html"), "w", encoding="utf-8") as f:
        f.write(quiz_page())

    # ---------- horoscope ----------
    with open(os.path.join(SITE, "horoscope.html"), "w", encoding="utf-8") as f:
        f.write(horoscope_page())

    # ---------- read-aloud engine ----------
    with open(os.path.join(SITE, "assets", "readaloud.js"), "w", encoding="utf-8") as f:
        f.write(READALOUD_JS)

    # ---------- tag pages ----------
    tag_map = {}
    for p in news + blog_posts:
        for t in p["topic_tags"]:
            tag_map.setdefault(t, []).append(p)
    tags_root = os.path.join(SITE, "tags")
    os.makedirs(tags_root, exist_ok=True)
    # drop stale tag dirs
    for d in os.listdir(tags_root):
        if os.path.isdir(os.path.join(tags_root, d)) and \
                d not in {tag_slug(t) for t in tag_map}:
            shutil.rmtree(os.path.join(tags_root, d))
    for t in sorted(tag_map):
        tdir = os.path.join(tags_root, tag_slug(t))
        os.makedirs(tdir, exist_ok=True)
        tposts = sorted(tag_map[t], key=lambda p: (p["date"], p["slot"]),
                        reverse=True)
        cards = "".join(card_html(p, depth=2) for p in tposts)
        tpage = f"""<!DOCTYPE html>
<html lang="en">
<head>
{head(f"Stories tagged {t}", f"Every Varta and Samkara story tagged {t}.", 2)}
</head>
<body>
{topbar(2, 'tags')}
<main class="wrap">
<div class="sec-head"><h2 class="sec-title">Tag: {html.escape(t)} ({len(tposts)})</h2>
<a class="sec-link" href="../">All tags &rarr;</a></div>
<div class="grid">
{cards}
</div>
</main>
{footer(2)}
<button class="totop" id="totop" aria-label="Back to top">&uarr;</button>
{JS}
</body>
</html>"""
        with open(os.path.join(tdir, "index.html"), "w", encoding="utf-8") as f:
            f.write(tpage)
    cloud = "".join(
        f'<a class="tagchip" href="{tag_slug(t)}/">{html.escape(t)} '
        f'<span class="tag-count">({len(tag_map[t])})</span></a>'
        for t in sorted(tag_map))
    tagsidx = f"""<!DOCTYPE html>
<html lang="en">
<head>
{head("Browse by Tag", "Browse every Varta and Samkara story by topic tag.", 1)}
</head>
<body>
{topbar(1, 'tags')}
<main class="wrap">
<div class="sec-head"><h2 class="sec-title">Browse by tag</h2></div>
<div class="tag-cloud">
{cloud}
</div>
</main>
{footer(1)}
<button class="totop" id="totop" aria-label="Back to top">&uarr;</button>
{JS}
</body>
</html>"""
    with open(os.path.join(tags_root, "index.html"), "w", encoding="utf-8") as f:
        f.write(tagsidx)

    # ---------- topic hub pages ----------
    topic_groups = {t: [] for t in TOPIC_SLUGS}
    for p in news + blog_posts:
        txt = p.get("story", "") or ""
        cap = (p["art"]["paras"][0] if p["art"]["paras"] else p.get("excerpt", ""))
        topic_groups[classify_topic(txt, cap)].append(p)
    topics_root = os.path.join(SITE, "topics")
    os.makedirs(topics_root, exist_ok=True)
    for fname in os.listdir(topics_root):
        if fname.endswith(".html") and fname not in {TOPIC_SLUGS[t] + ".html" for t in TOPIC_SLUGS} and fname != "index.html":
            os.remove(os.path.join(topics_root, fname))
    counts = {}
    for t in TOPIC_SLUGS:
        tposts = sorted(topic_groups[t], key=lambda p: (p["date"], p["slot"]), reverse=True)
        counts[t] = len(tposts)
    for t in TOPIC_SLUGS:
        tposts = sorted(topic_groups[t], key=lambda p: (p["date"], p["slot"]), reverse=True)
        with open(os.path.join(topics_root, TOPIC_SLUGS[t] + ".html"), "w", encoding="utf-8") as f:
            f.write(topic_page(t, tposts, counts))
    with open(os.path.join(topics_root, "index.html"), "w", encoding="utf-8") as f:
        f.write(topics_index_page(counts))

    # ---------- fact check ----------
    with open(os.path.join(SITE, "factcheck.html"), "w", encoding="utf-8") as f:
        f.write(factcheck_page(fc_entries))

    # ---------- pwa: manifest, service worker, offline page ----------
    make_icon(192)
    make_icon(512)
    manifest = {
        "name": "Varta & Samkara",
        "short_name": "VartaSamkara",
        "start_url": ".",
        "display": "standalone",
        "background_color": "#0a1a3c",
        "theme_color": "#0a1a3c",
        "description": "Verified news, sharp explainers and honest opinion from India.",
        "icons": [
            {"src": "assets/icon-192.png", "sizes": "192x192",
             "type": "image/png"},
            {"src": "assets/icon-512.png", "sizes": "512x512",
             "type": "image/png"},
        ],
    }
    with open(os.path.join(SITE, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    sw = """const CACHE = "vs-cache-v11";
const CORE = ["./", "index.html", "offline.html", "styles.css",
              "manifest.json", "assets/placeholder.svg", "assets/search-index.json",
              "scores.html", "assets/scores.js", "assets/motogp.json",
              "horoscope.html", "assets/horoscope.json",
              "markets.html", "policy.html", "assets/policy.json", "videos.html", "articles.html", "economics.html", "geography.html", "current-affairs.html", "assets/constitution-articles.json", "assets/current-affairs.json",
              "history-ancient.html", "history-medieval.html", "history-modern.html", "upsc-syllabus.html",
              "study.html", "constitution.html", "articles.html",
              "quiz.html", "assets/quiz.json",
              "factcheck.html", "timeline.html", "topics/index.html",
              "assets/i18n.json", "assets/headlines-hi.json", "assets/factchecks.json",
              "assets/lang-toggle.js", "assets/js/vs-reader.js", "assets/og-default.png",
              "today.html",
              "assets/readaloud.js"];
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE))
    .then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((ks) => Promise.all(ks.filter((k) => k !== CACHE)
      .map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  e.respondWith(
    caches.match(e.request).then((hit) => {
      const net = fetch(e.request).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      }).catch(() => (e.request.mode === "navigate"
        ? caches.match("offline.html") : undefined));
      return hit || net;
    })
  );
});
"""
    with open(os.path.join(SITE, "sw.js"), "w", encoding="utf-8") as f:
        f.write(sw)
    offline = f"""<!DOCTYPE html>
<html lang="en">
<head>
{head("You are offline", "Varta and Samkara offline page.", 0)}
</head>
<body>
{topbar(0, '')}
<section class="hero" style="padding:3rem 1.2rem">
<div class="hero-inner">
<div class="kicker">Offline</div>
<h2>You are offline.</h2>
<p>Check your connection and try again. Pages you have already visited may still be available from the cache.</p>
<div class="cta-row"><a class="btn" href="index.html">Try the homepage</a></div>
</div>
</section>
{footer(0)}
{JS}
</body>
</html>"""
    with open(os.path.join(SITE, "offline.html"), "w", encoding="utf-8") as f:
        f.write(offline)

    # ---------- custom 404 ----------
    notfound = f"""<!DOCTYPE html>
<html lang="en">
<head>
{head("Page not found", "The page you are looking for does not exist.", 0)}
</head>
<body>
{topbar(0, '')}
<section class="hero" style="padding:3rem 1.2rem">
<div class="hero-inner">
<div class="kicker">404</div>
<h2>That page does not exist.</h2>
<p>It may have moved, or the link may be wrong. Head back home or browse the full archive.</p>
<div class="cta-row">
<a class="btn" href="index.html">Go home</a>
<a class="btn ghost" href="archive.html">Browse the archive</a>
</div>
</div>
</section>
{footer(0)}
<button class="totop" id="totop" aria-label="Back to top">&uarr;</button>
{JS}
</body>
</html>"""
    with open(os.path.join(SITE, "404.html"), "w", encoding="utf-8") as f:
        f.write(notfound)

    # ---------- rss feed + sitemap ----------
    def rss_date(d):
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
        except (ValueError, TypeError):
            dt = datetime.now()
        return dt.strftime("%a, %d %b %Y 06:00:00 +0530")

    feed_items = []
    for p in sorted(news + blog_posts, key=lambda p: p["date"], reverse=True)[:60]:
        feed_items.append(f"""<item>
<title>{html.escape(p['art']['title'])}</title>
<link>{page_url(p)}</link>
<guid>{page_url(p)}</guid>
<pubDate>{rss_date(p['date'])}</pubDate>
<description>{html.escape(p['excerpt'])}</description>
</item>""")
    feed = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<title>Varta &amp; Samkara</title>
<link>https://rahilsahu.github.io/varta-samkara/</link>
<description>Verified news, sharp explainers and honest opinion from India.</description>
<language>en</language>
{''.join(feed_items)}
</channel></rss>"""
    with open(os.path.join(SITE, "feed.xml"), "w", encoding="utf-8") as f:
        f.write(feed)

    sm_urls = (["", "archive.html", "blog.html", "heroes.html", "scores.html", "horoscope.html",
               "markets.html", "policy.html", "videos.html", "study.html", "constitution.html",
               "articles.html", "economics.html", "geography.html",
               "history-ancient.html", "history-medieval.html", "history-modern.html",
               "upsc-syllabus.html", "quiz.html", "today.html", "current-affairs.html", "tags/",
               "factcheck.html", "timeline.html", "topics/",
               "topics/politics.html", "topics/economy.html", "topics/science-tech.html",
               "topics/sports.html", "topics/world.html", "topics/india.html"]
               + [f"tags/{tag_slug(t)}/" for t in sorted(tag_map)]
               + [f"posts/{p['slug']}/" for p in news]
               + [f"blog/{p['slug']}/" for p in blog_posts]
               + [f"heroes/{p['slug']}/" for p in hero_posts])
    sm = "".join(
        f"<url><loc>https://rahilsahu.github.io/varta-samkara/{u}</loc></url>"
        for u in sm_urls)
    sitemap = (f'<?xml version="1.0" encoding="utf-8"?>\n'
               f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>')
    with open(os.path.join(SITE, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(sitemap)
    with open(os.path.join(SITE, "robots.txt"), "w", encoding="utf-8") as f:
        f.write("User-agent: *\nAllow: /\nSitemap: https://rahilsahu.github.io/varta-samkara/sitemap.xml\n")

    print(f"news: {len(news)}, blog: {len(blog_posts)} "
          f"({len(user_blogs)} user blogs), heroes: {len(hero_posts)} published")
    if absorbed:
        print("merged caption pages into user blogs:",
              ", ".join(f"{u} <- IG post" for u in sorted(absorbed)))
    print("site written to", SITE)

if __name__ == "__main__":
    build()
