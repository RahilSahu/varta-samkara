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
from datetime import datetime
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
</script>"""

# ---------------------------------------------------------------- templates

def rel(depth):
    return "../" * depth

def topbar(depth, active):
    r = rel(depth)
    def link(href, label, key):
        cls = ' class="active"' if active == key else ""
        return f'<a href="{r}{href}"{cls}>{label}</a>'
    return f"""<header class="topbar">
<div class="brand"><div class="wm">&#2357;</div><h1>VARTA <span>&amp;</span> SAMKARA</h1></div>
<nav class="navlinks" id="navlinks">{link('index.html','Home','home')}{link('archive.html','News','news')}{link('blog.html','Blog','blog')}{link('tags/','Tags','tags')}<a href="{IG}" target="_blank" rel="noopener">Instagram</a></nav>
<div class="top-actions">
<button class="theme-toggle" id="theme-toggle" aria-label="Toggle dark mode"><span id="theme-icon">&#9789;</span></button>
<button class="hamburger" id="burger" aria-label="Menu">&#9776;</button>
</div>
</header>"""

def footer(depth):
    r = rel(depth)
    return f"""<footer><div class="foot-inner">
<div>&copy; 2026 Varta &amp; Samkara. News verified, opinions owned.</div>
<div class="foot-links"><a href="{r}tags/">Tags</a><a href="{r}archive.html">Archive</a><a href="{r}feed.xml">RSS</a><a href="{r}sitemap.xml">Sitemap</a><a href="{IG}" target="_blank" rel="noopener">Instagram</a></div>
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

# ---------------------------------------------------------------- build

def build():
    os.makedirs(SITE, exist_ok=True)
    os.makedirs(CONTENT, exist_ok=True)
    os.makedirs(BLOGSDIR, exist_ok=True)
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
    sw = """const CACHE = "vs-cache-v2";
const CORE = ["./", "index.html", "offline.html", "styles.css",
              "manifest.json", "assets/placeholder.svg"];
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

    sm_urls = (["", "archive.html", "blog.html", "feed.xml", "tags/"]
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
