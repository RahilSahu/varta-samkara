#!/usr/bin/env python3
"""Generate the Varta & Samkara static news website from the posts log.

Outputs:
  index.html            homepage (hero + latest 9)
  archive.html          full archive grouped by date
  posts/<slug>/        one full article page per published post
  assets/thumbs/       card thumbnails
  assets/articles/     article images (hero + carousel galleries)
  styles.css

Idempotent: assets are only recopied when the source is newer; stale
article directories are removed. Safe to run on a schedule.
"""
import os, re, shutil, html
from PIL import Image

WS = "/home/hatch/workspace/job-campaign"
LOG = os.path.join(WS, "varta-samkara-posts-log.md")
POSTS = os.path.join(WS, "varta-samkara", "posts")
SITE = "/home/hatch/workspace/varta-samkara-website"
THUMBS = os.path.join(SITE, "assets", "thumbs")
ARTICLES = os.path.join(SITE, "assets", "articles")
IG = "https://www.instagram.com/vartaandsamkaraindia/"

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
        })
    return rows

STOP = {"the", "a", "an", "of", "on", "in", "to", "for", "and", "with", "amid",
        "over", "from", "by", "s", "is", "are", "was", "as", "at", "its"}

def keywords(s):
    return {w.lower() for w in re.findall(r"[A-Za-z0-9&]+", s)
            if w.lower() not in STOP and len(w) > 2}

def find_post_dir(row):
    """Fuzzy-match a post directory by date + keyword overlap."""
    cands = [d for d in os.listdir(POSTS) if d.startswith(row["date"])]
    if not cands:
        return None
    skw = keywords(row["story"])
    best, best_score = None, 0
    for d in cands:
        slug = d[len(row["date"]) + 1:]
        score = len(skw & keywords(slug.replace("-", " ")))
        if score > best_score:
            best, best_score = d, score
    return best

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
    title = row["story"]
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
    return {"title": title, "paras": paras, "source": source,
            "visuals": visuals, "tags": tags}

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
                  "card-01.png"])
            or os.path.join(dpath, imgs[0]))
    gallery = [os.path.join(dpath, f) for f in imgs
               if f.startswith(("slide-", "card-")) and os.path.join(dpath, f) != hero]
    return hero, gallery

# ---------------------------------------------------------------- assets

def copy_image(src, rel_dst, max_w):
    """Copy+resize src to SITE/rel_dst, skipping when up to date. Returns web path."""
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
:root{--navy:#0a1a3c;--navy2:#122a5e;--saffron:#ff9933;--ink:#1a1a1a;--muted:#5b6472;--bg:#f7f8fb;--card:#fff}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,-apple-system,Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink);line-height:1.6}
a{color:inherit;text-decoration:none}
.topbar{background:var(--navy);color:#fff;padding:.7rem 1.2rem;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}
.brand{display:flex;align-items:center;gap:.7rem}
.brand .wm{width:42px;height:42px;border-radius:50%;background:var(--saffron);color:var(--navy);display:flex;align-items:center;justify-content:center;font-size:1.5rem;font-weight:700;font-family:'Noto Sans Devanagari','Noto Serif Devanagari',sans-serif}
.brand h1{font-size:1.15rem;letter-spacing:.06em}
.brand h1 span{color:var(--saffron)}
nav a{margin-left:1.2rem;font-size:.95rem;opacity:.9}
nav a:hover{color:var(--saffron)}
.hero{background:linear-gradient(135deg,var(--navy) 0%,var(--navy2) 70%);color:#fff;padding:3rem 1.2rem;position:relative;overflow:hidden}
.hero::after{content:'\\0935';font-family:'Noto Sans Devanagari',sans-serif;position:absolute;right:-30px;top:-70px;font-size:22rem;opacity:.05;color:#fff;pointer-events:none}
.hero-inner{max-width:1100px;margin:0 auto;position:relative}
.hero .kicker{color:var(--saffron);text-transform:uppercase;letter-spacing:.2em;font-size:.8rem;margin-bottom:.8rem}
.hero h2{font-size:clamp(1.6rem,4vw,2.6rem);line-height:1.25;max-width:640px;margin-bottom:1rem}
.hero p{max-width:640px;opacity:.85;margin-bottom:1.4rem}
.btn{display:inline-block;background:var(--saffron);color:var(--navy);font-weight:700;padding:.7rem 1.4rem;border-radius:8px}
.btn:hover{filter:brightness(1.08)}
.btn.ghost{background:transparent;color:var(--saffron);border:2px solid var(--saffron)}
.wrap{max-width:1100px;margin:0 auto;padding:2.2rem 1.2rem}
.wrap.narrow{max-width:760px}
.sec-title{font-size:1.5rem;margin-bottom:1.2rem;display:flex;align-items:center;gap:.6rem}
.sec-title::before{content:'';width:6px;height:1.4em;background:var(--saffron);border-radius:3px;display:inline-block}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:1.4rem}
.card{background:var(--card);border-radius:12px;overflow:hidden;box-shadow:0 2px 10px rgba(10,26,60,.08);display:flex;flex-direction:column;transition:transform .15s}
.card:hover{transform:translateY(-3px)}
.card img{width:100%;aspect-ratio:16/9;object-fit:cover;background:var(--navy)}
.card-body{padding:1rem 1.1rem 1.2rem;display:flex;flex-direction:column;gap:.5rem;flex:1}
.meta{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}
.meta .tag{background:#eef1f7;border-radius:20px;padding:.1rem .6rem;color:var(--navy2);font-weight:600}
.card h3{font-size:1.05rem;line-height:1.4}
.card p{font-size:.9rem;color:var(--muted);flex:1}
.card .src{font-size:.78rem;color:var(--muted)}
.read{color:var(--saffron);font-weight:700;font-size:.9rem}
.about{background:var(--navy);color:#fff;border-radius:16px;padding:2.2rem;margin-top:2.5rem;position:relative;overflow:hidden}
.about::after{content:'\\0935';font-family:'Noto Sans Devanagari',sans-serif;position:absolute;right:10px;bottom:-90px;font-size:16rem;opacity:.06;pointer-events:none}
.about h2{margin-bottom:.8rem}
.about h2 span{color:var(--saffron)}
.about p{opacity:.88;max-width:700px;margin-bottom:.6rem}
footer{background:#060d20;color:#aab4cc;padding:2rem 1.2rem;margin-top:3rem;font-size:.88rem}
.foot-inner{max-width:1100px;margin:0 auto;display:flex;justify-content:space-between;gap:1rem;flex-wrap:wrap}
footer a{color:var(--saffron)}
.day-group{margin-bottom:2.4rem}
.day-group h3{font-size:1.15rem;color:var(--navy);margin-bottom:1rem;border-bottom:2px solid var(--saffron);display:inline-block;padding-bottom:.2rem}
.list-item{display:flex;gap:1rem;background:var(--card);border-radius:10px;padding:.8rem;margin-bottom:.8rem;box-shadow:0 1px 6px rgba(10,26,60,.06);align-items:center}
.list-item img{width:120px;height:90px;object-fit:cover;border-radius:8px;flex-shrink:0;background:var(--navy)}
.list-item .li-body{flex:1;min-width:0}
.list-item h4{font-size:1rem;line-height:1.35;margin-bottom:.2rem}
.list-item .meta{margin-bottom:.15rem}
/* article pages */
.article-hero{width:100%;max-height:460px;object-fit:cover;border-radius:14px;background:var(--navy);margin:1.2rem 0 1.6rem}
.article h1{font-size:clamp(1.5rem,3.5vw,2.2rem);line-height:1.3;margin:.6rem 0 1rem;color:var(--navy)}
.article .lede{font-size:1.12rem;color:#333;margin-bottom:1.2rem}
.article p.body{margin-bottom:1.1rem;font-size:1.02rem}
.sourcebox{background:#fff;border-left:5px solid var(--saffron);border-radius:8px;padding:1rem 1.2rem;margin:1.8rem 0;box-shadow:0 1px 6px rgba(10,26,60,.06);font-size:.92rem}
.sourcebox div{margin-bottom:.3rem}
.sourcebox .lbl{font-weight:700;color:var(--navy2)}
.tagrow{display:flex;flex-wrap:wrap;gap:.5rem;margin:1.4rem 0}
.tagrow span{background:#eef1f7;color:var(--navy2);border-radius:20px;padding:.25rem .8rem;font-size:.82rem;font-weight:600}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:1rem;margin:1.6rem 0}
.gallery img{width:100%;border-radius:10px;box-shadow:0 2px 8px rgba(10,26,60,.1)}
.ig-cta{display:flex;gap:1rem;align-items:center;background:var(--navy);color:#fff;border-radius:12px;padding:1.2rem 1.4rem;margin:2rem 0;flex-wrap:wrap}
.ig-cta p{flex:1;min-width:200px;margin:0}
.prevnext{display:flex;justify-content:space-between;gap:1rem;margin:2.2rem 0 1rem;flex-wrap:wrap}
.prevnext a{background:#fff;border-radius:10px;padding:.9rem 1.1rem;box-shadow:0 1px 6px rgba(10,26,60,.06);flex:1;min-width:220px;font-size:.92rem}
.prevnext a:hover{border:1px solid var(--saffron)}
.prevnext .dir{display:block;font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:.2rem}
@media(max-width:640px){nav a{margin-left:.7rem}.list-item img{width:96px;height:72px}}
"""

# ---------------------------------------------------------------- templates

def topbar(active):
    def link(href, label):
        return f'<a href="{href}">{label}</a>'
    return f"""<header class="topbar">
<div class="brand"><div class="wm">&#2357;</div><h1>VARTA <span>&amp;</span> SAMKARA</h1></div>
<nav>{link('../index.html' if active=='article' else 'index.html','Home')}{link('../archive.html' if active=='article' else 'archive.html','Archive')}<a href="{IG}" target="_blank" rel="noopener">Instagram</a></nav>
</header>"""

FOOTER = f"""<footer><div class="foot-inner">
<div>&copy; 2026 Varta &amp; Samkara. News verified, opinions owned.</div>
<div><a href="{IG}" target="_blank" rel="noopener">Instagram</a></div>
</div></footer>"""

def excerpt_of(art, story):
    if art["paras"]:
        txt = re.sub(r"\s+", " ", art["paras"][0]).strip()
        return txt[:150] + ("..." if len(txt) > 150 else "")
    return story[:150]

def card_html(p):
    return f"""<article class="card">
<a href="{p['article_url']}"><img src="{p['thumb']}" alt="{html.escape(p['art']['title'])}" loading="lazy"></a>
<div class="card-body">
<div class="meta"><span class="tag">{html.escape(p['format'])}</span><span>{p['date']}</span><span>{html.escape(p['slot'])}</span></div>
<h3><a href="{p['article_url']}">{html.escape(p['art']['title'])}</a></h3>
<p>{html.escape(p['excerpt'])}</p>
<div class="src">Source: {html.escape(p['art']['source'][:90])}</div>
<a class="read" href="{p['article_url']}">Read full story &rarr;</a>
</div></article>"""

def article_html(p, prev_p, next_p):
    art = p["art"]
    body = "".join(f"<p class='body'>{html.escape(pa)}</p>" for pa in art["paras"])
    if not body:
        body = f"<p class='body'>{html.escape(p['story'])}</p>"
    gallery = ""
    if p["gallery"]:
        figs = "".join(f'<img src="../{g}" alt="{html.escape(art["title"])}" loading="lazy">' for g in p["gallery"])
        gallery = f"<h2 class='sec-title'>In pictures</h2><div class='gallery'>{figs}</div>"
    tags = ""
    if art["tags"]:
        tags = "<div class='tagrow'>" + "".join(f"<span>{html.escape(t)}</span>" for t in art["tags"]) + "</div>"
    nav = "<div class='prevnext'>"
    nav += (f"<a href='../{prev_p['slug']}/'><span class='dir'>&larr; Newer</span>{html.escape(prev_p['art']['title'][:70])}</a>" if prev_p else "<span></span>")
    nav += (f"<a href='../{next_p['slug']}/' style='text-align:right'><span class='dir'>Older &rarr;</span>{html.escape(next_p['art']['title'][:70])}</a>" if next_p else "<span></span>")
    nav += "</div>"
    desc = html.escape(art["paras"][0][:160] if art["paras"] else p["story"][:160])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(art['title'])} | Varta &amp; Samkara</title>
<meta name="description" content="{desc}">
<meta property="og:title" content="{html.escape(art['title'])}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="article">
<link rel="stylesheet" href="../styles.css">
</head>
<body>
{topbar('article')}
<main class="wrap narrow article">
<div class="meta" style="margin-top:1rem"><span class="tag">{html.escape(p['format'])}</span><span>{p['date']}</span><span>{html.escape(p['slot'])}</span></div>
<h1>{html.escape(art['title'])}</h1>
<img class="article-hero" src="../{p['hero']}" alt="{html.escape(art['title'])}">
{body}
{gallery}
<div class="sourcebox">
<div><span class="lbl">Source:</span> {html.escape(art['source'])}</div>
<div><span class="lbl">Visuals:</span> {html.escape(art['visuals'])}</div>
</div>
{tags}
<div class="ig-cta">
<p>See the original reel / carousel with motion graphics on Instagram.</p>
<a class="btn" href="{p['url']}" target="_blank" rel="noopener">View on Instagram</a>
</div>
{nav}
</main>
{FOOTER}
</body>
</html>"""

# ---------------------------------------------------------------- build

def build():
    os.makedirs(SITE, exist_ok=True)
    rows = parse_log()
    posts = []
    used_slugs = set()
    for r in rows:
        dname = find_post_dir(r)
        dpath = os.path.join(POSTS, dname) if dname else None
        slug = dname or (slugify(r["date"] + "-" + r["story"]) or "post")
        if slug in used_slugs:
            slug = f"{slug}-{slugify(r['slot'])}"
        n = 2
        while slug in used_slugs:
            slug = f"{slug}-{n}"
            n += 1
        used_slugs.add(slug)
        caption = load_caption(dpath) if dpath else ""
        art = parse_article(caption, r)
        hero_src, gallery_srcs = post_images(dpath)
        if hero_src:
            hero = copy_image(hero_src, f"assets/articles/{slug}/hero.jpg", 1080)
            thumb = copy_image(hero_src, f"assets/thumbs/{slug}.jpg", 640)
        else:
            hero = thumb = "assets/placeholder.svg"
        gallery = [copy_image(g, f"assets/articles/{slug}/{os.path.basename(g)}", 1080)
                   for g in gallery_srcs]
        posts.append({**r, "slug": slug, "art": art, "hero": hero,
                      "thumb": thumb, "gallery": gallery,
                      "article_url": f"posts/{slug}/",
                      "excerpt": excerpt_of(art, r["story"])})
    posts.sort(key=lambda p: (p["date"], p["slot"]), reverse=True)

    # remove article dirs for posts that no longer exist
    expected = {p["slug"] for p in posts}
    posts_root = os.path.join(SITE, "posts")
    os.makedirs(posts_root, exist_ok=True)
    for d in os.listdir(posts_root):
        if os.path.isdir(os.path.join(posts_root, d)) and d not in expected:
            shutil.rmtree(os.path.join(posts_root, d))

    # article pages
    for i, p in enumerate(posts):
        prev_p = posts[i - 1] if i > 0 else None
        next_p = posts[i + 1] if i < len(posts) - 1 else None
        adir = os.path.join(posts_root, p["slug"])
        os.makedirs(adir, exist_ok=True)
        with open(os.path.join(adir, "index.html"), "w", encoding="utf-8") as f:
            f.write(article_html(p, prev_p, next_p))

    with open(os.path.join(SITE, "styles.css"), "w", encoding="utf-8") as f:
        f.write(BASE_CSS)
    os.makedirs(os.path.join(SITE, "assets"), exist_ok=True)
    with open(os.path.join(SITE, "assets", "placeholder.svg"), "w") as f:
        f.write(PLACEHOLDER_SVG)

    latest = posts[:9]
    hero = posts[0]
    index = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Varta &amp; Samkara | News, Analysis &amp; Opinions from India</title>
<meta name="description" content="Varta and Samkara: verified news reels, explainers and opinion pieces from India. Every story verified against at least two independent sources.">
<link rel="stylesheet" href="styles.css">
</head>
<body>
{topbar('home')}
<section class="hero">
<div class="hero-inner">
<div class="kicker">Top story &bull; {hero['date']}</div>
<h2>{html.escape(hero['art']['title'])}</h2>
<p>{html.escape(hero['excerpt'])}</p>
<a class="btn" href="{hero['article_url']}">Read full story</a>
&nbsp;<a class="btn ghost" href="{hero['url']}" target="_blank" rel="noopener">Watch on Instagram</a>
</div>
</section>
<main class="wrap">
<h2 class="sec-title">Latest Stories</h2>
<div class="grid">
{''.join(card_html(p) for p in latest)}
</div>
<div style="margin-top:1.6rem"><a class="btn" href="archive.html">Browse the full archive ({len(posts)} stories)</a></div>
<section class="about">
<h2>About <span>Varta &amp; Samkara</span></h2>
<p><strong>Varta</strong> means discourse, <strong>Samkara</strong> means impression. This page brings verified news, sharp explainers and honest opinion from India, five posts a day.</p>
<p>Every news story is verified against at least two independent sources before it goes up. Opinion pieces are labelled as opinion. AI-generated visuals are always disclosed.</p>
<p>Follow the daily slate on Instagram: <a href="{IG}" target="_blank" rel="noopener" style="color:var(--saffron);font-weight:700">@vartaandsamkaraindia</a></p>
</section>
</main>
{FOOTER}
</body>
</html>"""
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(index)

    groups = {}
    for p in posts:
        groups.setdefault(p["date"], []).append(p)
    blocks = []
    for d in sorted(groups, reverse=True):
        items = []
        for p in groups[d]:
            items.append(f"""<div class="list-item">
<a href="{p['article_url']}"><img src="{p['thumb']}" alt="" loading="lazy"></a>
<div class="li-body">
<div class="meta"><span class="tag">{html.escape(p['format'])}</span><span>{html.escape(p['slot'])}</span></div>
<h4><a href="{p['article_url']}">{html.escape(p['art']['title'])}</a></h4>
</div></div>""")
        blocks.append(f'<div class="day-group"><h3>{d}</h3>{"".join(items)}</div>')
    archive = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Archive | Varta &amp; Samkara</title>
<link rel="stylesheet" href="styles.css">
</head>
<body>
{topbar('archive')}
<main class="wrap">
<h2 class="sec-title">Full Archive ({len(posts)} stories)</h2>
{''.join(blocks)}
</main>
{FOOTER}
</body>
</html>"""
    with open(os.path.join(SITE, "archive.html"), "w", encoding="utf-8") as f:
        f.write(archive)
    n_art = sum(1 for _ in os.listdir(posts_root))
    n_thumb = sum(1 for p in posts if p["thumb"] != "assets/placeholder.svg")
    print(f"posts: {len(posts)}, article pages: {n_art}, with images: {n_thumb}")
    print("site written to", SITE)

if __name__ == "__main__":
    build()
