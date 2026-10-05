# Fact Check page: integration spec for the generator

Source data: `assets/factchecks.json` (JSON array, committed in the repo, updated by hand, not by a cron). Each entry has: `id` (slug), `claim`, `verdict` (one of `True`, `Misleading`, `False`, `Unverified`), `summary` (2-4 sentences), `details` (bullet list), `sources` (array of `{name, url}`), `date` (YYYY-MM-DD).

## Page layout (factcheck.html, depth 0)

1. Use `head()` with title "Fact Check" and a neutral description, then `topbar(depth=0, active='factcheck')`, standard footer, shared `styles.css`, theme toggle, and top-search. Follow the same shell as `policy.html` / `scores.html`.
2. Page header: a kicker line "Fact Check", an H1 "Fact Check / Propaganda Buster", and a short mission paragraph, in the site's voice: "Varta and Samkara exists to bust misinformation. This section collects the claims we have verified against our own reporting and independent sources: what was claimed, what we found, and where you can check it yourself." Keep it neutral, NDTV style, no em dashes.
3. A verdict legend strip under the header: three or four small badges with labels ("True: claim checked out", "Misleading: partly true or missing key context", "False: contradicted by the evidence", "Unverified: not enough evidence yet").
4. Card list: one card per entry, newest first (sort by `date` descending). Each card contains:
   - The claim as the card title, quoted, styled like a card heading (`sec-title` family).
   - A verdict badge (see colors below), placed next to or above the claim.
   - The summary paragraph.
   - An expandable `<details>` element labeled "What we verified" with the `details` bullets as a `<ul>`, and a "Sources" list with each source as a linked name (`target="_blank" rel="noopener"`), followed by the date and the entry id as the card's HTML anchor (`id="<id>"`).
5. Cards use the existing `.card` class family so dark mode, shadows, and radius match the rest of the site.

## Verdict badge colors

Use solid CSS classes, not inline styles:

- `.verdict-true` : green. Background `#1e7f3f` (light) / `#2fa85c` text on dark, white text on light.
- `.verdict-misleading`, `.verdict-unverified` : amber. Background `#b25f09` / text `#f5a623` on dark, white text on light.
- `.verdict-false` : red. Background `#b3212c` / text `#ff6b6b` on dark, white text on light.

Badges: uppercase, 0.72rem, letter-spacing .08em, border-radius 999px, padding .25rem .8rem, placed in a meta row above the claim title. Navy/saffron identity stays untouched; badges are the only new colors, kept consistent with the site's existing accent conventions.

## Nav addition ("Fact Check")

In `topbar()` (generate_site.py), add `link('factcheck.html','Fact Check','factcheck')` after the Blog link: Home, News, Videos, Blog, Fact Check, Heroes, Scores, Markets, Policy, Study, Today, Horoscope, Tags, Instagram. The `active` key `'factcheck'` drives the saffron underline via the existing `.navlinks a.active` rule. Also add `factcheck.html` to the footer link row after Blog. No hamburger changes needed; it renders from the same `navlinks` list.

## Generator build steps

In the main build flow of generate_site.py:

1. Load `assets/factchecks.json` at startup (fail soft: if missing or unparsable, skip the page but still write everything else, and log a warning).
2. Validate each entry: `id`, `claim`, `verdict` in {True, Misleading, False, Unverified}, `summary`, `details` (non-empty list), `sources` (non-empty list of `{name, url}` with http(s) URLs), `date` matching `^\d{4}-\d{2}-\d{2}$`. Skip invalid entries with a warning.
3. Render `factcheck.html` with the layout above. Escape all entry text with `html.escape`. Keep `id` values URL-safe; use them as card anchors.
4. No changes to the service-worker CORE cache list are needed beyond the usual rule: if `factcheck.html` is added to the precache list, bump the CACHE version string (per the AGENTS.md lesson on cache bumps).

## Search-index inclusion

In the search-index build (where news, heroes, policy, and constitution entries are added), iterate the validated fact-check entries and call `_add_search` with: title `"Fact check: {claim}"`, url `"factcheck.html#{id}"`, kind `"factcheck"`, the `summary` as text, and the `date`. This makes the topbar site-wide search cover the fact checks.

## Content rules for this section

- Every entry must be derived from the site's own published reporting (posts log at `~/workspace/job-campaign/varta-samkara-posts-log.md` plus the caption files under `~/workspace/job-campaign/varta-samkara/posts/`) and at least one opened external source. Never invent entries.
- Present claims as claims; verdict wording stays neutral and evidence-based. Entries include self-corrections (e.g. the Jaishankar 2027 headline, which the site itself later corrected) with the correction noted in the details.
- No em dashes in any user-facing copy; use commas, colons, or hyphens.
