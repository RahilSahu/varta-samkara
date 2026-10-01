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

WS = "/home/hatch/workspace/job-campaign"
LOG = os.path.join(WS, "varta-samkara-posts-log.md")
POSTS = os.path.join(WS, "varta-samkara", "posts")
SITE = "/home/hatch/workspace/varta-samkara-website"
CONTENT = os.path.join(SITE, "content")
BLOGSDIR = os.path.join(SITE, "blogs")
THUMBS = os.path.join(SITE, "assets", "thumbs")
ARTICLES = os.path.join(SITE, "assets", "articles")
IG = "https://www.instagram.com/vartaandsamkaraindia/"

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
html{scroll-behavior:smooth}
body{font-family:'Segoe UI',system-ui,-apple-system,Roboto,'Noto Sans',Arial,sans-serif;
  background:var(--bg);color:var(--ink);line-height:1.65;
  -webkit-font-smoothing:antialiased;overflow-x:hidden}
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
.navlinks a{font-size:.95rem;opacity:.92;position:relative;padding:.2rem 0;transition:color .2s}
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
  background:var(--navy);margin:1.3rem 0 1.8rem;box-shadow:var(--shadow-lg);
  animation:fadeUp .6s ease both}
.article h1{font-size:clamp(1.55rem,4vw,2.35rem);line-height:1.28;margin:.7rem 0 1.1rem;
  color:var(--navy);animation:fadeUp .6s .1s ease both}
.article h2{font-size:1.3rem;color:var(--navy);margin:1.9rem 0 .8rem;
  padding-left:.7rem;border-left:4px solid var(--saffron)}
.article p.lede{font-size:1.16rem;color:#2c3648;margin-bottom:1.3rem;font-weight:500}
.article p.body{margin-bottom:1.15rem;font-size:1.03rem;color:#2a3342}
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
  border-bottom:2px solid var(--saffron)}
.ticker-label{background:var(--saffron);color:var(--navy);font-weight:800;font-size:.78rem;
  text-transform:uppercase;letter-spacing:.1em;display:flex;align-items:center;
  padding:.5rem .9rem;flex-shrink:0}
.ticker-view{overflow:hidden;flex:1;display:flex;align-items:center}
.ticker-track{display:inline-block;white-space:nowrap;max-width:max-content;
  padding:.5rem 0;animation:tickmove 25s linear infinite;color:#ffd9a3;font-size:.92rem;font-weight:600}
.ticker-track:hover{animation-play-state:paused;color:var(--saffron)}
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
    def link(href, label, key, extra=""):
        cls = ' class="active"' if active == key else ""
        return f'<a href="{r}{href}"{cls}{extra}>{label}</a>'
    return f"""<header class="topbar">
<div class="brand"><div class="wm">&#2357;</div><h1>VARTA <span>&amp;</span> SAMKARA</h1></div>
<nav class="navlinks" id="navlinks">{link('index.html','Home','home')}{link('archive.html','News','news')}{link('blog.html','Blog','blog')}{link('scores.html','Scores','scores',' data-scores-nav')}{link('horoscope.html','Horoscope','horoscope')}{link('tags/','Tags','tags')}<a href="{IG}" target="_blank" rel="noopener">Instagram</a></nav>
<div class="top-actions">
<button class="theme-toggle" id="theme-toggle" aria-label="Toggle dark mode"><span id="theme-icon">&#9789;</span></button>
<button class="hamburger" id="burger" aria-label="Menu">&#9776;</button>
</div>
</header>"""

def footer(depth):
    r = rel(depth)
    return f"""<footer><div class="foot-inner">
<div>&copy; 2026 Varta &amp; Samkara. News verified, opinions owned.</div>
<div class="foot-links"><a href="{r}scores.html">Scores</a><a href="{r}horoscope.html">Horoscope</a><a href="{r}tags/">Tags</a><a href="{r}archive.html">Archive</a><a href="{r}feed.xml">RSS</a><a href="{r}sitemap.xml">Sitemap</a><a href="{IG}" target="_blank" rel="noopener">Instagram</a></div>
</div></footer>"""

def head(title, desc, depth, og_image="", extra_jsonld=""):
    r = rel(depth)
    og = (f'<meta property="og:image" content="{html.escape(og_image)}">' if og_image else "")
    tw = (f'<meta name="twitter:card" content="summary_large_image">\n'
          f'<meta name="twitter:image" content="{html.escape(og_image)}">' if og_image else "")
    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0a1a3c">
<title>{html.escape(title)} | Varta &amp; Samkara</title>
<script>(function(){{try{{var t=localStorage.getItem('vs-theme');if(!t){{t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}}document.documentElement.setAttribute('data-theme',t);}}catch(e){{}}}})();</script>
<meta name="description" content="{html.escape(desc)}">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(desc)}">
<meta property="og:type" content="article">
{og}
{tw}
<link rel="manifest" href="{r}manifest.json">
<link rel="stylesheet" href="{r}styles.css">
<link rel="alternate" type="application/rss+xml" title="Varta &amp; Samkara" href="{r}feed.xml">
<script>if('serviceWorker' in navigator){{window.addEventListener('load',function(){{navigator.serviceWorker.register('{r}sw.js').catch(function(){{}});}});}}</script>
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
    section = "blog" if p["is_blog"] else "posts"
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
        "datePublished": p["date"],
        "author": {"@type": "Organization", "name": "Varta & Samkara"},
        "publisher": {"@type": "Organization", "name": "Varta & Samkara"},
        "description": p["excerpt"],
        "mainEntityOfPage": page_url(p),
    }
    if img:
        data["image"] = img
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
    section = "blog" if p["is_blog"] else "posts"
    rt = reading_time(p.get("blocks"))
    return f"""<article class="card reveal" data-search="{html.escape(p['art']['title'])} {html.escape(p['excerpt'])}">
<a class="thumb" href="{r}{section}/{p['slug']}/"><img src="{r}{p['thumb']}" alt="{html.escape(p['art']['title'])}" loading="lazy" decoding="async"></a>
<div class="card-body">
<div class="meta">{tag}{chips}<span>{p['date']}</span><span>{rt} min read</span></div>
<h3><a href="{r}{section}/{p['slug']}/">{html.escape(p['art']['title'])}</a></h3>
<p>{html.escape(p['excerpt'])}</p>
<a class="read" href="{r}{section}/{p['slug']}/">Read full story &rarr;</a>
</div></article>"""

def article_page(p, prev_p, next_p, related=None, latest=None):
    art = p["art"]
    body = render_body(p["blocks"]) if p["blocks"] else "".join(
        f"<p class='body'>{html.escape(pa)}</p>" for pa in art["paras"])
    if not body.strip():
        body = f"<p class='body'>{html.escape(p['story'])}</p>"
    r = rel(2)
    rt = reading_time(p.get("blocks"))
    toc = toc_of(p["blocks"])
    toc_html = ""
    if len(toc) >= 3:
        lis = "".join(f'<li><a href="#{a}">{html.escape(t)}</a></li>' for a, t in toc)
        toc_html = f"""<nav class="toc" aria-label="On this page"><strong>On this page</strong><ul>{lis}</ul></nav>"""
    purl = page_url(p)
    share_txt = urllib.parse.quote(f"{art['title']} - Varta & Samkara")
    share_url = urllib.parse.quote(purl, safe="")
    share_html = f"""<div class="share-row"><span class="lbl">Share:</span>
<a class="share-btn" href="https://wa.me/?text={share_txt}%20{share_url}" target="_blank" rel="noopener">WhatsApp</a>
<a class="share-btn" href="https://twitter.com/intent/tweet?text={share_txt}&url={share_url}" target="_blank" rel="noopener">X</a>
<a class="share-btn" href="https://www.facebook.com/sharer/sharer.php?u={share_url}" target="_blank" rel="noopener">Facebook</a>
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
        psec = "blog" if prev_p["is_blog"] else "posts"
        nav += f"<a href='{r}{psec}/{prev_p['slug']}/'><span class='dir'>&larr; Newer</span>{html.escape(prev_p['art']['title'][:70])}</a>"
    else:
        nav += "<span></span>"
    if next_p:
        nsec = "blog" if next_p["is_blog"] else "posts"
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
            qsec = "blog" if q["is_blog"] else "posts"
            cards.append(f"""<article class="card">
<a class="thumb" href="{r}{qsec}/{q['slug']}/"><img src="{r}{q['thumb']}" alt="{html.escape(q['art']['title'])}" loading="lazy" decoding="async"></a>
<div class="card-body"><div class="meta"><span>{q['date']}</span></div>
<h3><a href="{r}{qsec}/{q['slug']}/">{html.escape(q['art']['title'])}</a></h3></div></article>""")
        related_html = f"""<section class="related"><h2>Keep reading</h2><div class="grid">{"".join(cards)}</div></section>"""
    sidebar_html = ""
    if latest:
        items = []
        for q in latest:
            qsec = "blog" if q["is_blog"] else "posts"
            items.append(f"""<a class="side-item" href="{r}{qsec}/{q['slug']}/">
<img src="{r}{q['thumb']}" alt="" loading="lazy" decoding="async">
<div><h4>{html.escape(q['art']['title'])}</h4><span class="sdate">{q['date']}</span></div></a>""")
        sidebar_html = f"""<aside class="latest-sidebar"><h2>Latest stories</h2>{"".join(items)}</aside>"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{head(art['title'], desc, 2, og_img, jsonld_article(p))}
</head>
<body>
<div class="progress" id="progress"></div>
{topbar(2, 'blog' if p['is_blog'] else 'news')}
<main class="wrap article-layout">
<div class="article-col article">
<div class="meta" style="margin-top:1rem">{meta_tag}<span>{p['date']}</span><span>{html.escape(p['slot'])}</span><span>{rt} min read</span></div>
{badge}
<h1>{html.escape(art['title'])}</h1>
{byline}
<button class="listen-cta" id="listen-btn" type="button"><span class="spk">&#9836;</span> Listen to this article</button>
<img class="article-hero" src="{r}{p['hero']}" alt="{html.escape(art['title'])}" decoding="async">
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
                meta[k.strip()] = v.strip()
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

def jsonld_scores():
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": "Varta & Samkara Scores Center",
        "url": f"{SITE_URL}scores.html",
        "description": ("Live scores and results: cricket, football, F1, UFC, "
                        "MotoGP and WWE. All times in IST."),
    }, ensure_ascii=False)

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
    hsec = "blog" if hero["is_blog"] else "posts"
    ht = html.escape(hero["art"]["title"])
    ticker = f"""<div class="ticker" aria-label="Breaking news"><span class="ticker-label">Breaking</span><div class="ticker-view"><a class="ticker-track" href="{hsec}/{hero['slug']}/"><span>{ht}</span><span class="tick-sep">&nbsp;&bull;&nbsp;</span><span>{ht}</span><span class="tick-sep">&nbsp;&bull;&nbsp;</span></a></div></div>"""
    index = f"""<!DOCTYPE html>
<html lang="en">
<head>
{head("News, Analysis & Opinions from India",
      "Varta and Samkara: verified news reels, explainers and opinion pieces from India. Every story verified against at least two independent sources.", 0, "", jsonld_home())}
</head>
<body>
{topbar(0, 'home')}
{ticker}
<section class="hero">
<div class="hero-inner">
<div class="kicker">Top story &bull; {hero['date']}</div>
<h2>{html.escape(hero['art']['title'])}</h2>
<p>{html.escape(hero['excerpt'])}</p>
<div class="cta-row">
<a class="btn" href="posts/{hero['slug']}/">Read full story</a>
<a class="btn ghost" href="{hero['url']}" target="_blank" rel="noopener">Watch on Instagram</a>
</div>
</div>
</section>
<main class="wrap">
<div class="sec-head"><h2 class="sec-title">Latest News</h2><a class="sec-link" href="archive.html">All news &rarr;</a></div>
<div class="grid">
{''.join(card_html(p) for p in latest_news)}
</div>
<div class="sec-head" style="margin-top:2.8rem"><h2 class="sec-title">From the Blog</h2><a class="sec-link" href="blog.html">All opinions &rarr;</a></div>
<div class="grid">
{''.join(card_html(p) for p in latest_blog)}
</div>
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
    sw = """const CACHE = "vs-cache-v4";
const CORE = ["./", "index.html", "offline.html", "styles.css",
              "manifest.json", "assets/placeholder.svg",
              "scores.html", "assets/scores.js", "assets/motogp.json",
              "horoscope.html", "assets/horoscope.json",
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

    sm_urls = (["", "archive.html", "blog.html", "scores.html", "horoscope.html",
               "feed.xml", "tags/"]
               + [f"tags/{tag_slug(t)}/" for t in sorted(tag_map)]
               + [f"posts/{p['slug']}/" for p in news]
               + [f"blog/{p['slug']}/" for p in blog_posts])
    sm = "".join(
        f"<url><loc>https://rahilsahu.github.io/varta-samkara/{u}</loc></url>"
        for u in sm_urls)
    sitemap = (f'<?xml version="1.0" encoding="utf-8"?>\n'
               f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>')
    with open(os.path.join(SITE, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(sitemap)

    print(f"news: {len(news)}, blog: {len(blog_posts)} "
          f"({len(user_blogs)} user blogs)")
    if absorbed:
        print("merged caption pages into user blogs:",
              ", ".join(f"{u} <- IG post" for u in sorted(absorbed)))
    print("site written to", SITE)

if __name__ == "__main__":
    build()
