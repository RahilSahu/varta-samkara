#!/usr/bin/env python3
"""Generate the Varta & Samkara static news website from the posts log."""
import os, re, shutil, html
from PIL import Image

WS = "/home/hatch/workspace/job-campaign"
LOG = os.path.join(WS, "varta-samkara-posts-log.md")
POSTS = os.path.join(WS, "varta-samkara", "posts")
SITE = "/home/hatch/workspace/varta-samkara-website"
THUMBS = os.path.join(SITE, "assets", "thumbs")

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

def find_thumb(row):
    """Fuzzy-match a post directory by date + keyword overlap, return best image."""
    cands = [d for d in os.listdir(POSTS) if d.startswith(row["date"])]
    if not cands:
        return None, None
    skw = keywords(row["story"])
    best, best_score = None, 0
    for d in cands:
        slug = d[len(row["date"]) + 1:]
        score = len(skw & keywords(slug.replace("-", " ")))
        if score > best_score:
            best, best_score = d, score
    if not best:
        return None, None
    dpath = os.path.join(POSTS, best)
    caption = ""
    cp = os.path.join(dpath, "caption.txt")
    if os.path.exists(cp):
        caption = open(cp, encoding="utf-8").read().strip()
    for name in ["cover.jpg", "cover.png", "slide-1.jpg", "card-01.png"]:
        p = os.path.join(dpath, name)
        if os.path.exists(p):
            return p, caption
    imgs = sorted(f for f in os.listdir(dpath)
                  if f.lower().endswith((".jpg", ".png", ".jpeg", ".webp")))
    if imgs:
        return os.path.join(dpath, imgs[0]), caption
    return None, caption

def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:60] or "post"

def copy_thumb(src, name):
    os.makedirs(THUMBS, exist_ok=True)
    dst = os.path.join(THUMBS, name + ".jpg")
    if os.path.exists(dst):
        return "assets/thumbs/" + name + ".jpg"
    im = Image.open(src).convert("RGB")
    im.thumbnail((640, 1138), Image.LANCZOS)
    im.save(dst, quality=80)
    return "assets/thumbs/" + name + ".jpg"

def excerpt(caption, story):
    if caption:
        txt = re.sub(r"\s+", " ", caption).strip()
        txt = re.sub(r"#\w+", "", txt).strip()
        return txt[:150] + ("..." if len(txt) > 150 else "")
    return story[:150]

CSS = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "styles.css")).read() \
    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "styles.css")) else None

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
.wrap{max-width:1100px;margin:0 auto;padding:2.2rem 1.2rem}
.sec-title{font-size:1.5rem;margin-bottom:1.2rem;display:flex;align-items:center;gap:.6rem}
.sec-title::before{content:'';width:6px;height:1.4em;background:var(--saffron);border-radius:3px;display:inline-block}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:1.4rem}
.card{background:var(--card);border-radius:12px;overflow:hidden;box-shadow:0 2px 10px rgba(10,26,60,.08);display:flex;flex-direction:column;transition:transform .15s}
.card:hover{transform:translateY(-3px)}
.card img{width:100%;aspect-ratio:16/9;object-fit:cover;background:var(--navy)}
.card-body{padding:1rem 1.1rem 1.2rem;display:flex;flex-direction:column;gap:.5rem;flex:1}
.meta{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;display:flex;gap:.6rem;align-items:center}
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
@media(max-width:640px){nav a{margin-left:.7rem}.list-item img{width:96px;height:72px}}
"""

def card_html(p):
    return f"""<article class="card">
<a href="{p['url']}" target="_blank" rel="noopener"><img src="{p['thumb']}" alt="{html.escape(p['story'])}" loading="lazy"></a>
<div class="card-body">
<div class="meta"><span class="tag">{html.escape(p['format'])}</span><span>{p['date']}</span></div>
<h3><a href="{p['url']}" target="_blank" rel="noopener">{html.escape(p['story'])}</a></h3>
<p>{html.escape(p['excerpt'])}</p>
<div class="src">Source: {html.escape(p['sources'][:90])}</div>
<a class="read" href="{p['url']}" target="_blank" rel="noopener">View on Instagram &rarr;</a>
</div></article>"""

def build():
    # clean only generated outputs, never the generator itself
    for name in ["index.html", "archive.html", "styles.css"]:
        p = os.path.join(SITE, name)
        if os.path.exists(p):
            os.remove(p)
    if os.path.exists(os.path.join(SITE, "assets")):
        shutil.rmtree(os.path.join(SITE, "assets"))
    os.makedirs(SITE, exist_ok=True)

    rows = parse_log()
    posts = []
    used = set()
    for r in rows:
        src, caption = find_thumb(r)
        name = slugify(r["date"] + "-" + r["story"])
        if name in used:
            name += "-" + slugify(r["slot"])
        used.add(name)
        thumb = copy_thumb(src, name) if src else "assets/placeholder.svg"
        posts.append({**r, "thumb": thumb,
                      "excerpt": excerpt(caption, r["story"])})
    posts.sort(key=lambda p: p["date"], reverse=True)
    print(f"posts with URLs: {len(rows)}, with thumbnails: {sum(1 for p in posts if p['thumb'] != 'assets/placeholder.svg')}")

    with open(os.path.join(SITE, "styles.css"), "w") as f:
        f.write(BASE_CSS)
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360">'
           '<rect width="640" height="360" fill="#0a1a3c"/>'
           '<text x="320" y="200" font-size="120" fill="#ff9933" text-anchor="middle" '
           'font-family="sans-serif">&#2357;</text></svg>')
    os.makedirs(os.path.join(SITE, "assets"), exist_ok=True)
    open(os.path.join(SITE, "assets", "placeholder.svg"), "w").write(svg)

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
<header class="topbar">
<div class="brand"><div class="wm">&#2357;</div><h1>VARTA <span>&amp;</span> SAMKARA</h1></div>
<nav><a href="index.html">Home</a><a href="archive.html">Archive</a><a href="https://www.instagram.com/vartaandsamkaraindia/" target="_blank" rel="noopener">Instagram</a></nav>
</header>
<section class="hero">
<div class="hero-inner">
<div class="kicker">Top story &bull; {hero['date']}</div>
<h2>{html.escape(hero['story'])}</h2>
<p>{html.escape(hero['excerpt'])}</p>
<a class="btn" href="{hero['url']}" target="_blank" rel="noopener">Watch on Instagram</a>
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
<p>Follow the daily slate on Instagram: <a href="https://www.instagram.com/vartaandsamkaraindia/" target="_blank" rel="noopener" style="color:var(--saffron);font-weight:700">@vartaandsamkaraindia</a></p>
</section>
</main>
<footer><div class="foot-inner">
<div>&copy; 2026 Varta &amp; Samkara. News verified, opinions owned.</div>
<div><a href="https://www.instagram.com/vartaandsamkaraindia/" target="_blank" rel="noopener">Instagram</a></div>
</div></footer>
</body>
</html>"""
    open(os.path.join(SITE, "index.html"), "w", encoding="utf-8").write(index)

    groups = {}
    for p in posts:
        groups.setdefault(p["date"], []).append(p)
    blocks = []
    for d in sorted(groups, reverse=True):
        items = []
        for p in groups[d]:
            items.append(f"""<div class="list-item">
<a href="{p['url']}" target="_blank" rel="noopener"><img src="{p['thumb']}" alt="" loading="lazy"></a>
<div class="li-body">
<div class="meta"><span class="tag">{html.escape(p['format'])}</span><span>{html.escape(p['slot'])}</span></div>
<h4><a href="{p['url']}" target="_blank" rel="noopener">{html.escape(p['story'])}</a></h4>
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
<header class="topbar">
<div class="brand"><div class="wm">&#2357;</div><h1>VARTA <span>&amp;</span> SAMKARA</h1></div>
<nav><a href="index.html">Home</a><a href="archive.html">Archive</a><a href="https://www.instagram.com/vartaandsamkaraindia/" target="_blank" rel="noopener">Instagram</a></nav>
</header>
<main class="wrap">
<h2 class="sec-title">Full Archive ({len(posts)} stories)</h2>
{''.join(blocks)}
</main>
<footer><div class="foot-inner">
<div>&copy; 2026 Varta &amp; Samkara. News verified, opinions owned.</div>
<div><a href="https://www.instagram.com/vartaandsamkaraindia/" target="_blank" rel="noopener">Instagram</a></div>
</div></footer>
</body>
</html>"""
    open(os.path.join(SITE, "archive.html"), "w", encoding="utf-8").write(archive)
    print("site written to", SITE)

if __name__ == "__main__":
    build()
